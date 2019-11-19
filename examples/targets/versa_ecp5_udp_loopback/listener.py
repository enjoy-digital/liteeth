# This file is Copyright (c) 2019 Yehowshua Immanuel <yimmanuel3@gatech.edu>
# License: BSD

import socket

UDP_IP = "192.168.1.100"
UDP_PORT = 8000

sock = socket.socket(socket.AF_INET, # Internet
                     socket.SOCK_DGRAM) # UDP
sock.bind((UDP_IP, UDP_PORT))

while True:
    data, addr = sock.recvfrom(1024) # buffer size is 1024 bytes
    print("received message:" + str(data))
