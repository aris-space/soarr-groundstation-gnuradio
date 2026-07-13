# data_creator

## Purpose

Generates a synthetic TX PDU (metadata + payload) on demand, triggered
by any message on `ping` — ground-tooling, an alternative to
`inject_db`/`db_client`'s DB-backed payload source (only one of the two
is used at a time in a given flowgraph). See
[architecture.md](../architecture.md).

## Pipeline position

TX chain, an alternative entry point to `inject_db`:

```
(any trigger) → data_creator.ping
data_creator.out → encapsulation_header.in
```

Not wired in `python/soarr/qa_layoutTest.py`'s flowgraph at all — that
test uses `inject_db`/`db_client` instead. No test or `.grc` flowgraph in
this repo exercises `data_creator` feeding `encapsulation_header`; its
only test coverage is standalone, via its own qa file.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `ping` | input | Any PMT — content is ignored entirely; receipt alone triggers generation. | `pmt.PMT_NIL` |
| `out` | output | PDU: `(metadata_dict . payload_u8vector)`. Metadata carries `telecommand.tc_header.{scid,bypass_flag,control_flag}` and `sdls.security_header.{spi,security_param_index}` (both `spi` and `security_param_index` hold the same value — `security_param_index` matches `ccsds_reader`'s own field name for the same concept on the RX side). | `pmt.cons({telecommand: {...}, sdls: {...}}, u8vector(payload))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `mode` | int | `0` | Only `0` is implemented. Any other value raises `NotImplementedError` directly out of the `ping` handler (see Known issues) — not caught, not logged as a drop. |
| `data` | int \| bytes \| bytearray \| array-like \| `None` | `None` | Explicit payload. An int is packed big-endian; bytes/bytearray/array-like are used as-is. |
| `data_length_bytes` | int \| `None` | `None` | Either the exact byte width to left-pad an int `data` to, or (if `data` is `None`) the length of a randomly generated payload. |
| `scid`, `spi`, `bypass`, `control` | int, int, bool, bool | `0, 0, False, False` | Written into the output metadata's nested `tc_header`/`security_header` (see Message ports). |
| `vcid`, `vcid_counter`, `sdls_counter` | int, int, int | `0, 0, 0` | Stored as instance attributes but never read by `generate_message` — not present anywhere in the output metadata. Not exposed as GRC parameters either (`grc/soarr_data_creator.block.yml`'s `make:` template passes only `mode`/`data`/`data_length_bytes`/`scid`/`spi`/`bypass`/`control` — 7 of the constructor's 10 parameters). |

## Behavior / edge cases / current error handling

**`data`/`data_length_bytes` validation is inverted relative to the
class's own documented example.** The class docstring states: "If data
is an int and data_length_bytes is set, the value is left-padded to that
length... Example: `data=0x00010203, data_length_bytes=4 -> 00 01 02
03`" — but the constructor's actual check
(`if self.data is not None and self.length is not None: raise
ValueError(...)`) rejects exactly that combination. Reproduced directly:
`data_creator(data=0x00010203, data_length_bytes=4)` — the docstring's
own worked example — raises `ValueError: Either data or
data_length_bytes must be provided.` `qa_data_creator.py::test_003`
currently asserts this rejection as expected behavior, directly
contradicting the docstring's example.

**Providing neither `data` nor `data_length_bytes` also crashes**, a
different way: with both `None`, the "either/or" check above doesn't
fire (it only fires when *both* are provided), so execution reaches
`np.random.randint(0, 256, size=self.length, dtype=np.uint8)` with
`self.length=None` — NumPy's `size=None` returns a scalar, not an array,
and the next line (`self.length = len(self.data)`) then raises
`TypeError: object of type 'numpy.uint8' has no len()`. Reproduced
directly: `data_creator()` (every parameter at its documented default)
crashes this way.

Only providing *exactly one* of `data`/`data_length_bytes` (never both,
never neither) currently constructs successfully.

**`generate_message`**: builds `telecommand.tc_header` (`scid`,
`bypass_flag`, `control_flag`) and `sdls.security_header` (`spi`,
`security_param_index`) nested dicts, combines them with the payload
into a PDU, and publishes on `out`. No shape validation on `msg` (the
`ping` trigger's content is never inspected — there's nothing to
validate).

**Error handling**: `_choose_mode` (the `ping` handler) raises
`NotImplementedError` directly for any `mode != 0` — not caught, not
logged, escapes the handler entirely.
`qa_data_creator.py::test_004_raises_for_unsupported_mode` currently
asserts this raise as expected behavior. Neither `_choose_mode` nor
`generate_message` wraps any part of its body in `try`/`except`.

**Docstrings**: the class docstring already has real, substantial
content (usage description, int-padding example) — unlike most blocks'
`gr_modtool` placeholders. `__init__`, `_choose_mode`, and
`generate_message` have none.

**Naming**: file, class, GRC block-id, every constructor parameter, and
both method names are already snake_case.

## CCSDS reference

None — this block is test/ground tooling, not a CCSDS-defined layer.

## Known issues / TODOs

- **The `data`/`data_length_bytes` validation contradicts the class's
  own documented example, and a test currently enshrines the
  contradiction rather than the documented behavior.** See Behavior
  above — reproduced directly against both the docstring's worked
  example and `test_003`'s assertion.
- **Providing neither `data` nor `data_length_bytes` crashes with an
  unrelated `TypeError`**, not the `ValueError` the "either/or" check
  seems intended to produce for that exact case. See Behavior above.
- **`vcid`, `vcid_counter`, `sdls_counter` are accepted, stored, and
  entirely unused** — never written into the output metadata, and not
  reachable via the GRC block at all. Any downstream block requiring
  them (`tc_primary_header` needs `vcid_counter`, `sdls_header` needs
  `sdls_counter`) would reject a message built purely from
  `data_creator` output with no other metadata source filling them in.
- **`_choose_mode` raises `NotImplementedError` directly out of the
  `ping` handler for any unsupported `mode`**, violating
  [ADR-0003](../adr/0003-message-handler-error-policy.md) (no message
  handler may raise). `qa_data_creator.py::test_004` currently asserts
  this raise as expected.
- **Neither handler wraps any part of its body in catch-log-drop.**
- **No docstrings on `__init__`, `_choose_mode`, or `generate_message`.**

## Test coverage

- `python/soarr/qa_data_creator.py` — 12 test methods (`test_instance` +
  `test_001`–`test_011`): manual-data generation with a real
  `pmt.u8vector` output check, mode-0 random-payload generation via
  `_choose_mode` with a length check, `data`+`data_length_bytes` both
  provided raising `ValueError` (`test_003` — enshrines the
  docstring-contradicting behavior above), an unsupported `mode` raising
  `NotImplementedError` out of `_choose_mode` (`test_004` — enshrines the
  ADR-0003 violation above), message ports registered, `bypass`/`control`
  default/set/combined across several tests, and flags combined with
  other parameters. No test constructs with neither `data` nor
  `data_length_bytes` (the crashing case), and no test checks `vcid`/
  `vcid_counter`/`sdls_counter` end up anywhere in the output metadata.
