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

The TX instance's `db_call`/`db_callback` wiring to `db_client` and its
`out → encapsulation_header.in` connection are confirmed via
`python/soarr/qa_layoutTest.py`'s `msg_connect` wiring; its `in` port is
fed by posting a PDU directly to the block in that test, not via
`msg_connect`, so the real upstream source (`inject_db`/`data_creator`)
is inferred from architecture.md, not independently wired here. The RX
instance's wiring is documented in [architecture.md](../architecture.md)'s
RX chain diagram only — not independently wired or tested in this repo.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Metadata must include `scid`/`spi` (int) and `bypass`/`control` (bool) — each checked at the top level first, falling back to the nested `telecommand.tc_header`/`sdls.security_header` path (see Behavior). | `pmt.cons({scid: 0x155, ...}, u8vector(payload))` |
| `db_call` | output | The same PDU received on `in`, unmodified, forwarded to `db_client` as a query. | same as `in` |
| `db_callback` | input | PDU: `(metadata_dict . payload_or_PMT_NIL)` — `db_client`'s query response. The payload need not be a u8vector as long as a prior `in` message left pending state to supply one instead (see Behavior) — e.g. `db_client(forward_body=False)`'s `PMT_NIL` payload. Metadata must include everything `in` requires, plus `auth_key`/`crypt_key` (symbol hex string or `PMT_NIL`) and `vcid`/`vcid_counter`/`sdls_counter` (int). | `pmt.cons({auth_key: "...", ...}, u8vector(payload))` |
| `out` | output | The `db_callback` PDU's metadata, merged with the original `in` PDU's metadata (`in`'s keys win — see Behavior), paired with the *original* `in` PDU's payload (not `db_callback`'s). | `pmt.cons({merged}, u8vector(original_payload))` |

## Parameters

None.

## Behavior / edge cases / current error handling

**Two-phase flow, correlated by single-slot instance state**: `send_db_call`
(the `in` handler) validates the incoming PDU (requiring a u8vector
payload), stores it as `self._pending_meta`/`self._pending_payload`, and
forwards it unchanged on `db_call`. `send_msg_out` (the `db_callback`
handler) validates the callback PDU's shape (metadata dict required, but
its own payload need not be a u8vector), merges it with `_pending_meta`
(see below), re-attaches the *original* pending payload — discarding the
callback's own payload entirely, whatever it was — clears both pending
fields, validates the merged result against a larger required-key set,
and publishes on `out`. If `_pending_meta` is `None` (no `in` message
preceded this callback), there's no pending payload to fall back on, so
the callback's own payload must be a u8vector or the message is dropped;
otherwise `send_msg_out` validates and forwards the callback PDU's own
metadata and payload directly, with no merge.

**Metadata merge** (`_merge_metadata`/`_should_merge_key`/
`_merge_key_into_nested`/`_resolve_key`): for every key in the
`db_callback` response (iterated directly via `pmt.dict_items`, so
values keep their original PMT type — nothing round-trips through a
Python conversion), merges it into the pending metadata **only if that
key is genuinely absent everywhere** (checked via `_resolve_key`, which
itself checks both the top level and the relevant nested path) — an
existing top-level or nested value always wins over the callback's.
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
Everything else found in this pass was fixed directly, including a real
correctness bug beyond what round 1 review first caught:
`secret_or_nil`-typed keys (`auth_key`/`crypt_key`) resolving to a
genuine `PMT_NIL` value were being rejected as "missing" — the same
class of bug as the deferred item above but with an unambiguous fix, not
a design question. Compounding it, `_merge_metadata`'s now-removed
`pmt.to_python`/`_python_to_pmt` round-trip path silently corrupted a
merged `PMT_NIL` value into the PMT symbol `"None"` before it ever
reached that check — a real, silent metadata-corruption bug affecting
every `db_callback` response that legitimately merges a nil `auth_key`/
`crypt_key` into pending metadata. See Behavior above for the current,
correct state.

## Test coverage

- `python/soarr/qa_inject_db.py` — 19 test methods (`test_instance` +
  `test_001`–`test_018`): a valid `in` PDU (with nested `telecommand`/
  `sdls` metadata) emitting a `db_call`, a missing required key and a
  non-integer `scid` each emitting nothing, a `db_callback` with valid
  symbol-hex `auth_key`/`crypt_key` emitting `out`, a genuinely nil
  `auth_key`/`crypt_key` accepted and forwarded downstream as real
  `PMT_NIL` (`test_005`), a wrong PMT type for `crypt_key` (a
  u8vector instead of `PMT_NIL`/symbol) rejected, a non-integer
  required field rejected, a non-u8vector payload rejected, integer
  `0`/`1` accepted in place of real PMT booleans for `bypass`/`control`,
  a missing `spi` rejected, a `db_callback` filling in keys absent from
  the original `in` metadata (`test_012`), confirming the merge never
  overwrites a key the original `in` metadata already had — including
  confirming a genuinely nil `auth_key`/`crypt_key` survives the merge as
  real `PMT_NIL`, not some other value (`test_013`), `auth_key`/
  `crypt_key` genuinely absent (not merely nil) also accepted
  (`test_014`), and a mock-forced publish failure proven to be caught
  and dropped rather than raised through the real handler, for both
  `send_db_call` (`test_015`) and `send_msg_out` (`test_016`), a
  `db_callback` with a `PMT_NIL` payload (simulating
  `db_client(forward_body=False)`) still publishing successfully using
  the pending payload from a preceding `send_db_call` (`test_017`), and
  the same `PMT_NIL` payload rejected when no `send_db_call` preceded it,
  since there's no pending payload to fall back on (`test_018`). No test
  exercises more than one in-flight request at a time (see Known issues
  above).
