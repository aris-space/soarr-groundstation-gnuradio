# cltu_burst_builder

## Purpose

Wraps each CLTU into one transmission burst for CCSDS 231.0-B PLOP-1
operation, where the carrier is on only while transmitting: acquisition
sequence + CLTU + short idle tail. Together with GNU Radio's PDU to
Tagged Stream block it replaces the continuous
[acquisition_idle_sequencer](acquisition_idle_sequencer.md) stream,
removing its latency. See [architecture.md](../architecture.md).

## Pipeline position

TX chain, after `cltu_framer`, in burst mode:

```
cltu_framer.out → cltu_burst_builder.in
cltu_burst_builder.out → pdu_to_tagged_stream.pdus (stock, length tag packet_len)
pdu_to_tagged_stream → modulator → (tagged_stream_multiply_length) → USRP sink (Length Tag Name packet_len)
```

Wired this way in `examples/tc_loopback_sim.grc`, where the stream goes
to the receiver directly instead of a modulator. For a USRP,
[plop_modulator](plop_modulator.md) replaces this block, PDU to Tagged
Stream, and the modulator, and adds switchable PLOP-2.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . cltu_u8vector)`, one CLTU. | `pmt.cons({}, u8vector(eb 90 ‖ codewords ‖ tail))` |
| `out` | output | PDU with the same metadata and payload `acquisition sequence ‖ CLTU ‖ idle tail`. | `pmt.cons({}, u8vector(16 × 0xAA ‖ CLTU ‖ 4 × 0xAA))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `acquisition_length` | int | `16` | Acquisition sequence length in bytes, sent before every CLTU so the receiver can lock on; size it to the on-board receiver's acquisition time. `0` = none. Raises `ValueError` if negative. |
| `tail_length` | int | `4` | Idle bytes after the CLTU, so the modulator's pulse-shaping filter flushes the last symbols before the burst ends. `0` = none. Raises `ValueError` if negative. |
| `fill_byte` | int | `0xAA` | Byte used for the acquisition sequence and the tail; `0xAA` is alternating bits. Raises `ValueError` outside `0`–`255`. |
| `diff_encoded` | bool | `False` | If `True`, use `0xFF` instead of `fill_byte`: a differential modulator turns it into alternating symbols. Same convention as `acquisition_idle_sequencer`. |

## Behavior / edge cases / current error handling

**Burst** (`build_burst`): concatenates the precomputed acquisition
bytes, the CLTU, and the precomputed tail, and publishes the result with
the input metadata. One CLTU in, one burst out; the block keeps no state
between messages.

**Why it removes the latency**: the stock `pdu_to_tagged_stream`
produces stream items only when a burst arrives, so the stream carries
bursts and nothing between them. With the continuous sequencer, idle
bytes fill every buffer up to the SDR and each new CLTU waits behind
them (seconds at 10 kbit/s); here a CLTU reaches the SDR as soon as it is
built. The USRP sink switches the transmitter on for each tagged burst
and off after it, which also frees the TX/RX port for receiving between
bursts.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `build_burst`'s
full body is wrapped in catch-log-drop, logged at `error` (TX side, not
raw RF). A non-pair message, a non-u8vector payload, or an empty CLTU is
dropped.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)):
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`build_burst`.

**Naming**: file, class, GRC block-id, every parameter, and the handler
method are snake_case.

## CCSDS reference

CCSDS 231.0-B (TC Synchronization and Channel Coding), physical layer
operations procedures: PLOP-1 switches the carrier off between
transmissions, and each transmission starts with an acquisition sequence
of alternating ones and zeros; PLOP-2 keeps the carrier on, with idle
sequences between CLTUs. This block implements the PLOP-1 transmission
unit; `acquisition_idle_sequencer` the PLOP-2 stream.

## Known issues / TODOs

None currently. Burst transmission over a real USRP is verified in the
lab, not by the test suite.

## Test coverage

- `python/soarr/qa_cltu_burst_builder.py` — 9 test methods
  (`test_instance` + `test_001`–`test_008`): default burst layout
  (`test_001`), configurable acquisition and tail lengths including `0`
  (`test_002`), `diff_encoded` using `0xFF` (`test_003`), metadata passed
  through (`test_004`), invalid parameters raising (`test_005`), malformed
  input dropped (`test_006`), a publish failure caught (`test_007`), and
  a real flowgraph with GNU Radio's `pdu_to_tagged_stream` whose stream
  holds exactly the bursts, each with a correct `packet_len` tag and
  nothing between them (`test_008`).
