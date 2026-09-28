# docs/ — documentation index

This folder holds gr-soarr's process/reference documentation: architecture,
development setup, and the decision records and PRDs that back them.

## Docs, in onboarding order

1. [architecture.md](architecture.md) — pipeline diagrams, block roles, open questions
2. [development.md](development.md) — build/install/test setup, dev tooling
3. [coding-standards.md](coding-standards.md) — naming, error handling,
   docstring/PMT-shape, and commit message rules for writing code here
4. `adr/` — accepted decision records:
   - [0001 — Block naming convention](adr/0001-block-naming-convention.md)
   - [0002 — Flatten repo structure](adr/0002-flatten-repo-structure.md)
   - [0003 — Message-handler error policy](adr/0003-message-handler-error-policy.md)
   - [0004 — Docstring and PMT shape convention](adr/0004-docstring-and-pmt-shape-convention.md)
   - [0005 — RX path canonical block](adr/0005-rx-path-canonical-block.md)
   - [0006 — Rename gr-sage to gr-soarr](adr/0006-rename-sage-to-soarr.md)
5. `prd/` — one requirements doc per block (23 total, written in
   signal-flow order):
   - [encapsulation_header](prd/encapsulation_header.md)
   - [sdls_encryption](prd/sdls_encryption.md)
   - [sdls_authentication](prd/sdls_authentication.md)
   - [sdls_header](prd/sdls_header.md)
   - [tc_primary_header](prd/tc_primary_header.md)
   - [lfsr_scrambler](prd/lfsr_scrambler.md)
   - [bch_encoder](prd/bch_encoder.md)
   - [cltu_framer](prd/cltu_framer.md)
   - [plop_modulator](prd/plop_modulator.md)
   - [cltu_burst_builder](prd/cltu_burst_builder.md)
   - [acquisition_idle_sequencer](prd/acquisition_idle_sequencer.md)
   - [inject_db](prd/inject_db.md)
   - [db_client](prd/db_client.md)
   - [data_creator](prd/data_creator.md)
   - [cltu_deframer](prd/cltu_deframer.md)
   - [bch_decoder](prd/bch_decoder.md)
   - [lfsr_descrambler](prd/lfsr_descrambler.md)
   - [ccsds_receiver](prd/ccsds_receiver.md)
   - [ccsds_reader](prd/ccsds_reader.md)
   - [sdls_authentication_verify](prd/sdls_authentication_verify.md)
   - [sdls_decryption](prd/sdls_decryption.md)
   - [encapsulation_parser](prd/encapsulation_parser.md)
   - [system_tester](prd/system_tester.md)

Open bugs, design questions, and test-coverage gaps are tracked as
[GitHub issues](https://github.com/aris-space/soarr-groundstation-gnuradio/issues).

## Folder map

```
docs/
├── README.md            this file
├── architecture.md
├── development.md
├── coding-standards.md
├── adr/                 six accepted ADRs
├── prd/                 one per block, 23 total
├── doxygen/             gr_modtool Doxygen setup (C++ API docs)
├── CMakeLists.txt       builds doxygen/ when ENABLE_DOXYGEN is on
└── README.soarr         gr_modtool-generated module readme
```

## Doc conventions

- ADRs are short: a title plus 1-3 sentences on the decision and why —
  the *why*, not the current rule. No template file; the format itself is
  small enough not to need one.
- The actual, current rules (naming, error handling, docstrings) live in
  [coding-standards.md](coding-standards.md), not in the ADRs — ADRs link
  to it rather than duplicating it, so there's one place to update when a
  rule changes.
- Block naming (in coding-standards.md, decided by
  [ADR-0001](adr/0001-block-naming-convention.md)) is snake_case: every
  block's file, class, and GRC block-id match. The docstring/PMT-shape
  convention (ADR-0004) and message-handler error handling (ADR-0003)
  apply to every block as well.
- PRDs carry no status marker (no "Draft"/"Reviewed" field): a PRD is
  reviewed before it is committed, so the committed version is the
  reviewed one.
- A PRD describes the block it documents **only from its current
  state** — not the editorial process that produced the doc, and not a
  prior, superseded version of anything it documents. Git history already
  preserves how and why a change happened; a PRD's job is to be an
  accurate snapshot of what exists now, not a diary of how it got there.
  A historical mapping (e.g. old-to-new naming) belongs in a doc whose
  purpose is to be that record — see
  [coding-standards.md](coding-standards.md)'s block-level rename table
  for the one place that applies here — not repeated ad hoc in PRD prose.
