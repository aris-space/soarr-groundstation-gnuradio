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

Confirmed via `python/soarr/qa_tx_chain.py`'s `msg_connect` wiring
(TX instance); the RX instance's wiring is documented in
[architecture.md](../architecture.md) only.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `db_call` | input | PDU: `(metadata_dict . payload_u8vector_or_PMT_NIL)`. Metadata must include `scid`/`spi` (int), each checked at the top level first, falling back to `telecommand.tc_header.scid`/`sdls.security_header.spi` (see Behavior). | `pmt.cons({scid: 0x155, spi: 1}, u8vector(payload))` |
| `db_callback` | output | PDU: `(metadata_dict . payload_or_PMT_NIL)`. Metadata carries the looked-up `scid`/`spi`/`bypass`/`control` (echoed from the query), plus `vcid`/`crypt_key`/`auth_key`/`sdls_counter`/`vcid_counter` (looked up), plus the query's `db_request_id` if it has one — `inject_db` uses it to pair the response with its request. Payload is the query's own payload if `forward_body=True` and it's a u8vector; otherwise `PMT_NIL`. | `pmt.cons({vcid: 0x12, crypt_key: "...", ...}, u8vector(payload))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `type` | int | `0` | `0`=dummy (in-memory, configurable via the parameters below), `1`=local YAML file, `2`=remote DB (unimplemented stub — falls back to dummy, logged at `error`). An unrecognized value also falls back to dummy, logged at `error`. |
| `ip`, `port` | str, int | `"127.0.0.1"`, `80` | Reserved for `type=2`'s remote DB mode; unused since that mode isn't implemented. |
| `yaml_path` | str | `""` | Path to the YAML file for `type=1`. Missing file, missing PyYAML, a parse failure, a non-mapping root, or zero valid entries all fall back to dummy (logged at `error`). |
| `forward_body` | bool | `True` | If `True` and the query's payload is a u8vector, echoes it back on `db_callback`; otherwise the response payload is `PMT_NIL`. Currently has no real effect on `inject_db`, the only consumer in this repo — it always uses its own independently-tracked pending payload instead (see Behavior). Kept for a future consumer that might not track its own pending payload. |
| `auto_reset_counters` | bool | `False` | `type=0` only: if `True`, `sdls_counter` resets to `0` after its max value is served instead of the entry being refused — reusing AES-CTR counters, so for test use only. Has no effect on `vcid_counter`, which always wraps. |
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
modeled as **16-bit** (`SDLS_COUNTER_MAX = 0xFFFF`) — it is transmitted
as the 2-byte IV in `sdls_header`'s Security Header, and every SDLS block
accepts exactly `0`–`65535`. `vcid_counter` is **8-bit**
(`VCID_COUNTER_MAX = 0xFF`). A stored counter outside its range is
rejected per request (logged at `error`, no publish).

**Counter overflow**: counters are incremented *after* publishing the
current (pre-increment) value, so the query that reaches a counter's max
still gets a valid response — only the *next* query at that SCID/SPI is
affected. The two counters overflow differently:

- **`vcid_counter`** is the TC frame sequence number N(S), which CCSDS
  232.0-B counts modulo 256 — it always wraps `255 → 0`, in every mode,
  independent of `auto_reset_counters`. It has no security role, so
  wrapping needs no new key.
- **`sdls_counter`**: `_checked_increment` raises `OverflowError` at its
  max; with `type=0 and auto_reset_counters` it resets to `0` (test use
  only — see below), otherwise the entry is marked exhausted.

**`sdls_counter` exhaustion**: without auto-reset, an `sdls_counter`
overflow marks the entry `sdls_counter_exhausted`, and every later query
for that SCID/SPI is refused (logged at `error`, no publish) instead of
re-serving the max value — re-serving it would reuse a (key, counter)
pair, which breaks AES-CTR confidentiality. Other entries are unaffected.
The entry stays refused until the block is restarted with a new key.

**`forward_body=False` (or a non-u8vector query payload)**: the
`db_callback` response's payload becomes `PMT_NIL`. This is harmless in
the real pipeline: `inject_db.send_msg_out` (see
[inject_db.md](inject_db.md)) always substitutes its own independently-
stored pending payload whenever a `send_db_call` preceded the callback —
the callback's own payload, echoed or not, is never actually used in
that case. `send_msg_out` only requires the callback's own payload to be
a u8vector when there's no pending request to fall back on.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `make_db_call`'s
full body — the pair/dict shape checks, the counter-validation/increment
steps' own more specific inner `try`/`except` blocks (preserved as-is,
since they encode real business logic — whether to auto-reset a
maxed-out counter — not just generic error handling), and the final
`db_callback` publish — is wrapped in catch-log-drop (`except
Exception`). All logged at `error` (this block is not on the raw-RF
`warn` list),
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

## Test coverage

- `python/soarr/qa_db_client.py` — 23 test methods (`test_instance` +
  `test_001`–`test_023`): a dummy-mode lookup returning every expected
  field and incrementing both counters across two calls, a missing
  `scid`/`spi` and an unknown `scid`/`spi` each emitting nothing, YAML
  mode loading a flat-layout entry and using it (`test_004`), an
  `sdls_counter` already at max being served once, then refused, while
  `vcid_counter` wraps to `0` (`auto_reset_counters` off, `test_005`), configurable dummy SCID/SPI/VCID,
  configurable dummy keys, configurable dummy initial counters,
  configurable dummy key states, every dummy parameter combined in one
  test, `forward_body=True` preserving a u8vector payload,
  `forward_body=False` producing a `PMT_NIL` payload (this file only
  proves that behavior, not `inject_db`'s reaction to it — see
  [inject_db.md](inject_db.md) for that),
  `auto_reset_counters` on/off at the max counter value, a non-pair input
  dropped cleanly instead of crashing the handler (`test_015`), `scid`
  and `spi` resolved independently — a valid top-level `scid` surviving
  even when top-level `spi` is absent and a differing nested `scid`
  exists (`test_016`), the same independence for `bypass`/`control`
  (`test_017`), the nested-by-SCID YAML layout — distinct from
  `test_004`'s flat layout, with no explicit `SCID`/`SPI` field on the
  inner entries (`test_018`), and a mock-forced publish failure proven to
  be caught and dropped rather than raised through the real handler
  (`test_019`), a stored `sdls_counter` above 16 bits refused
  (`test_020`), an exhausted entry refused while another entry keeps
  being served (`test_021`), `db_request_id` echoed back when the
  query carries one and absent otherwise (`test_022`), and
  `vcid_counter` served as `254, 255, 0, 1` in YAML mode (`test_023`).
