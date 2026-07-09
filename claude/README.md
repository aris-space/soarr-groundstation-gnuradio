# claude/ — documentation index

This folder holds gr-soarr's process/reference documentation: architecture,
development setup, and the decision records and PRDs that back them.
[CONTEXT.md](../CONTEXT.md), at the repo root, is the entry point (what
this project is, domain vocabulary) — start there, not here.

## Docs, in onboarding order

1. [../CONTEXT.md](../CONTEXT.md) — what gr-soarr is, domain vocabulary
2. [architecture.md](architecture.md) — pipeline diagrams, block roles, open questions
3. [development.md](development.md) — build/install/test setup, dev tooling
4. `adr/` — accepted decision records:
   - [0001 — Block naming convention](adr/0001-block-naming-convention.md)
   - [0002 — Flatten repo structure](adr/0002-flatten-repo-structure.md)
   - [0003 — Message-handler error policy](adr/0003-message-handler-error-policy.md)
   - [0004 — Docstring and PMT shape convention](adr/0004-docstring-and-pmt-shape-convention.md)
   - [0005 — RX path canonical block](adr/0005-rx-path-canonical-block.md)
   - [0006 — Rename gr-sage to gr-soarr](adr/0006-rename-sage-to-soarr.md)
5. `prd/` — one requirements doc per block (20 total). **Not yet
   populated** — see the parent plan's Step 4.

## Folder map

```
claude/
├── README.md         this file
├── architecture.md
├── development.md
├── adr/              six accepted ADRs
└── prd/              empty — future per-block work
```

## Doc conventions

- ADRs are short: a title plus 1-3 sentences on the decision and why,
  with `Status`, a rejected-alternatives note, or a "Consequences"-style
  callout added only when they add genuine value (e.g. a reference table
  a future implementer needs verbatim) — no template file; the format
  itself is small enough not to need one.
- Block naming follows [ADR-0001](adr/0001-block-naming-convention.md)'s
  target snake_case convention — **not yet applied**; every block is still
  named as it exists on disk today (camelCase). CONTEXT.md and
  architecture.md carry the same disclaimer; don't assume the rename has
  happened because it's decided.
- A PRD verification-status legend (for `prd/`) is not yet defined — decide
  it when Step 4 starts, don't invent one here ahead of that work.
