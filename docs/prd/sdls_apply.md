# sdls_apply

## Purpose

SDLS ApplySecurity (CCSDS 355.0-B-1), the sending end of SDLS, as one
hierarchical block: `sdls_encryption`, then `sdls_authentication`, then
`sdls_header`, wired in that fixed order. GRC label **SDLS Apply**,
category `[Soarr]/Common/SDLS` (TM will use SDLS too). See
[ADR-0007](../adr/0007-grc-block-tree-and-labels.md) for the hierarchical
block rule and [architecture.md](../architecture.md) for the chain.

## Pipeline position

TX chain, in place of the three SDLS blocks:

```
encapsulation_header.out → sdls_apply.in
sdls_apply.out → tc_primary_header.in
```

The three blocks stay in the tree for debugging and custom chains.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`, as `sdls_encryption` takes it: metadata with `crypt_key`/`auth_key` (the ones the mode uses), `spi` and `sdls_counter`, top level or nested (see the three blocks' PRDs). | `pmt.cons({crypt_key: "0011…", auth_key: "FFEE…", spi: 1, sdls_counter: 0x1234}, u8vector(payload))` |
| `out` | output | PDU: `(metadata_dict . header‖payload[‖mac])` — the SDLS security header (`spi`, `sdls_counter`), the (encrypted) payload, and the 16-byte MAC if the mode authenticates. Both keys are removed from the metadata. | `pmt.cons({…}, u8vector(header ‖ ciphertext ‖ mac))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `mode` | int | `3` | `1` = encryption only, `2` = authentication only, `3` = both — the same values as `ccsds_reader`'s `sdls_type`, so TX and RX settings match one to one. There is no "none": without SDLS the block is left out. Any other value raises `ValueError`. |
| `nonce` | bytes | `b"\x00" * 14` | The fixed 14-byte nonce both the encryption and the MAC use (never transmitted; RX needs the same value). Validated by the inner blocks (`TypeError`/`ValueError`). GRC takes it as the integer `nonce_hex`, like the individual SDLS blocks. |

The IV width is not a parameter: `sdls_header` keeps its default,
`sdls_constants.SDLS_COUNTER_BYTES`.

## Behavior / edge cases / current error handling

`mode` switches `sdls_encryption`'s `encryption_state` (on for `1`/`3`)
and `sdls_authentication`'s `authentication_state` (on for `2`/`3`); a
step that is off passes the payload through and still removes its own
key. `sdls_header` always runs. Every message-handling rule — what each
step drops, and at which log level — is the inner blocks' own (see
[sdls_encryption.md](sdls_encryption.md),
[sdls_authentication.md](sdls_authentication.md),
[sdls_header.md](sdls_header.md)); this block adds no handler of its own,
only the wiring. The inner blocks are reachable as `encryption`,
`authentication` and `header`.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — ApplySecurity is
the standard's name for the sending-end function: encryption,
authentication and the security header on a frame's data field.

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_sdls_apply.py` — 5 test methods (`test_001`–`test_005`):
  each mode, fed through the block's own `in` port in a running
  flowgraph with a non-zero nonce, producing the same payload and
  metadata as the three blocks called one after another (`test_001`
  both, `test_002` encryption only, `test_003` authentication only), both
  keys removed (`test_004`), and modes `0`/`4` raising `ValueError`
  (`test_005`).
