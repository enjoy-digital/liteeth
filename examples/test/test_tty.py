# This file is Copyright (c) 2015-2018 Florent Kermarrec <florent@enjoy-digital.fr>
# License: BSD

import socket
import threading


def test(fpga_ip, udp_port, test_message):
    tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx_sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx_sock.bind(("", udp_port))
    rx_sock.settimeout(0.5)

    def receive():
        while True:
            try:
                msg = rx_sock.recv(8192)
                for byte in msg:
                    print(chr(byte), end="")
            except:
                break

    def send():
        tx_sock.sendto(bytes(test_message, "utf-8"), (fpga_ip, udp_port))

    receive_thread = threading.Thread(target=receive)
    receive_thread.start()

    send_thread = threading.Thread(target=send)
    send_thread.start()

    try:
        send_thread.join(5)
        send_thread.join(5)
    except KeyboardInterrupt:
        pass


# # #

test_message = "LiteEth virtual TTY Hello world\n"
test("192.168.1.50", 10000, test_message)

# # #