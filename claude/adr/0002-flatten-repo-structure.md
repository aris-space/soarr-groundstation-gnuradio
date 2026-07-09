# 0002 — Flatten repo structure

> *Promote `gr-sage/`'s contents to the repository root instead of leaving the module nested one level below it.*

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Yannick Kulli

## Context

The module was originally scaffolded with `gr_modtool newmod sage`, GNU Radio's
standard OOT-module generator, which by convention creates every module inside
its own `gr-<name>/` subdirectory (first commit `0bedc51`, "Initial structure
for 'Out of Tree' (OOT) module 'sage' created (with gr_modtool)"). The
surrounding repo wrapper (root `README.md`, `requirements.txt`, `tools/`,
`.vscode/`) was built up around that generated skeleton, leaving `gr-sage/`
nested one level below the git root — a structure that exists purely as a
byproduct of the scaffolding tool's default, not a deliberate choice.

Ahead of public release, real documentation (`CONTEXT.md`, `architecture.md`,
per-block PRDs) was about to be written. Writing that documentation against a
structure that carries a meaningless extra directory level would either bake
the indirection into the docs permanently or require rewriting them later.

## Decision

Flatten: `git mv` everything from `gr-sage/*` (including dotfiles —
`.clang-format`, `.cmake-format.py`) up to the repository root, so the git
root and the module root are the same directory. Every path reference that
previously carried a redundant `gr-sage/` segment moved in the same pass:
CMake (`cmake.sourceDirectory`/`cmake.buildDirectory` in
`.vscode/settings.json`), `tools/*` scripts, and `.gitignore`.

Executed in the same commit as [ADR-0006](0006-rename-sage-to-soarr.md)'s
namespace rename, for execution convenience — the two decisions remain
conceptually independent (you could flatten without renaming, or rename
without flattening), hence two separate ADRs rather than one.

## Alternatives considered

- **Leave nested as-is** — Rejected. The nesting is an artifact of
  `gr_modtool newmod`'s default layout, not something intentionally chosen.
  Keeping it would perpetuate a meaningless extra path segment across every
  tool config for no benefit, and would force root-level docs to describe a
  two-layer structure that doesn't reflect how the module is actually built,
  tested, or used.
- **Convert to a git submodule** — Not seriously considered; nothing in the
  repo's history indicates this was ever evaluated. The nested layout only
  resembles a submodule visually. Listing it as a rejected alternative would
  manufacture a choice that was never genuinely on the table.

## Consequences

- Every path reference (CMake source/build dirs, `.vscode/settings.json`,
  `tools/*` scripts, `.gitignore`) had to move in lockstep with the flatten —
  done in the same pass to avoid a two-step drift.
- **Known wart:** absolute paths embedded in plain-text/JSON config
  (`.vscode/settings.json`, `README.md`) and paths baked into artifacts
  *outside* version control (the CMake `build/` cache's `CMakeCache.txt`, and
  a shadow-install `.pth` file the dev tooling writes into the conda
  environment's site-packages) are **not** caught by grepping the tracked
  source tree for the old name — they reference the old *directory name*
  only, which a code/identifier grep won't match. A follow-up portation check
  after this flatten found and fixed several of these: a stale
  `cmake.sourceDirectory`, a `build/` directory whose cached paths blocked
  CMake reconfiguration entirely, and a stale shadow-install `.pth` file.
  Future restructures of this kind should explicitly check absolute paths and
  out-of-repo generated artifacts, not just grep the tracked source tree.

---

_Write an ADR only when the decision is (1) hard to reverse, (2) surprising
without context, and (3) the result of a real trade-off. If any of the three is
missing, it probably belongs in a doc or in CONTEXT.md, not here._
