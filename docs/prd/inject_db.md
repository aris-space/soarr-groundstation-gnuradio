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

Used as two separate instances, one per chain — `role="tx"` and
`role="rx"` respectively (see Parameters):

```
TX: data_creator.out (or another PDU source) → inject_db.in
    inject_db.db_call ⇄ db_client.db_call/db_callback (side-channel)
    inject_db.out → encapsulation_header.in

RX: ccsds_reader.debug → inject_db.in
    inject_db.db_call ⇄ db_client.db_call/db_callback (side-channel)
    inject_db.out → sdls_authentication_verify.in
```

The TX instance's `db_call`/`db_callback` wiring to `db_client` and its
`out → encapsulation_header.in` connection are confirmed via
`python/soarr/qa_tx_chain.py`'s `msg_connect` wiring; its `in` port is
fed by posting a PDU directly to the block in that test, not via
`msg_connect`; the example flowgraphs wire `data_creator.out →
inject_db.in`. The RX
instance's wiring is documented in [architecture.md](../architecture.md)'s
RX chain diagram only — not independently wired or tested in this repo.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Metadata must include `scid`/`spi` (int) and `bypass`/`control` (bool) — each checked at the top level first, falling back to the nested `telecommand.tc_header`/`sdls.security_header` path (see Behavior). | `pmt.cons({scid: 0x155, ...}, u8vector(payload))` |
| `db_call` | output | The PDU received on `in`, with `db_request_id` (uint64, unique per request) added to its metadata, forwarded to `db_client` as a query. | `pmt.cons({scid: 0x155, ..., db_request_id: 7}, u8vector(payload))` |
| `db_callback` | input | PDU: `(metadata_dict . payload_or_PMT_NIL)` — `db_client`'s query response, carrying the query's `db_request_id` back. The payload need not be a u8vector as long as a pending request supplies one instead (see Behavior) — e.g. `db_client(forward_body=False)`'s `PMT_NIL` payload. Metadata must include everything `in` requires, plus `auth_key`/`crypt_key` (symbol hex string or `PMT_NIL`) and `vcid`/`vcid_counter`/`sdls_counter` (int). | `pmt.cons({auth_key: "...", ...}, u8vector(payload))` |
| `out` | output | The `db_callback` PDU's metadata, merged with the metadata of the `in` PDU it answers (`in`'s keys win, except the counters in the TX role — see Behavior), paired with that *original* `in` PDU's payload (not `db_callback`'s). `db_request_id` is removed. | `pmt.cons({merged}, u8vector(original_payload))` |

## Parameters

| Name | Type | Default | Description |
|---|---|---|---|
| `role` | str: `"tx"` \| `"rx"` | `"tx"` | Who owns the per-frame counters (`sdls_counter`, `vcid_counter`). `"tx"`: the database assigns them and its values replace any the `in` PDU carries. `"rx"`: the `in` PDU's counters (read from the received frame) are kept and the database's are ignored. Any other value raises `ValueError`. The role is logged at `info` on startup. |

## Behavior / edge cases / current error handling

**Two-phase flow, correlated by request id**: `send_db_call` (the `in`
handler) validates the incoming PDU (requiring a u8vector payload),
assigns it the next request id, publishes it on `db_call` with that id
added as `db_request_id`, and stores the unmodified metadata and payload
in `self._pending` (an ordered id → request map). Any number of requests
may be in flight at once. `send_msg_out` (the `db_callback` handler)
validates the callback PDU's shape (metadata dict required, but its own
payload need not be a u8vector), removes `db_request_id` from it, and
takes the matching request out of `_pending`. A response without
`db_request_id` (a database block that doesn't echo it) takes the
*oldest* pending request instead. It then merges the response into that
request's metadata (see below), re-attaches the request's *original*
payload — discarding the callback's own payload entirely, whatever it
was — validates the merged result against a larger required-key set,
and publishes on `out`.

A `db_request_id` that isn't an integer, or that matches no pending
request (unknown, already answered, or discarded as too old), is
dropped (logged at `error`). If no request is pending at all, there's no
pending payload to fall back on, so the callback's own payload must be a
u8vector or the message is dropped; otherwise `send_msg_out` validates
and forwards the callback PDU's own metadata and payload directly, with
no merge.

**Unanswered requests**: `db_client` answers nothing for some queries
(unknown SCID/SPI, invalid or exhausted counters). Because responses are
matched by id, a missing answer never shifts later pairings; the
unanswered request just stays pending. `_pending` holds at most
`MAX_PENDING_REQUESTS` (256): a new request beyond that discards the
oldest one (logged at `warn`), so unanswered requests can't accumulate
forever.

**Metadata merge** (`_merge_metadata`/`_should_merge_key`/
`_merge_key_into_nested`/`_resolve_key`): for every key in the
`db_callback` response (iterated directly via `pmt.dict_items`, so
values keep their original PMT type — nothing round-trips through a
Python conversion), merges it into the pending metadata **only if that
key is genuinely absent everywhere** (checked via `_resolve_key`, which
itself checks both the top level and the relevant nested path) — an
existing top-level or nested value wins over the callback's.

The exception is the counters (`DB_OWNED_KEYS`: `sdls_counter`,
`vcid_counter`) in the TX role: the database's value is always written
to the nested path, and a top-level copy in the `in` metadata is
removed, so a frame source's fixed placeholders (e.g. `data_creator`'s,
default 0) never reach the wire and no counter repeats. In the RX role
the counters follow the normal rule: the values `ccsds_reader` read from
the received frame are kept, so the RX database's own counters (which
don't know about lost frames) can't put SDLS authentication out of step.
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
`crypt_key`, which may legitimately be absent or explicitly nil). For
these two nullable types, a key resolving to `PMT_NIL` is *not* treated
as "missing" — the nil/absent decision is deferred entirely to
`_check_key_type`, which accepts it. For `int`/`bool`, a key resolving to
`PMT_NIL` is still correctly treated as missing (dropped, logged). A
missing or wrong-type required key drops the message (logged, no
publish).

**`send_db_call`'s required keys**: `scid`, `spi` (int), `bypass`,
`control` (bool) — its own docstring notes this is a fixed, simplified
query, not necessarily every field a real database lookup would need.

**`send_msg_out`'s required keys**: everything `send_db_call` requires,
plus `auth_key`, `crypt_key` (`secret_or_nil`), `vcid`, `vcid_counter`,
`sdls_counter` (int).

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): every
rejection logs at `error` (this block is not on the raw-RF `warn` list).
`send_db_call` and `send_msg_out` each wrap their full body in
catch-log-drop (`except Exception`), including the final
`message_port_pub` call.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises`/`Returns` for `__init__` and every PMT-touching private
helper, `Args`/`Publishes`/`Drops when` for both handlers.

## CCSDS reference

None — this block is pure metadata plumbing, not a CCSDS-defined layer.

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_inject_db.py` — 31 test methods (`test_instance` +
  `test_001`–`test_030`): a valid `in` PDU (with nested `telecommand`/
  `sdls` metadata) emitting a `db_call`, a missing required key and a
  non-integer `scid` each emitting nothing, a `db_callback` with valid
  symbol-hex `auth_key`/`crypt_key` emitting `out`, a genuinely nil
  `auth_key`/`crypt_key` accepted and forwarded downstream as real
  `PMT_NIL` (`test_005`), a wrong PMT type for `crypt_key` (a
  u8vector instead of `PMT_NIL`/symbol) rejected, a non-integer
  required field rejected, a non-u8vector payload rejected, integer
  `0`/`1` accepted in place of real PMT booleans for `bypass`/`control`,
  a missing `spi` rejected, a `db_callback` filling in keys absent from
  the original `in` metadata (`test_012`), confirming the merge keeps
  every other key the original `in` metadata already had — including a
  genuinely nil `auth_key`/`crypt_key` surviving as real `PMT_NIL` — while
  the TX role takes the database's counters (`test_013`), `auth_key`/
  `crypt_key` genuinely absent (not merely nil) also accepted
  (`test_014`), and a mock-forced publish failure proven to be caught
  and dropped rather than raised through the real handler, for both
  `send_db_call` (`test_015`) and `send_msg_out` (`test_016`), a
  `db_callback` with a `PMT_NIL` payload (simulating
  `db_client(forward_body=False)`) still publishing successfully using
  the pending payload from a preceding `send_db_call` (`test_017`), and
  the same `PMT_NIL` payload rejected when no `send_db_call` preceded it,
  since there's no pending payload to fall back on (`test_018`). Request
  correlation: every `db_call` carrying a distinct `db_request_id`
  (`test_019`), two in-flight requests paired correctly with responses in
  order (`test_020`) and in reverse order (`test_021`), an unanswered
  request not shifting a later pairing (`test_022`), an unknown id
  dropped without consuming the real pending request (`test_023`),
  `db_request_id` never forwarded on `out` (`test_024`), the pending map
  bounded at `MAX_PENDING_REQUESTS` with the oldest evicted (`test_025`),
  and two in-flight requests against a real `db_client`, answered in
  reverse order, each receiving its own entry's counter (`test_026`).
  Counter ownership: top-level counters in the `in` metadata removed in
  favour of the database's nested ones (`test_027`), the `in` counters
  kept when the database sends none (`test_028`), `role="rx"` keeping the
  frame's counters over the database's (`test_029`), and an invalid role
  raising `ValueError` (`test_030`).
