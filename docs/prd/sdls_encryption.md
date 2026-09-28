# sdls_encryption

## Purpose

Encrypts a PDU's payload with AES-256-CTR, using a key and per-message
counter supplied in the PDU's metadata. The first of the two SDLS
protection steps applied on TX (encrypt-then-authenticate — see
[architecture.md](../architecture.md)).

## Pipeline position

TX chain, between `encapsulation_header` and `sdls_authentication`:

```
encapsulation_header.out → sdls_encryption.in
sdls_encryption.out → sdls_authentication.in
```

RX counterpart: [sdls_decryption](sdls_decryption.md).

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Metadata must include `crypt_key` (symbol hex string or u8vector, 32 bytes) and `sdls_counter` (integer, 0–65535) — see Behavior for the exact lookup rules. | `pmt.cons({crypt_key: "00112233...", sdls_counter: 0x1234}, u8vector(plaintext))` |
| `out` | output | PDU: `(metadata_dict . ciphertext_u8vector)`. `crypt_key` is removed from the metadata; `sdls_counter` and everything else pass through unchanged (`sdls_header`, downstream, needs `sdls_counter` to embed it as the wire-level IV). | `pmt.cons({sdls_counter: 0x1234}, u8vector(ciphertext))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `encryption_state` | bool | `True` | `False` makes the block a pure passthrough after its shape checks — input republished on `out` unchanged, no key/counter validation, no metadata mutation. Named to mirror `sdls_decryption`'s `decryption_state`. |
| `nonce` | bytes | `b"\x00" * 14` (all-zero) | Fixed for the block's lifetime, combined with the per-message `sdls_counter` into one 16-byte AES-CTR counter block. Never transmitted — RX's `sdls_decryption` must be configured with the identical value out-of-band. Validated in `__init__` (`TypeError`/`ValueError` if not exactly 14 bytes). |

## Behavior / edge cases / current error handling

**Shape validation runs first, unconditionally**: `add_encryption`
rejects a non-PDU input, non-dict metadata, or non-u8vector payload
without publishing, regardless of `encryption_state`. Only past those
checks does it look at `encryption_state`.

**`encryption_state=False`**: republishes the already-shape-validated
input PDU on `out` completely unchanged — no key/counter extraction.
**Same order as the RX sibling**: `sdls_decryption.decrypt_message`
also checks shape first, `decryption_state` second.

**`crypt_key` lookup** (`_extract_secret`) — accepts either form:
- a PMT symbol holding a hex string (decoded via `bytes.fromhex`), or
- a u8vector.

Detects an absent key via `pmt.dict_has_key`, checked before the value
is ever fetched — the same pattern `_extract_counter` uses below, and
`sdls_decryption.py`'s own `_validate_and_extract_key` uses too.
Rejected (no publish) if absent, an invalid hex string, an unsupported
PMT type, or not exactly 32 bytes (AES-256 key size).

**`sdls_counter` lookup** — checked in two places, in order:
1. `dict_msg["sdls_counter"]` directly (checked for presence via
   `pmt.dict_has_key`, not by comparing the looked-up value to
   `pmt.PMT_NIL` — the latter would be unable to distinguish "key
   present with a nil value" from "key absent"), or
2. if the top-level key is genuinely absent, the nested path
   `dict_msg["sdls"]["security_header"]["sdls_counter"]`.

Rejected (no publish) if neither is present, the value isn't an integer
PMT, or it's outside `0–65535` (the block's counter is 2 bytes — see
Known Issues for a real inconsistency with how `db_client` produces this
value).

**Encryption**: `AES.new(key, AES.MODE_CTR, nonce=self.nonce, initial_value=counter).encrypt(payload)`. `crypt_key` is deleted from the outgoing
metadata (key material isn't forwarded downstream); `sdls_counter` and
every other metadata key pass through unmodified.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): every rejection
above logs at `error` (this TX-side block isn't in the raw-RF `warn`
list). `add_encryption`'s full body — the shape checks, the
`encryption_state=False` passthrough, key/counter extraction,
encryption, and the final publish — is wrapped in one catch-log-drop
(`except Exception`), so an internal failure anywhere in either path
(e.g. a future encryption-library incompatibility, or a publish
failure) is logged and dropped rather than escaping the handler.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`add_encryption`, and `Args`/`Returns`/`Raises` for the three PMT-touching
helpers.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — this block
implements the encryption half of SDLS's Security Header/Trailer
construction. `sdls_counter` is the spec's IV — transmitted in the
Security Header, distinct per frame. `nonce` is a fixed 14-byte value set
once per block instance and never transmitted; RX must share it
out-of-band. The two are combined (`nonce` ‖ `sdls_counter`) into the
16-byte AES-CTR counter block.

**Simplified:** only AES-256-CTR is supported — no algorithm agility, no
support for SDLS's other permitted cipher suites.

## Known issues / TODOs

- **No (nonce, counter) reuse protection.** This block trusts that
  `sdls_counter` is unique per message for a given key — it has no
  internal state to detect or prevent reuse. Under normal operation
  (`db_client` issuing a monotonically-incrementing counter, no reset)
  this holds; if `db_client`'s `auto_reset_counters=True` is ever set on
  an instance actually used with real key material (not just dummy/test
  payloads), the counter would wrap and repeat, and — combined with this
  block's fixed `nonce` — reusing a (nonce, counter) pair under AES-CTR
  with the same key breaks confidentiality for both messages involved.
- **Default `nonce` is all-zero** (`b"\x00" * 14`) unless explicitly
  overridden by the caller/GRC flowgraph.
- **The nested `sdls_counter` fallback lookup path
  (`dict_msg["sdls"]["security_header"]["sdls_counter"]`) is not an edge
  case — it's the only path `sdls_counter` takes in the real pipeline,
  and it has zero test coverage.** Verified directly in `inject_db.py`:
  `sdls_counter` is assigned *by* the `db_client` response, not known
  before the DB call, so `inject_db`'s merge logic
  (`_merge_key_into_nested`, `inject_db.py:190-196`) always nests it
  under `sdls.security_header.sdls_counter` — never top-level. Every test
  in `qa_sdls_encryption.py` constructs `sdls_counter` at the top level
  directly, bypassing `inject_db` entirely, and
  `qa_layoutTest.py::test_002_end_to_end_message_routing` — the one test
  that *does* run the real wired topology — shims this block's real
  handler out. The code path this block's `sdls_counter` handling
  actually takes in production has never been run by any test in this
  repo.

## Test coverage

- `python/soarr/qa_sdls_encryption.py` — 16 test methods (`test_instance`
  + `test_001`–`test_015`): `encryption_state=False` passthrough, AES-CTR
  encryption correctness with `crypt_key` removal verified against an
  independently computed expected ciphertext, invalid key length /
  counter overflow / missing key / missing counter / non-dict metadata /
  non-u8vector payload all rejected with no publish, constructor `nonce`
  type/length validation, counter value changing ciphertext between two
  messages with the same key, a full
  encrypt-then-decrypt-with-PyCryptodome-directly round trip, two
  mock-forced internal-failure tests proving both publish paths are
  caught and dropped rather than raised through the real handler
  (`test_013` for the encrypted path, mirroring `encapsulation_header`'s
  `test_020`; `test_014` for the `encryption_state=False` passthrough
  path specifically), and a non-PDU input rejected even with
  `encryption_state=False`, proving shape validation runs regardless of
  the flag (`test_015`).
- `python/soarr/qa_EncryptDecrypt.py` — 8 test methods pairing this block
  with its RX counterpart `sdls_decryption`: round trip, wrong key, wrong
  counter, and nonce-mismatch all changing the recovered plaintext as
  expected; missing key preventing decryption; multiple sequential
  messages; both blocks disabled as a combined passthrough.
- `python/soarr/qa_layoutTest.py::test_006_sdls_encryption_real_handler` —
  same pattern as `encapsulation_header`'s `test_008`: builds a **fresh,
  standalone** instance and calls `add_encryption` directly, proving real
  encryption logic and metadata handling (`crypt_key` removed,
  `sdls_counter`/`frame_id` preserved) but not the `msg_connect` wiring.
  `test_002_end_to_end_message_routing` covers the wiring separately, with
  this block's handler shimmed out — same split as documented in
  `encapsulation_header`'s PRD.
