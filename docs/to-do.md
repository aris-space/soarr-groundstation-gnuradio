# To-do

Outstanding, currently-postponed items — kept short; each points to where
the detail actually lives instead of duplicating it here. Not a status
log or a session snapshot (see git history for that).

## Known bugs

- [ccsds_reader](prd/ccsds_reader.md) / [sdls_decryption](prd/sdls_decryption.md)
  — SDLS encryption with an encapsulation header doesn't round-trip: TX
  encrypts the encapsulation header together with the payload, but RX's
  `ccsds_reader` strips the header before `sdls_decryption` runs, reading
  ciphertext as a header. Reproduced with `examples/tc_loopback_sim.grc`
  and both SDLS encryption blocks on: every payload is wrong (252 instead
  of 256 bytes), while authentication still passes. Fixing it means
  removing the encapsulation header only after decryption (see
  [architecture.md](architecture.md)'s Known gaps) — a contract change
  for `ccsds_reader`.
- [sdls_encryption](prd/sdls_encryption.md) — no (nonce, counter) reuse
  protection. If `db_client`'s `auto_reset_counters=True` is ever set on
  an instance used with real key material, the counter wraps and
  repeats, and reusing a (nonce, counter) pair under AES-CTR with the
  same key breaks confidentiality for both messages involved.
## Deferred design questions

- [acquisition_idle_sequencer](prd/acquisition_idle_sequencer.md) — the
  acquisition sequence is fixed at construction with no public attribute
  to reassign at runtime, unlike `cltu_framer`'s `start_sequence`/
  `tail_sequence`, which are public and re-read fresh on every message.
  Whether this block should match that pattern is undecided.
- [cltu_deframer](prd/cltu_deframer.md) — two ways to locate CLTUs:
  tag-based `general_work` (what GNU Radio's scheduler calls) and the
  untagged `process_bytes` search. Both share one CLTU parser and both
  are tested, but `process_bytes` is only used by tests and has no
  production caller. Whether to keep it as a public fallback or make it
  test-only is undecided.

## Cross-block architectural debt

- [encapsulation_header](prd/encapsulation_header.md) — its wire format
  is independently reimplemented (not shared) in `ccsds_reader.py`'s
  `encapsulation_header()` and `sdls_authentication_verify.py`'s
  `_build_encapsulation_header` — field-for-field consistent today, but
  all three must be kept in manual sync with no shared source of truth.
- [sdls_authentication_verify](prd/sdls_authentication_verify.md) — its
  trailer-in-metadata tag-reconstruction path independently reimplements
  `ccsds_reader.encapsulation_header()`'s bit-packing logic in a
  different file, with no shared code and no test exercising it through
  the real `ccsds_reader` → `sdls_authentication_verify` pipeline.
- The fixed 2-byte SDLS counter width is now hardcoded independently in
  4 places with no shared source of truth: [sdls_header](prd/sdls_header.md)'s
  `REQUIRED_IV_LENGTH_BYTES`, [sdls_encryption](prd/sdls_encryption.md)/
  `sdls_decryption`'s `NONCE_LEN`-derived AES-CTR counter width, and
  `sdls_authentication`/`sdls_authentication_verify`'s literal
  `counter.to_bytes(2, ...)`. A future counter-width change would need
  all 4 updated together, with no compiler or test to catch a missed one.

## Test-coverage gaps (no known bug, just untested)

- [sdls_authentication](prd/sdls_authentication.md) and
  [sdls_encryption](prd/sdls_encryption.md) — the nested
  `sdls.security_header.sdls_counter` fallback path is the *only* path
  `sdls_counter` takes in the real pipeline (confirmed via `inject_db`'s
  merge logic), but every test in both blocks constructs it at the top
  level directly, bypassing that path entirely.
- [sdls_header](prd/sdls_header.md) — same untested-but-only-real-path
  issue for both `spi` and `sdls_counter`'s nested fallback lookups.
- [tc_primary_header](prd/tc_primary_header.md) — same
  untested-but-only-real-path issue for `vcid_counter`, this block's one
  required field.
- [tc_primary_header](prd/tc_primary_header.md) — no test checks the
  `frame_length` field's actual value against a real, wired
  `digital.crc_append`; `qa_tx_chain.py::test_002` only checks total
  length, and the real handler is shimmed out there anyway.
- [system_tester](prd/system_tester.md) — no test exercises
  `handle_transmitted`, `AUTOMATIC_MODE`, the `repetitions` cap, or
  `stats_path` actually writing a file.
