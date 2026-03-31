
from litex.gen import *

from liteeth.common import *

from liteeth.mac       import LiteEthMAC
from liteeth.core.arp  import LiteEthARP
from liteeth.core.ip   import LiteEthIP
from liteeth.core.udp  import LiteEthUDP
from liteeth.core.icmp import LiteEthICMP

from liteeth.core.rocev2.rocev2 import LiteEthIBTransport

class LiteEthRoCEv2Core(LiteXModule):
    def __init__(self, phy, mac_address, ip_address, mrs, clk_freq, arp_entries=1, dw=8,
        with_icmp         = True, icmp_fifo_depth=128,
        with_ip_broadcast = True,
        with_sys_datapath = False,
        tx_cdc_depth      = 32,
        tx_cdc_buffered   = True,
        rx_cdc_depth      = 32,
        rx_cdc_buffered   = True,
        interface         = "crossbar",
        endianness        = "big",
    ):
        # Parameters.
        # -----------
        ip_address = convert_ip(ip_address)

        # MAC.
        # ----
        self.mac = LiteEthMAC(
            phy               = phy,
            dw                = dw,
            interface         = interface,
            endianness        = endianness,
            hw_mac            = mac_address,
            with_preamble_crc = True,
            with_sys_datapath = with_sys_datapath,
            tx_cdc_depth      = tx_cdc_depth,
            tx_cdc_buffered   = tx_cdc_buffered,
            rx_cdc_depth      = rx_cdc_depth,
            rx_cdc_buffered   = rx_cdc_buffered,
        )

        # ARP.
        # ----
        self.arp = LiteEthARP(
            mac         = self.mac,
            mac_address = mac_address,
            ip_address  = ip_address,
            clk_freq    = clk_freq,
            entries     = arp_entries,
            dw          = dw,
        )

        # IP.
        # ---
        self.ip  = LiteEthIP(
            mac            = self.mac,
            mac_address    = mac_address,
            ip_address     = ip_address,
            arp_table      = self.arp.table,
            with_broadcast = with_ip_broadcast,
            dw             = dw,
            dont_fragment  = True,
        )
        # ICMP (Optional).
        # ----------------
        if with_icmp:
            self.icmp = LiteEthICMP(
                ip         = self.ip,
                ip_address = ip_address,
                dw         = dw,
                fifo_depth = icmp_fifo_depth,
            )

        # ----
        self.udp = LiteEthUDP(
            ip         = self.ip,
            ip_address = ip_address,
            dw         = dw,
        )

        self.rocev2 = LiteEthIBTransport(
            ip          = self.ip,
            udp         = self.udp,
            mrs         = mrs,
            clk_freq    = clk_freq,
        )
