# 0005 — RX path canonical block

**Status:** Accepted

`ccsds_receiver` imports `bch_decoder`/`lfsr_descrambler` directly and
implements frame reassembly and TFPH search found nowhere else — it's the
one documented, recommended RX path. The standalone
`cltu_deframer → bch_decoder → lfsr_descrambler` chain (exercised only by
`qa_lfsr_receive_chain.py`) stays GRC-exposed for isolating physical/FEC-
layer bugs (rejected: removing its GRC exposure, since that would make
this kind of isolated debugging harder for no benefit), but is explicitly
not a substitute RX path — confirmed incomplete, it never reaches frame
reassembly or TFPH search and can't decode a full CCSDS TC frame on its
own. No code changes result from this ADR; it governs what
`architecture.md` and the block PRDs say, not implementation.
