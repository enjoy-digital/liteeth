from litex.gen import *

from liteeth.common import *

from litex.soc.interconnect.stream import SyncFIFO

# Queue pairs --------------------------------------------------------------------------------------
class _LiteEthIBSendQueue(SyncFIFO):
    def __init__(self, layout, depth, with_rdma_state=True):
        super().__init__(layout, depth, buffered=True)

        self.psn   = Signal(24)
        if with_rdma_state:
            # only for tracking RDMA Read Responses locations
            self.rdma_state = Record([
                ("va_r",  64, DIR_M_TO_S),
                ("va_l",  64, DIR_M_TO_S),
                ("l_key", 32, DIR_M_TO_S)
            ], reset_less=True)

class _LiteEthIBReceiveQueue(SyncFIFO):
    def __init__(self, layout, depth, with_rdma_state=True):
        super().__init__(layout, depth, buffered=True)

        self.psn   = Signal(24, reset=STARTING_PSN)
        if with_rdma_state:
            # mem_key is r_key on WRITE and l_key on SEND (which cannot be received simultaneously)
            self.rdma_state = Record([
                ("va",      64, DIR_M_TO_S),
                ("mem_key", 32, DIR_M_TO_S)
            ], reset_less=True)

class _LiteEthIBGenericQP(LiteXModule):
    def __init__(self, qp_id, conn_type):
        # The two queues constituting the QP
        self.send_queue    = _LiteEthIBSendQueue(
            layout          = eth_rocev2_send_wr_description(),
            depth           = 16,
            with_rdma_state = (qp_id not in [0, 1])
        )
        self.receive_queue = _LiteEthIBReceiveQueue(
            layout          = eth_rocev2_recv_wr_description(),
            depth           = 16,
            with_rdma_state = (qp_id not in [0, 1])
        )

        self.id = Constant(qp_id, 24)
        # Id of the connected qp
        self.other_id   = Signal(24, reset_less=True)
        self.ip_address = Signal(32)

        # Signals tracking the state of the QP
        self.conn_type = Signal(3)
        self.msn       = Signal(24, reset_less=True)

        self.p_key = Signal(16)

        # # #

        # We treat conn_type as a constant
        self.comb += self.conn_type.eq(conn_type)
        # Default partition
        self.comb += self.p_key.eq(DEFAULT_P_KEY)

class LiteEthIBQP(_LiteEthIBGenericQP):
    """Infiniband Queue Pair

    A pair of queues (send and receive) used for establishing an RC (Reliable Connection)

    Parameters
    ----------
    qp_id : int
        Unique number associated with the QP.

    Attributes
    ----------
    qp_state : in/out
        Current state of the QP.
    ip_address : in/out
        Current IP address of the QP this QP is connected to.
    nak_sent : in/out
        Indicates if the qp has an outstanding NAK.
    retry_cnt_rst: in/out
        Value to which the retry counter has to be reset.
    retry_cnt_rnr_rst: in/out
        Value to which the rnr retry counter has to be reset.
    """
    (RESET, INIT, RTR, RTS, ERROR) = range(5)
    def __init__(self, qp_id):
        super().__init__(qp_id, QP_CONN_TYPE.RC)
        assert qp_id not in [0, 1] # 0 and 1 are special QPs

        self.qp_state = qp_state = Signal(bits_for(4), reset=LiteEthIBQP.RESET)
        self.nak_sent = nak_sent = Signal()

        self.local_error = local_error = Signal()

        self.retry_cnt_rst     = Signal(3)
        self.retry_cnt_rnr_rst = Signal(3)

        # TODO How many requests can be outstanding?
        # Requests that are awaiting acknowledgement
        self.outstanding_read_requests = SyncFIFO(
            layout   = add_params(eth_rocev2_send_wr_description(), [("psn", 24)]),
            depth    = INITIATOR_DEPTH,
            buffered = False
        )
        self.outstanding_requests = SyncFIFO(
            layout   = add_params(eth_rocev2_send_wr_description(), [("psn", 24)]),
            depth    = 16,
            buffered = False
        )
        self.outstanding_requests_chooser = SyncFIFO(
            layout   = [("choose", 1)],
            depth    = INITIATOR_DEPTH + 16,
            buffered = False
        )

        # # #

        # We reset the QP depending on state
        reset = Signal()
        self.comb += reset.eq(qp_state == LiteEthIBQP.RESET)

        op_seq_check_req  = OpcodeSequenceCheckerRequester()
        op_seq_check_resp = OpcodeSequenceCheckerResponder()
        self.submodules.op_seq_check_req  = op_seq_check_req
        self.submodules.op_seq_check_resp = op_seq_check_resp

        # Reset logic
        self.comb += op_seq_check_req.reset.eq(reset)
        self.comb += op_seq_check_resp.reset.eq(reset)
        self.sync += [
            If(reset,
                self.receive_queue.psn.eq(self.receive_queue.psn.reset),
                self.msn.eq(self.msn.reset),
                self.receive_queue.rdma_state.va.eq(self.receive_queue.rdma_state.va.reset),
                self.receive_queue.rdma_state.mem_key.eq(self.receive_queue.rdma_state.mem_key.reset),
                self.send_queue.rdma_state.va_r.eq(self.send_queue.rdma_state.va_r.reset),
                self.send_queue.rdma_state.va_l.eq(self.send_queue.rdma_state.va_l.reset),
                self.send_queue.rdma_state.l_key.eq(self.send_queue.rdma_state.l_key.reset),
                self.ip_address.eq(self.ip_address.reset),
                local_error.eq(0),
                nak_sent.eq(0)
            )
        ]

# 0 and 1 are reserved QP ids. 1 is the only special QP in RoCEv2
class LiteEthIBSpecialQP(_LiteEthIBGenericQP):
    def __init__(self):
        super().__init__(1, QP_CONN_TYPE.UD)

        self.other_id = Constant(1, 24)

# Misc ---------------------------------------------------------------------------------------------
@ResetInserter()
class OpcodeSequenceCheckerResponder(LiteXModule):
    """Opcode Sequence checker for the responder

    Checks that the sequence of received opcodes is correct

    Attributes
    ----------
    update : in
        Consume new packet opcode.
    opcode : in
        Opcode of incoming packet.
    invalid_sequence : out
        Indicates that the current incoming packet
        has an invalid opcode considering the previous
        ones.
    """
    def __init__(self):
        self.update = update = Signal()
        self.opcode = opcode = Signal(5)

        self.invalid_sequence = invalid_sequence = Signal()

        # # #
        self.fsm = fsm = FSM(reset_state="NONE")
        fsm.act("NONE",
            # Invalid sequence
            invalid_sequence.eq(~is_in(opcode, [
                BTH_OPCODE_OP.SEND_First,
                BTH_OPCODE_OP.SEND_Only,
                BTH_OPCODE_OP.SEND_Only_with_Immediate,
                BTH_OPCODE_OP.RDMA_WRITE_First,
                BTH_OPCODE_OP.RDMA_WRITE_Only,
                BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate,
                BTH_OPCODE_OP.RDMA_READ_Request,
                # BTH_OPCODE_OP.CmpSwap,
                # BTH_OPCODE_OP.FetchAdd
            ])),
            # State transitions
            If(update,
                Case(opcode, {
                    BTH_OPCODE_OP.SEND_First:       NextState("SEND"),
                    BTH_OPCODE_OP.RDMA_WRITE_First: NextState("RDMA_WRITE"),
                    "default":                      NextState("NONE")
                })
            )
        )

        fsm.act("SEND",
            # Invalid sequence
            invalid_sequence.eq(~is_in(opcode, [
                BTH_OPCODE_OP.SEND_Middle,
                BTH_OPCODE_OP.SEND_Last,
                BTH_OPCODE_OP.SEND_Last_with_Immediate
            ])),
            # State transitions
            If(update,
                Case(opcode, {
                    BTH_OPCODE_OP.SEND_Middle: NextState("SEND"),
                    "default":                 NextState("NONE")
                })
            )
        )

        fsm.act("RDMA_WRITE",
            # Invalid sequence
            invalid_sequence.eq(~is_in(opcode, [
                BTH_OPCODE_OP.RDMA_WRITE_Middle,
                BTH_OPCODE_OP.RDMA_WRITE_Last,
                BTH_OPCODE_OP.RDMA_WRITE_Last_with_Immediate
            ])),
            # State transitions
            If(update,
                Case(opcode, {
                    BTH_OPCODE_OP.RDMA_WRITE_Middle: NextState("RDMA_WRITE"),
                    "default":                       NextState("NONE")
                })
            )
        )

@ResetInserter()
class OpcodeSequenceCheckerRequester(LiteXModule):
    """Opcode Sequence checker for the requester

    Checks that the sequence of received opcodes is correct

    Attributes
    ----------
    update : in
        Consume new packet opcode.
    opcode : in
        Opcode of incoming packet.
    invalid_sequence : out
        Indicates that the current incoming packet
        has an invalid opcode considering the previous
        ones.
    """
    def __init__(self):
        self.update = update = Signal()
        self.opcode = opcode = Signal(5)

        self.invalid_sequence = invalid_sequence = Signal()

        # # #
        self.fsm = fsm = FSM(reset_state="NONE")
        fsm.act("NONE",
            # Invalid sequence
            invalid_sequence.eq(~is_in(opcode, [
                BTH_OPCODE_OP.RDMA_READ_response_First,
                BTH_OPCODE_OP.RDMA_READ_response_Only,
                BTH_OPCODE_OP.Acknowledge
            ])),
            # State transitions
            If(update,
                Case(opcode, {
                    BTH_OPCODE_OP.RDMA_READ_response_First: NextState("RDMA_READ"),
                    "default":                              NextState("NONE")
                })
            )
        )

        fsm.act("RDMA_READ",
            # Invalid sequence
            invalid_sequence.eq(~is_in(opcode, [
                BTH_OPCODE_OP.RDMA_READ_response_Middle,
                BTH_OPCODE_OP.RDMA_READ_response_Last
            ])),
            # State transitions
            If(update,
                Case(opcode, {
                    BTH_OPCODE_OP.RDMA_READ_response_Middle: NextState("RDMA_READ"),
                    "default":                               NextState("NONE")
                })
            )
        )

# Completion queue
class LiteEthCQ(SyncFIFO):
    def __init__(self, depth, buffered=True):
        super().__init__(eth_rocev2_cq_description(), depth, buffered)

