# tc_primary_header

## Purpose

Builds and prepends the CCSDS 232.0-B-4 TC Transfer Frame Primary Header
(TFPH, 5 bytes) to a PDU's payload. The last framing step before the
frame is CRC-protected and channel-coded — see
[architecture.md](../architecture.md).

## Pipeline position

TX chain, between `sdls_header` and the stock `digital.crc_append`:

```
sdls_header.out → tc_primary_header.in
tc_primary_header.out → digital.crc_append.in (stock block)
```

`crc_append` appends the CCSDS FECF (2 bytes) *after* this block, based
on `frame_length` in the header this block builds — this block must
account for those 2 bytes in `frame_length` without adding them itself
(see `is_crc_used` below).

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. `vcid_counter` required (checked at the top level first, falling back to `telecommand.tc_header.vcid_counter` only if absent). `scid`/`vcid`/`bypass`/`control` optional, same dual-lookup rule. | `pmt.cons({vcid_counter: 9}, u8vector(payload))` |
| `out` | output | PDU: `(metadata_dict . header‖payload_u8vector)`. `bypass`, `control`, `frame_length`, `vcid_counter` removed from metadata; `scid`/`vcid` kept (used by later blocks) and written into the metadata if defaulted. | `pmt.cons({scid: 0x155, vcid: 0x12}, u8vector([...5-byte header, ...payload]))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `scid` | int | `0` | Used when metadata provides no `scid`. Validated in `__init__` against the field's 10-bit width (`0–0x3FF`), matching the GRC yaml's `asserts`. |
| `vcid` | int | `0` | Used when metadata provides no `vcid`. Validated in `__init__` against the field's 6-bit width (`0–0x3F`), matching the GRC yaml's `asserts`. |
| `is_crc_used` | bool | `True` | If `True`, `frame_length` accounts for a 2-byte FECF appended *downstream* by `digital.crc_append` — this block never appends it itself. |

## Behavior / edge cases / current error handling

**Field lookup** — `scid`, `vcid`, `bypass`, `control` each check
`dict_msg[key]` first (via a shared `_lookup_with_fallback` helper),
falling back to `telecommand.tc_header[key]` only if the top-level key is
absent, defaulting to the constructor's value if neither is present (and,
for `scid`/`vcid` specifically, the default is written back into the
outgoing metadata for downstream blocks to use). `bypass`/`control`'s
nested fallback checks `tc_header["bypass_flag"/"control_flag"]`
specifically, **not** the unrenamed `"bypass"/"control"`, since that's
the only nested key name this repo's real pipeline ever produces
(`inject_db.py` renames `bypass`→`bypass_flag`, `control`→`control_flag`
when nesting under `telecommand.tc_header`).

`vcid_counter` (→ `frame_sequence_number`) is the one **required** field:
same dual-lookup, but the message is dropped if it's absent everywhere —
no default exists for it.

`frame_length`: if present in metadata, used directly (dropped, logged,
if present but not convertible to an integer); if absent, computed as
`payload_length + 5 (header) - 1 (frame_length counts from byte 6) + 2
(if is_crc_used)`.

Every one of `scid`/`vcid`/`frame_length`/`frame_sequence_number` is then
explicitly validated against its field's bit width (dropped, logged, if
out of range) — **not** silently masked/truncated at pack time. This
matters for values that can come from caller-supplied metadata, not just
the (already constructor-validated) `scid`/`vcid` defaults: without this
check, a payload large enough to push the computed `frame_length` past
1023 (10 bits) would wrap silently via bitmasking into a different,
wrong, unlogged value instead of being rejected.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): every rejection
logs at `error` (this TX-side block isn't in the raw-RF `warn` list).
Field extraction (`_try_get_int`/`_try_get_bool`, built on one shared
`_try_get` helper) logs at `error` when a value is *present but not
convertible*, for every field this block reads
(`scid`/`vcid`/`bypass`/`control`/`vcid_counter`/`frame_length`).
The full body past the three input-shape checks — field extraction,
validation, header packing, and the publish call — is wrapped in
catch-log-drop.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Returns` for the extraction helpers, `Args`/`Raises` for
`__init__`, `Args`/`Publishes`/`Drops when` for `build_header`. The class
docstring has real content, not `gr_modtool`'s unfilled placeholder.

## CCSDS reference

CCSDS 232.0-B-4 (TC Space Data Link Protocol) — this block builds the
TFPH: `tfvn (2 bits) ‖ bypass (1) ‖ control (1) ‖ reserved (2) ‖ scid (10)
‖ vcid (6) ‖ frame_length (10) ‖ frame_sequence_number (8)` = 40 bits / 5
bytes.

## Known issues / TODOs

- **`vcid_counter` — this block's one required field — takes the
  untested nested-fallback path as its primary path in the real
  pipeline**, the same finding already documented for `spi`/`sdls_counter`
  in the SDLS blocks' PRDs. Verified directly in `db_client.py`:
  `vcid_counter` is assigned *by* the DB response (never known
  beforehand), so `inject_db.py`'s merge logic always nests it under
  `telecommand.tc_header.vcid_counter`, never top-level. Every test in
  `qa_tc_primary_header.py` sets `vcid_counter` at the top level directly,
  and `qa_layoutTest.py::test_002_end_to_end_message_routing` shims this
  block's real handler out. The code path this block's `vcid_counter`
  handling actually takes in production has never been run by any test
  in this repo.
- **No test decodes/checks the `frame_length` *field value* itself
  against a wired, real `digital.crc_append`.** `qa_layoutTest.py`'s real
  `digital.crc_append` is configured for CCSDS 232.0-B-4's actual 2-byte
  FECF (CRC-16/CCITT, poly `0x1021`, init `0xFFFF`, no reflection, no
  final XOR), matching this block's `additional_crc_bytes=2` assumption
  under `is_crc_used=True` — but `test_002` only checks the new
  total-length expectation, not the TFPH `frame_length` field
  specifically (and couldn't, easily, since `tc_primary_header`'s real
  handler is shimmed out in that test).
## Test coverage

- `python/soarr/qa_tc_primary_header.py` — 10 test methods (`test_instance`
  + `test_001`–`test_009`): construction with CRC disabled,
  rejection when `vcid_counter` is absent, constructor defaults used when
  `scid`/`vcid` are absent from metadata, PDU-provided fields taking
  priority over defaults (checked against every header field, including
  `frame_length`'s CRC-inclusive computation), `frame_length` excluding
  the CRC bytes when `is_crc_used=False`, `scid`/`vcid` out-of-range
  values rejected at construction, an unconvertible `frame_length`
  dropped cleanly instead of crashing on `None & mask`, a mock-forced
  internal build failure proven to be caught and dropped rather than
  raised through the real handler, and an oversized computed
  `frame_length` (past the 10-bit field's range) proven to be dropped
  rather than silently wrapped via bitmasking into a wrong value.
- `python/soarr/qa_layoutTest.py::test_004_tc_primary_header_real_handler`
  — same pattern as the other TX blocks' "real handler" tests: builds a
  **fresh, standalone** instance and calls `build_header` directly,
  proving real header-building logic but not the `msg_connect` wiring
  (covered separately by `test_002_end_to_end_message_routing`, with this
  block's handler shimmed out).
