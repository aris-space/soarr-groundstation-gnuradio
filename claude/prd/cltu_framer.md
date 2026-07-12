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
| `startSequence` | int | `0xEB90` | Packed as big-endian 2 bytes (`struct.pack('!H', ...)`). Not validated anywhere. Re-packed fresh on every message (not cached at construction), which is what makes runtime mutation take effect immediately — see Behavior. |
| `tailSequence` | int | `0xC5C5C5C5C5C5C579` | Packed as big-endian 8 bytes (`struct.pack('!Q', ...)`). Same lack of validation and re-packing behavior as `startSequence`. |

Both are plain public attributes, not constructor-only values: directly
reassigning `dut.startSequence = ...` / `dut.tailSequence = ...` between
messages is a real, tested pattern
(`qa_cltu_framer.py::test_006_runtime_sequence_modification`), not an
incidental side effect of Python's attribute model.

## Behavior / edge cases / current error handling

**Algorithm** — on every message, `startSequence`/`tailSequence` are
packed fresh (not cached), then concatenated as
`start ‖ payload ‖ tail` and published with the metadata dict unchanged.

**Payload size**: exactly 8 bytes required; anything else, including an
empty payload, is rejected (logged, no publish —
`qa_cltu_framer.py::test_002`, `test_003`, `test_012`, `test_013`).

**`filled` metadata**: if present in the input metadata (set by
`bch_encoder` only on a message's last codeword — see
[bch_encoder.md](bch_encoder.md)), triggers one extra `info`-level log
line (`"OK\n"`) after the normal `debug`-level `"OK"` — a cosmetic
end-of-message marker with no other effect, same as documented for
`bch_encoder`.

**Error handling** (intended to comply with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md), but currently
does not — see Known issues):

- No `pmt.is_pair(msg)` check exists before `pmt.car(msg)`/`pmt.cdr(msg)`.
- No `try/except` exists anywhere in the method — nothing catches a
  `struct.pack` failure or any other internal error.

**Docstrings**: none anywhere — the class docstring is still
`gr_modtool`'s unfilled placeholder (`"""docstring for block
cltu_framer"""`), unlike `bch_encoder`/`lfsr_scrambler`, which already had
real class-docstring content before their own passes.

**Naming**: `startSequence`, `tailSequence` (constructor parameters and
attributes) and the handler method `addSequences` are camelCase —
inconsistent with every already-fixed sibling block in this pass, where
constructor parameters (`scid`, `register_length`, `polynomial`) and
handler names (`build_header`, `handle_msg`, `encode_bch`) are snake_case.
The block/file/class/GRC-id are already snake_case (`cltu_framer`); only
these method/parameter names are not.

**Comments partially in German**: two of the method's four inline
comments are in German (`# Konstanten laut CCSDS 231.0-B-4`, `# Die Tail
Sequence ist das "End-of-Transmission" Muster`) — every other file
reviewed in this pass is English-only.

## CCSDS reference

CCSDS 231.0-B-4 (TC Synchronization and Channel Coding), verified
directly against the primary standard document (not just the code's own
comment citation, unlike the other CCSDS references in this pass):

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
  one.** This is the most significant finding in this pass so far —
  confirmed directly against the primary CCSDS 231.0-B-4 standard
  document (see CCSDS reference above), not just inferred. The standard
  defines one CLTU as *one* start sequence, *all* of a transfer frame's
  BCH codewords concatenated, and *one* tail sequence. `bch_encoder`
  publishes one PDU per 56-bit
  codeword (`bch_encoder.md`'s Message ports section) — any payload
  longer than 7 bytes (essentially guaranteed once headers are included)
  produces more than one codeword PDU. `cltu_framer` has no buffering: it
  treats every incoming 8-byte PDU as a complete, independent frame and
  wraps *each one* in its own start/tail sequence. Downstream,
  `acquisition_idle_sequencer.py`'s `work()` (lines 75-116) queues and
  streams each PDU it receives back-to-back with no gap or separator
  (confirmed by reading its `_pdu_queue`/`_start_burst` logic) — so the
  actual RF byte stream for a multi-codeword message becomes `START
  codeword1 TAIL START codeword2 TAIL ...` instead of the
  CCSDS-compliant `START codeword1 codeword2 ... TAIL`. No test in this
  repo exercises a multi-codeword message through `cltu_framer` (every
  `qa_cltu_framer.py` test sends one independent 8-byte PDU per call;
  `test_007_multiple_consecutive_messages` proves 5 independent calls
  behave independently, not that one logical message spanning multiple
  codewords is combined into one CLTU). Fixing this means `cltu_framer`
  buffering codewords across messages until it sees `filled` in the
  metadata (the same signal `bch_encoder` already sets on the last
  codeword) and emitting one combined PDU — a real behavioral/contract
  change (1-in-1-out becomes N-in-1-out), not a small fix, and not yet
  applied.
- **`addSequences` crashes on a non-pair input.** `pmt.car`/`pmt.cdr` are
  called unconditionally before any shape check; reproduced directly:
  `addSequences(pmt.intern("not-a-pair"))` raises `ValueError: pmt_car:
  wrong_type not-a-pair`.
- **A bad runtime-reassigned sequence value crashes the next message.**
  Neither `startSequence` nor `tailSequence` is validated anywhere
  (construction or message time), and both are plain, directly-mutable
  public attributes (see Parameters). Reproduced directly: constructing
  with `startSequence=0x10000` succeeds silently (no validation at
  construction either); calling `addSequences` afterward raises
  `struct.error: 'H' format requires 0 <= number <= 65535`, uncaught.
  Because runtime mutation bypasses any constructor-time check entirely,
  constructor validation alone (the pattern used in every other block
  fixed so far) would not be sufficient here — the handler itself needs
  its own catch-log-drop to be the actual safety net.
- **No `try/except` anywhere in the handler** — the two crash paths above
  are two symptoms of this single, complete gap (not partial coverage
  like other reviewed blocks).
- **No docstrings anywhere** — the class docstring is still
  `gr_modtool`'s placeholder; `__init__` and `addSequences` have none.
- **camelCase naming** — `startSequence`, `tailSequence`, `addSequences`
  don't match every other already-fixed sibling block's snake_case
  parameters/attributes/handler names.
- **Two inline comments are in German**, inconsistent with the rest of
  the repo.

## Test coverage

- `python/soarr/qa_cltu_framer.py` — 15 test methods (`test_instance` +
  `test_001`–`test_014`): construction with custom start/tail sequences,
  correct framing (start ‖ payload ‖ tail) with arbitrary/all-zero/
  all-`0xFF` payloads, an empty payload rejected, a wrong-size payload
  (3 bytes) rejected, a non-u8vector body rejected, custom sequences,
  runtime sequence reassignment taking effect on the *next* message (not
  the current one — proven by asserting the first message still used the
  original sequences), 5 independent consecutive messages framed
  correctly and independently, PDU metadata (empty and rich) preserved
  exactly, the 7-byte and 9-byte payload-size boundaries both rejected,
  and default sequence values used when omitted. No test covers a payload
  spanning multiple BCH codewords (see Known issues above — this repo has
  no test proving or disproving multi-codeword CLTU framing either way).
- `python/soarr/qa_layoutTest.py::test_011_cltu_framer_real_handler` —
  same pattern as the other TX blocks' "real handler" tests: builds a
  fresh, standalone instance and calls `addSequences` directly, not the
  `msg_connect` wiring itself (shimmed out in
  `test_002_end_to_end_message_routing` via `_bind_passthrough`).
