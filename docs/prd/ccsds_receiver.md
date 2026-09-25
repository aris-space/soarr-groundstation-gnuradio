# ccsds_receiver

## Purpose

The RX chain's frame-reassembly stage: takes fixed-length codeword PDUs
already BCH-corrected and descrambled at the physical/FEC layer,
searches for a valid CCSDS 232.0-B-4 Transfer Frame Primary Header
(TFPH), accumulates payload bytes until a complete TC transfer frame has
been collected, and publishes the reassembled frame. See
[architecture.md](../architecture.md).

## Pipeline position

The one canonical, documented RX path
([ADR-0005](../adr/0005-rx-path-canonical-block.md)): imports
`bch_decoder` and `lfsr_descrambler` directly as Python helpers
(`ccsds_receiver.py:14,99-100`) and calls them per incoming codeword —
see [bch_decoder.md](bch_decoder.md) and
[lfsr_descrambler.md](lfsr_descrambler.md) for exactly how each is
invoked and pre-validated from here (`_checkmsg`, `DESCRAMBLING_ACTIVE`,
and the 4 `reset_sequence()` call sites are documented in those two
PRDs, not repeated here). Despite being the canonical path, **no `.grc`
flowgraph file exists anywhere in this repo** (`find . -iname "*.grc"`
returns nothing) — this block has no example flowgraph wiring it to
anything.

```
(BCH-corrected, descrambled codeword PDUs) → ccsds_receiver.in
ccsds_receiver.out → ccsds_reader.in
```

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . codeword_u8vector)`, codeword exactly 8 bytes — validated by `_checkmsg` before use. | `pmt.cons({}, u8vector(8 bytes))` |
| `out` | output | PDU: `(metadata_dict . frame_u8vector)`, metadata is a freshly built dict carrying only `frame_length` (int, the published frame's actual byte count), payload is the fully reassembled TC transfer frame (TFPH + payload bytes, whatever was accumulated). | `pmt.cons({frame_length: 16}, u8vector(16 bytes))` |

`receiver` (the handler registered on `in`) returns the **original,
undecoded input `msg`** on the one frame-completion path (see Behavior)
and `None` implicitly everywhere else — this return value is never used
by anything in this repo (unlike `bch_decoder`/`lfsr_descrambler`, whose
return values `ccsds_receiver` itself relies on).

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `message_type` | int | `0` | `0`=TC, `1`=TM, `2`=Fixed Length. Validated in `__init__` (raises `ValueError` if outside `0`-`2`). Selecting `1` (TM) doesn't fail construction — the `NotImplementedError` for TM only fires later, from inside the message handler (see Behavior). |
| `field_type` | int | `0` | `0`=TC Field, `1`=Encapsulation Field. Validated in `__init__` (raises `ValueError` if outside `0`-`1`). Same pattern as `message_type`: `1` passes construction, only raises from inside the handler when actually exercised. Only meaningful when `message_type=0`; the GRC yaml hides this parameter otherwise. |
| `fixed_byte_length` | int | `0` | Target byte count for Fixed Length mode. Validated in `__init__` (raises `ValueError` if outside `0`-`1023`), matching the GRC yaml's own asserts. Only meaningful when `message_type=2`. |
| `scid` | int | `0` | Spacecraft ID to filter on. Only applied if `scid_filter_enable=True`; otherwise `self.scid` is set to `None` and no filtering occurs. |
| `vcid` | int | `0` | Virtual Channel ID to filter on. Same enable-flag pattern as `scid`. |
| `scid_filter_enable` | bool | `False` | Gates whether `scid` is stored (`self.scid`) or discarded in favor of `None` (no filtering). |
| `vcid_filter_enable` | bool | `False` | Gates whether `vcid` is stored (`self.vcid`) or discarded in favor of `None` (no filtering). |

## Behavior / edge cases / current error handling

**Per-codeword pipeline** (`_readInputMsg`, called once per incoming
PDU): `_checkmsg` validates PDU shape and exact 8-byte length → the
codeword is passed through `bch_decoder.error_correction_mode` (returns
`None` on failure) → then, if `DESCRAMBLING_ACTIVE` (a module constant,
always `True`), through `lfsr_descrambler.descramble_msg` (also `None`
on failure) → the resulting 8 payload bytes are returned. Any failure at
any stage returns `None` from `_readInputMsg`.

**TC mode, TFPH search and frame reassembly** (`message_type=0`,
`field_type=0`): while no frame is in progress (`self.length_found ==
False`), every incoming 8-byte codeword is passed to `_searchTFPH`,
which parses the CCSDS 232.0-B-4 TFPH (`tc_header()`, 40 bits: `tfvn`(2),
`bypass_flag`(1), `control_flag`(1), `reserve`(2), `scid`(10),
`vcid`(6), `frame_length`(10), `frame_sequence_number`(8) —
`_searchTFPH` slices `cltu_frame[:6]` for this, one byte more than the
struct's own 5-byte/40-bit width; harmless, since `construct`'s
`BitStruct.parse()` doesn't require exact-length input and just ignores
the unused 6th byte, confirmed directly against the `construct` library)
from the current 8-byte chunk and rejects it (returns `None`, search
continues on the next codeword) if `tfvn != 0`, `reserve != 0`,
`frame_length` is outside `5`-`1024` (`MAX_FRAME_SIZE`), or (when the
corresponding filter is enabled) `scid`/`vcid` don't match. Once a valid
TFPH is found: `total_frame_length = frame_length` (the raw field
value), `remaining_frame_length = frame_length + 1` (the actual total
frame byte count — CCSDS's length-minus-one field convention),
`length_found = True`. **The same codeword that contained the TFPH is
also recorded into `frame_buffer`** in the same `receiver` call (the
TFPH lives at the start of the frame's own byte stream, not separate
from it) — accumulation continues on each subsequent codeword via
`_recordCurrentMessage` until `remaining_frame_length <= 0`, at which
point `_publishFrame(self.total_frame_length)` slices exactly
`total_frame_length + 1` bytes from `frame_buffer` and publishes them,
`length_found` resets to `False`, and `lfsr_descrambler.reset_sequence()`
is called to prepare for the next frame.

**TM mode** (`message_type=1`): `receiver` executes
`raise NotImplementedError("Message type TM is not implemented yet.")`
directly and unconditionally on the first codeword of any search cycle —
this is the precise, current form of this repo's "TC-only" scope: not a
value that's silently ignored, but a raise, caught by `receiver`'s own
catch-log-drop wrap (see Error handling below) rather than escaping the
handler.

**Fixed Length mode** (`message_type=2`): handled entirely inside
`_handleMessageTypeFixed`, which extends `frame_buffer`, decrements
`remaining_fixed_bytes`, and — once `len(frame_buffer) >=
fixed_byte_length` — publishes via `_publishFrame(fixed_byte_length -
1)`, resets `remaining_fixed_bytes`, and calls
`lfsr_descrambler.reset_sequence()`. **`self.length_found` is never set
`True` anywhere for this mode** (`grep` confirms `length_found = True`
appears exactly once in this file, inside the TC-only branch), so
`receiver`'s frame-completion check (which only fires when
`length_found` is true) never runs for this mode — this mode's
publish/reset logic inside `_handleMessageTypeFixed` is the only
reachable completion path. No test in this repo constructs
`message_type=2`.

**Encapsulation Field** (`field_type=1`, only reachable when
`message_type=0`): `receiver` executes
`raise NotImplementedError("Length type 'Encapsulation Field' is not implemented yet.")`
directly, on the first codeword of any search cycle, exactly the same
pattern as the TM case above — caught by `receiver`'s catch-log-drop
wrap.

**SCID/VCID filtering**: when disabled (the default), `self.scid`/
`self.vcid` are `None` and `_searchTFPH` skips the corresponding
mismatch check entirely; when enabled, a mismatch causes `_searchTFPH`
to return `None` (treated the same as "no valid TFPH yet," search
continues) rather than any kind of error — a filtered-out frame is
silently never found, not explicitly rejected.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `ccsds_receiver`
is on the raw-RF `warn` list, and every log call in `_checkmsg`,
`_readInputMsg`, and `receiver` that reports a drop condition is at
`warn`. `receiver`'s full body — including both `NotImplementedError`
sites and any other exception a TFPH parse or frame-reassembly step
might raise — is wrapped in catch-log-drop (`except Exception`), logged
at `warn`; nothing escapes the handler.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): a real
class-level summary; full `Args`/`Raises` for `__init__`; `Args`/`Returns`
for `_checkmsg` and `_readInputMsg` (both touch PMT); full
`Args`/`Publishes` for `_publishFrame` (directly builds and publishes a
PMT PDU); full `Args`/`Publishes`/`Drops when` for `receiver` (the
PMT-touching message handler). `tc_header` has a docstring describing
the TFPH layout (non-PMT, already adequate). `_searchTFPH`,
`_handleMessageTypeTC`, `_handleMessageTypeFixed`, `_recordCurrentMessage`
(non-PMT private helpers) have none, permitted as-is by
[coding-standards.md](../coding-standards.md)'s exemption for that
category.

**Naming**: file, class, GRC block-id, and every constructor parameter
are already snake_case. Several private/helper method names are not:
`_readInputMsg`, `_searchTFPH`, `_handleMessageTypeTC`,
`_handleMessageTypeFixed`, `_recordCurrentMessage`, `_publishFrame` are
all camelCase, not snake_case — a naming gap in this block, not yet
covered by [coding-standards.md](../coding-standards.md)'s block-level
rename table (that table covers file/class/block-id renames only).
`_checkmsg` is a milder, separate case — missing an underscore rather
than true camelCase.

## CCSDS reference

CCSDS 232.0-B-4 (TC Space Data Link Protocol) — the Transfer Frame
Primary Header (TFPH) this block searches for and parses: 40 bits
(`tfvn`, `bypass_flag`, `control_flag`, `reserve`, `scid`, `vcid`,
`frame_length`, `frame_sequence_number`), with `frame_length` encoded as
total-frame-byte-count-minus-one (this block adds `+1` to recover the
real byte count). Stated per the code's own pre-existing field layout
and comments; not independently verified against the standard from this
repo alone.
`frame_length`'s own field description
(`ccsds_receiver.py:185`) states it includes "the Frame Error Control
Field," but this block performs no FECF validation or stripping — a
published frame is exactly whatever bytes were accumulated, with no CRC
check against the field this codebase's TX side adds via the stock
`digital.crc_append` block.

## Test coverage

- `python/soarr/qa_ccsds_receiver.py` — 11 test methods (`test_instance`
  + `test_001`-`test_010`): construction and its default parameter
  values (`test_instance`), an invalid TFPH (`tfvn=1`) never publishing
  (`test_001`), a complete 2-codeword TC frame published with the
  correct bytes, length, and TFPH field values recoverable from the
  output (`test_002`), SCID and VCID filters each independently
  rejecting a mismatched frame (`test_003`, `test_004`), a partial
  (1-codeword) frame's `remaining_frame_length` reflecting the one
  already-consumed codeword (`test_005`), invalid reserved bits never
  publishing (`test_006`), TM mode dropped cleanly instead of raising
  (`test_007`), Encapsulation Field dropped cleanly instead of raising
  (`test_008`), a mock-forced publish failure proven to be caught
  and dropped rather than raised through the real handler (`test_009`),
  and a negative or `>= 1024` `fixed_byte_length` raising `ValueError`
  at construction (`test_010`). No test in this repo constructs
  `message_type=2` (Fixed Length mode).
