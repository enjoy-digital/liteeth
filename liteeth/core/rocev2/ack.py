from liteeth.common import *

class _Acknowledge:
    @staticmethod
    def emit(leading_bits, code, dest_qp, msn, psn, ack_sink):
        assert leading_bits < 4 # 2 bits
        assert code < 32        # 5 bits
        if leading_bits & 0b11 == 0b10:
            print("Reserved pattern")
            raise ValueError
        return [
            ack_sink.valid.eq(1),
            ack_sink.last.eq(1),
            ack_sink.psn.eq(psn),
            ack_sink.opcode.eq(BTH_OPCODE.RC.Acknowledge),
            ack_sink.dest_qp.eq(dest_qp),
            ack_sink.syndrome.eq(Constant((leading_bits << 5) + code, 8)),
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
        # Invalid_RD_Request       = 4

    @staticmethod
    def emit(code, dest_qp, msn, psn, ack_sink, duplicate, qps):
        return [
            If(~duplicate,
                Case(dest_qp, {qp.id : [
                    If(~qp.nak_sent,
                        NextValue(qp.nak_sent, 1),
                        super(__class__, __class__).emit(0b11, code, dest_qp, msn, psn, ack_sink)
                    )
                ] for qp in qps[1:]}),
            )
        ]
