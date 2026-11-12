# tm_cadu_framer

## Purpose

Builds a CCSDS TM Channel Access Data Unit (CCSDS 131.0-B-5 section 9):
the Attached Sync Marker (ASM) followed by the codeblock, codeword or
Transfer Frame. GRC label **TM CADU Framer**, category
`[Soarr]/TM (Telemetry)/CADU` — the TM counterpart of `cltu_framer`; the
RX side (finding the ASM and cutting out the codeblock) will be a TM CADU
Deframer.

## Pipeline position

```
TX: (Reed-Solomon encoder) → tm_randomizer → tm_cadu_framer → (modulator)
                                                        └→ cltu_burst_builder (Burst Builder) for bursts
```

The ASM is attached after randomizing and is itself not randomized
(131.0-B-5 10.3.4). The CADU has nothing but the ASM and the codeblock:
CCSDS allows no bits between consecutive CADUs (9.4.2), so a preamble and
tail for a burst come from [cltu_burst_builder](cltu_burst_builder.md)
after this block, not from here.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . u8vector)`, one (randomized) codeblock, codeword or Transfer Frame as packed bytes. | `pmt.cons({}, u8vector(255 bytes))` |
| `out` | output | PDU: the same metadata, payload `ASM ‖ codeblock` — one CADU. | `pmt.cons({}, u8vector(1A CF FC 1D ‖ 255 bytes))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `asm` | str | `"1ACFFC1D"` | The ASM in hexadecimal, one of the CCSDS markers (`ASMS`), chosen by the coding (9.3): `1ACFFC1D` (32-bit) for uncoded, convolutional, Reed-Solomon, concatenated, rate-7/8 LDPC and SMTF-stream LDPC data; `034776C7272895B0` (64-bit) for rate-1/2 Turbo and rate-1/2, 2/3, 4/5 LDPC of a Transfer Frame; 96-, 128- and 192-bit markers for Turbo 1/3, 1/4, 1/6; `352EF853` for an embedded data stream (9.6). Anything else raises `ValueError`; case-insensitive. |
| `length_check` | bool | `False` | If `True`, PDUs that aren't `codeblock_length` bytes are dropped. |
| `codeblock_length` | int | `255` | Expected length in bytes with `length_check` (e.g. 255 × interleaving depth for Reed-Solomon); must be ≥ 1 then, otherwise `ValueError`. GRC shows it only while the check is enabled. |

## Behavior / edge cases / current error handling

**Framing** (`add_asm`): publishes `ASM ‖ codeblock` with the input
metadata; one PDU in, one CADU out, no state between messages.

**Error handling** (catch-log-drop,
[ADR-0003](../adr/0003-message-handler-error-policy.md)): a non-PDU, a
non-u8vector or empty payload, a wrong length with the check on, or any
failure while framing or publishing is dropped and logged at `error` —
a TX block, not exposed to raw RF data
([coding-standards.md](../coding-standards.md)).

## CCSDS reference

CCSDS 131.0-B-5 (TM Synchronization and Channel Coding, September 2023),
section 9: 9.1.2 and table 9-1 (CADU content per coding scheme), 9.3 and
figures 9-1 to 9-5 (ASM bit patterns), 9.4 (location: immediately before
the codeblock, nothing between CADUs), 9.6 and figure 9-6 (embedded
data stream ASM).

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_tm_cadu_framer.py` — 8 test methods (`test_001`–`test_008`):
  every ASM equal to the bit pattern of its figure in the standard,
  transcribed independently of the hexadecimal notation (`test_001`), a
  CADU being exactly ASM and codeblock with the metadata kept
  (`test_002`), every ASM option (`test_003`), the length check on and off
  (`test_004`), this block followed by `cltu_burst_builder` giving
  preamble, ASM, codeblock and tail (`test_005`), malformed and empty
  input dropped (`test_006`), a publish failure caught (`test_007`), and
  invalid settings raising (`test_008`).
