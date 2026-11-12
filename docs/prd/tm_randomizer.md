# tm_randomizer

## Purpose

The CCSDS TM Pseudo-Randomizer (CCSDS 131.0-B-5 section 10): XORs each
codeblock, codeword or Transfer Frame with the standard pseudo-random
sequence, restarted at the first bit of every PDU. XOR is its own
inverse, so the same block randomizes on TX and derandomizes on RX. GRC
label **TM Randomizer**, category `[Soarr]/TM (Telemetry)/Randomizer` —
the first TM block. The TM sequences differ from the TC randomizer's
(`lfsr_scrambler`, CCSDS 231.0-B), so that block can't stand in.

## Pipeline position

```
TX: (Reed-Solomon / Turbo / LDPC encoder) → tm_randomizer → (ASM attached in front)
RX: (ASM found, codeblock cut out after it) → tm_randomizer → (decoder)
```

On TX after Reed-Solomon, Turbo or LDPC encoding (if used) and before
convolutional encoding (if used); on RX after convolutional decoding and
codeblock synchronization, before the other decoders (131.0-B-5 10.2.2).
The ASM is neither randomized nor derandomized (10.3.4).

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . u8vector)`, one codeblock, codeword or Transfer Frame as packed bytes (e.g. 255 bytes for RS(255,223) with interleaving depth 1). | `pmt.cons({}, u8vector(255 bytes))` |
| `out` | output | PDU: the same metadata, the payload XORed with the sequence; same length. | `pmt.cons({}, u8vector(255 bytes))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `register_length` | int | `17` | The LFSR, named as `lfsr_scrambler`'s `register_length` on TC. `17` — the **17-bit LFSR**, h(x) = x^17 + x^14 + 1, start state `11000111000111000`, a 131071-bit sequence (10.4.1, 10.4.3); `8` — the **8-bit LFSR (legacy)**, h(x) = x^8 + x^7 + x^5 + x^3 + 1, start state all ones, a 255-bit sequence, for backward compatibility with legacy systems (10.4.2). Anything else raises `ValueError`. The start state isn't a parameter: CCSDS fixes it per sequence. GRC labels the options "17-bit LFSR" and "8-bit LFSR (legacy)". |
| `length_check` | bool | `False` | If `True`, PDUs that aren't `codeblock_length` bytes are dropped. Off by default, so frames and Turbo/LDPC codewords of any length pass. |
| `codeblock_length` | int | `255` | Expected length in bytes with `length_check` (e.g. 255 × interleaving depth for Reed-Solomon); must be ≥ 1 then, otherwise `ValueError`. GRC shows it only while the check is enabled. |

## Behavior / edge cases / current error handling

**Sequence generation** (`sequence_bytes`): the generator's start state,
written as in 10.4.3, read right to left gives the first bits of the
sequence; every further bit follows h(x) as a recurrence,
`s[n+d] = XOR of s[n+k]` over the exponents below the degree `d`. Bytes
are packed first bit in the MSB, the order they are XORed onto the
codeblock (10.3.2). Results are cached per (sequence, length), so a
stream of equal-length codeblocks computes the sequence once. A codeblock
longer than one period gets the repeating sequence (10.4.3).

**Error handling** (catch-log-drop,
[ADR-0003](../adr/0003-message-handler-error-policy.md)): a non-PDU, a
non-u8vector payload, a wrong length with the check on, or any failure
while randomizing or publishing is dropped and logged at `warn` — on RX
this block sees data straight after the ASM, before any structural check
([coding-standards.md](../coding-standards.md)).

## CCSDS reference

CCSDS 131.0-B-5 (TM Synchronization and Channel Coding, September 2023),
section 10: 10.2.2 where it's applied, 10.3 synchronization with the
ASM, 10.4.1–10.4.3 the sequences and start states, 10.4.3 note 2 the
first 40 bits of each sequence — `1C 71 B9 1B A9` (17-bit LFSR,
131071-bit sequence) and `FF 48 0E C0 9A` (8-bit LFSR, 255-bit
sequence).

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_tm_randomizer.py` — 9 test methods (`test_001`–`test_009`):
  both LFSRs' first 40 bits equal to the standard's (`test_001`), each
  sequence following its polynomial's recurrence and repeating with its
  register's full period, 2^n - 1 bits (`test_002`), a round trip and the
  restart per PDU (`test_003`), metadata kept (`test_004`), a codeblock
  longer than the 8-bit LFSR's 255-bit period (`test_005`), the length check on and off (`test_006`),
  malformed input dropped (`test_007`), a publish failure caught
  (`test_008`), and invalid settings raising (`test_009`).
