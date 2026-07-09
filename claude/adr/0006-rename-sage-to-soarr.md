# 0006 — Rename gr-sage to gr-soarr

> *Rename the module, its Python namespace, its GRC block-id prefix, and the wrapper repo's own directory — full rename, one pass, no compatibility shim.*

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Yannick Kulli

## Context

The module was scaffolded under the name "sage," which was ARIS's project
name at the time (`gr_modtool newmod sage`, first commit `0bedc51`). During
development of this library, the ARIS project rebooted and renamed itself
SOARR. Continuing to develop the module, its namespace, and its block IDs
under the old "sage" name after that rename would leave the codebase
referring to a project that no longer exists under that name — confusing for
anyone who joins knowing only the current "SOARR" name, and increasingly
inaccurate as documentation referring to "SOARR" gets written against a
"sage"-named codebase.

## Decision

Full rename, `gr-sage` → `gr-soarr`, executed in the same pass as
[ADR-0002](0002-flatten-repo-structure.md)'s flatten:

- Python namespace: `gnuradio.sage` → `gnuradio.soarr`
  (`python/sage/` → `python/soarr/`,
  `python/gnuradio/sage/` → `python/gnuradio/soarr/`)
- Include path: `include/gnuradio/sage/` → `include/gnuradio/soarr/`
- All 20 GRC block-id prefixes: `sage_<name>.block.yml` →
  `soarr_<name>.block.yml`, including the `id:` field inside each
- CMake project name: `project(gr-sage ...)` → `project(gr-soarr ...)`
- `MANIFEST.yml`'s module name field
- The wrapper repo's own directory name:
  `sage-groundstation-gnuradio` → `soarr-groundstation-gnuradio` — this
  expands on the plan that originally proposed this rename, which said the
  wrapper repo "can keep its own name for now." In practice, leaving the
  top-level folder named "sage" while everything inside it said "soarr"
  would itself have been the confusing in-between state this decision exists
  to avoid, so the wrapper was renamed too. This ADR documents what was
  actually done, superseding that earlier "keep it for now" intent.

All of the above changed together in one pass, to avoid an in-between state
where some identifiers say `soarr` and others still say `sage`.

## Alternatives considered

- **Partial/folder-only rename** (rename the directory but leave the Python
  namespace, GRC prefix, and CMake project name as `sage`) — Rejected. Would
  leave a confusing in-between state where the folder name and the actual
  code identifiers disagree, actively misleading for anyone reading the code
  without also knowing this history.
- **Keep the "sage" name entirely** — Rejected. The upstream project that
  "sage" named no longer exists under that name; continuing to develop under
  a defunct project's name misrepresents what this module is and who it
  belongs to.

## Consequences

- No backward-compatibility shim (no `gnuradio.sage` re-export module, no
  deprecated `sage_*` GRC block-id aliases). Acceptable because the module
  has no external consumers yet — pre-release, with no `LICENSE`, no CGRAN
  listing, and blank `MANIFEST.yml` repo/website fields.
- Anyone searching for "gr-sage" (old commit messages, old ARIS
  documentation, old collaborators' bookmarks) will no longer find this
  project under that name; intentional and expected given the upstream
  project's own rename.
- Git history predating the rename (e.g. the first commit's message,
  "Initial structure for ... module 'sage'") still refers to "sage" —
  left as-is; rewriting history is out of scope and undesirable.
- Executed in the same commit as [ADR-0002](0002-flatten-repo-structure.md)'s
  flatten for execution convenience; the two decisions remain conceptually
  independent.

---

_Write an ADR only when the decision is (1) hard to reverse, (2) surprising
without context, and (3) the result of a real trade-off. If any of the three is
missing, it probably belongs in a doc or in CONTEXT.md, not here._
