# claude/ — documentation index

This folder holds gr-soarr's process/reference documentation: architecture,
development setup, and the decision records and PRDs that back them.
[CONTEXT.md](../CONTEXT.md), at the repo root, is the entry point (what
this project is, domain vocabulary) — start there, not here.

## Docs, in onboarding order

1. [../CONTEXT.md](../CONTEXT.md) — what gr-soarr is, domain vocabulary
2. [architecture.md](architecture.md) — pipeline diagrams, block roles, open questions
3. [development.md](development.md) — build/install/test setup, dev tooling
4. [coding-standards.md](coding-standards.md) — naming, error handling,
   docstring/PMT-shape rules for writing code here
5. `adr/` — accepted decision records:
   - [0001 — Block naming convention](adr/0001-block-naming-convention.md)
   - [0002 — Flatten repo structure](adr/0002-flatten-repo-structure.md)
   - [0003 — Message-handler error policy](adr/0003-message-handler-error-policy.md)
   - [0004 — Docstring and PMT shape convention](adr/0004-docstring-and-pmt-shape-convention.md)
   - [0005 — RX path canonical block](adr/0005-rx-path-canonical-block.md)
   - [0006 — Rename gr-sage to gr-soarr](adr/0006-rename-sage-to-soarr.md)
6. `prd/` — one requirements doc per block (20 total, written in
   signal-flow order per the parent plan's Step 4). In progress:
   - [encapsulation_header](prd/encapsulation_header.md)
   - [sdls_encryption](prd/sdls_encryption.md)
   - [sdls_authentication](prd/sdls_authentication.md)
   - [sdls_header](prd/sdls_header.md)

## Folder map

```
claude/
├── README.md            this file
├── architecture.md
├── development.md
├── coding-standards.md
├── adr/                 six accepted ADRs
└── prd/                 one per block, 20 total (in progress)
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
  [ADR-0001](adr/0001-block-naming-convention.md)) is snake_case — applied;
  every block's file, class, and GRC block-id now match. Message-handler
  error handling (ADR-0003) and the docstring/PMT-shape convention
  (ADR-0004) are still not applied to existing blocks — don't assume those
  two are done just because naming is.
- PRDs carry no verification-status marker (no "Draft"/"Reviewed" field) —
  like ADRs and the root docs before them, a PRD is only committed after
  being reviewed and signed off in the same interview process that writes
  it, so a status field would just duplicate that.
