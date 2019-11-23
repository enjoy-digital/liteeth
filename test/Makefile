PYTHON = python3

CMD = $(PYTHON)

examples:
	cd ../examples && $(PYTHON) make.py -t base -s BaseSoC -p kc705 -Ob run False build-bitstream
	cd ../examples && $(PYTHON) make.py -t base -s BaseSoCDevel -p kc705 -Ob run False build-bitstream
	cd ../examples && $(PYTHON) make.py -t udp -s UDPSoC -p kc705 -Ob run False build-bitstream
	cd ../examples && $(PYTHON) make.py -t udp -s UDPSoCDevel -p kc705 -Ob run False build-bitstream
	cd ../examples && $(PYTHON) make.py -t etherbone -s EtherboneSoC -p kc705 -Ob run False build-bitstream
	cd ../examples && $(PYTHON) make.py -t etherbone -s EtherboneSoCDevel -p kc705 -Ob run False build-bitstream
	cd ../examples && $(PYTHON) make.py -t tty -s TTYSoC -p kc705 -Ob run False build-bitstream
	cd ../examples && $(PYTHON) make.py -t tty -s TTYSoCDevel -p kc705 -Ob run False build-bitstream

all: examples