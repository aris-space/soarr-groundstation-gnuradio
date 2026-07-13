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
   RX path (`ccsds_receiver.py:79,138,140`):
   ```python
   self.lfsr_descrambler = lfsr_descrambler(169, 255, 8)
   ...
   if not self.length_found and self.message_type != MESSAGE_TYPE_FIXED:
       self.lfsr_descrambler.reset_sequence()
   msg = self.lfsr_descrambler.descramble_msg(msg)
   if msg is None:
       ...  # LFSR descrambling failed, ignore message
   ```
   The `msg` passed here is always `bch_decoder.error_correction_mode`'s
   own return value from the line just above it in `ccsds_receiver`
   (`ccsds_receiver.py:129`) — a real PDU only when BCH correction
   succeeded, never a raw/malformed message — so in this call site,
   `descramble_msg` never receives a malformed PDU either.

Also feeds the separate, internal-testing-only chain documented in
[ADR-0005](../adr/0005-rx-path-canonical-block.md):
`cltu_deframer → bch_decoder → lfsr_descrambler`, exercised by
`python/soarr/qa_lfsr_receive_chain.py`.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`. No required metadata keys, but `scramble_reset` or `filled` (either one, if present) trigger a sequence reset — see Behavior below. | `pmt.cons({}, u8vector(payload))` |
| `out` | output | PDU: `(metadata_dict . descrambled_payload_u8vector)`. Metadata passed through unchanged; only the payload bytes are transformed, length preserved. | `pmt.cons({}, u8vector(descrambled))` |

`descramble_msg` (the handler registered on `in`) also **returns** the
output PDU on success or `None`/nothing on failure — the return value
`ccsds_receiver`'s direct call (Pipeline position above) relies on. This
return value has no meaning to GNU Radio's own message-passing machinery;
it only matters for the direct-call usage.

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `mask` | int | `0xA9` | Accepted and stored as `self.mask` — **not validated, and never read anywhere else in the class.** The descrambling recurrence (`_next_scramble_bit`) hardcodes its feedback taps directly (`_window[6]^_window[4]^_window[3]^_window[2]^_window[1]^_window[0]`), the same fixed CCSDS 231.0-B-3 polynomial `lfsr_scrambler.apply_scrambling` hardcodes on the TX side — `mask` plays no role in selecting or altering it. |
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
`seed` and resets `self._seq_index` to `0` — called once at construction,
and again whenever `descramble_msg` sees either metadata key on an
incoming PDU: `scramble_reset` (only if its value is truthy) or `filled`
(**on presence alone — its value is never inspected**, unlike
`scramble_reset`). Neither key is ever published by anything in this
repo (`grep` confirms `scramble_reset` appears only in this file, and
`bch_encoder`'s own `filled` key is never wired to this block — see
[bch_encoder.md](bch_encoder.md)'s Message ports section for that key's
other, disconnected use) — both reset paths are currently unreachable
through any wiring that exists in this repo.

**Error handling**: `lfsr_descrambler` *is* on
[coding-standards.md](../coding-standards.md)'s raw-RF `warn` list, but
its three `self.logger.error(...)` calls (not-a-pair, not-a-u8vector, the
`register_length` `ValueError` from `apply_descrambling`) are all logged
at `error` — the wrong level for this block's pipeline position, per
[ADR-0003](../adr/0003-message-handler-error-policy.md)/
[coding-standards.md](../coding-standards.md).
[ADR-0003](../adr/0003-message-handler-error-policy.md) also requires
every message handler to wrap its **full** body in catch-log-drop;
`descramble_msg` doesn't: only the `apply_descrambling` call is wrapped,
and only `except ValueError`, not `except Exception`. Everything between
the `is_u8vector` check and that `try` — `pmt.dict_has_key`/`dict_ref` on
`meta` (never checked to actually be a dict) and the `reset_sequence()`
calls — runs outside any exception handling.

**Docstrings**: none of ADR-0004's required coverage is present. The
class itself still carries `gr_modtool`'s placeholder
(`"""docstring for block lfsr_descrambler"""`); `__init__`,
`reset_sequence`, `descramble_msg` (a PMT-touching message handler), and
`apply_descrambling` (the public algorithm method — `lfsr_scrambler`'s
sibling `apply_scrambling` has a full docstring) all have no docstring at
all. `_next_scramble_bit` (a private, non-PMT helper) has none either,
permitted as-is by
[coding-standards.md](../coding-standards.md)'s exemption for that
category.

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
`qa_lfsrScramberDescrambler.py`/`qa_lfsr_receive_chain.py` both round-trip
live `lfsr_scrambler` output through this block instead of relying on a
hand-derived vector alone.

## Known issues / TODOs

- **`mask` is accepted, unvalidated, and stored but never used.** See
  Parameters above. `lfsr_scrambler.md` already flags this as "out of
  scope there" and defers it to this block's own PRD; whether it's a
  forward-looking placeholder or simply dead is a design question —
  deliberately not resolved here.

## Test coverage

- `python/soarr/qa_lfsr_descrambler.py` — 9 test methods (`test_instance`
  + `test_001`-`test_008`): construction with default and custom
  parameters, an independent-source known-answer descramble (see CCSDS
  reference above), PDU metadata identity preserved, output length
  preserved, zero-length payload, a non-u8vector body dropped cleanly, and
  an invalid `register_length` raising at construction.
- `python/soarr/qa_lfsrScramberDescrambler.py` — 3 test methods
  (`test_instance` + `test_001`-`test_002`): `lfsr_scrambler` →
  `lfsr_descrambler` round trip recovers the original payload and
  preserves metadata, using a live scrambler instance rather than a fixed
  vector.
- `python/soarr/qa_lfsr_receive_chain.py::test_001_end_to_end_receive` —
  the only test in this repo exercising this block as part of the full
  `cltu_deframer → bch_decoder → lfsr_descrambler` chain (see
  [bch_decoder.md](bch_decoder.md)'s Test coverage for this file's other,
  decoder-specific tests); confirms the final recovered payload matches
  the original after passing through this block.
