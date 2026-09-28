# cltu_framer

## Purpose

Frames one transfer frame's BCH codewords into a single CLTU (Command
Link Transmission Unit) per CCSDS 231.0-B-4: prepends a 2-byte start
sequence and appends an 8-byte tail sequence, both runtime-configurable. The last
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
| `in` | input | PDU: `(metadata_dict . codewords_u8vector)` — all codewords of one frame, as `bch_encoder` publishes them. Payload must be a non-zero multiple of 8 bytes — anything else (including empty) is rejected. | `pmt.cons({}, u8vector(N × 8 bytes))` |
| `out` | output | PDU: `(metadata_dict . cltu_u8vector)`, one CLTU: `start_sequence (2 bytes) + codewords (N × 8 bytes) + tail_sequence (8 bytes)`. Metadata unchanged. | `pmt.cons({}, u8vector(2 + N × 8 + 8 bytes))` |

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

**Payload size**: a non-zero multiple of 8 bytes (`CODEWORD_SIZE_BYTES`)
required — one or more whole codewords; anything else, including an
empty payload, is rejected via one unified check (logged, no publish —
`qa_cltu_framer.py::test_002`, `test_003`, `test_012`, `test_013`,
`test_019`).

**One CLTU per frame**: every codeword of the input PDU goes between a
single start/tail pair, so a multi-codeword transfer frame becomes one
CLTU (`START cw1 cw2 ... TAIL`), as the standard requires. This relies
on `bch_encoder` publishing all of a frame's codewords as one PDU.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `add_sequences`'s
full body — the pair/u8vector shape checks, extracting the payload,
validating its size is a whole number of codewords, packing both
sequences, building the CLTU, and the `message_port_pub` call — is
wrapped in catch-log-drop (`except Exception`), logged at `error` (this
TX-side block isn't in the raw-RF `warn` list).

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

None currently.

## Test coverage

- `python/soarr/qa_cltu_framer.py` — 20 test methods (`test_instance` +
  `test_001`–`test_019`): construction with custom start/tail sequences,
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
  of raising `struct.error` through the real handler (`test_017`), a
  3-codeword payload framed as one CLTU (`test_018`), and payloads that
  aren't a whole number of codewords rejected (`test_019`).
- `python/soarr/qa_lfsr_cltu_bch_chain.py` — the real
  `lfsr_scrambler → bch_encoder → cltu_framer` chain producing exactly
  one CLTU per input frame for 1, 2, and 3 codewords, byte-identical to
  a stagewise reference built per CCSDS 231.0-B-4 Figure 5-1.
- `python/soarr/qa_layoutTest.py::test_011_cltu_framer_real_handler` —
  same pattern as the other TX blocks' "real handler" tests: builds a
  fresh, standalone instance and calls `add_sequences` directly, not the
  `msg_connect` wiring itself (shimmed out in
  `test_002_end_to_end_message_routing` via `_bind_passthrough`).
