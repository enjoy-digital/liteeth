from liteeth.common import *

from litex.soc.interconnect.stream import SyncFIFO, Buffer, Endpoint

from liteeth.core.rocev2.common import VariablePacketizer, VariableDepacketizer
from liteeth.core.rocev2.qp import LiteEthIBQP

from enum import IntFlag

MAD_PMTU = 256 # The payload size for MAD is always 256 regardless of PMTU

class LiteEthCMConn(LiteXModule):
    def __init__(self):
        self.qpn = Signal(24)
        self.Remote_Communication_ID = Signal(32)

class LiteEthCMDepacketizer(LiteXModule):
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
    def __init__(self, qps, buffered_out=True, dw=8):
        self.source = source = Endpoint(eth_rocev2_user_description(dw))

        if buffered_out:
            buff_out = Buffer(eth_rocev2_user_description(dw), pipe_ready=True)
            self.submodules += buff_out
            self.comb += buff_out.source.connect(source)
            source = buff_out.sink

        reply_pipe = Buffer(eth_mad_description(dw), pipe_ready=True)
        self.submodules += reply_pipe

        self.reply_sink = reply_pipe.sink

        # # #
        counter = Signal(max=MAD_PMTU, reset_less=True)

        # Packetizer.
        self.packetizer = packetizer = LiteEthCMPacketizer(dw=dw)

        self.comb += [
            reply_pipe.source.connect(packetizer.sink, omit={"ready"}),
            packetizer.sink.header_only.eq(1)
        ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        self.fsm_indicator_mad_tx = fsm_indicator_mad_tx = Signal(2)
        fsm.act("IDLE",
            fsm_indicator_mad_tx.eq(0),
            If(packetizer.source.valid,
                NextValue(counter, 0),
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            fsm_indicator_mad_tx.eq(1),
            packetizer.source.connect(source, keep={"valid", "ready", "data"}),
            If(source.valid & source.ready,
                NextValue(counter, counter + 1),
                If(packetizer.source.last,
                    NextState("PAD"),
                    reply_pipe.source.ready.eq(1),
                    If(counter == MAD_PMTU - 1,
                        NextState("IDLE")
                    )
                )
            )
        )
        # MAD packets are always the size of MAD_PMTU
        fsm.act("PAD",
            fsm_indicator_mad_tx.eq(2),

            source.valid.eq(1),
            source.last.eq(counter == MAD_PMTU - 1),
            If(source.valid & source.ready,
                NextValue(counter, counter + 1),
                If(source.last,
                    NextState("IDLE")
                )
            )
        )

class MAD_response:
    class MAD_Status(IntFlag):
        Busy                             = 1 << 0
        RedirectionRequired              = 1 << 1
        NoInvalidFields                  = 0 << 2
        BadVersion                       = 1 << 2
        MethodNotSupported               = 2 << 2
        MethodAttributeComboNotSupported = 3 << 2
        AttributeInvalidValue            = 7 << 2

    @staticmethod
    def emit(transactionID, attribID, BaseVersion, MgmtClass, ClassVersion, reply_sink, status=0):
        return [
            reply_sink.valid.eq(1),
            reply_sink.last.eq(1),

            reply_sink.BaseVersion.eq(BaseVersion),
            reply_sink.MgmtClass.eq(MgmtClass),
            reply_sink.ClassVersion.eq(ClassVersion),
            reply_sink.Status.eq(status),
            reply_sink.TransactionID.eq(transactionID),
            reply_sink.AttributeID.eq(attribID),
        ]

class _CM_response(MAD_response):
    @staticmethod
    def emit(attribID, receive_source, reply_sink, status=0):
        return super(__class__, __class__).emit(
            transactionID = receive_source.TransactionID,
            attribID      = attribID,
            BaseVersion   = 0x01,
            MgmtClass     = 0x07,
            ClassVersion  = 0x02,
            reply_sink    = reply_sink,
            status        = status) + \
        [
            If(attribID == MAD_ATTRIB_ID.ClassPortInfo,
                reply_sink.Method.eq(CM_Methods.ComMgtGetResp)
            ).Else(
                reply_sink.Method.eq(CM_Methods.ComMgtSend)
            )
        ]

class CPI(_CM_response):
    class CapabilityMask(IntFlag):
        IsReliableConnectionCapable   = 1 << 9
        IsReliableDatagramCapable     = 1 << 10
        IsUnreliableConnectionCapable = 1 << 12
        IsSIDRCapable                 = 1 << 13

    @staticmethod
    def emit(receive_source, reply_sink):
        return super(__class__, __class__).emit(
            attribID       = MAD_ATTRIB_ID.ClassPortInfo,
            receive_source = receive_source,
            reply_sink     = reply_sink
        ) + [
            reply_sink.CapabilityMask.eq(CPI.CapabilityMask.IsReliableConnectionCapable)
        ]

class REP(_CM_response):
    @staticmethod
    def emit(qp, receive_source, reply_sink):
        return super(__class__, __class__).emit(
            attribID       = MAD_ATTRIB_ID.ConnectReply,
            receive_source = receive_source,
            reply_sink     = reply_sink
        ) + [
            reply_sink.Local_Communication_ID.eq(LOCAL_COMMUNICATION_ID),
            reply_sink.Remote_Communication_ID.eq(receive_source.Local_Communication_ID),
            reply_sink.Responder_Resources.eq(RESPONDER_RESOURCES),
            reply_sink.Initiator_Depth.eq(0x00),
            reply_sink.Target_ACK_Delay.eq(0x0f),
            reply_sink.Local_QPN.eq(qp.id),
            reply_sink.Starting_PSN.eq(qp.receive_queue.psn),
            reply_sink.RNR_Retry_Count.eq(7)
        ]

class REJ(_CM_response):
    class REASON(IntEnum):
        No_QP_available                    = 1
        No_EEC_available                   = 2
        No_resources_available             = 3
        Timeout                            = 4
        Unsupported_request                = 5
        Invalid_Communication_ID           = 6
        Invalid_Communication_Instance     = 7
        Invalid_Service_ID                 = 8
        Invalid_Transport_Service_Type     = 9
        Stale_connection                   = 10
        RDC_does_not_exist                 = 11
        Primary_Remote_Port_GID_rejected   = 12
        Primary_Remote_Port_LID_rejected   = 13
        Invalid_Primary_SL                 = 14
        Invalid_Primary_Traffic_Class      = 15
        Invalid_Primary_Hop_Limit          = 16
        Invalid_Primary_Packet_Rate        = 17
        Alternate_Remote_Port_GID_rejected = 18
        Alternate_Remote_Port_LID_rejected = 19
        Invalid_Alternate_SL               = 20
        Invalid_Alternate_Traffic_Class    = 21
        Invalid_Alternate_Hop_Limit        = 22
        Invalid_Alternate_Packet_Rate      = 23
        Port_and_CM_Redirection            = 24
        Port_Redirection                   = 25
        Invalid_Path_MTU                   = 26
        Insufficient_Responder_Resources   = 27
        Consumer_Reject                    = 28
        RNR_Retry_Count_Reject             = 29
        Duplicate_Local_Communication_ID   = 30
        Unsupported_Class_Version          = 31
        Invalid_Primary_Flow_Label         = 32
        Invalid_Alternate_Flow_Label       = 33

    @staticmethod
    def emit(reason, receive_source, reply_sink, status=0):
        return super(__class__, __class__).emit(
            attribID       = MAD_ATTRIB_ID.ConnectReply,
            receive_source = receive_source,
            reply_sink     = reply_sink,
            status         = status
        ) + [
            reply_sink.Message_REJected.eq(0x0),
            reply_sink.Reject_Info_Length.eq(0x0),
            reply_sink.Reason.eq(reason)
        ]

class DREP(_CM_response):
    @staticmethod
    def emit(receive_source, reply_sink):
        return super(__class__, __class__).emit(
            attribID       = MAD_ATTRIB_ID.DisconnectReply,
            receive_source = receive_source,
            reply_sink     = reply_sink
        ) + [
            reply_sink.Local_Communication_ID.eq(LOCAL_COMMUNICATION_ID),
            reply_sink.Remote_Communication_ID.eq(receive_source.Local_Communication_ID)
        ]

class DREQ(_CM_response):
    @staticmethod
    def emit(remote_qpn, receive_source, reply_sink):
        return super(__class__, __class__).emit(
            attribID       = MAD_ATTRIB_ID.DisconnectRequest,
            receive_source = receive_source,
            reply_sink     = reply_sink
        ) + [
            reply_sink.Local_Communication_ID.eq(LOCAL_COMMUNICATION_ID),
            reply_sink.Remote_Communication_ID.eq(receive_source.Local_Communication_ID),
            reply_sink.Remote_QPN_EECN.eq(remote_qpn)
        ]

class CMWaitTimer(Module):
    def __init__(self, clk_freq):
        self.wait = Signal()
        self.done = Signal()
        self.pow = Signal(5)

        # # #

        # Cast t to int.
        cnt_dict = {
            Constant(i, len(self.pow)):
                int(4.096e-6*(2**i)*clk_freq)
            for i in range(1 << len(self.pow))
        }
        rst_count = Signal(bits_for(max(cnt_dict.values())))
        count = Signal(reset_less=True).like(rst_count)

        self.comb += Case(self.pow, {i: rst_count.eq(v) for i, v in cnt_dict.items()})
        self.comb += self.done.eq(count == 0)
        self.sync += [
            If(self.wait,
                If(~self.done,
                    count.eq(count - 1)
                )
            ).Else(
                count.eq(rst_count)
            )
        ]

# See page 698 of doc for reference
class CMStateFSM(LiteXModule):
    def __init__(self, qp, cm_conn, reply_sink, clk_freq, dw=8):
        self.sink = sink = Endpoint(eth_mad_description(dw))

        self.new_request = new_request = Signal()
        self.qp_rcv_wire = qp_rcv_wire = Signal()

        # # #

        cm_timer = CMWaitTimer(clk_freq)
        self.submodules += cm_timer

        local_cm_timeout = Signal(5, reset_less=True)

        # CM FSM
        self.fsm = fsm = FSM(reset_state="LISTEN")
        fsm.act("LISTEN",
            If(new_request & (sink.AttributeID == MAD_ATTRIB_ID.ConnectRequest),
                NextValue(local_cm_timeout, sink.Local_CM_Response_Timeout),
                NextValue(qp.qp_state, LiteEthIBQP.INIT),
                NextState("REQ Rcvd")
            )
        )

        fsm.act("REQ Rcvd",
            If((qp.qp_state == LiteEthIBQP.INIT) | (qp.qp_state == LiteEthIBQP.ERROR),
                REP.emit(qp, sink, reply_sink),
                NextValue(qp.qp_state, LiteEthIBQP.RTR),
                cm_timer.pow.eq(local_cm_timeout),
                NextState("REP Sent")
            ).Else(
                REJ.emit(REJ.REASON.No_QP_available, sink, reply_sink),
                NextState("REJ Sent")
            )
        )

        fsm.act("REJ Sent",
            NextValue(qp.qp_state, LiteEthIBQP.ERROR),
            NextState("ERROR")
        )

        fsm.act("REP Sent",
            cm_timer.wait.eq(1),
            If((new_request & (sink.AttributeID == MAD_ATTRIB_ID.ReadyToUse)) | qp_rcv_wire,
                NextValue(qp.qp_state, LiteEthIBQP.RTS),
                NextState("ESTABLISHED")
            ).Elif(new_request & (sink.AttributeID == MAD_ATTRIB_ID.MsgRcptAck),
                cm_timer.pow.eq(sink.ServiceTimeout),
                NextState("MRA(REP) Rcvd")
            ).Elif(new_request & (sink.AttributeID == MAD_ATTRIB_ID.ConnectReject),
                NextValue(qp.qp_state, LiteEthIBQP.ERROR),
                NextState("ERROR")
            ).Elif(cm_timer.done,
                NextState("RTU Timeout")
            ).Elif(new_request & (sink.AttributeID == MAD_ATTRIB_ID.ConnectRequest),
                REP.emit(qp, sink, reply_sink)
            )
        )

        fsm.act("MRA(REP) Rcvd",
            cm_timer.wait.eq(1),
            If((new_request & (sink.AttributeID == MAD_ATTRIB_ID.ReadyToUse)) | qp_rcv_wire,
                NextValue(qp.qp_state, LiteEthIBQP.RTS),
                NextState("ESTABLISHED")
            ).Elif(cm_timer.done,
                NextState("RTU Timeout")
            ).Elif(new_request & (sink.AttributeID == MAD_ATTRIB_ID.ConnectReject),
                NextValue(qp.qp_state, LiteEthIBQP.ERROR),
                NextState("ERROR")
            )
        )

        fsm.act("ESTABLISHED",
            If(new_request & (sink.AttributeID == MAD_ATTRIB_ID.DisconnectRequest) &
              (sink.Local_Communication_ID == cm_conn.Remote_Communication_ID) &
              (sink.Remote_Communication_ID == LOCAL_COMMUNICATION_ID) &
              (sink.Remote_QPN_EECN == cm_conn.qpn),
                NextValue(qp.qp_state, LiteEthIBQP.ERROR),
                NextState("DREQ Rcvd")
            ).Elif(new_request &
                 ((sink.AttributeID == MAD_ATTRIB_ID.ConnectReply) |
                  (sink.AttributeID == MAD_ATTRIB_ID.ConnectRequest)),
                If(sink.Local_QPN == qp.other_id,
                    REJ.emit(REJ.REASON.Stale_connection, sink, reply_sink),
                    NextState("Send DREQ")
                )
            )
        )

        fsm.act("DREQ Rcvd",
            DREP.emit(sink, reply_sink),
            cm_timer.pow.eq(local_cm_timeout),
            NextState("TimeWait")
        )

        # TODO Warning: Timers look at queue time not at send time
        fsm.act("DREQ Sent",
            cm_timer.wait.eq(1),
            If(cm_timer.done,
                NextState("DREP Timeout")
            ).Elif(new_request & (sink.AttributeID == MAD_ATTRIB_ID.DisconnectRequest) &
                  (sink.Local_Communication_ID == cm_conn.Remote_Communication_ID) &
                  (sink.Remote_Communication_ID == LOCAL_COMMUNICATION_ID) &
                  (sink.Remote_QPN_EECN == cm_conn.qpn),
                NextState("DREQ Rcvd")
            ).Elif(new_request & (sink.AttributeID == MAD_ATTRIB_ID.DisconnectReply),
                cm_timer.pow.eq(local_cm_timeout),
                NextState("TimeWait")
            )
        )

        fsm.act("RTU Timeout",
            REJ.emit(REJ.REASON.Timeout, sink, reply_sink),
            cm_timer.pow.eq(local_cm_timeout),
            NextValue(qp.qp_state, LiteEthIBQP.ERROR),
            NextState("TimeWait")
        )

        fsm.act("TimeWait",
            cm_timer.wait.eq(1),
            If(new_request & (sink.AttributeID == MAD_ATTRIB_ID.DisconnectRequest) &
              (sink.Local_Communication_ID == cm_conn.Remote_Communication_ID) &
              (sink.Remote_Communication_ID == LOCAL_COMMUNICATION_ID) &
              (sink.Remote_QPN_EECN == cm_conn.qpn),
                DREP.emit(sink, reply_sink)
            ),
            If(cm_timer.done,
                NextState("ERROR")
            )
        )

        fsm.act("DREP Timeout",
            DREQ.emit(qp.other_id, sink, reply_sink),
            cm_timer.pow.eq(local_cm_timeout),
            NextState("TimeWait")
        )

        # State added for convenience of treating the connection established and stale qp case (REQ Received)
        fsm.act("Send DREQ",
            DREQ.emit(qp.other_id, sink, reply_sink),
            cm_timer.pow.eq(local_cm_timeout),
            NextState("DREQ Sent")
        )

        fsm.act("ERROR",
            NextValue(qp.qp_state, LiteEthIBQP.ERROR),
            NextState("RESET")
        )

        fsm.act("RESET",
            NextValue(qp.qp_state, LiteEthIBQP.RESET),
            NextState("LISTEN")
        )


class LiteEthIBMADRX(LiteXModule):
    def __init__(self, qps, cm_conn, reply_sink, clk_freq, buffered_in=True, dw=8):
        self.sink   = sink   = Endpoint(eth_rocev2_user_description(dw))
        self.qp_rcv_wire = Signal()

        # # #

        if buffered_in:
            buff_in = Buffer(eth_rocev2_user_description(dw), pipe_ready=True)
            self.submodules += buff_in
            self.comb += sink.connect(buff_in.sink)
            sink = buff_in.source

        # Depacketizer.
        self.depacketizer = depacketizer = LiteEthCMDepacketizer(dw=dw)
        self.comb += sink.connect(depacketizer.sink)

        self.reply_pipe = reply_pipe = SyncFIFO(eth_mad_description(dw), 3, buffered=True)
        self.comb += reply_pipe.source.connect(reply_sink)
        reply_sink = reply_pipe.sink


        # CM state controller
        cm_state = CMStateFSM(qps[1], cm_conn, reply_sink, clk_freq, dw=dw)
        self.submodules += cm_state

        self.comb += depacketizer.source.connect(cm_state.sink, omit={"ready", "header_only"})
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

        # MAD RX FSM
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(depacketizer.source.valid,
                NextState("DROP"),
                If(valid_base_version & valid_class_version & valid_meth & valid_methattrcombo & valid_conn_type,
                    cm_state.new_request.eq(1),
                    NextState("RECEIVE")
                ).Else(
                    If(depacketizer.source.AttributeID == MAD_ATTRIB_ID.ConnectRequest,
                        If(~valid_base_version,
                            REJ.emit(REJ.REASON.Unsupported_request, depacketizer.source, reply_sink, status=MAD_response.MAD_Status.BadVersion)
                        ).Elif(~valid_class_version,
                            REJ.emit(REJ.REASON.Unsupported_Class_Version, depacketizer.source, reply_sink, status=MAD_response.MAD_Status.BadVersion)
                        ).Elif(~valid_meth,
                            REJ.emit(REJ.REASON.Unsupported_request, depacketizer.source, reply_sink, status=MAD_response.MAD_Status.MethodNotSupported)
                        ).Elif(~valid_methattrcombo,
                            REJ.emit(REJ.REASON.Unsupported_request, depacketizer.source, reply_sink, status=MAD_response.MAD_Status.MethodAttributeComboNotSupported)
                        ).Elif(~valid_pmtu,
                            REJ.emit(REJ.REASON.Invalid_Path_MTU, depacketizer.source, reply_sink, status=MAD_response.MAD_Status.AttributeInvalidValue)
                        ).Elif(~valid_initiator_depth,
                            REJ.emit(REJ.REASON.Insufficient_Responder_Resources, depacketizer.source, reply_sink, status=MAD_response.MAD_Status.AttributeInvalidValue)
                        )
                    )
                )
            )
        )

        fsm.act("RECEIVE",
            depacketizer.source.ready.eq(1),
            Case(depacketizer.source.AttributeID, {
                MAD_ATTRIB_ID.ClassPortInfo: [
                    CPI.emit(depacketizer.source, reply_sink)
                ],
                MAD_ATTRIB_ID.ConnectRequest: [
                    If((qps[1].qp_state == LiteEthIBQP.INIT) | (qps[1].qp_state == LiteEthIBQP.ERROR),
                        NextValue(qps[1].other_id, depacketizer.source.Local_QPN),
                        NextValue(qps[1].send_queue.psn, depacketizer.source.Starting_PSN),
                        NextValue(qps[1].ip_address, depacketizer.source.Primary_Local_Port_GID[:32]),

                        NextValue(cm_conn.qpn, qps[1].id),
                        NextValue(cm_conn.Remote_Communication_ID, depacketizer.source.Local_Communication_ID)
                    )
                ]
            }),
            NextState("DROP"), # Discard Private data
            If(depacketizer.source.valid & depacketizer.source.ready & depacketizer.source.last,
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
        self.tx = tx = LiteEthIBMADTX(qps, buffered_out=True)
        self.rx = rx = LiteEthIBMADRX(qps, cm_conn, tx.reply_sink, clk_freq, buffered_in=True)
