# This file is Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import socket
import time
from litex.soc.tools.remote.etherbone import *

SRAM_BASE = 0x01000000

socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# test probe
packet = EtherbonePacket()
packet.pf = 1
packet.encode()
socket.sendto(bytes(packet), ("192.168.1.50", 20000))
time.sleep(0.01)

# test writes
writes_datas = [j for j in range(16)]
writes = EtherboneWrites(base_addr=SRAM_BASE, datas=writes_datas)
record = EtherboneRecord()
record.writes = writes
record.wcount = len(writes_datas)

packet = EtherbonePacket()
packet.records = [record]
packet.encode()
socket.sendto(bytes(packet), ("192.168.1.50", 20000))
time.sleep(0.01)

# test reads
reads_addrs = [SRAM_BASE+4*j for j in range(16)]
reads = EtherboneReads(base_ret_addr=0x1000, addrs=reads_addrs)
record = EtherboneRecord()
record.reads = reads
record.rcount = len(reads_addrs)

packet = EtherbonePacket()
packet.records = [record]
packet.encode()
socket.sendto(bytes(packet), ("192.168.1.50", 20000))
time.sleep(0.01)
