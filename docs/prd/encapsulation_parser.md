# encapsulation_parser

## Purpose

Splits a PDU's payload into its CCSDS 133.1-B encapsulation packets,
strips each packet's header, and publishes each data field with the
parsed header fields as metadata. The RX counterpart of
[encapsulation_header](encapsulation_header.md). See
[architecture.md](../architecture.md).

## Pipeline position

RX chain, after SDLS verification and decryption:

```
sdls_decryption.out → encapsulation_parser.in
encapsulation_parser.out → (application)
```

It has to come after `sdls_decryption`: on TX the encapsulation header
is added before `sdls_encryption`, so with encryption on the header is
ciphertext until it has been decrypted.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . packets_u8vector)` — one or more encapsulation packets back to back. | `pmt.cons({...}, u8vector(fe 00 01 04 ‖ 256 bytes))` |
| `out` | output | One PDU per data packet: the input metadata plus `encapsulation_header` (dict: `first_octet`, `packet_version`, `protocol_id`, `length_of_length`, `packet_length`, and — only where the header variant carries them — `user_defined_field`, `protocol_id_extension`, `ccsds_defined_field`), paired with the packet's data field. | `pmt.cons({encapsulation_header: {packet_length: 260, ...}, ...}, u8vector(256 bytes))` |

The `encapsulation_header` metadata uses the same keys `ccsds_reader`
publishes when it parses the header itself (unencrypted frames).

## Parameters

None.

## Behavior / edge cases / current error handling

**Packet walk** (`parse_packets`): starting at byte 0, parses a header
with `encapsulation_packet.parse_header`, takes `packet_length` bytes
(header plus data; the 1-byte variant without a length field is a
header-only idle packet), publishes the data field, and continues with
the next packet until the payload is used up. Packets whose
`protocol_id` is idle (`0b000`) are skipped without output — CCSDS
133.1-B uses them as fill.

**Malformed packets**: a truncated header, a `packet_length` shorter
than the header, or one longer than the remaining data stops the walk:
that packet and everything after it are dropped (logged at `error`);
packets before it have already been published.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)):
`parse_packets`'s full body is wrapped in catch-log-drop, all logged at
`error` — this block sits after `ccsds_reader`'s structural checks, not
on raw RF data. `PMT_NIL` metadata is accepted (PMT treats it as an
empty dict).

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)):
`Args` for `__init__`, `Args`/`Publishes`/`Drops when` for
`parse_packets`, `Args`/`Returns` for `_header_meta`.

**Naming**: file, class, GRC block-id, and the handler method are
snake_case.

## CCSDS reference

CCSDS 133.1-B (Encapsulation Service) — the packet header format and
its four length-of-length variants (Table 4-2), defined once in
`python/soarr/encapsulation_packet.py` and shared with
`encapsulation_header`, `ccsds_reader`, and
`sdls_authentication_verify`.

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_encapsulation_parser.py` — 9 test methods
  (`test_instance` + `test_001`–`test_008`): round trips with the real
  `encapsulation_header` block for every header variant (`test_001`),
  header fields in the metadata with the input metadata kept and absent
  fields left out (`test_002`), idle packets producing no output, also
  after a data packet (`test_003`), several packets in one PDU
  (`test_004`), truncated packets dropped while earlier ones are still
  delivered (`test_005`), a packet length shorter than its header
  dropped (`test_006`), malformed input dropped without raising
  (`test_007`), and a publish failure caught (`test_008`).
