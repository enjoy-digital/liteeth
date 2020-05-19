# This file is Copyright (c) 2020 Shawn Hoffman <godisgovernment@gmail.com>
# License: BSD

from liteeth.common import *

phy_status_layout = [
    ("ctl_r", 1),
    ("ctl_f", 1),
    ("data", 8),
]

class LiteEthPHYRGMIIStatus(Module):
    def __init__(self):
        self.phy = phy = Record(phy_status_layout)

        # phy_status signals optionally sent during inter-frame
        self.link_up = Signal()
        self.rxc_speed = Signal(2)
        self.full_duplex = Signal()

        inter_frame = Signal()
        self.comb += inter_frame.eq(~(phy.ctl_r | phy.ctl_f))

        self.sync += If(inter_frame,
            self.link_up.eq(phy.data[0]),
            self.rxc_speed.eq(phy.data[1:3]),
            self.full_duplex.eq(phy.data[3]))
