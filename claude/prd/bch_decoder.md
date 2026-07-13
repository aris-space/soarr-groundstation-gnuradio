# bch_decoder

## Purpose

Corrects up to 2 bit errors in a CCSDS 231.0-B-4 (63,56) BCH codeword,
recovering the 56-bit information field, or drops the message if the
codeword can't be corrected within that bound. The RX-side counterpart to
`bch_encoder`. See [architecture.md](../architecture.md).

## Pipeline position

Internal primitive, GRC-exposed for testing only
([ADR-0005](../adr/0005-rx-path-canonical-block.md)) — not wired into
either signal chain by any `.grc` flowgraph in this repo. Used two ways:

1. **As a real GNU Radio message handler**, registered on its own `in`
   port, reachable if a `.grc` flowgraph wires another block's message
   output to it directly.
2. **As a plain Python helper**, instantiated and called directly (not
   through the message-port graph) by `ccsds_receiver`, the one canonical
   RX path (`ccsds_receiver.py:78,129`):
   ```python
   self.bch_decoder = bch_decoder(mode=0, generator_polynomial=0xC5, primitive_polynomial=0x43)
   ...
   msg = self.bch_decoder.error_correction_mode(msg)
   if msg is None:
       ...  # BCH decoding failed, ignore message
   ```
   `ccsds_receiver._checkmsg` validates PDU shape (pair, u8vector payload,
   dict metadata, exactly 8 bytes) *before* this call, so a malformed PDU
   never reaches `error_correction_mode` through this call site — only
   through the block's own message port, if a `.grc` flowgraph wired one
   in directly.

Also feeds the separate, internal-testing-only chain documented in
[ADR-0005](../adr/0005-rx-path-canonical-block.md):
`cltu_deframer → bch_decoder → lfsr_descrambler`, exercised by
`python/soarr/qa_lfsr_receive_chain.py`.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . codeword_u8vector)`, codeword exactly 8 bytes (64 bits: 56 info + 7 parity + 1 filler). | `pmt.cons({}, u8vector(8 bytes))` |
| `out` | output | PDU: `(metadata_dict . info_u8vector)`, info exactly 7 bytes (56 bits) — published only when correction succeeds. | `pmt.cons({}, u8vector(7 bytes))` |

`error_correction_mode` (the handler registered on `in`) also **returns**
the output PDU on success or `None` on failure/drop — the return value
`ccsds_receiver`'s direct call (Pipeline position above) relies on. This
return value has no meaning to GNU Radio's own message-passing machinery;
it only matters for the direct-call usage.

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `mode` | int | `0` | Only `0` is implemented. Any other value raises `ValueError` in `__init__` (mode is not settable at message-handler time, so this is constructor-time validation, not the message-handler catch-log-drop policy). |
| `generator_polynomial` | int | `0xC5` | BCH generator polynomial g(x), matching `bch_encoder`'s default. Validated in `__init__` (raises `ValueError` if outside `0x00`-`0xFF`). |
| `primitive_polynomial` | int | `0x43` | Accepted, range-validated (`0x00`-`0xFF`) in `__init__`, and stored as `self.primitive_polynomial` — **never read anywhere else in the class**. The decode algorithm (see Behavior below) is a brute-force search that needs only the generator polynomial; it performs no Galois-field syndrome computation, which is the only kind of BCH decoding that would need a primitive polynomial. `bch_encoder` has no equivalent parameter at all. |

## Behavior / edge cases / current error handling

**Algorithm** — brute-force search, not syndrome-based decoding: the
64-bit input is trimmed to its 63 data+parity bits (the trailing filler
bit is dropped). `_codeword_is_valid` checks the 63 bits by recomputing
the expected 7 parity bits from the first 56 (via `_compute_parity_bits`,
the same GF(2) polynomial long division `bch_encoder._compute_parity_bits`
uses) and comparing. If that fails, every single-bit flip of the 63 bits
is tried (63 checks); if none validates, every 2-bit-flip combination is
tried (1953 checks). The first flip combination that produces a valid
codeword is accepted as the correction. If no 0-, 1-, or 2-bit correction
validates, `_decode` returns `None` — the codeword is uncorrectable.

**On success**: `error_correction_mode` builds a new PDU from the
corrected 56 information bits and the original metadata dict, publishes
it on `out`, and returns it.

**On an uncorrectable codeword** (`_decode` returns `None`):
`error_correction_mode` logs at `warn` and returns `None` without
publishing — there's no dedicated error-signaling output port (per
[coding-standards.md](../coding-standards.md), `system_tester` provides
out-of-band error accounting instead). Before returning, it builds a
`bch_error`-tagged copy of the metadata dict and a `corrected_bytes`
value from the *uncorrected* payload — neither is used for anything;
both are discarded by the `return None` on the next line.

**On a malformed PDU** (not a pair, payload not a u8vector, metadata not
a dict) or a codeword of the wrong length (not exactly 8 bytes/64 bits):
`error_correction_mode` logs at `warn` and then **raises** `ValueError` —
it does not catch its own raise, so the exception propagates out of the
handler. Reached today only through the message port (`ccsds_receiver`'s
direct call already validates shape and length first, per Pipeline
position above), and only exercised in this repo by tests that call
`error_correction_mode` directly and assert the raise
(`qa_bch_decoder.py::test_003`, `test_004`, `test_006`, `test_007`,
`test_008`).

**Error handling**: `bch_decoder` *is* on
[coding-standards.md](../coding-standards.md)'s raw-RF `warn` list — its
`self.logger.warn(...)` calls (malformed PDU, wrong-length codeword,
uncorrectable error) are already at the correct level. What isn't
correct: [ADR-0003](../adr/0003-message-handler-error-policy.md) requires
every message handler to wrap its full body in catch-log-drop and never
raise; `error_correction_mode` currently raises directly for the four
malformed-input conditions above instead of logging and dropping.

**Docstrings**: none of `ADR-0004`'s required coverage is present. The
class itself still carries `gr_modtool`'s placeholder
(`"""docstring for block bch_decoder"""`); `__init__` and
`error_correction_mode` (a PMT-touching message handler) have no
docstring at all. `_decode`, `_compute_parity_bits`, and
`_codeword_is_valid` (non-PMT-touching private helpers) each have a
one-line docstring, permitted as-is by
[coding-standards.md](../coding-standards.md)'s exemption for that
category; `_bytes_to_bits`/`_bits_to_bytes` have none, also permitted.
`_decode`'s signature is annotated `-> bytes | None`, but it returns a
list of individual bit values (`0`/`1` ints), never a `bytes` object —
the type hint doesn't match what the method actually returns.

**Naming**: file, class, GRC block-id, every constructor parameter, and
every method name are already snake_case.

## CCSDS reference

CCSDS 231.0-B-4 (TC Synchronization and Channel Coding) — the (63,56) BCH
code: generator polynomial `g(x) = x^7 + x^6 + x^2 + 1` (`0xC5`), matching
`bch_encoder`'s own default and citation. Stated per the code's own
pre-existing parity-computation logic (identical to `bch_encoder`'s); not
independently verified against the standard from this repo alone (same
caveat as `bch_encoder.md`'s CCSDS reference). `qa_bchEncoderDecoder.py`
and `qa_lfsr_receive_chain.py` cross-check this block's parity computation
against `bch_encoder`'s own, and round-trip encoder output back through
this block, rather than relying on a hand-derived known-answer vector.

## Known issues / TODOs

- **`primitive_polynomial` is accepted, validated, and stored but never
  used.** See Parameters above. Whether this is a forward-looking
  placeholder for a future real (syndrome-based) decode mode, or simply
  dead, is a design question — deliberately not resolved here.
- **`error_correction_mode` raises instead of catch-log-drop for
  malformed/wrong-length input**, an [ADR-0003](../adr/0003-message-handler-error-policy.md)
  violation. See Behavior above for exactly which conditions raise and
  the one call site (`ccsds_receiver`) that currently avoids triggering
  them by pre-validating.
- **Dead code in the uncorrectable-error branch**: the discarded
  `dict_msg`/`corrected_bytes` reassignment described in Behavior above.

## Test coverage

- `python/soarr/qa_bch_decoder.py` — 13 test methods (`test_instance` +
  `test_001`-`test_004`, `test_006`-`test_013`): construction and its
  default parameter values, invalid `generator_polynomial`/
  `primitive_polynomial` raising at construction, the four malformed/
  wrong-length-input conditions each raising `ValueError` through the
  handler (see Behavior above), a valid 8-byte length not raising, and —
  using `bch_encoder` to produce real codewords — no-error passthrough
  (all-`0xFF`, all-zero), 1-bit-error correction, and metadata
  preservation.
- `python/soarr/qa_bchEncoderDecoder.py` — 6 test methods (`test_instance`
  + `test_001`-`test_005`): encoder→decoder round trip for a valid
  codeword, 1-bit and 2-bit corrected errors, metadata preservation, and
  a regression case pinned to a specific observed flowgraph vector.
- `python/soarr/qa_lfsr_receive_chain.py` — full TX-then-RX chain
  (`lfsr_scrambler → bch_encoder → cltu_framer` producing frames, then
  `cltu_deframer → bch_decoder → lfsr_descrambler` recovering them):
  `test_001_end_to_end_receive` confirms the recovered payload matches
  the original through this block; `test_003_bch_decoder_validation_no_errors`
  and `test_004_bch_parity_consistency` check this block specifically —
  a perfect codeword passes with no false correction, and this block's
  `_compute_parity_bits` agrees with `bch_encoder`'s for several fixed
  vectors.
