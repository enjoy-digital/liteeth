# MAC TX Token Bucket Hardware Implementation

This document describes the actual digital hardware implementation of
[`liteeth/mac/rate_limiter.py`](/mnt/WORKSPACE/Workspaces/litecompute/liteeth/liteeth/mac/rate_limiter.py)
as it is integrated by
[`liteeth/mac/core.py`](/mnt/WORKSPACE/Workspaces/litecompute/liteeth/liteeth/mac/core.py).

The focus here is hardware structure: math, signal meaning, state transitions,
cycle-level behavior, and why each implementation decision was taken.

## 1. Placement In The MAC TX Pipeline

The limiter is inserted in the TX datapath after all width conversion / CDC and
after optional MAC-side framing stages, but before IFG insertion or the PHY.

Common placement:

```text
core.sink
  -> CDC / width conversion
  -> padding
  -> CRC inserter
  -> preamble inserter
  -> token bucket limiter
  -> gap inserter
  -> PHY
```

If the PHY provides its own IFG insertion:

```text
core.sink
  -> CDC / width conversion
  -> padding
  -> CRC inserter
  -> preamble inserter
  -> token bucket limiter
  -> PHY
```

Why this placement:
- The limiter always runs in `eth_tx`.
- The limiter always sees PHY-width words.
- The limiter measures transmitted stream byte count directly, not a pre-conversion approximation.
- IFG waveform generation stays in the gap stage or PHY; limiter shaping is still frame-boundary based.
- Even though the limiter is placed before the explicit IFG inserter, its worst-case reservation uses `eth_mtu = 1530`, which in this codebase is defined as the total per-frame TX charge budget including IFG.

## 2. Top-Level Interface

The module is:

```python
LiteEthMACTokenBucket(dw, rate=0, burst=None)
```

Stream interface:

```text
sink   : eth_phy_description(dw)
source : eth_phy_description(dw)
```

Configuration interface:

```text
enable : 1 bit   internal eth_tx-domain enable latch
rate   : 32 bits Q16.16 bytes / eth_tx cycle
burst  : 32 bits bytes
```

CSR interface:

```text
_enable : CSRStorage(1)
_rate   : CSRStorage(32)
_burst  : CSRStorage(32)
```

## 3. Core Design Decision

The implementation is intentionally frame-gated and non-buffering.

That means:
- It can stop a frame before the first word.
- It must not create a bubble once a frame has started.
- It therefore reserves against a worst-case start threshold, then charges the exact observed frame size at frame end.

This is why the module does not have a separate packet buffer and does not try
to admit a frame only after knowing its exact length. That would require
storing the whole frame or precomputing the exact length upstream.

## 4. Token Math

### 4.1 Units

Tokens are stored in Q16.16 fixed-point bytes.

```text
1 byte = 0x0001_0000 tokens
```

So:

```text
tokens               : 48-bit Q16.16 accumulator
rate                 : 32-bit Q16.16 bytes/cycle
charge_tokens        = frame_charge << 16
effective_burst_q16  = effective_burst << 16
```

### 4.2 Maximum Reservation Constant

The implementation defines:

```text
max_frame_charge = eth_mtu
```

In this codebase, `eth_mtu` is already treated as the full TX charge budget
used by the MAC, including IFG. This matters because the limiter is placed
before the IFG stage in the TX pipeline, so the reservation constant must
already include that downstream gap cost. The limiter therefore must not add
IFG / preamble / FCS on top of it again when building the worst-case
reservation threshold.

With current constants:

```text
eth_mtu             = 1530

max_frame_charge = 1530 bytes
```

This is the frame-start threshold and minimum effective burst.

Important consequence:
- `frame_can_start` uses `max_frame_charge`, not the exact upcoming frame size.
- Therefore every frame start requires enough tokens for the worst-case frame.

This is the key hardware simplification that guarantees no mid-frame stall.

### 4.3 Exact Frame Charge

Once a frame is actually transmitted, the exact charge is:

```text
frame_charge = observed_frame_bytes
```

`observed_frame_bytes` is measured directly from accepted output beats
(`source.valid & source.ready`) using `last_be` on the final beat.

No additional overhead term is added by the limiter itself.

### 4.4 Burst Clamp

The configured burst is clamped upward:

```text
effective_burst = max(burst, max_frame_charge)
```

So even if software programs a smaller burst, the hardware internally forces it
to at least one `eth_mtu` reservation.

Why:
- Without that clamp, a legal configuration could make `frame_can_start`
  permanently false.
- The clamp preserves the "one whole frame can always start" rule.

### 4.5 Refill Equation

Each `eth_tx` cycle:

```text
refilled_tokens =
    effective_burst_q16                      if tokens + rate >= effective_burst_q16
    tokens + rate                            otherwise
```

This is a saturating accumulator.

### 4.6 End-Of-Frame Debit

On the final transmitted beat:

```text
tokens_next = refilled_tokens - (frame_charge << 16)
```

Notably:
- refill and debit happen in the same cycle
- debit uses the exact observed frame length

## 5. Byte Counting Logic

The limiter counts bytes from the outgoing stream itself.

### 5.1 Last Word Byte Decode

`last_be` is decoded into `last_bytes`:

```text
last_be == 1 << 0  -> 1 byte valid
last_be == 1 << 1  -> 2 bytes valid
last_be == 1 << 2  -> 3 bytes valid
...
default            -> byte_width bytes valid
```

For non-final beats:

```text
beat_bytes = byte_width
```

For the final beat:

```text
beat_bytes = last_bytes
```

### 5.2 Running Length

The implementation uses:

```text
byte_count           : bytes already accepted before current beat
current_frame_bytes  = byte_count + beat_bytes
frame_charge         = current_frame_bytes
```

Update rule:

```text
if transfer and last:
    byte_count <- 0
elif transfer:
    byte_count <- current_frame_bytes
```

So `byte_count` is the running byte length of the in-flight frame.

## 6. Clock Domain Crossing

The limiter datapath itself is in `eth_tx`.

CSR crossing strategy:

```text
_enable -> MultiReg -> csr_enable
_rate   -> BusSynchronizer(sys -> eth_tx)
_burst  -> BusSynchronizer(sys -> eth_tx)
```

Why the split:
- `enable` is one bit, so a simple `MultiReg` is enough.
- `rate` and `burst` are 32-bit control words and need coherent transfer, so
  `BusSynchronizer` is used.

### 6.1 Configuration Commit Policy

The live control signals are only updated in `IDLE`:

```text
if fsm.ongoing("IDLE"):
    enable <- csr_enable
    rate   <- rate_cdc.o
    burst  <- burst_cdc.o
```

This is an important implementation decision:
- no configuration change can occur in the middle of an active frame
- all control updates are naturally frame-boundary safe

## 7. Handshake And Control Signals

Important internal signals:

```text
transfer              = source.valid & source.ready
frame_can_start       = (tokens >= (max_frame_charge << 16)) | ~enable
effective_burst       = max(burst, max_frame_charge)
effective_burst_q16   = effective_burst << 16
charge_tokens         = frame_charge << 16
```

Data path wiring:

```text
sink.connect(source, omit={"ready", "valid"})
```

This means payload, `last`, `last_be`, and `error` pass combinationally from
input to output. Only `valid` and `ready` are controlled by the limiter FSM.

## 8. State Machine

The implementation is a 2-state FSM in `eth_tx`.

There is no explicit `WAIT` state. The waiting behavior is folded into `IDLE`
through `frame_can_start`.

### 8.1 State List

```text
IDLE
SEND
```

### 8.2 IDLE

Behavior:

```text
source.valid = sink.valid & frame_can_start
sink.ready   = source.ready & frame_can_start
```

Meaning:
- if limiter disabled: `frame_can_start = 1`, so this behaves as a pass-through
- if limiter enabled and tokens are insufficient: both `source.valid` and
  `sink.ready` are held low for the first beat

Transition:

```text
if transfer and not source.last:
    IDLE -> SEND
```

If the frame is a single-beat frame, it remains in `IDLE`.

### 8.3 SEND

Behavior:

```text
source.valid = sink.valid
sink.ready   = source.ready
```

Meaning:
- once a frame has started, the limiter becomes fully transparent
- no extra throttling is inserted during the body of the frame

Transition:

```text
if transfer and source.last:
    SEND -> IDLE
```

### 8.4 ASCII FSM Diagram

```text
                 +----------------------------------+
                 |                                  |
                 |  transfer & source.last          |
                 |                                  v
        +-----------------+                 +-----------------+
        |      IDLE       |                 |      SEND       |
        |-----------------|                 |-----------------|
        | source.valid =  |                 | source.valid =  |
        | sink.valid &    |                 | sink.valid      |
        | frame_can_start |                 |                 |
        |                 |                 | sink.ready =    |
        | sink.ready =    |                 | source.ready    |
        | source.ready &  |                 |                 |
        | frame_can_start |                 |                 |
        +-----------------+                 +-----------------+
                 |                                  ^
                 | transfer & ~source.last          |
                 +----------------------------------+
```

## 9. Cycle-Level Timing

### 9.1 Disabled Mode

In disabled mode, `frame_can_start = 1` because of `| ~enable`.

```text
clk        : __/--\__/--\__/--\__/--\__/--\__/--\__
enable     : 0--------------------------------------
sink.valid : 0____/===========================\_____
sink.ready : 1--------------------------------------
source.valid:0____/===========================\_____
source.ready:1--------------------------------------
state      : IDLE.....SEND....................IDLE..
```

Behavior:
- no throttling
- tokens are forced to full burst every cycle

### 9.2 Enabled, Not Enough Tokens At Start

```text
clk         : __/--\__/--\__/--\__/--\__/--\__/--\__/--\__
enable      : 1--------------------------------------------
tokens>=thr : 0-----------0-----------1---------------------
sink.valid  : ____/=====================================\___
sink.ready  : 0-----------0-----------1=====================
source.valid: 0-----------0-----------1=====================
source.ready: 1---------------------------------------------
state       : IDLE..................................SEND....
```

Behavior:
- first beat is backpressured until enough tokens exist
- frame starts only when threshold is met

### 9.3 Enabled, Frame In Progress

```text
clk         : __/--\__/--\__/--\__/--\__/--\__/--\__
state       : IDLE..SEND..SEND..SEND..SEND..IDLE....
sink.valid  : ____/===============================\__
sink.ready  : ____/===============================\__
source.valid: ____/===============================\__
source.ready: 1--------------------------------------
transfer    : ____/===============================\__
byte_count  : 0 -> b1 -> b2 -> b3 -> reset to 0
tokens      : refill, refill, refill, refill-debit
```

Behavior:
- no internal bubble once `SEND` has been entered
- exact debit happens on the last beat cycle

### 9.4 Two Back-To-Back Frames, Rate-Limited

```text
Frame A ends, debit applied, tokens drop below threshold

clk          : __/--\__/--\__/--\__/--\__/--\__/--\__/--\__/--\__
frame A      : .........[==================A==================]....
frame B req  : ................................[==== held ====][B].
state        : IDLE..SEND.................IDLE...........IDLE..SEND
tokens       : high........debit->low.....refill...refill...enough
frame_can_start
             : 1................0...................0.......1......
sink.ready   : 1 for A..........0 for first beat of B.......1......
source.valid : pass A...........0 while blocked.............1......
```

Behavior:
- frame B can be presented by upstream
- limiter blocks only the first beat
- frame B begins only after token refill reaches threshold

## 10. Why The Math Uses Worst-Case Start Threshold

This is the central hardware tradeoff.

If the limiter started a frame with fewer than worst-case tokens, it would need
one of these mechanisms:
- mid-frame stall
- full-frame buffering
- a trusted upstream exact-length sideband

The current implementation chooses none of those.

So it uses:

```text
start condition = tokens >= eth_mtu
```

This gives:
- simple hardware
- no extra RAM
- no mid-frame stall
- deterministic frame-boundary throttle only

The cost is:
- short frames are admitted conservatively
- burst utilization is less precise than a buffered exact-length shaper

## 11. Reset / Initialization Behavior

Initial token value:

```text
tokens reset = burst << 16
```

If `burst is None`, constructor logic replaces it with:

```text
burst = max_frame_charge
```

While disabled:

```text
tokens <- effective_burst_q16
```

every `eth_tx` cycle.

This means:
- disabling the limiter refills the bucket to full
- enabling later starts from a full burst budget

## 12. Practical Notes For Hardware Review

### 12.1 Datapath Latency

The limiter adds no payload buffering and no word reformatting.
Its effect is only:
- start-of-frame gating in `IDLE`
- transparent pass-through in `SEND`

### 12.2 Critical Logic

The likely timing-relevant combinational logic is:
- `effective_burst = max(burst, max_frame_charge)`
- saturating refill compare
- `frame_can_start` threshold compare
- `last_be` decode

At current widths this is small:
- 32-bit compare for burst clamp
- 48-bit compare/add for tokens
- small decode for `last_be`

### 12.3 Safe Reconfiguration

Because live config latches only in `IDLE`, software should program:

```text
1. burst
2. rate
3. enable
```

at startup.

This is not a functional requirement for steady-state use, but it is the clean
software sequence because the live config only commits on frame boundaries.

## 13. Summary

The implemented limiter is a compact frame-gated token bucket with:
- Q16.16 token accounting
- exact end-of-frame debit
- worst-case start reservation
- no mid-frame bubbles
- coherent `sys -> eth_tx` CSR synchronization
- frame-boundary-safe config updates

In short:

```text
Throttle only before the first beat.
Pass through every beat once the frame starts.
Debit observed frame bytes at frame end.
Refill every eth_tx cycle with saturation.
```
