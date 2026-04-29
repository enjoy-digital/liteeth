from litex.gen import *

from liteeth.common import *

from litex.soc.interconnect.stream import SyncFIFO

# Queue pairs --------------------------------------------------------------------------------------
class _LiteEthIBQueue(LiteXModule):
    def __init__(self, dw=8):
        self.psn   = Signal(24, reset=STARTING_PSN)
        self.p_key = Signal(16)

        # # #

        # Default partition
        self.comb += self.p_key.eq(DEFAULT_P_KEY)

class _LiteEthIBGenericQP(LiteXModule):
    def __init__(self, id, conn_type):
        # The two queues constituting the QP
        self.send_queue    = _LiteEthIBQueue()
        self.receive_queue = _LiteEthIBQueue()

        self.id = Constant(id, 24)
        # Id of the connected qp
        self.other_id = Signal(24, reset_less=True)

        # Signals tracking the state of the QP
        self.conn_type  = Signal(3)
        self.msn        = Signal(24, reset_less=True)

        # # #

        # We treat conn_type as a constant
        self.comb += self.conn_type.eq(conn_type)

class LiteEthIBQP(_LiteEthIBGenericQP):
    """Infiniband Queue Pair

    A pair of queues (send and receive) used for establishing an RC (Reliable Connection)

    Parameters
    ----------
    id : int
        Unique number associated with the QP.

    Attributes
    ----------
    qp_state : in/out
        Current state of the QP.
    ip_address : in/out
        Current IP address of the QP this QP is connected to
    nak_sent : in/out
        Indicates if the qp has an outstanding NAK
    """
    (RESET, INIT, RTR, RTS, ERROR) = range(5)
    def __init__(self, id):
        super().__init__(id, QP_CONN_TYPE.RC)
        assert id not in [0, 1] # 0 and 1 are special QPs

        self.qp_state   = qp_state   = Signal(bits_for(4), reset=LiteEthIBQP.RESET)
        self.ip_address = ip_address = Signal(32)
        self.nak_sent   = nak_sent   = Signal()

        self.rdma_state = Record([("va", 64, DIR_M_TO_S), ("r_key", 32, DIR_M_TO_S), ("dma_len", 32, DIR_M_TO_S)], reset_less=True)

        # # #

        # We reset the QP depending on state
        reset = Signal()
        self.comb += reset.eq(qp_state == LiteEthIBQP.RESET)

        op_seq_check = OpcodeSequenceChecker()
        self.add_module("op_seq_check", op_seq_check)

        # Reset logic
        self.comb += op_seq_check.reset.eq(reset)
        self.sync += [
            If(reset,
                self.receive_queue.psn.eq(self.receive_queue.psn.reset),
                self.msn.eq(self.msn.reset),
                self.rdma_state.va.eq(self.rdma_state.va.reset),
                self.rdma_state.r_key.eq(self.rdma_state.r_key.reset),
                self.rdma_state.dma_len.eq(self.rdma_state.dma_len.reset),
                ip_address.eq(ip_address.reset),
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
class OpcodeSequenceChecker(LiteXModule):
    """Opcode Sequence checker

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
                BTH_OPCODE_OP.RDMA_READ_response_First,
                BTH_OPCODE_OP.RDMA_READ_response_Middle, #
                BTH_OPCODE_OP.RDMA_READ_response_Last,   #
                BTH_OPCODE_OP.RDMA_READ_response_Only,
                BTH_OPCODE_OP.Acknowledge,
                # BTH_OPCODE_OP.CmpSwap,
                # BTH_OPCODE_OP.FetchAdd,
                BTH_OPCODE_OP.SEND_First,
                BTH_OPCODE_OP.RDMA_WRITE_First
            ])),
            # State transitions
            If(update,
                Case(opcode, {
                    **dict.fromkeys([
                        BTH_OPCODE_OP.SEND_First,
                        BTH_OPCODE_OP.SEND_Only,
                        BTH_OPCODE_OP.SEND_Only_with_Immediate,
                        BTH_OPCODE_OP.RDMA_WRITE_First,
                        BTH_OPCODE_OP.RDMA_WRITE_Only,
                        BTH_OPCODE_OP.RDMA_WRITE_Only_with_Immediate,
                        BTH_OPCODE_OP.RDMA_READ_Request,
                        BTH_OPCODE_OP.RDMA_READ_response_First,
                        BTH_OPCODE_OP.RDMA_READ_response_Middle, #
                        BTH_OPCODE_OP.RDMA_READ_response_Last,   #
                        BTH_OPCODE_OP.RDMA_READ_response_Only,
                        BTH_OPCODE_OP.Acknowledge,
                        # BTH_OPCODE_OP.CmpSwap,
                        # BTH_OPCODE_OP.FetchAdd
                    ], NextState("NONE")),

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
                    BTH_OPCODE_OP.SEND_Middle:              NextState("SEND"),
                    BTH_OPCODE_OP.SEND_Last:                NextState("NONE"),
                    BTH_OPCODE_OP.SEND_Last_with_Immediate: NextState("NONE"),
                    "default":                              NextState("NONE")
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
                    BTH_OPCODE_OP.RDMA_WRITE_Middle:              NextState("RDMA_WRITE"),
                    BTH_OPCODE_OP.RDMA_WRITE_Last:                NextState("NONE"),
                    BTH_OPCODE_OP.RDMA_WRITE_Last_with_Immediate: NextState("NONE"),
                    "default":                                    NextState("NONE")
                })
            )
        )
