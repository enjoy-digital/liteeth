#!/usr/bin/env python3

# This file is Copyright (c) 2019 Yehowshua Immanuel <yimmanuel3@gatech.edu>
# License: BSD

import socket

UDP_IP   = "192.168.1.50"
UDP_PORT = 8000

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))

while True:
    data, addr = sock.recvfrom(1024)
    print(data.decode("utf-8"))
