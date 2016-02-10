cores:
	rm -rf cores
	mkdir cores

	python3 core.py --phy MII --core mac
	cp liteeth/gateware/liteeth.v cores/liteeth_mac_wishbone_mii.v
	python3 core.py --phy GMII --core mac
	cp liteeth/gateware/liteeth.v cores/liteeth_mac_wishbone_gmii.v
	python3 core.py --phy RGMII --core mac
	cp liteeth/gateware/liteeth.v cores/liteeth_mac_wishbone_rgmii.v

	python3 core.py --phy MII --core udp
	cp liteeth/gateware/liteeth.v cores/liteeth_udpip_mii.v
	python3 core.py --phy GMII --core udp
	cp liteeth/gateware/liteeth.v cores/liteeth_udpip_gmii.v
	python3 core.py --phy RGMII --core udp
	cp liteeth/gateware/liteeth.v cores/liteeth_udpip_rgmii.v

.PHONY: cores
