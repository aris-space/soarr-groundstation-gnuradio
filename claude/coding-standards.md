# Coding standards

The current, practical rules for writing code in gr-soarr — naming, error
handling, and docstrings. This is "what to do"; each linked ADR is "why we
decided it."

**None of this is fully applied to the existing 20 blocks yet** — every
block still uses pre-standard camelCase names, mixed raise/log-and-drop
error handling, and placeholder docstrings. These rules are the target for
new code and for each block's rename/cleanup pass (Step 4 of the parent
plan, driven per-block by `claude/prd/<block>.md`).

## Block naming

File name, class name, and GRC block-id suffix (after the `soarr_`
prefix) are identical, snake_case — e.g. `ccsdsReader.py` (class
`ccsdsReader`, `qa_ccsdsReader.py`, block id `soarr_ccsdsReader`) becomes
`ccsds_reader.py` (class `ccsds_reader`, `qa_ccsds_reader.py`, block id
`soarr_ccsds_reader`). Each block's matching `qa_*.py` test file renames
the same way.

**Acronym rule:** known multi-letter domain acronyms (CCSDS, SDLS, BCH,
CLTU, TC) collapse to one lowercase token, not letter-by-letter —
`ccsdsReader` → `ccsds_reader`, not `c_c_s_d_s_reader`.

**Rename table:**

| Current | New |
|---|---|
| `encapsulationHeader` | `encapsulation_header` |
| `sdlsEncryption` | `sdls_encryption` |
| `sdlsAuthentication` | `sdls_authentication` |
| `sdlsHeader` | `sdls_header` |
| `tcPrimaryHeader` | `tc_primary_header` |
| `lfsrScrambler` | `lfsr_scrambler` |
| `bchEncoder` | `bch_encoder` |
| `cltuFramer` | `cltu_framer` |
| `Injectdb` | `inject_db` (its own GRC `name=` field is already "Inject DB") |
| `dbClient` | `db_client` |
| `dataCreator` | `data_creator` |
| `aqusitionIdleSequencer` | `acquisition_idle_sequencer` (typo fix) |
| `cltuDeframer` | `cltu_deframer` |
| `ccsdsReceiver` | `ccsds_receiver` |
| `ccsdsReader` | `ccsds_reader` |
| `sdlsAuthenticationVerify` | `sdls_authentication_verify` |
| `sdlsDecryption` | `sdls_decryption` |
| `systemTester` / `SystemTester` | `system_tester` (fixes the one existing file/class mismatch) |
| `bchDecoder` | `bch_decoder` |
| `lfsrDescrambler` | `lfsr_descrambler` |

See [ADR-0001](adr/0001-block-naming-convention.md) for why.

## Message-handler error handling

No message handler raises. Every handler wraps its full body in
catch-log-drop — this covers internal bugs during processing, not just
input-shape validation (`pmt.is_pair`, `pmt.is_u8vector`, `pmt.is_dict`
checks). On any exception: log it, drop the message (return without
publishing), never let it escape the handler.

`__init__`/constructor parameter validation is the one exception — it
still raises normally, since it runs at flowgraph-build time, not on the
async message-handler thread this rule targets.

**Log level by pipeline position**, not per-call-site judgment:

- `warn` — blocks before any structural/integrity check has passed,
  directly exposed to raw, potentially noisy RF data: `cltuDeframer`,
  `bchDecoder`, `lfsrDescrambler`, `ccsdsReceiver`, `ccsdsReader`.
- `error` — everything else. Once a frame has cleared `ccsdsReader`
  (structurally valid, successfully parsed), malformed input downstream
  is no longer plausibly channel noise.

No dedicated error-signaling output port — `systemTester` provides
independent, out-of-band end-to-end error accounting by comparing
original vs. received payloads.

See [ADR-0003](adr/0003-message-handler-error-policy.md) for why.

## Docstrings and PMT shape

Google-style docstrings for every method, split by category:

- **`__init__` and private helper methods** — standard `Args`/`Returns`/
  `Raises`. Full treatment required for `__init__` and any method that
  directly touches a PMT/PDU; a one-line summary (or no docstring) is
  fine for trivial private helpers with no PMT involvement.
- **Message handler methods** — use `Publishes`/`Drops when` instead of
  `Returns`/`Raises` (a handler never lets an exception escape, per the
  error-handling rule above, and never meaningfully returns a value):

  ```
  Args:
      msg (pmt_pair): PDU shape table for the input port.

  Publishes:
      "<port_name>" (pmt_pair): PDU shape table for a successful result.

  Drops when:
      - <condition> (<log level> — <why>)
      - <condition> (<log level> — <why>)
  ```

  `Drops when` documents the exact conditions that trigger a drop for
  this handler, and at which log level, per the classification above.

**PDU dict shape** — nested bullets under `Args`/`Publishes`, not a
rendered table:

```
Args:
    msg (pmt_pair): PDU with metadata dict and u8vector payload.
        Metadata keys:
            packet_id (int): unique identifier assigned upstream by dataCreator/Injectdb.
            scid (int, optional): spacecraft ID; present only if SCID filtering is enabled.
        Payload (bytes): raw CCSDS TC frame, BCH-encoded and scrambled.
```

Units are stated explicitly only when not already obvious from the key
name (`iv (bytes): 8-byte SDLS initialization vector` needs the
"8-byte"; `timeout_s (float): timeout` doesn't need to restate
"seconds").

See [ADR-0004](adr/0004-docstring-and-pmt-shape-convention.md) for why.
