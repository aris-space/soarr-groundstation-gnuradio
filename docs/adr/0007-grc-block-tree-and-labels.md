# 0007 — GRC block tree, labels, and hierarchical blocks

**Status:** Accepted

**Tree:** every block sits under `[Soarr]` (GNU Radio's capitalisation for
module roots) in one of four folders: `TC (Telecommand)` and
`TM (Telemetry)` for blocks only one link direction uses; `Common` for
blocks TC and TM share; `Utilities` for test tooling. Inside them, blocks
are grouped by function, not by TX and RX, so a block and its
counterpart sit together: TC has Frame, Randomizer, BCH, CLTU and PLOP,
Common has SDLS, Encapsulation and Key Database (rejected: TX/RX
folders, which split each encoder/decoder pair). A hierarchical block for
a whole chain (TC Frame Encoder/Decoder) sits directly in its link
folder, one for a function next to its parts. GRC files a block under one
category only, so a shared block lives in `Common` rather than appearing
under both TC and TM (rejected: a second block YAML with another id for
the same block — flowgraphs store the id, so one block would have two
names). A block TM can reuse only once it is generalised (e.g. the TC
scrambler: TM uses another randomizer) stays under TC until then.

**Labels** (what GRC displays; ids and Python names follow
[ADR-0001](0001-block-naming-convention.md) and don't change with them),
after gr-satellites' CCSDS blocks: a TX block that adds a field is a
`… Adder`, its RX counterpart that strips it into the metadata a
`… Parser`, an RX block that checks and strips a MAC a `… Verifier`;
transforms keep their verb pairs (Encoder/Decoder, Scrambler/Descrambler,
Framer/Deframer, Encryption/Decryption). Every block outside `Common` and
`Utilities` starts with `TC` or `TM`. Changing a label never breaks a
saved flowgraph.

**Hierarchical blocks** are Python `gr.hier_block2` classes with a block
YAML, tested like any block (rejected: GRC-built hier blocks compiled with
`grcc`, which leave generated code in the repo). They expose only what a
mission chooses; values CCSDS fixes (sequences, polynomials, CRC
parameters, field widths) are built in. The individual blocks stay in the
tree for debugging and custom chains.
