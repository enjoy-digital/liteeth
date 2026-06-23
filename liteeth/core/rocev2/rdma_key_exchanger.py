from migen import *

from litex.gen import *

from litex.soc.interconnect.stream import Endpoint, EndpointDescription
from liteeth.packet import Depacketizer, Header, HeaderField

from liteeth.common import WC

class KeyDecoder(LiteXModule):
    def __init__(self, memr_num, dw):
        self.sink   = sink   = Endpoint([("data", dw)])
        self.source = source = Endpoint([("r_key", 32), ("va", 64), ("key_cnt", log2_int(memr_num))])

        # # #

        ratio = 96 // dw

        memr_cnt = Signal(max=memr_num)
        buff = Signal(96)
        buff_prev = Signal(96)
        cnt  = Signal(max=ratio)

        self.comb += [
            buff.eq(Cat(sink.data, buff_prev[:-dw])),
            source.r_key.eq(buff[64:96]),
            source.va.eq(buff[0:64]),
            source.key_cnt.eq(memr_cnt),
            source.valid.eq(cnt == ratio-1),
            sink.ready.eq(~source.valid | source.ready),
            source.last.eq(memr_cnt == memr_num - 1)
        ]

        self.sync += [
            If(sink.valid & (~source.valid | source.ready),
                buff_prev.eq(buff),
                If(cnt == ratio - 1,
                    cnt.eq(0)
                ).Else(
                    cnt.eq(cnt + 1)
                )
            ),
            memr_cnt.eq(memr_cnt + (source.valid & source.ready))
        ]


class LiteEthRDMAKeyExchanger(LiteXModule):
    def __init__(self, qp, cq, keymem, memr_num, dw):
        self.source = Endpoint([("r_key", 32), ("va", 64), ("key_cnt", log2_int(memr_num))])
        self.request = request = Signal()

        # # #

        key_dec = KeyDecoder(memr_num, dw)
        self.submodules.key_dec = key_dec

        port = keymem.get_port()
        self.specials += port

        self.fsm = fsm = FSM()
        fsm.act("IDLE",
            If(request,
                NextState("EXPECT_SEND")
            )
        )

        fsm.act("EXPECT_SEND",
            qp.receive_queue.sink.valid.eq(1),
            qp.receive_queue.sink.va.eq(0),
            qp.receive_queue.sink.dma_len.eq(96),
            qp.receive_queue.sink.l_key.eq(0xdeaded),
            If(qp.receive_queue.sink.ready,
                NextState("WAIT")
            )
        )

        fsm.act("WAIT",
            If(cq.source.valid &
               (cq.source.status == WC.Status.SUCCESS) &
               (cq.source.opcode == WC.Opcode.RECV),
                cq.source.ready.eq(1),
                NextState("DECODE")
            )
        )

        r_size = 2**8
        r_dw = len(port.dat_r)
        ratio_r_port = r_dw//dw

        r_addr = Signal(log2_int(r_size//(r_dw//8)))
        r_addr_next = Signal().like(r_addr)
        r_cnt = Signal(log2_int(ratio_r_port))
        r_buff = Signal(r_dw)

        fsm.act("DECODE",
            key_dec.sink.valid.eq(1),
            Case(r_cnt, {
                i: key_dec.sink.data.eq(r_buff[i*dw:(i+1)*dw])
                for i in range(ratio_r_port)
            }),
            port.adr[:log2_int(r_size//(r_dw//8))].eq(r_addr_next),
            r_buff.eq(port.dat_r),
            r_addr_next.eq(r_addr + (key_dec.sink.ready & (r_cnt == ratio_r_port - 1))),
            If(key_dec.source.last & key_dec.source.valid & key_dec.source.ready,
                NextValue(r_addr, 0),
                NextValue(r_cnt, 0),
                NextState("IDLE")
            ).Elif(key_dec.sink.ready,
                NextValue(r_addr, r_addr_next),
                NextValue(r_cnt, r_cnt + 1)
            ),
            key_dec.source.connect(self.source)
        )


