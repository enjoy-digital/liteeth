from litex.gen import *

from litex.soc.interconnect import stream

from liteeth.common import *

from litex.soc.interconnect.stream import Buffer

from liteeth.core.rocev2.common import *
from liteeth.core.rocev2.mad_cm import LiteEthIBMAD
from liteeth.core.rocev2 import pad, icrc
from liteeth.core.rocev2.qp import LiteEthIBSpecialQP, LiteEthIBQP, LiteEthCQ

from liteeth.core.rocev2.requester import LiteEthRDMARequesterTX, LiteEthRDMARequesterRX
from liteeth.core.rocev2.responder import LiteEthRDMAResponderTX, LiteEthRDMAResponderRX

# ROCEv2 TX ----------------------------------------------------------------------------------------

class LiteEthIBTransportPacketizer(VariablePacketizer):
    def __init__(self, dw=8):
        VariablePacketizer.__init__(self,
            eth_rocev2_description(dw),
            eth_udp_user_description(dw),
            IBT_headers,
            IBT_opmap
        )

class LiteEthIBTransportTX(LiteXModule):
    def __init__(self, ip, mad_tx, qps, mrs, with_crc=True, buffered_out=True, dw=8):
        self.source = source = Endpoint(eth_udp_user_description(dw))

        self.enable = enable = Signal()

        self.responder = responder = LiteEthRDMAResponderTX(mad_tx, qps, mrs, dw=dw)
        self.requester = requester = LiteEthRDMARequesterTX(qps[1], mrs, dw=dw)

        # # #

        # Add ICRC at the end of messages
        if with_crc:
            self.tx_crc = tx_crc = icrc.LiteEthInfinibandICRCInserter(
                listen_description = eth_mac_description(dw),
                description        = eth_udp_user_description(dw)
            )
            self.comb += [
                ip.tx.source.connect(tx_crc.calculator_sink, keep={"valid", "data", "last"}),
                # We listen passively to the output of ip, so no control of ready
                # (we don't use connect for it)
                tx_crc.calculator_sink.ready.eq(ip.tx.source.ready),
            ]
            self.comb += tx_crc.source.connect(source)
            source = tx_crc.sink

        # The lower layers, once ready, have to pipe the entire data through without interruptions
        # Otherwise, the packet on the wire would have holes
        # We use this fact to cut timing
        if buffered_out:
            buff_out = Buffer(eth_udp_user_description(dw), pipe_ready=True)
            self.submodules.buff_out = buff_out
            self.comb += buff_out.source.connect(source)
            source = buff_out.sink

        pad_ins = pad.LiteEthIBTPaddingInserter(dw)
        self.submodules.pad_ins = pad_ins

        # Packetizer
        self.packetizer = packetizer = LiteEthIBTransportPacketizer(dw=dw)

        ip_address = Signal(32)
        choose_responder = Signal()
        choose_responder_next = Signal()
        self.fsm = fsm = FSM()
        fsm.act("IDLE",
            If(qps[1].qp_state == LiteEthIBQP.ERROR,
                NextState("ERROR")
            ).Else(
                NextState("SEND"),
                If(responder.source.valid & requester.source.valid,
                    choose_responder_next.eq(~choose_responder),
                ).Elif(responder.source.valid,
                    choose_responder_next.eq(1)
                ).Elif(requester.source.valid,
                    choose_responder_next.eq(0)
                ).Else(
                    NextState("IDLE")
                ),
                If(choose_responder_next,
                    If(responder.source.dest_qp == 1,
                        NextValue(ip_address, qps[0].ip_address)
                    ).Else(
                        NextValue(ip_address, qps[1].ip_address)
                    )
                ).Else(
                    If(requester.source.dest_qp == 1,
                        NextValue(ip_address, qps[0].ip_address)
                    ).Else(
                        NextValue(ip_address, qps[1].ip_address)
                    )
                ),
                NextValue(choose_responder, choose_responder_next)
            )
        )

        fsm.act("SEND",
            If(choose_responder,
                responder.source.connect(pad_ins.sink)
            ).Else(
                requester.source.connect(pad_ins.sink)
            ),

            If(source.valid & source.ready & source.last,
                NextState("IDLE")
            )
        )

        fsm.act("ERROR",
            responder.source.ready.eq(1),
            requester.source.ready.eq(1),
            If(qps[1].qp_state != LiteEthIBQP.ERROR,
                NextState("IDLE")
            )
        )

        # Data-Path.
        self.comb += [
            pad_ins.source.connect(packetizer.sink, omit={"length"}),

            source.valid.eq(packetizer.source.valid & enable),
            packetizer.source.connect(source, keep={"ready", "last", "data"}),
            source.length.eq(pad_ins.source.length + (0x4 if with_crc else 0)),
            source.dst_port.eq(ROCEV2_PORT),
            # TODO: Needs to be changed if we add more qps
            source.ip_address.eq(ip_address)
        ]

# RX/TX interface ----------------------------------------------------------------------------------

# ROCEv2 RX ----------------------------------------------------------------------------------------

class LiteEthIBTransportDepacketizer(VariableDepacketizer):
    def __init__(self, dw=8):
        VariableDepacketizer.__init__(self,
            eth_udp_user_description(dw),
            eth_rocev2_description(dw),
            IBT_headers,
            IBT_opmap
        )

class LiteEthIBTransportValidation(LiteXModule):
    def __init__(self, qps, dw=8):
        self.sink   = sink   = Endpoint(
            add_params(eth_rocev2_description(dw), [
                ("header_only", 1)
            ])
        )
        self.source = source = Endpoint(
            add_params(eth_rocev2_description(dw), [
                ("header_only", 1),
                ("payload_length", PMTU_BITS + 1),
                ("invalid_packet", 1),
                ("validate", 1),
                ("response", 1)
            ])
        )

        self.pad_error = pad_error = Signal()
        self.valid_crc = valid_crc = Signal()

        ## Params to save sink parameters
        params = Endpoint(add_params(eth_rocev2_description(dw=dw), [("header_only", 1)]))
        self.sync += sink.connect(params, omit={"valid", "ready", "last", "data", "error"})

        ### Opcode splitting
        # Connection type and operation extracted from opcode
        opcode_conn_type = Signal(3)
        opcode_op        = Signal(5)
        # Opcode decomposition (must be always correct, not only for validation)
        self.comb += [
            If(sink.valid,
                Cat(opcode_op, opcode_conn_type).eq(sink.opcode)
            ).Else(
                Cat(opcode_op, opcode_conn_type).eq(params.opcode)
            )
        ]

        ### Signals linked to the qp's state
        conn_type         = Signal(3, reset_less=True)
        qp_state          = Signal(max=5, reset_less=True)
        p_key             = Signal(16, reset_less=True)
        # Extract qp parameters
        init_dict = {
            qp.id : [
                p_key.eq(qp.p_key),
                conn_type.eq(qp.conn_type),
                qp_state.eq(qp.qp_state)
            ] for qp in qps[1:]
        }
        init_dict[1] = [
            p_key.eq(qps[0].p_key),
            conn_type.eq(qps[0].conn_type),
        ]
        init_dict["default"] = [
            p_key.eq(0),
            conn_type.eq(0)
        ]
        self.sync += [
            If(sink.valid,
                Case(sink.dest_qp, init_dict)
            )
        ]

        ### Packet payload/integrity checks
        # Packet header checks
        # Silent drop validations
        valid_tver  = Signal()
        valid_dest  = Signal()
        valid_p_key = Signal()
        check_q_key = Signal()
        valid_q_key = Signal()

        self.comb += [
            valid_tver.eq(params.tver == 0),
            valid_dest.eq(
                (is_in(params.dest_qp, [qp.id for qp in qps])) & # QP exists
                ((params.dest_qp != 1) & (is_in(qp_state, [LiteEthIBQP.RTR, LiteEthIBQP.RTS])) |
                (params.dest_qp == 1)) & # QP is in correct state
                (opcode_conn_type == conn_type) # Requested conn_type is correct for this QP
            ),
            valid_p_key.eq(params.p_key == p_key),
            check_q_key.eq(params.dest_qp == 1),
            valid_q_key.eq(~check_q_key | (params.q_key == DEFAULT_CM_Q_Key))
        ]

        # Payload validations
        valid_padding = Signal()
        self.comb += valid_padding.eq(~pad_error)

        expect_full_packet  = Signal()
        expect_empty_packet = Signal()
        no_empty_packet     = Signal()

        self.comb += [
            is_in_flag(opcode_op, [
                BTH_OPCODE_OP.SEND_First,
                BTH_OPCODE_OP.SEND_Middle,
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Middle,
                BTH_OPCODE_OP.RDMA_READ_response_First,
                BTH_OPCODE_OP.RDMA_READ_response_Middle
            ], expect_full_packet),
            is_in_flag(opcode_op, [
                BTH_OPCODE_OP.SEND_Last,
                BTH_OPCODE_OP.SEND_Last_with_Immediate,
                BTH_OPCODE_OP.RDMA_WRITE_Last,
                BTH_OPCODE_OP.RDMA_WRITE_Last_with_Immediate,
                BTH_OPCODE_OP.RDMA_READ_response_Last
            ], no_empty_packet),
            is_in_flag(opcode_op, [
                BTH_OPCODE_OP.RDMA_READ_Request,
                BTH_OPCODE_OP.Acknowledge
            ], expect_empty_packet, case=False)
        ]

        # Misc validations
        check_dma_len  = Signal()
        check_zero_pad = Signal()
        self.comb += [
            is_in_flag(opcode_op, [
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Only,
                BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate,
                BTH_OPCODE_OP.RDMA_READ_Request
            ], check_dma_len),
            is_in_flag(opcode_op, [
                BTH_OPCODE_OP.SEND_First,
                BTH_OPCODE_OP.SEND_Middle,
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Middle
            ], check_zero_pad)
        ]

        # Payload validations
        payload_length = Signal(PMTU_BITS + 1)
        valid_payload = Signal()
        self.comb += valid_payload.eq(
            (valid_padding) &
            (payload_length <= PMTU) &
            (~expect_empty_packet | (payload_length == 0)) &
            (~expect_full_packet | (payload_length == PMTU)) &
            (~no_empty_packet | (payload_length != 0))
        )

        # Validate header, context, packet and ICRC
        valid_headers = Signal()
        valid_packet = Signal()
        valid_icrc = Signal()
        self.comb += [
            valid_headers.eq(
                valid_tver &
                valid_dest &
                valid_p_key &
                valid_q_key
                #    valid_syndrome,
            ),
            valid_packet.eq(
                valid_payload &
                (~check_zero_pad | (params.pad == 0b00)) &
                (~check_dma_len | (params.dma_len <= 2**31))
            ),
            valid_icrc.eq(valid_crc)
        ]

        ### Request indicator
        # Whether the receiver is receiving a request or a response to our request
        self.comb += [
            is_in_flag(opcode_op, [
                BTH_OPCODE_OP.RDMA_READ_response_First,
                BTH_OPCODE_OP.RDMA_READ_response_Middle,
                BTH_OPCODE_OP.RDMA_READ_response_Last,
                BTH_OPCODE_OP.RDMA_READ_response_Only,
                BTH_OPCODE_OP.Acknowledge
            ], source.response)
        ]

        self.comb += source.payload_length.eq(payload_length)

        # Control-Path (FSM).
        self.fsm = fsm = FSM(reset_state="RECEIVE")
        # Full packet reception is performed here
        fsm.act("RECEIVE",
            sink.connect(source, omit={"payload_length", "invalid_packet", "validate", "response"}),
            If(sink.valid & sink.ready,
                If(~sink.header_only,
                    NextValue(payload_length, payload_length + 1),
                ),
                If(sink.last,
                    NextState("VALIDATE")
                )
            )
        )

        # Checks are performed here
        fsm.act("VALIDATE",
            source.valid.eq(1),
            params.connect(source, omit={
                "valid",
                "ready",
                "payload_length",
                "invalid_packet",
                "validate",
                "response"
            }),
            NextValue(payload_length, 0),
            NextState("RECEIVE"),

            If(valid_headers & valid_icrc,
                If(~valid_packet,
                    source.invalid_packet.eq(1)
                ).Else(
                    source.validate.eq(1)
                )
            )
        )

class LiteEthIBTransportRX(LiteXModule):
    def __init__(self, ip, tx, mad_rx, qps, cq, mrs, clk_freq, with_crc=True, check_crc=True, buffered_in=True, dw=8):
        self.sink   = sink   = Endpoint(eth_udp_user_description(dw))

        # # #

        if check_crc:
            assert with_crc

        pipeline_mods = [sink]

        ### Data-Path.
        # ICRC
        if with_crc:
            self.rx_crc = rx_crc = icrc.LiteEthInfinibandICRCChecker(
                listen_description = eth_mac_description(dw),
                description        = eth_udp_user_description(dw)
            )

            self.comb += [
                ip.rx.sink.connect(rx_crc.calculator_sink, keep={"valid", "data", "last"}),
                # We listen passively to the output of ip,
                # so no control of ready (we don't use connect for it)
                rx_crc.calculator_sink.ready.eq(ip.rx.sink.ready)
            ]

            pipeline_mods.append(rx_crc)

        if buffered_in:
            buff_in = Buffer(eth_udp_user_description(dw), pipe_ready=True)
            self.submodules.buff_in = buff_in
            pipeline_mods.append(buff_in)

        # Depacketizer.
        self.depacketizer = depacketizer = LiteEthIBTransportDepacketizer(dw=dw)
        pipeline_mods.append(depacketizer)

        # Padding remover
        pad_rem = pad.LiteEthIBTPaddingRemover(dw)
        self.submodules.pad_rem = pad_rem
        pipeline_mods.append(pad_rem)

        # Validation of packet integrity and correctness for generic packets (requests or responses)
        pack_validation = LiteEthIBTransportValidation(qps, dw)
        self.submodules.pack_validation = pack_validation
        pipeline_mods.append(pack_validation)

        # Connections outside the pipeline
        self.comb += [
            pack_validation.pad_error.eq(pad_rem.error),
            pack_validation.valid_crc.eq(rx_crc.valid_crc if check_crc else 1)
        ]

        pipeline = stream.Pipeline(*pipeline_mods)
        self.submodules.pipeline = pipeline
        source = pipeline.source

        # Responder and requester
        responder = LiteEthRDMAResponderRX(tx.responder, mad_rx, qps, cq, mrs, dw=dw)
        requester = LiteEthRDMARequesterRX(tx.requester, qps, cq, mrs, clk_freq, dw=dw)
        self.submodules.responder = responder
        self.submodules.requester = requester

        # Dispatch validation output
        self.comb += [
            If(pipeline.source.response,
                source.connect(requester.sink, omit={"response", "invalid_packet"})
            ).Else(
                source.connect(responder.sink, omit={"response"}),
                responder.sink.ip_address.eq(sink.ip_address)
            )
        ]

# ROCEv2 RX + TX -----------------------------------------------------------------------------------
class LiteEthIBTransport(LiteXModule):
    def __init__(self, ip, udp, mrs, clk_freq, dw=8):
        self.cq = cq = LiteEthCQ(depth=0x10)

        self.qp = qp = LiteEthIBQP(qp_id=0xdeaded)

        special_qp = LiteEthIBSpecialQP()
        self.submodules.special_qp = special_qp

        self.qps = qps = [special_qp, qp]

        self.mad = mad = LiteEthIBMAD(qps, clk_freq, dw=dw)

        self.tx = tx = LiteEthIBTransportTX(
            ip           = ip,
            mad_tx       = mad.tx,
            qps          = qps,
            mrs          = mrs,
            with_crc     = True,
            buffered_out = True,
        )
        self.rx = rx = LiteEthIBTransportRX(
            ip        = ip,
            tx        = tx,
            mad_rx    = mad.rx,
            qps       = qps,
            cq        = cq,
            clk_freq  = clk_freq,
            mrs       = mrs,
            with_crc  = True,
            check_crc = False
        )

        self.udp_port = udp_port = udp.crossbar.get_port(ROCEV2_PORT, dw)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
