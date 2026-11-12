# tc_frame_decoder

## Purpose

Turns a received bit stream into parsed TC transfer frames (CCSDS
231.0-B / 232.0-B) as one hierarchical block: the stock
`digital.correlate_access_code_tag_bb` on the CLTU start sequence,
`cltu_deframer`, `ccsds_receiver`, the stock `digital.crc_check` (FECF)
and `ccsds_reader`. The RX counterpart of
[tc_frame_encoder](tc_frame_encoder.md). GRC label **TC Frame Decoder**,
category `[Soarr]/TC (Telecommand)/RX`. See
[ADR-0007](../adr/0007-grc-block-tree-and-labels.md) for the hierarchical
block rule.

## Pipeline position

RX chain, from the demodulator's bits to the RX key lookup:

```
(binary slicer: one bit per byte) → tc_frame_decoder (stream in)
tc_frame_decoder.out → key_database (role "rx").in
```

The individual blocks stay in the tree for debugging and custom chains.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input (stream) | `uint8` items, one bit per item (`0`/`1`), unpacked. | — |
| `out` | output | PDU: `(metadata_dict . data_field_u8vector)` as `ccsds_reader` publishes it — the parsed TC header (`telecommand.tc_header`), the SDLS header and trailer (`sdls.*`), the encapsulation header where it was parsed (see [ccsds_reader.md](ccsds_reader.md)). | `pmt.cons({telecommand: {tc_header: {scid: 341, vcid_counter: 0, …}}, sdls: {…}}, u8vector(data))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `threshold` | int | `1` | Bit errors allowed in the 16-bit CLTU start sequence (and in the tail), for **both** the correlator and `cltu_deframer` — one value, so the two can't disagree. `0`–`16`, otherwise `ValueError`. |
| `scid_filter_enable`, `scid` | bool, int | `False`, `0` | Keep only frames of this SCID (see [ccsds_receiver.md](ccsds_receiver.md)); `scid` outside 10 bits raises. |
| `vcid_filter_enable`, `vcid` | bool, int | `False`, `0` | Keep only frames of this VCID; `vcid` outside 6 bits raises. |
| `sdls_mode` | int | `3` | `0` = no SDLS, `1` = encryption only, `2` = authentication only, `3` = both — `ccsds_reader`'s `sdls_type`, and the same values as `sdls_apply`/`sdls_process`. Outside `0`–`3` raises. |
| `encapsulation_used` | bool | `True` | Frames carry an encapsulation packet (see [ccsds_reader.md](ccsds_reader.md) for when it's parsed here and when after decryption). |

Built in, as CCSDS fixes them: the access code (`cltu_deframer`'s
default start sequence `EB90`, so correlator and deframer search for the
same pattern), the CLTU tail, the 8-byte codeword, `max_codewords`, the
BCH code and randomizer inside `ccsds_receiver`, and the FECF CRC
(`tc_frame_encoder.FECF_CRC`, shared with TX). Only `crc_check`'s `ok`
output is wired, so a frame failing the FECF check is dropped.

## Behavior / edge cases / current error handling

The block adds no handler of its own, only the wiring; every drop rule is
the inner blocks' own (see their PRDs). The inner blocks are reachable as
`correlator`, `deframer`, `receiver`, `crc` and `parser`.

## CCSDS reference

CCSDS 231.0-B-4 (TC Synchronization and Channel Coding) — CLTU, BCH
(63,56), randomizer; CCSDS 232.0-B-4 (TC Space Data Link Protocol) —
primary header and FECF.

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_tc_frame_decoder.py` — 4 test methods (`test_001`–`test_004`),
  each feeding a running bit stream (CLTUs repeated between carrier-idle
  bits, throttled, so the flowgraph doesn't shut down before the message
  hops have run): the TC Frame Encoder's CLTU decoded back to its data
  field, header fields included (`test_001`), a frame with a wrong FECF
  but valid codewords never coming out while a good one does
  (`test_002`), the closed loop from hierarchical blocks —
  `key_database` → `encapsulation_header` → `sdls_apply` →
  `tc_frame_encoder` → bits → this block → `key_database` (RX) →
  `sdls_process` → `encapsulation_parser` — returning the original
  payload (`test_003`), and an out-of-range threshold, SDLS mode or VCID
  raising (`test_004`).
