# lfsr_descrambler

## Purpose

Reverses CCSDS pseudo-randomization on a PDU's payload — XORs it against
the same fixed, deterministic LFSR-generated sequence `lfsr_scrambler`
used to scramble it. The RX-side counterpart to `lfsr_scrambler`. See
[architecture.md](../architecture.md).

## Pipeline position

Internal primitive, GRC-exposed for testing only
([ADR-0005](../adr/0005-rx-path-canonical-block.md)) — not wired into
either signal chain by any `.grc` flowgraph in this repo. Used two ways:

1. **As a real GNU Radio message handler**, registered on its own `in`
   port, reachable if a `.grc` flowgraph wires another block's message
   output to it directly.
2. **As a plain Python helper**, instantiated and called directly (not
   through the message-port graph) by `ccsds_receiver`, the one canonical
   RX path (instantiated in `ccsds_receiver.__init__`), which calls `descramble_msg` once per
   incoming PDU and `reset_sequence()` directly — bypassing
   `descramble_msg` entirely — at four separate points tied to frame
   boundaries, not PDU metadata (see Behavior below for all four). The
   `descramble_msg` call itself (in `ccsds_receiver._readInputMsg`), inside a
   `if DESCRAMBLING_ACTIVE:` guard (a module-level constant, currently
   always `True`):
   ```python
   if DESCRAMBLING_ACTIVE:
       if not self.length_found and self.message_type != MESSAGE_TYPE_FIXED:
           self.lfsr_descrambler.reset_sequence()

       msg = self.lfsr_descrambler.descramble_msg(msg)
       if msg is None:
           ...  # LFSR descrambling failed, ignore message
   ```
   The `msg` passed here is always `bch_decoder.error_correction_mode`'s
   own return value from the line just above it in `ccsds_receiver`
   (`_readInputMsg`) — a real PDU only when BCH correction
   succeeded, never a raw/malformed message — so in this call site,
   `descramble_msg` never receives a malformed PDU either.

Also feeds the separate, internal-testing-only chain documented in
[ADR-0005](../adr/0005-rx-path-canonical-block.md):
`cltu_deframer → bch_decoder → lfsr_descrambler`, exercised by
`python/soarr/qa_lfsr_receive_chain.py`.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. No required metadata keys, but `scramble_reset` or `filled` (either one, if present) trigger a sequence reset via this port's own handler — one of two separate reset paths, see Behavior below. | `pmt.cons({}, u8vector(payload))` |
| `out` | output | PDU: `(metadata_dict . descrambled_payload_u8vector)`. Metadata passed through unchanged; only the payload bytes are transformed, length preserved. | `pmt.cons({}, u8vector(descrambled))` |

`descramble_msg` (the handler registered on `in`) also **returns** the
output PDU on success or `None`/nothing on failure — the return value
`ccsds_receiver`'s direct call (Pipeline position above) relies on. This
return value has no meaning to GNU Radio's own message-passing machinery;
it only matters for the direct-call usage.

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `seed` | int | `0xFF` | Initial 8-bit LFSR register state, matching `lfsr_scrambler`'s own default. Only the low `register_length` bits are used; not range-validated. |
| `register_length` | int | `8` | Validated in `__init__` via `reset_sequence()` (raises `ValueError` if not `8`) — the CCSDS 231.0-B-3 randomizer is only defined for an 8-bit register, matching `lfsr_scrambler`'s identical constraint. `apply_descrambling` re-checks the same condition defensively; since nothing in this repo mutates `register_length` after construction, that second check is unreachable in practice. |

## Behavior / edge cases / current error handling

**Algorithm** — unlike `lfsr_scrambler.apply_scrambling` (which builds
the whole randomizer sequence up front from a fresh `seed` on every
call), this block keeps the LFSR as **running state** across messages
(`self._window`, `self._seq_index`) via `_next_scramble_bit`, only
reseeding when told to (see below). `apply_descrambling` XORs each
payload byte (MSB first) against successive bits pulled from that
running state.

**Sequence reset**: `reset_sequence()` reseeds `self._window` from
`seed` and resets `self._seq_index` to `0`, and is reachable through two
entirely separate paths:

1. **Metadata-key path, inside `descramble_msg`** — triggered by either
   `scramble_reset` (only if its value is truthy; a present `False`
   skips the `filled` check) or `filled` (**on presence alone — its value
   is never inspected**, unlike `scramble_reset`) on an incoming PDU's
   metadata. `cltu_deframer` sets `scramble_reset` on every codeword it
   publishes — `True` on each CLTU's first codeword, `False` on the rest —
   so on the standalone `cltu_deframer → bch_decoder → lfsr_descrambler`
   path the sequence restarts at every CLTU, as CCSDS de-randomization
   requires (`qa_lfsr_receive_chain.py::test_005`). `bch_encoder`'s own
   `filled` key is never wired to this block — see
   [bch_encoder.md](bch_encoder.md)'s Message ports section for that
   key's other, disconnected use.
2. **Direct calls from `ccsds_receiver`, bypassing `descramble_msg`
   entirely** — the block's actual, frequently-exercised reset mechanism
   in the one real caller, tied to frame boundaries rather than PDU
   metadata, at four call sites: before the first codeword of a new
   frame search, when not already accumulating one and not in fixed-
   length mode (`_readInputMsg`, inside the `DESCRAMBLING_ACTIVE`
   guard shown in Pipeline position above); after a fixed-length frame's
   buffer reaches `fixed_byte_length` and is published
   (`_handleMessageTypeFixed`); after a frame-accumulation error aborts
   the current frame, and after a complete TC frame is published (both in
   `receiver`).

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `descramble_msg`
checks `msg` is a pair and its payload is a u8vector before use, both
logged at `warn` (matching this block's membership in the raw-RF `warn`
list); the full body past those two checks — the reset-key checks,
`apply_descrambling`, PDU construction, and the `message_port_pub` call —
is wrapped in catch-log-drop (`except Exception`), also logged at `warn`.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)): a real
class-level summary; full `Args`/`Raises` for `__init__`; full
`Args`/`Publishes`/`Drops when` for `descramble_msg` (a PMT-touching
message handler); `Args`/`Returns`/`Raises` for `apply_descrambling`
(the public algorithm method, matching `lfsr_scrambler`'s sibling
`apply_scrambling` treatment); `Raises` for `reset_sequence`.
`_next_scramble_bit` (a private, non-PMT helper) has none, permitted
as-is by [coding-standards.md](../coding-standards.md)'s exemption for
that category.

**Naming**: file, class, GRC block-id, and every constructor parameter
are already snake_case. `descramble_msg` (the handler) and
`apply_descrambling` (the algorithm) follow the same naming pattern as
`lfsr_scrambler`'s `handle_msg`/`apply_scrambling`, though the exact verb
differs between the two sibling blocks (`handle`/`apply` vs.
`descramble`/`apply`) — not a violation of any documented rule, just
not identical between the pair.

## CCSDS reference

CCSDS 231.0-B-3 (TC Synchronization and Channel Coding) — the same TC
pseudo-randomizer `lfsr_scrambler` applies: generator polynomial
`h(x) = x^8 + x^6 + x^4 + x^3 + x^2 + x + 1`, all-ones (`0xFF`) initial
register state. Because XOR is self-inverse, running the identical
sequence a second time recovers the original data — confirmed directly
against `lfsr_scrambler.md`'s independent-source known-answer vector:
`qa_lfsr_descrambler.py::test_003_known_sequence_descramble` feeds
`lfsr_scrambler`'s own published-standard output
(`0xFF 0x39 0x9E 0x5A 0x68`) back through this block and checks it
recovers the all-zero input, and
`qa_lfsr_round_trip.py`/`qa_lfsr_receive_chain.py` both round-trip
live `lfsr_scrambler` output through this block instead of relying on a
hand-derived vector alone.

## Test coverage

- `python/soarr/qa_lfsr_descrambler.py` — 11 test methods (`test_instance`
  + `test_001`-`test_010`): construction with default and custom
  parameters, an independent-source known-answer descramble (see CCSDS
  reference above), PDU metadata identity preserved, output length
  preserved, zero-length payload, a non-u8vector body dropped cleanly, an
  invalid `register_length` raising at construction, a mock-forced
  publish failure proven to be caught and dropped rather than raised
  through the real handler, and `scramble_reset=True` restarting the
  sequence while `False` keeps it running (`test_010`).
- `python/soarr/qa_lfsr_round_trip.py` — 3 test methods
  (`test_instance` + `test_001`-`test_002`): `lfsr_scrambler` →
  `lfsr_descrambler` round trip recovers the original payload and
  preserves metadata, using a live scrambler instance rather than a fixed
  vector.
- `python/soarr/qa_lfsr_receive_chain.py::test_001_end_to_end_receive`
  and `test_005_multi_codeword_frames_round_trip` — this block as part of
  the full `cltu_deframer → bch_decoder → lfsr_descrambler` chain (see
  [bch_decoder.md](bch_decoder.md)'s Test coverage for this file's other,
  decoder-specific tests); confirm the recovered payload matches the
  original, `test_005` for two consecutive multi-codeword CLTUs, which
  only works because the sequence restarts at each CLTU.
