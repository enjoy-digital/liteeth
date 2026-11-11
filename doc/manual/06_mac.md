# MAC {#mac}

This chapter describes the MAC: the transmit and receive paths that turn packets into Ethernet
frames and back, the three ways to connect it (to the hardware stack, to a CPU, or to both), and the
attributes a PHY uses to change its behaviour.

Read it to integrate a software MAC, to understand the error counters, or to connect a hard MAC. The
frame format is explained in [Ethernet in a nutshell](#ethernet-primer).

## At a glance {#mac-glance}

| Item | Value |
|----------------------|---------------------------------------------------------------------|
| Top-level class | `LiteEthMAC` (`liteeth/mac/__init__.py`) |
| Interfaces | `crossbar` (hardware stack), `wishbone` (CPU), `hybrid` (both) |
| TX framing | Padding to 60 bytes, CRC32 FCS, preamble and SFD, inter-frame gap |
| RX checking | Preamble, FCS, minimum length; errors flagged on the last beat |
| Default MTU | 1530 bytes (`eth_mtu_default`), 9022 with jumbo frames |
| CPU buffers | 2 RX and 2 TX slots of 2048 bytes by default |
| Counters | Preamble errors, CRC errors, store-and-forward drops |
| Not handled | VLAN tags, pause frames, address filtering outside the hybrid mode |

## Interfaces {#mac-interfaces}

The `interface` argument selects what sits above the MAC. The table below compares the three modes.

| `interface` | Above the MAC | Typical use |
|------------------|------------------------------------------|----------------------------------------|
| `crossbar` | MAC header parser and crossbar by EtherType | Hardware stack (`LiteEthUDPIPCore`) |
| `wishbone` | SRAM slots on two Wishbone buses, events | Software MAC for the BIOS or Linux (`add_ethernet()`) |
| `hybrid` | Address filter, hardware stack and SRAM slots | Etherbone and a CPU network stack on one link |

In every mode the MAC core underneath is the same (`LiteEthMACCore`): a transmit path from the
interface to the PHY and a receive path back. The next two sections describe them.

## Transmit path {#mac-tx}

The figure below shows the transmit path. Frames come from the interface at the core width and
leave to the PHY at the PHY width, with the framing added in between.

![MAC transmit path. Follow a frame from left to right: it crosses into the PHY clock, is padded,
gets its FCS and preamble, waits whole in the optional packet FIFO, and leaves after the gap of the
previous frame.](mac_tx.svg)

The stages, in order:

- **Clock crossing and width conversion.** By default the frame crosses from `sys` to `eth_tx`
  first, with the width converter on the narrow side of the crossing. With `with_sys_datapath`,
  the framing stages run in `sys` and the crossing comes after them; see
  [Clock domains](#arch-clocks).
- **Padding.** A frame shorter than 60 bytes is padded with zero bytes up to 60, so that it reaches
  the 64-byte minimum with its FCS (`LiteEthMACPaddingInserter`).
- **CRC.** The CRC32 of IEEE 802.3, polynomial `0x04C11DB7`, is computed over the frame and
  appended as the 4-byte FCS (`LiteEthMACCRC32Inserter`).
- **Preamble.** Seven `0x55` bytes and the SFD byte `0xd5` are sent before the frame
  (`LiteEthMACPreambleInserter`).
- **Packet FIFO.** With store-and-forward, each formatted frame is held whole in `eth_tx` before it
  starts, so the PHY is fed without a gap; see [Buffering and drops](#arch-buffering).
- **Inter-frame gap.** After each frame the gap stage holds the stream idle for 12 bytes of the
  PHY width (`LiteEthMACGap`). It comes after every FIFO, since a FIFO would remove the idle
  cycles.

The gap counts `eth_tx` cycles: `ceil(12/(dw/8))` cycles at the PHY width `dw`, or the value of the
PHY's `tx_gap_cycles` attribute. The ECP5 RGMII PHY sets it for each link speed. A PHY that
inserts the gap itself (`integrated_ifg_inserter`, as the XGMII PHY does) gets no gap stage. The
BASE-R PHYs reuse the XGMII encoder without declaring it, so both gaps apply there ([5G, 10G and
25G BASE-R, and 100G](#baser-phys)).

## Receive path {#mac-rx}

The figure below shows the receive path. It checks the frame in the PHY clock, then hands it to the
interface in `sys`.

![MAC receive path. Look at the three checkers in the PHY clock and the counters they feed in
`sys`: the checkers flag errors, the optional drop FIFO removes whole frames.](mac_rx.svg)

The stages, in order:

- **Preamble check.** The checker drops beats until it sees the end of the preamble, the SFD at
  8 bits, then passes the frame on. A frame that ends before that counts as a preamble error
  (`LiteEthMACPreambleChecker`).
- **CRC check.** The checker removes the 4 FCS bytes. On a mismatch, or on a PHY error in any byte,
  it sets `error` on every lane of the last beat; a mismatch also counts a CRC error
  (`LiteEthMACCRC32Checker`).
- **Padding check.** A frame shorter than 60 bytes without FCS gets `error` on its last beat. The
  padding of a short frame is not removed; the IP layer passes it on and UDP or ICMP trims it.
- **Drop FIFO.** With store-and-forward, frames are stored whole and dropped when the FIFO cannot
  hold them; each drop is counted.
- **Clock crossing and width conversion** to the core width in `sys`.

The checkers mark bad frames but do not remove them. The software MAC drops them; in the hardware
stack, each layer looks at `error` at the end of a packet (see [Errors](#arch-errors)). The three
counters reach `sys` through pulse synchronizers.

## Software MAC {#mac-wishbone}

In `wishbone` mode the MAC stores frames in SRAM slots that a CPU reads and writes over Wishbone,
as the figure below shows. LiteX builds this mode with `add_ethernet()`, and the BIOS and the Linux
`liteeth` driver use it.

![Software MAC. Follow the RX path at the top: the SRAM writer fills an RX slot and reports it in
the registers; the CPU reads the slot. On the TX path at the bottom, the CPU writes a slot and
starts the SRAM reader through the registers.](mac_slots.svg)

### Slots and memory map {#mac-slots}

Each slot stores one frame of up to `eth_mtu` bytes, without preamble and FCS. Slots are spaced by
`slot_size`, the MTU rounded up to a power of two: 2048 bytes for the default MTU, 16384 bytes with
jumbo frames. The MAC
region of the SoC holds the RX slots first, then the TX slots. The constants `rx_slots`, `tx_slots`
and `slot_size` give the software the layout.

| Region | Content | Default size |
|----------------|-------------------------------------------|-----------------------------|
| `ethmac_rx` | `nrxslots` RX slots, read-only by default | 2 x 2048 bytes |
| `ethmac_tx` | `ntxslots` TX slots | 2 x 2048 bytes |

Words are stored in the CPU's byte order (`endianness`, `big` by default, set from the CPU by
LiteX). The `full_memory_we` option splits the slot memories into 8-bit blocks, which helps block
RAM inference on most FPGAs but can cost memory with some toolchains (ECP5 with Yosys).

### Receive {#mac-wishbone-rx}

The SRAM writer stores each received frame into the next slot and pushes its slot number and
length into a status FIFO of `nrxslots` entries. The `available` event stays pending while the FIFO
holds a frame. Software reads the slot and length registers, copies the frame, then clears the
event, which pops the entry and frees the slot.

When every slot is full, the writer drops new frames and counts them in its `errors` register.
With `rx_fifo_depth`, frames wait in that FIFO instead; when it is full too, the MAC core is held
back, and its store-and-forward FIFO, when it has one, drops the frames that do not fit. The writer
also drops frames with an error (bad FCS, short frame) and frames longer than the MTU, without
writing past the slot. A rejected frame never becomes visible in a slot.

### Transmit {#mac-wishbone-tx}

Software writes a frame into a TX slot, writes the slot number and the length, then writes the
`start` register. Commands queue in a FIFO of `ntxslots` entries: `ready` tells that the FIFO has
room and `level` how many commands wait. The `done` event fires when the reader has read a frame
out of its slot, so that the slot can be reused, or has retired an invalid command. The frame may
not have reached the wire yet.

A command with a zero length, a length beyond the MTU (rounded up to whole words) or a slot number
out of range is retired
with the normal completion, but sends no frame and reads no memory. Software should validate its
commands, since no register reports this case.

### Timestamps {#mac-timestamps}

With a `timestamp` signal, the MAC records the value of that signal for each frame: on receive
when the first beat reaches the SRAM writer, on transmit when the SRAM reader starts the frame.
LiteX connects the uptime counter of `timer0` (`with_timestamp`). Both points are in `sys`, on the
interface side of the MAC, not on the wire.

The receive timestamp travels with the slot status. On transmit, a second FIFO keeps the slot
and timestamp of each sent frame, and `done` becomes a level event that stays pending until
software reads them. When that FIFO is full, the reader waits instead of losing a completion. An
invalid command reports a timestamp of zero.

### Registers {#mac-registers}

The table below lists the registers of the software MAC, without the instance prefix (`ethmac_` in
LiteX). [Registers](#registers) gives their addresses and fields.

| Register | Access | Meaning |
|--------------------------------------------|----------|----------------------------------------|
| `sram_writer_slot` | R | Slot of the oldest received frame |
| `sram_writer_length` | R | Its length in bytes |
| `sram_writer_errors` | R | Received frames dropped |
| `sram_writer_timestamp` | R | Its timestamp (with `timestamp`) |
| `sram_writer_ev_*` | R/W | Event `available`: status, pending, enable |
| `sram_reader_start` | W | Queue the command in `slot` and `length` |
| `sram_reader_ready` | R | Command FIFO has room |
| `sram_reader_level` | R | Commands waiting |
| `sram_reader_slot` | R/W | Slot of the next command |
| `sram_reader_length` | R/W | Length of the next command in bytes |
| `sram_reader_timestamp_slot` | R | Slot of the oldest sent frame (with `timestamp`) |
| `sram_reader_timestamp` | R | Its timestamp (with `timestamp`) |
| `sram_reader_ev_*` | R/W | Event `done`: status, pending, enable |
| `preamble_crc` | R | 1 when preamble and CRC are handled by the MAC |
| `rx_datapath_preamble_errors` | R | Preamble errors |
| `rx_datapath_crc_errors` | R | CRC errors |
| `rx_datapath_drops` | R | Frames dropped by the RX store-and-forward FIFO |

The two events share one interrupt line. The counters only exist when their stage does: no preamble
or CRC counter when the PHY handles them, no drop counter without store-and-forward.

## Hybrid mode {#mac-hybrid}

In `hybrid` mode the hardware stack and a CPU share one MAC, each with its own MAC address. LiteX
builds it with `add_etherbone(with_ethmac=True)`: Etherbone answers on one address while Linux or
the BIOS uses the software MAC on the other. The figure below shows the paths.

![Hybrid MAC. Follow the RX path at the top: the address filter sends frames for the hardware
address and group frames to the hardware stack, and all but the local frames to the CPU slots. On
TX, the arbiter interleaves whole frames of both.](mac_hybrid.svg)

The filter reads the destination address of each received frame and compares it with the hardware
address (`hw_mac`):

| Destination | Hardware stack | CPU |
|----------------------------------------|----------------|-----|
| The hardware address | yes | no |
| Broadcast `ff:ff:ff:ff:ff:ff` | yes | yes |
| IPv4 multicast `01:00:5e:xx:xx:xx` | yes | yes |
| IPv6 multicast `33:33:xx:xx:xx:xx` | yes | yes |
| Any other address | no | yes |

A frame for both paths is accepted when both can take it. Without `hw_mac`, every frame goes to
both. On transmit, an arbiter grants whole frames, the CPU first when both wait.

In LiteX, the CPU interface keeps the `ethmac` name and registers, and the BIOS does not reset the
PHY (`ETH_PHY_NO_RESET`), so that it does not cut the hardware stack.

## PHY attributes {#mac-phy-attributes}

The MAC reads a few attributes of the PHY object to adapt its paths. A PHY sets them as class or
instance attributes; the table below lists them.

| Attribute | Effect | Set by |
|-------------------------------------------|-------------------------------------|----------------------------|
| `dw` | PHY stream width | Every PHY |
| `tx_clk_freq`, `rx_clk_freq` | Pipelining and store-and-forward heuristics | Every PHY |
| `with_preamble_crc` | `False`: no preamble or CRC stages | Simulation model, AXI-Stream adapter |
| `with_padding` | `False`: no padding stages | AXI-Stream adapter |
| `integrated_ifg_inserter` | `True`: no gap stage | XGMII PHY, AXI-Stream adapter (not the BASE-R PHYs) |
| `tx_gap_cycles` | Gap length in `eth_tx` cycles, constant or signal | ECP5 RGMII PHY |
| `with_store_and_forward` | Forces the store-and-forward choice | AXI-Stream adapter |

## AXI-Stream adapter for hard MACs {#mac-axis}

Some FPGAs have a hard MAC with its own PCS, such as the 100G CMAC of UltraScale+ devices. The
adapter `LiteEthMACAXIStream` presents such a MAC to `LiteEthMAC` as if it were a PHY.

The hard MAC then owns the FCS, the preamble and the gap; the adapter sets the attributes of the
table above so that the MAC core adds none of them.

| Item | Value |
|----------------------|-------------------------------------------------------------------|
| Widths | 64, 128, 256 or 512 bits (512 by default) |
| Clock | `eth_tx` and `eth_rx`, 322.265625 MHz by default |
| AXI-Stream ports | `tx` and `rx`, `tkeep` from `be`, `tuser` as the frame error |
| TX | Pads to 60 bytes, holds each frame whole, then sends it in one burst |
| RX | No backpressure; a drop FIFO discards bad and oversized frames whole |
| Counters | `rx_packets`, `rx_drops`, `rx_bad_frames` (signals, not registers) |

On transmit, the adapter flags a frame with a malformed mask or a lane error by setting `tuser` on
its last beat. On receive, it discards a frame flagged by the hard MAC, with a malformed mask,
longer than the MTU or shorter than 60 bytes.

Each queue must hold one frame of the MTU, which the constructor checks. The 100G path built on it
is in development; see [BASE-R PHYs](#baser-phys).

## Parameters {#mac-parameters}

| Parameter | Default | Meaning |
|--------------------------------------|-------------------|-----------------------------------------|
| `dw` | (required) | Core width, a multiple of 8 |
| `interface` | `"crossbar"` | `crossbar`, `wishbone` or `hybrid` |
| `endianness` | `"big"` | Byte order of the SRAM slots |
| `with_preamble_crc` | `True` | Preamble and CRC stages (a PHY attribute overrides it) |
| `nrxslots`, `ntxslots` | 2, 2 | Number of RX and TX slots |
| `rxslots_read_only` | `True` | RX slots read-only on the bus |
| `txslots_write_only` | `False` | TX slots write-only on the bus |
| `hw_mac` | `None` | Hardware address of the hybrid filter |
| `timestamp` | `None` | Signal recorded per frame |
| `full_memory_we` | `False` | Split slot memories into 8-bit blocks |
| `with_sys_datapath` | `False` | Framing in `sys` at the core width |
| `tx_cdc_depth` | 32 | Depth of the TX clock crossing |
| `rx_cdc_depth` | 32 | Depth of the RX clock crossing |
| `tx_cdc_buffered` | `False` | Registered TX crossing output |
| `rx_cdc_buffered` | `False` | Registered RX crossing output |
| `eth_mtu` | 1530 | Largest frame in bytes, without preamble and FCS |
| `rx_fifo_depth` | 0 | Frames buffered before the RX slots |
| `tx_fifo_depth` | 0 | Frames buffered after the TX slots |
| `with_store_and_forward` | `"auto"` | PHY-side whole-frame FIFOs |
| `sys_clk_freq` | `None` | `sys` clock for the `"auto"` heuristic, from the SoC when absent |

The core classes pass their own values: `LiteEthIPCore` and `LiteEthUDPIPCore` enable buffered
crossings by default and set `hw_mac` to the core's MAC address.

## Limitations {#mac-limits}

- No VLAN tag handling and no pause frames: a tagged frame reaches the crossbar with EtherType
  `0x8100` and is dropped there.
- In `crossbar` mode the MAC has no destination address filter. The hardware stack receives every
  frame; ARP checks the target IP address, and IP checks the destination only with
  `with_ip_broadcast=False`.
- In `crossbar` mode the MAC exposes no registers: the error and drop counters are only visible
  in `wishbone` and `hybrid` modes.
- The gap counts `eth_tx` cycles, not bytes on the wire, and the MII, RMII and GMII/MII (in MII
  mode) PHYs set no `tx_gap_cycles`. The gap on the wire is then 48 bit times on MII, 24 bit times
  on RMII at 100 Mbps and 2.4 bit times on RMII at 10 Mbps, instead of 96. A receiver can merge
  back-to-back frames at 10 Mbps on RMII.
- The BASE-R PHYs get the gap stage of the MAC on top of the gap of their XGMII encoder, so their
  gaps are longer than needed ([5G, 10G and 25G BASE-R, and 100G](#baser-phys)).

## In the code {#mac-code}

| File | Main classes | Role |
|-----------------------------|-------------------------------------------------------|------------------------|
| `mac/__init__.py` | `LiteEthMAC`, `LiteEthMACCoreCrossbar` | Top level, hybrid filter and arbiter |
| `mac/core.py` | `LiteEthMACCore`, `LiteEthMACCDC` | TX and RX paths |
| `mac/common.py` | `LiteEthMACCrossbar`, packetizers | MAC header, crossbar |
| `mac/crc.py` | `LiteEthMACCRC32Inserter`, `LiteEthMACCRC32Checker` | FCS |
| `mac/preamble.py` | `LiteEthMACPreambleInserter`, `LiteEthMACPreambleChecker` | Preamble and SFD |
| `mac/padding.py` | `LiteEthMACPaddingInserter`, `LiteEthMACPaddingChecker` | Minimum length |
| `mac/gap.py` | `LiteEthMACGap` | Inter-frame gap |
| `mac/sram.py` | `LiteEthMACSRAMWriter`, `LiteEthMACSRAMReader` | Slots, events |
| `mac/packet.py` | `LiteEthMACPacketWriter`, `LiteEthMACPacketReader` | Slot bounds |
| `mac/wishbone.py` | `LiteEthMACWishboneInterface` | Slot buses |
| `mac/axis.py` | `LiteEthMACAXIStream` | Hard MAC adapter |

Where to go next: [ARP, IP, ICMP, UDP, DHCP and IGMP](#ip-stack) for the layers above the MAC,
[MII, RMII, GMII, RGMII, XGMII and 10BASE-T](#parallel-phys) for the PHYs below it,
[Debugging](#debugging) for the counters.
