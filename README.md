```
                                      __   _ __      ______  __
                                     / /  (_) /____ / __/ /_/ /
                                    / /__/ / __/ -_) _// __/ _ \
                                   /____/_/\__/\__/___/\__/_//_/

                                 Copyright 2012-2020 / EnjoyDigital

                             A small footprint and configurable Ethernet core
                                      powered by Migen & LiteX
```

[![](https://github.com/enjoy-digital/liteeth/workflows/ci/badge.svg)](https://github.com/enjoy-digital/liteeth/actions) ![License](https://img.shields.io/badge/License-BSD%202--Clause-orange.svg)


[> Intro
--------
LiteEth provides a small footprint and configurable Ethernet core.

LiteEth is part of LiteX libraries whose aims are to lower entry level of
complex FPGA cores by providing simple, elegant and efficient implementations
of components used in today's SoC such as Ethernet, SATA, PCIe, SDRAM Controller...

Using Migen to describe the HDL allows the core to be highly and easily configurable.

LiteEth can be used as LiteX library or can be integrated with your standard
design flow by generating the verilog rtl that you will use as a standard core.

[> Features
-----------
PHY:
  - MII, RMII 100Mbps PHYs.
  - GMII / RGMII /1000BaseX 1Gbps PHYs.

Core:
  - Configurable MAC (HW or SW interface)
  - ARP / ICMP / UDP (HW or SW)

Frontend:
  - Etherbone (Wishbone over UDP: Slave or Master support)

[> FPGA Proven
---------------
LiteEth is already used in commercial and open-source designs:
- MiSoC: http://m-labs.hk/gateware.html
- ARTIQ: http://m-labs.hk/artiq/index.html
- HDMI2USB: http://hdmi2usb.tv/home/
- and others commercial designs...

[> Possible improvements
------------------------
- add standardized interfaces (AXI, Avalon-ST)
- add DMA interface to MAC
- add more documentation
- ... See below Support and consulting :)

If you want to support these features, please contact us at florent [AT]
enjoy-digital.fr.

[> Getting started
------------------
1. Install Python 3.6+ and FPGA vendor's development tools.
2. Install LiteX and the cores by following the LiteX's wiki [installation guide](https://github.com/enjoy-digital/litex/wiki/Installation).
3. You can find examples of integration of the core with LiteX in LiteX-Boards and in the examples directory.

[> Tests
--------
Unit tests are available in ./test/.
To run all the unit tests:
```sh
$ ./setup.py test
```

Tests can also be run individually:
```sh
$ python3 -m unittest test.test_name
```

[> License
----------
LiteEth is released under the very permissive two-clause BSD license. Under
the terms of this license, you are authorized to use LiteEth for closed-source
proprietary designs.
Even though we do not require you to do so, those things are awesome, so please
do them if possible:
 - tell us that you are using LiteEth
 - cite LiteEth in publications related to research it has helped
 - send us feedback and suggestions for improvements
 - send us bug reports when something goes wrong
 - send us the modifications and improvements you have done to LiteEth.

[> Support and consulting
-------------------------
We love open-source hardware and like sharing our designs with others.

LiteEth is developed and maintained by EnjoyDigital.

If you would like to know more about LiteEth or if you are already a happy
user and would like to extend it for your needs, EnjoyDigital can provide standard
commercial support as well as consulting services.

So feel free to contact us, we'd love to work with you! (and eventually shorten
the list of the possible improvements :)

[> Contact
----------
E-mail: florent [AT] enjoy-digital.fr