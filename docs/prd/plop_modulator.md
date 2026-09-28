# plop_modulator

## Purpose

BPSK-modulates CLTUs into the SDR's sample stream in either CCSDS
231.0-B physical layer operations procedure, switchable while the
flowgraph runs: PLOP-1 (one burst per CLTU, transmitter off in between)
or PLOP-2 (continuous carrier with the idle sequence). The low-latency
end of the TX chain. See [architecture.md](../architecture.md).

## Pipeline position

TX chain, the last block before the SDR:

```
cltu_framer.out → plop_modulator.in
plop_modulator.out → USRP sink (Length Tag Name empty; bursts from tx_sob/tx_eob)
(mode control, optional) → plop_modulator.mode
```

Wired this way in `examples/tc_tx_usrp.grc` and
`tc_loopback_usrp.grc`, and in `tc_loopback_plop_sim.grc`, where a
throttle standing in for the SDR clock and a channel model feed the
receiver's DSP chain instead.

## Message ports / stream port

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input (message) | PDU: `(metadata_dict . cltu_u8vector)`, one CLTU. Metadata is ignored. | `pmt.cons({}, u8vector(eb 90 ‖ codewords ‖ tail))` |
| `mode` | input (message, optional) | integer `1` (PLOP-1) or `2` (PLOP-2). | `pmt.from_long(1)` |
| `out` | output (stream) | `complex64` baseband at `samples_per_symbol` × symbol rate, with `tx_sob` on each carrier start and `tx_eob` on each carrier end. | — |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `mode` | int | `2` | `1` = PLOP-1, `2` = PLOP-2. Runtime-settable via `set_mode()` (GRC callback, e.g. a QT chooser) or the `mode` port. Raises `ValueError` otherwise. |
| `samples_per_symbol` | int | `40` | Samples per BPSK symbol (sample rate / symbol rate). Raises `ValueError` below `2`. |
| `excess_bw` | float | `0.35` | Root-raised-cosine roll-off. Raises `ValueError` outside `(0, 1]`. |
| `differential` | bool | `True` | Differential encoding (NRZ-M), matching a differential BPSK receiver. |
| `acquisition_length` | int | `64` | Acquisition sequence bytes at every carrier start — size it to the on-board receiver's lock time. |
| `tail_length` | int | `4` | Idle bytes before the carrier ends. |
| `fill_byte` | int | `0xAA` | Acquisition/idle/tail byte; with `differential` it is `0xFF`, which becomes alternating symbols on the channel. |
| `amplitude` | float | `1.0` | Output amplitude of a full-scale symbol. |

## Behavior / edge cases / current error handling

**Modulation** (`_modulate`/`_filter`): bytes → bits (MSB first) →
optional differential encoding → BPSK symbols (bit `0` → −1, `1` → +1,
as GNU Radio's `constellation_bpsk`) → zero-stuffed by
`samples_per_symbol` → root-raised-cosine FIR (`11 × sps + 1` taps,
normalised to unit gain). The differential state and the filter history
carry over from call to call, so the acquisition sequence, idle chunks,
and CLTUs form one continuous waveform: inserting a CLTU or switching
mode is phase-continuous.

**PLOP-2**: the carrier starts with `tx_sob` and the acquisition
sequence, then idles in 8-byte chunks; a queued CLTU is modulated at the
next chunk boundary (a byte boundary). Idle is only generated for as
many samples as `work()` is asked for, so the CLTU waits behind at most
the current sample buffer.

**PLOP-1**: with no CLTU queued the block produces nothing (it waits up
to 10 ms for a CLTU or mode change, then returns 0). Each CLTU becomes
one burst: `tx_sob`, acquisition sequence, CLTU, idle tail, the filter's
ring-out (`flush_length` samples), `tx_eob`; the modulator state is
reset for the next burst.

**Switching** (`set_mode`/`handle_mode`): PLOP-2 → 1 ends the running
carrier after its current chunk with the idle tail, ring-out, and
`tx_eob`; PLOP-1 → 2 starts a carrier with `tx_sob` and the acquisition
sequence. A PLOP-1 burst in progress is always finished first.

**Error handling** (compliant with
[coding-standards.md](../coding-standards.md),
[ADR-0003](../adr/0003-message-handler-error-policy.md)): both message
handlers are wrapped in catch-log-drop, logged at `error` (TX side). A
non-PDU, a non-u8vector payload, or an empty CLTU on `in` is dropped; a
non-integer or out-of-range `mode` message is dropped and the mode is
unchanged.

**Docstrings** (compliant with
[ADR-0004](../adr/0004-docstring-and-pmt-shape-convention.md)):
`Args`/`Raises` for `__init__` and `set_mode`, `Args`/`Publishes`/`Drops
when` for both handlers, `Args`/`Returns` for `work`.

**Naming**: file, class, GRC block-id, every parameter, and the handler
methods are snake_case.

## CCSDS reference

CCSDS 231.0-B (TC Synchronization and Channel Coding), physical layer
operations procedures: PLOP-1 switches the carrier off between
transmissions, each starting with an acquisition sequence of alternating
ones and zeros; PLOP-2 keeps the carrier on, with idle sequences between
CLTUs.

## Known issues / TODOs

None currently. Transmission over a real USRP, and the acquisition
length the SOARR on-board receiver needs, are verified in the lab, not by
the test suite.

## Test coverage

- `python/soarr/qa_plop_modulator.py` — 9 test methods (`test_instance`
  + `test_001`–`test_008`): PLOP-1 bursts with `tx_sob`/`tx_eob` and
  nothing between them (`test_001`), a PLOP-1 burst demodulating to
  acquisition + CLTU + tail with an ideal matched-filter receiver
  (`test_002`), a PLOP-2 carrier starting with the acquisition sequence
  and carrying an inserted CLTU framed by idle (`test_003`), PLOP-2 → 1
  ending the carrier with `tx_eob` as the last sample (`test_004`),
  PLOP-1 → 2 starting it (`test_005`), `set_mode` matching the message
  port (`test_006`), invalid parameters raising (`test_007`), and invalid
  messages dropped with the mode unchanged (`test_008`).
- `examples/tc_loopback_plop_sim.grc` — through the receiver's real DSP
  chain (FIR, AGC, FLL, RRC, symbol sync, Costas, differential decoding)
  with noise and a frequency offset, switched PLOP-2 → PLOP-1 → PLOP-2
  while running: every frame intact, no BCH or authentication failures.
