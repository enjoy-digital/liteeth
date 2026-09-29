# PTP core organization

`liteeth.core.ptp.LiteEthPTP` integrates the UDP/IPv4 PTP slave. Existing imports
of the top level, component classes, constants and packet description continue
to work from `liteeth.core.ptp`.

| Module | Responsibility |
| --- | --- |
| `common.py` | Wire header, message constants and stream description |
| `clock.py` | Time stamping unit and receive timestamp latching |
| `packet.py` | UDP packet construction and receive parsing |
| `servo.py` | Pipelined offset and frequency correction |
| `control.py` | Master tracking and E2E/P2P protocol state machines |
| `__init__.py` | Integration, public exports and top-level CSRs |

The packet helpers exchange decoded fields with protocol control. Control
selects timestamp pairs and initiates servo updates; the servo applies phase
and frequency corrections to the TSU. Timestamp capture stays separate from
packet parsing so receive timing is tied to the incoming stream.

All components run in the enclosing system clock domain. This organization
does not change timestamp capture latency, servo arithmetic, pipeline stages,
CSR names or the supported transport and delay mechanisms. The core supports
slave mode over UDP/IPv4, with E2E or P2P delay measurement; it does not implement
PTP master or boundary clock operation or Layer 2 transport.
