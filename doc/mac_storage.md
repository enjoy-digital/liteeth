# MAC packet storage bounds

The packet writer accepts at most `min(eth_mtu, depth*dw/8)` bytes per packet.
It checks the next beat before exposing a write, so a beat beyond the capacity
cannot wrap a SRAM address and overwrite earlier data. Oversized packets drain
through `last`, report a drop/error, and leave the writer ready for the next
packet. Earlier writes from the rejected packet are not committed as an RX slot.

SRAM slot counts must be positive. Slot indices use enough bits for all slots
and the writer wraps explicitly at `nslots - 1`, including one-slot and
non-power-of-two configurations. The Wishbone decoder uses the same slot-index
width. Existing power-of-two configurations retain their CSR widths and slot
addresses; configurations that previously could not address all their slots
now receive the necessary index bits. CSR names and ordering are unchanged.

TX lengths must be nonzero and fit the configured storage. The SRAM reader also
checks that the requested slot exists. An invalid command is retired with the
normal completion indication but emits no frame and does not read memory.
No new error CSR is added. A timestamped invalid command reports timestamp zero;
software should validate commands before submission.

When timestamping is enabled, completion becomes visible only after its status
has been stored. If software leaves the timestamp-status FIFO full, command
retirement waits for space rather than losing the next completion. Software
continues to use the existing ready, level and event CSRs to manage the queue.
