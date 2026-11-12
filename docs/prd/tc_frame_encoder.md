# tc_frame_encoder

## Purpose

Turns a TC transfer frame's data field into a CLTU (CCSDS 232.0-B /
231.0-B) as one hierarchical block: `tc_primary_header`, the stock
`digital.crc_append` (FECF), `lfsr_scrambler`, `bch_encoder` and
`cltu_framer`, wired in that fixed order. GRC label **TC Frame Encoder**,
category `[Soarr]/TC (Telecommand)/TX`. See
[ADR-0007](../adr/0007-grc-block-tree-and-labels.md) for the hierarchical
block rule.

## Pipeline position

TX chain, in place of the five blocks:

```
sdls_apply.out → tc_frame_encoder.in
tc_frame_encoder.out → plop_modulator.in (or cltu_burst_builder.in)
```

The individual blocks stay in the tree for debugging and custom chains.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . data_field_u8vector)`, as `tc_primary_header` takes it: `vcid_counter` (the frame sequence number) required, `scid`/`vcid`/`bypass`/`control` optional, top level or nested (see [tc_primary_header.md](tc_primary_header.md)). | `pmt.cons({telecommand: {tc_header: {vcid_counter: 0, …}}}, u8vector(data_field))` |
| `out` | output | PDU: `(metadata_dict . cltu_u8vector)` — start sequence, BCH codewords, tail sequence (see [cltu_framer.md](cltu_framer.md)). | `pmt.cons({…}, u8vector(EB90 ‖ codewords ‖ C5C5C5C5C5C5C579))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `scid` | int | `0` | Spacecraft ID (10 bits), used when the metadata carries none; validated by `tc_primary_header` (`ValueError`). |
| `vcid` | int | `0` | Virtual Channel ID (6 bits), same rule. |

Built in, as CCSDS fixes them: the FECF (`FECF_CRC`: CRC-16/CCITT, poly
`0x1021`, preset `0xFFFF`, no final XOR, no reflection — shared with the
RX side), and the defaults of `lfsr_scrambler` (seed `0xFF`),
`bch_encoder` (`0xC5`) and `cltu_framer` (`EB90` / `C5C5C5C5C5C5C579`).
`tc_primary_header` runs with `is_crc_used=True`, so `frame_length`
counts the FECF.

## Behavior / edge cases / current error handling

The block adds no handler of its own, only the wiring; every drop rule is
the inner blocks' own (see their PRDs). The inner blocks are reachable as
`primary_header`, `crc`, `scrambler`, `bch` and `framer`.

## CCSDS reference

CCSDS 232.0-B-4 (TC Space Data Link Protocol) — primary header and FECF;
CCSDS 231.0-B-4 (TC Synchronization and Channel Coding) — randomizer, BCH
(63,56) code, CLTU.

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_tc_frame_encoder.py` — 2 test methods (`test_001`–`test_002`),
  each in a running flowgraph: the block's CLTU equal to one built
  independently (header from its bit fields, FECF computed in the test as
  CRC-16/CCITT-FALSE, then scrambled, BCH-encoded and wrapped)
  (`test_001`), and the whole TX path from hierarchical blocks —
  `key_database` → `encapsulation_header` → `sdls_apply` → this block —
  recomputed bit for bit, with AES-CTR and CMAC from pycryptodome
  (`test_002`).
