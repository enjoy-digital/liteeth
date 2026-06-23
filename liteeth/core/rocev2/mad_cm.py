from liteeth.common import *

from litex.soc.interconnect.stream import SyncFIFO, Buffer, Endpoint

from liteeth.core.rocev2.common import VariablePacketizer, VariableDepacketizer, VarWaitTimer
from liteeth.core.rocev2.qp import LiteEthIBQP
from liteeth.core.rocev2.mad_cm_msg import REP, REJ, DREP, DREQ, CPI, MAD_response, _CM_response
from liteeth.core.rocev2.ipcm import LiteEthIPCM

from litex.gen.genlib.misc import WaitTimer

MAD_PMTU = 256 # The payload size for MAD is always 256 regardless of PMTU

class LiteEthCMConn(LiteXModule):
    def __init__(self):
        self.qpn = Signal(24)
        self.Remote_Communication_ID = Signal(32)

        # C13-19.1.1: Transaction uniqueness is ensured by the combination of
        # MgmtClass, TransactionID (and SGID or SLID, which don't exist for RoCEv2)
        # Since we only support communication Managment, MgmtClass is always the same
        self.TransactionID = Signal(64)

        self.saved = Signal()

class LiteEthCMDepacketizer(VariableDepacketizer):
    def __init__(self, dw=8):
        VariableDepacketizer.__init__(self,
            eth_rocev2_user_description(dw),
            eth_mad_description(dw),
            mad_cm_headers,
            mad_cm_opmap,
            "AttributeID"
        )

class LiteEthCMPacketizer(VariablePacketizer):
    def __init__(self, dw=8):
        VariablePacketizer.__init__(self,
            eth_mad_description(dw),
            eth_rocev2_user_description(dw),
            mad_cm_headers,
            mad_cm_opmap,
            "AttributeID"
        )

class LiteEthIBMADTX(LiteXModule):
    def __init__(self, buffered_out=True, dw=8):
        self.source = source = Endpoint(eth_rocev2_user_description(dw))
        self.reply_sink = Endpoint(eth_mad_description(dw))

        # # #

        if buffered_out:
            buff_out = Buffer(eth_rocev2_user_description(dw), pipe_ready=True)
            self.submodules.buff_out = buff_out
            self.comb += buff_out.source.connect(source)
            source = buff_out.sink

        # Buffer to get replies from LiteEthIBMADRX
        reply_buf = Buffer(eth_mad_description(dw), pipe_ready=True)
        self.submodules.reply_buf = reply_buf
        self.comb += self.reply_sink.connect(reply_buf.sink)

        # Packetizer.
        self.packetizer = packetizer = LiteEthCMPacketizer(dw=dw)

        self.comb += [
            reply_buf.source.connect(packetizer.sink, omit={"ready"}),
            packetizer.sink.header_only.eq(1)
        ]

        counter = Signal(max=MAD_PMTU, reset_less=True)

        # FSM
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(packetizer.source.valid,
                NextValue(counter, 0),
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            packetizer.source.connect(source, keep={"valid", "ready", "data"}),
            If(source.valid & source.ready,
                NextValue(counter, counter + 1),
                If(packetizer.source.last,
                    NextState("PAD"),
                    reply_buf.source.ready.eq(1),
                    If(counter == MAD_PMTU - 1,
                        NextState("IDLE")
                    )
                )
            )
        )
        # MAD packets are always the size of MAD_PMTU
        fsm.act("PAD",
            source.valid.eq(1),
            source.last.eq(counter == MAD_PMTU - 1),
            If(source.valid & source.ready,
                NextValue(counter, counter + 1),
                If(source.last,
                    NextState("IDLE")
                )
            )
        )

# See page 698 of doc for reference
class CMStateFSM(LiteXModule):
    def __init__(self, qp, cm_conn, clk_freq, dw=8):
        self.sink = sink = Endpoint(eth_mad_description(dw))
        self.qp_rcv_wire = qp_rcv_wire = Signal()

        # # #

        # Buffer for incoming requests
        buff_in = Buffer(eth_mad_description(dw))
        self.submodules.buff_in = buff_in
        self.comb += sink.connect(buff_in.sink)
        sink = buff_in.source

        # We are always consuming
        self.comb += sink.ready.eq(1)

        # The CM waits after certain operations
        # (the time can change depending on REQ parameters)
        cm_timer = VarWaitTimer(clk_freq)
        self.submodules.cm_timer = cm_timer

        local_cm_timeout = Signal(5, reset_less=True)

        # CM FSM
        self.fsm = fsm = FSM(reset_state="LISTEN")
        fsm.act("LISTEN",
            NextValue(qp.qp_state, LiteEthIBQP.INIT),
            If(sink.valid &
               (qp.qp_state == LiteEthIBQP.INIT) &
               (sink.AttributeID == MAD_ATTRIB_ID.ConnectRequest),
                NextValue(local_cm_timeout, sink.Local_CM_Response_Timeout),

                NextValue(qp.other_id, sink.Local_QPN),
                NextValue(qp.send_queue.psn, sink.Starting_PSN),
                NextValue(qp.ip_address, sink.Primary_Local_Port_GID[:32]),

                NextValue(qp.retry_cnt_rst, sink.Retry_Count),
                NextValue(qp.retry_cnt_rnr_rst, sink.RNR_Retry_Count),

                NextState("REQ Rcvd")
            )
        )

        fsm.act("REQ Rcvd",
            If((qp.qp_state == LiteEthIBQP.INIT) | (qp.qp_state == LiteEthIBQP.ERROR),
                REP.emit(qp),
                NextValue(qp.qp_state, LiteEthIBQP.RTR),
                cm_timer.pow.eq(local_cm_timeout),
                NextState("REP Sent")
            ).Else(
                REJ.emit(REJ.REASON.No_QP_available),
                NextState("REJ Sent")
            )
        )

        fsm.act("REJ Sent",
            NextValue(qp.qp_state, LiteEthIBQP.ERROR),
            NextState("ERROR")
        )

        fsm.act("REP Sent",
            cm_timer.wait.eq(1),
            If(cm_timer.done,
                NextState("RTU Timeout")
            ).Else(
                If((sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.ReadyToUse)) | qp_rcv_wire,
                    NextValue(qp.qp_state, LiteEthIBQP.RTS),
                    NextState("ESTABLISHED")
                ).Elif(sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.MsgRcptAck),
                    cm_timer.pow.eq(sink.ServiceTimeout),
                    NextState("MRA(REP) Rcvd")
                ).Elif(sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.ConnectReject),
                    NextValue(qp.qp_state, LiteEthIBQP.ERROR),
                    NextState("ERROR")
                ).Elif(sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.ConnectRequest),
                    REP.emit(qp),
                )
            )
        )

        fsm.act("MRA(REP) Rcvd",
            cm_timer.wait.eq(1),
            If(cm_timer.done,
                NextState("RTU Timeout")
            ).Else(
                If((sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.ReadyToUse)) | qp_rcv_wire,
                    NextValue(qp.qp_state, LiteEthIBQP.RTS),
                    NextState("ESTABLISHED")
                ).Elif(sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.ConnectReject),
                    NextValue(qp.qp_state, LiteEthIBQP.ERROR),
                    NextState("ERROR")
                )
            )
        )

        fsm.act("ESTABLISHED",
            If(sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.DisconnectRequest) &
              (sink.Local_Communication_ID == cm_conn.Remote_Communication_ID) &
              (sink.Remote_Communication_ID == LOCAL_COMMUNICATION_ID) &
              (sink.Remote_QPN_EECN == cm_conn.qpn),
                NextValue(qp.qp_state, LiteEthIBQP.ERROR),
                NextState("DREQ Rcvd")
            ).Elif(sink.valid &
                 ((sink.AttributeID == MAD_ATTRIB_ID.ConnectReply) |
                  (sink.AttributeID == MAD_ATTRIB_ID.ConnectRequest)),
                If(sink.Local_QPN == qp.other_id,
                    REJ.emit(REJ.REASON.Stale_connection),
                    NextState("Send DREQ")
                )
            ).Elif(qp.local_error,
                NextState("Send DREQ")
            )
        )

        fsm.act("DREQ Rcvd",
            DREP.emit(),
            cm_timer.pow.eq(local_cm_timeout),
            NextState("TimeWait")
        )

        # TODO Warning: Timers look at queue time not at send time
        fsm.act("DREQ Sent",
            cm_timer.wait.eq(1),
            If(cm_timer.done,
                NextState("DREP Timeout")
            ).Else(
                If(sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.DisconnectRequest) &
                    (sink.Local_Communication_ID == cm_conn.Remote_Communication_ID) &
                    (sink.Remote_Communication_ID == LOCAL_COMMUNICATION_ID) &
                    (sink.Remote_QPN_EECN == cm_conn.qpn),
                    NextState("DREQ Rcvd")
                ).Elif(sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.DisconnectReply),
                    cm_timer.pow.eq(local_cm_timeout),
                    NextState("TimeWait")
                )
            )
        )

        fsm.act("RTU Timeout",
            REJ.emit(REJ.REASON.Timeout),
            cm_timer.pow.eq(local_cm_timeout),
            NextValue(qp.qp_state, LiteEthIBQP.ERROR),
            NextState("TimeWait")
        )

        fsm.act("TimeWait",
            cm_timer.wait.eq(1),
            If(cm_timer.done,
                NextState("ERROR")
            ).Else(
                If(sink.valid & (sink.AttributeID == MAD_ATTRIB_ID.DisconnectRequest) &
                (sink.Local_Communication_ID == cm_conn.Remote_Communication_ID) &
                (sink.Remote_Communication_ID == LOCAL_COMMUNICATION_ID) &
                (sink.Remote_QPN_EECN == cm_conn.qpn),
                    DREP.emit()
                )
            )
        )

        fsm.act("DREP Timeout",
            DREQ.emit(qp.other_id),
            cm_timer.pow.eq(local_cm_timeout),
            NextState("TimeWait")
        )

        # State added for convenience of treating the connection established
        # and stale qp case (REQ Received)
        fsm.act("Send DREQ",
            DREQ.emit(qp.other_id),
            cm_timer.pow.eq(local_cm_timeout),
            NextState("DREQ Sent")
        )

        # FIXME: Rather arbitrary time choice
        self.error_timer = error_timer = WaitTimer(PMTU)

        fsm.act("ERROR",
            NextValue(qp.qp_state, LiteEthIBQP.ERROR),
            error_timer.wait.eq(1),
            If(error_timer.done,
                NextState("RESET")
            )
        )

        fsm.act("RESET",
            NextValue(qp.qp_state, LiteEthIBQP.RESET),
            NextState("LISTEN")
        )


class LiteEthIBMADRX(LiteXModule):
    def __init__(self, qps, cm_conn, reply_sink, clk_freq, buffered_in=True, dw=8):
        self.sink   = sink   = Endpoint(eth_rocev2_user_description(dw))
        self.source = source = Endpoint(EndpointDescription([("data", 8)], [("AttributeID", 16)]))
        self.validate_sink = validate_sink = Endpoint([("validate", 1)])
        self.qp_rcv_wire = Signal()

        # # #

        if buffered_in:
            buff_in = Buffer(eth_rocev2_user_description(dw), pipe_ready=True)
            self.submodules.buff_in = buff_in
            self.comb += sink.connect(buff_in.sink)
            sink = buff_in.source

        # Depacketizer.
        self.depacketizer = depacketizer = LiteEthCMDepacketizer(dw=dw)
        self.comb += sink.connect(depacketizer.sink)

        self.reply_pipe = reply_pipe = SyncFIFO(eth_mad_description(dw), 3, buffered=True)
        self.comb += reply_pipe.source.connect(reply_sink)
        reply_sink = reply_pipe.sink

        # To avoid setting sink and context on every emit CM response call
        _CM_response.set_attrs(
            reply_sink = reply_sink,
            cm_ctx     = cm_conn
        )

        # CM state controller
        cm_state = CMStateFSM(qps[1], cm_conn, clk_freq, dw=dw)
        self.submodules += cm_state

        new_request = Signal()

        self.comb += [
            If(new_request,
                depacketizer.source.connect(cm_state.sink, omit={"ready", "header_only"})
            )
        ]
        self.comb += cm_state.qp_rcv_wire.eq(self.qp_rcv_wire)

        # Message validation flags
        valid_base_version  = Signal()
        valid_class_version = Signal()
        valid_methattrcombo = Signal()
        valid_meth          = Signal()
        valid_conn_type     = Signal()

        valid_pmtu            = Signal()
        valid_initiator_depth = Signal()

        self.comb += valid_base_version.eq(depacketizer.source.BaseVersion == 1)
        self.comb += valid_class_version.eq(depacketizer.source.ClassVersion == 2)
        self.comb += valid_methattrcombo.eq(
            (
                (depacketizer.source.Method == CM_Methods.ComMgtGet) &
                (depacketizer.source.AttributeID == MAD_ATTRIB_ID.ClassPortInfo)
            ) | (
                (depacketizer.source.Method == CM_Methods.ComMgtSend) &
                is_in(depacketizer.source.AttributeID, [
                    MAD_ATTRIB_ID.ConnectRequest,
                    MAD_ATTRIB_ID.MsgRcptAck,
                    MAD_ATTRIB_ID.ConnectReject,
                    MAD_ATTRIB_ID.ConnectReply,
                    MAD_ATTRIB_ID.ReadyToUse,
                    MAD_ATTRIB_ID.DisconnectRequest,
                    MAD_ATTRIB_ID.DisconnectReply,
                    MAD_ATTRIB_ID.ServiceIDResReq,
                    MAD_ATTRIB_ID.ServiceIDResReqResp,
                    MAD_ATTRIB_ID.LoadAlternatePath,
                    MAD_ATTRIB_ID.AlternatePathResponse
                ])
            )
        )
        self.comb += valid_meth.eq(is_in(depacketizer.source.Method, [
            CM_Methods.ComMgtGet,
            CM_Methods.ComMgtSet,
            CM_Methods.ComMgtGetResp,
            CM_Methods.ComMgtSend
        ]))
        # We only support RC (and UD only for QP1)
        self.comb += valid_conn_type.eq(depacketizer.source.Transport_Service_Type == QP_CONN_TYPE.RC)

        self.comb += valid_pmtu.eq(depacketizer.source.Path_Packet_Payload_MTU == PMTU_KEY)
        self.comb += valid_initiator_depth.eq(depacketizer.source.Initiator_Depth <= RESPONDER_RESOURCES)

        # Transaction persistance checking
        valid_transaction = Signal()

        self.comb += valid_transaction.eq(~cm_conn.saved |
            ((depacketizer.source.TransactionID == cm_conn.TransactionID) |
             (depacketizer.source.MgmtClass == 0x7))
        )

        # MAD RX FSM
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(depacketizer.source.valid,
                If(valid_base_version & valid_class_version & valid_meth & valid_methattrcombo & valid_conn_type &
                    valid_transaction,
                    If(depacketizer.source.AttributeID == MAD_ATTRIB_ID.ClassPortInfo,
                        CPI.emit()
                    ),
                    If(depacketizer.source.AttributeID != MAD_ATTRIB_ID.ConnectRequest,
                        new_request.eq(1),
                    ),
                    NextState("PRIVATE_DATA")
                ).Else(
                    If(depacketizer.source.AttributeID == MAD_ATTRIB_ID.ConnectRequest,
                        If(~valid_base_version,
                            REJ.emit(REJ.REASON.Unsupported_request, status=MAD_response.MAD_Status.BadVersion)
                        ).Elif(~valid_class_version,
                            REJ.emit(REJ.REASON.Unsupported_Class_Version, status=MAD_response.MAD_Status.BadVersion)
                        ).Elif(~valid_meth,
                            REJ.emit(REJ.REASON.Unsupported_request, status=MAD_response.MAD_Status.MethodNotSupported)
                        ).Elif(~valid_methattrcombo,
                            REJ.emit(REJ.REASON.Unsupported_request, status=MAD_response.MAD_Status.MethodAttributeComboNotSupported)
                        ).Elif(~valid_pmtu,
                            REJ.emit(REJ.REASON.Invalid_Path_MTU, status=MAD_response.MAD_Status.AttributeInvalidValue)
                        ).Elif(~valid_initiator_depth,
                            REJ.emit(REJ.REASON.Insufficient_Responder_Resources, status=MAD_response.MAD_Status.AttributeInvalidValue)
                        )
                    ),
                    NextState("DROP")
                )
            )
        )

        fsm.act("PRIVATE_DATA",
            depacketizer.source.connect(source, keep={"valid", "last", "data", "AttributeID"}),
            depacketizer.source.ready.eq(
                ((source.AttributeID != MAD_ATTRIB_ID.ConnectRequest) | ~source.last) &
                source.ready
            ),
            If(source.valid & source.ready & source.last,
                If(source.AttributeID == MAD_ATTRIB_ID.ConnectRequest,
                    NextState("AWAIT_VALIDATION")
                ).Else(
                    NextState("IDLE")
                )
            )
        )

        fsm.act("AWAIT_VALIDATION",
            validate_sink.ready.eq(1),
            If(validate_sink.valid,
                depacketizer.source.ready.eq(1),
                new_request.eq(validate_sink.validate),
                If(validate_sink.validate,
                    NextValue(cm_conn.qpn, qps[1].id),
                    NextValue(cm_conn.Remote_Communication_ID, depacketizer.source.Local_Communication_ID),
                    NextValue(cm_conn.TransactionID, depacketizer.source.TransactionID),
                    NextValue(cm_conn.saved, 1)
                ),
                NextState("IDLE")
            )
        )

        fsm.act("DROP",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid & depacketizer.source.ready & depacketizer.source.last,
                NextState("IDLE")
            )
        )


class LiteEthIBMAD(LiteXModule):
    def __init__(self, qps, clk_freq, dw=8):
        self.cm_conn = cm_conn = LiteEthCMConn()
        self.tx = tx = LiteEthIBMADTX(buffered_out=True)
        self.rx = rx = LiteEthIBMADRX(qps, cm_conn, tx.reply_sink, clk_freq, buffered_in=True)

        self.ipcm = ipcm = LiteEthIPCM(rx)

        self.comb += [
            ipcm.validate_sink.validate.eq(1),
            ipcm.validate_sink.valid.eq(1)
        ]
