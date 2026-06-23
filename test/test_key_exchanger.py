import unittest
from litex.gen import *

from litex.soc.interconnect.stream import Endpoint
from liteeth.core.rocev2.rdma_key_exchanger import KeyDecoder

from litex.gen.genlib.misc import WaitTimer

class StrippedKeyExchanger(LiteXModule):
    def __init__(self, keymem, memr_num, dw):
        self.request = Signal()
        self.source = Endpoint([("r_key", 32), ("va", 64), ("key_cnt", log2_int(memr_num))])

        # # #

        key_dec = KeyDecoder(memr_num, dw)
        self.submodules.key_dec = key_dec

        port = keymem.get_port()
        self.specials += port

        r_size = 2**8
        r_dw = len(port.dat_r)
        ratio_r_port = r_dw//dw

        r_addr = Signal(log2_int(r_size//(r_dw//8)))
        r_addr_next = Signal().like(r_addr)
        r_cnt = Signal(log2_int(ratio_r_port))
        r_buff = Signal(r_dw)

        self.fsm = fsm = FSM()
        fsm.act("WAIT",
            If(self.request,
                NextState("WORK")
            )
        )

        fsm.act("WORK",
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
                NextValue(r_cnt, 0)
            ).Elif(key_dec.sink.ready,
                NextValue(r_addr, r_addr_next),
                NextValue(r_cnt, r_cnt + 1)
            )
        )

        self.comb += key_dec.source.connect(self.source),

keys = "001122330011223344556677001122330011223344556677001122330011223344556677001122330011223344556677001122330011223344556677001122330011223344556677001122330011223344556677001122330011223344556677"

class DUT(LiteXModule):
    def __init__(self, memr_num, dw):
        self.done = Signal()

        keymem_size = 2**log2_int(memr_num * 12 + 1, need_pow2=False)
        keymem = Memory(16, keymem_size // 2, init=[int(keys[4*i+2:4*i+4] + keys[4*i:4*i+2], 16) for i in range(len(keys) // 4)])
        self.specials += keymem

        key_exchanger = StrippedKeyExchanger(keymem, memr_num, dw)
        self.submodules.key_exchanger = key_exchanger

        timer = WaitTimer(100)
        self.submodules.timer = timer

        r_keys = Array([Signal(32) for _ in range(memr_num)])
        vas    = Array([Signal(64) for _ in range(memr_num)])

        self.fsm = fsm = FSM()
        fsm.act("WAIT",
            timer.wait.eq(1),
            If(timer.done,
                key_exchanger.request.eq(1),
                NextState("WAIT_KEYS")
            )
        )

        fsm.act("WAIT_KEYS",
            If(self.key_exchanger.source.valid,
                NextState("R_KEYS")
            )
        )

        fsm.act("R_KEYS",
            key_exchanger.source.ready.eq(1),
            If(key_exchanger.source.valid,
                NextValue(r_keys[key_exchanger.source.key_cnt], key_exchanger.source.r_key),
                NextValue(vas[key_exchanger.source.key_cnt], key_exchanger.source.va),
                If(key_exchanger.source.last,
                    NextState("STOP")
                )
            )
        )

        fsm.act("STOP",
            self.done.eq(1)
        )

def simulate(dut):
    while not (yield dut.done):
        yield

class TestKeyExchanger(unittest.TestCase):
    dw = 8
    def test_exchange(self):
        dut = DUT(8, self.dw)

        run_simulation(dut, simulate(dut), vcd_name="test.vcd")

if __name__ == "__main__":
    unittest.main()
