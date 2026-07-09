# 0001 — Block naming convention

> *All 20 blocks — file name, class name, and GRC block-id suffix — move to snake_case, matching the wider GNU Radio OOT ecosystem (e.g. gr-satellites) instead of PEP8-standard PascalCase classes.*

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Yannick Kulli

## Context

Today's 20 blocks use inconsistent naming: 18 use camelCase
(`ccsdsReader`, `sdlsAuthentication`, …), one uses PascalCase (`Injectdb`),
and one has a mismatch between its file/class name (`systemTester`) and its
actual class name (`SystemTester`). One filename also carries a baked-in
typo, `aqusitionIdleSequencer` (missing the "c" in "acquisition"). In every
case but `systemTester`, the module file name and the class name inside it
are identical — this file/class identity is itself an implicit convention
worth preserving, not just the casing.

Before public release and before per-block PRDs are written (which will
reference block names throughout), this needs to be a single, explicit,
reproducible rule rather than 20 independent judgment calls made later,
piecemeal, while writing each PRD.

## Decision

Rename all 20 blocks to snake_case. For each block, the module file name,
the class name inside it, its matching `qa_*.py` test file name, and its GRC
block-id suffix (after the `soarr_` prefix from
[ADR-0006](0006-rename-sage-to-soarr.md)) all change together and stay
identical to each other — e.g. `ccsdsReader.py` (class `ccsdsReader`,
`qa_ccsdsReader.py`, block id `soarr_ccsdsReader`) becomes
`ccsds_reader.py` (class `ccsds_reader`, `qa_ccsds_reader.py`, block id
`soarr_ccsds_reader`).

**Acronym rule:** known multi-letter domain acronyms — CCSDS, SDLS, BCH,
CLTU, TC — collapse to a single lowercase token, not letter-by-letter. A
naive camelCase→snake_case conversion (insert `_` before every capital)
would otherwise mangle `ccsdsReader` into `c_c_s_d_s_reader` and
`tcPrimaryHeader` into `t_c_primary_header`.

**Full rename table:**

| Current | New |
|---|---|
| `encapsulationHeader` | `encapsulation_header` |
| `sdlsEncryption` | `sdls_encryption` |
| `sdlsAuthentication` | `sdls_authentication` |
| `sdlsHeader` | `sdls_header` |
| `tcPrimaryHeader` | `tc_primary_header` |
| `lfsrScrambler` | `lfsr_scrambler` |
| `bchEncoder` | `bch_encoder` |
| `cltuFramer` | `cltu_framer` |
| `Injectdb` | `inject_db` (its own GRC `name=` field is already "Inject DB", confirming this word order) |
| `dbClient` | `db_client` |
| `dataCreator` | `data_creator` |
| `aqusitionIdleSequencer` | `acquisition_idle_sequencer` (typo fix) |
| `cltuDeframer` | `cltu_deframer` |
| `ccsdsReceiver` | `ccsds_receiver` |
| `ccsdsReader` | `ccsds_reader` |
| `sdlsAuthenticationVerify` | `sdls_authentication_verify` |
| `sdlsDecryption` | `sdls_decryption` |
| `systemTester` / `SystemTester` | `system_tester` (both file and class — fixes the one existing file/class mismatch) |
| `bchDecoder` | `bch_decoder` |
| `lfsrDescrambler` | `lfsr_descrambler` |

Each corresponding `qa_*.py` test file is renamed to match (e.g.
`qa_ccsdsReader.py` → `qa_ccsds_reader.py`), in the same future change as
its block, so no test file is left testing a module under a name that no
longer exists.

This ADR covers naming/identifiers only — docstrings ([ADR-0004](0004-docstring-and-pmt-shape-convention.md))
and error-handling ([ADR-0003](0003-message-handler-error-policy.md)) are
separate decisions.

## Alternatives considered

- **Keep current mixed naming** — Rejected. Three incompatible styles
  coexisting (18× camelCase, 1× PascalCase, 1× file/class mismatch) with no
  discoverable rule for which style applies where is exactly the problem
  being solved.
- **PEP8-standard casing** (`PascalCase` classes in `snake_case` files,
  e.g. `ccsds_reader.py` containing `class CcsdsReader`) — Rejected in favor
  of ecosystem consistency. GNU Radio's own stock blocks and gr-satellites
  (the plan's stated reference project) both use snake_case classes matching
  their filenames; deviating to PEP8-standard PascalCase would make this
  module's blocks look inconsistent sitting next to stock blocks in a
  flowgraph or GRC search, which matters more here than general Python
  style-guide conformance.

## Consequences

- The actual rename (touching all 20 blocks' files, classes, test files,
  GRC yaml, and every internal import) is **not executed by this ADR** —
  it's deferred to future work, driven per-block by each block's PRD (per
  the plan's "Out of scope" section). This ADR records the target naming so
  that deferred work has one unambiguous table to follow, rather than
  requiring a fresh judgment call per block.
- No `.grc` example flowgraph exists yet that references any block's
  current GRC id (see the plan's "Known gap" note), so this rename carries
  no risk of breaking an existing saved flowgraph.
- Once executed, every internal `from gnuradio.soarr import <old_name>` /
  `from .{old_name} import ...` reference across the codebase (blocks,
  `qa_*.py` files, `python/soarr/__init__.py`, `python/soarr/CMakeLists.txt`)
  must be updated in the same change as each block's rename, to avoid a
  partially-renamed in-between state (the same reasoning as
  [ADR-0006](0006-rename-sage-to-soarr.md)'s "no in-between state" rule).

---

_Write an ADR only when the decision is (1) hard to reverse, (2) surprising
without context, and (3) the result of a real trade-off. If any of the three is
missing, it probably belongs in a doc or in CONTEXT.md, not here._
