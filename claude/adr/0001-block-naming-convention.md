# 0001 — Block naming convention

**Status:** Accepted

All 20 blocks move to snake_case — file name, class name, and GRC block-id
suffix identical — matching gr-satellites' ecosystem convention rather than
PEP8-standard PascalCase classes (rejected for ecosystem consistency: GNU
Radio's own blocks and gr-satellites both use snake_case classes matching
their filenames). Fixes today's inconsistent mix of camelCase/PascalCase/
mismatched names and the baked-in `aqusitionIdleSequencer` typo. Executed;
see [coding-standards.md](../coding-standards.md) for the acronym rule and
the full rename table.
