# sdls_process

## Purpose

SDLS ProcessSecurity (CCSDS 355.0-B-1), the receiving end of SDLS, as one
hierarchical block: `sdls_authentication_verify`, then `sdls_decryption`,
wired in that fixed order. GRC label **SDLS Process**, category
`[Soarr]/Common/SDLS` (TM will use SDLS too). The RX counterpart of
[sdls_apply](sdls_apply.md); see
[ADR-0007](../adr/0007-grc-block-tree-and-labels.md) for the hierarchical
block rule.

## Pipeline position

RX chain, in place of the two SDLS blocks:

```
inject_db (RX instance).out → sdls_process.in
sdls_process.out → encapsulation_parser.in
```

The two blocks stay in the tree for debugging and custom chains.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . payload_u8vector)`, as `sdls_authentication_verify` takes it: metadata with `crypt_key`/`auth_key` (the ones the mode uses) and `sdls_counter`; the MAC either as `sdls.security_trailer` (what `ccsds_reader` publishes) or as the payload's last 16 bytes. | `pmt.cons({crypt_key: "0011…", auth_key: "FFEE…", sdls_counter: 0x1234, sdls: {security_trailer: mac}}, u8vector(ciphertext))` |
| `out` | output | PDU: `(metadata_dict . plaintext_u8vector)`, MAC stripped. Both keys are removed from the metadata. | `pmt.cons({…}, u8vector(plaintext))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `mode` | int | `3` | `1` = encryption only, `2` = authentication only, `3` = both — the same values as `sdls_apply` and `ccsds_reader`'s `sdls_type`. Any other value raises `ValueError`. |
| `nonce` | bytes | `b"\x00" * 14` | The fixed 14-byte nonce TX used for both the encryption and the MAC. Validated by the inner blocks (`TypeError`/`ValueError`). GRC takes it as the integer `nonce_hex`, like the individual SDLS blocks. |

## Behavior / edge cases / current error handling

`mode` switches `sdls_authentication_verify`'s `authentication_state` (on
for `2`/`3`) and `sdls_decryption`'s `decryption_state` (on for `1`/`3`);
a step that is off passes the payload through and still removes its own
key. Verification comes first, so a frame whose MAC fails is dropped
before anything is decrypted. Every message-handling rule — what each
step drops, and at which log level — is the inner blocks' own (see
[sdls_authentication_verify.md](sdls_authentication_verify.md),
[sdls_decryption.md](sdls_decryption.md)); this block adds no handler of
its own, only the wiring. The inner blocks are reachable as
`verification` and `decryption`.

## CCSDS reference

CCSDS 355.0-B-1 (Space Data Link Security Protocol) — ProcessSecurity is
the standard's name for the receiving-end function: verifying the MAC
and decrypting a frame's data field.

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_sdls_process.py` — 6 test methods (`test_001`–`test_006`):
  input built independently with pycryptodome, fed through the block's
  own `in` port in a running flowgraph with a non-zero nonce, recovering
  the original plaintext in each mode (`test_001` both, `test_002`
  encryption only, `test_003` authentication only), a tampered MAC
  dropped in both authenticating modes (`test_004`), both keys removed
  (`test_005`), and modes `0`/`4` raising `ValueError` (`test_006`).
