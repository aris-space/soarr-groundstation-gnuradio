# Coding standards

The current, practical rules for writing code in gr-soarr — naming, error
handling, and docstrings. This is "what to do"; each linked ADR is "why we
decided it."

**Naming is applied; error handling and docstrings are not yet.** All 20
blocks use the snake_case naming below. Message-handler error handling
still uses each block's original mixed raise/log-and-drop style, and
docstrings are still `gr_modtool`'s placeholders — those two rules are the
target for each block's future cleanup pass (Step 4 of the parent plan,
driven per-block by `claude/prd/<block>.md`).

## Block naming

File name, class name, and GRC block-id suffix (after the `soarr_`
prefix) are identical, snake_case — e.g. `ccsds_reader.py` (class
`ccsds_reader`, `qa_ccsds_reader.py`, block id `soarr_ccsds_reader`). Each
block's matching `qa_*.py` test file follows the same name.

**Acronym rule:** known multi-letter domain acronyms (CCSDS, SDLS, BCH,
CLTU, TC) collapse to one lowercase token, not letter-by-letter —
`ccsdsReader` → `ccsds_reader`, not `c_c_s_d_s_reader`.

**Rename table** (applied — kept here as the historical old→new mapping,
e.g. for matching an old `.grc` flowgraph or old GRC block-id against its
current name):

| Old | New (current) |
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
  directly exposed to raw, potentially noisy RF data: `cltu_deframer`,
  `bch_decoder`, `lfsr_descrambler`, `ccsds_receiver`, `ccsds_reader`.
- `error` — everything else. Once a frame has cleared `ccsds_reader`
  (structurally valid, successfully parsed), malformed input downstream
  is no longer plausibly channel noise.

No dedicated error-signaling output port — `system_tester` provides
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
            packet_id (int): unique identifier assigned upstream by data_creator/inject_db.
            scid (int, optional): spacecraft ID; present only if SCID filtering is enabled.
        Payload (bytes): raw CCSDS TC frame, BCH-encoded and scrambled.
```

Units are stated explicitly only when not already obvious from the key
name (`iv (bytes): 8-byte SDLS initialization vector` needs the
"8-byte"; `timeout_s (float): timeout` doesn't need to restate
"seconds").

See [ADR-0004](adr/0004-docstring-and-pmt-shape-convention.md) for why.

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

Enforced by a `commit-msg` hook — see
[development.md](development.md#commit-message-hook) for one-time setup.

- **atomic commits** — one type per commit. Conventional Commits has no
  way to combine two types in one message (no dual headers, no
  comma-separated types), so a commit spanning two separate concerns
  should be split rather than bundled — e.g. a PRD-writing session that
  also fixes bugs it uncovers splits along the file boundary into a
  `docs` commit (the PRD/doc files) and a `fix`/`refactor` commit (the
  block's code/test files), not one commit covering both. Commit early
  and often rather than batching unrelated work into one commit.
- **type** — one of: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`,
  `perf`, `build`, `ci`.
- **scope** — required for block-level work: the block's snake_case name
  (`tc_primary_header`, `sdls_header`, ...), matching the naming convention
  above. For non-block work, an area name: `docs`, `adr`, `prd`, `tools`,
  `grc`. Omit only when a change has no single coherent scope.
- **subject** — imperative mood, lowercase after `: `, no trailing period,
  ≤72 chars, single clause. If a change doesn't fit in one clause, split it
  into multiple commits, or move the extra detail into the body — don't
  chain it onto the subject with `;`. Name the resulting change itself
  (what's different after the commit), not the activity that produced it
  — `remove unused mask param, fix crash on bad input`, not `close two
  /code-review rounds of ADR-0003/0004 gaps`. Process narration (review
  rounds, standard/ADR names, TDD steps) belongs in the body's rationale
  at most, per the body rule below — never in the subject.
- **body** — free text. Conventional Commits itself only defines the
  structure above (the spec's body is explicitly free-form); what goes in
  it is this project's own rule: state **what changed and why** (motivation,
  the problem being solved, a decision and its rationale) — not **how** it
  was found or fixed. Leave out `/code-review`-round narration, test
  pass/fail tallies, and TDD red/green step commentary; that detail belongs
  in the PR/session, not the permanent log. Describe only what this
  commit actually contains — not work planned for a later commit, and not
  an external plan/roadmap's phase or step number. The commit log outlives
  any particular planning document or session, so a message should be
  self-explanatory to a reader who has neither.
- **footer** (optional) — `Refs: <path>` pointing at the relevant doc (e.g.
  `Refs: claude/prd/tc_primary_header.md`), and/or `BREAKING CHANGE: <desc>`
  for breaking changes (block-id rename, changed PDU shape).

Example:

```
fix(tc_primary_header): validate frame_length against its bit width

frame_length was masked instead of validated, so an out-of-range value
silently wrapped (e.g. 1024 -> 0) and still published. Replaced masking
with an explicit range check, raising the same way the other three
packed fields already do.

Refs: claude/prd/tc_primary_header.md
```
