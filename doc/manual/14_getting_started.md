# Getting started {#getting-started}

This chapter takes you from a fresh machine to a board that answers `ping` and serves its bus over
Ethernet. It installs the tools, checks them without hardware, builds the Arty A7 bench target, then
talks to it from the host with `ping`, `litex_server` and the test scripts.

It ends with a LiteX SoC whose BIOS uses the network: `ping` from the board and boot over TFTP. Each
step gives the commands, what you should see, and what to check when you do not.

## What you need {#gs-needs}

The first steps use the Arty A7, a common Artix-7 board with a 100 Mbps Ethernet port (MII to an
external PHY chip). Any other bench board of [Supported FPGAs and boards](#boards) follows the
same steps with its own target. The table below lists the rest.

| Item | Use |
|-------------------------|------------------------------------------------------------------|
| Python 3.9 or newer | LiteEth, LiteX and the host scripts |
| LiteX and its cores | LiteX, LiteX-Boards, LiteICLink, LiteEth |
| Vivado | Bitstreams for the AMD (Xilinx) boards, the Arty A7 included |
| OpenOCD | Loading the Arty A7 over JTAG (`--load`) |
| RISC-V GCC toolchain | The BIOS of the SoC with a CPU (last steps only) |
| Host with an RJ45 port | The link partner; a port of its own, not the one of your LAN |
| Ethernet cable | Straight cable from the board to the host |
| TFTP server on the host | Network boot (last step only) |

A host port dedicated to the board avoids conflicts with the addresses of your local network.
The board uses 192.168.1.50 and expects the host at 192.168.1.100, the LiteX defaults.

## Step 1: install and check {#gs-install}

LiteEth is a Python package built on LiteX. Install LiteX by following the installation page of
its wiki: `litex_setup.py` clones and installs Migen, LiteX, LiteX-Boards and the cores, LiteEth
and LiteICLink included. `--gcc=riscv` adds the RISC-V toolchain for the BIOS.

```sh
wget https://raw.githubusercontent.com/enjoy-digital/litex/master/litex_setup.py
chmod +x litex_setup.py
./litex_setup.py --init --install --config=standard
./litex_setup.py --gcc=riscv
```

To work on a clone of LiteEth instead, install it in development mode. The unit tests then check
the installation with no board and no FPGA tool; the selection below covers the ARP, IP, UDP and
Etherbone layers with the Python models of the repository.

```sh
git clone https://github.com/enjoy-digital/liteeth
cd liteeth
pip3 install -e .
python3 -m pytest -v test/test_arp.py test/test_ip.py test/test_udp.py test/test_etherbone.py
```

**What you should see.** All selected tests pass. `python3 -m pytest -v` runs the whole suite;
some of its tests need Icarus Verilog and Verilator and are skipped without them, as [Simulation
and verification](#simulation) describes.

**If not.** An import error names the missing package: LiteX for `litex`, LiteICLink for
`liteiclink`, PyYAML for `yaml`. Rerun the LiteX installation or `pip3 install -e .`.

## Step 2: configure the host {#gs-host}

Give the host port an address in the subnet of the board. On Linux, with `<interface>` the name of
the port that the cable reaches:

```sh
sudo ip addr add 192.168.1.100/24 dev <interface>
sudo ip link set <interface> up
```

A network manager may remove this address when the cable is plugged in or the link goes down.
Declare a static profile in the manager instead, or check the address with `ip addr show
<interface>` before each step.

## Step 3: build and load the Arty A7 target {#gs-build}

The `arty` bench target is a LiteX SoC with no CPU, shown in the figure below. It has the MII PHY
(`LiteEthPHYMII`), the hardware stack with Etherbone on UDP port 1234, a 4 KiB SRAM at `0x20000000`,
and a UDP streamer on port 6000 that drives the LEDs.

The host reaches the bus of the SoC through `litex_server`, which bridges TCP clients to Etherbone
packets.

![The setup of this chapter. The test scripts connect to litex_server over TCP; litex_server
sends Etherbone packets to UDP port 1234 of the board, and the Etherbone frontend turns them into
Wishbone accesses to the SRAM and the registers.](gs_setup.svg)

Build the bitstream with Vivado, then load it over the USB cable of the board. Run the commands
from the `bench/` directory: the target writes `csr.csv`, the register map that the host tools
read, in the working directory.

```sh
cd bench
python3 arty.py --build
python3 arty.py --load
```

**What you should see.** Vivado runs to the end and writes the bitstream in `build/`. After
`--load`, the LEDs of the board run a chaser, and the link LEDs of the RJ45 port light when the
cable reaches the host.

**If not.** Vivado reports its errors in the logs of `build/digilent_arty/gateware`. No link LED
points to the cable or to the host port: check that the port is up (`ip link`).

## Step 4: ping the board {#gs-ping}

The hardware stack answers ARP requests for its IP address and ICMP echo requests, with no CPU.
`ping` is therefore the first test of the link, the PHY, the MAC and the stack together.

```sh
ping -c 4 192.168.1.50
```

**What you should see.** One reply per request, from 192.168.1.50. The replies carry a TTL of 128
(`0x80`), the value that the IP layer of LiteEth sets; a reply with a TTL of 64 comes from a Linux
device holding the same address.

**If not.** Without replies, look at the ARP table of the host with `ip neigh`. An entry
`192.168.1.50` with the MAC address `10:e2:d5:00:00:00` proves that ARP works and points to IP or
ICMP; no entry points to the link, the address or the subnet of the host port. [Debugging](#debugging)
follows these cases in order.

## Step 5: start litex_server {#gs-server}

`litex_server` opens TCP port 1234 on the host for the LiteX tools, and forwards their accesses to
the board as Etherbone packets. With `--udp`, it talks to 192.168.1.50 on UDP port 1234 by
default; `--udp-ip` and `--udp-port` change them. Leave it running in a terminal of its own.

```sh
litex_server --udp --udp-ip=192.168.1.50
```

`--udp-scan` looks for Etherbone devices on the subnet of `--udp-ip` instead. The capture below,
from the LiteX commit that added the option, found two boards:

```console
litex_server --udp --udp-scan --udp-ip=192.168.1.x --udp-port=1234
Etherbone scan on 192.168.1.x network:
- 192.168.1.20
- 192.168.1.50
```

**If not.** A bind error means that another `litex_server` holds TCP port 1234: stop it, or give
this one another port with `--bind-port`.

## Step 6: read and write the bus {#gs-etherbone}

`bench/test_etherbone.py` exercises the bus of the bench SoCs through `litex_server`. It reads
`csr.csv` in the working directory to find the identifier and the SRAM. Run it from `bench/`,
one option at a time or all together.

```sh
python3 test_etherbone.py --ident
python3 test_etherbone.py --access
python3 test_etherbone.py --sram
python3 test_etherbone.py --speed
```

| Option | Test | What to look for |
|----------------|-------------------------------------------------|----------------------------------------------|
| `--ident` | Reads the identifier string of the SoC | `LiteEth bench on Arty` and the build date |
| `--access` | Writes `0x12345678` in the SRAM, reads it back | The read returns the written value |
| `--sram` | Fills 128 bytes with a counter, then 4 patterns | Each dump matches the data written before it |
| `--speed` | Writes and reads 16 bursts of 255 words (about 16 KiB) | Two rates, in KiB/s |

`litex_cli` gives the same access from the command line: `litex_cli --ident` prints the
identifier, and `litex_cli --regs` dumps every register of `csr.csv`.

The `--speed` rates depend on the link, on the host and on the burst length; on the Arty A7, the
100 Mbps link bounds them. Writes get no reply, so the write rate is the rate at which the host
sends them. [Etherbone performance](#perf-etherbone) quotes measurements made on other boards.

**If not.** A timeout of `RemoteClient` with `ping` working points to Etherbone or to
`litex_server`: check its terminal, the UDP port, and that `csr.csv` comes from the bitstream on
the board. A wrong identifier means a `csr.csv` of another build.

## Step 7: drive the LEDs over UDP {#gs-streamer}

The UDP streamer of the target receives bytes on UDP port 6000 and shows the last byte on the
LEDs for one second, then gives them back to the chaser. `bench/test_udp_streamer.py` sends
`0x00` and `0xff` in turn, eight times.

```sh
python3 test_udp_streamer.py --leds
```

**What you should see.** The LEDs blink all on and all off while the script runs, then the chaser
resumes. The script also has a `--switches` option, which listens for the switches of the board;
the `arty` target leaves that part of its logic disabled, so it receives nothing.

## Step 8: a SoC with a CPU and the BIOS {#gs-bios}

The bench targets of Step 3 have no CPU. The LiteX-Boards targets build a full SoC, with a CPU, its
BIOS and the DRAM of the board. `--with-ethernet` adds the software MAC that the BIOS and Linux use;
with `--eth-dynamic-ip`, the BIOS also offers the `ping` command and sets its addresses at run time.

```sh
python3 -m litex_boards.targets.digilent_arty --with-ethernet --eth-dynamic-ip \
    --build --load
litex_term /dev/ttyUSBx
```

`/dev/ttyUSBx` stands for the serial port of the board, at 115200 baud by default. In the console,
`help` lists the commands; the network ones are in the table below.

| Command | Effect |
|--------------------------------------|------------------------------------------------------------|
| `ping <address>` | Sends one ICMP echo request and prints the reply |
| `eth_local_ip <address>` | Sets the IP address of the board |
| `eth_remote_ip <address>` | Sets the IP address of the TFTP server |
| `eth_mac_addr <address>` | Sets the MAC address of the board |
| `netboot [file]` | Boots over TFTP, as the next step describes |
| `mdio_dump <phy> <count>` | Reads the registers of the external PHY chip |

**What you should see.** `ping 192.168.1.100` prints one line with the size of the reply, the
address of the host, the sequence number and the round-trip time. A host that does not answer
ARP gives `ARP failed`.

Etherbone and the BIOS console also work together. With `--uart-name=crossover`, the console goes
through the bus instead of the serial port, and `litex_term crossover` reaches it through
`litex_server`. The capture below was made this way on a ButterStick (ECP5, RGMII), with
`--uart-name=crossover --with-etherbone`; the end of the boot is cut.

```console
        __   _ __      _  __
       / /  (_) /____ | |/_/
      / /__/ / __/ -_)>  <
     /____/_/\__/\__/_/|_|
   Build your hardware, easily!
[...]
--=============== SoC ==================--
CPU:            VexRiscv @ 75MHz
BUS:            WISHBONE 32-bit @ 4GiB
CSR:            32-bit data
ROM:            128KiB
SRAM:           8KiB
L2:             8KiB
SDRAM:          524288KiB 16-bit @ 300MT/s (CL-6 CWL-5)
[...]
```

## Step 9: boot over the network {#gs-netboot}

The BIOS loads software over TFTP from the host, at 192.168.1.100 on UDP port 69. It first asks
for `boot.json`, a manifest that lists files and their load addresses, then for `boot.bin`, loaded
at the start of the main RAM. `netboot` starts this by hand, and the BIOS tries it at boot among
its other boot methods.

Run a TFTP server on the host, serving a directory with the files. The manifest below is the one
of the Linux images of the capture that follows: each key is a file, each value its load address.
The BIOS jumps to the last image of the file, or to the address of an `addr` key.

```json
{
    "Image":       "0x40000000",
    "rv32.dtb":    "0x41000000",
    "opensbi.bin": "0x40f00000"
}
```

**What you should see.** The BIOS prints the addresses, then one line per file with its size,
then jumps. The capture below, from the LiteX documentation, boots Linux on a VexRiscv SoC; it
comes from an earlier BIOS, whose spacing differs slightly (`Local IP:` today):

```console
Booting from network...
Local IP : 192.168.1.50
Remote IP: 192.168.1.100
Booting from boot.json...
Copying Image to 0x40000000... (4545524 bytes)
Copying rv32.dtb to 0x41000000... (2167 bytes)
Copying opensbi.bin to 0x40f00000... (53640 bytes)
Executing booted program at 0x40f00000
--============= Liftoff! ===============--
```

**If not.** `Network boot failed.` after the addresses means that the TFTP server did not answer
with the file: check that it listens on 192.168.1.100, that a firewall leaves UDP port 69 open, and
the names of the files.

`ARP failed` points to the link or to the host address. [Integration](#integration) describes the
boot manifest and the network options of the BIOS.

## Where to go next {#gs-next}

[Integration](#integration) adds LiteEth to your own SoC or design; [Examples and
targets](#examples) describes the other bench targets; [Debugging](#debugging) follows a link that
does not come up, step by step.
