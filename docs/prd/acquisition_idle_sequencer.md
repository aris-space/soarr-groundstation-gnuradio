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
`out` port directly (`qa_tx_chain.py` doesn't instantiate this block at
all); the pipeline position above is documented in `architecture.md`, not
independently exercised here.

## Message ports / stream port

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `trigger_acq` | input | Any PMT; content is ignored entirely — receipt alone sets `pending_acquisition = True`. | `pmt.PMT_NIL` |
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. The metadata dict is ignored entirely — only the payload bytes are queued for streaming. A non-pair message or non-u8vector payload is dropped and logged at `error`. | `pmt.cons({}, u8vector(payload))` |
| `out` | output (stream, not message) | Continuous `uint8` byte stream — the block's real output. No output message port exists. | — |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `idle_sequence` | int | `0xAA` | Byte value repeated when idle. Validated in `__init__` (raises `ValueError` if outside `0–255`). |
| `acquisition_sequence` | list[int] \| None | `None` (defaults to `[0xAA] * 16`) | Bytes streamed once, with priority, when an acquisition is pending. Each element validated in `__init__` (raises `ValueError` if outside `0–255`). |
| `initial_acquisition` | bool | `False` | If `True`, `pending_acquisition` starts set — an acquisition burst is emitted before the first PDU/idle byte. |
| `diff_encoded` | bool | `False` | If `True`, overrides `idle_sequence` to `0xFF` and `acquisition_sequence` to `[0xFF] * len(acquisition_sequence)` — the *values* passed for both are discarded; only `acquisition_sequence`'s length is kept. |
| `tsb_tag_name` | str \| None | `None` | Tag key used to mark stream-tagged-burst boundaries (`add_item_tag`). `None` means no tag is emitted at all — every `add_item_tag` call site checks `tsb_tag_key is not None` first. The GRC yaml's own default is `"packet_len"`, differing from the Python constructor's default of `None`; a flowgraph built through GRC always gets a real tag name, direct/programmatic instantiation gets no tagging unless one is passed explicitly. |
| `max_idle_chunk` | int | `64` | Max bytes emitted per idle-mode `work()` iteration. Falls back to `64` (logged at `debug`) if the passed value isn't a positive int. |

## Behavior / edge cases / current error handling

**Streaming priority**, checked fresh every `work()` iteration once the
current burst (if any) finishes: (1) finish any in-progress burst without
interruption, (2) start an acquisition burst if `pending_acquisition` is
set, (3) start a burst from the next queued PDU, (4) otherwise emit up to
`max_idle_chunk` bytes of `idle_sequence`. A burst, once started, is
never preempted mid-stream by a higher-priority source.

**Tag emission**: a burst start (`_start_burst`) and an idle-fill chunk
each call `add_item_tag(0, out_pos, self.tsb_tag_key, pmt.from_long(length))`
to mark the boundary, guarded by `tsb_tag_key is not None`. The
burst-continuation path (writing more of an already-started burst) does
not call it again — the tag was already written when that burst started.

**`handle_msg`**: checks `pmt.is_pair(msg)` and `pmt.is_u8vector(vec)`
before queuing payload bytes; a message failing either check is dropped
and logged at `error`.

**`handle_trigger_acq`**: sets `pending_acquisition = True` unconditionally,
regardless of the received PMT's content.

**Error handling**: `handle_msg` and `handle_trigger_acq` each wrap their
full body in catch-log-drop, compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md). `work()` is not
a message handler — it runs on GNU Radio's scheduler thread, a different
execution context ADR-0003 doesn't cover — so it has no try/except;
instead, the values it depends on (`idle_sequence`, each
`acquisition_sequence` element, `tsb_tag_key`) are validated or
guarded before `work()` can ever see a bad one.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`handle_trigger_acq`/`handle_msg`, `Args`/`Returns` for `_start_burst`
and `work()`.

**Naming**: file, class, GRC block-id, every constructor parameter, and
every handler method name are already snake_case.

## Known issues / TODOs

- **The acquisition sequence is fixed at construction, with no public
  attribute to reassign at all.** `_acq_bytes` (private) is built once in
  `__init__` from the constructor's `acquisition_sequence` argument;
  nothing keeps that argument's value on the instance afterward. This
  differs from `cltu_framer`'s `start_sequence`/`tail_sequence`, which are
  public attributes re-read fresh on every message. No test in this repo
  exercises runtime reassignment of this block's acquisition bytes either
  way.

## Test coverage

- `python/soarr/qa_acquisition_idle_sequencer.py` — 9 test methods
  (`test_instance` + `test_001`–`test_008`): instantiation, an initial
  acquisition burst followed by idle fill, `diff_encoded` overriding both
  sequences to `0xFF`, PDU-payload passthrough onto the stream followed
  by idle fill, an idle-only stream matching a configured byte, invalid
  `idle_sequence`/`acquisition_sequence` values raising at construction
  (`test_005`, `test_006`), a malformed `in` message dropped cleanly with
  nothing queued (`test_007`), and the default `tsb_tag_name=None`
  configuration streaming correctly with no tag emitted (`test_008`).
  `test_003`'s PDU injection calls `handle_msg` directly rather than
  `message_port_pub` — the latter publishes *from* an output port, and
  `in` is an input port, so it isn't the right call for feeding a message
  into this block from a test.
