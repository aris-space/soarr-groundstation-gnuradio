# To-do

Outstanding, currently-postponed items — kept short; each points to where
the detail actually lives instead of duplicating it here. Not a status
log or a session snapshot (see git history for that).

## Repo-level

- **Test discovery conflict**: `.vscode/settings.json` configures
  `unittest` discovery while `pytest.ini` and the documented workflow use
  `pytest` — see [development.md](development.md) for the existing note.
  Needs reconciling, not fixed.

## Known code gaps (audited 2026-07-14, not yet fixed)

- **ADR-0003 handler-wrap gap in 8 blocks**: a shape-check guard
  (`pmt.is_pair`/`is_u8vector`/`is_dict`) runs before the handler's
  `try:` block instead of inside it, so an exception from one of those
  calls would escape the handler unwrapped instead of being caught,
  logged, and dropped. Affects `acquisition_idle_sequencer.py`
  (`handle_msg`), `bch_encoder.py` (`encode_bch`), `ccsds_reader.py`
  (`decode_ccsds`), `cltu_framer.py` (`add_sequences`), `db_client.py`
  (`make_db_call`), `encapsulation_header.py` (`add_header`),
  `sdls_authentication.py` (`add_authentication`), and
  `sdls_encryption.py` (`add_encryption`). The RX-side siblings of the
  last two (`sdls_authentication_verify.py`, `sdls_decryption.py`)
  already moved the equivalent calls inside `try` correctly, so this
  looks like a leftover from earlier in the TX-side blocks' cleanup, not
  a systemic pattern across the whole codebase.

## Deferred design questions (per-block PRDs)

Each of these is a parameter or code path a block's own PRD documents as
a genuine, deliberately-unresolved design question — see the linked
PRD's "Known issues / TODOs" section for full detail:

- [bch_decoder](prd/bch_decoder.md) — `primitive_polynomial` is accepted,
  validated, and stored but never used; forward-looking placeholder or
  dead code, undecided.
- [ccsds_reader](prd/ccsds_reader.md) — `message_type` is accepted and
  GRC-exposed as a TC/TM choice but completely unused; same
  placeholder-or-dead question.
- [ccsds_receiver](prd/ccsds_receiver.md) — `fixed_byte_length` has no
  Python-side constructor validation (the GRC yaml enforces
  `0 <= fixed_byte_length < 1024`, direct Python construction doesn't);
  whether to add one, and what range, is undecided.
- [lfsr_descrambler](prd/lfsr_descrambler.md) (and
  [lfsr_scrambler](prd/lfsr_scrambler.md), which defers the same question
  to this PRD) — `mask` is accepted, unvalidated, and never used; same
  placeholder-or-dead question.
- [sdls_decryption](prd/sdls_decryption.md) — `_validate_and_extract_key`
  still uses the ambiguous `PMT_NIL`-comparison pattern to detect an
  absent `crypt_key`, matching `sdls_encryption._extract_secret`'s own
  current implementation; fixing one without the other would leave the
  pattern inconsistent, so both are deferred together. Also:
  `decrypt_message` checks shape before `decryption_state`, the opposite
  order from `sdls_encryption.add_encryption` — a structural difference
  between the two blocks, not resolved.
