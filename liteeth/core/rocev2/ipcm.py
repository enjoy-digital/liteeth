from liteeth.common import *

from litex.soc.interconnect.stream import Endpoint

from liteeth.packet import Depacketizer

class LiteEthIPCMDepacketizer(Depacketizer):
    def __init__(self, dw=8):
        Depacketizer.__init__(self,
            eth_mad_description(dw),
            eth_ipcm_description(dw),
            ipcm_header
        )

# TODO : Add handling for ServiceID
class LiteEthIPCM(LiteXModule):
    def __init__(self, mad_rx, dw=8):
        self.validate_sink = validate_sink = Endpoint([("validate", 1)])
        self.source = source = Endpoint(EndpointDescription([("data", dw)], [("AttributeID", 16)]))

        # # #

        self.ipcm_depacketizer = ipcm_depacketizer = LiteEthIPCMDepacketizer(dw=dw)

        # Data-path
        self.comb += [
            If(mad_rx.source.AttributeID == MAD_ATTRIB_ID.ConnectRequest,
                mad_rx.source.connect(ipcm_depacketizer.sink, omit={"AttributeID"}),
                ipcm_depacketizer.source.connect(source, keep={"valid", "ready", "last", "data"}),
                source.AttributeID.eq(MAD_ATTRIB_ID.ConnectRequest)
            ).Else(
                mad_rx.source.connect(source)
            )
        ]

        self.comb += [
            validate_sink.connect(mad_rx.validate_sink)
        ]
