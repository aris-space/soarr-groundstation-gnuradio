# sdls_header

## Purpose

Builds and prepends the CCSDS 355.0-B-1 SDLS Security Header
(`spi (2 bytes) || sdls_counter (2 bytes)`) to a PDU's payload. The last
of the three SDLS-layer steps on TX, after encryption and
authentication — see [architecture.md](../architecture.md).

## Pipeline position

TX chain, between `sdls_authentication` and `tc_primary_header`:

```
sdls_authentication.out → sdls_header.in
sdls_header.out → tc_primary_header.in
```

`spi` originates from the TX payload source (`data_creator` in the
examples), which places it under `sdls.security_header.spi`, never
top-level (see Known Issues); `inject_db` uses it to look up
`crypt_key`/`auth_key` in `db_client` and keeps it as is. `sdls_counter` passes through from `db_client` all
the way from `sdls_encryption` and `sdls_authentication`, both of which
consume it but never remove it.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Metadata must include `spi` and `sdls_counter` (each a symbol hex string, u8vector, or non-negative integer — see Behavior), checked at the top level first, falling back to `sdls.security_header.<key>` only if the top-level key is absent. | `pmt.cons({spi: "1234", sdls_counter: "AABB"}, u8vector(payload))` |
| `out` | output | PDU: `(metadata_dict . header‖payload_u8vector)`. `spi` and `sdls_counter` are removed from the metadata; everything else passes through unchanged. | `pmt.cons({}, u8vector([0x12, 0x34, 0xAA, 0xBB, ...payload]))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `iv_length_bytes` | int | `2` | Fixed byte width `sdls_counter` is padded/validated to. Must be `2` — `sdls_encryption`/`sdls_decryption`'s AES-CTR counter width and `sdls_authentication`/`sdls_authentication_verify`'s CMAC counter width are both fixed at exactly 2 bytes, so any other value would desync the wire-transmitted field width from what TX/RX actually use cryptographically. Validated in `__init__` (raises `ValueError` if not `2`); not GRC-exposed (hardcoded to `2` in the `make` template, matching `bch_decoder`'s fixed `mode` parameter). |

## Behavior / edge cases / current error handling

**`spi`/`sdls_counter` lookup** — each checked at `dict_msg[key]` first
(via `pmt.dict_has_key`, already correct — this block never had the
`PMT_NIL`-comparison ambiguity found in `sdls_encryption`/
`sdls_authentication`), falling back to the nested
`dict_msg["sdls"]["security_header"][key]` path only if the top-level key
is genuinely absent.

**Value parsing** — `_extract_dictionary_key` accepts three forms, more
than `sdls_encryption`/`sdls_authentication`'s key lookups:
- a PMT symbol holding a hex string,
- a u8vector, or
- a non-negative integer PMT (converted to `fixed_length_bytes` bytes,
  big-endian) — rejected if `fixed_length_bytes` isn't a positive number.

A value shorter than `fixed_length_bytes` is zero-padded (logged at
`info`); longer is rejected (logged at `error`, no publish) — `spi` is
always validated against a fixed 2 bytes; `sdls_counter` against
`iv_length_bytes`.

**Header**: `spi (2 bytes, big-endian) ‖ sdls_counter (iv_length_bytes)`,
built once per instance (the schema only depends on `iv_length_bytes`,
constant for the block's lifetime) and reused for every message. Both
keys are deleted from the outgoing metadata after use.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): every rejection
logs at `error` (this TX-side block isn't in the raw-RF `warn` list). A
single catch-log-drop wraps the full body past input-shape validation,
including header packing and the publish call. `_extract_dictionary_key`
signals every failure the same way (log + return `None`), including the
"value too long" case.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Returns` for
`_extract_dictionary_key`, `Args`/`Publishes`/`Drops when` for
`add_header`.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — this block builds
the Security Header half (SPI + IV) of SDLS's protection fields; the
Security Trailer (MAC) was already appended upstream by
`sdls_authentication`. `sdls_counter` is exactly the wire-transmitted IV
value.

## Known issues / TODOs

- **The nested `sdls.security_header.<key>` fallback lookup has zero test
  coverage, and it's not an edge case for either key — it's the only path
  both `spi` and `sdls_counter` take in the real pipeline.** `sdls_counter`
  is assigned *by* the `db_client` response, never known beforehand, so
  `inject_db.py`'s merge logic (`_merge_key_into_nested`) always nests
  it under
  `sdls.security_header.sdls_counter` — confirmed directly in that file.
  `spi` is nested from the start too: `data_creator.py` (the one real
  message-generator in this repo) builds it directly under
  `sdls.security_header.spi` (`generate_message`) — it never
  produces a top-level `spi` at all, so `inject_db`'s merge logic never
  even gets a chance to run on it; `qa_inject_db.py`'s own tests confirm
  the same (every one constructs `spi` nested, never top-level). No test
  anywhere exercises the nested shape: every unit test in
  `qa_sdls_header.py` (and the sibling blocks') constructs both keys at
  the top level directly, bypassing `inject_db`/`data_creator` entirely,
  and `qa_tx_chain.py::test_002_end_to_end_message_routing` — the one
  test that *does* run the real wired topology — shims every block's
  real handler out, including `inject_db`'s own two handlers and
  `db_client`'s, so it never exercises real merge or extraction logic
  either. In short: the code path this block's `spi`/`sdls_counter`
  handling actually takes in production has never been run by any test
  in this repo.

## Test coverage

- `python/soarr/qa_sdls_header.py` — 13 test methods (`test_instance` +
  `test_001`–`test_012`): header prepended correctly while preserving
  unrelated metadata and removing `spi`/`sdls_counter`, short fields
  zero-padded, an over-length `spi` and an over-length `sdls_counter`
  each rejected with no publish, a missing `spi`, a non-u8vector payload,
  an invalid hex symbol, and a non-pair message all rejected, both the
  u8vector and integer forms of `spi`/`sdls_counter` producing correct
  output, any `iv_length_bytes` value other than `2` rejected at
  construction time, and a mock-forced internal build failure proven to
  be caught and dropped rather than raised through the real handler.
- `python/soarr/qa_tx_chain.py::test_005_sdls_header_real_handler` —
  same pattern as the other TX blocks' "real handler" tests: builds a
  **fresh, standalone** instance and calls `add_header` directly, proving
  real header-building logic and metadata handling but not the
  `msg_connect` wiring (covered separately by
  `test_002_end_to_end_message_routing`, with this block's handler
  shimmed out).
