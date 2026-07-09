# 0005 — RX path canonical block

> *`ccsdsReceiver` is documented as the one recommended RX path. The standalone wired chain (`cltuDeframer` → `bchDecoder` → `lfsrDescrambler`) stays in the codebase and keeps its GRC exposure, but is documented as an internal/development testing pattern, not the public API.*

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Yannick Kulli

## Context

`ccsdsReceiver` imports `bchDecoder` and `lfsrDescrambler` directly
(`from gnuradio.soarr import bchDecoder, lfsrDescrambler` in
`python/soarr/ccsdsReceiver.py`) and calls them as plain Python helper
objects internally — not wired via GNU Radio message ports. On top of that,
it implements frame reassembly, TFPH search, and message-type dispatch,
none of which exist as separate, independently wireable blocks anywhere
else in the codebase.

Both `bchDecoder` and `lfsrDescrambler` are also still fully exposed as
standalone GRC blocks, and `qa_lfsr_receive_chain.py` wires them together
with `cltuDeframer` into a standalone chain (`cltuDeframer` → `bchDecoder`
→ `lfsrDescrambler`) for testing. Someone writing a flowgraph from scratch,
or reading the GRC block tree, could reasonably wire that same three-block
chain expecting it to be a complete RX path — it isn't. It stops at the
physical/FEC layer and never reaches frame reassembly or TFPH search, so it
cannot actually decode a full CCSDS TC frame on its own. Before writing
`claude/architecture.md` and the RX-block PRDs, this needs to be resolved
so the documentation doesn't present two competing "the RX path" stories.

## Decision

`ccsdsReceiver` is the one documented, recommended RX path — the block that
`architecture.md` and any example flowgraph should point to for decoding
received frames. `bchDecoder` and `lfsrDescrambler` remain real dependencies
in the codebase, keep their GRC exposure, and keep working as standalone
blocks — but are documented as internal primitives / a
development-and-debugging testing pattern (useful for isolating bugs at
exactly the physical/FEC layer they operate on via
`qa_lfsr_receive_chain.py`), not as an alternative public RX API.

This ADR is purely a documentation-emphasis decision — no code changes
result from it. It governs what `architecture.md` and the block PRDs say,
not how any block is implemented.

This ADR does not cover the SDLS verify-then-decrypt ordering question
(`sdlsAuthenticationVerify` → `sdlsDecryption`), which is a separate,
downstream open question tracked in `architecture.md`, unrelated to which
block is the canonical physical/FEC-layer RX entry point.

## Alternatives considered

- **Make the standalone wired chain the canonical RX path instead** —
  Rejected on a factual, verifiable basis: it's incomplete. It only
  exercises `cltuDeframer` → `bchDecoder` → `lfsrDescrambler` (confirmed in
  `qa_lfsr_receive_chain.py`) and never reaches frame reassembly, TFPH
  search, or message-type dispatch — logic that exists only inside
  `ccsdsReceiver`. It cannot decode a full CCSDS TC frame by itself, so it
  cannot be "the" RX path regardless of documentation preference.
- **Deprecate or remove `bchDecoder`/`lfsrDescrambler`'s standalone GRC
  exposure**, since `ccsdsReceiver` now duplicates their functionality
  internally — Rejected. They remain useful as real, independently
  testable/wireable blocks for isolating bugs at the physical/FEC layer
  specifically (that's what `qa_lfsr_receive_chain.py` is for); removing
  their GRC exposure would make that kind of isolated debugging harder for
  no benefit, since `ccsdsReceiver` calling them internally doesn't require
  them to disappear as standalone blocks.

## Consequences

- `architecture.md`'s RX chain diagram and legend must clearly distinguish
  "canonical path" (`ccsdsReceiver`) from "internal primitive, GRC-exposed
  for testing" (`bchDecoder`, `lfsrDescrambler`) — not just list all RX
  blocks as equals.
- `bchDecoder` and `lfsrDescrambler`'s future PRDs (`claude/prd/`) should
  reference this ADR to explain why a block described as an "internal
  primitive" still has its own PRD and full GRC exposure, rather than
  leaving that as an apparent contradiction for the reader.
- `ccsdsReceiver`'s own PRD carries the weight of documenting the full RX
  decode behavior (BCH-decode, descramble, TFPH search, frame reassembly,
  message-type dispatch) in one place, since none of that logic is
  documented separately anywhere else.

---

_Write an ADR only when the decision is (1) hard to reverse, (2) surprising
without context, and (3) the result of a real trade-off. If any of the three is
missing, it probably belongs in a doc or in CONTEXT.md, not here._
