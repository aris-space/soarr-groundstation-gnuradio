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
| `type` | int | `0` | `0`=dummy (in-memory, configurable via the parameters below), `1`=local YAML file, `2`=remote DB (unimplemented stub — falls back to dummy, logged at `warn`). An unrecognized value also falls back to dummy, logged at `warn`. |
| `ip`, `port` | str, int | `"127.0.0.1"`, `80` | Reserved for `type=2`'s remote DB mode; unused since that mode isn't implemented. |
| `yaml_path` | str | `""` | Path to the YAML file for `type=1`. Missing file, missing PyYAML, a parse failure, a non-mapping root, or zero valid entries all fall back to dummy (logged at `error`). |
| `forward_body` | bool | `True` | If `True` and the query's payload is a u8vector, echoes it back on `db_callback`; otherwise the response payload is `PMT_NIL` (see Known issues for what this does to `inject_db`). |
| `auto_reset_counters` | bool | `False` | `type=0` only: if `True`, a counter at its max value resets to `0` after being served instead of refusing to increment further. |
| `scid`, `spi`, `vcid`, `crypt_key`, `auth_key`, `sdls_counter`, `vcid_counter`, `key_state_enc`, `key_state_auth` | — | see code | The dummy (`type=0`) entry's fields, each independently configurable. |

## Behavior / edge cases / current error handling

**Lookup key resolution** (`_resolve_scid_spi`): `scid`/`spi` checked at
the top level first, falling back to `telecommand.tc_header.scid`/
`sdls.security_header.spi` only if the top-level key is absent. This is
the block that originally assigns these values (via `db_client`'s own
dummy/YAML data) before `inject_db`'s merge logic ever nests them — the
fact that `inject_db.py`'s merge logic always nests DB-assigned keys is
confirmed directly against this block's data model, cited by several
other blocks' PRDs.

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

**Error handling**: `make_db_call` calls `pmt.car(msg)` unconditionally
before any shape check — reproduced directly: `make_db_call(pmt.intern
("not-a-pair"))` raises `ValueError: pmt_car: wrong_type not-a-pair`.
Past that, `_validate_counter`'s two calls are wrapped in one
`try/except (TypeError, ValueError, OverflowError)`, and each
`_checked_increment` call is separately wrapped in its own
`try/except OverflowError` — but building `response_meta` and the
`self._publish(...)` call (`self.message_port_pub`) sit between those,
entirely unguarded. Every message-handler-path log call already uses
`error` (this block is not on the raw-RF `warn` list); the two `warn`
calls in the file are both in `_init_database`, a construction-time
configuration-fallback path, not part of `make_db_call`'s message
handling — a different category from the handler rejection logs
ADR-0003's warn/error rule targets.

**Docstrings**: the class docstring and `make_db_call`'s docstring
already have real content (mode descriptions, input/output metadata
keys), unlike most blocks' `gr_modtool` placeholders — but neither
follows [ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)'s
template, and every other method (`__init__` and the dozen private
helpers) has none.

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
  that the combination is effectively broken.
- **`make_db_call` crashes on a non-pair input.** `pmt.car(msg)` is
  called unconditionally before any shape check; reproduced directly
  (see Error handling above).
- **Building the response and publishing it are unguarded.** Only the
  counter-validation and counter-increment steps are wrapped in
  `try`/`except`; `response_meta` construction and the `db_callback`
  publish call are not, unlike every already-fixed sibling block's
  full-body catch-log-drop.
- **No docstrings on `__init__` or any private helper** — full
  ADR-0004 gap for everything except the class and `make_db_call`.

## Test coverage

- `python/soarr/qa_db_client.py` — 15 test methods (`test_instance` +
  `test_001`–`test_014`): a dummy-mode lookup returning every expected
  field and incrementing both counters across two calls, a missing
  `scid`/`spi` and an unknown `scid`/`spi` each emitting nothing, YAML
  mode loading a nested-by-SCID entry and using it, a counter already at
  max being served correctly with no wraparound (`auto_reset_counters`
  off), configurable dummy SCID/SPI/VCID, configurable dummy keys,
  configurable dummy initial counters, configurable dummy key states,
  every dummy parameter combined in one test, `forward_body=True`
  preserving a u8vector payload, `forward_body=False` producing a
  `PMT_NIL` payload (proving the behavior, not `inject_db`'s reaction to
  it — see Known issues), and `auto_reset_counters` on/off at the max
  counter value. No test exercises a non-pair input, YAML mode's
  malformed-file fallback paths, or the `type=2`/unknown-`type` fallback
  paths.
