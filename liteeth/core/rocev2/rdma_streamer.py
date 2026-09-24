from migen import *

from litex.gen import *

from litex.soc.interconnect.stream import Endpoint, SyncFIFO

from liteeth.common import WR_OPCODE, WC, eth_rocev2_send_wr_description, INITIATOR_DEPTH, MAX_OUTSTANDING
from litex.soc.interconnect.stream import StrideConverter

class LiteEthRDMAStreamerWriter(LiteXModule):
    def __init__(self, port, l_key, mem_num, size, dw):
        self.sink  = sink  = Endpoint([("data", dw)])
        self.acked = acked = Signal()
        self.qp_sink = qp_sink = Endpoint(eth_rocev2_send_wr_description())

        self.key_sink = key_sink = Endpoint([("r_key", 32), ("va", 64)])

        self.loss_cnt = Signal(24)

        # # #

        word_size = size//(dw//8)

        self.key_fifo = key_fifo = SyncFIFO([("r_key", 32), ("va", 64)], mem_num + 1)
        self.comb += [
            If(key_sink.valid,
                key_sink.connect(key_fifo.sink)
            )
        ]

        # Write buffs (data sent)
        # Buffers on host that do not have an associated pending write
        available_buffs = Signal(max=mem_num + 1, reset=mem_num)
        available_buffs_inc = Signal()
        available_buffs_dec = Signal()

        addr = Signal(log2_int(word_size))
        addr_next = Signal().like(addr)
        last_word = Signal()

        self.comb += sink.connect(port.sink)

        # Helper signals
        self.comb += [
            addr_next.eq(addr + 1),
            last_word.eq(addr_next == 0)
        ]

        self.sync += [
            If(port.sink.valid & port.sink.ready,
                addr.eq(addr_next),
            ).Elif(sink.valid,
                self.loss_cnt.eq(self.loss_cnt + 1)
            )
        ]

        # Buffer availability logic
        self.comb += [
            If(last_word & port.sink.valid & port.sink.ready,
                key_fifo.source.connect(key_fifo.sink),
                available_buffs_dec.eq(1)
            ),
            If(acked,
                available_buffs_inc.eq(1)
            )
        ]
        self.sync += available_buffs.eq(available_buffs + available_buffs_inc - available_buffs_dec),

        self.load_fsm = load_fsm = FSM()
        load_fsm.act("SEND",
            If((available_buffs != 0) & (sink.valid | ~port.sink.ready),
                qp_sink.valid.eq(1),
                If(qp_sink.ready,
                    NextState("WAIT")
                )
            )
        )

        load_fsm.act("WAIT",
            If(port.sink.valid & port.sink.ready,
                If(last_word,
                    NextState("SEND")
                )
            )
        )

        # QP signals (other than valid)
        self.comb += [
            qp_sink.wr_opcode.eq(WR_OPCODE.RDMA_WRITE),
            qp_sink.ack_req.eq(1),
            qp_sink.w_immdt.eq(0),
            qp_sink.va.eq(key_fifo.source.va),
            qp_sink.dma_len.eq(0),
            qp_sink.l_key.eq(l_key),
            qp_sink.r_key.eq(key_fifo.source.r_key),
        ]

@ResetInserter()
class LiteEthRDMAStreamerReader(LiteXModule):
    def __init__(self, port, l_key, mem_num, size, i_dw, o_dw):
        self.acked = acked = Signal()
        self.source  = source  = Endpoint([("data", o_dw)])
        self.qp_sink = qp_sink = Endpoint(eth_rocev2_send_wr_description())

        self.key_sink = key_sink = Endpoint([("r_key", 32), ("va", 64)])

        self.enable = enable = Signal()

        # # #

        self.conv      = conv      = StrideConverter([("data", i_dw)], [("data", o_dw)], reverse=True)
        self.conv_fifo = conv_fifo = SyncFIFO([("data", o_dw)], 64)

        word_size = size//(i_dw//8)

        self.key_fifo = key_fifo = SyncFIFO([("r_key", 32), ("va", 64)], mem_num + 1)
        self.comb += [
            If(key_sink.valid,
                key_sink.connect(key_fifo.sink)
            )
        ]

        # Read buffs (data received)
        # Buffers on host that do not have an associated pending read
        available_buffs = Signal(max=mem_num + 1, reset=mem_num)
        available_buffs_inc = Signal()
        available_buffs_dec = Signal()

        readable_buffs = Signal(max=mem_num + 1)
        readable_buffs_inc = Signal()
        readable_buffs_dec = Signal()

        addr = Signal(log2_int(word_size))
        addr_next = Signal().like(addr)
        last_word = Signal()

        self.comb += [
            port.source.connect(conv.sink, omit={"valid", "ready"}),
            conv.sink.valid.eq(enable & port.source.valid),
            port.source.ready.eq(enable & conv.sink.ready),

            conv.source.connect(conv_fifo.sink),
            conv_fifo.source.connect(source)
        ]

        # Helper signals
        self.comb += [
            addr_next.eq(addr + 1),
            last_word.eq(addr_next == 0)
        ]

        # Transition logic
        self.sync += [
            If(port.source.valid & port.source.ready,
                addr.eq(addr_next)
            )
        ]

        from litex.gen.genlib.misc import WaitTimer
        self.timer = timer = WaitTimer(size)

        notify = Signal()
        self.fsm = fsm = FSM()
        fsm.act("SEND_READ",
            If(enable,
                qp_sink.valid.eq(1),
                If(qp_sink.ready,
                    key_fifo.source.connect(key_fifo.sink),
                    NextState("SEND_WRITE")
                )
            ),
        )

        fsm.act("SEND_WRITE",
            notify.eq(1),
            qp_sink.valid.eq(1),
            If(qp_sink.ready,
                available_buffs_dec.eq(1),
                NextState("WAIT")
            )
        )

        fsm.act("WAIT",
            timer.wait.eq(1),
            If(timer.done & (available_buffs != 0),
                NextState("SEND_READ")
            )
        )

        # Buffer availability logic
        self.comb += [
            readable_buffs_inc.eq(acked),
            If(last_word & port.source.valid & port.source.ready,
                readable_buffs_dec.eq(1),
                available_buffs_inc.eq(1)
            )
        ]
        self.sync += [
            readable_buffs.eq(readable_buffs + readable_buffs_inc - readable_buffs_dec),
            available_buffs.eq(available_buffs + available_buffs_inc - available_buffs_dec)
        ]

        # Send queue signals (except valid)
        self.comb += [
            If(notify,
                qp_sink.wr_opcode.eq(WR_OPCODE.RDMA_WRITE),
                qp_sink.dma_len.eq(0),
                qp_sink.w_immdt.eq(1),
                qp_sink.immdt.eq(0),
            ).Else(
                qp_sink.wr_opcode.eq(WR_OPCODE.RDMA_READ),
                qp_sink.dma_len.eq(size),
                qp_sink.w_immdt.eq(0)
            ),
            qp_sink.ack_req.eq(1),
            qp_sink.va.eq(key_fifo.source.va),
            qp_sink.l_key.eq(l_key),
            qp_sink.r_key.eq(key_fifo.source.r_key)
        ]

@ResetInserter()
class LiteEthRDMAStreamer(LiteXModule):
    def __init__(self, qp, cq, wmem, rmem, wmem_l_key, rmem_l_key, mem_num, dw):
        self.sink   = sink   = Endpoint([("data", dw)])
        self.source = source = Endpoint([("data", dw)])

        self.key_sink = key_sink = Endpoint([("r_key", 32), ("va", 64)])

        self.enable = enable = Signal()

        # # #

        # Check if we can send enough reads and writes
        assert mem_num // 2 <= INITIATOR_DEPTH
        assert mem_num // 2 <= MAX_OUTSTANDING - INITIATOR_DEPTH

        size = 2048

        # Parameters (extraction and validation)
        read_r_port = rmem.read_port
        r_dw = read_r_port.data_width

        # assert r_dw == o_dw

        write_w_port = wmem.write_port
        w_dw = write_w_port.data_width

        # assert w_dw == o_dw

        # Streamer logic

        # Warning : I have not changed writer width to be correct
        self.writer = writer = LiteEthRDMAStreamerWriter(
            port     = write_w_port,
            l_key    = wmem_l_key,
            mem_num = mem_num // 2,
            size     = size,
            dw       = w_dw
        )
        self.reader = reader = LiteEthRDMAStreamerReader(
            port     = read_r_port,
            l_key    = rmem_l_key,
            mem_num = mem_num // 2,
            size     = size,
            i_dw     = r_dw,
            o_dw     = dw
        )

        self.comb += [
            sink.connect(writer.sink),
            reader.source.connect(source)
        ]

        # CQ logic
        self.comb += [
            If(cq.source.valid & (cq.source.status == WC.Status.SUCCESS),
                writer.acked.eq(cq.source.opcode == WC.Opcode.RDMA_WRITE),
                reader.acked.eq(cq.source.opcode == WC.Opcode.RDMA_READ),
                cq.source.ready.eq(writer.acked | reader.acked)
            ).Else(
                cq.source.ready.eq(1)
            )
        ]

        # Send queue arbitration
        self.comb += [
            If(enable,
                If(reader.qp_sink.valid,
                    reader.qp_sink.connect(qp.send_sink)
                ).Elif(writer.qp_sink.valid,
                    writer.qp_sink.connect(qp.send_sink)
                )
            )
        ]


        self.comb += [
            # writer.enable.eq(enable),
            reader.enable.eq(enable)
        ]

        # Pass r_keys and vas
        cnt = Signal(max=mem_num)
        self.fsm = fsm = FSM()
        fsm.act("RECEIVE_WRITE_KEYS",
            key_sink.connect(writer.key_sink),
            If(key_sink.valid & key_sink.ready,
                NextValue(cnt, cnt + 1),
                If(cnt == mem_num // 2 - 1,
                    NextState("RECEIVE_READ_KEYS")
                )
            )
        )

        fsm.act("RECEIVE_READ_KEYS",
            key_sink.connect(reader.key_sink),
            If(key_sink.valid & key_sink.ready,
                NextValue(cnt, cnt + 1),
                If(cnt == mem_num - 1,
                    NextState("IDLE")
                )
            )
        )

        fsm.act("IDLE") # Do nothing
