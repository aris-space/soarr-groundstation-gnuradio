# 0003 — Message-handler error policy

**Status:** Accepted

No message handler raises — GNU Radio's async message-handler thread has
no supervisor, so any uncaught exception (input validation *or* an
internal bug) can kill the whole flowgraph mid-pass. Every handler wraps
its full body in catch-log-drop instead, intentionally covering internal
bugs too, not just input-shape checks (rejected: catching only
input-validation failures and letting internal bugs crash loudly, since a
handler can't reliably tell in advance which kind of exception it will
hit). Constructor/parameter validation (`__init__`) is exempt and still
raises — it runs synchronously at flowgraph-build time, not on the
message-handler thread this policy targets. See
[coding-standards.md](../coding-standards.md) for the warn/error log-level
classification.
