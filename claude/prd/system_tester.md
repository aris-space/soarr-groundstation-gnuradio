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
repo. `python/soarr/qa_layoutTest.py`'s `layout` fixture instantiates it
alongside the real TX chain, but not `msg_connect`-wired into that
chain's topology — `test_012_system_tester_tracks_real_tx_chain_output`
feeds it directly: `handle_original` with the raw pre-chain payload,
then `handle_received` with `cltu_framer`'s real captured output
(concatenated across every CLTU chunk `bch_encoder`'s 7-byte block
splitting produces, since `system_tester` tracks one payload per
packet). There is no RX chain anywhere in this repo, so this doesn't
establish a real receiving wiring for `original`/`transmitted`/
`received`/`trigger` — only that the tracking machinery itself
integrates with the real TX chain's output shape.

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
`vcid_counter` at all. `vcid_counter` is the sole synchronization
mechanism for `original`/`transmitted`/`received`; there is no
insertion-order or other fallback tracking for a message that lacks one
— such a message is dropped.

**`handle_original` and `handle_transmitted` are near-identical**:
extract the payload and `vcid_counter`; if either extraction fails,
log and drop the message entirely (no entry created or updated); on
success, `_get_or_create_entry(vcid_counter, source)` and store the
payload under `entry["original"]`/`entry["transmitted"]` respectively.
`handle_received` follows the same extraction pattern, then additionally
calls `_finalize_entry`, which picks `entry["original"]` if present,
else `entry["transmitted"]`, as the reference payload; if **neither** is
set, the packet counts as lost.

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
of `_entries`/`_stats`, since GNU Radio can invoke this block's four
message handlers from different scheduler threads concurrently — a
real, deliberate safety measure, not incidental.

**Error handling**: all four message handlers (`handle_start`,
`handle_original`, `handle_transmitted`, `handle_received`) wrap their
full body in catch-log-drop, matching
[ADR-0003](../adr/0003-message-handler-error-policy.md)'s policy — this
block is not on [coding-standards.md](../coding-standards.md)'s raw-RF
`warn` list, so every log call at a handler's failure point is at
`error`, matching `data_creator`'s own equivalent log calls.

**Docstrings**: the class docstring, `__init__`, all four message
handlers, and every private helper that directly touches a PMT/PDU
(`_extract_payload`, `_extract_vcid_counter`, `_payload_to_pmt`,
`_make_trigger_msg`) have full `Args`/`Returns`/`Raises` or
`Args`/`Publishes`/`Drops when` docstrings per
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md).
`_get_or_create_entry` keeps a one-line summary (it doesn't touch PMT
directly). `_get_stats_snapshot`, `get_stats`, `_save_stats`,
`_print_stats`, `_update_stats`, `_start_timeout_timer`, `_bit_errors`,
`_finalize_entry`, and `_timeout_packet` have no docstring — none of
them touch PMT directly, so ADR-0004 doesn't require one.

**Naming**: file, class, every constructor parameter, every method
name, and the GRC yaml's `label:` field ("System Tester") are all
snake_case/Title Case, matching every other block's convention.
`category: '[soarr]'` has no subcategory, unlike most other blocks
(`'[soarr]/SDLS'`, `'[soarr]/Reception'`, etc.) — consistent with this
being ground-tooling rather than a protocol-layer block, not obviously
wrong.

## CCSDS reference

None — this block is test/ground tooling, not a CCSDS-defined layer.
`vcid_counter` is a metadata field other blocks populate (ultimately
sourced from the TFPH's Frame Sequence Number, per
[ccsds_reader.md](ccsds_reader.md)), used here only as an opaque
tracking key.

## Known issues / TODOs

- No test exercises `handle_transmitted`, `AUTOMATIC_MODE`, the
  `repetitions` cap, or `stats_path` actually writing a file.

## Test coverage

- `python/soarr/qa_system_tester.py` — 6 test methods: construction and
  default parameter values (`test_instance`); a `start`→`original`→
  `received` flow with matching payloads, each PDU carrying a
  `telecommand.tc_header.vcid_counter` metadata path, expected to
  report zero errors (`test_001_waiting_mode_triggers_and_matches_payload`);
  the same flow with a deliberately different payload, expected to
  report one message error and a nonzero bit-error count
  (`test_002_payload_difference_counts_as_message_and_bit_error`); a
  `start` followed by a direct `_timeout_packet` call (bypassing
  `received` entirely) confirmed to mark the packet lost
  (`test_003_timeout_marks_packet_lost`); `_save_stats` raising
  `OSError` during `handle_received` confirmed not to propagate past
  the handler (`test_004_save_stats_failure_does_not_crash_handle_received`);
  and `message_port_pub` raising `RuntimeError` during `handle_start`
  confirmed not to propagate past the handler
  (`test_005_trigger_publish_failure_does_not_crash_handle_start`).
- `python/soarr/qa_layoutTest.py::test_012_system_tester_tracks_real_tx_chain_output`
  — `handle_original`/`handle_received` called directly against the real
  TX chain's own output (not passthrough shims), confirming a packet
  reaches `received_packets` with `lost_packets` staying `0` (see
  Pipeline position above). The test computes the exact number of
  `cltu_framer` chunks the fixed payload/config should produce and
  asserts the capture matches it exactly, so an async wait that
  returned early fails loudly instead of silently scoring a partial
  capture as a complete packet.
