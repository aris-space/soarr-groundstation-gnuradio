# db_client

## Purpose

Looks up key/security material and per-frame counters by SCID/SPI and
returns them via a `db_call`/`db_callback` request/response pair, in one
of three modes: an in-memory dummy entry (`type=0`), a local YAML file
(`type=1`), or a remote database (`type=2`, unimplemented stub that
falls back to the dummy entry). Ground-tooling sitting on `inject_db`'s
side-channel — never touches the wire directly. See
[architecture.md](../architecture.md).

## Pipeline position

Side-channel off `inject_db` (both the TX and RX instances):

```
inject_db.db_call → db_client.db_call
db_client.db_callback → inject_db.db_callback
```

Confirmed via `python/soarr/qa_layoutTest.py`'s `msg_connect` wiring
(TX instance); the RX instance's wiring is documented in
[architecture.md](../architecture.md) only.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `db_call` | input | PDU: `(metadata_dict . payload_u8vector_or_PMT_NIL)`. Metadata must include `scid`/`spi` (int), each checked at the top level first, falling back to `telecommand.tc_header.scid`/`sdls.security_header.spi` (see Behavior). | `pmt.cons({scid: 0x155, spi: 1}, u8vector(payload))` |
| `db_callback` | output | PDU: `(metadata_dict . payload_or_PMT_NIL)`. Metadata carries the looked-up `scid`/`spi`/`bypass`/`control` (echoed from the query), plus `vcid`/`crypt_key`/`auth_key`/`sdls_counter`/`vcid_counter` (looked up). Payload is the query's own payload if `forward_body=True` and it's a u8vector; otherwise `PMT_NIL`. | `pmt.cons({vcid: 0x12, crypt_key: "...", ...}, u8vector(payload))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `type` | int | `0` | `0`=dummy (in-memory, configurable via the parameters below), `1`=local YAML file, `2`=remote DB (unimplemented stub — falls back to dummy, logged at `error`). An unrecognized value also falls back to dummy, logged at `error`. |
| `ip`, `port` | str, int | `"127.0.0.1"`, `80` | Reserved for `type=2`'s remote DB mode; unused since that mode isn't implemented. |
| `yaml_path` | str | `""` | Path to the YAML file for `type=1`. Missing file, missing PyYAML, a parse failure, a non-mapping root, or zero valid entries all fall back to dummy (logged at `error`). |
| `forward_body` | bool | `True` | If `True` and the query's payload is a u8vector, echoes it back on `db_callback`; otherwise the response payload is `PMT_NIL` (see Known issues for what this does to `inject_db`). |
| `auto_reset_counters` | bool | `False` | `type=0` only: if `True`, a counter at its max value resets to `0` after being served instead of refusing to increment further. |
| `scid`, `spi`, `vcid`, `crypt_key`, `auth_key`, `sdls_counter`, `vcid_counter`, `key_state_enc`, `key_state_auth` | — | see code | The dummy (`type=0`) entry's fields, each independently configurable. |

## Behavior / edge cases / current error handling

**Lookup key resolution** (`_resolve_scid_spi`/`_resolve_bypass_control`):
`scid`/`spi`/`bypass`/`control` are each resolved *independently* — top
level first, falling back to `telecommand.tc_header.<key>`/
`sdls.security_header.spi` only for that specific key if it's absent at
the top level, matching `inject_db._resolve_key`'s established per-key
independence. `scid`/`spi` are always present in the query's own
metadata by the time it reaches this block (`inject_db.send_db_call`
requires both before it will even query `db_client`), so this block
doesn't originate them — it looks up an entry using them, then echoes
them straight back on `db_callback`. Since `inject_db`'s merge only
fires for keys genuinely absent from its pending metadata, and pending
already has `scid`/`spi`, this block's echoed values are normally
*discarded* by that merge, not nested — a different relationship to
`inject_db`'s merge logic than `vcid`/`vcid_counter`/`sdls_counter`
(which `db_client` genuinely does originate fresh, and which do end up
nested, per `inject_db.py`'s PRD).

**Counter model**: each dummy/YAML entry is a persistent, mutable dict
stored in `self._db` — `sdls_counter`/`vcid_counter` are served, then
incremented in place (`entry["sdls_counter"] = ...`), so counters
genuinely persist and monotonically increase across calls for the
lifetime of the block instance (not reset per-call). `sdls_counter` is
modeled as **32-bit** (`SDLS_COUNTER_MAX = 0xFFFFFFFF`); `vcid_counter`
as **8-bit** (`VCID_COUNTER_MAX = 0xFF`). This 32-bit `sdls_counter`
ceiling is the actual origin of the 32-bit-vs-16-bit mismatch documented
in `sdls_encryption`'s and `sdls_authentication`'s PRDs (those blocks
each cap `sdls_counter` at 16 bits internally).

**Counter overflow**: `_checked_increment` raises `OverflowError` if a
counter is already at its max; caught separately for `sdls_counter` and
`vcid_counter`, each independently either reset to `0`
(`type=0 and auto_reset_counters`) or left at max with an error logged.
Overflow is checked *after* publishing the current (pre-increment)
value, so the query that pushes a counter to its max still gets a valid
response — only the *next* query at that SCID/SPI is affected.

**`forward_body=False` (or a non-u8vector query payload)**: the
`db_callback` response's payload becomes `PMT_NIL`. Since
`inject_db.send_msg_out` requires a u8vector payload unconditionally
(see [inject_db.md](inject_db.md)), a `PMT_NIL` response payload gets the
*entire* `db_callback` message rejected by `inject_db` — reproduced
directly: `db_client(forward_body=False)`'s response, fed into a real
`inject_db.send_msg_out`, is dropped with `"Received message from
database with non-u8vector payload"`, publishing nothing.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `make_db_call`
checks `msg` is a pair and its metadata is a dict before use; the full
body past those two checks is wrapped in catch-log-drop (`except
Exception`), including the counter-validation/increment steps' own more
specific inner `try`/`except` blocks (preserved as-is, since they encode
real business logic — whether to auto-reset a maxed-out counter — not
just generic error handling) and the final `db_callback` publish. All
logged at `error` (this block is not on the raw-RF `warn` list),
including `_init_database`'s two construction-time fallback logs
(`type=2`/unrecognized `type`), now consistent with `_load_yaml_db`'s
own fallback logs, which already used `error` for the same category of
event.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`make_db_call`, `Args`/`Returns`/`Raises` for every PMT-touching private
helper.

**Naming**: file, class, GRC block-id, every constructor parameter, and
the handler method name are already snake_case.

## CCSDS reference

None — this block is pure key/counter lookup, not a CCSDS-defined layer.

## Known issues / TODOs

- **`forward_body=False` makes `db_client`'s response unusable by
  `inject_db`.** Confirmed directly (see Behavior above) — the entire
  `db_callback` message is silently dropped by `inject_db`, not just its
  payload. Since the default is `True`, this only bites a flowgraph that
  explicitly sets `forward_body=False`, but nothing in either block warns
  that the combination is effectively broken. This is a cross-block
  design interaction, not something fixable in `db_client.py` alone.

## Test coverage

- `python/soarr/qa_db_client.py` — 20 test methods (`test_instance` +
  `test_001`–`test_019`): a dummy-mode lookup returning every expected
  field and incrementing both counters across two calls, a missing
  `scid`/`spi` and an unknown `scid`/`spi` each emitting nothing, YAML
  mode loading a flat-layout entry and using it (`test_004`), a counter
  already at max being served correctly with no wraparound
  (`auto_reset_counters` off), configurable dummy SCID/SPI/VCID,
  configurable dummy keys, configurable dummy initial counters,
  configurable dummy key states, every dummy parameter combined in one
  test, `forward_body=True` preserving a u8vector payload,
  `forward_body=False` producing a `PMT_NIL` payload (proving the
  behavior, not `inject_db`'s reaction to it — see Known issues),
  `auto_reset_counters` on/off at the max counter value, a non-pair input
  dropped cleanly instead of crashing the handler (`test_015`), `scid`
  and `spi` resolved independently — a valid top-level `scid` surviving
  even when top-level `spi` is absent and a differing nested `scid`
  exists (`test_016`), the same independence for `bypass`/`control`
  (`test_017`), the nested-by-SCID YAML layout — distinct from
  `test_004`'s flat layout, with no explicit `SCID`/`SPI` field on the
  inner entries (`test_018`), and a mock-forced publish failure proven to
  be caught and dropped rather than raised through the real handler
  (`test_019`).
