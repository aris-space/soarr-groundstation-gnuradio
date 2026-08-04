# ccsds_reader

## Purpose

Parses a reassembled CCSDS TC transfer frame into its constituent
headers (TFPH, optional CSP header, optional Encapsulation header,
optional SDLS security header/trailer, FECF) and republishes the parsed
fields as PDU metadata alongside the extracted data payload. See
[architecture.md](../architecture.md).

## Pipeline position

RX chain, after the stock `digital.crc_check` block:

```
ccsds_receiver.out → digital.crc_check.in (stock block, checks the FECF)
digital.crc_check.{fail,ok} → ccsds_reader.ccsds
ccsds_reader.debug → inject_db (RX instance)
```

[architecture.md](../architecture.md) documents two facts about this
wiring that belong to the flowgraph, not this block's own code: (1) in
the confirmed external flowgraph, **both** of `digital.crc_check`'s
`fail` and `ok` output ports are wired to `ccsds_reader`'s input —
"that's how the flowgraph was left, not a documented design decision —
don't read architectural intent into it";
(2) this block's output port is named `debug`, but it's confirmed to be
its real, only, production output — not a diagnostic-only tap. No `.grc`
flowgraph file exists anywhere in this repo (`find . -iname "*.grc"`
returns nothing) to independently confirm either fact from inside this
repo alone.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `ccsds` | input | PDU: `(metadata_dict . frame_u8vector)`, payload the full CCSDS-framed byte stream to parse. | `pmt.cons({}, u8vector(frame bytes))` |
| `debug` | output | PDU: `(metadata_dict . data_u8vector)`. Metadata dict extends the *input* metadata dict (reused, not replaced) with `telecommand.tc_header.*`, `telecommand.frame_error_control_field`, `sdls.security_header.*`, `sdls.security_trailer` (if present), and `encapsulation_header.*` (if present). Payload is the frame's extracted data field only, unchanged. | see Behavior for exact metadata keys |

`decode_ccsds` (the handler registered on `ccsds`) also returns the
original, unparsed input `msg` on success — this return value is unused
by anything in this repo.

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `message_type` | int | `0` | GRC-exposed as a TC/TM enum (`0`/`1`), but **`self.message_type` is stored and never read anywhere else in the class** — `ccsds_message()` always builds a TC-shaped parser regardless of this value. Selecting TM in GRC has no effect on parsing. |
| `sdls_type` | int | `3` | `0`=No SDLS, `1`=Encryption only, `2`=Authentication only, `3`=Both. Not validated in `__init__` — no range check at all. |
| `encapsulation_used` | bool | `True` | Whether the parser expects an Encapsulation Packet Protocol header. Not validated (any truthy/falsy value is accepted as-is). |
| `data_type` | int | `0` | `0`=Raw, `1`=CSP (adds a `csp_header` field). Not validated — no range check at all. Unlike `message_type`, this parameter genuinely controls `ccsds_message()`'s structure (see Behavior). |

None of the four constructor parameters is validated — no range or type
checks anywhere in `__init__`.

## Behavior / edge cases / current error handling

**Structure selection** (`ccsds_message`): builds one of eight possible
`construct.Struct` layouts from `data_type`, `encapsulation_used`
(parameter, or an override argument), and `sdls_type`, selected via an
explicit `if`/`elif` chain covering every combination — always
`tc_header` (5 bytes) + optional `csp_header` (6 bytes, if
`data_type=1`) + optional `sdls_security_header` (4 bytes, if
`sdls_type != 0`) + optional `encapsulation_header` (variable,
1-8 bytes) + `data` (computed length) + optional
`sdls_security_trailer` (16 bytes, only if `sdls_type` is `2` or `3` —
authentication-bearing) + `frame_error_control_field` (2 bytes, always).
The `data` field's length is computed from the TFPH's own `frame_length`
value minus every other section's byte width (`_data_length`, `max(...,
0)`-clamped against underflow).

**Encapsulation header's variable width**: `length_of_length` (2 bits of
the header's first octet) selects between four sub-shapes (0-8 bytes
total) via `construct`'s `If`/`Switch`. The `user_defined_field` and
`protocol_id_extension` `Computed` fields are only meaningfully derived
when `length_of_length >= 0b10`; otherwise their lambda explicitly
evaluates to Python `None`. Likewise `ccsds_defined_field` and
`packet_length` are absent (`None`) below their respective
`length_of_length` thresholds.

**A `None`-valued encapsulation-header field is published as
`pmt.PMT_NIL`.** `_python_to_pmt` checks for `None` first and returns
`pmt.PMT_NIL` directly, before any of its other type branches — so a
minimal (`length_of_length=0`) encapsulation header's `user_defined_field`,
`protocol_id_extension`, `ccsds_defined_field`, and `packet_length`
(all Python `None` in that configuration) publish as a real null instead
of the PMT symbol `"None"`.

**SDLS/TC field duplication in output metadata**: `sdls.security_header`
carries both `spi` and `security_param_index` (same source value,
`parsed.sdls_security_header.security_param_index`) — matching
[data_creator.md](data_creator.md)'s own documented convention for the
same pair of key names on the TX side. It also carries both
`sdls_counter` and `initialization_vector` (same source value,
`parsed.sdls_security_header.initialization_vector`) — there's no
dedicated `sdls_counter` field anywhere in the SDLS security header's
wire format (`sdls_security_header()` only defines
`security_param_index`/`initialization_vector`), so this block
synthesizes `sdls_counter` by reusing the IV's own value.
`telecommand.tc_header` similarly carries both `vcid_counter` and `fsn`
(same source value, `parsed.tc_header.fsn`) — the TFPH has no dedicated
per-VCID counter field either; `fsn` (Frame Sequence Number) is reused
for it.

**`sdls_security_header`'s IV width is fixed, despite its own comment
saying otherwise**: the field comment reads "Initialization Vector (16
bits minimum, can be 8 or 16)," but the `BitStruct` itself hardcodes
`Bits(16)` unconditionally, and `_sdls_header_length()` always returns
`4` (2 bytes SPI + 2 bytes IV) — no code path implements the 8-bit IV
variant the comment describes.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `decode_ccsds`'s
full body — the `pmt.is_pair(msg)`/`pmt.is_u8vector(in_body)` shape
checks, parsing, building every metadata dict, extracting `data`, and
the final `message_port_pub` call — is wrapped in catch-log-drop
(`except Exception`), all logged at `warn` (matching this block's
membership in the raw-RF `warn` list).

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): a real
class-level summary; full `Args` for `__init__`; full
`Args`/`Publishes`/`Drops when` for `decode_ccsds` (the message
handler); full `Args`/`Returns` for `_python_to_pmt` (a PMT-touching
private helper). `csp_header`, `encapsulation_header`, `sdls_security_header`,
`sdls_security_trailer`, `tc_header`, `frame_error_control_field`, and
`ccsds_message` (all non-PMT `construct`-format builders, returning a
parser/`Struct` object rather than touching PMT directly) already carry
free-text docstrings describing their wire layout — adequate as-is, not
ADR-0004's PMT-handler template since they don't touch PMT.
`_encapsulation_header_length`/`_sdls_header_length` (non-PMT private
helpers) have none, permitted as-is by
[coding-standards.md](../coding-standards.md)'s exemption for that
category.

**Naming**: file, class, GRC block-id, every constructor parameter, and
every method name are already snake_case — unlike `ccsds_receiver.py`,
this file has no camelCase naming gap.

**Duplicated overhead computation**: the byte-accounting logic used to
size the `data` field during parsing (`_data_length`, a closure inside
`ccsds_message`) is re-derived a second time, independently, inside
`decode_ccsds` (`total_length`/`overhead` locals) purely to build a
debug log line — the same arithmetic exists in two places.

## CCSDS reference

CCSDS 232.0-B-4 (TC Space Data Link Protocol) for the TFPH (identical
layout to [ccsds_receiver.md](ccsds_receiver.md)'s own citation) and the
Frame Error Control Field; CCSDS 355.0-B-1 (SDLS) for the security
header/trailer layout, cross-referenced against
[sdls_header.md](sdls_header.md)'s own citation of the same standard on
the TX side; the Encapsulation Packet Protocol header (CCSDS 133.1-B)
for the variable-length encapsulation header. Stated per the code's own
pre-existing field layouts and comments; not independently verified
against the standards from this repo alone. The CSP (Cubesat Space
Protocol) header is not a CCSDS standard — it's a separate,
widely-used-in-practice convention layered on top, included here because
`data_type=1` selects it.

## Known issues / TODOs

- **`message_type` is accepted, GRC-exposed as a TC/TM choice, and
  completely unused.** See Parameters above. Whether this is a
  forward-looking placeholder for real TM support or simply dead is a
  design question — deliberately not resolved here.
- **No constructor-time validation for any of the four parameters** —
  `sdls_type` and `data_type` in particular control which `construct`
  structure gets built; an out-of-range value isn't rejected until (or
  unless) parsing itself fails downstream.

## Test coverage

- `python/soarr/qa_ccsds_reader.py` — 19 test methods: construction with
  default and several custom parameter combinations
  (`test_instance_default`, `test_instance_custom_tc_no_security`,
  `test_instance_csp_encryption`, `test_instance_authentication_only`),
  `ccsds_message()`'s field-name structure for every combination of
  optional headers (`test_ccsds_message_structure_minimal`,
  `_with_csp`, `_with_encapsulation`, `_with_sdls`, `_full`,
  `test_sdls_encryption_only`, `test_sdls_authentication_only`,
  `test_sdls_no_security` — structural checks against `subcons` field
  names, not a build/parse round-trip), message port registration
  (`test_message_port_registration`), two full build-then-decode
  round trips through the real `decode_ccsds` handler
  (`test_decode_full_packet_outputs_all_fields_and_unchanged_u8vector`,
  every optional section present; `test_decode_minimal_packet_outputs_fields_and_unchanged_u8vector`,
  minimal TC-only frame) that check the published payload bytes and a
  representative sample of metadata fields, a minimal encapsulation
  header's `None`-valued fields confirmed not to publish as the PMT
  symbol `"None"` (`test_decode_none_valued_encapsulation_fields_not_published_as_none_symbol`),
  a non-pair and a non-u8vector-body input each dropped cleanly
  (`test_decode_non_pair_dropped_cleanly`,
  `test_decode_non_u8vector_body_dropped_cleanly`), and a mock-forced
  publish failure proven to be caught and dropped rather than raised
  through the real handler (`test_decode_publish_failure_dropped_cleanly`).
