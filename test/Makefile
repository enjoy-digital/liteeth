PYTHON = python3

CMD = $(PYTHON)

model_tb:
	cd ../ && PYTHONPATH=./ $(CMD) test/model/mac.py
	cd ../ && PYTHONPATH=./ $(CMD) test/model/arp.py
	cd ../ && PYTHONPATH=./ $(CMD) test/model/ip.py
	cd ../ && PYTHONPATH=./ $(CMD) test/model/udp.py
	cd ../ && PYTHONPATH=./ $(CMD) test/model/icmp.py
	cd ../ && PYTHONPATH=./ $(CMD) test/model/etherbone.py

example_designs:
	cd ../example_designs && $(PYTHON) make.py -t base -s BaseSoC -p kc705 -Ob run False build-bitstream
	cd ../example_designs && $(PYTHON) make.py -t base -s BaseSoCDevel -p kc705 -Ob run False build-bitstream
	cd ../example_designs && $(PYTHON) make.py -t udp -s UDPSoC -p kc705 -Ob run False build-bitstream
	cd ../example_designs && $(PYTHON) make.py -t udp -s UDPSoCDevel -p kc705 -Ob run False build-bitstream
	cd ../example_designs && $(PYTHON) make.py -t etherbone -s EtherboneSoC -p kc705 -Ob run False build-bitstream
	cd ../example_designs && $(PYTHON) make.py -t etherbone -s EtherboneSoCDevel -p kc705 -Ob run False build-bitstream
	cd ../example_designs && $(PYTHON) make.py -t tty -s TTYSoC -p kc705 -Ob run False build-bitstream
	cd ../example_designs && $(PYTHON) make.py -t tty -s TTYSoCDevel -p kc705 -Ob run False build-bitstream

all: model_tb example_designs