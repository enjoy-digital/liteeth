PYTHON = python3

CMD = $(PYTHON)

model_tb:
	PYTHONPATH=./ $(CMD) ./model/mac.py
	PYTHONPATH=./ $(CMD) ./model/arp.py
	PYTHONPATH=./ $(CMD) ./model/ip.py
	PYTHONPATH=./ $(CMD) ./model/udp.py
	PYTHONPATH=./ $(CMD) ./model/icmp.py
	PYTHONPATH=./ $(CMD) ./model/etherbone.py

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