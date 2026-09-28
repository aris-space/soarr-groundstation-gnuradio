# cltu_deframer

## Purpose

Finds CLTUs in an incoming byte/bit stream (start sequence, one or more
codewords, tail sequence — CCSDS 231.0-B-4), validates the start/tail
sequences within a bit-error threshold, and publishes each CLTU's
codewords as individual PDUs. The RX chain's stream-to-message entry
point. See [architecture.md](../architecture.md).

## Pipeline position

RX chain, the entry point:

```
(raw RF byte/bit stream) → cltu_deframer.in
cltu_deframer.out → ccsds_receiver.in
```

Also feeds the separate, internal-testing-only chain documented in
[ADR-0005](../adr/0005-rx-path-canonical-block.md):
`cltu_deframer → bch_decoder → lfsr_descrambler`. Both paths are
exercised end to end from the real TX blocks by
`python/soarr/qa_lfsr_receive_chain.py` (`test_005`, `test_006`).

## Message ports / stream port

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input (stream) | `uint8` items — bytes if `input_packed=True`, one bit (0/1) per item if `False`. | — |
| `out` | output (message) | One PDU per codeword: `(metadata_dict . codeword)`. Metadata carries `corr_start_errors`/`corr_tail_errors` (int, bit-error counts against the expected start/tail sequences, the same for every codeword of a CLTU) and `scramble_reset` (bool, `True` only on a CLTU's first codeword). Payload is `payload_bytes` bytes if `output_packed=True`, one bit per item if `False`. | `pmt.cons({corr_start_errors: 0, corr_tail_errors: 0, scramble_reset: True}, u8vector(8 bytes))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `start_sequence` | int | `0xEB90` | CCSDS 231.0-B-4 CLTU start sequence (16 bits). |
| `tail_sequence` | int | `0xC5C5C5C5C5C5C579` | CCSDS 231.0-B-4 CLTU tail sequence (64 bits). |
| `payload_bytes` | int | `8` | Codeword length in bytes (8 for the BCH (63,56) code incl. its filler bit). Validated in `__init__` (raises `ValueError` if not positive). |
| `threshold` | int | `2` | Max bit errors tolerated in the start *or* tail sequence for a CLTU to still be accepted. Validated in `__init__` (raises `ValueError` if negative). |
| `input_packed` | bool | `True` | Stream item format. |
| `output_packed` | bool | `True` | Output payload format. |
| `tag_name` | str | `"start"` | Stream tag key the correlated-access-code detection path (`general_work`) looks for. |
| `max_codewords` | int | `147` | Most codewords one CLTU may hold — 147 is a maximum-size 1024-byte TC transfer frame. A start sequence with no tail within this many codewords is discarded as a false detection. Validated in `__init__` (raises `ValueError` if not positive). Last constructor argument, so positional calls from older flowgraphs stay valid. |

## Behavior / edge cases / current error handling

**CLTU parsing** (`_scan_cltu`): starting at a candidate start-sequence
position, compares the 16-bit start sequence (≤ `threshold` bit errors),
then walks forward one block at a time: a 64-bit block matching the
tail sequence (≤ `threshold` bit errors) ends the CLTU; anything else is
taken as the next `payload_bytes`-byte codeword. The result is one of:
`ok` (complete CLTU, ≥ 1 codeword), `incomplete` (more input needed to
decide), `no_start`, `no_tail` (more than `max_codewords` codewords
without a tail), or `empty` (tail directly after the start — not a
CLTU). A CLTU's codewords are published only once its tail has been
found, so a partial or false CLTU never produces output.

**Publishing** (`_publish_cltu`/`_publish_payload`): one PDU per
codeword, in order. `scramble_reset` is `True` on the first codeword of
each CLTU and `False` on the rest: CCSDS restarts de-randomization at
every CLTU, and `lfsr_descrambler` honors this key, so the standalone
`bch_decoder → lfsr_descrambler` chain stays aligned across consecutive
CLTUs. `ccsds_receiver` resets its own descrambler at each frame start
as well, so the key is redundant but harmless there.

**Two ways to locate CLTUs**, both using `_scan_cltu`:

1. **Tag-based** (`general_work` → `_try_process_pending_tag`): the GNU
   Radio scheduler entry point. `general_work` appends incoming stream
   items to the buffer, queues each stream tag matching `tag_name`, and
   processes pending tags in order. The CLTU is expected to start
   `tag_offset_bits = -15` bits before the tag (the correlator tags the
   access code's last bit; hardcoded, not a constructor parameter); in
   unpacked mode the positions one bit before and after are tried too. A
   tag whose CLTU is still `incomplete` stays pending until more data
   arrives. Tags that fall inside an already-accepted CLTU (a start-like
   pattern within a codeword) are ignored. With no tag pending, the
   buffer is trimmed to the last few items, so an untagged stream doesn't
   grow it without bound.
2. **Untagged search** (`process_bytes`): scans the buffer for a
   start-sequence position whose `_scan_cltu` result is `ok` — at byte
   boundaries in packed mode, at every bit in unpacked mode — publishes
   every complete CLTU found, and keeps any undecided tail of the buffer
   for the next call, so a CLTU may arrive split across calls.

**Bit ordering is always MSB-first** — `_bytes_to_bits` and
`_pack_bits_to_bytes` unconditionally treat each byte's most significant
bit first; there is no configurable alternative.

**Error handling**: `cltu_deframer` *is* on
[coding-standards.md](../coding-standards.md)'s raw-RF `warn` list — a
tag whose CLTU is rejected (`no_start`, `no_tail`, `empty`) is logged at
`warn`; `process_bytes`'s search logs a start with no tail at `debug`,
since random data regularly resembles a 16-bit start sequence within the
threshold. This block has no message input port and registers no
`set_msg_handler`, so
[ADR-0003](../adr/0003-message-handler-error-policy.md)'s catch-log-drop
policy — scoped to the message-handler thread — doesn't literally apply
here; `general_work` runs on GNU Radio's scheduler thread instead, the
same category of execution context as `acquisition_idle_sequencer.work()`.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Returns` for `general_work` and
`_scan_cltu`, `Args`/`Publishes`/`Returns` for `_publish_payload` and
`_publish_cltu` (both build/publish PMT PDUs). Trivial private helpers
with no PMT involvement have either a one-line docstring or none at all
(`_bytes_to_bits`, `_pack_bits_to_bytes`, `_count_errors`) — permitted
as-is by [coding-standards.md](../coding-standards.md)'s exemption for
that category.

**Naming**: file, class, GRC block-id, every constructor parameter, and
every method name are snake_case.

## CCSDS reference

CCSDS 231.0-B-4 (TC Synchronization and Channel Coding) — the CLTU
structure this block searches for (§5.2, Figure 5-1): start sequence
(`0xEB90`, 16 bits), an integral number of codewords, tail sequence
(`0xC5C5C5C5C5C5C579`, 64 bits). Same structure `cltu_framer` builds on
TX — see [cltu_framer.md](cltu_framer.md).

## Known issues / TODOs

None currently. Whether `process_bytes` should stay public alongside
`general_work` is an open design question ([#3](https://github.com/aris-space/soarr-groundstation-gnuradio/issues/3)).

## Test coverage

- `python/soarr/qa_cltu_deframer.py` — 21 test methods (`test_instance`
  + `test_001`–`test_007`, `test_010`–`test_022`). Via `process_bytes`:
  single-codeword CLTU extraction, extraction after leading/trailing
  noise, input split across two calls, multiple CLTUs in one chunk,
  unpacked-bit input, unpacked-bit output, a 3-codeword CLTU split into
  its codewords with `scramble_reset` only on the first (`test_013`),
  nothing published before the tail arrives (`test_014`), a
  multi-codeword CLTU in unpacked bits (`test_015`), back-to-back CLTUs
  of different lengths (`test_016`), a start with no tail discarded
  after `max_codewords` and the following real CLTU still found
  (`test_018`), a CLTU longer than `max_codewords` rejected
  (`test_019`), an empty CLTU ignored (`test_020`). Via the tag helpers
  (`test_set_bit_buffer`/`test_set_pending_tag`/`process_pending_tag`):
  a simulated correlator tag producing a PDU, a tag not emitting until
  the full CLTU is buffered, threshold behavior (errors within/exceeding
  the limit accepted/rejected), a tagged 3-codeword CLTU waiting for its
  tail (`test_017`). `test_021` checks `max_codewords` validation.
  `test_022` runs a real flowgraph — `vector_source_b` with genuine
  `start` stream tags → this block → `message_debug` — through
  `general_work`, with two CLTUs and a spurious tag inside the first one
  that must not produce output.
- `python/soarr/qa_lfsr_receive_chain.py::test_005`/`test_006` — TX
  (`lfsr_scrambler → bch_encoder → cltu_framer`) to RX round trips of
  multi-codeword frames, through `bch_decoder → lfsr_descrambler` and
  through `ccsds_receiver` respectively.
