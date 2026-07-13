# cltu_deframer

## Purpose

Finds CLTU frames in an incoming byte/bit stream (start sequence,
payload, tail sequence — CCSDS 231.0-B-4), validates the start/tail
sequences within a bit-error threshold, and publishes the extracted
payload as a PDU. The RX chain's stream-to-message entry point. See
[architecture.md](../architecture.md).

## Pipeline position

RX chain, the entry point:

```
(raw RF byte/bit stream) → cltu_deframer.in
cltu_deframer.out → ccsds_receiver.in
```

Also feeds the separate, internal-testing-only chain documented in
[ADR-0005](../adr/0005-rx-path-canonical-block.md):
`cltu_deframer → bch_decoder → lfsr_descrambler`, exercised by
`python/soarr/qa_lfsr_receive_chain.py`. No test or `.grc` flowgraph in
this repo wires `cltu_deframer` into `ccsds_receiver`.

## Message ports / stream port

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input (stream) | `uint8` items — bytes if `input_packed=True`, one bit (0/1) per item if `False`. | — |
| `out` | output (message) | PDU: `(metadata_dict . payload)`. Metadata carries `corr_start_errors`/`corr_tail_errors` (int, bit-error counts against the expected start/tail sequences). Payload is bytes if `output_packed=True`, one bit per item if `False`. | `pmt.cons({corr_start_errors: 0, corr_tail_errors: 0}, u8vector(payload))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `start_sequence` | int | `0xEB90` | CCSDS 231.0-B-4 CLTU start sequence (16 bits). |
| `tail_sequence` | int | `0xC5C5C5C5C5C5C579` | CCSDS 231.0-B-4 CLTU tail sequence (64 bits). |
| `payload_bytes` | int | `8` | Expected payload width. Validated in `__init__` (raises `ValueError` if not positive). |
| `threshold` | int | `2` | Max bit errors tolerated in the start *or* tail sequence for a frame to still be accepted. Validated in `__init__` (raises `ValueError` if negative). |
| `input_packed` | bool | `True` (constructor) / `False` (GRC yaml default) | Stream item format — see Known issues for the default mismatch. |
| `output_packed` | bool | `True` (constructor) / `False` (GRC yaml default) | Output payload format — same mismatch. |
| `tag_name` | str | `"start"` | Stream tag key the correlated-access-code detection path (`general_work`) looks for. |

## Behavior / edge cases / current error handling

**Two independent frame-detection implementations exist on this class**,
sharing the same mutable buffers (`self._buffer`/`self._bit_buffer`) but
otherwise unconnected:

1. **Tag-based** (`general_work` → `_try_process_pending_tag` →
   `_process_tag`): the real GNU Radio scheduler entry point.
   `general_work` appends incoming stream items to the buffer, looks up
   GNU Radio stream tags matching `tag_name` in the just-consumed range,
   queues each as a pending tag, and calls `_try_process_pending_tag`,
   which locates the frame relative to the tag offset (`tag_offset_bits
   = -15`, hardcoded, not a constructor parameter), validates it, and
   publishes.
2. **Untagged brute-force** (`process_bytes`, docstringed "for backward
   compatibility with tests"): scans the buffer byte-by-byte (or
   bit-by-bit) for a window that passes `_validate_frame_bits`, with no
   tag involved at all — an O(n) scan per byte position, i.e. O(n²) over
   a growing buffer.

**No test in this repo calls `general_work` directly or wires this block
into a real stream flowgraph** — `qa_cltu_deframer.py`'s tag-based tests
(`test_007`, `test_010`–`test_012`) call `_try_process_pending_tag`
directly via the `test_set_bit_buffer`/`test_set_pending_tag`/
`process_pending_tag` test helpers, bypassing `general_work` entirely;
every other test (`test_001`–`test_006`) uses `process_bytes`. The one
cross-block integration test that exercises this block
(`qa_lfsr_receive_chain.py`) also calls `process_bytes`, not
`general_work`. The block's actual GNU Radio scheduler entry point has
zero test coverage, direct or indirect, anywhere in this repo.

**`bit_order` is hardcoded to `"msb"`** (`self.bit_order = "msb"` in
`__init__`, no corresponding constructor parameter) — the `else`
branches handling `bit_order != "msb"` in `_bytes_to_bits`,
`_pack_bits_to_bytes`, and `_bit_error_positions` are unreachable, and
`_reverse_byte_bits` (apparently an lsb-path helper) is defined but never
called anywhere in the class.

**Error handling**: `cltu_deframer` *is* on
[coding-standards.md](../coding-standards.md)'s raw-RF `warn` list —
its existing `self.logger.warn(...)` calls (frame rejected for exceeding
the correlation-error threshold) are already correctly at `warn`, not
`error`, matching the established rule (unlike every other block
reviewed so far in this pass, which needed a warn→error fix). This block
has no message input port and registers no `set_msg_handler`, so
[ADR-0003](../adr/0003-message-handler-error-policy.md)'s catch-log-drop
policy — scoped to the message-handler thread — doesn't literally apply
here; `general_work` runs on GNU Radio's scheduler thread instead, the
same category of execution context as `acquisition_idle_sequencer.work()`.

**Docstrings**: the class docstring already has substantial real
content (packed/unpacked semantics, tag offset behavior) — unlike most
`gr_modtool` placeholders. Several helpers have a one-line docstring
(`_process_tag`, `process_bytes`, `_try_process_pending_tag`,
`process_pending_tag`, `test_set_bit_buffer`, `test_set_pending_tag`,
`_validate_frame_bits`, `_bit_errors`), but none follow
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)'s
Args/Returns template; `__init__`, `general_work`, `_reverse_byte_bits`,
`_bytes_to_bits`, `_pack_bits_to_bytes`, `_bit_error_positions`,
`_bits_to_string`, and `_publish_payload` have none at all.

**Naming**: file, class, GRC block-id, every constructor parameter, and
every method name are already snake_case.

## CCSDS reference

CCSDS 231.0-B-4 (TC Synchronization and Channel Coding) — the CLTU
structure this block searches for: start sequence (`0xEB90`, 16 bits),
codeword(s) (here, one fixed-width payload region), tail sequence
(`0xC5C5C5C5C5C5C579`, 64 bits). Same structure `cltu_framer` builds on
TX — see [cltu_framer.md](cltu_framer.md), including that PRD's
confirmed-against-the-primary-standard finding that a real CLTU's
codeword region can span multiple BCH codewords under one start/tail
pair, not necessarily the single fixed `payload_bytes` width this block
currently assumes per frame.

## Known issues / TODOs

- **Two independent, duplicated frame-detection code paths share mutable
  state, and the real production path has zero test coverage.** See
  Behavior above. `process_bytes`'s own docstring acknowledges it exists
  only "for backward compatibility with tests," yet it's the path nearly
  every test (and the one real cross-block integration test) actually
  exercises — `general_work`, what GNU Radio's scheduler would actually
  call in a wired flowgraph, is untested anywhere in this repo.
- **`grc/soarr_cltu_deframer.block.yml`'s `input_packed`/`output_packed`
  defaults (`'False'`) don't match the Python constructor's own defaults
  (`True`)** — a flowgraph built through GRC without explicitly setting
  either gets bit-mode by default; direct Python construction gets
  byte-packed mode by default. A real, consequential mismatch (changes
  the block's fundamental I/O format), not just a style inconsistency.
- **`bit_order`'s non-`"msb"` branches and `_reverse_byte_bits` are dead
  code** — `bit_order` is hardcoded with no way to configure it
  differently. See Behavior above.
- **Docstrings incomplete on most methods** — see Behavior above.

## Test coverage

- `python/soarr/qa_cltu_deframer.py` — 13 test methods (`test_instance`
  + `test_001`–`test_007`, `test_010`–`test_012`): single-frame
  extraction, extraction after leading/trailing noise, input split
  across two calls, multiple frames in one chunk, unpacked-bit input,
  unpacked-bit output — all via `process_bytes` (the untagged
  brute-force path, not `general_work`) — a simulated correlator tag
  producing a PDU, a tag not emitting until the full frame is buffered,
  and threshold behavior (errors within/exceeding the limit accepted/
  rejected) — these four via direct `_try_process_pending_tag` calls
  (through test-only helper methods), not `general_work`. No test calls
  `general_work` itself, and no test constructs a real GNU Radio
  flowgraph feeding this block's stream input.
