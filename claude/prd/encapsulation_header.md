# encapsulation_header

## Purpose

Builds and prepends a CCSDS 133.1-B Encapsulation Packet header to an
outgoing PDU's payload, selecting the header variant (length-of-length)
from payload size per Table 4-2. See
[Encapsulation Packet](../../CONTEXT.md) in the glossary for what this
layer is and how it's positioned relative to the transfer frame.

## Pipeline position

TX chain, immediately after payload sourcing/key-lookup
(`inject_db`/`db_client`/`data_creator`) and before `sdls_encryption` — see
[architecture.md](../architecture.md)'s TX chain diagram for the full
picture. This block is TX-only; it has no RX-side role. However, its wire
format *is* consumed on RX — see Known Issues below.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)` — no required metadata keys | `pmt.cons({frame_id: 42}, u8vector([0x10, 0x20, 0x30]))` |
| `out` | output | PDU: `(metadata_dict . header‖payload_u8vector)` — metadata dict is the same object as input, unmodified | `pmt.cons({frame_id: 42}, u8vector([header_bytes..., 0x10, 0x20, 0x30]))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `user_defined_field` | int | `0b0000` (0) | 4-bit field, only present *in the header* when payload size selects LOL ≥ `10` (see Behavior) — but validated at construction time regardless of payload size. |

## Behavior / edge cases / current error handling

**Input validation and error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): `add_header`
rejects and logs at `error` level, without publishing, for a non-PDU
input, non-dict metadata, a non-u8vector payload, or a header-packing
failure (e.g. an oversized payload — see `_determine_length_of_length`).
`user_defined_field` is range-checked once, in the constructor (raises
`ValueError` at flowgraph-build time if outside 0–15), rather than only
incidentally when a large-enough payload happens to make the field get
packed.

**Header variant selection** — the 2-bit `length_of_length` (LOL) code is
chosen from payload size, per CCSDS 133.1-B Table 4-2 (single source of
truth: `encapsulation_header.LENGTH_OF_LENGTH_TABLE`):

| Payload size | LOL | Header size | Extra fields present |
|---|---|---|---|
| 0 bytes | `00` | 1 byte | none |
| 1–253 bytes | `01` | 2 bytes | 1-byte `packet_length` |
| 254–65531 bytes | `10` | 4 bytes | + `user_defined_field`, `protocol_id_extension` (1 byte); 2-byte `packet_length` |
| 65532–4294967287 bytes | `11` | 8 bytes | + `ccsds_defined_field` (2 bytes, always 0); 4-byte `packet_length` |
| larger | — | — | header packing fails; caught and dropped, not raised (see above) |

`packet_length`, when present, encodes the **total** packet length
(header + payload), not just the payload length.

`protocol_id` is `IDLE` (`0b000`) when the payload is empty, `DATA`
(`0b111`) otherwise — this is the only thing that selects an idle packet;
there's no separate "send idle" input.

## CCSDS reference

CCSDS 133.1-B (Encapsulation Packet Protocol), Table 4-2, governs header
variant/length-of-length selection.

**Simplified/omitted:** `protocol_id_extension` and `ccsds_defined_field`
are always hardcoded to 0 — not exposed as GRC parameters, unlike
`user_defined_field`. There's no support for a `protocol_id` other than
`IDLE`/`DATA` (the spec's extended-Protocol-ID mechanism, e.g. for
VCA/Bitstream encapsulation, is unimplemented).

## Known issues / TODOs

- **The wire format built here is independently re-implemented, not
  shared, in two other places**: `ccsds_reader.py`'s own
  `encapsulation_header()` method (RX-side parsing) and
  `sdls_authentication_verify.py`'s `_build_encapsulation_header` (RX-side
  rebuild, needed to reconstruct the authenticated bytes for CMAC
  verification). Confirmed field-for-field consistent with this block as
  of this writing, but all three must be kept in manual sync if this
  format ever changes — there's no shared source of truth. (Out of scope
  to fix here — would mean changing two other blocks' files.)

## Test coverage

- `python/soarr/qa_encapsulation_header.py` — 21 test methods
  (`test_instance` + `test_001`–`test_020`): PDU-shape rejection (3 cases),
  empty-payload/idle framing, each LOL variant (01/10/11), boundary-size
  transitions (both a focused check and an end-to-end sweep), the
  oversized-payload exception from the static helper directly
  (`test_009`), valid-input passthrough, metadata preservation (2 tests,
  including a multi-key integrity sweep across all size variants), field
  omission for LOL=01, protocol-id selection on empty vs. non-empty
  payload, exact `packet_length` byte encoding at key boundaries
  (`test_017`), the two `user_defined_field` range checks now proven at
  construction time (`test_014`, `test_015`), and a mock-forced internal
  build failure proven to be caught and dropped rather than raised through
  the real handler (`test_020` — a real oversized payload would need
  ~4GB to construct, so the failure is forced via
  `unittest.mock.patch.object` instead).
- `python/soarr/qa_layoutTest.py::test_008_encapsulation_header_real_handler`
  — builds a **fresh, standalone** `encapsulation_header` instance (not
  the one wired into the test flowgraph) and calls `add_header` directly;
  proves the real header-building logic produces correct bytes, but not
  that the `msg_connect` wiring works.
- `python/soarr/qa_layoutTest.py::test_002_end_to_end_message_routing` —
  proves the opposite: that the full TX chain's `msg_connect` topology
  correctly routes a message end-to-end, but with every block's real
  handler (including this one's `add_header`) replaced by a pass-through
  shim. Together, `test_002` and `test_008` give topology and logic
  coverage separately; neither proves both at once for this block.
