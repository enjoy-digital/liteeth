from liteeth.common import *

from enum import IntFlag

class MAD_response:
    class MAD_Status(IntEnum):
        Busy                             = 1 << 0
        RedirectionRequired              = 1 << 1

        NoInvalidFields                  = 0 << 2
        BadVersion                       = 1 << 2
        MethodNotSupported               = 2 << 2
        MethodAttributeComboNotSupported = 3 << 2
        AttributeInvalidValue            = 7 << 2

    @classmethod
    def emit(cls, attribID, BaseVersion, MgmtClass, ClassVersion, reply_sink, cm_ctx, status=0):
        return [
            reply_sink.valid.eq(1),
            reply_sink.last.eq(1),

            reply_sink.BaseVersion.eq(BaseVersion),
            reply_sink.MgmtClass.eq(MgmtClass),
            reply_sink.ClassVersion.eq(ClassVersion),
            reply_sink.Status.eq(status),
            reply_sink.TransactionID.eq(cm_ctx.TransactionID),
            reply_sink.AttributeID.eq(attribID),
        ]

class _CM_response:
    BASE_VERSION  = 0x01
    MGMT_CLASS    = 0x07
    CLASS_VERSION = 0x02

    cm_ctx     = None
    reply_sink = None
    @classmethod
    def set_attrs(cls, cm_ctx, reply_sink):
        cls.cm_ctx     = cm_ctx
        cls.reply_sink = reply_sink

    @classmethod
    def emit(cls, attribID, status=0):
        return MAD_response.emit(
            attribID     = attribID,
            BaseVersion  = cls.BASE_VERSION,
            MgmtClass    = cls.MGMT_CLASS,
            ClassVersion = cls.CLASS_VERSION,
            reply_sink   = cls.reply_sink,
            cm_ctx       = cls.cm_ctx,
            status       = status) + \
        [
            cls.reply_sink.Method.eq(CM_Methods.ComMgtGetResp) \
            if attribID == MAD_ATTRIB_ID.ClassPortInfo \
            else cls.reply_sink.Method.eq(CM_Methods.ComMgtSend)
        ]

class CPI:
    class CapabilityMask(IntFlag):
        IsReliableConnectionCapable   = 1 << 9
        IsReliableDatagramCapable     = 1 << 10
        IsUnreliableConnectionCapable = 1 << 12
        IsSIDRCapable                 = 1 << 13

    @classmethod
    def emit(cls):
        return _CM_response.emit(MAD_ATTRIB_ID.ClassPortInfo) + [
            _CM_response.reply_sink.CapabilityMask.eq(
                CPI.CapabilityMask.IsReliableConnectionCapable
            )
        ]

class REP:
    @classmethod
    def emit(cls, qp):
        return _CM_response.emit(MAD_ATTRIB_ID.ConnectReply) + [
            _CM_response.reply_sink.Local_Communication_ID.eq(LOCAL_COMMUNICATION_ID),
            _CM_response.reply_sink.Remote_Communication_ID.eq(_CM_response.cm_ctx.Remote_Communication_ID),
            _CM_response.reply_sink.Responder_Resources.eq(RESPONDER_RESOURCES),
            _CM_response.reply_sink.Initiator_Depth.eq(INITIATOR_DEPTH),
            _CM_response.reply_sink.Target_ACK_Delay.eq(0x01),
            _CM_response.reply_sink.Local_QPN.eq(qp.id),
            _CM_response.reply_sink.Starting_PSN.eq(qp.receive_queue.psn),
            _CM_response.reply_sink.RNR_Retry_Count.eq(7)
        ]

class REJ:
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

    @classmethod
    def emit(cls, reason, status=0):
        return _CM_response.emit(
            attribID   = MAD_ATTRIB_ID.ConnectReject,
            status     = status
        ) + [
            _CM_response.reply_sink.Local_Communication_ID.eq(LOCAL_COMMUNICATION_ID),
            _CM_response.reply_sink.Remote_Communication_ID.eq(_CM_response.cm_ctx.Remote_Communication_ID),
            _CM_response.reply_sink.Message_REJected.eq(0x0),
            _CM_response.reply_sink.Reject_Info_Length.eq(0x0),
            _CM_response.reply_sink.Reason.eq(reason)
        ]

class DREP:
    @classmethod
    def emit(cls):
        return _CM_response.emit(MAD_ATTRIB_ID.DisconnectReply) + [
            _CM_response.reply_sink.Local_Communication_ID.eq(LOCAL_COMMUNICATION_ID),
            _CM_response.reply_sink.Remote_Communication_ID.eq(_CM_response.cm_ctx.Remote_Communication_ID)
        ]

class DREQ:
    @classmethod
    def emit(cls, remote_qpn):
        return _CM_response.emit(MAD_ATTRIB_ID.DisconnectRequest) + [
            _CM_response.reply_sink.Local_Communication_ID.eq(LOCAL_COMMUNICATION_ID),
            _CM_response.reply_sink.Remote_Communication_ID.eq(_CM_response.cm_ctx.Remote_Communication_ID),
            _CM_response.reply_sink.Remote_QPN_EECN.eq(remote_qpn)
        ]
