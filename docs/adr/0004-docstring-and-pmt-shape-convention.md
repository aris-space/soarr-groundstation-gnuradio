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
of `Returns`/`Raises`. See [coding-standards.md](../coding-standards.md)
for the handler docstring template and the PDU dict shape convention.
