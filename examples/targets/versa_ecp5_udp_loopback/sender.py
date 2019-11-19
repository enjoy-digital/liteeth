# This file is Copyright (c) 2019 Yehowshua Immanuel <yimmanuel3@gatech.edu>
# License: BSD
import socket

UDP_IP = "192.168.1.50"
UDP_PORT = 8000
MESSAGE = "Hey."

print("UDP target IP:" + str(UDP_IP))
print("UDP target port:" + str(UDP_PORT))
print("message:" + str(MESSAGE))

sock = socket.socket(socket.AF_INET, # Internet
                     socket.SOCK_DGRAM) # UDP
sock.sendto(MESSAGE.encode('utf-8'), (UDP_IP, UDP_PORT))
