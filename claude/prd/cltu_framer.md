# cltu_framer

## Purpose

Frames a single 8-byte BCH codeword PDU into a CLTU (Command Link
Transmission Unit) per CCSDS 231.0-B-4: prepends a 2-byte start sequence
and appends an 8-byte tail sequence, both runtime-configurable. The last
framing step before the byte stream reaches the modulator. See
[architecture.md](../architecture.md).

## Pipeline position

TX chain, between `bch_encoder` and `acquisition_idle_sequencer`:

```
bch_encoder.codewords → cltu_framer.in
cltu_framer.out → acquisition_idle_sequencer.in
```

The input side is confirmed via `python/soarr/qa_layoutTest.py:105`'s
`msg_connect` wiring. The output side is documented in
[architecture.md](../architecture.md) but not independently wired or
tested anywhere in this repo — `qa_layoutTest.py` captures `cltu_framer`'s
`out` port directly instead of connecting it onward.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. Payload must be exactly 8 bytes — anything else (including empty) is rejected. | `pmt.cons({}, u8vector(8 bytes))` |
| `out` | output | PDU: `(metadata_dict . cltu_u8vector)`, always `start_sequence (2 bytes) + payload (8 bytes) + tail_sequence (8 bytes)` = 18 bytes total. Metadata unchanged. | `pmt.cons({}, u8vector(18 bytes))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `start_sequence` | int | `0xEB90` | Packed as big-endian 2 bytes (`struct.pack('!H', ...)`). Validated in `__init__` (raises `ValueError` if outside `0x0000`-`0xFFFF`). Re-packed fresh on every message (not cached at construction), which is what makes runtime mutation take effect immediately — see Behavior. |
| `tail_sequence` | int | `0xC5C5C5C5C5C5C579` | Packed as big-endian 8 bytes (`struct.pack('!Q', ...)`). Validated in `__init__` (raises `ValueError` if outside `0x0`-`0xFFFFFFFFFFFFFFFF`). Same re-packing behavior as `start_sequence`. |

Both are plain public attributes, not constructor-only values: directly
reassigning `dut.start_sequence = ...` / `dut.tail_sequence = ...` between
messages is a real, tested pattern
(`qa_cltu_framer.py::test_006_runtime_sequence_modification`), not an
incidental side effect of Python's attribute model. Because this
bypasses `__init__`'s validation entirely, the handler's own
catch-log-drop (see Error handling below) is what actually protects
against a bad runtime-reassigned value, not the constructor check alone.

## Behavior / edge cases / current error handling

**Algorithm** — on every message, `start_sequence`/`tail_sequence` are
packed fresh (not cached), then concatenated as
`start ‖ payload ‖ tail` and published with the metadata dict unchanged.

**Payload size**: exactly 8 bytes required (named constant
`PAYLOAD_SIZE_BYTES`); anything else, including an empty payload, is
rejected via one unified check (logged, no publish —
`qa_cltu_framer.py::test_002`, `test_003`, `test_012`, `test_013`).

**`filled` metadata**: if present in the input metadata (set by
`bch_encoder` only on a message's last codeword — see
[bch_encoder.md](bch_encoder.md)), triggers one extra `info`-level log
line (`"OK\n"`) after the normal `debug`-level `"OK"` — a cosmetic
end-of-message marker with no other effect, same as documented for
`bch_encoder`.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `add_sequences`
checks `msg` is a pair and its payload is a u8vector before use; the
full body past those two shape checks —
extracting the payload, validating its size is exactly
`PAYLOAD_SIZE_BYTES`, packing both sequences, building the CLTU, and the
`message_port_pub` call — is wrapped in catch-log-drop (`except
Exception`), logged at `error` (this TX-side block isn't in the raw-RF
`warn` list).

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): full
`Args`/`Raises` for `__init__`, `Args`/`Publishes`/`Drops when` for
`add_sequences`.

**Naming**: `start_sequence`, `tail_sequence` (constructor parameters and
attributes) and the handler method `add_sequences` match every other
already-fixed sibling block's snake_case convention, including
`grc/soarr_cltu_framer.block.yml`'s parameter ids and `make:` template.

**Comments**: all inline comments are in English.

## CCSDS reference

CCSDS 231.0-B-4 (TC Synchronization and Channel Coding), verified
directly against the primary standard document:

- **Definition** (§1, Terms and Definitions): a CLTU is "a Synchronization
  and Channel Coding Sublayer data entity which is used to synchronize
  and delimit the beginning of a continuum of bits consisting of a Start
  Sequence followed by an **integral number of codewords** and a Tail
  Sequence" — one Start/Tail pair per CLTU, regardless of codeword count.
- **Figure 5-1** (§5.2.1, "Components of the CLTU when BCH Coding Is
  Used"): `START SEQUENCE (16 bits) | N × BCH CODEWORD (64 bits each) |
  TAIL SEQUENCE (64 bits)` — a single structure, one start, one tail, N
  codewords between them.
- **Exact values** (§5.2.2.2, §5.2.4.1) match this block's defaults
  exactly: Start Sequence `0xEB90` (16 bits), Tail Sequence
  `0xC5C5C5C5C5C5C579` (64 bits — 0xC5 repeated seven times plus a final
  `0x79`).
- **Per-frame(s) generation, not per-codeword** (§3.4.1, Figure 2-2,
  Annex A4.2): fill bits are appended once, to the *last* codeword of the
  whole CLTU (§3.4.1) — not per codeword. The generation pipeline (Figure
  2-2) groups a full set of codewords into a CLTU. Annex A4.2 discusses a
  single CLTU spanning *multiple* transfer frames, which is only possible
  if one Start/Tail pair can bound many codewords at once.

## Known issues / TODOs

- **Multi-codeword messages are framed as multiple separate CLTUs, not
  one.** Confirmed directly against the primary CCSDS 231.0-B-4 standard
  document (see CCSDS reference above). The standard defines one CLTU as
  *one* start sequence, *all* of a transfer frame's BCH codewords
  concatenated, and *one* tail sequence. `bch_encoder` publishes one PDU
  per 56-bit codeword it splits a payload into — each such PDU is itself
  8 bytes/64 bits (7 info + 1 parity/filler byte), and any payload longer
  than 7 bytes (essentially guaranteed once headers are included)
  produces more than one codeword PDU (`bch_encoder.md`'s Message ports
  section). `cltu_framer` has no buffering: it treats every incoming
  8-byte PDU as a complete, independent frame and wraps *each one* in its
  own start/tail sequence. Downstream, `acquisition_idle_sequencer.py`'s
  `work()` (lines 75-116) queues and streams each PDU it receives
  back-to-back with no gap or separator (confirmed by reading its
  `_pdu_queue`/`_start_burst` logic) — so the actual RF byte stream for a
  multi-codeword message becomes `START codeword1 TAIL START codeword2
  TAIL ...` instead of the CCSDS-compliant `START codeword1 codeword2 ...
  TAIL`. No test in this repo exercises a multi-codeword message through
  `cltu_framer` (every `qa_cltu_framer.py` test sends one independent
  8-byte PDU per call; `test_007_multiple_consecutive_messages` proves 5
  independent calls behave independently, not that one logical message
  spanning multiple codewords is combined into one CLTU). Fixing this
  means `cltu_framer` buffering codewords across messages until it sees
  `filled` in the metadata (the same signal `bch_encoder` already sets on
  the last codeword) and emitting one combined PDU — a real
  behavioral/contract change (1-in-1-out becomes N-in-1-out), deliberately
  left as follow-up work rather than folded into this block's other
  fixes.

## Test coverage

- `python/soarr/qa_cltu_framer.py` — 18 test methods (`test_instance` +
  `test_001`–`test_017`): construction with custom start/tail sequences,
  correct framing (start ‖ payload ‖ tail) with arbitrary/all-zero/
  all-`0xFF` payloads, an empty payload rejected, a wrong-size payload
  (3 bytes) rejected, a non-u8vector body rejected, custom sequences,
  runtime sequence reassignment taking effect on the *next* message (not
  the current one — proven by asserting the first message still used the
  original sequences), 5 independent consecutive messages framed
  correctly and independently, PDU metadata (empty and rich) preserved
  exactly, the 7-byte and 9-byte payload-size boundaries both rejected,
  default sequence values used when omitted, invalid `start_sequence`/
  `tail_sequence` raising at construction (`test_015`), a non-pair input
  dropped cleanly instead of crashing the handler (`test_016`), and a
  runtime-reassigned out-of-range sequence value dropped cleanly instead
  of raising `struct.error` through the real handler (`test_017`). No
  test covers a payload spanning multiple BCH codewords (see Known
  issues above — this repo has no test proving or disproving
  multi-codeword CLTU framing either way).
- `python/soarr/qa_layoutTest.py::test_011_cltu_framer_real_handler` —
  same pattern as the other TX blocks' "real handler" tests: builds a
  fresh, standalone instance and calls `add_sequences` directly, not the
  `msg_connect` wiring itself (shimmed out in
  `test_002_end_to_end_message_routing` via `_bind_passthrough`).
