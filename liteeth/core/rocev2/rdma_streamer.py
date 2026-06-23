from migen import *

from litex.gen import *

from litex.soc.interconnect.stream import Endpoint

from liteeth.common import WR_OPCODE, WC

class LiteEthRDMAStreamer(LiteXModule):
    def __init__(self, qp, cq, wmems, rmems, dw):
        self.sink   = sink   = Endpoint([("data", dw)])
        self.source = source = Endpoint([("data", dw)])

        self.r_keys = r_keys = Array([Signal(32, reset_less=True) for _ in range(len(wmems) + len(rmems))])
        self.vas    = vas    = Array([Signal(64, reset_less=True) for _ in range(len(wmems) + len(rmems))])

        self.enable = enable = Signal()

        # # #

        # Parameters
        read_l_keys = []
        read_r_ports = []
        r_dw = 0
        r_size = 0

        for i, mr in enumerate(rmems):
            read_r_port = mr.memory.get_port()
            self.specials += read_r_port

            if i == 0:
                r_dw = len(read_r_port.dat_r)
                r_size = mr.region_size
            else:
                assert len(read_r_port.dat_r) == r_dw
                assert mr.region_size == r_size

            read_l_keys.append(mr.l_key)
            read_r_ports.append(read_r_port)

        assert r_dw % dw == 0
        ratio_r_port = r_dw // dw

        write_l_keys = []
        write_w_ports = []
        w_dw = 0
        w_size = 0

        for i, mr in enumerate(wmems):
            write_w_port = mr.memory.get_port(write_capable=True)
            self.specials += write_w_port

            if i == 0:
                w_dw = len(write_w_port.dat_w)
                w_size = mr.region_size
            else:
                assert len(write_w_port.dat_w) == w_dw
                assert mr.region_size == w_size

            write_l_keys.append(mr.l_key)
            write_w_ports.append(write_w_port)

        assert w_dw % dw == 0
        ratio_w_port = w_dw // dw

        # Streamer logic

        # Write buffs (data sent)
        available_w_buffs = Signal(max=len(wmems) + 1, reset=len(wmems))
        available_w_buffs_inc = Signal()
        available_w_buffs_dec = Signal()
        choose_w_port = Signal(max=len(wmems))
        choose_w_port_next = Signal().like(choose_w_port)
        w_addr = Signal(log2_int(w_size//(w_dw//8)))
        w_addr_next = Signal().like(w_addr)
        w_cnt = Signal(log2_int(ratio_w_port))
        w_buff = Signal(w_dw)
        w_buff_next = Signal().like(w_buff)

        self.comb += [
            sink.ready.eq(available_w_buffs != 0),
            Case(choose_w_port, {
                i: [
                    write_w_ports[i].adr[:log2_int(w_size//(w_dw//8))].eq(w_addr),
                    write_w_ports[i].we.eq(sink.valid & (w_cnt == ratio_w_port - 1)),
                    write_w_ports[i].dat_w.eq(w_buff_next),
                ]
                for i in range(len(wmems))
            }),
            w_buff_next.eq(Cat(w_buff[dw:], sink.data)),
            w_addr_next.eq(w_addr),
            choose_w_port_next.eq(choose_w_port),
            If(w_cnt == ratio_w_port - 1,
                w_addr_next.eq(w_addr + 1),
                If(w_addr == w_size//(w_dw//8) - 1,
                    If(choose_w_port == len(wmems) - 1,
                        choose_w_port_next.eq(0)
                    ).Else(
                        choose_w_port_next.eq(choose_w_port + 1)
                    )
                )
            )
        ]

        self.sync += [
            If(sink.valid & (available_w_buffs != 0),
                w_addr.eq(w_addr_next),
                w_buff.eq(w_buff_next),
                w_cnt.eq(w_cnt + 1),
            ),
            available_w_buffs.eq(available_w_buffs + available_w_buffs_inc - available_w_buffs_dec),
            choose_w_port.eq(choose_w_port_next)
        ]

        self.comb += [
            If(sink.valid & (w_addr_next != w_addr) & (choose_w_port_next != choose_w_port),
                available_w_buffs_dec.eq(1),
                qp.send_queue.sink.valid.eq(1),
                qp.send_queue.sink.wr_opcode.eq(WR_OPCODE.RDMA_WRITE),
                qp.send_queue.sink.ack_req.eq(1),
                qp.send_queue.sink.w_immdt.eq(0),
                qp.send_queue.sink.va.eq(vas[choose_w_port]),
                qp.send_queue.sink.dma_len.eq(w_size),
                Case(choose_w_port, {
                    i: [
                        qp.send_queue.sink.l_key.eq(write_l_keys[i]),
                    ] for i in range(len(wmems))
                }),
                qp.send_queue.sink.r_key.eq(r_keys[choose_w_port])
            ),
            If(cq.source.valid &
               (cq.source.status == WC.Status.SUCCESS) &
               (cq.source.opcode == WC.Opcode.RDMA_WRITE),
                cq.source.ready.eq(1),
                available_w_buffs_inc.eq(1)
            )
        ]

        # Read buffs (data received)

        readable_r_buffs = Signal(max=len(rmems) + 1)
        readable_r_buffs_inc = Signal()
        readable_r_buffs_dec = Signal()
        available_r_buffs = Signal(max=len(rmems) + 1, reset=len(rmems))
        available_r_buffs_inc = Signal()
        available_r_buffs_dec = Signal()
        choose_req_port = Signal(max=len(rmems))
        choose_req_port_next = Signal().like(choose_req_port)
        choose_r_port = Signal(max=len(rmems))
        choose_r_port_next = Signal().like(choose_r_port)
        r_addr = Signal(log2_int(r_size//(r_dw//8)))
        r_addr_next = Signal().like(r_addr)
        r_cnt = Signal(log2_int(ratio_r_port))
        r_buff = Signal(r_dw)

        self.comb += [
            source.valid.eq(readable_r_buffs != 0),
            Case(r_cnt, {
                i: source.data.eq(r_buff[i*dw:(i+1)*dw])
                for i in range(ratio_r_port)
            }),
            Case(choose_r_port, {
                i: [
                    read_r_ports[i].adr[:log2_int(r_size//(r_dw//8))].eq(r_addr),
                    r_buff.eq(read_r_ports[i].dat_r),
                ]
                for i in range(len(rmems))
            }),
            r_addr_next.eq(r_addr),
            If(r_cnt == ratio_r_port - 1,
                r_addr_next.eq(r_addr + 1),
                If(r_addr == r_size//(r_dw//8) - 1,
                    readable_r_buffs_dec.eq(1),
                    If(choose_r_port == len(rmems) - 1,
                        choose_r_port_next.eq(0)
                    ).Else(
                        choose_r_port_next.eq(choose_r_port + 1)
                    )
                )
            )
        ]

        self.sync += [
            If(source.ready & (readable_r_buffs != 0),
                r_addr.eq(r_addr_next),
                r_cnt.eq(r_cnt + 1),
            ),
            available_r_buffs.eq(available_r_buffs + available_r_buffs_inc - available_r_buffs_dec),
            readable_r_buffs.eq(readable_r_buffs + readable_r_buffs_inc - readable_r_buffs_dec),
            choose_req_port.eq(choose_req_port_next)
        ]

        self.comb += [
            choose_req_port_next.eq(choose_req_port),
            If((r_addr_next != r_addr) & (choose_r_port_next != choose_r_port),
                readable_r_buffs_dec.eq(1),
                available_r_buffs_inc.eq(1)
            ),
            If(enable & sink.valid & (available_r_buffs != 0) & ~available_w_buffs_dec, # Wait when Write is committing request to send_queue
                available_r_buffs_dec.eq(1),
                qp.send_queue.sink.valid.eq(1),
                qp.send_queue.sink.wr_opcode.eq(WR_OPCODE.RDMA_READ),
                qp.send_queue.sink.ack_req.eq(1),
                qp.send_queue.sink.w_immdt.eq(0),
                qp.send_queue.sink.va.eq(vas[choose_req_port + len(wmems)]),
                qp.send_queue.sink.dma_len.eq(r_size),
                Case(choose_req_port, {
                    Constant(i): [
                        qp.send_queue.sink.l_key.eq(read_l_keys[i]),
                    ] for i in range(len(rmems))
                }),
                qp.send_queue.sink.r_key.eq(r_keys[choose_req_port + len(wmems)]),
                choose_req_port_next.eq(choose_req_port + 1)
            ),
            If(cq.source.valid &
               (cq.source.status == WC.Status.SUCCESS) &
               (cq.source.opcode == WC.Opcode.RDMA_READ),
                cq.source.ready.eq(1),
                readable_r_buffs_inc.eq(1)
            )
        ]

