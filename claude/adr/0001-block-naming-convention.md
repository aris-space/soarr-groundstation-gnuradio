# 0001 — Block naming convention

**Status:** Accepted

All 20 blocks move to snake_case — file name, class name, and GRC block-id
suffix identical — matching gr-satellites' ecosystem convention rather than
PEP8-standard PascalCase classes (rejected for ecosystem consistency: GNU
Radio's own blocks and gr-satellites both use snake_case classes matching
their filenames). Fixes today's inconsistent mix of camelCase/PascalCase/
mismatched names and the baked-in `aqusitionIdleSequencer` typo. Not yet
executed — this records the target naming for future work, driven
per-block by each block's PRD.

**Acronym rule:** known multi-letter domain acronyms (CCSDS, SDLS, BCH,
CLTU, TC) collapse to one lowercase token, not letter-by-letter —
`ccsdsReader` → `ccsds_reader`, not `c_c_s_d_s_reader`.

**Rename table** (each block's matching `qa_*.py` test file renames the
same way):

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
| `Injectdb` | `inject_db` (its own GRC `name=` field is already "Inject DB") |
| `dbClient` | `db_client` |
| `dataCreator` | `data_creator` |
| `aqusitionIdleSequencer` | `acquisition_idle_sequencer` (typo fix) |
| `cltuDeframer` | `cltu_deframer` |
| `ccsdsReceiver` | `ccsds_receiver` |
| `ccsdsReader` | `ccsds_reader` |
| `sdlsAuthenticationVerify` | `sdls_authentication_verify` |
| `sdlsDecryption` | `sdls_decryption` |
| `systemTester` / `SystemTester` | `system_tester` (fixes the one existing file/class mismatch) |
| `bchDecoder` | `bch_decoder` |
| `lfsrDescrambler` | `lfsr_descrambler` |
