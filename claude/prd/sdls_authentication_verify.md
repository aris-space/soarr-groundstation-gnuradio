# sdls_authentication_verify

## Purpose

Verifies the CMAC authentication tag `sdls_authentication` appended on
TX, over a CTR-style counter block plus the payload, and strips the tag
before republishing. The RX-side counterpart to `sdls_authentication`
(see [its PRD](sdls_authentication.md), which already documents several
of this block's own gaps from the TX side — cross-referenced throughout
below rather than repeated). Verify-then-decrypt order: this runs before
`sdls_decryption`, so the tag it checks covers still-encrypted bytes.

## Pipeline position

RX chain, between `inject_db` (RX instance, key lookup) and
`sdls_decryption`:

```
inject_db (RX instance).out → sdls_authentication_verify.in
sdls_authentication_verify.out → sdls_decryption.in
```

Not in [coding-standards.md](../coding-standards.md)'s raw-RF `warn`
list — this block sits downstream of `ccsds_reader`, where malformed
input is "no longer plausibly channel noise" per that list's own
framing. No `.grc` flowgraph file exists anywhere in this repo to
independently confirm the wiring above; it matches
[architecture.md](../architecture.md)'s documented RX chain.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Metadata must include `auth_key` (symbol hex string or u8vector, 32 bytes) and `sdls_counter` (integer, 0-65535, same dual-lookup rule as `sdls_authentication`'s own counter lookup — see Behavior). Payload is the tagged ciphertext: either `payload‖16-byte tag` (the trailer-in-payload case), or ciphertext alone with the tag supplied separately via `dict_msg["sdls"]["security_trailer"]` (the trailer-in-metadata case — see Behavior). | `pmt.cons({auth_key: "00112233...", sdls_counter: 0x1234}, u8vector(ciphertext ‖ tag))` |
| `out` | output | PDU: `(metadata_dict . payload_u8vector)`. `auth_key` removed from metadata; payload is the tag-stripped (and, in the trailer-in-metadata case, possibly encapsulation-header-stripped) bytes. Published only when the tag verifies. | `pmt.cons({sdls_counter: 0x1234}, u8vector(ciphertext))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `authentication_state` | bool | `True` | `False` makes the block a pure passthrough — input republished on `out` unchanged, no validation, no metadata mutation. Matches `sdls_authentication`'s own parameter name (same convention, not new). |
| `nonce` | bytes | `b"\x00" * 14` (all-zero) | Fixed for the block's lifetime, combined with the per-message `sdls_counter` to rebuild the same 16-byte counter block `sdls_authentication` prepended before computing its tag. Must match `sdls_authentication`'s own `nonce` value out-of-band, or every tag fails to verify (`qa_AuthenticateAuthVerify.py::test_005_nonce_mismatch_fails`). Validated in `__init__` (`TypeError`/`ValueError` if not exactly 14 bytes) — the error message hardcodes "14" rather than referencing `NONCE_LEN`, cosmetic only since the two currently agree. |

## Behavior / edge cases / current error handling

**`authentication_state=False`**: republishes the input PDU on `out`
completely unchanged — no shape validation, no key/counter extraction,
no exception handling around the publish call (unlike
`sdls_authentication`'s own passthrough path, which wraps its publish in
catch-log-drop).

**`auth_key`/`sdls_counter` lookup** (`_extract_secret`,
`_extract_counter`): same shapes/rules as
[sdls_authentication.md](sdls_authentication.md) documents for its own
`_extract_secret`/`_extract_counter` — a PMT symbol hex string or
u8vector for the key (rejected if not exactly 32 bytes), an integer PMT
counter checked first at `dict_msg["sdls_counter"]`, falling back to
the nested `dict_msg["sdls"]["security_header"]["sdls_counter"]` path.
**Not implemented the same way as the TX sibling**: both of this
block's lookups use `pmt.dict_ref(..., default=pmt.PMT_NIL)` followed by
`pmt.eqv(value, pmt.PMT_NIL)` to detect absence — `sdls_authentication`'s
own `_extract_counter` instead checks `pmt.dict_has_key(...)` first,
unambiguously distinguishing "key absent" from "key present with value
`PMT_NIL`" (see [sdls_authentication.md](sdls_authentication.md#behavior--edge-cases--current-error-handling),
which documents this exact asymmetry from the TX side). This block has
the ambiguous pattern in *both* lookups, not just the counter one.

**Two distinct tag-input reconstructions**, selected by whether
`dict_msg["sdls"]["security_trailer"]` is present:

1. **Trailer absent** (`tag is None`): assumes the last 16 bytes of the
   payload *are* the tag (`_split_payload_tag`) — rejects (no publish)
   if the payload is shorter than 16 bytes. `mac_payload` is the
   remaining, tag-stripped bytes; `out_payload_bytes` is the same.
2. **Trailer present** (`dict_msg["sdls"]["security_trailer"]` is a
   u8vector — the shape `ccsds_reader` produces when `sdls_type` is `2`
   or `3`, see [ccsds_reader.md](ccsds_reader.md)): the tag comes from
   there instead, and `_build_encapsulation_header` reconstructs the
   encapsulation header's raw bytes from `dict_msg["encapsulation_header"]`
   metadata (`ccsds_reader` parses this into fields but doesn't keep the
   original bytes) — an **independent reimplementation of the same
   bit-packing logic `ccsds_reader.encapsulation_header()` already
   defines**, in a different file, that must stay byte-for-byte
   consistent with it or CMAC verification silently breaks. If the
   payload already starts with those reconstructed bytes (`has_encap`),
   they're stripped from `out_payload_bytes`; otherwise they're
   prepended only for `mac_payload` (the CMAC input), matching the bytes
   `sdls_authentication` originally tagged before `ccsds_reader` split
   the encapsulation header out of the payload.

[sdls_authentication.md](sdls_authentication.md#known-issues--todos)
already flags this second path as "untested, unexplained" from the TX
side, since `sdls_authentication` itself never produces a
`security_trailer`-shaped output — confirmed here too:
`qa_sdls_authentication_verify.py::test_013`/`test_014` exercise it
directly (constructing the metadata shape by hand), but no test in this
repo produces that shape via the real TX→RX pipeline.

**Tag verification** (`_verify_tag`): `CMAC.new(secret, ciphermod=AES).update(counter_bytes + mac_payload).verify(tag)`
— on `ValueError` (mismatch), returns `False` rather than propagating,
consistent with pycryptodome's own `verify()` contract.

**Error handling**: `verify_message` checks `pmt.is_pair(msg)`,
`pmt.is_u8vector(payload_u8vector)`, and `pmt.is_dict(dict_msg)`, but
**raises `ValueError` for all three** instead of catch-log-drop — the
first raise's message text even reads "Expected a PMT pair for
**decryption** input," misdescribing this as the decryption block, not
authentication-verify. `sdls_authentication`'s own `add_authentication`
(the TX sibling) already handles the identical three checks with
log-and-drop instead of raising — direct precedent for what this block's
fix should look like. Past those three checks, **nothing in
`verify_message` is wrapped in any exception handling at all** — not the
passthrough path, not the key/counter extraction, not tag
reconstruction or verification, not the final publish. An unexpected
failure anywhere in that path (e.g. a `_build_encapsulation_header`
integer-conversion error, or a `message_port_pub` failure) would crash
the handler thread.

Every log call this block makes (11 total: 6 in `_extract_secret`/
`_extract_counter`, 1 in `_split_payload_tag`, 1 for the counter-range
check, 2 in `verify_message`'s own "failed to extract" messages, 1 for
tag-verification failure) is currently at either `warn` (10 of them) or
`error` (1 — the counter-range check, already correct). Since this
block isn't on the raw-RF `warn` list, **all 10 `warn` calls are at the
wrong level** — the reverse of the error→warn fix every RX block earlier
in this pipeline (`cltu_deframer` through `ccsds_reader`) needed;
`sdls_authentication`'s own equivalent log calls are already all at
`error`, confirming which level this block's should be too.

**Docstrings**: none of ADR-0004's required coverage is present. The
class itself still carries `gr_modtool`'s placeholder
(`"""docstring for block sdls_authentication_verify"""`); `__init__` and
`verify_message` (the message handler) have no docstring. Every private
helper — `_extract_secret`, `_extract_counter` (PMT-touching),
`_build_ctr_counter_block`, `_split_payload_tag`, `_verify_tag` (non-PMT,
plain bytes/int), `_pmt_dict_get_int`, `_build_encapsulation_header`
(PMT-touching) — has none either.
`sdls_authentication.py`'s equivalent methods already carry full
ADR-0004-compliant docstrings, directly reusable as a template for the
PMT-touching ones here.

**Naming**: file, class, GRC block-id, both constructor parameters, and
every method name are already snake_case.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — the
authentication-tag half of SDLS's Security Trailer, same standard
[sdls_authentication.md](sdls_authentication.md) cites for the TX side.
**Simplified**: only AES-CMAC is supported, matching the TX sibling —
no algorithm agility.

## Known issues / TODOs

- **`verify_message` raises instead of catch-log-drop, and has no
  exception handling anywhere past its three shape checks** — the
  largest ADR-0003 gap, matching the same category of issue found and
  fixed in several RX blocks earlier in this pass
  (`ccsds_receiver`, `ccsds_reader`). `sdls_authentication.add_authentication`
  is a direct, already-fixed template for the equivalent checks.
- **10 of 11 log calls are at the wrong level (`warn` instead of
  `error`)** — this block is not on the raw-RF `warn` list;
  `sdls_authentication`'s identical log calls are already correctly at
  `error`.
- **Both `_extract_secret` and `_extract_counter` use an ambiguous
  `PMT_NIL`-comparison to detect an absent key**, unlike
  `sdls_authentication._extract_counter`'s `pmt.dict_has_key`-first
  pattern — see Behavior above and
  [sdls_authentication.md](sdls_authentication.md)'s own note on this
  asymmetry (documented from the TX side before this PRD existed).
- **The trailer-in-metadata tag-reconstruction path
  (`_build_encapsulation_header`) independently reimplements
  `ccsds_reader.encapsulation_header()`'s bit-packing logic** in a
  different file — the two must stay byte-for-byte consistent by
  convention, not by any shared code, and this path has never been
  exercised through the real ccsds_reader→sdls_authentication_verify
  pipeline in any test (see Behavior above).
- **The first raised `ValueError`'s message text says "decryption"**,
  not verification/authentication — a copy-paste artifact from another
  block's boilerplate.

## Test coverage

- `python/soarr/qa_sdls_authentication_verify.py` — 15 test methods
  (`test_instance` + `test_001`-`test_014`): `authentication_state=False`
  passthrough (`test_001`), a valid tag verifying and `auth_key` removed
  from the output metadata (`test_002`), invalid key length and counter
  overflow each dropped with no publish (`test_003`, `test_004`), missing
  `auth_key`/missing `sdls_counter` each dropped with no publish
  (`test_005`, `test_006`), non-dict metadata and non-u8vector payload
  each raising `ValueError` through the handler — the current, unfixed
  behavior (`test_007`, `test_008`), a tampered tag failing verification
  with no publish (`test_009`), a too-short payload (no room for a
  16-byte tag) dropped with no publish (`test_010`), constructor `nonce`
  type/length validation (`test_011`, `test_012`), and the two
  trailer-in-metadata reconstruction paths described in Behavior above
  — with (`test_013`) and without (`test_014`) the payload already
  containing the reconstructed encapsulation-header prefix.
- `python/soarr/qa_AuthenticateAuthVerify.py` — 10 test methods
  (`test_instance` + `test_001`-`test_009`), pairing this block with the
  real `sdls_authentication`: a full authenticate-then-verify round trip,
  tag-tamper and payload-tamper detection, `authentication_state`
  disabled on one side while enabled on the other (both directions),
  nonce mismatch, counter mismatch, an empty-payload round trip, and
  multiple sequential messages with different counters. Every case in
  this file uses the trailer-in-payload path (`_split_payload_tag`) —
  none constructs the trailer-in-metadata shape `ccsds_reader` would
  actually produce.
