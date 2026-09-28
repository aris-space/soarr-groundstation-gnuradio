# data_creator

## Purpose

Generates a synthetic TX PDU (metadata + payload) on demand, triggered
by any message on `ping` — the test payload source at the start of the TX
chain. It supplies the frame fields (SCID, SPI, bypass/control flags,
VCID); `inject_db` then adds the keys and counters from `db_client`. See
[architecture.md](../architecture.md).

## Pipeline position

TX chain source, feeding `inject_db`:

```
(any trigger) → data_creator.ping
data_creator.out → inject_db.in
```

Every example flowgraph in [examples/](../../examples/) wires it this way,
triggered by a message strobe or a push button. Not wired in
`python/soarr/qa_tx_chain.py`, which posts its PDU to `inject_db`
directly; its unit tests are standalone, via its own qa file.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `ping` | input | Any PMT — content is ignored entirely; receipt alone triggers generation. | `pmt.PMT_NIL` |
| `out` | output | PDU: `(metadata_dict . payload_u8vector)`. Metadata carries `telecommand.tc_header.{scid,bypass_flag,control_flag,vcid,vcid_counter}` and `sdls.security_header.{spi,security_param_index,sdls_counter}` (both `spi` and `security_param_index` hold the same value — `security_param_index` matches `ccsds_reader`'s own field name for the same concept on the RX side). | `pmt.cons({telecommand: {...}, sdls: {...}}, u8vector(payload))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `mode` | int | `0` | Only `0` is implemented. Any other value is dropped and logged at `error` (no publish). |
| `data` | int \| bytes \| bytearray \| array-like \| `None` | `None` | Explicit payload. An int is left-padded big-endian to `data_length_bytes` (or its own minimal byte width if `data_length_bytes` is `None`) — the one combination of `data`+`data_length_bytes` that's allowed together, since it's the only one with a single unambiguous meaning. For bytes/bytearray/array-like `data`, `data_length_bytes` must either be omitted or match the data's actual length exactly (raises `ValueError` otherwise). |
| `data_length_bytes` | int \| `None` | `None` | Required if `data` is `None` (length of the randomly generated payload). |
| `scid`, `spi`, `bypass`, `control`, `vcid`, `vcid_counter`, `sdls_counter` | int, int, bool, bool, int, int, int | all `0`/`False` | Written into the output metadata's nested `tc_header`/`security_header` (see Message ports). All seven are exposed as GRC parameters and passed through the `make:` template. `vcid_counter`/`sdls_counter` are placeholders when this block feeds `inject_db(role="tx")`, which replaces them with `db_client`'s counters. |

## Behavior / edge cases / current error handling

**`data`/`data_length_bytes` validation**: raises `ValueError` if neither
is provided. If `data` is an int, `data_length_bytes` (if given) sets the
exact left-pad width — the docstring's own worked example
(`data=0x00010203, data_length_bytes=4 -> 00 01 02 03`) is the intended
behavior for this case. If `data` is bytes/bytearray/array-like,
`data_length_bytes` (if given) must match `data`'s actual length exactly
— there's no single correct way to reconcile a mismatch (pad? truncate?
both are guesses), so a mismatch raises `ValueError` rather than picking
one.

**`generate_message`**: builds `telecommand.tc_header` (`scid`,
`bypass_flag`, `control_flag`, `vcid`, `vcid_counter`) and
`sdls.security_header` (`spi`, `security_param_index`, `sdls_counter`)
nested dicts, combines them with the payload into a PDU, and publishes
on `out`. No shape validation on `msg` (the `ping` trigger's content is
never inspected — there's nothing to validate).

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `_choose_mode`
(the `ping` handler) wraps its full body in catch-log-drop, logging at
`error` and dropping (no publish) for an unsupported `mode`.
`generate_message` — called both from `_choose_mode` and directly in
this repo's "real handler" test style — independently wraps its own full
body in catch-log-drop too, so it's safe regardless of caller.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`_choose_mode` and `generate_message`.

**Naming**: file, class, GRC block-id, every constructor parameter, and
both method names are already snake_case. The `data_length_bytes`
constructor parameter is stored as `self.data_length_bytes`, matching
the parameter name exactly.

## CCSDS reference

None — this block is test/ground tooling, not a CCSDS-defined layer.

## Known issues / TODOs

None outstanding — see Behavior and Parameters above for the current
implementation.

## Test coverage

- `python/soarr/qa_data_creator.py` — 17 test methods (`test_instance` +
  `test_001`–`test_016`): manual-data generation with a real
  `pmt.u8vector` output check, mode-0 random-payload generation via
  `_choose_mode` with a length check, mismatched explicit data and
  `data_length_bytes` raising `ValueError` (`test_003`), an unsupported
  `mode` dropped cleanly instead of crashing the handler (`test_004`),
  message ports registered, `bypass`/`control` default/set/combined
  across several tests, flags combined with other parameters, neither
  `data` nor `data_length_bytes` raising a clear `ValueError`
  (`test_012`), an int `data` value padded correctly per the docstring's
  own example (`test_013`), explicit data whose length matches
  `data_length_bytes` succeeding (`test_014`), `vcid`/`vcid_counter`/
  `sdls_counter` present in the published metadata (`test_015`), and a
  mock-forced publish failure proven to be caught and dropped rather
  than raised through the real handler (`test_016`).
