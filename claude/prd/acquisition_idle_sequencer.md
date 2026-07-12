# acquisition_idle_sequencer

## Purpose

Bridges the TX chain's framed-PDU output (from `cltu_framer`) into a
continuous `uint8` byte stream for the SDR/modulator downstream — the
sole stream block (`gr.sync_block`) in this module; every other TX/RX
block is message-passing only. Multiplexes three sources onto one
continuous output stream, in priority order: an acquisition-sequence
burst, queued PDU bytes, and an idle-fill pattern. See
[architecture.md](../architecture.md).

## Pipeline position

TX chain, the terminal block:

```
cltu_framer.out → acquisition_idle_sequencer.in
```

`out` is a continuous byte stream, not a PDU port — it feeds an
SDR/modulator downstream, outside this repo's scope. No `.grc` flowgraph
or test in this repo wires this block's `in` port to `cltu_framer`'s
`out` port directly (`qa_layoutTest.py` doesn't instantiate this block at
all); the pipeline position above is documented in `architecture.md`, not
independently exercised here.

## Message ports / stream port

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `trigger_acq` | input | Any PMT; content is ignored entirely — receipt alone sets `pending_acquisition = True`. | `pmt.PMT_NIL` |
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. The metadata dict is ignored entirely — only the payload bytes are queued for streaming. A non-pair message or non-u8vector payload is silently dropped (no log). | `pmt.cons({}, u8vector(payload))` |
| `out` | output (stream, not message) | Continuous `uint8` byte stream — the block's real output. No output message port exists. | — |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `idle_sequence` | int | `0xAA` | Byte value repeated when idle. Not validated against the `0–255` range a `uint8` stream requires. |
| `acquisition_sequence` | list[int] | `[0xAA] * 16` | Bytes streamed once, with priority, when an acquisition is pending. A mutable list as a default argument — not currently mutated in place (`__init__` builds a fresh `np.array` from it), but a latent Python footgun. Not validated against the `0–255` per-element range. |
| `initial_acquisition` | bool | `False` | If `True`, `pending_acquisition` starts set — an acquisition burst is emitted before the first PDU/idle byte. |
| `diff_encoded` | bool | `False` | If `True`, overrides `idle_sequence` to `0xFF` and `acquisition_sequence` to `[0xFF] * len(acquisition_sequence)` — the *values* passed for both are discarded; only `acquisition_sequence`'s length is kept. |
| `tsb_tag_name` | str \| None | `None` | Tag key used to mark stream-tagged-burst boundaries (`add_item_tag`). **Default of `None` crashes the entire process the moment `work()` runs — see Known issues.** The GRC yaml's own default (`"packet_len"`) differs from the Python constructor's default (`None`); a flowgraph built through GRC never hits this default, but any direct/programmatic instantiation does. |
| `max_idle_chunk` | int | `64` | Max bytes emitted per idle-mode `work()` iteration. Falls back to `64` silently (no log) if the passed value is falsy or ≤ 0 — not a raised error. |

## Behavior / edge cases / current error handling

**Streaming priority**, checked fresh every `work()` iteration once the
current burst (if any) finishes: (1) finish any in-progress burst without
interruption, (2) start an acquisition burst if `pending_acquisition` is
set, (3) start a burst from the next queued PDU, (4) otherwise emit up to
`max_idle_chunk` bytes of `idle_sequence`. A burst, once started, is
never preempted mid-stream by a higher-priority source.

**Tag emission**: every burst start and every idle-fill chunk calls
`add_item_tag(0, out_pos, self.tsb_tag_key, pmt.from_long(length))` to
mark the boundary — this call is on every single code path `work()` can
take, with no exception.

**`handle_msg`**: checks `pmt.is_pair(msg)` and `pmt.is_u8vector(vec)`
before queuing payload bytes; a message failing either check is silently
dropped — no log call on that path, unlike every other reviewed block's
rejection paths.

**`handle_trigger_acq`**: sets `pending_acquisition = True` unconditionally,
regardless of the received PMT's content.

**Error handling**: `handle_msg`/`handle_trigger_acq` have no
`try`/`except` (nothing in either does PMT-shape-risky work beyond the
two checks already present in `handle_msg`). `work()` also has no
`try`/`except` — see Known issues for what that means in practice here.

**Docstrings**: the class docstring has one real line ("Acquisition and
Idle Sequencer (PDU to Stream Source)"), not `gr_modtool`'s placeholder.
`__init__`, `handle_trigger_acq`, `handle_msg`, `_start_burst`, and
`work()` have none.

**Naming**: file, class, GRC block-id, every constructor parameter, and
every handler method name are already snake_case — this block needs no
naming changes.

## Known issues / TODOs

- **`work()` crashes the entire Python process with a native access
  violation under this block's own default configuration — not a
  catchable Python exception.** Reproduced directly, with a full stack
  trace pointing to `add_item_tag(0, out_pos, self.tsb_tag_key, ...)`
  (both call sites: `_start_burst:72` and `work()`'s idle branch:109),
  called with `self.tsb_tag_key = None` (the result of the constructor's
  own `tsb_tag_name=None` default). Confirmed the crash occurs on *every*
  `work()` code path (burst-start and idle-fill both call
  `add_item_tag`), and confirmed passing an explicit, valid
  `tsb_tag_name` (e.g. `"packet_len"`, matching the GRC yaml's own
  default) avoids it entirely — GNU Radio's `add_item_tag` binding
  appears to dereference the tag-key PMT without checking for a Python
  `None` first. Because this is a segfault, not a Python exception,
  `except Exception` (ADR-0003's usual pattern) cannot catch it — the
  fix has to prevent a `None` tag key from ever reaching `add_item_tag`
  in the first place (e.g. defaulting `tsb_tag_key` to a real PMT symbol
  or `pmt.PMT_NIL` instead of `None`). This is why
  `python/soarr/qa_acquisition_idle_sequencer.py` has been excluded from
  every full-suite `pytest` run referenced elsewhere in this repo's
  history — none of its 5 test methods pass `tsb_tag_name`, so all 5 hit
  this crash.
- **`idle_sequence`/`acquisition_sequence` element values are not
  validated against the `0–255` range a `uint8` output stream requires.**
  Reproduced indirectly: NumPy raises `OverflowError` assigning an
  out-of-range Python int into a `uint8` array slice, the same operation
  `work()` performs for `idle_sequence`. Not wrapped in any
  `try`/`except`, so this would propagate out of `work()` uncaught.
- **`handle_msg` drops a malformed `in` message with no log call**,
  unlike every other reviewed block's rejection paths (which all log at
  `error` or `warn`).
- **`acquisition_sequence` is fixed at construction** (`_acq_bytes` is
  built once in `__init__`); reassigning `instance.acquisition_sequence`
  afterward has no effect on the bytes actually streamed, unlike
  `cltu_framer`'s `start_sequence`/`tail_sequence`, which re-read fresh
  on every message. No test in this repo exercises runtime reassignment
  of this block's sequence parameters either way.
- **`max_idle_chunk`'s invalid-value fallback is silent** — no log call
  when a falsy/non-positive value is replaced with `64`.

## Test coverage

- `python/soarr/qa_acquisition_idle_sequencer.py` — 5 test methods
  (`test_instance` + `test_001`–`test_004`): instantiation, an initial
  acquisition burst followed by idle fill, `diff_encoded` overriding both
  sequences to `0xFF`, PDU-payload passthrough onto the stream followed
  by idle fill, and an idle-only stream matching a configured byte.
  **None of the 4 behavioral tests (`test_001`–`test_004`) currently
  complete** — every one crashes the test process via the access
  violation described above, since none passes `tsb_tag_name`.
