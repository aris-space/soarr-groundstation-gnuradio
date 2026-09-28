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
| `decryption_state` | bool | `True` | `False` makes the block a pure passthrough after its shape checks — input republished on `out` unchanged, no key/counter validation, no metadata mutation. Named to mirror `sdls_encryption`'s `encryption_state`. |
| `nonce` | bytes | `b"\x00" * 14` (all-zero) | Fixed for the block's lifetime, combined with the per-message `sdls_counter` into the same 16-byte AES-CTR counter block `sdls_encryption` used to encrypt. Must match `sdls_encryption`'s own `nonce` out-of-band, or the recovered plaintext is garbage rather than the original data (`qa_sdls_encryption_round_trip.py::test_005_nonce_mismatch_changes_plaintext`). Validated in `__init__` (`TypeError`/`ValueError` if not exactly 14 bytes). |

## Behavior / edge cases / current error handling

**`decryption_state=False`**: `decrypt_message` runs its three shape
checks first, unconditionally, before ever looking at
`decryption_state` — logged at `error` and dropped on failure exactly
as it would for `decryption_state=True` (see Error handling below); only
past those does it check the flag and, if `False`, republish the input
PDU on `out` completely unchanged, with no key/counter extraction.
**Same order as the TX sibling**: `sdls_encryption.add_encryption` also
checks shape first, `encryption_state` second — a malformed message is
rejected regardless of either flag's value. Both blocks wrap their
passthrough publish in catch-log-drop.

**`crypt_key` lookup** (`_validate_and_extract_key`): a PMT symbol hex
string or u8vector, rejected if not exactly 32 bytes (AES-256).
Detects an absent key via `pmt.dict_has_key`, checked before the value
is ever fetched — the same pattern `_extract_counter` uses below, and
`sdls_encryption.py`'s own `_extract_secret` uses too.

**`sdls_counter` lookup** (`_extract_counter`): checked first at
`dict_msg["sdls_counter"]` via `pmt.dict_has_key`, matching the TX
sibling's own `_extract_counter`, falling back to the nested
`dict_msg["sdls"]["security_header"]["sdls_counter"]` path only if the
top-level key is genuinely absent. In the real RX pipeline, this key
always arrives at the nested path: `ccsds_reader`
populates `sdls.security_header.sdls_counter` directly from the parsed
SDLS Security Header's `initialization_vector` field
(`ccsds_reader.py:368`), itself a fixed-width 16-bit wire field — so
this block's inbound counter is inherently bounded to 16 bits by the
wire format it was parsed from.

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

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `decrypt_message`'s
full body — the `pmt.is_pair(msg)`/`pmt.is_u8vector(payload_u8vector)`/
`pmt.is_dict(dict_msg)` shape checks, the `decryption_state=False`
passthrough, key/counter extraction, decryption, and the final
publish — is wrapped in catch-log-drop (`except Exception`). Every log
call this block makes reporting a drop condition is at `error`,
matching that this block sits downstream of `ccsds_reader` and isn't on
the raw-RF `warn` list; `sdls_encryption`'s own equivalent log calls are
likewise all at `error`.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): a real
class-level summary; full `Args`/`Raises` for `__init__`; full
`Args`/`Publishes`/`Drops when` for `decrypt_message` (the message
handler); full `Args`/`Returns` for the PMT-touching helpers
(`_validate_and_extract_key`, `_extract_counter`); full
`Args`/`Returns`/`Raises` for `_decrypt_payload` (non-PMT, plain
bytes/int, but not trivial — documented anyway, matching
`sdls_encryption._encrypt_payload`'s own treatment).

**Naming**: file, class, GRC block-id, both constructor parameters, and
every method name are already snake_case.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — the decryption
half of SDLS's Security Header/Trailer construction, same standard
[sdls_encryption.md](sdls_encryption.md) cites for the TX side.
**Simplified**: only AES-256-CTR is supported, matching the TX
sibling — no algorithm agility.

## Test coverage

- `python/soarr/qa_sdls_decryption.py` — 13 test methods (`test_instance`
  + `test_001`-`test_012`): `decryption_state=False` passthrough
  (`test_001`), AES-CTR decryption correctness with `crypt_key` removal
  verified against an independently computed ciphertext (`test_002`),
  invalid key length and counter overflow each dropped with no publish
  (`test_003`, `test_004`), missing `crypt_key`/missing `sdls_counter`
  each dropped with no publish (`test_005`, `test_006`), non-dict
  metadata and non-u8vector payload each dropped cleanly instead of
  raising (`test_007`, `test_008`), constructor `nonce` type/length
  validation (`test_009`, `test_010`), a mock-forced publish failure
  proven to be caught and dropped rather than raised through the real
  handler (`test_011`), and a top-level `sdls_counter` explicitly set to
  `PMT_NIL` (present, not absent) correctly rejected rather than
  silently falling back to a nested counter that would otherwise decrypt
  successfully (`test_012`).
- `python/soarr/qa_sdls_encryption_round_trip.py` — 8 test methods (`test_instance`
  + `test_001`-`test_007`), pairing this block with the real
  `sdls_encryption`: a full encrypt-then-decrypt round trip, a wrong key
  and a wrong counter each changing the recovered plaintext (proving
  AES-CTR's sensitivity to both), a missing key preventing decryption, a
  nonce mismatch changing the recovered plaintext, multiple sequential
  messages, and both blocks disabled together as a combined passthrough.
