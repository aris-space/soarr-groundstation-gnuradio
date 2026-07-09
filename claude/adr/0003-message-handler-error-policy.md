# 0003 — Message-handler error policy

> *Message handlers never raise — any exception, from input-shape validation to an unexpected internal bug, is caught, logged, and the message is dropped. Constructor/parameter validation is exempt and stays fail-fast.*

- **Status:** Accepted
- **Date:** 2026-07-07
- **Deciders:** Yannick Kulli

## Context

Today's blocks use two incompatible error styles for the same situation
(receiving a malformed input PDU). `bchDecoder.error_correction_mode` logs a
warning and then `raise ValueError(...)`. `bchEncoder.encodeBCH` and
`ccsdsReceiver.handle_message` log an error and `return` without raising.
Both patterns coexist across the codebase with no documented rule for which
applies where.

This matters beyond style: a message handler runs on GNU Radio's async
message-passing thread, invoked via `set_msg_handler`. This ground station is
meant to run autonomously through a satellite pass; there is no supervisor
that restarts a crashed handler or flowgraph. An uncaught exception on that
thread does not fail gracefully — it can take down the block, or the whole
flowgraph, for the remainder of the pass.

## Decision

No message handler ever raises. Each handler's body is wrapped so that any
exception — whether from explicit input-shape validation (`pmt.is_pair`,
`pmt.is_u8vector`, `pmt.is_dict` checks) or from an unexpected internal bug
during processing — is caught, logged, and the message is dropped (the
handler returns without publishing on any output port). This is intentionally
broad: it is not limited to input-validation failures. A bug triggered by one
malformed frame must not be allowed to crash the handler and silently kill
every subsequent frame for the rest of the pass.

**Log level depends on pipeline position, not on which check failed:**

- **`warn`** — blocks that sit before any structural/integrity check has
  passed, directly exposed to raw, potentially noisy RF data:
  `cltuDeframer`, `bchDecoder`, `lfsrDescrambler`, `ccsdsReceiver`,
  `ccsdsReader`. Malformed input here is plausibly routine channel noise,
  not a bug.
- **`error`** — everything else, including all TX-path blocks and every
  RX-path block from `sdlsAuthenticationVerify`/`sdlsDecryption` onward.
  Once a frame has cleared `ccsdsReader` (structurally valid, successfully
  parsed), malformed input downstream is no longer plausibly channel noise —
  it indicates a real protocol/logic mismatch (e.g. SDLS type
  misconfiguration) and deserves attention.

This is a fixed classification per block (based on its position in the
confirmed pipeline — see `claude/architecture.md`), not a per-call-site
judgment made while writing each check.

**Explicit exemption:** constructor/parameter validation (`__init__`, e.g.
`ccsdsReceiver`'s `raise ValueError` on an invalid `message_type`) is *not*
covered by this policy and keeps raising. It runs synchronously at
flowgraph-construction time on the main thread, not the async
message-handler thread this policy targets — failing fast there is correct:
GRC surfaces the exception immediately, and a misconfigured block should
refuse to build rather than silently limp along.

No dedicated error-signaling output port is introduced. Downstream error
visibility relies on `self.logger` output plus `systemTester`'s existing
out-of-band end-to-end payload comparison (`systemTester` already detects
lost/corrupted messages by comparing original vs. received payloads at the
end of the pipeline, independent of any per-block error signal — confirmed
in `python/soarr/systemTester.py`).

## Alternatives considered

- **Keep current mixed styles** — Rejected; this is the status quo problem
  being solved, with no discoverable rule for which style applies where.
- **Raise everywhere, let the flowgraph/process crash** — Rejected. There is
  no supervisor to restart a crashed handler or flowgraph in this system; a
  single malformed frame would end an entire ground-station pass instead of
  losing one message.
- **Narrow policy: log-and-drop only for explicit input-shape validation,
  let genuine internal bugs raise and crash loudly** — Considered, for the
  debugging convenience of a loud crash during development. Rejected for
  the same operational-continuity reason as above: an internal bug is just
  as capable of ending a pass as a validation failure, and the handler
  can't reliably tell in advance which kind of exception it will hit.

## Consequences

- Every message handler needs a top-level try/except (or equivalent)
  wrapping its full body. This ADR records the policy; applying it across
  all 20 blocks is deferred to future work, driven per-block by each
  block's PRD (same deferral pattern as [ADR-0001](0001-block-naming-convention.md)).
- Debugging a dropped message requires reading logs — there is no per-message
  error signal published anywhere in the flowgraph itself. Acceptable for
  now since `systemTester` already provides independent, out-of-band
  end-to-end error accounting; a structured per-block error/stats channel
  could be considered later but is out of scope here.
- The `warn`/`error` split is a per-block classification tied to pipeline
  position (see `claude/architecture.md`), not something each block decides
  independently — adding a new block requires placing it in the pipeline
  first, which then determines its log level under this policy.
- Constructor validation is unaffected by this ADR and continues to raise;
  readers should not assume "no raises anywhere" — only "no raises from a
  message handler."

---

_Write an ADR only when the decision is (1) hard to reverse, (2) surprising
without context, and (3) the result of a real trade-off. If any of the three is
missing, it probably belongs in a doc or in CONTEXT.md, not here._
