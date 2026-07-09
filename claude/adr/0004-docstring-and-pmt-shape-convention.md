# 0004 — Docstring and PMT shape convention

**Status:** Accepted

Every block's docstring today is `gr_modtool`'s unfilled placeholder
(`"""docstring for block X"""`). Google-style docstrings for every method
(rejected: NumPy/Sphinx style — Google reads directly in source without a
rendering step, and this project has no Sphinx build), split by method
category: `__init__` and private helpers keep standard
`Args`/`Returns`/`Raises`; message handlers — which never let an
exception escape (per [0003](0003-message-handler-error-policy.md)) and
never meaningfully return a value — use `Publishes`/`Drops when` instead
of `Returns`/`Raises`. Full treatment is required for `__init__`,
handlers, and any PMT-touching method; a one-line summary is enough for
trivial private helpers.

**Handler template:**

```
Args:
    msg (pmt_pair): PDU shape table for the "in" port.

Publishes:
    "out" (pmt_pair): PDU shape table for a successful result.

Drops when:
    - <condition> (<log level> — <why>)
```

**PDU dict shape** — nested bullets under `Args`/`Publishes`, not a
rendered table:

```
Args:
    msg (pmt_pair): PDU with metadata dict and u8vector payload.
        Metadata keys:
            packet_id (int): unique identifier assigned upstream.
        Payload (bytes): raw CCSDS TC frame, BCH-encoded and scrambled.
```

Units are stated only when not already obvious from the key name.
