# 0004 — Docstring and PMT shape convention

> *Google-style docstrings for every block, with a three-way split by method category, plus a required nested-bullet convention for documenting PDU dict shape (keys/types/units) wherever a block consumes or produces messages.*

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Yannick Kulli

## Context

Every block's class docstring today is scaffolding boilerplate
(`"""docstring for block X"""`, unchanged from `gr_modtool newmod`'s
generated template). Most blocks pass structured PMT dicts through message
ports rather than plain typed values, so a conventional `Args: msg (object)`
line documents nothing useful — the actual contract is the shape of the
dict inside the PDU, which today exists only as tribal knowledge or must be
reverse-engineered from reading each block's body.

Blocks also have three genuinely different method shapes that a single
docstring template can't serve equally well:

- `__init__` — a normal constructor; raises on bad parameters and is exempt
  from [ADR-0003](0003-message-handler-error-policy.md)'s no-raise policy.
- Message handlers (registered via `set_msg_handler`) — per ADR-0003, never
  let an exception escape, and never meaningfully "return" a value (always
  `None`); their real output is conditionally publishing to an output port.
- Private helper methods — plain functions, called internally by a handler,
  that raise and return normally; it's the calling handler's job (per
  ADR-0003) to catch what they raise.

## Decision

Google-style docstrings (not NumPy/Sphinx style — more readable directly in
source without a rendering step, and simpler to write without RST
field-list syntax) for every block, split three ways by method category:

- **`__init__` and private helper methods** — standard Google-style
  `Args`/`Returns`/`Raises`, unchanged. Full treatment is required for
  `__init__` and any method that directly touches a PMT/PDU; a one-line
  summary (or no docstring at all, if the name is unambiguous) is
  acceptable for trivial private helpers with no PMT involvement (e.g.
  `bchDecoder._bytes_to_bits`).
- **Message handler methods** — `Returns`/`Raises` are replaced with two
  handler-specific sections:

  ```
  Args:
      msg (pmt_pair): PDU shape table for the input port.

  Publishes:
      "<port_name>" (pmt_pair): PDU shape table for a successful result.

  Drops when:
      - <condition> (<log level> — <why>)
      - <condition> (<log level> — <why>)
  ```

  `Drops when` documents the exact conditions under which
  ADR-0003's catch-and-log-and-drop policy fires for this handler, and at
  which log level, per ADR-0003's `warn`/`error` classification.

**PDU dict shape convention:** wherever a docstring references a PDU
(`Args: msg`, or a `Publishes:` entry), its metadata dict is documented as a
nested bullet list of keys, not a rendered table:

```
Args:
    msg (pmt_pair): PDU with metadata dict and u8vector payload.
        Metadata keys:
            packet_id (int): unique identifier assigned upstream by dataCreator/Injectdb.
            scid (int, optional): spacecraft ID; present only if SCID filtering is enabled.
        Payload (bytes): raw CCSDS TC frame, BCH-encoded and scrambled.
```

Units are stated explicitly only when not already obvious from the key name
(`iv (bytes): 8-byte SDLS initialization vector` needs the "8-byte";
`timeout_s (float): timeout` doesn't need to restate "seconds").

## Alternatives considered

- **Keep current placeholder docstrings** — Rejected; this is the stated
  problem (`"""docstring for block X"""` on every block documents nothing).
- **NumPy/Sphinx-style docstrings** — Rejected in favor of Google style.
  Google style reads directly as plain text in source without needing a
  rendering step or RST field-list syntax (`:param:`, `:type:`), which
  matters more here than Sphinx-ecosystem compatibility, since this project
  has no Sphinx build today.
- **One uniform docstring template for every method regardless of
  category** — Rejected. A message handler's `Returns`/`Raises` would
  either be permanently empty (misleading — implies "nothing happens on
  error" instead of "logs and drops") or would have to describe
  ADR-0003's catch-all behavior on every single handler's docstring
  redundantly; a dedicated `Publishes`/`Drops when` shape says what
  actually happens without repeating ADR-0003's policy each time.

## Consequences

- All 20 blocks' docstrings need rewriting to this convention — deferred to
  future work, driven per-block by each block's PRD (same deferral pattern
  as [ADR-0001](0001-block-naming-convention.md) and
  [ADR-0003](0003-message-handler-error-policy.md)).
- A handler's `Drops when` section only stays accurate if kept in sync with
  its actual implementation as ADR-0003's try/except wrapping is applied —
  writing the docstring and applying the error-handling change should happen
  in the same pass per block, or the docstring risks describing conditions
  the code doesn't actually check yet (or vice versa).
- Documenting PDU dict shapes only from reading current code risks baking in
  today's *accidental* shape rather than the *intended* one, particularly
  for the RX-path blocks whose SDLS ordering is still flagged as open in
  `architecture.md` — those PRDs should confirm the real shape while writing
  the docstring, not just transcribe whatever keys happen to appear in the
  code today.

---

_Write an ADR only when the decision is (1) hard to reverse, (2) surprising
without context, and (3) the result of a real trade-off. If any of the three is
missing, it probably belongs in a doc or in CONTEXT.md, not here._
