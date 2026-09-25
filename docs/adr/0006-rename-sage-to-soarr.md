# 0006 — Rename gr-sage to gr-soarr

**Status:** Accepted

The module was scaffolded as "sage," ARIS's project name at the time; the
project later rebooted and renamed itself SOARR, so continuing to develop
under the old name would misrepresent what this module is. Full rename in
one pass (Python namespace `gnuradio.sage`→`gnuradio.soarr`, GRC block-id
prefix, CMake project name, `MANIFEST.yml`, and the wrapper repo's own
directory name) to avoid an in-between state where some identifiers say
`soarr` and others still say `sage` (rejected: a partial/folder-only
rename, for exactly that reason). No compatibility shim — the module has
no external consumers yet (pre-release, no `LICENSE`, no CGRAN listing).
