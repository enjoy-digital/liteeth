from litex.gen import *

from litex.soc.interconnect import stream

from liteeth.common import *

from litex.soc.interconnect.stream import SyncFIFO, Buffer

from liteeth.core.rocev2.mad_cm import LiteEthIBMAD
from liteeth.core.rocev2.common import *

from liteeth.core.rocev2 import pad, icrc
from liteeth.core.rocev2.qp import *
from liteeth.core.rocev2.mr import PERM as MR_PERM

# ROCEv2 TX ----------------------------------------------------------------------------------------

class LiteEthIBTransportPacketizer(VariablePacketizer):
    def __init__(self, dw=8):
        VariablePacketizer.__init__(self,
            eth_rocev2_description(dw),
            eth_udp_user_description(dw),
            IBT_headers,
            IBT_opmap
        )

# Consumes a read request and generates valid read responses by reading qp's memory region
class RDMAReadResponder(LiteXModule):
    def __init__(self, qp, mrs, dw=8):
        self.sink   = sink   = Endpoint(eth_rocev2_description(dw))
        self.source = source = Endpoint(add_params(eth_rocev2_description(dw), [("rq_last", 1), ("header_only", 1)]))

        # # #

        pmtu_bits  = log2_int(PMTU)

        mem_reader_sink   = Endpoint([("va", 64), ("len", bits_for(PMTU))])
        mem_reader_source = Endpoint([("data", dw)])

        msn      = Signal(24, reset_less=True)
        p_key    = Signal(16, reset_less=True)
        other_id = Signal(24, reset_less=True)

        psn      = Signal(24, reset_less=True)
        va       = Signal(64, reset_less=True)

        # For First/Middle/Last/Only packet variations
        first_packet = Signal()
        # If there are more packets left to send after the current one
        more_left = Signal()

        # Number of PMTU-sized blocks that need to be sent
        whole_blocks      = Signal(24)
        whole_blocks_left = Signal(24)
        remainder_bytes   = Signal(pmtu_bits)
        payload_length    = Signal(pmtu_bits + 1)

        # WaitPipe to fetch entire packet from RAM before send
        # (MAC cannot be stopped after packet start and RAM reads in bursts)
        read_wait_pipe = WaitPipe(add_params(eth_rocev2_description(dw), [("rq_last", 1)]), 8, PMTU, discarding=False, dw=dw)
        self.submodules += read_wait_pipe

        self.comb += Case(sink.r_key, {
            mr.r_key: [mem_reader_sink.connect(mr.reader.sink), mr.reader.source.connect(mem_reader_source)]
            for mr in mrs if MR_PERM.REMOTE_READ in mr.permissions
        })

        self.comb += [
            remainder_bytes.eq(sink.dma_len[:pmtu_bits]),
            whole_blocks.eq(sink.dma_len[pmtu_bits:])
        ]
        self.comb += more_left.eq(~((whole_blocks_left == 0) | ((whole_blocks_left == 1) & (remainder_bytes == 0))))

        self.comb += [
            Case(Cat(more_left, first_packet), {
                0b00: read_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_READ_response_Last),
                0b01: read_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_READ_response_Middle),
                0b10: read_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_READ_response_Only),
                0b11: read_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_READ_response_First)
            }),
            If(whole_blocks_left != 0,
                read_wait_pipe.sink.pad.eq(0),
                payload_length.eq(PMTU)
            ).Else(
                read_wait_pipe.sink.pad.eq((0b100 - (remainder_bytes & 0b11))[:2]),
                payload_length.eq(remainder_bytes)
            ),
            mem_reader_sink.len.eq(payload_length),

            Case(read_wait_pipe.sink.opcode, {
                BTH_OPCODE.RC.RDMA_READ_response_Last:
                    read_wait_pipe.sink.length.eq(IBT_header_length(BTH_OPCODE.RC.RDMA_READ_response_Last) + payload_length),
                BTH_OPCODE.RC.RDMA_READ_response_Middle:
                    read_wait_pipe.sink.length.eq(IBT_header_length(BTH_OPCODE.RC.RDMA_READ_response_Middle) + payload_length),
                BTH_OPCODE.RC.RDMA_READ_response_Only:
                    read_wait_pipe.sink.length.eq(IBT_header_length(BTH_OPCODE.RC.RDMA_READ_response_Only) + payload_length),
                BTH_OPCODE.RC.RDMA_READ_response_First:
                    read_wait_pipe.sink.length.eq(IBT_header_length(BTH_OPCODE.RC.RDMA_READ_response_First) + payload_length)
            }),
            read_wait_pipe.sink.psn.eq(psn),
            read_wait_pipe.sink.msn.eq(msn),

            read_wait_pipe.sink.p_key.eq(p_key),
            read_wait_pipe.sink.dest_qp.eq(other_id),

            read_wait_pipe.source.connect(source),

            mem_reader_sink.va.eq(va),
        ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(sink.valid & ~read_wait_pipe.full,
                NextValue(msn, qp.msn),
                NextValue(p_key, qp.receive_queue.p_key),
                NextValue(other_id, qp.other_id),

                NextValue(psn, sink.psn),
                NextValue(va, sink.va),
                NextValue(whole_blocks_left, whole_blocks),
                NextValue(first_packet, 1),
                If(sink.dma_len != 0,
                    NextValue(mem_reader_sink.valid, 1),
                    NextState("READ")
                ).Else(
                    NextState("EMPTY")
                )
            )
        )

        fsm.act("READ",
            If(mem_reader_sink.ready == 1, # Once the reader has consumed the request, we end it
                NextValue(mem_reader_sink.valid, 0)
            ),
            mem_reader_source.connect(read_wait_pipe.sink, keep={"valid", "ready", "data", "last"}),

            If(~more_left,
                read_wait_pipe.sink.rq_last.eq(1)
            ),

            If(mem_reader_source.valid & mem_reader_source.last & read_wait_pipe.sink.ready,
                read_wait_pipe.sink.validate.eq(1),
                NextValue(first_packet, 0),
                If(more_left,
                    NextValue(mem_reader_sink.valid, 1),
                    NextValue(whole_blocks_left, whole_blocks_left - 1),
                    NextValue(va, va + PMTU),
                    NextValue(psn, psn + 1)
                ).Else(
                    NextValue(mem_reader_sink.valid, 0),
                    # Consume read request
                    sink.ready.eq(1),
                    NextState("IDLE")
                )
            )
        )

        fsm.act("EMPTY",
            read_wait_pipe.sink.rq_last.eq(1),

            read_wait_pipe.sink.valid.eq(1),
            read_wait_pipe.sink.last.eq(1),
            read_wait_pipe.sink.header_only.eq(1),
            If(read_wait_pipe.sink.ready,
                read_wait_pipe.sink.validate.eq(1),
                sink.ready.eq(1),
                NextState("IDLE")
            )
        )

class LiteEthIBTransportTX(LiteXModule):
    def __init__(self, ip, mad_tx, qps, mrs, with_crc=True, buffered_out=True, dw=8):
        self.sink   = sink   = Endpoint(eth_rocev2_user_description(dw))
        self.source = source = Endpoint(eth_udp_user_description(dw))

        self.enable = enable = Signal()

        # Sink used by receiver to send acks
        self.ack_sink = None
        # Sink used by receiver to enqueue pending RDMA read requests
        self.read_requests_sink = None
        # Sink used by receiver to indicate order of acks/naks and read requests
        self.resp_choose_sink = None

        # # #

        # Input fifos
        ack_buf           = Buffer(eth_rocev2_description(dw), pipe_ready=True)
        read_requests_buf = Buffer(eth_rocev2_description(dw), pipe_ready=True)
        self.submodules += ack_buf, read_requests_buf

        # Expose fifo sinks
        self.ack_sink = ack_buf.sink
        self.read_requests_sink = read_requests_buf.sink

        # Add module that will construct packets responses to read requests
        read_responder = RDMAReadResponder(qps[1], mrs, dw=dw)
        self.submodules += read_responder

        pad_ins = pad.LiteEthIBTPaddingInserter(dw)
        self.submodules += pad_ins

        # Add ICRC at the end of messages
        if with_crc:
            self.tx_crc = tx_crc = icrc.LiteEthInfinibandICRCInserter(eth_mac_description(dw), eth_udp_user_description(dw))
            self.comb += [
                ip.tx.source.connect(tx_crc.calculator_sink, keep={"valid", "data", "last"}),
                # We listen passively to the output of ip, so no control of ready (we don't use connect for it)
                tx_crc.calculator_sink.ready.eq(ip.tx.source.ready),
            ]
            self.comb += tx_crc.source.connect(source)
            source = tx_crc.sink

        # The lower layers, once ready, have to pipe the entire data through without interruptions
        # Otherwise, the packet on the wire would have holes
        # We use this fact to cut ready timing
        if buffered_out:
            buff_out = Buffer(eth_udp_user_description(dw), pipe_ready=True)
            self.submodules += buff_out
            self.comb += buff_out.source.connect(source)
            source = buff_out.sink


        # Select between treating acks and read requests
        resp_choose_buf = Buffer([("ack", 1)], 6)
        self.submodules += resp_choose_buf

        self.resp_choose_sink = resp_choose_buf.sink

        # Packetizer
        self.packetizer = packetizer = LiteEthIBTransportPacketizer(dw=dw)

        self.comb += pad_ins.source.connect(packetizer.sink)

        # Read responder is always treating new requests
        self.comb += read_requests_buf.source.connect(read_responder.sink),
        # Data-Path.
        self.comb += [
            pad_ins.sink.tver.eq(0),
            pad_ins.sink.m.eq(0),
            pad_ins.sink.se.eq(0),
            pad_ins.sink.a.eq(0),

            If(~resp_choose_buf.source.valid,
                mad_tx.source.connect(pad_ins.sink, keep={"valid", "ready", "data", "last"}),
                pad_ins.sink.opcode.eq(BTH_OPCODE.UD.SEND_Only),
                pad_ins.sink.p_key.eq(qps[0].send_queue.p_key),
                pad_ins.sink.dest_qp.eq(1),
                pad_ins.sink.pad.eq(0b00),
                pad_ins.sink.psn.eq(qps[0].send_queue.psn),
                pad_ins.sink.q_key.eq(DEFAULT_CM_Q_Key),
                pad_ins.sink.src_qp.eq(1),
                pad_ins.sink.length.eq(
                    IBT_header_length(BTH_OPCODE.UD.SEND_Only) +
                    256 +
                    (0x4 if with_crc else 0)
                )
            ).Else(
                # Acks in the ack_fifo are ready to be sent
                # Read requests need to be treated by the read_responder (read from memory, generate packets, etc.)
                If(resp_choose_buf.source.ack,
                    ack_buf.source.connect(pad_ins.sink, keep={"valid", "ready", "opcode", "syndrome", "msn"}),
                    # Acks have no payload
                    pad_ins.sink.header_only.eq(1),

                    pad_ins.sink.pad.eq(0b00), # Acks have padding of 0
                    Case(ack_buf.source.dest_qp, {qp.id : [
                        pad_ins.sink.p_key.eq(qp.receive_queue.p_key),
                        pad_ins.sink.dest_qp.eq(qp.other_id)
                    ] for qp in qps}),
                    pad_ins.sink.psn.eq(ack_buf.source.psn),
                    pad_ins.sink.length.eq(
                        IBT_header_length(BTH_OPCODE.RC.Acknowledge) +
                        (0x4 if with_crc else 0)
                    )
                ).Else(
                    read_responder.source.connect(pad_ins.sink, keep={"valid", "ready", "last", "data", "opcode", "pad", "psn", "va", "r_key", "dma_len", "msn", "p_key", "dest_qp", "header_only"}),
                    pad_ins.sink.length.eq(
                        read_responder.source.length +
                        (0x100 - read_responder.source.length[:2])[:2] +
                        (0x4 if with_crc else 0)
                    )
                ),
            ),
            source.dst_port.eq(rocev2_port),
            source.ip_address.eq(qps[1].ip_address),
            source.length.eq(packetizer.sink.length)
        ]

        # Control-Path (FSM).
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            # Source length has to stay correct for ICRC even when the fifo entry has been consumed
            If(packetizer.source.valid & enable,
                NextState("SEND")
            )
        )

        fsm.act("SEND",
            packetizer.source.connect(source, keep={"valid", "ready", "last", "data"}),
            If(source.valid & source.ready,
                If(source.last,
                    NextState("IDLE"),
                    resp_choose_buf.source.ready.eq(resp_choose_buf.source.ack | read_responder.source.rq_last)
                )
            )
        )


# RX/TX interface ----------------------------------------------------------------------------------

class _Acknowledge:
    @staticmethod
    def emit(leading_bits, type, dest_qp, msn, psn, ack_sink):
        if leading_bits & 0b11 == 0b10:
            print("Reserved pattern")
            raise ValueError
        return [
            ack_sink.valid.eq(1),
            ack_sink.last.eq(1),
            ack_sink.psn.eq(psn),
            ack_sink.opcode.eq(BTH_OPCODE.RC.Acknowledge),
            ack_sink.dest_qp.eq(dest_qp),
            ack_sink.syndrome.eq(Cat(Constant(type, 5), Constant(leading_bits, 2), Constant(0, 1))),
            ack_sink.msn.eq(msn)
        ]

class ACK(_Acknowledge):
    @staticmethod
    def emit(dest_qp, msn, psn, ack_sink):
        return super(__class__, __class__).emit(0b00, 0b00000, dest_qp, msn, psn, ack_sink)

class RNR_NAK(_Acknowledge):
    # Look at RNR ACK timer field times to know which value corresponds to what time
    @staticmethod
    def emit(time, dest_qp, msn, psn, ack_sink, qps):
        return [
            Case(dest_qp, {qp.id : [
                If(~qp.nak_sent,
                    NextValue(qp.nak_sent, 1),
                    super(__class__, __class__).emit(0b01, time, dest_qp, msn, psn, ack_sink)
                )
            ] for qp in qps[1:]}),
        ]

class NAK(_Acknowledge):
    class Code(IntEnum):
        PSN_Sequence_Error       = 0
        Invalid_Request          = 1
        Remote_Access_Error      = 2
        Remote_Operational_Error = 3
        Invalid_RD_Request       = 4

    @staticmethod
    def emit(type, dest_qp, msn, psn, ack_sink, duplicate, qps):
        return [
            If(~duplicate,
                Case(dest_qp, {qp.id : [
                    If(~qp.nak_sent,
                        NextValue(qp.nak_sent, 1),
                        super(__class__, __class__).emit(0b11, type, dest_qp, msn, psn, ack_sink)
                    )
                ] for qp in qps[1:]}),
            )
        ]

# ROCEv2 RX ----------------------------------------------------------------------------------------

class LiteEthIBTransportDepacketizer(VariableDepacketizer):
    def __init__(self, dw=8):
        VariableDepacketizer.__init__(self,
            eth_udp_user_description(dw),
            eth_rocev2_description(dw),
            IBT_headers,
            IBT_opmap
        )

class LiteEthIBTransportRX(LiteXModule):
    def __init__(self, ip, ack_sink, read_sink, resp_choose_sink, mad_rx, qps, mrs, with_crc=True, dw=8):
        self.sink   = sink   = Endpoint(eth_udp_user_description(dw))
        self.source = source = Endpoint(eth_rocev2_user_description(dw))

        # # #

        # Params
        pmtu_bits = log2_int(PMTU)

        # ICRC
        if with_crc:
            self.rx_crc = rx_crc = icrc.LiteEthInfinibandICRCChecker(eth_mac_description(dw), eth_udp_user_description(dw))

            self.comb += [
                ip.rx.sink.connect(rx_crc.calculator_sink, keep={"valid", "data", "last"}),
                # We listen passively to the output of ip,
                # so no control of ready (we don't use connect for it)
                rx_crc.calculator_sink.ready.eq(ip.rx.sink.ready),
            ]

        ### Data-Path.
        # Depacketizer.
        self.depacketizer = depacketizer = LiteEthIBTransportDepacketizer(dw=dw)
        # Saved params (to retain depacketizer parameters after transfer is done, for validation)
        params = Endpoint(add_params(eth_rocev2_description(dw=dw), [("header_only", 1)]))

        # Local fifo into TX's buffer to cut timing
        self.ack_pipe  = ack_pipe  = SyncFIFO(eth_rocev2_description(dw), RESPONDER_RESOURCES, buffered=True)
        self.read_pipe = read_pipe = SyncFIFO(eth_rocev2_description(dw), RESPONDER_RESOURCES, buffered=True)
        self.resp_choose_pipe = resp_choose_pipe = SyncFIFO([("ack", 1)], RESPONDER_RESOURCES * 2, buffered=True)
        self.comb += [
            ack_pipe.source.connect(ack_sink),
            read_pipe.source.connect(read_sink),
            resp_choose_pipe.source.connect(resp_choose_sink)
        ]
        ack_sink = ack_pipe.sink
        read_sink = read_pipe.sink
        resp_choose_sink = resp_choose_pipe.sink

        self.comb += [
            # Both the ack and read sinks should never be valid at the same time
            resp_choose_sink.valid.eq(ack_sink.valid | read_sink.valid),
            resp_choose_sink.ack.eq(ack_sink.valid)
        ]


        if with_crc:
            self.comb += [
                sink.connect(rx_crc.sink),
                rx_crc.source.connect(depacketizer.sink)
            ]
        else:
            self.comb += sink.connect(depacketizer.sink)

        # Padding remover
        pad_rem = pad.LiteEthIBTPaddingRemover(dw)
        self.add_module("pad_rem", pad_rem)

        self.comb += depacketizer.source.connect(pad_rem.sink)

        # Wait pipe
        wait_pipe = WaitPipe(eth_rocev2_description(dw), 16, PMTU, dw=dw)
        self.submodules += wait_pipe

        ### Opcode splitting
        # Connection type and operation extracted from opcode
        opcode_conn_type = Signal(3)
        opcode_op        = Signal(5)

        # Opcode decomposition
        self.comb += Cat(opcode_op, opcode_conn_type).eq(params.opcode)

        ### Signals linked to the qp's state
        conn_type         = Signal(3, reset_less=True)
        qp_state          = Signal(max=5, reset_less=True)
        p_key             = Signal(16, reset_less=True)
        msn               = Signal(24, reset_less=True)
        msn_next          = Signal(24)
        rem_reth          = Signal()
        saved_rdma      = Record([
            ("va",       64, DIR_M_TO_S),
            ("r_key",    32, DIR_M_TO_S),
            ("dma_len",  32, DIR_M_TO_S)
        ], reset_less=True)
        current_rdma      = Record([
            ("va",       64, DIR_M_TO_S),
            ("r_key",    32, DIR_M_TO_S),
            ("dma_len",  32, DIR_M_TO_S)
        ], reset_less=True)
        va_next           = Signal(64)
        expected_psn      = Signal(24, reset_less=True)
        expected_psn_next = Signal(24)
        psn_jump          = Signal(24)

        qp_rcv_wire_next = Signal()

        # Extract qp parameters
        init_dict = {
            qp.id : [
                p_key.eq(qp.receive_queue.p_key),
                msn.eq(qp.msn),
                expected_psn.eq(qp.receive_queue.psn),
                conn_type.eq(qp.conn_type),
            ] for qp in qps
        }
        init_dict[qps[1].id].extend([
            qps[1].rdma_state.connect(saved_rdma, keep={"va", "r_key", "dma_len"}),
        ])
        init_dict["default"] = [
            p_key.eq(0),
            msn.eq(0),
            expected_psn.eq(0),
            conn_type.eq(0),
            saved_rdma.va.eq(0),
            saved_rdma.r_key.eq(0),
            saved_rdma.dma_len.eq(0),
        ]
        self.sync += [
            If(pad_rem.source.valid,
                qp_state.eq(qps[1].qp_state),
                Case(params.dest_qp, init_dict)
            )
        ]

        self.comb += If(is_in(opcode_op, [
            BTH_OPCODE_OP.RDMA_READ_Request,
            BTH_OPCODE_OP.RDMA_WRITE_First,
            BTH_OPCODE_OP.RDMA_WRITE_Only,
            BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate
        ]), [
            current_rdma.va.eq(params.va),
            current_rdma.r_key.eq(params.r_key),
            current_rdma.dma_len.eq(params.dma_len)
        ]).Else(
            saved_rdma.connect(current_rdma, keep={"va", "r_key", "dma_len"})
        )

        self.comb += current_rdma.connect(wait_pipe.sink, keep={"va", "r_key", "dma_len"})

        ### Packet payload/integrity checks
        # Packet header checks
        # Silent drop validations
        valid_tver  = Signal()
        valid_dest  = Signal()
        valid_p_key = Signal()
        check_q_key = Signal()
        valid_q_key = Signal()

        # NAK validations
        check_psn         = Signal()
        valid_psn         = Signal()
        duplicate         = Signal()
        valid_opcode_seq  = Signal()
        valid_opcode      = Signal()
        check_r_key_write = Signal()
        check_r_key_read  = Signal()
        valid_r_key       = Signal()

        rdma_under        = Signal()
        rdma_over         = Signal()

        valid_fields   = Signal()

        padding_valid     = Signal()
        zero_pad_check = Signal()
        check_dma_len  = Signal()

        # Payload validations
        payload_length = Signal(pmtu_bits + 1)

        expect_full_packet  = Signal()
        expect_empty_packet = Signal()
        no_empty_packet     = Signal()

        ### Header checks logic
        # Dropping validations
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

        # NAK validations
        self.comb += [
            check_psn.eq(conn_type == QP_CONN_TYPE.RC),
            valid_psn.eq(~check_psn | (params.psn == expected_psn)),
            duplicate.eq(~((expected_psn - params.psn) >> 23) & (expected_psn != params.psn)),
            valid_opcode_seq.eq(~qps[1].op_seq_check.invalid_sequence),
            Case(conn_type, {
                QP_CONN_TYPE.RC: valid_opcode.eq(is_in(opcode_op, IBT_RC_OPS)),
                QP_CONN_TYPE.UD:
                    If(params.dest_qp != 1,
                        valid_opcode.eq(is_in(opcode_op, IBT_UD_OPS))
                    ).Else(
                        valid_opcode.eq(opcode_op == BTH_OPCODE_OP.SEND_Only)
                    ),
                "default": valid_opcode.eq(0)
            }),
            check_r_key_write.eq(is_in(opcode_op, [
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Only,
                BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate
            ]) & (params.dma_len != 0)), # o9-55
            check_r_key_read.eq((opcode_op == BTH_OPCODE_OP.RDMA_READ_Request)
               & (params.dma_len != 0)), # o9-55
            valid_r_key.eq(
                (~check_r_key_write | is_in(params.r_key, [mr.r_key for mr in mrs if MR_PERM.REMOTE_WRITE in mr.permissions])) &
                (~check_r_key_read  | is_in(params.r_key, [mr.r_key for mr in mrs if MR_PERM.REMOTE_READ  in mr.permissions]))
            )
        ]

        self.comb += [
            padding_valid.eq(~pad_rem.source.error),
            zero_pad_check.eq(is_in(opcode_op, [
                BTH_OPCODE_OP.SEND_First,
                BTH_OPCODE_OP.SEND_Middle,
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Middle
            ])),
            check_dma_len.eq(is_in(opcode_op, [
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Only,
                BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate,
                BTH_OPCODE_OP.RDMA_READ_Request
            ]))
        ]
        # Packet sequence tracking
        self.comb += qps[1].op_seq_check.opcode.eq(opcode_op)

        # Control-Path (FSM).
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            pad_rem.source.ready.eq(1),
            If(pad_rem.source.valid,
                pad_rem.source.ready.eq(0),
                NextValue(payload_length, 0),
                NextState("RECEIVE")
            )
        )

        self.sync += If(fsm.ongoing("IDLE"),
            pad_rem.source.connect(params, omit={"valid", "ready", "last", "data", "error"})
        )

        # Notify CM a message was received
        self.sync += mad_rx.qp_rcv_wire.eq(qp_rcv_wire_next)

        # Full packet reception is performed here
        fsm.act("RECEIVE",
            pad_rem.source.connect(wait_pipe.sink, omit={"error"}),
            If(pad_rem.source.valid,
                If(pad_rem.source.ready,
                    NextValue(payload_length, payload_length + 1)
                ),
                If(pad_rem.source.ready & pad_rem.source.last,
                    If(pad_rem.source.header_only,
                        NextValue(payload_length, 0)
                    ),
                    NextState("VALIDATE")
                )
            )
        )

        self.sync += If(fsm.ongoing("RECEIVE"),
            Case(current_rdma.r_key, {
                mr.r_key: [
                    rdma_under.eq(current_rdma.va < mr.region_start),
                    If(opcode_op == BTH_OPCODE_OP.RDMA_READ_Request,
                        rdma_over.eq(pad_rem.source.va + pad_rem.source.dma_len > mr.region_start + mr.region_size)
                    ).Else(
                        # We set greater or equal, because this is done synchronously on RECEIVE
                        # And so the payload_length is the number of bytes written so far
                        # Thus, one cycle before validate, payload_length will be one less than the actual size
                        rdma_over.eq(current_rdma.va + payload_length >= mr.region_start + mr.region_size)
                    )
                ]
                for mr in mrs
            })
        )

        # Only useful for RDMA Read
        self.comb += [
            If(params.dma_len == 0,
                psn_jump.eq(1),
            ).Else(
                psn_jump.eq(params.dma_len[pmtu_bits:] + (params.dma_len[:pmtu_bits] != 0))
            )
        ]

        # Checks are performed here
        fsm.act("VALIDATE",
            NextState("IDLE"),
            wait_pipe.sink.invalidate.eq(1),
            # Validate header and ICRC
            If((rx_crc.valid_crc if with_crc else 1) & # Valid crc
               valid_tver &
               valid_dest &
               valid_p_key &
               valid_q_key,
                # Invalid or duplicate psn
                If(~valid_psn,
                    If(~duplicate,
                        NAK.emit(NAK.Code.PSN_Sequence_Error, params.dest_qp, msn, params.psn, ack_sink, duplicate, qps)
                    ).Else(
                        valid_fields.eq(1)
                    )
                ).Elif(~valid_opcode_seq | ~valid_opcode,
                    NAK.emit(NAK.Code.Invalid_Request, params.dest_qp, msn, params.psn, ack_sink, duplicate, qps)
                ).Elif(~valid_r_key | ((check_r_key_read | check_r_key_write) & (rdma_under | rdma_over)),
                    NAK.emit(NAK.Code.Remote_Access_Error, params.dest_qp, msn, params.psn, ack_sink, duplicate, qps),
                ).Else(
                    # Valid
                    valid_fields.eq(1),
                )
            ),

            # Check and transfer signals
            params.connect(wait_pipe.sink, omit={"valid", "ready", "last", "data", "va", "r_key", "dma_len"}),
            expected_psn_next.eq(expected_psn + 1),
            msn_next.eq(msn),
            Case(opcode_op, {
                BTH_OPCODE_OP.SEND_First: [
                    expect_full_packet.eq(1),
                ],
                BTH_OPCODE_OP.RDMA_WRITE_First: [
                    va_next.eq(current_rdma.va + PMTU),
                    expect_full_packet.eq(1),
                    rem_reth.eq(1),
                ],
                BTH_OPCODE_OP.RDMA_READ_Request: [
                    expect_empty_packet.eq(1),
                    expected_psn_next.eq(expected_psn + psn_jump),
                    msn_next.eq(msn + 1)
                ],
                BTH_OPCODE_OP.Acknowledge: [
                    expect_empty_packet.eq(1),
                ],
                # Persistent SEND --------------------------------------------------------------------------
                BTH_OPCODE_OP.SEND_Middle: [
                    expect_full_packet.eq(1)
                ],
                BTH_OPCODE_OP.SEND_Last: [
                    no_empty_packet.eq(1),
                    msn_next.eq(msn + 1),
                ],
                BTH_OPCODE_OP.SEND_Last_with_Immediate: [
                    no_empty_packet.eq(1),
                    msn_next.eq(msn + 1)
                ],

                # Persistent RDMA_READ ---------------------------------------------------------------------
                BTH_OPCODE_OP.RDMA_READ_response_Middle: [
                    expect_full_packet.eq(1),
                ],
                BTH_OPCODE_OP.RDMA_READ_response_Last: [
                    no_empty_packet.eq(1),
                ],

                # Persistent RDMA WRITE --------------------------------------------------------------------
                BTH_OPCODE_OP.RDMA_WRITE_Middle: [
                    expect_full_packet.eq(1),
                    va_next.eq(current_rdma.va + PMTU),
                ],
                BTH_OPCODE_OP.RDMA_WRITE_Last: [
                    no_empty_packet.eq(1),
                    msn_next.eq(msn + 1),
                ],
                BTH_OPCODE_OP.RDMA_WRITE_Last_with_Immediate: [ # HOW
                    no_empty_packet.eq(1),
                    msn_next.eq(msn + 1),
                ]
            }),

            # Validate payload
            If(valid_fields,
                If((~padding_valid) |
                   (zero_pad_check & (params.pad != 0b00)) |
                   (payload_length > PMTU) |
                   (expect_empty_packet & (payload_length != 0)) |
                   (expect_full_packet & (payload_length != PMTU)) |
                   (no_empty_packet & (payload_length == 0)) |
                   (check_dma_len & (current_rdma.dma_len > 2**31)),
                    NAK.emit(NAK.Code.Invalid_Request, params.dest_qp, msn, params.psn, ack_sink, duplicate, qps),
                    # Flush saved packet
                    wait_pipe.sink.invalidate.eq(1),
                ).Else(
                    Case(params.dest_qp, {qp.id : [
                        NextValue(qp.nak_sent, 0),
                    ] for qp in qps[1:]}),
                    If((conn_type == QP_CONN_TYPE.RC) & (opcode_op != BTH_OPCODE_OP.RDMA_READ_Request) & (~wait_pipe.full), # Read requests are ACKed with a read response
                        ACK.emit(params.dest_qp, msn_next, params.psn, ack_sink)
                    ),
                    If(~(duplicate & (opcode_op != BTH_OPCODE_OP.RDMA_READ_Request)),
                        wait_pipe.sink.invalidate.eq(0),
                        wait_pipe.sink.validate.eq(1),
                        If(params.dest_qp == qps[1].id,
                            qp_rcv_wire_next.eq(1)
                        )
                    ),
                    If(~duplicate,
                        If(~wait_pipe.full,
                            Case(params.dest_qp, {qp.id :
                                [
                                    NextValue(qp.receive_queue.psn, expected_psn_next),
                                    NextValue(qp.msn, msn_next),
                                ] + ([
                                    NextValue(qp.rdma_state.va, va_next),
                                    If(rem_reth,
                                        NextValue(qp.rdma_state.r_key, current_rdma.r_key),
                                        NextValue(qp.rdma_state.dma_len, current_rdma.dma_len)
                                    )
                                ] if qp.id.value != 1 else [])
                            for qp in qps}),
                            If(params.dest_qp != 1,
                                qps[1].op_seq_check.update.eq(1),
                            )
                        ).Elif(conn_type == QP_CONN_TYPE.RC,
                            RNR_NAK.emit(0b00001, params.dest_qp, msn, params.psn, ack_sink, qps)
                        )
                    )
                )
            )
        )

        ### RX output dispatch

        # Packet redirection to correct source is performed here
        # Wait pipe output is valid, so we send it to the next processing step
        wait_pipe_opcode_conn_type = Signal(3)
        wait_pipe_opcode_op = Signal(5)
        self.comb += Cat(wait_pipe_opcode_op, wait_pipe_opcode_conn_type).eq(wait_pipe.source.opcode)

        mem_writer_sink   = Endpoint([("data", dw), ("va", 64)])

        self.comb += Case(wait_pipe.source.r_key, {
            mr.r_key: [mem_writer_sink.connect(mr.writer.sink)]
            for mr in mrs if MR_PERM.REMOTE_WRITE in mr.permissions
        })

        RC_out_sel_dict = {
            # SEND
            (
                BTH_OPCODE_OP.SEND_First,
                BTH_OPCODE_OP.SEND_Only,
                BTH_OPCODE_OP.SEND_Only_with_Immediate,
                BTH_OPCODE_OP.SEND_Middle,
                BTH_OPCODE_OP.SEND_Last,
                BTH_OPCODE_OP.SEND_Last_with_Immediate,
            ):
            [
                wait_pipe.source.connect(source, keep={"valid", "ready", "last", "data", "dest_qp", "length"}),
            ],
            # RDMA_WRITE
            (
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Only,
                BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate,
                BTH_OPCODE_OP.RDMA_WRITE_Middle,
                BTH_OPCODE_OP.RDMA_WRITE_Last,
                BTH_OPCODE_OP.RDMA_WRITE_Last_with_Immediate
            ):
            [
                If(wait_pipe.source.dma_len == 0,
                    wait_pipe.source.ready.eq(1)
                ).Else(
                    wait_pipe.source.connect(mem_writer_sink, keep={"valid", "ready", "last", "data"}),
                    mem_writer_sink.va.eq(wait_pipe.source.va)
                )
            ],
            # RDMA_READ_Request
                BTH_OPCODE_OP.RDMA_READ_Request:
            [
                wait_pipe.source.connect(read_sink, omit={"header_only"}),
            ],
            # Ignored
            (
                BTH_OPCODE_OP.RDMA_READ_response_First,
                BTH_OPCODE_OP.RDMA_READ_response_Only,
                BTH_OPCODE_OP.RDMA_READ_response_Middle,
                BTH_OPCODE_OP.RDMA_READ_response_Last,
                BTH_OPCODE_OP.Acknowledge,
                # BTH_OPCODE_OP.ATOMIC_Acknowledge,
                # BTH_OPCODE_OP.CmpSwap,
                # BTH_OPCODE_OP.FetchAdd,
                # BTH_OPCODE_OP.SEND_Last_with_Invalidate,
                # BTH_OPCODE_OP.SEND_Only_with_Invalidate,
            ):
            [
                wait_pipe.source.ready.eq(1) # Ignore
            ]
        }

        RC_out_sel_dict_final = {}
        for k, v in RC_out_sel_dict.items():
            if isinstance(k, tuple):
                RC_out_sel_dict_final.update({kk: v for kk in k})
            else:
                RC_out_sel_dict_final[k] = v

        self.comb += Case(wait_pipe_opcode_conn_type, {
            QP_CONN_TYPE.RC: Case(wait_pipe_opcode_op, RC_out_sel_dict_final),
            QP_CONN_TYPE.UD:
                If(wait_pipe.source.dest_qp == 1,
                    wait_pipe.source.connect(mad_rx.sink, keep={"valid", "ready", "last", "data"})
                ).Else(
                    wait_pipe.source.connect(source, keep={"valid", "ready", "last", "data", "dest_qp"}),
                )
        })

# ROCEv2 RX + TX -----------------------------------------------------------------------------------
class LiteEthIBTransport(LiteXModule):
    def __init__(self, ip, udp, mrs, clk_freq, dw=8):
        qp = LiteEthIBQP(id=0xdeaded)
        self.submodules += qp

        special_qp = LiteEthIBSpecialQP()
        self.submodules += special_qp

        qps = [special_qp, qp]

        self.mad = mad = LiteEthIBMAD(qps, clk_freq, dw=dw)

        self.tx = tx = LiteEthIBTransportTX(
            ip            = ip,
            mad_tx        = mad.tx,
            qps           = qps,
            mrs           = mrs,
            with_crc      = True,
            buffered_out  = True,
        )
        self.rx = rx = LiteEthIBTransportRX(
            ip               = ip,
            ack_sink         = tx.ack_sink,
            read_sink        = tx.read_requests_sink,
            resp_choose_sink = tx.resp_choose_sink,
            mad_rx           = mad.rx,
            qps              = qps,
            mrs              = mrs,
            with_crc         = True,
        )

        self.comb += [
            # rx.source.connect(qp.receive_queue.sink),
            #qp.send_queue.source.connect(tx.sink)
        ]

        self.udp_port = udp_port = udp.crossbar.get_port(rocev2_port, dw)
        self.comb += [
            tx.source.connect(udp_port.sink),
            udp_port.source.connect(rx.sink)
        ]
