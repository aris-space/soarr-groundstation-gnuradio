# system_tester

## Purpose

Round-trip verification for a loopback test: tracks a packet's original,
transmitted, and received payloads by a common tracking key, computes
bit error rate, message error rate, and lost-packet rate once a packet
completes, and can drive the test by emitting `trigger` messages on
request. See [architecture.md](../architecture.md).

## Pipeline position

Ground-tooling, not signal chain
([architecture.md](../architecture.md)'s own framing): "compares
original vs. received payloads out-of-band, at the end of the pipeline;
it does not sit inline in either chain and has no per-block error-signal
port to depend on." No `.grc` flowgraph file exists anywhere in this
repo, and no wiring for this block appears in `qa_layoutTest.py` either
— its real, intended wiring (what feeds `original`/`transmitted`/
`received`, and what consumes `trigger`) isn't established anywhere in
this repo.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `start` | input | Any PMT — content is ignored; receipt alone triggers a new tracked packet (subject to `repetitions`/`mode` gating). | `pmt.PMT_NIL` |
| `original` | input | PDU: `(metadata_dict . payload_u8vector)`. Stored as a packet's reference payload if a `vcid_counter` can be extracted (see Behavior); dropped otherwise. | `pmt.cons({telecommand: {tc_header: {vcid_counter: 3}}}, u8vector(payload))` |
| `transmitted` | input | Same PDU shape as `original`; stored as an alternate reference payload (used only if `original` was never recorded for that packet — see Behavior). | same shape as `original` |
| `received` | input | Same PDU shape as `original`; on receipt, finalizes and scores that packet's tracking entry against whichever reference payload is available. | same shape as `original` |
| `trigger` | output | PDU: `(metadata_dict . PMT_NIL)`, metadata `{packet_id: int, mode: int}`, no payload. | `pmt.cons({packet_id: 0, mode: 0}, pmt.PMT_NIL)` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `repetitions` | int | `100` | Caps how many packets `handle_start` will begin tracking; `<= 0` means no cap. Not validated for type (`int(repetitions)` coerces silently). |
| `mode` | int | `0` | `0`=`WAITING_TRIGGER_MODE` (a new `start` is ignored, incrementing `ignored_starts`, while any tracked packet is still incomplete), `1`=`AUTOMATIC_MODE` (no such gating — `start` always begins a new tracked packet, allowing overlap). Validated in `__init__` (raises `ValueError` if not `0` or `1`). |
| `timeout_s` | float | `1.0` | How long a tracked packet waits for `received` before `_timeout_packet` marks it lost. `<= 0` disables the timer entirely (`_start_timeout_timer` returns `None` without starting one — a packet that never gets a `received` message then simply never completes or counts as lost). |
| `stats_path` | str \| `None` | `"system_tester_stats.json"` | Where `_save_stats` writes a JSON stats snapshot after every packet completes or times out — a relative path by default, resolved against the process's current working directory, not this file's location. `None` (or empty) disables writing entirely (every test in this repo passes `stats_path=None` to avoid the side effect). |

## Behavior / edge cases / current error handling

**Tracking key**: every packet is tracked in `self._entries` keyed by an
integer `packet_id`. For `original`/`transmitted`/`received`, this key
comes from `_extract_vcid_counter`, which walks
`meta["telecommand"]["tc_header"]["vcid_counter"]` and returns `None` if
any level of that path is missing or the final value isn't an integer
PMT. For `start`, no such metadata exists yet (a `start` message carries
no payload to extract it from), so `handle_start` instead uses
`fallback_id = len(self._entries)` — a sequential counter, not tied to
`vcid_counter` at all.

**`handle_original` and `handle_transmitted` are near-identical**:
extract the payload and `vcid_counter`; if either extraction fails,
log and drop the message entirely (no entry created or updated); on
success, `_get_or_create_entry(vcid_counter, source)` and store the
payload under `entry["original"]`/`entry["transmitted"]` respectively.
`handle_received` follows the same extraction pattern, then additionally
calls `_finalize_entry`, which picks `entry["original"]` if present,
else `entry["transmitted"]`, as the reference payload; if **neither** is
set, the packet counts as lost.

**A `vcid_counter`-less `original`/`transmitted`/`received` message is
dropped with no fallback tracking, despite `handle_start`'s own comment
implying one should exist.** `handle_start`'s docstring reads: "the
stream parsing ports will automatically track using vcid_counter if
it's extracted there instead" — worded as if `vcid_counter` extraction
were the *preferred* path with a fallback behind it, not the *only*
path. `self._pending_order` (a `deque`) is appended to inside
`_get_or_create_entry` every time a new entry is created, but is never
read, iterated, or popped anywhere else in the class — the only place
this state could plausibly be used (an insertion-order fallback for
packets a caller couldn't tag with a real `vcid_counter`) doesn't exist.
Confirmed directly: feeding `handle_original`/`handle_received` a PDU
with an empty metadata dict (no `telecommand` key at all) causes
`_extract_vcid_counter` to return `None` — `pmt.dict_ref` on a missing
key returns `pmt.PMT_NIL`, and `pmt.is_dict(PMT_NIL)` is `True` in this
PMT library (an empty dict and `PMT_NIL` are the same value), so the
intermediate `is_dict` checks on the missing `telecommand`/`tc_header`
levels don't short-circuit early — the function only actually returns
`None` at the final `pmt.is_integer(vcid_counter_pmt)` check, since
`PMT_NIL` isn't an integer PMT. Either way, the message is dropped with
only a `warn` log, and `self._stats["generated_packets"]`/
`["received_packets"]` never increment for it.

**This is the confirmed root cause of this repo's two remaining
baseline `pytest` failures**
(`qa_system_tester.py::test_001_waiting_mode_triggers_and_matches_payload`,
`::test_002_payload_difference_counts_as_message_and_bit_error`): both
construct `original`/`received` PDUs via a bare
`pmt.cons(pmt.make_dict(), u8vector(payload))` — no `telecommand`
metadata at all — so every call into `handle_original`/`handle_received`
is silently dropped, and `stats["received_packets"]` stays `0` instead
of the `1` both tests expect.

**`handle_start`'s repetition/mode gating**: a new `start` is ignored
outright (no trigger emitted, no entry created) if `repetitions > 0` and
the cap is already reached, or if `mode == WAITING_TRIGGER_MODE` and any
existing entry is still incomplete (counted in `ignored_starts`). Both
checks happen before a `fallback_id`-keyed entry is created, so a
packet's `packet_id` sequence can have gaps relative to how many `start`
messages were actually sent.

**Scoring** (`_finalize_entry`): if the reference and received payloads
differ in length or content, `message_errors` increments once for the
whole packet; `_bit_errors` separately counts every differing bit
across the shared length, plus every set bit in whichever payload is
longer past that point (treating a length mismatch as if the extra
bytes were compared against all-zero). `compared_bits` accumulates
`max(len(reference), len(received)) * 8` per packet, the denominator
`bit_error_rate` is later computed against.

**Concurrency**: `self._lock` (`threading.RLock`) guards every read/write
of `_entries`/`_pending_order`/`_stats`, since GNU Radio can invoke this
block's four message handlers from different scheduler threads
concurrently — a real, deliberate safety measure, not incidental.

**Error handling**: none of the four message handlers
(`handle_start`, `handle_original`, `handle_transmitted`,
`handle_received`) wrap their body in catch-log-drop — this block is
not on [coding-standards.md](../coding-standards.md)'s raw-RF `warn`
list, so [ADR-0003](../adr/0003-message-handler-error-policy.md)'s
policy applies in full, same as `data_creator`'s already-fixed
handlers. A concrete, reachable failure exists in this gap:
`_save_stats` (`json.dump` to a file, `os.makedirs` on its directory)
is real file I/O, called from `_update_stats`, called from
`_finalize_entry` (inside `handle_received`) and `_timeout_packet` (a
background `threading.Timer` callback, not even on the message-handler
thread) — an `OSError` there (unwritable path, full disk) would
propagate uncaught. Five `self.logger.warn(...)` calls exist across
`handle_original`/`handle_transmitted`/`handle_received` (missing
`vcid_counter`) and `_finalize_entry`/`_timeout_packet` (lost packet);
three `self.logger.error(...)` calls exist in the same three handlers
(missing payload). Since this block isn't on the raw-RF `warn` list, all
five `warn` calls are at the wrong level — the same `warn`→`error`
direction `data_creator`'s own fix already established for the other
ground-tooling block.

**Docstrings**: the class itself already has a real summary, not a
`gr_modtool` placeholder. `_extract_vcid_counter` has a prose
docstring describing the metadata path, and `_get_or_create_entry` has
a one-line summary — neither in ADR-0004's `Args`/`Returns` format, but
both convey the same information. `__init__`, all four message
handlers, and every other private helper (`_extract_payload`,
`_payload_to_pmt`, `_make_trigger_msg`, `_get_stats_snapshot`,
`get_stats`, `_save_stats`, `_print_stats`, `_update_stats`,
`_start_timeout_timer`, `_bit_errors`, `_finalize_entry`,
`_timeout_packet`) have no docstring at all.

**Naming**: file, class, every constructor parameter, and every method
name are already snake_case. The GRC yaml's own `label:` field still
reads `systemTester` (camelCase) — every other block's `label:` in this
repo uses a human-readable Title Case string (e.g. "CCSDS Reader",
"BCH Decoder"); this is the one block where that convention wasn't
applied. `category: '[soarr]'` has no subcategory, unlike most other
blocks (`'[soarr]/SDLS'`, `'[soarr]/Reception'`, etc.) — consistent with
this being ground-tooling rather than a protocol-layer block, not
obviously wrong.

## CCSDS reference

None — this block is test/ground tooling, not a CCSDS-defined layer.
`vcid_counter` is a metadata field other blocks populate (ultimately
sourced from the TFPH's Frame Sequence Number, per
[ccsds_reader.md](ccsds_reader.md)), used here only as an opaque
tracking key.

## Known issues / TODOs

- **`original`/`transmitted`/`received` messages without an extractable
  `vcid_counter` are silently dropped, with no fallback tracking
  mechanism** — the confirmed root cause of this repo's last two
  baseline `pytest` failures (see Behavior above). `_pending_order`
  exists but is never read, suggesting a FIFO/insertion-order fallback
  was intended but never implemented. Whether the right fix is
  implementing that fallback, or correcting the two failing tests to
  construct realistic metadata (matching the class's own top-level
  docstring, which frames `vcid_counter` as *the* synchronization
  mechanism, not one of several) is a genuine design question —
  deliberately not resolved here.
- **No message handler wraps its body in catch-log-drop**, with a
  concrete reachable failure mode (`_save_stats`'s file I/O) that would
  currently crash a handler thread.
- **Five `warn`-level log calls should be `error`**, matching
  `data_creator`'s own already-fixed precedent for this repo's other
  ground-tooling block.
- **`qa_system_tester.py::test_001_descriptive_test_name`** — a test
  whose own name states it isn't descriptive, duplicating the
  `test_001` prefix already used by
  `test_001_waiting_mode_triggers_and_matches_payload` in the same
  file (Python's `unittest` permits this since the full method names
  differ, but it reads as a mistake). Its body is `self.tb.run()` on an
  empty `top_block` — it doesn't exercise `system_tester` at all.

## Test coverage

- `python/soarr/qa_system_tester.py` — 5 test methods (`test_instance`,
  `test_001_waiting_mode_triggers_and_matches_payload`,
  `test_002_payload_difference_counts_as_message_and_bit_error`,
  `test_003_timeout_marks_packet_lost`, plus the vestigial
  `test_001_descriptive_test_name` noted above): construction and
  default parameter values (`test_instance`); a `start`→`original`→
  `received` flow expected to report zero errors for matching payloads
  (`test_001`, currently failing — see Known issues); a `start`→
  `original`→`received` flow with a deliberately different payload
  expected to report one message error and a nonzero bit-error count
  (`test_002`, currently failing, same root cause); and a `start`
  followed by a direct `_timeout_packet` call (bypassing `received`
  entirely, so it doesn't hit the broken `vcid_counter` path) confirmed
  to mark the packet lost (`test_003`, passing). No test exercises
  `handle_transmitted`, `AUTOMATIC_MODE`, the `repetitions` cap, a
  forced exception past any handler's existing checks, or `stats_path`
  actually writing a file.
