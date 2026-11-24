# sdls_header_parser

## Purpose

Reads and strips the CCSDS 355.0-B-1 SDLS Security Header (SPI + IV) at
the start of a PDU's payload and puts its fields into the metadata,
where the RX Key Database and SDLS Process look for them. The RX
counterpart of [sdls_header](sdls_header.md). GRC label **SDLS Header
Parser**, category `[Soarr]/Common/SDLS`. On TC, `ccsds_reader` parses
the security header together with the frame; this block is for chains
where nothing else does, such as TM.

## Pipeline position

TM chain, with the SDLS-protected data carried in an encapsulation packet
so the TM frame layer can delimit it:

```
TX: key_database (role "tx") → sdls_apply → encapsulation_header → tm_frame_generator → …
RX: … → tm_frame_decoder.out → encapsulation_parser → sdls_header_parser
    → key_database (role "rx") → sdls_process → (application)
```

The security header, the (encrypted) data and the MAC have no length
field of their own, so the encapsulation header goes on after SDLS
Apply, and the Encapsulation Parser takes it off before this block. The
data needs no encapsulation header of its own: its length is the
encapsulation packet's minus the security header and the MAC.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . u8vector)`, the payload starting with the security header: SPI (2 bytes) ‖ IV (`sdls_constants.SDLS_COUNTER_BYTES`, 2 bytes), as `sdls_header` builds it. | `pmt.cons({scid: 341}, u8vector(00 01 00 2a ‖ ciphertext ‖ mac))` |
| `out` | output | PDU: the input metadata plus `sdls.security_header` with `spi`, `security_param_index`, `sdls_counter` and `initialization_vector` (int) — the same keys `ccsds_reader` publishes — and the payload after the header (the (encrypted) data and, if the mode authenticates, the MAC). | `pmt.cons({scid: 341, sdls: {security_header: {spi: 1, sdls_counter: 42, …}}}, u8vector(ciphertext ‖ mac))` |

`spi` and `security_param_index` carry the same value, as do
`sdls_counter` and `initialization_vector`: the IV is the SDLS counter.
Any other keys already under `sdls` are kept. The MAC stays in the
payload; `sdls_authentication_verify` takes it from the last 16 bytes
when no `sdls.security_trailer` is given.

## Parameters

None. The header layout is fixed: the SPI width (`SPI_BYTES`) and the IV
width (`SDLS_COUNTER_BYTES`) from `sdls_constants`, the same widths
`sdls_header` uses.

## Behavior / edge cases / current error handling

**Parsing** (`parse_header`): the first 2 bytes are the SPI, the next 2
the IV, both big-endian. The rest of the payload is published as it is,
also when it is empty.

**Error handling** (catch-log-drop,
[ADR-0003](../adr/0003-message-handler-error-policy.md)): a non-PDU, a
non-dict metadata, a non-u8vector payload, a payload shorter than the
security header, or any failure while parsing or publishing is dropped
and logged at `error`. On RX this block sits after the frame decoder and
the encapsulation parser have checked the structure, so malformed input
is no longer plausibly channel noise
([coding-standards.md](../coding-standards.md)).

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol): the Security Header
(SPI, IV).

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_sdls_header_parser.py` — 4 test methods
  (`test_001`–`test_004`): a header built by `sdls_header` parsed back to
  its SPI and counter with the rest of the payload unchanged and the
  input metadata kept (`test_001`), other keys under `sdls` kept
  (`test_002`), malformed input and a payload shorter than the header
  dropped (`test_003`), and a publish failure caught (`test_004`).
- `python/soarr/qa_tm_sdls_chain.py` — 2 test methods: the whole TM chain
  above, from `data_creator` to the decrypted data, with the metadata
  cleared between the frame generator and the frame decoder as on a real
  link, returning the original payload in each SDLS mode (`test_001`),
  and a frame altered on the way dropped by the MAC check (`test_002`).
