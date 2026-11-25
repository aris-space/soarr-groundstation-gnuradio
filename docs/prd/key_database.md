# key_database

## Purpose

The key injector and key store as one hierarchical block: `inject_db`
and `db_client`, with the `db_call`/`db_callback` query loop between them
wired inside. Each PDU on `in` comes out on `out` with its entry's keys
and counters merged into the metadata. GRC label **Key Database**,
category `[Soarr]/Common/Key Database` (TM will use the same keys). See
[ADR-0007](../adr/0007-grc-block-tree-and-labels.md) for the hierarchical
block rule.

## Pipeline position

One instance per chain, in place of the `inject_db` + `db_client` pair:

```
TX: data_creator.out → key_database (role "tx").in
    key_database.out → encapsulation_header.in
RX: ccsds_reader.debug → key_database (role "rx").in
    key_database.out → sdls_process.in
```

The two blocks stay in the tree for setups the minimal parameter set
below doesn't cover (e.g. custom dummy keys, or a separately placed store).

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU as `inject_db` takes it: metadata with `scid`/`spi`/`bypass`/`control`, top level or nested (see [inject_db.md](inject_db.md)). | `pmt.cons({telecommand: {tc_header: {scid: 341, …}}, sdls: {security_header: {spi: 1}}}, u8vector(payload))` |
| `out` | output | The same PDU with the entry's `crypt_key`/`auth_key`, `vcid` and counters merged in (TX: the store's counters; RX: the frame's kept). | `pmt.cons({…, crypt_key: "0011…", auth_key: "FFEE…"}, u8vector(payload))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `role` | str | `"tx"` | `"tx"` or `"rx"`, passed to `inject_db` (who owns the counters; RX lookups don't count, see [db_client.md](db_client.md)). Anything else raises `ValueError`. |
| `db_type` | int | `0` | `0` = dummy entry with the built-in test keys, `1` = YAML file. The remote database (`db_client` type `2`) isn't implemented and isn't offered; any other value raises `ValueError`. |
| `yaml_path` | str | `""` | The YAML file for `db_type=1` (see `examples/db_client_example.yaml`). |
| `auto_reset_counters` | bool | `False` | Dummy entry only: restart the SDLS counter after its maximum, for long test runs. `db_client` refuses it with anything but the test keys. |
| `scid`, `spi`, `vcid` | int | `0x155`, `1`, `0x12` | The dummy entry's SCID, SPI and VCID. |

Everything else keeps `db_client`'s default: the test keys, counters
starting at `0`, active key states, `forward_body`, and the unused remote
IP/port. Real keys come from a YAML file, not from block parameters.

## Behavior / edge cases / current error handling

The block adds no handler of its own, only the wiring; every lookup,
merge and drop rule is `inject_db`'s and `db_client`'s (see
[inject_db.md](inject_db.md), [db_client.md](db_client.md)). Because
`inject_db` tags each query with its role, an RX instance never uses up
the store's SDLS counter. The inner blocks are reachable as `injector`
and `store`.

## CCSDS reference

None directly — key and counter management supporting CCSDS 355.0-B-1
SDLS (the SPI selects the security association, the counter is the
transmitted IV).

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_key_database.py` — 4 test methods (`test_001`–`test_004`),
  each feeding the block's own `in` port in a running flowgraph: TX
  merging the dummy entry's test key with the store's counter advancing
  `0, 1` across two messages, so the loop works inside (`test_001`), RX
  keeping the frame's counter (`test_002`), the YAML database serving the
  example file's second entry with its starting counter (`test_003`),
  and an unimplemented `db_type` or invalid role raising (`test_004`).
