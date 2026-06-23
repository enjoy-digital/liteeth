from liteeth.common import *

from liteeth.core.rocev2.mr import PERM as MR_PERM
from liteeth.core.rocev2.common import WaitPipe, VarWaitTimer
from liteeth.core.rocev2.ack import NAK
from liteeth.core.rocev2.qp import LiteEthIBQP

from litex.soc.interconnect.stream import Endpoint, Buffer, SyncFIFO


class LiteEthRDMARequesterTX(LiteXModule):
    def __init__(self, qp, mrs, dw=8):
        self.sink  = sink  = Endpoint(add_params(eth_rocev2_send_wr_description(), [("psn", 24)]))
        self.source = source = Endpoint(add_params(eth_rocev2_description(dw), [("header_only", 1)]))

        self.timer_start = timer_start = Signal()

        # # #

        # Indicates if we are resending an old unacknowledged packet
        resending       = Signal()
        resending_set   = Signal()
        resending_unset = Signal()
        resending_mem   = Signal()

        self.comb += [
            If(resending_set,
                resending.eq(1)
            ).Elif(resending_unset,
                resending.eq(0)
            ).Else(
                resending.eq(resending_mem)
            )
        ]
        self.sync += [
            If(resending_set,
                resending_mem.eq(1)
            ).Elif(resending_unset,
                resending_mem.eq(0)
            )
        ]

        # Picks requests from send_queue or resend_handler
        request_source = Endpoint(eth_rocev2_send_wr_description())

        self.comb += [
            If(resending,
                sink.connect(request_source, omit={"psn"})
            ).Else(
                qp.send_queue.source.connect(request_source)
            )
        ]

        mem_reader_sink   = Endpoint([("va", 64), ("len", bits_for(PMTU))])
        mem_reader_source = Endpoint([("data", dw)])

        self.comb += [
            If(mem_reader_sink.valid | mem_reader_source.ready,
                Case(request_source.l_key, {
                    Constant(mr.l_key, 32): [
                        mem_reader_sink.connect(mr.reader.sink),
                        mr.reader.source.connect(mem_reader_source)]
                    for mr in mrs if not MR_PERM.NO_LOCAL_READ in mr.permissions
                })
            )
        ]

        # WaitPipe to fetch entire packet from RAM before send
        # (MAC cannot be stopped after packet start and RAM reads in bursts)
        send_wait_pipe = WaitPipe(eth_rocev2_description(dw), 8, PMTU, discarding=False, dw=dw)
        self.submodules.send_wait_pipe = send_wait_pipe

        p_key    = Signal(16, reset_less=True)
        other_id = Signal(24, reset_less=True)

        psn     = Signal(24, reset_less=True)
        va_src  = Signal(64, reset_less=True)

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


        self.comb += [
            If(request_source.wr_opcode != WR_OPCODE.RDMA_READ,
                remainder_bytes.eq(request_source.dma_len[:PMTU_BITS]),
                whole_blocks.eq(request_source.dma_len[PMTU_BITS:])
            )
        ]
        self.comb += more_left.eq(
            ~((whole_blocks_left == 0) |
              ((whole_blocks_left == 1) & (remainder_bytes == 0)))
        )

        invalid_request = Signal()
        # Requests in error state are invalid
        # Ignore a read request for which the local write location does not allow writing
        invalid_request.eq(
            (qp.qp_state == LiteEthIBQP.ERROR) |
            (qp.send_queue.source.dma_len != 0) &
            (qp.send_queue.source.wr_opcode == WR_OPCODE.RDMA_READ) &
            ~is_in(qp.send_queue.source.l_key, [
                Constant(mr.l_key, 32) for mr in mrs
                if MR_PERM.LOCAL_WRITE in mr.permissions
            ]))

        # Main transmission combinatory logic
        self.comb += [
            send_wait_pipe.sink.tver.eq(0),
            send_wait_pipe.sink.m.eq(0),
            send_wait_pipe.sink.se.eq(0),
            send_wait_pipe.sink.a.eq(request_source.ack_req),

            If(request_source.wr_opcode == WR_OPCODE.RDMA_READ,
                send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_READ_Request)
            ).Else(
                If(more_left,
                    If(first_packet,
                        Case(request_source.wr_opcode, {
                            WR_OPCODE.RDMA_WRITE: send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_WRITE_First),
                            WR_OPCODE.SEND:       send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.SEND_First),
                        })
                    ).Else(
                        Case(request_source.wr_opcode, {
                            WR_OPCODE.RDMA_WRITE: send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_WRITE_Middle),
                            WR_OPCODE.SEND:       send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.SEND_Middle),
                        })
                    )
                ).Else(
                    If(~request_source.w_immdt,
                        If(first_packet,
                            Case(request_source.wr_opcode, {
                                WR_OPCODE.RDMA_WRITE: send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_WRITE_Only),
                                WR_OPCODE.SEND:       send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.SEND_Only),
                            })
                        ).Else(
                            Case(request_source.wr_opcode, {
                                WR_OPCODE.RDMA_WRITE: send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_WRITE_Last),
                                WR_OPCODE.SEND:       send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.SEND_Last),
                            })
                        )
                    ).Else(
                        If(first_packet,
                            Case(request_source.wr_opcode, {
                                WR_OPCODE.RDMA_WRITE: send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_WRITE_Only_with_Immediate),
                                WR_OPCODE.SEND:       send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.SEND_Only_with_Immediate),
                            })
                        ).Else(
                            Case(request_source.wr_opcode, {
                                WR_OPCODE.RDMA_WRITE: send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.RDMA_WRITE_Last_with_Immediate),
                                WR_OPCODE.SEND:       send_wait_pipe.sink.opcode.eq(BTH_OPCODE.RC.SEND_Last_with_Immediate),
                            })
                        )
                    )
                )
            ),
            If(whole_blocks_left != 0,
                send_wait_pipe.sink.pad.eq(0),
                payload_length.eq(PMTU)
            ).Else(
                send_wait_pipe.sink.pad.eq((0b100 - (remainder_bytes & 0b11))[:2]),
                payload_length.eq(remainder_bytes)
            ),
            mem_reader_sink.len.eq(payload_length),

            IBT_header_length(send_wait_pipe.sink.opcode, header_length),
            send_wait_pipe.sink.length.eq(header_length + payload_length),

            send_wait_pipe.sink.psn.eq(psn),

            send_wait_pipe.sink.dma_len.eq(request_source.dma_len),

            send_wait_pipe.sink.p_key.eq(p_key),
            send_wait_pipe.sink.dest_qp.eq(other_id),

            send_wait_pipe.sink.r_key.eq(request_source.r_key),
            send_wait_pipe.sink.va.eq(request_source.va),

            send_wait_pipe.sink.immdt.eq(request_source.immdt),

            send_wait_pipe.source.connect(source),

            mem_reader_sink.va.eq(va_src),
        ]

        send_resend = Signal()
        send_new_req = Signal()

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            NextValue(p_key, qp.p_key),
            NextValue(other_id, qp.other_id),

            NextValue(va_src, 0),
            NextValue(whole_blocks_left, whole_blocks),
            NextValue(first_packet, 1),

            If(qp.qp_state == LiteEthIBQP.ERROR,
                qp.send_queue.source.ready.eq(1),
                sink.ready.eq(1)
            ).Else(
                # Ignore invalid requests from our send queue
                qp.send_queue.source.ready.eq(invalid_request),
                If(~send_wait_pipe.full,
                    If(sink.valid,
                        send_resend.eq(1)
                    ).Elif(qp.send_queue.source.valid & ~invalid_request &
                        qp.outstanding_requests_chooser.sink.ready,
                        send_new_req.eq(1)
                    )
                ),
                If(send_resend,
                    If((sink.dma_len != 0) & (sink.wr_opcode != WR_OPCODE.RDMA_READ),
                        NextValue(mem_reader_sink.valid, 1),
                        NextState("READ_RAM")
                    ).Else(
                        NextState("EMPTY")
                    ),
                    resending_set.eq(1),

                    NextValue(psn, sink.psn)
                ),
                If(send_new_req,
                    If((qp.send_queue.source.dma_len != 0) & (qp.send_queue.source.wr_opcode != WR_OPCODE.RDMA_READ),
                        NextValue(mem_reader_sink.valid, 1),
                        NextState("READ_RAM")
                    ).Else(
                        NextState("EMPTY")
                    ),
                    resending_unset.eq(1),

                    NextValue(psn, qp.send_queue.psn),

                    # Transfer wr from send_queue to outstanding
                    qp.outstanding_requests_chooser.sink.valid.eq(1),
                    If(qp.send_queue.source.wr_opcode != WR_OPCODE.RDMA_READ,
                        qp.outstanding_requests_chooser.sink.choose.eq(1),
                        qp.send_queue.source.connect(qp.outstanding_requests.sink, omit={"ready", "psn"}),
                        qp.outstanding_requests.sink.psn.eq(qp.send_queue.psn)
                    ).Else(
                        qp.outstanding_requests_chooser.sink.choose.eq(0),
                        qp.send_queue.source.connect(qp.outstanding_read_requests.sink, omit={"ready", "psn"}),
                        qp.outstanding_read_requests.sink.psn.eq(qp.send_queue.psn)
                    ),

                    If(qp.send_queue.source.ack_req | (qp.send_queue.source.wr_opcode == WR_OPCODE.RDMA_READ),
                        timer_start.eq(1)
                    )
                )
            )
        )

        fsm.act("READ_RAM",
            If(qp.qp_state == LiteEthIBQP.ERROR,
                send_wait_pipe.validate_sink.invalidate.eq(1),
                NextValue(mem_reader_sink.valid, 0),
                NextState("ERROR_DUMP")
            ).Else(
                If(mem_reader_sink.ready == 1, # Once the reader has consumed the request, we end it
                    NextValue(mem_reader_sink.valid, 0)
                ),
                mem_reader_source.connect(send_wait_pipe.sink, keep={"valid", "ready", "data", "last"}),

                If(mem_reader_source.valid & mem_reader_source.last & send_wait_pipe.sink.ready,
                    send_wait_pipe.validate_sink.validate.eq(1),
                    NextValue(first_packet, 0),
                    If(~resending,
                        NextValue(qp.send_queue.psn, psn + 1)
                    ),
                    If(more_left,
                        NextValue(mem_reader_sink.valid, 1),
                        NextValue(whole_blocks_left, whole_blocks_left - 1),
                        NextValue(va_src, va_src + PMTU),
                        NextValue(psn, psn + 1),
                    ).Else(
                        NextValue(mem_reader_sink.valid, 0),
                        # Consume request
                        If(~resending,
                            qp.send_queue.source.ready.eq(1)
                        ).Else(
                            sink.ready.eq(1)
                        ),
                        NextState("IDLE")
                    )
                )
            )
        )

        fsm.act("EMPTY",
            If(qp.qp_state == LiteEthIBQP.ERROR,
                NextState("IDLE")
            ).Else(
                send_wait_pipe.sink.valid.eq(1),
                send_wait_pipe.sink.last.eq(1),
                send_wait_pipe.sink.header_only.eq(1),
                If(send_wait_pipe.sink.ready,
                    send_wait_pipe.validate_sink.validate.eq(1),
                    If(~resending,
                        If(request_source.wr_opcode == WR_OPCODE.RDMA_READ,
                            NextValue(qp.send_queue.psn, psn + request_source.dma_len[PMTU_BITS:] + (request_source.dma_len[:PMTU_BITS] != 0))
                        ).Else(
                            NextValue(qp.send_queue.psn, psn + 1)
                        ),
                        qp.send_queue.source.ready.eq(1)
                    ).Else(
                        sink.ready.eq(1)
                    ),
                    NextState("IDLE")
                )
            )
        )

        fsm.act("ERROR_DUMP",
            mem_reader_source.ready.eq(1),
            If(~mem_reader_source.valid | mem_reader_source.last,
                NextState("IDLE")
            )
        )

# PSN helper functions
def psn_ge(psn_a, psn_b):
    return (psn_a - psn_b)[-1:] == 0

def psn_gt(psn_a, psn_b):
    return psn_ge(psn_a, psn_b) & (psn_a != psn_b)

class LiteEthRDMARequesterValidation(LiteXModule):
    def __init__(self, read_wait_pipe, qps, mrs, dw=8):
        self.sink = sink = Endpoint(add_params(eth_rocev2_description(dw), [("header_only", 1), ("payload_length", PMTU_BITS + 1), ("validate", 1)]))
        self.oldest_unacked_psn = oldest_unacked_psn = Signal(24)

        self.source = source = Endpoint([("opcode_op", 5), ("psn", 24), ("syndrome", 8), ("dest_qp", 24)])

        # # #

        ## Params to save sink parameters
        params = Endpoint(add_params(eth_rocev2_description(dw=dw), [("header_only", 1)]))
        self.sync += sink.connect(params, omit={"valid", "ready", "last", "data", "payload_length", "validate"})

        ### Opcode splitting
        # Connection type and operation extracted from opcode
        opcode_conn_type = Signal(3)
        opcode_op        = Signal(5)
        # Opcode decomposition (must be always correct, not only for validation)
        self.comb += Cat(opcode_op, opcode_conn_type).eq(params.opcode)

        ## Reception logic
        current_mem_loc = Record([
            ("va_r",  64, DIR_M_TO_S),
            ("va_l",  64, DIR_M_TO_S),
            ("l_key", 32, DIR_M_TO_S)
        ], reset_less=True)

        # Extract qp parameters
        init_dict = {
            qp.id : [
                qp.send_queue.rdma_state.connect(current_mem_loc)
            ] for qp in qps if qp.id.value != 1
        }
        init_dict["default"] = [
            current_mem_loc.va_r.eq(0),
            current_mem_loc.va_l.eq(0),
            current_mem_loc.l_key.eq(0)
        ]
        self.sync += [
            If(sink.valid,
                Case(sink.dest_qp, init_dict)
            )
        ]

        ## Validation logic
        valid_opcode_seq = Signal()
        check_syndrome   = Signal()
        valid_syndrome   = Signal()

        self.comb += [
            qps[1].op_seq_check_req.opcode.eq(opcode_op),
            valid_opcode_seq.eq(~qps[1].op_seq_check_req.invalid_sequence),
            check_syndrome.eq(opcode_op == BTH_OPCODE_OP.Acknowledge),
            valid_syndrome.eq(~check_syndrome |
                (
                    params.syndrome[7] == 0 &
                    (
                        ((params.syndrome[6:7] == 0b00) & (params.syndrome[0:6] != 0b11111)) | # ACK credits
                        (params.syndrome[6:7] == 0b01)                                      | # RNR NAKs
                        ((params.syndrome[6:7] == 0b11) & (params.syndrome[0:6] <= 0b100))     # NAK codes
                    )
                )
            )
        ]

        # MR boundary checks
        check_memory = Signal()
        valid_memory = Signal()
        read_response = Signal()

        memory_under = Signal()
        memory_over  = Signal()

        self.comb += [
            check_memory.eq(sink.payload_length != 0),
            valid_memory.eq(~check_memory | (~memory_under & ~memory_over)),
            is_in_flag(opcode_op, [
                BTH_OPCODE_OP.RDMA_READ_response_First,
                BTH_OPCODE_OP.RDMA_READ_response_Middle,
                BTH_OPCODE_OP.RDMA_READ_response_Last,
                BTH_OPCODE_OP.RDMA_READ_response_Only,
            ], read_response)
        ]
        self.sync += [
            If(read_response,
                Case(current_mem_loc.l_key, {
                    Constant(mr.l_key, 32): [
                        memory_under.eq(current_mem_loc.va_l < mr.region_start),
                        # We set greater or equal, because this is done synchronously on RECEIVE
                        # And so the payload_length is the number of bytes written so far
                        # Thus, one cycle before validate, payload_length will be one less than the actual size
                        memory_over.eq(current_mem_loc.va_l + sink.payload_length >= mr.region_start + mr.region_size)
                    ]
                    for mr in mrs if MR_PERM.LOCAL_WRITE in mr.permissions
                })
            )
        ]

        # Duplicate and readiness
        duplicate = Signal()
        rnr = Signal()
        self.comb += [
            duplicate.eq(~((oldest_unacked_psn - params.psn) >> 23) & (oldest_unacked_psn != params.psn)),
            rnr.eq(
                ~duplicate &
                read_wait_pipe.full &
                (opcode_op != BTH_OPCODE_OP.Acknowledge)
            )
        ]

        # Reception FSM
        self.fsm = fsm = FSM()
        fsm.act("RECEIVE",
            # We only buffer non-empty read responses through wait_pipe, to later commit them to memory
            If(~sink.header_only,
                sink.connect(read_wait_pipe.sink, keep={"valid", "ready", "last", "data"})
            ).Else(
                sink.ready.eq(1)
            ),
            If(sink.valid & sink.ready & sink.last,
                NextState("VALIDATE")
            )
        )

        # QP context checks
        fsm.act("VALIDATE",
            NextState("RECEIVE"),
            If(sink.valid,
                read_wait_pipe.sink.l_key.eq(current_mem_loc.l_key),
                read_wait_pipe.sink.va.eq(current_mem_loc.va_l),
                read_wait_pipe.sink.signal_cq.eq(
                    (opcode_op == BTH_OPCODE_OP.RDMA_READ_response_Last) |
                    (opcode_op == BTH_OPCODE_OP.RDMA_READ_response_Only)
                ),
                # We validate the packet even if the psn is incorrect (we will send another read request for that location if that is the case)
                # In fact, we cannot stall the sink for too long as if packets are send one after another, depacketizer only waits while it
                # receives the packet header
                If(sink.validate & valid_opcode_seq & valid_syndrome & valid_memory &
                ~psn_gt(oldest_unacked_psn, params.psn) | ~rnr, # Not duplicate and receiver ready
                    # If we accept packet, we send it to the fsm
                    source.psn.eq(params.psn),
                    source.syndrome.eq(params.syndrome),
                    source.opcode_op.eq(opcode_op),
                    source.dest_qp.eq(params.dest_qp),
                    source.valid.eq(1),
                    read_wait_pipe.validate_sink.validate.eq(~params.header_only),
                ).Else(
                    read_wait_pipe.validate_sink.invalidate.eq(~params.header_only)
                )
            )
            # Else should never happen
        )

class LiteEthRDMARequesterContextChecker(LiteXModule):
    class ToggleTimer(VarWaitTimer):
        def __init__(self, clk_freq):
            super().__init__(clk_freq)

            self.start   = start   = Signal()
            self.stop    = stop    = Signal()
            self.reset   = reset   = Signal()

            # # #

            running = Signal()

            self.comb += self.pow.eq(0x11)
            self.comb += self.wait.eq(running & ~reset & ~self.done)

            self.sync += [
                If(start,
                    running.eq(1)
                ).Elif(stop,
                    running.eq(0)
                )
            ]

    def __init__(self, qps, pre_cq, clk_freq):
        self.responses_sink = responses_sink = Endpoint([("opcode_op", 5), ("psn", 24), ("syndrome", 8), ("dest_qp", 24)])
        self.timer_start = Signal()

        self.resend_source = resend_source = Endpoint(add_params(eth_rocev2_send_wr_description(), [("psn", 24)]))
        self.oldest_unacked_psn = oldest_unacked_psn = Signal(24)

        # # #

        # Params
        outstanding_requests = qps[1].outstanding_requests
        outstanding_read_requests = qps[1].outstanding_read_requests
        outstanding_requests_chooser = qps[1].outstanding_requests_chooser

        ## Datapath
        # responses_sink -> SyncFIFO
        # Responses to be treated from the receiver
        responses_fifo = SyncFIFO([("opcode_op", 5), ("psn", 24), ("syndrome", 8), ("dest_qp", 24)], 16, buffered=True)
        self.submodules.responses_fifo = responses_fifo
        self.comb += responses_sink.connect(responses_fifo.sink)

        # Buffer -> resend_source
        # Pending resend
        pending_resend = Buffer(add_params(eth_rocev2_send_wr_description(), [("psn", 24)]))
        self.submodules.pending_resend = pending_resend

        ## Logic
        # Timer logic
        outstanding_timer = self.ToggleTimer(clk_freq)
        self.submodules.outstanding_timer = outstanding_timer

        self.comb += [
            outstanding_timer.start.eq(self.timer_start),
            outstanding_timer.stop.eq(~outstanding_requests_chooser.source.valid),
            outstanding_timer.reset.eq(responses_fifo.source.valid & responses_fifo.source.ready & outstanding_requests_chooser.source.valid)
        ]

        # Syndrome decode
        ack_type  = Signal(2)
        ack_value = Signal(5)

        self.comb += [
            ack_value.eq(responses_fifo.source.syndrome[:5]),
            ack_type.eq(responses_fifo.source.syndrome[5:7])
        ]

        # Resend logic
        # Retry counters, when equal to 7, indicate infinite retries
        retry_cnt     = Signal(3)
        retry_cnt_rnr = Signal(3)

        resend_ask = Signal()

        # Response logic

        # Number of expected packets for top outstanding request
        wr_packets = Signal(16, reset_less=True)
        self.comb += [
            If(outstanding_requests_chooser.source.choose,
                wr_packets.eq(outstanding_requests.source.dma_len[PMTU_BITS:] + (outstanding_requests.source.dma_len[:PMTU_BITS] != 0))
            ).Else(
                wr_packets.eq(outstanding_read_requests.source.dma_len[PMTU_BITS:] + (outstanding_read_requests.source.dma_len[:PMTU_BITS] != 0))
            )
        ]

        ## Resend logic
        last_expected_psn  = Signal(24, reset_less=True) # From the oldest unacknowledged request

        resend_starting_pos     = Signal(64, reset_less=True)
        resend_remaining_length = Signal(32, reset_less=True)

        # FSM
        self.fsm = fsm = FSM()
        # Fetch next outstanding work request
        fsm.act("NEXT_WR",
            If(qps[1].qp_state == LiteEthIBQP.ERROR,
                NextState("ERROR")
            ).Elif(outstanding_requests_chooser.source.valid,
                If(outstanding_requests_chooser.source.choose,
                    NextValue(oldest_unacked_psn, outstanding_requests.source.psn),
                    NextValue(last_expected_psn, outstanding_requests.source.psn + wr_packets - 1),

                    NextValue(resend_starting_pos, outstanding_requests.source.va),
                    NextValue(resend_remaining_length, outstanding_requests.source.dma_len)
                ).Else(
                    NextValue(oldest_unacked_psn, outstanding_read_requests.source.psn),
                    NextValue(last_expected_psn, outstanding_read_requests.source.psn + wr_packets - 1),

                    NextValue(resend_starting_pos, outstanding_read_requests.source.va),
                    NextValue(resend_remaining_length, outstanding_read_requests.source.dma_len)
                ),

                # Reload retry counters
                NextValue(retry_cnt, qps[1].retry_cnt_rst),
                NextValue(retry_cnt_rnr, qps[1].retry_cnt_rnr_rst),

                # QP state - For reception location
                NextValue(qps[1].send_queue.rdma_state.va_r, outstanding_read_requests.source.va),
                NextValue(qps[1].send_queue.rdma_state.va_l, 0),
                NextValue(qps[1].send_queue.rdma_state.l_key, outstanding_read_requests.source.l_key),

                # We receiving a read response is different from receiving an ack to send or write
                If(outstanding_requests_chooser.source.choose,
                    NextState("WAIT_ACK")
                ).Else(
                    NextState("WAIT_READ")
                )
            ).Else(
                responses_fifo.source.ready.eq(1)
            )
        )

        # Set pre_cq parameters
        self.comb += [
            If(fsm.ongoing("ERROR"),
                pre_cq.sink.opcode.eq(WC.Opcode.FLUSH)
            ).Else(
                If(outstanding_requests_chooser.source.choose,
                    Case(outstanding_requests.source.wr_opcode, {
                        WR_OPCODE.SEND:
                            pre_cq.sink.opcode.eq(WC.Opcode.SEND),
                        WR_OPCODE.RDMA_WRITE:
                            pre_cq.sink.opcode.eq(WC.Opcode.RDMA_WRITE)
                    })
                ).Else(
                    # It has to be a read
                    pre_cq.sink.opcode.eq(WC.Opcode.RDMA_READ)
                )

            ),
            If(outstanding_requests_chooser.source.choose,
                outstanding_requests.source.connect(pre_cq.sink, keep={"w_immdt", "immdt", "dma_len"})
            ).Else(
                outstanding_read_requests.source.connect(pre_cq.sink, keep={"w_immdt", "immdt", "dma_len"}),
            ),
            pre_cq.sink.qp_num.eq(responses_fifo.source.dest_qp),
            Case(responses_fifo.source.dest_qp, {qp.id : [
                pre_cq.sink.src_qp.eq(qp.other_id)
            ] for qp in qps[1:]})
        ]

        # Shorthand for decrementing retry_cnt or rnr_retry_cnt and reporting an error
        def decrease_retry_cnt(rnr=False, resend=True, check_diff_pack=False, error_status=WC.Status.GENERAL_ERR):
            rst = qps[1].retry_cnt_rnr_rst if rnr else qps[1].retry_cnt_rst
            cnt = retry_cnt_rnr if rnr else retry_cnt

            # Ask the tx module to resend the last outstanding packet
            resend = resend_ask.eq(1) if resend else []

            decrement_cnt = (
                # Check if NAK concerns same packet
                If(oldest_unacked_psn == responses_fifo.source.psn,
                    NextValue(cnt, cnt - 1)
                ).Else(
                    NextValue(cnt, rst - 1)
                )
            ) if check_diff_pack else (
                NextValue(cnt, cnt - 1)
            )

            return If(rst != 7, # Infinite retries
                If(cnt != 0,
                    resend,
                    # CLASS A ERROR
                    decrement_cnt
                ).Else(
                    # CLASS B ERROR
                    NextState("ERROR"),

                    pre_cq.sink.status.eq(error_status),
                    pre_cq.sink.valid.eq(1)
                )
            ).Else(
                resend
            )

        # Wait for a response to a SEND or RDMA_WRITE
        fsm.act("WAIT_ACK",
            If(qps[1].qp_state == LiteEthIBQP.ERROR,
                NextState("ERROR")
            ).Else(
                If(outstanding_timer.done & (retry_cnt != 0),
                    # Local Ack Timeout error.
                    decrease_retry_cnt(resend=False, error_status=WC.Status.RESP_TIMEOUT_ERR),
                ),
                responses_fifo.source.ready.eq(1),
                # We receive a response with a psn in the expected range
                If(responses_fifo.source.valid & psn_ge(responses_fifo.source.psn, oldest_unacked_psn),
                    # We receive a response with a psn relating to a later request
                    If(psn_gt(responses_fifo.source.psn, last_expected_psn),
                        # We receive a response with a psn that is less than the last possible psn
                        If(psn_ge(responses_fifo.source.psn, qps[1].send_queue.psn),
                            responses_fifo.source.ready.eq(0),
                            outstanding_requests.source.ready.eq(1),
                            outstanding_requests_chooser.source.ready.eq(1),

                            # Push to pre_cq at end of response
                            pre_cq.sink.valid.eq(1),
                            pre_cq.sink.status.eq(WC.Status.SUCCESS),

                            NextState("NEXT_WR")
                        )
                        # Ignore packet if it has an invalid psn
                    ).Else(
                        # We cannot receive a READ Response on a request where we are expecting an ACK
                        # (responses to subsequent packets are treated in above condition)
                        If(responses_fifo.source.opcode_op == BTH_OPCODE_OP.Acknowledge,
                            qps[1].op_seq_check_req.update.eq(1),
                            If(ack_type == 0b00, # ACK
                                If(responses_fifo.source.psn == last_expected_psn,
                                    outstanding_requests.source.ready.eq(1),
                                    outstanding_requests_chooser.source.ready.eq(1),
                                    # CQ completion
                                    # Push to pre_cq at end of response
                                    pre_cq.sink.valid.eq(1),
                                    pre_cq.sink.status.eq(WC.Status.SUCCESS),

                                    NextState("NEXT_WR")
                                ).Else(
                                    NextValue(resend_starting_pos, resend_starting_pos + (oldest_unacked_psn - responses_fifo.source.psn) << PMTU_BITS),
                                    NextValue(resend_remaining_length, resend_remaining_length - (oldest_unacked_psn - responses_fifo.source.psn) << PMTU_BITS),
                                ),
                                NextValue(oldest_unacked_psn, responses_fifo.source.psn + 1),
                                NextValue(retry_cnt, qps[1].retry_cnt_rst),
                                NextValue(retry_cnt_rnr, qps[1].retry_cnt_rnr_rst)
                            ).Else(
                                NextValue(resend_starting_pos, resend_starting_pos + (oldest_unacked_psn - responses_fifo.source.psn - 1) << PMTU_BITS),
                                NextValue(resend_remaining_length, resend_remaining_length - (oldest_unacked_psn - responses_fifo.source.psn - 1) << PMTU_BITS),

                                NextValue(oldest_unacked_psn, responses_fifo.source.psn),
                            ),
                            If(ack_type == 0b01, # RNR NAK
                                # RNR NAK Retry error.
                                decrease_retry_cnt(rnr=True, check_diff_pack=True, error_status=WC.Status.RNR_RETRY_EXC_ERR),
                            ).Elif(ack_type == 0b11, # NAK
                                If(ack_value == NAK.Code.PSN_Sequence_Error,
                                    # Packet sequence error.
                                    decrease_retry_cnt(check_diff_pack=True, error_status=WC.Status.RETRY_EXC_ERR),
                                ).Else(
                                    # Unrecoverable NAK error.
                                    # CLASS B ERROR
                                    pre_cq.sink.valid.eq(1),
                                    Case(ack_value, {
                                        NAK.Code.Invalid_Request:
                                            pre_cq.sink.status.eq(WC.Status.REM_INV_REQ_ERR),
                                        NAK.Code.Remote_Access_Error:
                                            pre_cq.sink.status.eq(WC.Status.REM_ACCESS_ERR),
                                        NAK.Code.Remote_Operational_Error:
                                            pre_cq.sink.status.eq(WC.Status.REM_OP_ERR),
                                    }),
                                    NextState("ERROR")
                                )
                            )
                        ).Else(
                            # Bad response.
                            # CLASS B ERROR
                            pre_cq.sink.valid.eq(1),
                            pre_cq.sink.status.eq(WC.Status.BAD_RESP_ERR),
                            NextState("ERROR")
                        )
                    )
                # Ignore duplicate
                ),
                If(pre_cq.sink.valid & ~pre_cq.sink.ready,
                    NextState("ERROR")
                )
            )
        )

        fsm.act("WAIT_READ",
            If(qps[1].qp_state == LiteEthIBQP.ERROR,
                NextState("ERROR")
            ).Else(
                If(outstanding_timer.done & (retry_cnt != 0),
                    # Local Ack Timeout error.
                    decrease_retry_cnt(resend=False, error_status=WC.Status.RESP_TIMEOUT_ERR),
                ),
                responses_fifo.source.ready.eq(1),
                # We receive a response with a psn in the expected range
                If(responses_fifo.source.valid & psn_ge(responses_fifo.source.psn, oldest_unacked_psn),
                    # We receive a response with a psn larger than expected
                    If(responses_fifo.source.psn != oldest_unacked_psn,
                        # Implied NAK sequence error.
                        decrease_retry_cnt(error_status=WC.Status.RETRY_EXC_ERR),
                    ).Else(
                        # We receive an acknowledge
                        If(responses_fifo.source.opcode_op == BTH_OPCODE_OP.Acknowledge,
                            If(ack_type == 0b01, # RNR NAK
                                # RNR NAK Retry error.
                                decrease_retry_cnt(rnr=True, error_status=WC.Status.RNR_RETRY_EXC_ERR),
                            ).Elif(ack_type == 0b11, # NAK
                                If(ack_value == NAK.Code.PSN_Sequence_Error,
                                    # Packet sequence error.
                                    decrease_retry_cnt(error_status=WC.Status.RETRY_EXC_ERR),
                                ).Else(
                                    # Unrecoverable NAK error.
                                    # CLASS B ERROR
                                    pre_cq.sink.valid.eq(1),
                                    Case(ack_value, {
                                        NAK.Code.Invalid_Request:
                                            pre_cq.sink.status.eq(WC.Status.REM_INV_REQ_ERR),
                                        NAK.Code.Remote_Access_Error:
                                            pre_cq.sink.status.eq(WC.Status.REM_ACCESS_ERR),
                                        NAK.Code.Remote_Operational_Error:
                                            pre_cq.sink.status.eq(WC.Status.REM_OP_ERR),
                                    }),
                                    NextState("ERROR")
                                )
                            ).Else(
                                # Bad response
                                # CLASS B ERROR
                                pre_cq.sink.valid.eq(1),
                                pre_cq.sink.status.eq(WC.Status.BAD_RESP_ERR),
                                NextState("ERROR")
                            )
                        # We receive a read response
                        ).Else(
                            qps[1].op_seq_check_req.update.eq(1),
                            If(responses_fifo.source.psn == last_expected_psn,
                                outstanding_read_requests.source.ready.eq(1),
                                outstanding_requests_chooser.source.ready.eq(1),

                                # Push to pre_cq at end of read response
                                pre_cq.sink.valid.eq(1),
                                If((responses_fifo.source.opcode_op == BTH_OPCODE_OP.RDMA_READ_response_Last) |
                                (responses_fifo.source.opcode_op == BTH_OPCODE_OP.RDMA_READ_response_Only),
                                    pre_cq.sink.status.eq(WC.Status.SUCCESS),
                                    NextState("NEXT_WR")
                                ).Else(
                                    # Bad response
                                    pre_cq.sink.status.eq(WC.Status.BAD_RESP_ERR),
                                    NextState("ERROR")
                                )
                            ),

                            # For receiving RDMA READ responses
                            NextValue(qps[1].send_queue.rdma_state.va_r, resend_starting_pos + PMTU),
                            NextValue(qps[1].send_queue.rdma_state.va_l, qps[1].send_queue.rdma_state.va_l + PMTU),

                            NextValue(resend_starting_pos, resend_starting_pos + PMTU),
                            NextValue(resend_remaining_length, resend_remaining_length - PMTU),

                            NextValue(oldest_unacked_psn, oldest_unacked_psn + 1),
                            NextValue(retry_cnt, qps[1].retry_cnt_rst),
                            NextValue(retry_cnt_rnr, qps[1].retry_cnt_rnr_rst)
                        )
                    )
                ),
                If(pre_cq.sink.valid & ~pre_cq.sink.ready,
                    NextState("ERROR")
                )
            )
        )

        # Dump all outstanding requests and received responses
        fsm.act("ERROR",
            If(qps[1].qp_state != LiteEthIBQP.ERROR,
                NextValue(qps[1].local_error, 1)
            ),
            outstanding_requests.source.ready.eq(pre_cq.sink.ready),
            outstanding_read_requests.source.ready.eq(pre_cq.sink.ready),
            outstanding_requests_chooser.source.ready.eq(pre_cq.sink.ready),
            pre_cq.sink.valid.eq(outstanding_requests_chooser.source.valid),
            pre_cq.sink.status.eq(WC.Status.WR_FLUSH_ERR),
            responses_fifo.source.ready.eq(1),
            If(~outstanding_requests_chooser.source.valid &
                ~responses_fifo.source.valid,
                NextState("NEXT_WR")
            )
        )

        # Resend
        self.comb += [
            If(outstanding_requests_chooser.source.choose,
                outstanding_requests.source.connect(pending_resend.sink, omit={"valid", "ready", "psn", "va", "dma_len"}),
            ).Else(
                outstanding_read_requests.source.connect(pending_resend.sink, omit={"valid", "ready", "psn", "va", "dma_len"}),
            ),
            pending_resend.sink.va.eq(resend_starting_pos),
            pending_resend.sink.dma_len.eq(resend_remaining_length),
            pending_resend.sink.psn.eq(oldest_unacked_psn),
            pending_resend.sink.valid.eq(resend_ask | (outstanding_timer.done & (retry_cnt != 0)))
        ]

        # Dump resend buffer on ERROR
        self.comb += [
            If(fsm.ongoing("ERROR"),
                pending_resend.source.ready.eq(1)
            ).Else(
                pending_resend.source.connect(resend_source)
            )
        ]

class LiteEthRDMARequesterRX(LiteXModule):
    """
                           ┌───────────┐
                           │   sink    │
    ┌──────────────────────┴─────┬─────┴─────────────────────────┐
    │                            │                               │
    │                            │                               │
    │                            │                               │
    │    ┌───────────────────────▼────────────────────────┐      │
    │    │                                                │      │
    │    │                                                │      │
    │    │                RDMARequester                   │      │
    │    │                 Validation                     │      │
    │    │                                                │      │
    │    │                                                │      │
    │    └───▲─────┬────────────────────────────────┬─────┘      │
    │        │     │                                │            │
    │        │     │                                │            │
    │  oldest│     │                                │            │
    │ unacked│     │responses                       │            │
    │     psn│     │                                │            │
    │        │     │                                │data+params │
    │        │     │                                │            │
    │    ┌───┴─────▼─────┐                          │            │
    │    │               │                     ┌────▼─────┐      │
    │    │  RDMAContext  │                     │          │      │
    │    │    Checker    │                     │ WaitPipe │      │
    │    │               │                     │          │      │
    │    └──────┬────────┘                     └──┬────┬──┘      │
    │           │                    +1 on read   │    │         │
    │           │completion       ┌───────────────┘    │data     │
    │           │                 │  last or only      │         │
    │      ┌────▼─────┐     ┌─────▼──────┐     ┌───────▼──┐      │
    │      │          │     │    read    │     │          │      │
    │      │  pre_cq  │     │ completion │     │  Memory  │      │
    │      │          │     │    cnt     │     │          │      │
    │      └────┬─────┘     └─────┬──────┘     └──────────┘      │
    └───────────┼─────────────────┼──────────────────────────────┘
                │                 │
                └────────┬────────┘
                      ┌──▼──┐
                      │     │
                      │ cq  │
                      │     │
                      └─────┘
    """

    def __init__(self, requester_tx, qps, cq, mrs, clk_freq, dw=8):
        self.sink = sink = Endpoint(add_params(eth_rocev2_description(dw), [("header_only", 1), ("payload_length", PMTU_BITS + 1), ("validate", 1)]))

        # # #

        # Read (response) wait pipe
        read_wait_pipe = WaitPipe(EndpointDescription([("data", dw)], [("va", 64), ("l_key", 32), ("signal_cq", 1)]), 16, PMTU, dw=dw)
        self.submodules.read_wait_pipe = read_wait_pipe

        # A completion queue that waits for read responses to be committed to memory,
        # before actually moving CQEs to the CQ
        pre_cq = SyncFIFO(eth_rocev2_cq_description(), INITIATOR_DEPTH*2 + 0x10, buffered=True)
        self.submodules.pre_cq = pre_cq

        # Counting completed (committed to memory) read requests
        # There is no defined limit on read response packets, only on number of requests
        read_completion_cnt = Signal(max=INITIATOR_DEPTH*2)
        read_completion_inc = Signal()
        read_completion_dec = Signal()

        requester_validator = LiteEthRDMARequesterValidation(
            read_wait_pipe = read_wait_pipe,
            qps            = qps,
            mrs            = mrs,
            dw             = dw
        )
        self.submodules.requester_validator = requester_validator

        context_checker = LiteEthRDMARequesterContextChecker(
            qps      = qps,
            pre_cq   = pre_cq,
            clk_freq = clk_freq
        )
        self.submodules.context_checker = context_checker

        ## Datapath
        # Connect modules
        self.comb += [
            sink.connect(requester_validator.sink),

            requester_validator.source.connect(context_checker.responses_sink),
            requester_validator.oldest_unacked_psn.eq(context_checker.oldest_unacked_psn),
            context_checker.timer_start.eq(requester_tx.timer_start),

            context_checker.resend_source.connect(requester_tx.sink)
        ]

        # read_wait pipe -> memory
        self.comb += [
            If(read_wait_pipe.source.valid,
                Case(read_wait_pipe.source.l_key, {
                    Constant(mr.l_key, 32): [
                        read_wait_pipe.source.connect(mr.writer.sink, keep={"valid", "ready", "last", "data"}),
                        mr.writer.sink.va.eq(read_wait_pipe.source.va)
                    ] for mr in mrs if MR_PERM.LOCAL_WRITE in mr.permissions
                }),
            ),
            read_completion_inc.eq(
                read_wait_pipe.source.valid &
                read_wait_pipe.source.ready &
                read_wait_pipe.source.last &
                read_wait_pipe.source.signal_cq
            )
        ]

        # Completion queue logic
        self.comb += [
            If(pre_cq.source.valid,
                pre_cq.source.connect(cq.sink, omit={"valid", "ready"}),
                If(pre_cq.source.opcode == WC.Opcode.RDMA_READ,
                    If(read_completion_inc | (read_completion_cnt != 0),
                        read_completion_dec.eq(1),
                        pre_cq.source.connect(cq.sink, keep={"valid", "ready"})
                    )
                ).Else(
                    pre_cq.source.connect(cq.sink, keep={"valid", "ready"})
                )
            )
        ]

        self.sync += [
            read_completion_cnt.eq(read_completion_cnt + read_completion_inc - read_completion_dec)
        ]
