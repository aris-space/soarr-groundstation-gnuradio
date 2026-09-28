# sdls_authentication

## Purpose

Computes a CMAC authentication tag over a CTR-style counter block plus the
PDU's payload, and appends it. The second of the two SDLS protection
steps applied on TX (encrypt-then-authenticate — see
[architecture.md](../architecture.md)): by the time this block runs, the
payload is already `sdls_encryption`'s ciphertext, so the tag authenticates
the encrypted bytes, not the plaintext.

## Pipeline position

TX chain, between `sdls_encryption` and `sdls_header`:

```
sdls_encryption.out → sdls_authentication.in
sdls_authentication.out → sdls_header.in
```

RX counterpart: [sdls_authentication_verify](sdls_authentication_verify.md).
It uses the same `authentication_state` parameter name as this block.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Metadata must include `auth_key` (symbol hex string or u8vector, 32 bytes) and `sdls_counter` (integer, 0–65535) — same dual-lookup rule as `sdls_encryption`'s `sdls_counter`. | `pmt.cons({auth_key: "00112233...", sdls_counter: 0x1234}, u8vector(ciphertext))` |
| `out` | output | PDU: `(metadata_dict . payload‖tag_u8vector)`. `auth_key` is removed from the metadata; `sdls_counter` and everything else pass through unchanged. Output is 16 bytes longer than the input payload (the CMAC tag). | `pmt.cons({sdls_counter: 0x1234}, u8vector(ciphertext ‖ 16-byte tag))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `authentication_state` | bool | `True` | `False` makes the block a pure passthrough after its shape checks — input republished on `out` unchanged, no key/counter validation, no metadata mutation. Matches `sdls_authentication_verify`'s own `authentication_state` parameter name — not a new convention. |
| `nonce` | bytes | `b"\x00" * 14` (all-zero) | Fixed for the block's lifetime, combined with the per-message `sdls_counter` into the 16-byte block prepended to the payload before CMAC (see Behavior). Never transmitted — RX's `sdls_authentication_verify` must be configured with the identical value out-of-band. **Independent of `sdls_encryption`'s `nonce`** — a separate parameter, separate key (`auth_key` vs. `crypt_key`), by design: using distinct key/nonce material for encryption vs. authentication is standard practice, not a bug, even though both currently default to the same all-zero value. Validated in `__init__` (`TypeError`/`ValueError` if not exactly 14 bytes). |

## Behavior / edge cases / current error handling

**Shape validation runs first, unconditionally**: `add_authentication`
rejects a non-PDU input, non-dict metadata, or non-u8vector payload
without publishing, regardless of `authentication_state`. Only past
those checks does it look at `authentication_state`.

**`authentication_state=False`**: republishes the already-shape-validated
input PDU on `out` completely unchanged — no key/counter extraction.
**Same order as the RX sibling**: `sdls_authentication_verify.verify_message`
also checks shape first, `authentication_state` second.

**`auth_key` lookup** (`_extract_secret`) — identical rules to
`sdls_encryption`'s `crypt_key`: a PMT symbol holding a hex string, or a
u8vector. Detects an absent key via `pmt.dict_has_key`, checked before
the value is ever fetched — the same pattern `sdls_authentication_verify.py`'s
own `_extract_secret` uses too. Rejected if absent, malformed, or not
exactly 32 bytes (AES-256 key size for the underlying AES-CMAC).

**`sdls_counter` lookup** — same dual-path rule as `sdls_encryption`'s
(see [its PRD](sdls_encryption.md) for the exact logic): checked at
`dict_msg["sdls_counter"]` first (via `pmt.dict_has_key`, not an
ambiguous `PMT_NIL` comparison), falling back to the nested
`dict_msg["sdls"]["security_header"]["sdls_counter"]` path only if the
top-level key is genuinely absent. Rejected if neither is present,
non-integer, or outside `0–65535`. **Symmetric with RX**:
`sdls_authentication_verify._extract_counter` uses the identical
`pmt.dict_has_key`-first pattern.

**Tag computation**: `data_to_authenticate = (nonce ‖ counter_2_bytes_big_endian) ‖ payload`, then `CMAC.new(auth_key, ciphermod=AES).update(data_to_authenticate).digest()`
— a 16-byte AES-CMAC tag, appended to (not replacing) the payload.
`auth_key` is deleted from the outgoing metadata; `sdls_counter` and every
other metadata key pass through unmodified.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): every rejection
logs at `error` (this TX-side block isn't in the raw-RF `warn` list).
`add_authentication`'s full body — the shape checks, the
`authentication_state=False` passthrough, key/counter extraction, tag
computation, and the final publish — is wrapped in one catch-log-drop
(`except Exception`), so an internal failure anywhere in either path is
logged and dropped rather than escaping the handler.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`add_authentication`, `Args`/`Returns`/`Raises` for the PMT-touching
helpers.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — this block
implements the authentication-tag half of SDLS's Security Trailer
construction.

**Simplified:** only AES-CMAC is supported — no algorithm agility, no
support for SDLS's other permitted MAC schemes.

## Known issues / TODOs

- **Default `nonce` is all-zero** (`b"\x00" * 14`) unless explicitly
  overridden — same as `sdls_encryption`.
- **The nested `sdls_counter` fallback lookup path is not an edge case —
  it's the only path `sdls_counter` takes in the real pipeline, and it
  has zero test coverage** (same finding as `sdls_encryption`'s PRD, and
  verified the same way directly in `inject_db.py`: `sdls_counter` is
  assigned *by* the `db_client` response, so `inject_db`'s merge logic
  always nests it under `sdls.security_header.sdls_counter`, never
  top-level). No test in `qa_sdls_authentication.py` or
  `qa_sdls_authentication_round_trip.py` constructs that shape, and
  `qa_tx_chain.py::test_002_end_to_end_message_routing` — the one test
  that runs the real wired topology — shims this block's real handler
  out. The code path this block's `sdls_counter` handling actually takes
  in production has never been run by any test in this repo.
- **`sdls_authentication_verify.py` (RX) has a known issue of its own,
  out of scope for this PRD** — its `verify_message` contains an entire
  alternate MAC-input construction path (triggered when
  `dict_msg["sdls"]["security_trailer"]` is present, optionally prefixing
  a reconstructed encapsulation header) that this block never produces —
  this block always appends the tag directly to the payload and never
  writes `sdls.security_trailer` or reads `encapsulation_header` metadata
  for tag construction. Under the current TX→RX pairing (the only path
  any test exercises) that alternate path is never reached, so there's no
  observed bug — see [sdls_authentication_verify.md](sdls_authentication_verify.md)
  for the full detail.

## Test coverage

- `python/soarr/qa_sdls_authentication.py` — 14 test methods
  (`test_instance` + `test_001`–`test_013`): `authentication_state=False`
  passthrough, tag correctness verified against an independently computed
  expected CMAC (confirming the payload itself is *not* modified, only a
  tag appended, and `auth_key` is removed from metadata), invalid key
  length / counter overflow / missing key / missing counter / non-dict
  metadata / non-u8vector payload all rejected with no publish, the tag
  changing between two messages that differ only by counter, a
  receiver-side CMAC verification check (using the test's own independent
  `_verify_tag` helper, not the real `sdls_authentication_verify` block)
  proving both a valid tag verifies and a tampered payload fails
  verification, two mock-forced internal-failure tests proving both
  publish paths are caught and dropped rather than raised through the
  real handler (`test_011` for the tagging path, `test_012` for the
  `authentication_state=False` passthrough path), and a non-PDU input
  rejected even with `authentication_state=False`, proving shape
  validation runs regardless of the flag (`test_013`).
- `python/soarr/qa_sdls_authentication_round_trip.py` — 10 test methods pairing
  this block with the real `sdls_authentication_verify`, in file order:
  round trip, tag tamper detection, disabled-authenticate with
  enabled-verify (fails), disabled-verify passthrough, nonce mismatch
  (fails), counter mismatch (fails), payload tamper detection, an
  empty-payload round trip, and multiple sequential messages.
- `python/soarr/qa_tx_chain.py::test_007_sdls_authentication_real_handler`
  — same pattern as the other TX blocks' "real handler" tests: builds a
  **fresh, standalone** instance and calls `add_authentication` directly,
  proving real tag-computation logic and metadata handling but not the
  `msg_connect` wiring (covered separately by
  `test_002_end_to_end_message_routing`, with this block's handler
  shimmed out).
