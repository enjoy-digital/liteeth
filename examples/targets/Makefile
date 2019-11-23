cores:
	rm -rf cores
	mkdir cores

	python3 core.py --phy MII --core wishbone
	cp liteeth/gateware/liteeth.v cores/liteeth_mac_mii.v
	python3 core.py --phy GMII --core wishbone
	cp liteeth/gateware/liteeth.v cores/liteeth_mac_gmii.v
	python3 core.py --phy RGMII --core wishbone
	cp liteeth/gateware/liteeth.v cores/liteeth_mac_rgmii.v

	python3 core.py --phy MII --core udp
	cp liteeth/gateware/liteeth.v cores/liteeth_udp_mii.v
	python3 core.py --phy GMII --core udp
	cp liteeth/gateware/liteeth.v cores/liteeth_udp_gmii.v
	python3 core.py --phy RGMII --core udp
	cp liteeth/gateware/liteeth.v cores/liteeth_udp_rgmii.v

.PHONY: cores
