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
lfsr_scrambler.out → bch_encoder.message
```

Confirmed via `python/soarr/qa_layoutTest.py:103-104`'s `msg_connect` wiring.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. No required metadata keys. | `pmt.cons({}, u8vector(payload))` |
| `out` | output | PDU: `(metadata_dict . scrambled_payload_u8vector)`. Metadata passed through unchanged; only the payload bytes are transformed (XORed with the randomizer sequence), length preserved. | `pmt.cons({}, u8vector(scrambled))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `seed` | int | `0xFF` | Initial 8-bit LFSR register state. Only the low `register_length` bits are used (extracted bit-by-bit); higher bits are silently ignored, not validated. |
| `register_length` | int | `8` | Validated in `__init__` (raises `ValueError` if not `8`) — the CCSDS 231.0-B-3 randomizer is only defined for an 8-bit register. |

A `mask` parameter previously existed here (stored but never read by
`apply_scrambling`, which hardcodes the CCSDS generator polynomial
directly) — removed during this pass, along with its GRC yaml entry, per
CCSDS 231.0-B-3 mandating one fixed polynomial for interoperability (see
Known issues). The identical dead parameter still exists in the sibling
`lfsr_descrambler` block, out of scope here.

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

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `handle_msg`
checks `msg` is a pair and its payload is a u8vector before use; the full
body past those two checks — payload extraction, scrambling, PDU
construction, and the publish call — is wrapped in catch-log-drop
(`except Exception`), all logged at `error` (this TX-side block isn't in
the raw-RF `warn` list).

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`handle_msg`, `Args`/`Returns` for `apply_scrambling`.

## CCSDS reference

CCSDS 231.0-B-3 (TC Synchronization and Channel Coding) — the TC
pseudo-randomizer: generator polynomial `h(x) = x^8 + x^6 + x^4 + x^3 +
x^2 + x + 1`, all-ones (`0xFF`) initial register state, reset at the
start of every transfer frame. `qa_lfsr_scrambler.py::test_003` checks
the first 40 output bits against the standard's published sequence
(`0xFF 0x39 0x9E 0x5A 0x68` for an all-zero input) as an independent-source
known-answer test, not just a self-consistency check.

## Known issues / TODOs

- **`mask` removed.** Was a dead parameter (stored, never read — see
  Parameters above) shared with the sibling `lfsr_descrambler` block. Fixed
  here by deletion (constructor signature, GRC yaml, and both call sites in
  `qa_layoutTest.py`) rather than making the polynomial genuinely
  configurable: CCSDS 231.0-B-3 mandates one fixed polynomial for TX/RX
  interoperability, and no caller in this repo ever varied it. `lfsr_descrambler`
  still has the identical dead parameter — out of scope for this PRD.
- **`self.reg_length` renamed to `self.register_length`** to match the
  constructor parameter name and the sibling `lfsr_descrambler`'s own
  attribute name (`lfsr_descrambler.py:24`), removing the unexplained
  rename.

All other findings from this pass (`register_length` validated too late,
`handle_msg` crashing on a non-pair input, incomplete catch-log-drop
coverage, missing docstrings) were fixed directly — see Behavior and
Parameters above for the current, compliant state.

## Test coverage

- `python/soarr/qa_lfsr_scrambler.py` — 14 test methods: a bare
  construction smoke test (`test_instance`), construction with default and
  custom parameters, a known-answer CCSDS sequence check
  (`test_003`, an independent-source correctness check, not tautological),
  determinism across repeated calls, XOR round-trip (double-scramble
  recovers the original), PDU metadata identity preserved, output length
  preserved, zero-length payload, a non-u8vector body dropped cleanly, an
  invalid `register_length` raising at construction, a 255-bit
  maximal-length-sequence period check, a non-pair input dropped cleanly
  instead of crashing the handler, and a mock-forced publish failure
  proven to be caught and dropped rather than raised through the real
  handler.
- `python/soarr/qa_layoutTest.py::test_009_lfsr_scrambler_real_handler` —
  same pattern as the other TX blocks' "real handler" tests: builds a
  fresh, standalone instance and calls `handle_msg` directly, confirming
  real XOR-against-the-CCSDS-sequence behavior and metadata pass-through,
  not the `msg_connect` wiring itself (`test_002_end_to_end_message_routing`
  shims this block's handler out, same as every other TX block).
