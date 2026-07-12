# lfsr_scrambler

## Purpose

Applies CCSDS pseudo-randomization (bit scrambling) to a PDU's payload
before BCH encoding — XORs every payload bit with a fixed, deterministic
LFSR-generated sequence, restarting from the same seed on every message.
See [architecture.md](../architecture.md).

## Pipeline position

TX chain, between the stock `digital.crc_append` and `bch_encoder`:

```
digital.crc_append.out (stock block) → lfsr_scrambler.in
lfsr_scrambler.out → bch_encoder.in
```

Confirmed via `python/soarr/qa_layoutTest.py:104-105`'s `msg_connect` wiring.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. No required metadata keys. | `pmt.cons({}, u8vector(payload))` |
| `out` | output | PDU: `(metadata_dict . scrambled_payload_u8vector)`. Metadata passed through unchanged; only the payload bytes are transformed (XORed with the randomizer sequence), length preserved. | `pmt.cons({}, u8vector(scrambled))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `mask` | int | `0xA9` | Stored on the instance but never read anywhere — see Known issues. |
| `seed` | int | `0xFF` | Initial 8-bit LFSR register state. Only the low `register_length` bits are used (extracted bit-by-bit); higher bits are silently ignored, not validated. |
| `register_length` | int | `8` | Only `8` is accepted — checked, but not until the first message arrives (see Known issues). |

## Behavior / edge cases / current error handling

**Algorithm** — `apply_scrambling` builds the full randomizer bit sequence
for the message up front (seeded from `seed`, extended via the CCSDS
recurrence `b[n] = b[n-2] ^ b[n-4] ^ b[n-5] ^ b[n-6] ^ b[n-7] ^ b[n-8]`),
then XORs it bit-for-bit (MSB first) against the payload. Because the
sequence is rebuilt from the same fixed `seed` on every call, scrambling
is stateless across messages and restarts fresh for every PDU — matching
CCSDS 231.0-B-3's per-transfer-frame reset, not a continuous stream
cipher. Since XOR is self-inverse, running the same fixed-seed sequence
twice recovers the original data (`qa_lfsr_scrambler.py::test_005`).

**Zero-length payload**: produces a zero-length output, no error
(`test_008`).

**Error handling** (intended to comply with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md), but currently
does not — see Known issues):

- `handle_msg` checks only that the payload is a u8vector before use; it
  does not check that `msg` itself is a pair before calling `pmt.car`/
  `pmt.cdr` on it.
- `apply_scrambling` is wrapped in `try/except ValueError`, but PDU
  construction and the final `message_port_pub` call are not covered, and
  only `ValueError` is caught (not `Exception`).
- `register_length != 8` raises inside `apply_scrambling`, at message
  time, not at construction.

**Docstrings**: none exist anywhere in this block (class, `__init__`,
`handle_msg`, `apply_scrambling`) — `gr_modtool`'s original state, per
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)'s status
note in coding-standards.md.

## CCSDS reference

CCSDS 231.0-B-3 (TC Synchronization and Channel Coding) — the TC
pseudo-randomizer: generator polynomial `h(x) = x^8 + x^6 + x^4 + x^3 +
x^2 + x + 1`, all-ones (`0xFF`) initial register state, reset at the
start of every transfer frame. `qa_lfsr_scrambler.py::test_003` checks
the first 40 output bits against the standard's published sequence
(`0xFF 0x39 0x9E 0x5A 0x68` for an all-zero input) as an independent-source
known-answer test, not just a self-consistency check.

## Known issues / TODOs

- **`mask` is a dead parameter.** It's stored (`self.mask = mask`) and
  exposed as a configurable GRC parameter (`grc/soarr_lfsr_scrambler.block.yml`),
  but `apply_scrambling` hardcodes the CCSDS generator polynomial directly
  in its recurrence and never reads `self.mask` — changing it from its
  default has zero effect on scrambling output. The same pattern exists in
  the sibling `lfsr_descrambler` block (`python/soarr/lfsr_descrambler.py:22`,
  also unused), so this isn't a one-off typo; out of scope to fix there
  since that block isn't under review in this pass.
- **`register_length` is validated too late.** An out-of-range value (only
  `8` is valid) is accepted silently at construction and only raises
  inside `apply_scrambling` on the first message — caught by `handle_msg`'s
  narrow `except ValueError` and logged, so the flowgraph doesn't crash,
  but it also never publishes again: every subsequent message is silently
  dropped for the lifetime of the block. Every other block reviewed so far
  in this pass (`tc_primary_header`, `sdls_header`, ...) validates
  constructor-supplied field widths at `__init__` instead, failing fast at
  flowgraph-build time.
- **`handle_msg` crashes on a non-pair input.** `pmt.car`/`pmt.cdr` are
  called unconditionally before any shape check; reproduced directly:
  `handle_msg(pmt.intern("not-a-pair"))` raises
  `ValueError: pmt_car: wrong_type not-a-pair` out of the handler,
  violating ADR-0003 (no message handler may raise).
- **catch-log-drop coverage is incomplete.** Only the `apply_scrambling`
  call is wrapped, and only `ValueError` is caught — PDU construction and
  `message_port_pub` are unprotected, and any other exception type escapes
  the handler.
- **No docstrings anywhere in this block** — full ADR-0004 gap.

## Test coverage

- `python/soarr/qa_lfsr_scrambler.py` — 12 test methods: construction
  (default and custom parameters), a known-answer CCSDS sequence check
  (`test_003`, an independent-source correctness check, not tautological),
  determinism across repeated calls, XOR round-trip (double-scramble
  recovers the original), PDU metadata identity preserved, output length
  preserved, zero-length payload, a non-u8vector body dropped cleanly,
  an invalid `register_length` raising (but only exercised by calling
  `apply_scrambling` directly, not through `handle_msg` — doesn't prove
  the handler-level "silently drops forever" behavior described above),
  and a 255-bit maximal-length-sequence period check.
- `python/soarr/qa_layoutTest.py::test_009_lfsr_scrambler_real_handler` —
  same pattern as the other TX blocks' "real handler" tests: builds a
  fresh, standalone instance and calls `handle_msg` directly, confirming
  real XOR-against-the-CCSDS-sequence behavior and metadata pass-through,
  not the `msg_connect` wiring itself (`test_002_end_to_end_message_routing`
  shims this block's handler out, same as every other TX block).
