from liteeth.common import *

from liteeth.core.rocev2.mr import PERM as MR_PERM
from liteeth.core.rocev2.common import WaitPipe
from liteeth.core.rocev2.qp import LiteEthIBQP
from liteeth.core.rocev2.ack import ACK, NAK, RNR_NAK

from litex.soc.interconnect.stream import Endpoint, Buffer, SyncFIFO

# Consumes a read request and generates valid read responses by reading qp's memory region
class LiteEthRDMAReadResponder(LiteXModule):
    def __init__(self, qp, mrs, dw=8):
        self.sink   = sink   = Endpoint(eth_rocev2_description(dw))
        self.source = source = Endpoint(
            add_params(
                eth_rocev2_description(dw),
                [("rq_last", 1), ("header_only", 1)]
            )
        )

        # # #

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
        remainder_bytes   = Signal(PMTU_BITS)
        payload_length    = Signal(PMTU_BITS + 1)
        header_length     = Signal(32)

        # WaitPipe to fetch entire packet from RAM before send
        # (MAC cannot be stopped after packet start and RAM reads in bursts)
        read_wait_pipe = WaitPipe(
            add_params(
                eth_rocev2_description(dw),
                [("rq_last", 1)]
            ), 8, PMTU, discarding=False, dw=dw
        )
        self.submodules.read_wait_pipe = read_wait_pipe

        self.comb += If(mem_reader_sink.valid | mem_reader_source.ready,
            Case(sink.r_key, {
                Constant(mr.r_key, 32): [
                    mem_reader_sink.connect(mr.reader.sink),
                    mr.reader.source.connect(mem_reader_source)]
                for mr in mrs if MR_PERM.REMOTE_READ in mr.permissions
            })
        )

        self.comb += [
            remainder_bytes.eq(sink.dma_len[:PMTU_BITS]),
            whole_blocks.eq(sink.dma_len[PMTU_BITS:])
        ]
        self.comb += more_left.eq(
            ~((whole_blocks_left == 0) |
              ((whole_blocks_left == 1) & (remainder_bytes == 0)))
        )

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

            IBT_header_length(read_wait_pipe.sink.opcode, header_length),
            read_wait_pipe.sink.length.eq(header_length + payload_length),

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
                NextValue(p_key, qp.p_key),
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
                read_wait_pipe.validate_sink.validate.eq(1),
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
                read_wait_pipe.validate_sink.validate.eq(1),
                sink.ready.eq(1),
                NextState("IDLE")
            )
        )

class LiteEthRDMAResponderTX(LiteXModule):
    def __init__(self, mad_tx, qps, mrs, dw=8):
        self.ack_sink         = ack_sink         = Endpoint(eth_rocev2_description(dw))
        self.read_sink        = read_sink        = Endpoint(eth_rocev2_description(dw))
        self.resp_choose_sink = resp_choose_sink = Endpoint([("ack", 1)])

        self.source = source = Endpoint(
            add_params(
                eth_rocev2_description(dw),
                [("header_only", 1)]
            )
        )

        # # #

        ## Buffering sinks
        # Buffer used to receive queued acks/naks
        ack_buf         = Buffer(eth_rocev2_description(dw))
        # Buffer used to receive queued RDMA read requests
        read_buf        = Buffer(eth_rocev2_description(dw))
        # Buffer used by responder to indicate order of acks/naks and RDMA read requests
        resp_choose_buf = Buffer([("ack", 1)])

        self.submodules.ack_buf         = ack_buf
        self.submodules.read_buf        = read_buf
        self.submodules.resp_choose_buf = resp_choose_buf

        self.comb += [
            ack_sink.connect(ack_buf.sink),
            read_sink.connect(read_buf.sink),
            resp_choose_sink.connect(resp_choose_buf.sink)
        ]

        ack_sink         = ack_buf.source
        read_sink        = read_buf.source
        resp_choose_sink = resp_choose_buf.source

        ## Read responder
        # Add module that will construct packet responses to read requests
        read_responder = LiteEthRDMAReadResponder(qps[1], mrs, dw=dw)
        self.submodules.read_responder = read_responder

        # Read responder is always treating new requests
        self.comb += read_sink.connect(read_responder.sink)

        # Packet send logic
        self.comb += [
            source.tver.eq(0),
            source.m.eq(0),
            source.se.eq(0),
            source.a.eq(0),

            If(~resp_choose_sink.valid,
                mad_tx.source.connect(source, keep={"valid", "ready", "data", "last"}),
                source.opcode.eq(BTH_OPCODE.UD.SEND_Only),
                source.p_key.eq(qps[0].p_key),
                source.dest_qp.eq(1),
                source.pad.eq(0b00),
                source.psn.eq(qps[0].send_queue.psn),
                source.q_key.eq(DEFAULT_CM_Q_Key),
                source.src_qp.eq(1),
                source.length.eq(
                    IBT_get_header_length(BTH_OPCODE.UD.SEND_Only) +
                    256
                )
            ).Else(
                # Acks in the ack_fifo are ready to be sent
                # Read requests need to be treated by the read_responder
                # (read from memory, generate packets, etc.)
                If(resp_choose_sink.ack,
                    ack_sink.connect(source, keep={"valid", "ready", "opcode", "syndrome", "msn"}),
                    # Acks have no payload
                    source.header_only.eq(1),
                    source.last.eq(1),

                    source.pad.eq(0b00), # Acks have padding of 0
                    Case(ack_sink.dest_qp, {qp.id : [
                        source.p_key.eq(qp.p_key),
                        source.dest_qp.eq(qp.other_id)
                    ] for qp in qps}),
                    source.psn.eq(ack_sink.psn),
                    source.length.eq(
                        IBT_get_header_length(BTH_OPCODE.RC.Acknowledge)
                    )
                ).Else(
                    read_responder.source.connect(source, keep={
                        "valid",
                        "ready",
                        "last",
                        "data",
                        "opcode",
                        "pad",
                        "psn",
                        "va",
                        "r_key",
                        "dma_len",
                        "msn",
                        "p_key",
                        "dest_qp",
                        "header_only"
                    }),
                    source.length.eq(read_responder.source.length)
                )
            ),

            If(source.valid & source.ready & source.last,
                resp_choose_sink.ready.eq(resp_choose_sink.ack | read_responder.source.rq_last)
            )
        ]

class LiteEthRDMAResponderRX(LiteXModule):
    def __init__(self, responder_tx, mad_rx, qps, cq, mrs, dw=8):
        self.sink = sink = Endpoint(
            add_params(
                eth_rocev2_description(dw),
                [
                    ("header_only", 1),
                    ("payload_length", PMTU_BITS + 1),
                    ("invalid_packet", 1),
                    ("validate", 1),
                    ("ip_address", 32)
                ]
            )
        )

        # # #

        ## Piping messages for Responder_TX
        # Pipe used to queue acks
        ack_pipe         = SyncFIFO(eth_rocev2_description(dw), RESPONDER_RESOURCES, buffered=True)
        # Pipe used by responder to enqueue pending RDMA read requests
        read_pipe        = SyncFIFO(eth_rocev2_description(dw), RESPONDER_RESOURCES, buffered=True)
        # Pipe used by responder to indicate order of acks/naks and read requests
        resp_choose_pipe = SyncFIFO([("ack", 1)], RESPONDER_RESOURCES * 2, buffered=True)

        self.submodules.ack_pipe         = ack_pipe
        self.submodules.read_pipe        = read_pipe
        self.submodules.resp_choose_pipe = resp_choose_pipe

        self.comb += [
            If(qps[1].qp_state != LiteEthIBQP.ERROR,
                ack_pipe.source.connect(responder_tx.ack_sink),
                read_pipe.source.connect(responder_tx.read_sink),
                resp_choose_pipe.source.connect(responder_tx.resp_choose_sink)
            ).Else(
                ack_pipe.source.ready.eq(1),
                read_pipe.source.ready.eq(1),
                resp_choose_pipe.source.ready.eq(1)
            )
        ]

        ack_source         = ack_pipe.sink
        read_source        = read_pipe.sink
        resp_choose_source = resp_choose_pipe.sink

        self.comb += [
            # Both the ack and read sinks should never be valid at the same time
            resp_choose_source.valid.eq(ack_source.valid | read_source.valid),
            resp_choose_source.ack.eq(ack_source.valid)
        ]

        ## Params to save sink parameters
        params = Endpoint(
            add_params(eth_rocev2_description(dw=dw), [
                ("header_only", 1),
                ("ip_address", 32)
            ])
        )
        self.sync += sink.connect(params, omit={
            "valid",
            "ready",
            "last",
            "data",
            "payload_length",
            "invalid_packet",
            "validate"
        })

        # Pre-completion queue
        pre_cq = SyncFIFO(eth_rocev2_cq_description(), INITIATOR_DEPTH*2 + 0x10, buffered=True)
        self.submodules.pre_cq = pre_cq

        # Wait pipe
        wait_pipe = WaitPipe(
            layout = EndpointDescription(
                payload_layout = [("data", dw)],
                param_layout   = [
                    ("conn_type", 2),
                    ("dest_qp",   24),
                    ("va",        64),
                    ("mem_key",   32),
                    ("send",      1),
                    ("signal_cq", 1)
                ]
            ),
            depth      = 16,
            block_size = PMTU,
            dw         = dw
        )
        self.submodules.wait_pipe = wait_pipe

        qp_rcv_wire_next = Signal()
        # Notify CM a message was received
        self.sync += mad_rx.qp_rcv_wire.eq(qp_rcv_wire_next)

        ## Memory location
        self.current_mem_loc = current_mem_loc = Record([
            ("va",       64, DIR_M_TO_S),
            ("mem_key",  32, DIR_M_TO_S)
        ], reset_less=True)

        ## Reception logic
        # Connection type and operation extracted from opcode
        opcode_conn_type = Signal(3)
        opcode_op        = Signal(5)

        # Opcode decomposition
        self.comb += Cat(opcode_op, opcode_conn_type).eq(params.opcode)

        conn_type         = Signal(3,  reset_less=True)
        msn               = Signal(24, reset_less=True)
        msn_next          = Signal(24)
        va_next           = Signal(64)
        expected_psn      = Signal(24, reset_less=True)
        expected_psn_next = Signal(24)
        psn_jump          = Signal(24)

        saved_mem_loc      = Record([
            ("va",       64, DIR_M_TO_S),
            ("mem_key",  32, DIR_M_TO_S)
        ], reset_less=True)

        # Extract qp parameters
        init_dict = {
            qp.id : [
                msn.eq(qp.msn),
                expected_psn.eq(qp.receive_queue.psn),
                conn_type.eq(qp.conn_type),
                qp.receive_queue.rdma_state.connect(saved_mem_loc, keep={"va", "mem_key"})
            ] for qp in qps if qp.id.value != 1
        }
        init_dict[1] = {
            msn.eq(qps[0].msn),
            expected_psn.eq(qps[0].receive_queue.psn),
            conn_type.eq(qps[0].conn_type),
        }
        init_dict["default"] = [
            msn.eq(0),
            expected_psn.eq(0),
            conn_type.eq(0),
            saved_mem_loc.va.eq(0),
            saved_mem_loc.mem_key.eq(0)
        ]
        self.sync += [
            If(sink.valid,
                Case(sink.dest_qp, init_dict)
            )
        ]

        # psn_jump (only useful for RDMA Read)
        self.comb += [
            If(params.dma_len == 0,
                psn_jump.eq(1),
            ).Else(
                psn_jump.eq(params.dma_len[PMTU_BITS:] + (params.dma_len[:PMTU_BITS] != 0))
            )
        ]

        self.comb += is_in_do(opcode_op, [
            ([
                BTH_OPCODE_OP.RDMA_READ_Request,
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Only,
                BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate
            ], [
                current_mem_loc.va.eq(params.va),
                current_mem_loc.mem_key.eq(params.r_key)
            ]),
            ([
                BTH_OPCODE_OP.SEND_First,
                BTH_OPCODE_OP.SEND_Only,
                BTH_OPCODE_OP.SEND_Only_with_Immediate
            ],
            [
                current_mem_loc.va.eq(qps[1].receive_queue.source.va),
                current_mem_loc.mem_key.eq(qps[1].receive_queue.source.l_key)
            ])
        ], default=saved_mem_loc.connect(current_mem_loc, keep={"va", "mem_key"}))

        ## Validation logic
        # NAK validations
        check_psn         = Signal()
        valid_psn         = Signal()
        duplicate         = Signal()
        check_opcode_seq  = Signal()
        valid_opcode_seq  = Signal()
        valid_opcode      = Signal()
        check_r_key_write = Signal()
        check_r_key_read  = Signal()
        valid_r_key       = Signal()

        # NAK validations
        self.comb += [
            check_psn.eq(conn_type == QP_CONN_TYPE.RC),
            valid_psn.eq(~check_psn | (params.psn == expected_psn)),
            duplicate.eq(~((expected_psn - params.psn) >> 23) & (expected_psn != params.psn)),
            check_opcode_seq.eq(conn_type == QP_CONN_TYPE.RC),
            qps[1].op_seq_check_resp.opcode.eq(opcode_op),
            valid_opcode_seq.eq(~check_opcode_seq | ~qps[1].op_seq_check_resp.invalid_sequence),
            Case(conn_type, {
                QP_CONN_TYPE.RC: is_in_flag(opcode_op, IBT_RC_OPS, valid_opcode),
                QP_CONN_TYPE.UD:
                    If(params.dest_qp != 1,
                        is_in_flag(opcode_op, IBT_UD_OPS, valid_opcode)
                    ).Else(
                        valid_opcode.eq(opcode_op == BTH_OPCODE_OP.SEND_Only)
                    ),
                "default": valid_opcode.eq(0)
            }),
            If(params.dma_len != 0,
                is_in_do(opcode_op, [
                    ([
                        BTH_OPCODE_OP.RDMA_WRITE_First,
                        BTH_OPCODE_OP.RDMA_WRITE_Only,
                        BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate
                    ], check_r_key_write.eq(1))
                ])
            ), # o9-55
            check_r_key_read.eq((opcode_op == BTH_OPCODE_OP.RDMA_READ_Request)
            & (params.dma_len != 0)), # o9-55
            valid_r_key.eq(
                (~check_r_key_write |
                    is_in(params.r_key, [
                        Constant(mr.r_key, 32) for mr in mrs
                        if MR_PERM.REMOTE_WRITE in mr.permissions
                    ])
                ) &
                (~check_r_key_read  |
                    is_in(params.r_key, [
                        Constant(mr.r_key, 32) for mr in mrs
                        if MR_PERM.REMOTE_READ in mr.permissions
                    ])
                )
            )
        ]

        # MR boundary checks
        check_memory = Signal()
        valid_memory = Signal()

        memory_under = Signal()
        memory_over  = Signal()

        check_r_key_write_bounds = Signal()
        check_r_key_read_bounds  = Signal()
        check_l_key_bounds       = Signal()
        self.comb += [
            check_memory.eq(sink.payload_length != 0),
            valid_memory.eq(~check_memory | (~memory_under & ~memory_over)),
            is_in_do(opcode_op, [
                ([
                    BTH_OPCODE_OP.RDMA_WRITE_First,
                    BTH_OPCODE_OP.RDMA_WRITE_Middle,
                    BTH_OPCODE_OP.RDMA_WRITE_Last,
                    BTH_OPCODE_OP.RDMA_WRITE_Last_with_Immediate,
                    BTH_OPCODE_OP.RDMA_WRITE_Only,
                    BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate,
                ], check_r_key_write_bounds.eq(1)),
                ([
                    BTH_OPCODE_OP.RDMA_READ_Request
                ], check_r_key_read_bounds.eq(1)),
                ([
                    BTH_OPCODE_OP.SEND_First,
                    BTH_OPCODE_OP.SEND_Middle,
                    BTH_OPCODE_OP.SEND_Last,
                    BTH_OPCODE_OP.SEND_Last_with_Immediate,
                    BTH_OPCODE_OP.SEND_Only,
                    BTH_OPCODE_OP.SEND_Only_with_Immediate
                ], check_l_key_bounds.eq(1))
            ])
        ]

        self.sync += [
            If(check_r_key_write_bounds,
                Case(current_mem_loc.mem_key, {
                    mr.r_key: [
                        memory_under.eq(current_mem_loc.va < mr.region_start),
                        # We set greater or equal, because this is done synchronously on RECEIVE
                        # And so the payload_length is the number of bytes written so far
                        # Thus, one cycle before validate, payload_length will be one less than the actual size
                        memory_over.eq(current_mem_loc.va + sink.payload_length >= mr.region_start + mr.region_size)
                    ]
                    for mr in mrs if MR_PERM.REMOTE_WRITE in mr.permissions
                })
            ).Elif(check_r_key_read_bounds,
                Case(current_mem_loc.mem_key, {
                    mr.r_key: [
                        memory_under.eq(current_mem_loc.va < mr.region_start),
                        memory_over.eq(sink.va + sink.dma_len > mr.region_start + mr.region_size)
                    ]
                    for mr in mrs if MR_PERM.REMOTE_READ in mr.permissions
                })
            ).Elif(check_l_key_bounds,
                Case(current_mem_loc.mem_key, {
                    mr.l_key: [
                        memory_under.eq(current_mem_loc.va < mr.region_start),
                        # We set greater or equal, because this is done synchronously on RECEIVE
                        # And so the payload_length is the number of bytes written so far
                        # Thus, one cycle before validate, payload_length will be one less than the actual size
                        memory_over.eq(current_mem_loc.va + sink.payload_length >= mr.region_start + mr.region_size)
                    ]
                    for mr in mrs if MR_PERM.LOCAL_WRITE in mr.permissions
                })
            )
        ]

        rnr = Signal()
        self.comb += rnr.eq(
            ((
                (~read_pipe.sink.ready) &
                (opcode_op == BTH_OPCODE_OP.RDMA_READ_Request)
            ) |
            (
                wait_pipe.full & ~params.header_only & ~duplicate
            ))
        )

        self.fsm = fsm = FSM()
        fsm.act("RECEIVE",
            If(~sink.header_only,
                sink.connect(wait_pipe.sink, keep={"valid", "ready", "last", "data"})
            ).Else(
                sink.ready.eq(1)
            ),
            If(sink.valid & sink.ready & sink.last,
                NextState("VALIDATE")
            )
        )

        valid_fields = Signal()
        fsm.act("VALIDATE",
            NextState("RECEIVE"),
            wait_pipe.validate_sink.invalidate.eq(~params.header_only & ~wait_pipe.validate_sink.validate),
            If(sink.valid,
                If(sink.validate,
                    current_mem_loc.connect(wait_pipe.sink, keep={"va", "mem_key"}),
                    wait_pipe.sink.conn_type.eq(conn_type),
                    wait_pipe.sink.dest_qp.eq(params.dest_qp),
                    is_in_flag(opcode_op, [
                        BTH_OPCODE_OP.SEND_First,
                        BTH_OPCODE_OP.SEND_Middle,
                        BTH_OPCODE_OP.SEND_Last,
                        BTH_OPCODE_OP.SEND_Last_with_Immediate,
                        BTH_OPCODE_OP.SEND_Only,
                        BTH_OPCODE_OP.SEND_Only_with_Immediate
                    ], wait_pipe.sink.send),
                    # Validation
                    # Invalid or duplicate psn
                    If(~valid_psn,
                        If(~duplicate,
                            NAK.emit(NAK.Code.PSN_Sequence_Error, params.dest_qp, msn, params.psn, ack_source, duplicate, qps)
                        ).Else(
                            valid_fields.eq(1)
                        )
                    ).Elif(~valid_opcode_seq | ~valid_opcode,
                        NAK.emit(NAK.Code.Invalid_Request, params.dest_qp, msn, params.psn, ack_source, duplicate, qps)
                    ).Elif(~valid_r_key | ((check_r_key_read | check_r_key_write) & ~valid_memory),
                        NAK.emit(NAK.Code.Remote_Access_Error, params.dest_qp, msn, params.psn, ack_source, duplicate, qps)
                    ).Elif(sink.invalid_packet,
                        NAK.emit(NAK.Code.Invalid_Request, params.dest_qp, msn, params.psn, ack_source, duplicate, qps)
                    ).Else(
                        # Valid
                        valid_fields.eq(1),
                    )
                )
            # Else should never happen
            ),
            If(valid_fields,
                If(opcode_op == BTH_OPCODE_OP.RDMA_READ_Request,
                    params.connect(read_source, omit={"valid", "ready", "data", "header_only", "ip_address"}),
                    read_source.valid.eq(read_pipe.sink.ready),
                    expected_psn_next.eq(expected_psn + psn_jump)
                ).Else(
                    expected_psn_next.eq(expected_psn + 1),
                ),
                is_in_do(opcode_op, ([
                    BTH_OPCODE_OP.SEND_First,
                    BTH_OPCODE_OP.SEND_Middle,
                    BTH_OPCODE_OP.RDMA_WRITE_First,
                    BTH_OPCODE_OP.RDMA_WRITE_Middle
                ], [
                    msn_next.eq(msn),
                    va_next.eq(current_mem_loc.va + PMTU),
                ]), default=[
                    msn_next.eq(msn + 1)
                ]),

                # QP update
                Case(params.dest_qp, {qp.id : [
                    NextValue(qp.nak_sent, 0),
                ] for qp in qps[1:]}),
                If((conn_type == QP_CONN_TYPE.RC) & (opcode_op != BTH_OPCODE_OP.RDMA_READ_Request) & (~rnr), # Read requests are ACKed with a read response
                    ACK.emit(params.dest_qp, msn_next, params.psn, ack_source)
                ),
                If(~duplicate,
                    If(~rnr,
                        qp_rcv_wire_next.eq(1),
                        wait_pipe.validate_sink.validate.eq(~params.header_only),

                        # Completion queue
                        If(opcode_conn_type == QP_CONN_TYPE.RC,
                            is_in_flag(opcode_op, [
                                BTH_OPCODE_OP.SEND_Last,
                                BTH_OPCODE_OP.SEND_Last_with_Immediate,
                                BTH_OPCODE_OP.SEND_Only,
                                BTH_OPCODE_OP.SEND_Only_with_Immediate
                            ], pre_cq.sink.valid),
                            # Consume work request
                            qps[1].receive_queue.source.ready.eq(1)
                        ),
                        pre_cq.sink.status.eq(WC.Status.SUCCESS),
                        is_in_do(opcode_op, [
                        ([
                            BTH_OPCODE_OP.SEND_Last,
                            BTH_OPCODE_OP.SEND_Only
                        ], [
                            pre_cq.sink.opcode.eq(WC.Opcode.RECV),
                            pre_cq.sink.w_immdt.eq(0)
                        ]), ([
                            BTH_OPCODE_OP.SEND_Last_with_Immediate,
                            BTH_OPCODE_OP.SEND_Only_with_Immediate
                        ], [
                            pre_cq.sink.opcode.eq(WC.Opcode.RECV_RDMA_WITH_IMM),
                            pre_cq.sink.w_immdt.eq(1)
                        ])]),
                        pre_cq.sink.immdt.eq(params.immdt),
                        pre_cq.sink.dma_len.eq(sink.payload_length),
                        pre_cq.sink.qp_num.eq(sink.dest_qp),
                        Case(params.dest_qp, {
                            qp.id: pre_cq.sink.src_qp.eq(qp.other_id)
                        for qp in qps}),
                        wait_pipe.sink.signal_cq.eq(pre_cq.sink.valid),
                        Case(params.dest_qp, {
                            qp.id : [
                                NextValue(qp.receive_queue.psn, expected_psn_next),
                                NextValue(qp.msn, msn_next),
                            ] + ([
                                qp.op_seq_check_resp.update.eq(1),
                                NextValue(qp.receive_queue.rdma_state.va, va_next),
                                If(is_in(opcode_op, [BTH_OPCODE_OP.RDMA_WRITE_First, BTH_OPCODE_OP.SEND_First]),
                                    NextValue(qp.receive_queue.rdma_state.mem_key, current_mem_loc.mem_key),
                                )
                            ] if qp.id.value != 1 else [
                                # NOTE: Requests from different ip addresses must not come in quick succession
                                # FIXME: Potential improvement: pass ip through pipeline to mad_tx
                                NextValue(qp.ip_address, params.ip_address)
                            ])
                        for qp in qps}),
                    ).Elif(conn_type == QP_CONN_TYPE.RC,
                        RNR_NAK.emit(0b00001, sink.dest_qp, msn, sink.psn, ack_source, qps)
                    )
                )
            )
        )

        ## Memory write
        self.comb += [
            Case(wait_pipe.source.conn_type, {
            QP_CONN_TYPE.RC:
                # Only connect when actually needed
                If(wait_pipe.source.valid,
                    If(wait_pipe.source.send,
                        Case(wait_pipe.source.mem_key, {
                            Constant(mr.l_key, 32): [
                                wait_pipe.source.connect(mr.writer.sink, keep={"valid", "ready", "last", "data"}),
                                mr.writer.sink.va.eq(wait_pipe.source.va)
                            ]
                            for mr in mrs if MR_PERM.LOCAL_WRITE in mr.permissions
                        })
                    ).Else(
                        Case(wait_pipe.source.mem_key, {
                            Constant(mr.r_key, 32): [
                                wait_pipe.source.connect(mr.writer.sink, keep={"valid", "ready", "last", "data"}),
                                mr.writer.sink.va.eq(wait_pipe.source.va)]
                            for mr in mrs if MR_PERM.REMOTE_WRITE in mr.permissions
                        })
                    )
                ),
            QP_CONN_TYPE.UD:
                If(wait_pipe.source.dest_qp == 1,
                    wait_pipe.source.connect(mad_rx.sink, keep={"valid", "ready", "last", "data"})
                ).Else(
                    wait_pipe.source.ready.eq(1)
                )
            })
        ]

        self.comb += [
            If(pre_cq.source.valid &
                (
                   (pre_cq.source.dma_len == 0) |
                   (wait_pipe.source.valid & wait_pipe.source.last &
                    wait_pipe.source.signal_cq)
                ),
                pre_cq.source.connect(cq.sink)
            )
        ]
