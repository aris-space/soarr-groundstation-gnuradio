# tm_frame_decoder

## Purpose

Extracts packets from received CCSDS TM Transfer Frames (CCSDS
132.0-B-3): reads each frame's headers, joins the parts of a packet split
across frames back together using the First Header Pointer and the
packet's own length field, skips idle packets, and publishes one PDU per
packet: encapsulation packets on one port, Space Packets on another. The
Operational Control Field of every frame comes out on a third port. The
RX counterpart of [tm_frame_encoder](tm_frame_encoder.md). GRC label
**TM Frame Decoder**, category `[Soarr]/TM (Telemetry)/Frame`.

## Pipeline position

```
TX: encapsulation_header → tm_frame_encoder → satellites.encode_rs → tm_randomizer → tm_cadu_framer → …
RX: (TM CADU Deframer) → tm_randomizer → satellites.decode_rs (gr-satellites) → tm_frame_decoder
    tm_frame_decoder.out → encapsulation_parser
    tm_frame_decoder.space_packets → (application)
    tm_frame_decoder.ocf → (application, e.g. CLCW evaluation)
```

The input is one frame per PDU, as `satellites.decode_rs` publishes it
after correcting the codeblock. A codeblock it can't correct is dropped
there, and this block sees the loss as a gap in the virtual channel frame
count. There's no TM CADU Deframer yet, so nothing upstream of the
Reed-Solomon decoder is in this repo.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . u8vector)`, one TM Transfer Frame. Any length that holds the headers, OCF and FECF. | `pmt.cons({}, u8vector(223 bytes))` |
| `out` | output | One PDU per encapsulation packet (version `111`), the whole packet (header included), so `encapsulation_parser` takes it unchanged. Metadata: that of the frame the packet starts in, plus `scid`, `vcid`, `mc_frame_count`, `vc_frame_count` (int) of that frame, and — only where that frame carries them — `secondary_header` (u8vector, the secondary header's data field) and `ocf` (uint64). | `pmt.cons({scid: 0, vcid: 2, mc_frame_count: 17, vc_frame_count: 5}, u8vector(e2 01 f4 ‖ 497 bytes))` |
| `space_packets` | output | One PDU per Space Packet (version `000`), the whole packet (primary header included), unparsed. Metadata as on `out`. | `pmt.cons({scid: 0, vcid: 2, …}, u8vector(08 64 c0 00 00 0f ‖ 16 bytes))` |
| `ocf` | output | One PDU per frame that carries an OCF, also when its data field holds no packet or only idle data: the frame's metadata plus `scid`, `vcid`, `mc_frame_count`, `vc_frame_count`, and the 4 OCF bytes as payload. | `pmt.cons({scid: 0, vcid: 2, mc_frame_count: 18, vc_frame_count: 6}, u8vector(01 00 00 2a))` |

`scid`, `vcid`, `secondary_header` and `ocf` are the keys
`tm_frame_encoder` reads from its input metadata, so a round trip gives
them back.

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `fecf` | bool | `False` | Frames end with a 2-byte FECF. It is checked (CRC-16/CCITT-FALSE, `fecf.fecf_crc`, as on TX) and removed before the data field is read. Must match the TX setting: the frame doesn't signal whether it has a FECF. |
| `scid_filter_enable`, `scid` | bool, int | `False`, `0` | Keep only frames of this SCID. `scid` outside 10 bits (0–1023) raises `ValueError`. GRC shows `scid` only while the filter is enabled. |
| `vcid_filter_enable`, `vcid` | bool, int | `False`, `0` | Keep only frames of this VCID. `vcid` outside 3 bits (0–7) raises `ValueError`. GRC shows `vcid` only while the filter is enabled. |

The rest of the layout comes from the frame itself: the OCF flag in the
primary header, and the secondary header flag with the length in the
secondary header's identification byte. The data field is what's left of
the frame after those fields.

## Behavior / edge cases / current error handling

**Frame checks** (`decode_frame`): transfer frame version `00`,
secondary header version `00`, and the frame long enough for its
headers, OCF and FECF. With `fecf`, the CRC over the frame must match its
last two bytes. A frame failing any check is dropped. Its virtual channel
frame count isn't taken over, so the next frame sees the gap.

**SCID/VCID filter**: with a filter enabled, a frame of another SCID or
VCID is dropped silently, before anything else is read from it: no OCF,
no packets, no frame count kept.

**OCF**: a frame that passes the checks and filter and has the OCF flag
set publishes its OCF on `ocf`, whatever its data field holds and before
its packets are read. A frame with synchronization flag `1` still
publishes its OCF; only its data field, and any packet in progress, is
dropped (logged).

**Per virtual channel**: the decoder keeps a packet in progress and the
last virtual channel frame count, separately for each SCID and VCID.
Frames of different virtual channels can be interleaved.

**Lost frames**: if a frame's virtual channel frame count isn't the last
one plus 1 (modulo 256), frames were lost. The gap is logged and the
packet in progress, if any, is discarded. The first frame of a virtual
channel has nothing to compare with and starts without a packet in
progress. A loss of a multiple of 256 frames leaves the count continuous;
it is caught when the First Header Pointer contradicts the packet in
progress, and otherwise goes unnoticed, as the frame count gives no more.

**Packet length**: the first byte of a packet gives its version, and with
it the length field (`tm_packets`, the rules `tm_frame_encoder` uses
too). An encapsulation packet (`111`) is `packet_length` bytes in total,
header included. A Space Packet (`000`) is its 6-byte primary header
plus the packet data length field (bytes 4–5) plus 1. Either is
published whole; nothing beyond the length is read from a Space Packet.

**Continuing a packet**: with a packet in progress, the bytes of the
data field before the First Header Pointer (all of it if the pointer is
`11111111111`, no packet start) belong to that packet. If they complete
it exactly, the packet is published. If they leave it incomplete and no
packet starts in the frame, it stays in progress. Otherwise the frame
contradicts the packet's length field, either because a packet starts
before it's complete or because it ends with bytes left over and no
packet start after them. An idle-only frame (`11111111110`) contradicts
it too. The packet is then discarded and logged, and decoding continues
at the First Header Pointer.

**Starting packets**: without a packet in progress, the bytes before the
First Header Pointer are the end of a packet whose start wasn't received,
and are dropped. A frame with no packet start (`11111111111`) or only
idle data (`11111111110`) has its data field dropped whole. Neither is
logged, since any loss behind it already was. From the First Header
Pointer on, packets are read one after the other. Idle packets are
skipped: an encapsulation packet with protocol ID `000` (such as the
1-byte `0xE0`) and a Space Packet with APID `11111111111`. A packet, or
even its header, that runs past the end of the data field becomes the
packet in progress.

**Packets that can't be delimited**: a version other than `111` or
`000`, or an encapsulation `packet_length` shorter than its header, gives
no usable length. The rest of the data field is dropped and logged, and
decoding resumes at the next frame's First Header Pointer. A First Header
Pointer beyond the data field drops the data field and any packet in
progress (logged).

**Error handling** (catch-log-drop,
[ADR-0003](../adr/0003-message-handler-error-policy.md)): a non-PDU, a
non-dict metadata or non-u8vector payload, a frame failing the checks
above, lost frames, a discarded packet, or any failure while decoding or
publishing is logged at `warn`. This block sits right after the channel
decoder, and every one of these can be caused by the link
([coding-standards.md](../coding-standards.md)). Packets and OCFs already
published stay sent.

## CCSDS reference

CCSDS 132.0-B-3 (TM Space Data Link Protocol, October 2021): 4.1.2 and
figures 4-2, 4-3 (primary header), 4.1.3 (secondary header), 4.1.5 (OCF),
4.1.6 (FECF), 4.1.2.7.6 (First Header Pointer), 4.3.2 (packet
extraction). CCSDS 133.1-B (Encapsulation Packet Protocol) for the
encapsulation packet header and idle packet; CCSDS 133.0-B-2 (Space
Packet Protocol) 4.1.3 for the Space Packet primary header and idle
APID.

## Known issues / TODOs

None currently.

## Test coverage

- `python/soarr/qa_tm_frame_decoder.py` — test methods `test_001`–`test_014`:
  a short packet in one idle-filled frame with its metadata (`test_001`),
  several packets in one frame (`test_002`), a packet split across frames,
  with an exact fit and with fill, from `tm_frame_encoder` (`test_003`),
  a packet header split across two frames (`test_004`), an idle-only
  (`11111111110`) frame and a continuation without its start dropped
  silently (`test_005`), a lost frame in the middle of a packet discarding
  it while the next packet comes through (`test_006`), a First Header
  Pointer contradicting the packet in progress (`test_007`), the secondary
  header read from its flag and published, and a wrong FECF dropped
  (`test_008`), the OCF published on `ocf` for every frame, including one
  with no packet start and an idle-only one (`test_009`), Space Packets
  split across frames published on `space_packets` and idle Space Packets
  skipped (`test_010`), two interleaved virtual channels (`test_011`), the
  SCID and VCID filters each dropping a mismatched frame, OCF included,
  and out-of-range filter values raising (`test_012`), malformed input,
  wrong versions, sync flag `1` and an unknown packet version dropped
  (`test_013`), and the round trip `tm_frame_encoder` → `encode_rs` →
  `decode_rs` → this block → `encapsulation_parser` returning the
  original payload (`test_014`, skipped without gr-satellites).
