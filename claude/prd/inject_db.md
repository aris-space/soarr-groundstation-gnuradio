# inject_db

## Purpose

Queries a key/security-material database via a side-channel request/
response pair (`db_call`/`db_callback` to `db_client`) and merges the
response into a PDU's metadata before forwarding it downstream. Sits
inline in the signal chain (unlike `system_tester`/`data_creator`,
ground-tooling that never touches the wire), but its actual job is
metadata enrichment, not signal processing. See
[architecture.md](../architecture.md).

## Pipeline position

Used as two separate instances, one per chain:

```
TX: (caller: inject_db/data_creator's PDU source) → inject_db.in
    inject_db.db_call ⇄ db_client.db_call/db_callback (side-channel)
    inject_db.out → encapsulation_header.in

RX: ccsds_reader.debug → inject_db.in
    inject_db.db_call ⇄ db_client.db_call/db_callback (side-channel)
    inject_db.out → sdls_authentication_verify.in
```

Confirmed via `python/soarr/qa_layoutTest.py`'s `msg_connect` wiring
(TX instance) and [architecture.md](../architecture.md)'s RX chain
diagram (RX instance, not independently wired/tested in this repo).

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Metadata must include `scid`/`spi` (int) and `bypass`/`control` (bool) — each checked at the top level first, falling back to the nested `telecommand.tc_header`/`sdls.security_header` path (see Behavior). | `pmt.cons({scid: 0x155, ...}, u8vector(payload))` |
| `db_call` | output | The same PDU received on `in`, unmodified, forwarded to `db_client` as a query. | same as `in` |
| `db_callback` | input | PDU: `(metadata_dict . payload_u8vector)` — `db_client`'s query response. Metadata must include everything `in` requires, plus `auth_key`/`crypt_key` (symbol hex string or `PMT_NIL`) and `vcid`/`vcid_counter`/`sdls_counter` (int). | `pmt.cons({auth_key: "...", ...}, u8vector(payload))` |
| `out` | output | The `db_callback` PDU's metadata, merged with the original `in` PDU's metadata (`in`'s keys win — see Behavior), paired with the *original* `in` PDU's payload (not `db_callback`'s). | `pmt.cons({merged}, u8vector(original_payload))` |

## Parameters

None.

## Behavior / edge cases / current error handling

**Two-phase flow, correlated by single-slot instance state**: `send_db_call`
(the `in` handler) validates the incoming PDU, stores it as
`self._pending_meta`/`self._pending_payload`, and forwards it unchanged
on `db_call`. `send_msg_out` (the `db_callback` handler) validates the
callback PDU, merges it with `_pending_meta` (see below), re-attaches the
*original* pending payload, clears both pending fields, validates the
merged result against a larger required-key set, and publishes on `out`.
If `_pending_meta` is `None` (no `in` message preceded this callback),
`send_msg_out` validates and forwards the callback PDU's own metadata
directly, with no merge.

**Metadata merge** (`_merge_metadata`/`_should_merge_key`/
`_merge_key_into_nested`/`_resolve_key`): for every key in the
`db_callback` response, merges it into the pending metadata **only if
that key is genuinely absent everywhere** (checked via `_resolve_key`,
which itself checks both the top level and the relevant nested path) —
an existing top-level or nested value always wins over the callback's.
`scid`/`vcid`/`bypass`→`bypass_flag`/`control`→`control_flag`/
`vcid_counter` are merged under `telecommand.tc_header`; `spi`/
`sdls_counter` under `sdls.security_header`. This exact logic is the
source of truth other blocks' PRDs (`sdls_encryption`, `sdls_authentication`,
`sdls_header`, `tc_primary_header`) cite when describing which of their
own metadata keys arrive nested vs. top-level in the real pipeline.

**Key validation** (`_check_keys`/`_check_key_type`): four supported
types — `int` (or `uint64`), `bool` (a real PMT boolean, or an integer
`0`/`1`), `int_or_nil` (unused by either handler currently), and
`secret_or_nil` (`PMT_NIL` or a PMT symbol — used for `auth_key`/
`crypt_key`, which may legitimately be absent). A missing or
wrong-type required key drops the message (logged, no publish).

**`send_db_call`'s required keys**: `scid`, `spi` (int), `bypass`,
`control` (bool) — noted in the code's own comment as a fixed,
simplified query ("For testing, we will just send a fixed query to the
database client"), not necessarily every field a real database lookup
would need.

**`send_msg_out`'s required keys**: everything `send_db_call` requires,
plus `auth_key`, `crypt_key` (`secret_or_nil`), `vcid`, `vcid_counter`,
`sdls_counter` (int).

**Error handling**: every one of `_extract_pdu`'s three shape checks and
`_check_keys`'s two failure cases logs at `warn` (11 call sites in this
file, all `warn`, none `error`) — this block is not on the raw-RF `warn`
list in [coding-standards.md](../coding-standards.md), so
[ADR-0003](../adr/0003-message-handler-error-policy.md)'s classification
would put every one of these at `error` instead (once a message reaches
this block, it's well past any raw-RF noise boundary on both the TX and
RX chains it's used in). Neither `send_db_call` nor `send_msg_out` wraps
its body in `try`/`except` — `_merge_metadata` has its own two internal
`try`/`except` blocks (guarding a `pmt.to_python`/`pmt.dict_items` call
each), but nothing wraps the handlers' full bodies, including the final
`message_port_pub` calls.

**Docstrings**: none anywhere — the class docstring is still
`gr_modtool`'s unfilled placeholder (`"""docstring for block
inject_db"""`).

## CCSDS reference

None — this block is pure metadata plumbing, not a CCSDS-defined layer.

## Known issues / TODOs

- **Single-slot pending state causes cross-request metadata corruption
  under concurrent/pipelined `in` messages.** Reproduced directly:
  calling `send_db_call` twice (request A, then request B) before either
  one's `db_callback` response arrives silently overwrites
  `_pending_meta`/`_pending_payload` with request B's data. When a
  `db_callback` response conceptually meant for request A then arrives,
  `send_msg_out` merges it with request B's pending metadata instead —
  request A's payload and request B's metadata end up combined and
  published as one message on `out`, with no error, no log, and no way
  for a caller to detect the mismatch. `db_client` is a real,
  asynchronous side-channel query — nothing in this repo's message-passing
  model guarantees a `db_callback` response arrives before the next `in`
  message. No test in this repo exercises more than one in-flight
  request at a time. Fixing this means correlating each `db_call`/
  `db_callback` pair (e.g. a request-id tag, or a queue of pending
  requests instead of one slot) — a real behavioral/contract change, not
  a small fix.
- **Every rejection logs at `warn`, not `error`** — see Error handling
  above; a repo-wide-established, mechanical fix once applied elsewhere.
- **Neither handler wraps its full body in catch-log-drop** — see Error
  handling above.
- **Dead self-assignments in `_resolve_key`/`_merge_key_into_nested`**:
  `if key == "vcid_counter": tc_key = "vcid_counter"` and
  `if key == "spi": sdls_key = "spi"` / `if key == "sdls_counter":
  sdls_key = "sdls_counter"` each reassign a variable to the value it
  already holds — no-op conditionals, apparently copy-pasted from the
  adjacent `bypass`→`bypass_flag`/`control`→`control_flag` renames where
  a real rename happens.
- **`send_db_call` and `send_msg_out` each duplicate `_extract_pdu`'s own
  `pmt.is_pair` check** — both handlers check `pmt.is_pair(msg)`
  themselves before calling `_extract_pdu`, which immediately performs
  the identical check again.
- **No docstrings anywhere** — full ADR-0004 gap.

## Test coverage

- `python/soarr/qa_inject_db.py` — 14 test methods (`test_instance` +
  `test_001`–`test_013`): a valid `in` PDU (with nested `telecommand`/
  `sdls` metadata) emitting a `db_call`, a missing required key and a
  non-integer `scid` each emitting nothing, a `db_callback` with valid
  symbol-hex `auth_key`/`crypt_key` emitting `out`, `PMT_NIL`
  auth/crypt keys accepted, a wrong PMT type for `crypt_key` (a
  u8vector instead of `PMT_NIL`/symbol) rejected, a non-integer
  required field rejected, a non-u8vector payload rejected, integer
  `0`/`1` accepted in place of real PMT booleans for `bypass`/`control`,
  a missing `spi` rejected, and two merge-behavior tests: a `db_callback`
  filling in keys absent from the original `in` metadata
  (`test_012`), and confirming the merge never overwrites a key the
  original `in` metadata already had, even when `db_callback` supplies a
  conflicting value for the same key (`test_013`). No test exercises
  more than one in-flight request at a time (see Known issues above).
