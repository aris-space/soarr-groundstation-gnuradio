# To-do

Outstanding, currently-postponed items — kept short; each points to where
the detail actually lives instead of duplicating it here. Not a status
log or a session snapshot (see git history for that).

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
