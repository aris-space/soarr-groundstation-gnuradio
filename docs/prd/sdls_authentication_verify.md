# sdls_authentication_verify

## Purpose

Verifies the CMAC authentication tag `sdls_authentication` appended on
TX, over a CTR-style counter block plus the payload, and strips the tag
before republishing. The RX-side counterpart to
[sdls_authentication](sdls_authentication.md). Verify-then-decrypt
order: this runs before `sdls_decryption`, so the tag it checks covers
still-encrypted bytes.

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
| `authentication_state` | bool | `True` | `False` makes the block a pure passthrough after its shape checks — input republished on `out` unchanged, no key/counter validation, no metadata mutation. Matches `sdls_authentication`'s own parameter name (same convention, not new). |
| `nonce` | bytes | `b"\x00" * 14` (all-zero) | Fixed for the block's lifetime, combined with the per-message `sdls_counter` to rebuild the same 16-byte counter block `sdls_authentication` prepended before computing its tag. Must match `sdls_authentication`'s own `nonce` value out-of-band, or every tag fails to verify (`qa_sdls_authentication_round_trip.py::test_005_nonce_mismatch_fails`). Validated in `__init__` (`TypeError`/`ValueError` if not exactly 14 bytes) — the error message hardcodes "14" rather than referencing `NONCE_LEN`, cosmetic only since the two currently agree. |

## Behavior / edge cases / current error handling

**Shape validation runs first, unconditionally**: `verify_message`
rejects a non-PDU input, non-dict metadata, or non-u8vector payload
without publishing, regardless of `authentication_state`. Only past
those checks does it look at `authentication_state`.

**`authentication_state=False`**: republishes the already-shape-validated
input PDU on `out` completely unchanged — no key/counter extraction.
**Same order as the TX sibling**: `sdls_authentication.add_authentication`
also checks shape first, `authentication_state` second.

**`auth_key`/`sdls_counter` lookup** (`_extract_secret`,
`_extract_counter`): same shapes/rules as
[sdls_authentication.md](sdls_authentication.md) documents for its own
`_extract_secret`/`_extract_counter` — a PMT symbol hex string or
u8vector for the key (rejected if not exactly 32 bytes), an integer PMT
counter checked first at `dict_msg["sdls_counter"]` via
`pmt.dict_has_key` (matching the TX sibling's own `_extract_counter`),
falling back to the nested
`dict_msg["sdls"]["security_header"]["sdls_counter"]` path only if the
top-level key is genuinely absent. `_extract_secret` detects an absent
`auth_key` the same way, via `pmt.dict_has_key` checked before the value
is ever fetched — matching `sdls_authentication._extract_secret`'s own
current implementation.

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
   original bytes), via `encapsulation_packet.build_header` — the same
   shared definition `ccsds_reader` parses with, so the two can't drift
   apart. If the
   payload already starts with those reconstructed bytes (`has_encap`),
   they're stripped from `out_payload_bytes`; otherwise they're
   prepended only for `mac_payload` (the CMAC input), matching the bytes
   `sdls_authentication` originally tagged before `ccsds_reader` split
   the encapsulation header out of the payload.

`sdls_authentication` itself never produces a `security_trailer`-shaped
output (see [sdls_authentication.md](sdls_authentication.md#known-issues--todos)),
so this path is untested through the real TX→RX pipeline:
`qa_sdls_authentication_verify.py::test_013`/`test_014` exercise it
directly (constructing the metadata shape by hand), but no test in this
repo produces that shape via the real pipeline.

**Tag verification** (`_verify_tag`): `CMAC.new(secret, ciphermod=AES).update(counter_bytes + mac_payload).verify(tag)`
— on `ValueError` (mismatch), returns `False` rather than propagating,
consistent with pycryptodome's own `verify()` contract.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `verify_message`'s
full body — the `pmt.is_pair(msg)`/`pmt.is_u8vector(payload_u8vector)`/
`pmt.is_dict(dict_msg)` shape checks, the `authentication_state=False`
passthrough, key/counter extraction, tag reconstruction and
verification, and the final publish — is wrapped in catch-log-drop
(`except Exception`). Every log call this block makes reporting a drop
condition is at `error`, matching that this block isn't on the raw-RF
`warn` list; `sdls_authentication`'s own equivalent log calls are
likewise all at `error`.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): a real
class-level summary; full `Args`/`Raises` for `__init__`; full
`Args`/`Publishes`/`Drops when` for `verify_message` (the message
handler); full `Args`/`Returns` for the PMT-touching helpers
(`_extract_secret`, `_extract_counter`, `_pmt_dict_get_int`,
`_build_encapsulation_header`). `_build_ctr_counter_block`,
`_split_payload_tag`, `_verify_tag` (non-PMT, plain bytes/int) have
none, permitted as-is by
[coding-standards.md](../coding-standards.md)'s exemption for that
category.

**Naming**: file, class, GRC block-id, both constructor parameters, and
every method name are already snake_case.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — the
authentication-tag half of SDLS's Security Trailer, same standard
[sdls_authentication.md](sdls_authentication.md) cites for the TX side.
**Simplified**: only AES-CMAC is supported, matching the TX sibling —
no algorithm agility.

## Known issues / TODOs

None currently. Both paths run through the real `ccsds_reader` in
`python/soarr/qa_sdls_rx_chain.py`: with encryption the encapsulation
header is part of the payload, without it the header is rebuilt from
metadata.

## Test coverage

- `python/soarr/qa_sdls_authentication_verify.py` — 17 test methods
  (`test_instance` + `test_001`-`test_016`): `authentication_state=False`
  passthrough (`test_001`), a valid tag verifying and `auth_key` removed
  from the output metadata (`test_002`), invalid key length and counter
  overflow each dropped with no publish (`test_003`, `test_004`), missing
  `auth_key`/missing `sdls_counter` each dropped with no publish
  (`test_005`, `test_006`), non-dict metadata and non-u8vector payload
  each dropped cleanly instead of raising (`test_007`, `test_008`), a
  tampered tag failing verification with no publish (`test_009`), a
  too-short payload (no room for a 16-byte tag) dropped with no publish
  (`test_010`), constructor `nonce` type/length validation (`test_011`,
  `test_012`), the two trailer-in-metadata reconstruction paths
  described in Behavior above — with (`test_013`) and without
  (`test_014`) the payload already containing the reconstructed
  encapsulation-header prefix, a mock-forced publish failure proven to
  be caught and dropped rather than raised through the real handler
  (`test_015`), and a top-level `sdls_counter` explicitly set to
  `PMT_NIL` (present, not absent) correctly rejected rather than
  silently falling back to a nested counter that would otherwise verify
  successfully (`test_016`).
- `python/soarr/qa_sdls_authentication_round_trip.py` — 10 test methods
  (`test_instance` + `test_001`-`test_009`), pairing this block with the
  real `sdls_authentication`: a full authenticate-then-verify round trip,
  tag-tamper and payload-tamper detection, `authentication_state`
  disabled on one side while enabled on the other (both directions),
  nonce mismatch, counter mismatch, an empty-payload round trip, and
  multiple sequential messages with different counters. Every case in
  this file uses the trailer-in-payload path (`_split_payload_tag`) —
  none constructs the trailer-in-metadata shape `ccsds_reader` would
  actually produce.
