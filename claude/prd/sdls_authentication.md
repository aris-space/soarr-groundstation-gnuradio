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

RX counterpart: `sdls_authentication_verify` (not yet documented — later
in this plan's block order; already uses the `authentication_state`
parameter name this block is renamed to match, below).

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Metadata must include `auth_key` (symbol hex string or u8vector, 32 bytes) and `sdls_counter` (integer, 0–65535) — same dual-lookup rule as `sdls_encryption`'s `sdls_counter`. | `pmt.cons({auth_key: "00112233...", sdls_counter: 0x1234}, u8vector(ciphertext))` |
| `out` | output | PDU: `(metadata_dict . payload‖tag_u8vector)`. `auth_key` is removed from the metadata; `sdls_counter` and everything else pass through unchanged. Output is 16 bytes longer than the input payload (the CMAC tag). | `pmt.cons({sdls_counter: 0x1234}, u8vector(ciphertext ‖ 16-byte tag))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `authentication_state` | bool | `True` | `False` makes the block a pure passthrough — input republished on `out` unchanged, no validation, no metadata mutation. Renamed from `state` to match `sdls_authentication_verify`'s existing `authentication_state` parameter — not a new convention, matching the name its own RX counterpart already uses. |
| `nonce` | bytes | `b"\x00" * 14` (all-zero) | Fixed for the block's lifetime, combined with the per-message `sdls_counter` into the 16-byte block prepended to the payload before CMAC (see Behavior). Never transmitted — RX's `sdls_authentication_verify` must be configured with the identical value out-of-band. **Independent of `sdls_encryption`'s `nonce`** — a separate parameter, separate key (`auth_key` vs. `crypt_key`), by design: using distinct key/nonce material for encryption vs. authentication is standard practice, not a bug, even though both currently default to the same all-zero value. Validated in `__init__` (`TypeError`/`ValueError` if not exactly 14 bytes). |

## Behavior / edge cases / current error handling

**`authentication_state=False`**: republishes the input PDU on `out`
completely unchanged — no shape validation, no key/counter extraction.

**Input validation** (`authentication_state=True`): rejects a non-PDU
input, non-dict metadata, or non-u8vector payload without publishing.

**`auth_key` lookup** — identical rules to `sdls_encryption`'s
`crypt_key`: a PMT symbol holding a hex string, or a u8vector; rejected
if absent, malformed, or not exactly 32 bytes (AES-256 key size for the
underlying AES-CMAC).

**`sdls_counter` lookup** — same dual-path rule as `sdls_encryption`'s
(see [its PRD](sdls_encryption.md) for the exact logic): checked at
`dict_msg["sdls_counter"]` first (via `pmt.dict_has_key`, not an
ambiguous `PMT_NIL` comparison), falling back to the nested
`dict_msg["sdls"]["security_header"]["sdls_counter"]` path only if the
top-level key is genuinely absent. Rejected if neither is present,
non-integer, or outside `0–65535`. **Not yet symmetric with RX**:
`sdls_authentication_verify._extract_counter` still uses the older
ambiguous `PMT_NIL`-comparison pattern this fix replaced here — that
block hasn't been reviewed/fixed yet (see Known Issues).

**Tag computation**: `data_to_authenticate = (nonce ‖ counter_2_bytes_big_endian) ‖ payload`, then `CMAC.new(auth_key, ciphermod=AES).update(data_to_authenticate).digest()`
— a 16-byte AES-CMAC tag, appended to (not replacing) the payload.
`auth_key` is deleted from the outgoing metadata; `sdls_counter` and every
other metadata key pass through unmodified.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): every rejection
logs at `error` (this TX-side block isn't in the raw-RF `warn` list).
Both publish paths — the `authentication_state=False` passthrough and the
tag-computation-through-publish path — are individually wrapped in
catch-log-drop.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`add_authentication`, `Args`/`Returns`/`Raises` for the PMT-touching
helpers.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — this block
implements the authentication-tag half of SDLS's Security Trailer
construction. See [SDLS](../../CONTEXT.md) in the glossary.

**Simplified:** only AES-CMAC is supported — no algorithm agility, no
support for SDLS's other permitted MAC schemes.

## Known issues / TODOs

- **Same counter-width mismatch with `db_client` as `sdls_encryption`**
  (see [its PRD](sdls_encryption.md#known-issues--todos) for the full
  analysis) — this block imports the identical `COUNTER_MAX = 0xFFFF`
  16-bit ceiling, while `db_client` models `sdls_counter` as 32-bit.
- **Default `nonce` is all-zero** (`b"\x00" * 14`) unless explicitly
  overridden — same as `sdls_encryption`.
- **The nested `sdls_counter` fallback lookup path is untested** — same
  gap as `sdls_encryption`, no test in `qa_sdls_authentication.py` or
  `qa_AuthenticateAuthVerify.py` exercises it.
- **`sdls_authentication_verify.py` (RX) is not yet reviewed/fixed** —
  still has the ambiguous `PMT_NIL`-comparison counter lookup this block's
  `_extract_counter` no longer has (see above), and its `verify_message`
  contains an entire alternate MAC-input construction path (triggered when
  `dict_msg["sdls"]["security_trailer"]` is present, optionally prefixing
  a reconstructed encapsulation header) that this block never produces —
  this block always appends the tag directly to the payload and never
  writes `sdls.security_trailer` or reads `encapsulation_header` metadata
  for tag construction. Under the current TX→RX pairing (the only path
  any test exercises) that alternate path is never reached, so there's no
  observed bug — but it's untested, unexplained, and worth investigating
  when `sdls_authentication_verify` gets its own PRD.

## Test coverage

- `python/soarr/qa_sdls_authentication.py` — 13 test methods
  (`test_instance` + `test_001`–`test_012`): `authentication_state=False`
  passthrough, tag correctness verified against an independently computed
  expected CMAC (confirming the payload itself is *not* modified, only a
  tag appended, and `auth_key` is removed from metadata), invalid key
  length / counter overflow / missing key / missing counter / non-dict
  metadata / non-u8vector payload all rejected with no publish, the tag
  changing between two messages that differ only by counter, a
  receiver-side CMAC verification check (using the test's own independent
  `_verify_tag` helper, not the real `sdls_authentication_verify` block)
  proving both a valid tag verifies and a tampered payload fails
  verification, and two mock-forced internal-failure tests proving both
  publish paths are caught and dropped rather than raised through the
  real handler (`test_011` for the tagging path, `test_012` for the
  `authentication_state=False` passthrough path).
- `python/soarr/qa_AuthenticateAuthVerify.py` — 10 test methods pairing
  this block with the real `sdls_authentication_verify`, in file order:
  round trip, tag tamper detection, disabled-authenticate with
  enabled-verify (fails), disabled-verify passthrough, nonce mismatch
  (fails), counter mismatch (fails), payload tamper detection, an
  empty-payload round trip, and multiple sequential messages.
- `python/soarr/qa_layoutTest.py::test_007_sdls_authentication_real_handler`
  — same pattern as the other TX blocks' "real handler" tests: builds a
  **fresh, standalone** instance and calls `add_authentication` directly,
  proving real tag-computation logic and metadata handling but not the
  `msg_connect` wiring (covered separately by
  `test_002_end_to_end_message_routing`, with this block's handler
  shimmed out).
