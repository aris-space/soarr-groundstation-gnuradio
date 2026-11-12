# cltu_burst_builder

## Purpose

Wraps each PDU into one transmission burst, where the carrier is on
only while transmitting: acquisition sequence + PDU + short idle tail. On
TC the PDU is a CLTU (CCSDS 231.0-B PLOP-1); together with GNU Radio's
PDU to Tagged Stream block it replaces the continuous
[acquisition_idle_sequencer](acquisition_idle_sequencer.md) stream,
removing its latency. On TM it is a CADU from
[tm_cadu_framer](tm_cadu_framer.md): CCSDS 131.0-B allows no bits between
consecutive CADUs (9.4.2), so a preamble belongs to the burst, not the
CADU; the CADUs of one packet, marked by
[tm_frame_generator](tm_frame_generator.md), go out together as one
burst. GRC label **Burst Builder**, category `[Soarr]/Common/Burst`; the
id stays `soarr_cltu_burst_builder`. See [architecture.md](../architecture.md).

## Pipeline position

TX chain, in burst mode — on TC after `cltu_framer` (or
`tc_frame_encoder`), on TM after `tm_cadu_framer`:

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
| `in` | input | PDU: `(metadata_dict . u8vector)`, one CLTU (TC) or CADU (TM). Metadata `burst_more` = `True`: more PDUs of this burst follow. | `pmt.cons({}, u8vector(eb 90 ‖ codewords ‖ tail))` |
| `out` | output | PDU with payload `acquisition sequence ‖ PDUs ‖ idle tail` and the metadata of the burst's last PDU, `burst_more` removed. | `pmt.cons({}, u8vector(64 × 0xAA ‖ CLTU ‖ 4 × 0xAA))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `acquisition_length` | int | `64` | Acquisition sequence (preamble) length in bytes, sent before every burst so the receiver can lock on; size it to the on-board receiver's acquisition time. `0` = none. Raises `ValueError` if negative. |
| `tail_length` | int | `4` | Idle bytes after the PDU, so the modulator's pulse-shaping filter flushes the last symbols before the burst ends. `0` = none. Raises `ValueError` if negative. |
| `fill_byte` | int | `0xAA` | Byte used for the acquisition sequence and the tail; `0xAA` is alternating bits. Raises `ValueError` outside `0`–`255`. |
| `diff_encoded` | bool | `False` | If `True`, use `0xFF` instead of `fill_byte`: a differential modulator turns it into alternating symbols. Same convention as `acquisition_idle_sequencer`. |
| `max_burst_pdus` | int | `64` | Most PDUs in one burst. Reached while `burst_more` is still set, the held PDUs go out as a burst and the rest start the next one. Raises `ValueError` below `1`. |

## Behavior / edge cases / current error handling

**Burst** (`build_burst`): concatenates the precomputed acquisition
bytes, the PDU, and the precomputed tail, and publishes the result with
the input metadata. A PDU without `burst_more` (or with it `False`) and
nothing held gives one burst, its metadata unchanged — every TC CLTU.

**Joined bursts**: a PDU with `burst_more` = `True` is held instead.
The next PDU without it closes the burst: acquisition bytes, all held
PDUs and that PDU back to back, tail — one burst with the closing PDU's
metadata, `burst_more` removed. On TM these are the CADUs of one packet,
whose frames `tm_frame_generator` marks. If `max_burst_pdus` PDUs are
held and the marker is still set, they go out as a burst, logged at
`warn`, and the PDUs after them start the next burst; nothing is held
forever. A marker that is not a PMT boolean counts as absent.

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
raw RF). A non-pair message, a non-u8vector payload, or an empty PDU is
dropped; PDUs already held stay held. If building or publishing a burst
fails, its held PDUs are lost with it, and the next PDU starts a new
burst.

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

- `python/soarr/qa_cltu_burst_builder.py` — 15 test methods
  (`test_instance` + `test_001`–`test_014`): default burst layout
  (`test_001`), configurable acquisition and tail lengths including `0`
  (`test_002`), `diff_encoded` using `0xFF` (`test_003`), metadata passed
  through (`test_004`), invalid parameters raising (`test_005`), malformed
  input dropped (`test_006`), a publish failure caught (`test_007`), and
  a real flowgraph with GNU Radio's `pdu_to_tagged_stream` whose stream
  holds exactly the bursts, each with a correct `packet_len` tag and
  nothing between them (`test_008`); PDUs marked `burst_more` joined into
  one burst, closed by `False` or a missing marker (`test_009`), the
  burst carrying the last PDU's metadata without the marker (`test_010`),
  `max_burst_pdus` sending a burst while the marker is still set
  (`test_011`), a malformed PDU leaving the held burst intact
  (`test_012`), a publish failure clearing it (`test_013`), and a packet
  through `tm_frame_generator` and `tm_cadu_framer` leaving as one burst
  of three CADUs (`test_014`).
