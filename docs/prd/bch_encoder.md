# bch_encoder

## Purpose

Applies CCSDS 231.0-B-4 (63,56) BCH forward-error-correction encoding to
a PDU's payload: splits it into 56-bit information chunks (padding the
last chunk with a fixed fill pattern if needed), computes 7 complemented
parity bits per chunk, and appends a filler bit — producing one 8-byte
BCH codeword per chunk, published together as one PDU. See
[architecture.md](../architecture.md).

## Pipeline position

TX chain, between `lfsr_scrambler` and `cltu_framer`:

```
lfsr_scrambler.out → bch_encoder.message
bch_encoder.codewords → cltu_framer.in
```

Confirmed via `python/soarr/qa_tx_chain.py:104-105`'s `msg_connect`
wiring.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `message` | input | PDU: `(metadata_dict . payload_u8vector)`. No required metadata keys. Payload of any length ≥ 1 byte. | `pmt.cons({}, u8vector(payload))` |
| `codewords` | output | PDU: `(metadata_dict . codewords_u8vector)` — **one PDU per input PDU**, holding every codeword of the payload back to back: 8 bytes (7 info + 1 parity/filler byte) per 56-bit chunk. The input metadata plus `filled` (bool, whether fill bits were added). | `pmt.cons({filled: True}, u8vector(N × 8 bytes))` |

Keeping a frame's codewords in one PDU is what lets `cltu_framer` wrap
them in a single CLTU, as CCSDS 231.0-B-4 requires (see
[cltu_framer.md](cltu_framer.md)). The same key name `filled` also
appears on the RX side, where `lfsr_descrambler` resets its sequence
when `filled` is present in its own (unrelated) input metadata; nothing
wires `bch_encoder`'s output metadata into `lfsr_descrambler`.

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
appended to the chunk's 7 information bytes — an 8-byte codeword. All
codewords are concatenated in order and published as one PDU.

**Empty payload**: rejected before any processing (`len(payload_bytes) ==
0` → logged, no publish) — the one input-shape condition this block
explicitly rejects.

**`filled` metadata**: added to the output PDU's metadata (`True`/
`False`, whether padding was needed). No TX block downstream reads it.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `encode_bch`'s
full body — the pair/u8vector shape checks, payload extraction, the
empty-payload check, the encoding loop, PDU construction, and the
`message_port_pub` call — is wrapped in catch-log-drop (`except
Exception`), all logged at `error` (this TX-side block isn't in the
raw-RF `warn` list). The handler name, `encode_bch`, matches every
sibling block's snake_case convention.

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
`lfsr_scrambler.md`'s CCSDS reference). `qa_tx_chain.py::test_010_bch_encoder_real_handler`
checks a hand-derivable known-answer case (an all-zero 7-byte payload
produces parity byte `0xFE` — all 7 parity bits set, filler bit `0`,
matching the algorithm's zero-dividend case), not just self-consistency.

## Known issues / TODOs

`filled`'s reuse with different semantics in `lfsr_descrambler` (Message
ports above) is a naming overlap to be aware of, not a bug in this
block.

## Test coverage

- `python/soarr/qa_bch_encoder.py` — 31 test methods (`test_instance` +
  `test_001`–`test_030`): construction with default and a genuinely
  different custom polynomial, single- and multi-codeword encoding across
  a wide range of boundary sizes (exactly 1/2/3/4/7/8 codewords, with and
  without fill bits), fill-pattern correctness (`0x55` bytes), parity-bit
  complementing, an algebraic affine/linearity property of the
  complemented code (`test_010`, a structural correctness check, not a
  hand-derived known-answer case), single-bit-flip changing the parity
  (error-detection property), PDU metadata preserved with `filled` added
  (`test_012`), a non-u8vector body dropped
  cleanly (`test_015`), an invalid `polynomial` raising at construction
  (`test_026`), a non-pair input dropped cleanly instead of crashing the
  handler (`test_027`), an empty payload dropped cleanly (`test_028`),
  a mock-forced publish failure proven to be caught and dropped
  rather than raised through the real handler (`test_029`), and a
  multi-codeword payload published as exactly one PDU with `filled`
  set correctly with and without fill bits (`test_030`). Every
  multi-codeword test checks the encoder's real output directly — one
  PDU of N × 8 bytes.
- `python/soarr/qa_tx_chain.py::test_010_bch_encoder_real_handler` —
  same pattern as the other TX blocks' "real handler" tests: builds a
  fresh, standalone instance and calls `encode_bch` directly, checking the
  hand-derivable known-answer parity byte described in CCSDS reference
  above, not the `msg_connect` wiring itself (shimmed out in
  `test_002_end_to_end_message_routing` via `_bind_passthrough`).
