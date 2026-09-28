# Architecture

Pipeline diagrams and block roles for gr-soarr. Domain terms (TC, CLTU, BCH,
SDLS, SPI, IV, etc.) follow their CCSDS definitions. Per-block detail
(message ports, PDU shapes, parameters, edge cases) lives in
[prd/](prd/), one file per block.

**Naming note:** every block below uses its current snake_case name, per
[coding-standards.md](coding-standards.md) ([ADR-0001](adr/0001-block-naming-convention.md)).

## Legend

| Style | Meaning |
|---|---|
| Plain box | Message-passing block (`gr.basic_block`, PDU in/out) — the default shape for this module |
| Box tagged "(stream block)" | The one block with a real `out_sig` (`gr.sync_block`) |
| Box tagged "(stock block)" | Stock GNU Radio block, not part of gr-soarr |
| Dashed connector | Internal primitive path — GRC-exposed for testing, not part of the public pipeline ([ADR-0005](adr/0005-rx-path-canonical-block.md)) |

## TX chain

Confirmed via `python/soarr/qa_tx_chain.py`'s `msg_connect` wiring
(lines 89–101), and independently confirmed end-to-end by a working
external flowgraph (`CCSDS_Full.grc` — see Known Gaps):

```mermaid
flowchart LR
    dbClient["db_client"]
    Injectdb["inject_db"]
    encapsulationHeader["encapsulation_header"]
    sdlsEncryption["sdls_encryption"]
    sdlsAuthentication["sdls_authentication"]
    sdlsHeader["sdls_header"]
    tcPrimaryHeader["tc_primary_header"]
    crcAppend["digital.crc_append (stock block)"]
    lfsrScrambler["lfsr_scrambler"]
    bchEncoder["bch_encoder"]
    cltuFramer["cltu_framer"]
    aqusitionIdleSequencer["acquisition_idle_sequencer (stream block)"]

    Injectdb -. "db_call / db_callback (key/SPI lookup)" .-> dbClient
    Injectdb -- out --> encapsulationHeader
    encapsulationHeader --> sdlsEncryption
    sdlsEncryption --> sdlsAuthentication
    sdlsAuthentication --> sdlsHeader
    sdlsHeader --> tcPrimaryHeader
    tcPrimaryHeader --> crcAppend
    crcAppend --> lfsrScrambler
    lfsrScrambler --> bchEncoder
    bchEncoder --> cltuFramer
    cltuFramer --> aqusitionIdleSequencer
```

`db_client` is a query/response side-channel off `inject_db` (`db_call`/
`db_callback` ports) — not a parallel input into the main chain. Only
`inject_db`'s `out` port feeds `encapsulation_header`.

Order is encrypt-then-authenticate (`sdls_encryption` before
`sdls_authentication`) — confirmed, not open.

`acquisition_idle_sequencer` bridges `cltu_framer`'s PDU output to a
continuous byte stream for the SDR/modulator downstream. It's the only
stream block in this module (`gr.sync_block`, `out_sig=[np.uint8]`) —
every other block above is `gr.basic_block`, message-passing only.

## RX chain

```mermaid
flowchart LR
    cltuDeframer["cltu_deframer"]
    ccsdsReceiver["ccsds_receiver (internal: BCH-decode, descramble,<br/>TFPH search, frame reassembly, dispatch)"]
    crcCheck["digital.crc_check (stock block)"]
    ccsdsReader["ccsds_reader"]
    InjectdbRx["inject_db (RX instance)"]
    dbClientRx["db_client (RX instance)"]
    sdlsAuthenticationVerify["sdls_authentication_verify"]
    sdlsDecryption["sdls_decryption"]

    bchDecoder["bch_decoder"]
    lfsrDescrambler["lfsr_descrambler"]

    cltuDeframer -- stream in --> ccsdsReceiver
    ccsdsReceiver --> crcCheck
    crcCheck --> ccsdsReader
    ccsdsReader -- "debug (its real output port)" --> InjectdbRx
    InjectdbRx -. "db_call / db_callback (key/SPI lookup)" .-> dbClientRx
    InjectdbRx --> sdlsAuthenticationVerify
    sdlsAuthenticationVerify --> sdlsDecryption

    cltuDeframer -.-> bchDecoder
    bchDecoder -.-> lfsrDescrambler
```

`ccsds_receiver` is the one canonical, documented RX path
([ADR-0005](adr/0005-rx-path-canonical-block.md)) — it imports
`bch_decoder` and `lfsr_descrambler` directly as Python helpers and calls
them internally, alongside frame reassembly and TFPH search logic that
exists nowhere else.

`digital.crc_check` sits between `ccsds_receiver` and `ccsds_reader`,
checking the FECF — the RX-side counterpart to TX's `digital.crc_append`.
It has two output ports, `fail` and `ok`; in the confirmed external
flowgraph both are wired to `ccsds_reader`'s input. That's how the
flowgraph was left, not a documented design decision — don't read
architectural intent into it.

`ccsds_reader`'s output port is named `debug`, but it's actually its real,
only output — not a diagnostic-only tap (confirmed: it's wired onward to
the next block in the working flowgraph, not just to a debug sink).

A second `inject_db`/`db_client` pair sits between `ccsds_reader` and
`sdls_authentication_verify`, mirroring the TX-side key lookup —
presumably fetching the SDLS key material needed for verification and
decryption.

The dashed `cltu_deframer → bch_decoder → lfsr_descrambler` path (bottom)
is a separate, standalone GRC-wireable chain, exercised only by
`qa_lfsr_receive_chain.py`. It is **not** a substitute RX path — it stops
at the physical/FEC layer and never reaches frame reassembly or TFPH
search. It stays GRC-exposed as an internal primitive, useful for isolating
bugs at exactly that layer.

### SDLS verify-then-decrypt order — confirmed

The order shown above (`sdls_authentication_verify` before
`sdls_decryption`) is required by the crypto construction:
`sdls_authentication_verify`'s CMAC tag is computed over the
still-encrypted bytes; `sdls_decryption`'s AES-CTR transforms those same
bytes. Decrypting first would break tag verification for any real payload.

This order is also confirmed by direct wiring in a working external
flowgraph (`sdlsAuthenticationVerify.out → sdlsDecryption.in`, using the
pre-rename block names it was built with) — not just by the crypto
argument. That flowgraph isn't in this repo yet (see Known Gaps), so there
is still no in-repo `.grc` example or QA test proving this order; treat it
as confirmed-in-practice but not yet self-verifying from this repo alone.

## Ground-tooling (not signal chain)

- **`system_tester`** — round-trip verification. Compares original vs.
  received payloads out-of-band, at the end of the pipeline; it does not
  sit inline in either chain and has no per-block error-signal port to
  depend on.
- **`data_creator`** — synthetic payload generator, an alternative to the
  `inject_db`/`db_client` DB-backed path for generating TX payloads. Only
  one of the two is needed at a time, not both.

## Known gaps

- No example `.grc` flowgraph exists **in this repo** for the full TX or
  RX chain (`examples/db_client_example.yaml` is a YAML *data* file for
  `db_client`'s type=1 config mode, not a flowgraph). A working example
  does exist outside the repo (`CCSDS_Full.grc`, built with this OOT
  module, plus `DataFlow_TX.drawio.xml`/`DataFlow_RX.drawio.xml`) and is
  intended to become an in-repo example — it currently uses pre-rename
  `sage_*` block IDs *and* pre-ADR-0001 camelCase block names, so it needs
  updating on both counts before it can be added.
