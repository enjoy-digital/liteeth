#!/usr/bin/env python3

# This file is Copyright (c) 2019 Yehowshua Immanuel <yimmanuel3@gatech.edu>
# License: BSD

import socket
import time
import datetime

UDP_IP   = "192.168.1.100"
UDP_PORT = 8000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

while True:
    t = datetime.datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
    print(t)
    sock.sendto(t.encode('utf-8'), (UDP_IP, UDP_PORT))
    time.sleep(0.5)
