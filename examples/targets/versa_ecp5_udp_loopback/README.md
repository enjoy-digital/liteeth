## Purpose
Example of UDP loopback on versa ECP5 FPGA using the Liteeth UDP module.
The FPGA will echo back any UDP packet it recieves over RJ45 ethernet to the sender.

You can also view a rather detail tutorial [here](http://127.0.0.1:8000/fpga/migen/ethernet_ecp5/).

## Usage

    #!bash
	./udp.py build
	./udp.py load

You will have to configure your ARP table manually since this example does not instantiate the Liteeth ARP module table.
The IP address assigned to the FPGA in this example is ``169.253.2.100``. You should make sure that your computer's
ethernet interface is on the same subnet, for example:

    #!bash
	$ifconfig en7 169.253.2.100 netmask 255.255.255.0

And then after that configure your ARP table:

	#!bash
	$arp -s 169.253.2.50 10:e2:d5:00:00:00 -iface en7

You should now be able to send and recieve UDP packets.

	#!bash
	$python3 listener.py &
	$python3 sender.py

	UDP target IP:169.253.2.50
	UDP target port:8000
	message:Hey.
	received message:b'Heyn'

If everything is working, you should be able to see the ``received message`` line as shown above.
