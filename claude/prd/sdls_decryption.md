# sdls_decryption

## Purpose

Decrypts a PDU's payload with AES-256-CTR, using a key and per-message
counter supplied in the PDU's metadata. The RX-side counterpart to
[sdls_encryption](sdls_encryption.md), and the terminal block of the RX
chain — nothing in this repo consumes its output further.

## Pipeline position

RX chain, after `sdls_authentication_verify`:

```
sdls_authentication_verify.out → sdls_decryption.in
```

[architecture.md](../architecture.md) confirms this order is required by
the crypto construction, not just how the flowgraph happens to be
wired: `sdls_authentication_verify`'s CMAC tag is computed over the
still-encrypted bytes, so decrypting first would break tag verification
for any real payload. That external flowgraph isn't in this repo, so
there's no in-repo `.grc` example or test proving the order — treated as
confirmed-in-practice, not self-verifying from this repo alone. No
`.grc` flowgraph file exists anywhere in this repo at all.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . ciphertext_u8vector)`. Metadata must include `crypt_key` (symbol hex string or u8vector, 32 bytes) and `sdls_counter` (integer, 0-65535) — see Behavior for the exact lookup rules. | `pmt.cons({crypt_key: "00112233...", sdls_counter: 0x1234}, u8vector(ciphertext))` |
| `out` | output | PDU: `(metadata_dict . plaintext_u8vector)`. `crypt_key` removed from metadata; `sdls_counter` and everything else pass through unchanged. | `pmt.cons({sdls_counter: 0x1234}, u8vector(plaintext))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `decryption_state` | bool | `True` | `False` makes the block a pure passthrough — input republished on `out` unchanged, no validation, no metadata mutation. Named to mirror `sdls_encryption`'s `encryption_state`. |
| `nonce` | bytes | `b"\x00" * 14` (all-zero) | Fixed for the block's lifetime, combined with the per-message `sdls_counter` into the same 16-byte AES-CTR counter block `sdls_encryption` used to encrypt. Must match `sdls_encryption`'s own `nonce` out-of-band, or the recovered plaintext is garbage rather than the original data (`qa_EncryptDecrypt.py::test_005_nonce_mismatch_changes_plaintext`). Validated in `__init__` (`TypeError`/`ValueError` if not exactly 14 bytes). |

## Behavior / edge cases / current error handling

**`decryption_state=False`**: `decrypt_message` still runs its three
shape checks first, unconditionally, before ever looking at
`decryption_state`; only past those does it check the flag and, if
`False`, republish the input PDU on `out` completely unchanged, with no
key/counter extraction. **Opposite order from the TX sibling**:
`sdls_encryption.add_encryption` checks `encryption_state` *first*,
before any shape validation.

**`crypt_key` lookup** (`_validate_and_extract_key`): a PMT symbol hex
string or u8vector, rejected if not exactly 32 bytes (AES-256).
Detects an absent key via `pmt.dict_ref(..., default=pmt.PMT_NIL)`
followed by `pmt.eqv(key, pmt.PMT_NIL)` — the same pattern
[sdls_encryption.md](sdls_encryption.md) documents for its own
`_extract_secret`, which has not been changed either; not a gap
specific to this block.

**`sdls_counter` lookup** (`_extract_counter`): checked first at
`dict_msg["sdls_counter"]` via `pmt.dict_ref(..., default=pmt.PMT_NIL)`/
`pmt.eqv(..., pmt.PMT_NIL)` — **ambiguous**, unable to distinguish "key
absent" from "key present with value `PMT_NIL`" — falling back to the
nested `dict_msg["sdls"]["security_header"]["sdls_counter"]` path.
`sdls_encryption`'s own `_extract_counter` instead checks
`pmt.dict_has_key(...)` first, unambiguously distinguishing the two
cases (see [sdls_encryption.md](sdls_encryption.md)). In the real RX
pipeline, this key always arrives at the nested path: `ccsds_reader`
populates `sdls.security_header.sdls_counter` directly from the parsed
SDLS Security Header's `initialization_vector` field
(`ccsds_reader.py:368`), itself a fixed-width 16-bit wire field — so
unlike `sdls_encryption.md`'s documented counter-width mismatch with
`db_client` (which can issue an unbounded, monotonically-incrementing
32-bit counter on TX), this block's inbound counter is inherently
bounded to 16 bits by the wire format it was parsed from, not by
anything `db_client` supplies.

**Decryption** (`_decrypt_payload`): `AES.new(key, AES.MODE_CTR, nonce=self.nonce, initial_value=counter).decrypt(payload)`
— AES-CTR is symmetric, so this is the same operation
`sdls_encryption._encrypt_payload` performs, just applied to already
counter-and-nonce-keyed ciphertext. Re-validates `self.nonce` length and
`counter` range defensively before decrypting (both already validated
by the time this is called), raising `ValueError` if either check
somehow fails — the identical defensive-recheck pattern
`sdls_encryption._encrypt_payload` uses. `crypt_key` is deleted from the
outgoing metadata; `sdls_counter` and every other metadata key pass
through unmodified.

**Error handling**: `decrypt_message` checks `pmt.is_pair(msg)`,
`pmt.is_u8vector(payload_u8vector)`, and `pmt.is_dict(dict_msg)`, but
**raises `ValueError` for all three** instead of catch-log-drop.
`sdls_encryption.add_encryption` (the TX sibling) already handles the
identical three checks with log-and-drop instead of raising — direct
precedent for what this block's fix should look like. Past those three
checks, **nothing in `decrypt_message` is wrapped in any exception
handling at all** — not the `decryption_state=False` passthrough
(unlike `sdls_encryption`'s own passthrough, which is wrapped), not key/
counter extraction, not decryption, not the final publish. An
unexpected failure anywhere in that path (e.g. a `message_port_pub`
failure) would crash the handler thread.

Every log call this block makes reporting a drop condition (9 total: 4
in `_validate_and_extract_key`, 2 in `_extract_counter` plus 1 for the
counter-range check, 2 in `decrypt_message`'s own "failed to extract"
messages — separate from the one `self.logger.info("OK")` on success)
is currently at either `warn` (8 of them) or `error` (1 — the
counter-range check, already correct). Since this block sits downstream
of `ccsds_reader` and isn't on the raw-RF `warn` list, **all 8 `warn`
calls are at the wrong level**; `sdls_encryption`'s own equivalent log
calls are already all at `error`.

**Docstrings**: none of ADR-0004's required coverage is present. The
class itself still carries `gr_modtool`'s placeholder
(`"""docstring for block sdls_decryption"""`); `__init__` and
`decrypt_message` (the message handler) have no docstring. Every
private helper — `_validate_and_extract_key`, `_extract_counter`
(PMT-touching), `_decrypt_payload` (non-PMT, plain bytes/int) — has none
either. `sdls_encryption.py`'s equivalent methods already carry full
ADR-0004-compliant docstrings, directly reusable as a template for the
PMT-touching ones here.

**Naming**: file, class, GRC block-id, both constructor parameters, and
every method name are already snake_case.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — the decryption
half of SDLS's Security Header/Trailer construction, same standard
[sdls_encryption.md](sdls_encryption.md) cites for the TX side.
**Simplified**: only AES-256-CTR is supported, matching the TX
sibling — no algorithm agility.

## Known issues / TODOs

- **`_validate_and_extract_key` still uses the ambiguous
  `PMT_NIL`-comparison pattern** to detect an absent `crypt_key`, rather
  than `_extract_counter`'s `pmt.dict_has_key`-first check. Matches
  `sdls_encryption._extract_secret`'s own current implementation, so
  this isn't an RX-specific gap; fixing it would mean fixing the
  identical pattern in both blocks together, not resolved here.
- **`decrypt_message` raises instead of catch-log-drop, and has no
  exception handling anywhere past its three shape checks.**
  `sdls_encryption.add_encryption` is a direct template for the
  equivalent checks, already handling them with log-and-drop instead of
  raising.
- **`decrypt_message` still checks shape before `decryption_state`**,
  the opposite order from `sdls_encryption.add_encryption` — a
  structural difference between the two blocks, not resolved here.

## Test coverage

- `python/soarr/qa_sdls_decryption.py` — 11 test methods (`test_instance`
  + `test_001`-`test_010`): `decryption_state=False` passthrough
  (`test_001`), AES-CTR decryption correctness with `crypt_key` removal
  verified against an independently computed ciphertext (`test_002`),
  invalid key length and counter overflow each dropped with no publish
  (`test_003`, `test_004`), missing `crypt_key`/missing `sdls_counter`
  each dropped with no publish (`test_005`, `test_006`), non-dict
  metadata and non-u8vector payload each raising `ValueError` through
  the handler — the current, unfixed behavior (`test_007`, `test_008`),
  and constructor `nonce` type/length validation (`test_009`,
  `test_010`). No test covers a forced exception past the shape checks
  (e.g. a mock-forced publish failure) — every other SDLS block's test
  file in this repo has one, this is the only one that doesn't.
- `python/soarr/qa_EncryptDecrypt.py` — 8 test methods (`test_instance`
  + `test_001`-`test_007`), pairing this block with the real
  `sdls_encryption`: a full encrypt-then-decrypt round trip, a wrong key
  and a wrong counter each changing the recovered plaintext (proving
  AES-CTR's sensitivity to both), a missing key preventing decryption, a
  nonce mismatch changing the recovered plaintext, multiple sequential
  messages, and both blocks disabled together as a combined passthrough.
