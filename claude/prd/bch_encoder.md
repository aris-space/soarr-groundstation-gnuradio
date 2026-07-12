# bch_encoder

## Purpose

Applies CCSDS 231.0-B-4 (63,56) BCH forward-error-correction encoding to
a PDU's payload: splits it into 56-bit information chunks (padding the
last chunk with a fixed fill pattern if needed), computes 7 complemented
parity bits per chunk, and appends a filler bit — producing one 8-byte
BCH codeword per chunk. See [architecture.md](../architecture.md).

## Pipeline position

TX chain, between `lfsr_scrambler` and `cltu_framer`:

```
lfsr_scrambler.out → bch_encoder.message
bch_encoder.codewords → cltu_framer.in
```

Confirmed via `python/soarr/qa_layoutTest.py:104-105`'s `msg_connect`
wiring.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `message` | input | PDU: `(metadata_dict . payload_u8vector)`. No required metadata keys. Payload of any length ≥ 1 byte. | `pmt.cons({}, u8vector(payload))` |
| `codewords` | output | PDU: `(metadata_dict . codeword_u8vector)`, always exactly 8 bytes (7 info + 1 parity/filler byte). **One `codewords` PDU is published per 56-bit chunk the input payload splits into** — a single input PDU spanning multiple codewords produces multiple output PDUs, not one. The metadata dict is reused across all of a message's codewords, with `filled` (bool) added only to the last one. | `pmt.cons({}, u8vector(8 bytes))`, possibly repeated |

This multi-publish-per-input behavior differs from every other TX block
in this pipeline (all 1-input-PDU-in → 1-PDU-out) — it matches
`cltu_framer`'s own contract, which requires exactly 8 bytes per
input PDU (`cltu_framer.py:54-56`, rejects anything else) and checks for
the `filled` key (`cltu_framer.py:68`) purely to emit a distinct `"OK\n"`
log line marking the end of a multi-codeword message — no control-flow
effect downstream of *this* block. The same key name is reused with real
control-flow weight elsewhere in the codebase, on the RX side:
`lfsr_descrambler.py:103-105` triggers a sequence reset when `filled` is
present in its own (unrelated) input metadata. The two are structurally
disconnected — nothing wires `bch_encoder`'s output metadata into
`lfsr_descrambler` — but it's the same key name doing two different jobs
in two different blocks, worth knowing before reusing it a third time.

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `polynomial` | int | `0xC5` | Generator polynomial g(x) = x^7 + x^6 + x^2 + 1, as an 8-bit value with bit 7 set (the implicit leading term is stored explicitly, not implied). Validated in `__init__` (raises `ValueError` if outside `0x80`-`0xFF`). |

## Behavior / edge cases / current error handling

**Algorithm** — payload bytes are expanded to individual bits (MSB
first), padded to a multiple of 56 bits with an alternating `0,1,0,1,...`
fill pattern (packs to `0x55` bytes) if not already aligned, then each
56-bit chunk is: converted back to 7 bytes, divided (GF(2) polynomial
long division) by `polynomial` to get a 7-bit remainder, complemented,
and packed as `[7 parity bits][1 filler bit, always 0]` into an 8th byte
appended to the chunk's 7 information bytes — an 8-byte codeword,
published immediately as its own PDU.

**Empty payload**: rejected before any processing (`len(payload_bytes) ==
0` → logged, no publish) — the one input-shape condition this block
explicitly rejects.

**`filled` metadata**: added (`True`/`False`, whether padding was needed)
only to the metadata dict of the *last* codeword's PDU in a message;
earlier codewords' PDUs carry the original metadata unchanged, without
the key at all — consumed downstream only by `cltu_framer`'s cosmetic
log line (see Pipeline position above).

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `encode_bch`
checks `msg` is a pair and its payload is a u8vector before use; the full
body past those two checks — payload extraction, the empty-payload check,
the encoding loop, PDU construction, and every `message_port_pub` call —
is wrapped in catch-log-drop (`except Exception`), all logged at `error`
(this TX-side block isn't in the raw-RF `warn` list). The handler name,
`encode_bch`, matches every sibling block's snake_case convention.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`encode_bch`, `Args`/`Returns` for `_compute_parity_bits`. `_bytes_to_bits`,
`_bits_to_bytes`, and `_apply_fill_bits` intentionally have no docstring
— ADR-0004 permits omitting one for trivial private helpers with no PMT
involvement, which all three are.

## CCSDS reference

CCSDS 231.0-B-4 (TC Synchronization and Channel Coding) — the (63,56)
BCH code: generator polynomial `g(x) = x^7 + x^6 + x^2 + 1` (`0xC5`),
complemented parity bits per §3.3.1, one filler bit (`0`) appended after
the 7 parity bits to complete each 64-bit/8-byte codeword. Stated per the
code's own pre-existing docstring citation; not independently verified
against the standard from this repo alone (same caveat as
`lfsr_scrambler.md`'s CCSDS reference). `qa_layoutTest.py::test_010_bch_encoder_real_handler`
checks a hand-derivable known-answer case (an all-zero 7-byte payload
produces parity byte `0xFE` — all 7 parity bits set, filler bit `0`,
matching the algorithm's zero-dividend case), not just self-consistency.

## Known issues / TODOs

`filled`'s reuse with different semantics in `lfsr_descrambler` (Message
ports above) is a naming overlap to be aware of, not a bug in this
block.

## Test coverage

- `python/soarr/qa_bch_encoder.py` — 30 test methods (`test_instance` +
  `test_001`–`test_029`): construction with default and a genuinely
  different custom polynomial, single- and multi-codeword encoding across
  a wide range of boundary sizes (exactly 1/2/3/4/7/8 codewords, with and
  without fill bits), fill-pattern correctness (`0x55` bytes), parity-bit
  complementing, an algebraic affine/linearity property of the
  complemented code (`test_010`, a structural correctness check, not a
  hand-derived known-answer case), single-bit-flip changing the parity
  (error-detection property), PDU metadata preserved with `filled` added
  only to the last codeword (`test_012`), a non-u8vector body dropped
  cleanly (`test_015`), an invalid `polynomial` raising at construction
  (`test_026`), a non-pair input dropped cleanly instead of crashing the
  handler (`test_027`), an empty payload dropped cleanly (`test_028`),
  and a mock-forced publish failure proven to be caught and dropped
  rather than raised through the real handler (`test_029`).
- `python/soarr/qa_layoutTest.py::test_010_bch_encoder_real_handler` —
  same pattern as the other TX blocks' "real handler" tests: builds a
  fresh, standalone instance and calls `encode_bch` directly, checking the
  hand-derivable known-answer parity byte described in CCSDS reference
  above, not the `msg_connect` wiring itself (shimmed out in
  `test_002_end_to_end_message_routing` via `_bind_passthrough`).
