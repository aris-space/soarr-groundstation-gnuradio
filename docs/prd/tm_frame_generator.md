# tm_frame_generator

## Purpose

Builds CCSDS TM Transfer Frames (CCSDS 132.0-B-3) of a fixed length from
one packet per PDU: primary header, optional secondary header, data
field, optional Operational Control Field (OCF) and Frame Error Control
Field (FECF). GRC label **TM Frame Generator**, category
`[Soarr]/TM (Telemetry)/Frame` — the TM counterpart of
`tc_primary_header`, with the splitting into frames that TM needs.

## Pipeline position

```
TX: encapsulation_header → tm_frame_generator → satellites.encode_rs (gr-satellites)
    → tm_randomizer → tm_cadu_framer → cltu_burst_builder (Burst Builder) → (modulator)
```

Each input PDU is one burst. Its frames carry the metadata key
`burst_more`: `True` on every frame but the last, `False` on the last.
The Reed-Solomon encoder, the randomizer and the CADU framer pass the
metadata on, so the Burst Builder can send all CADUs of the PDU as one
burst.

## Message ports

| Port | Direction | PMT shape | Example |
|---|---|---|---|
| `in` | input | PDU: `(metadata_dict . u8vector)`, one packet (e.g. an encapsulation packet). Optional metadata: `scid`, `vcid`, `ocf` (integer; `ocf` above `0x7FFFFFFF` as `uint64`, since a PMT long is 32 bits on Windows), `secondary_header` (u8vector of the configured length) replace the block's values for this PDU. | `pmt.cons({vcid: 2}, u8vector(500 bytes))` |
| `out` | output | One PDU per frame, `frame_length` bytes, in order; the input metadata plus `burst_more` (bool). | `pmt.cons({vcid: 2, burst_more: #t}, u8vector(223 bytes))` |

## Parameters

| Name | Type | Default | Notes |
|---|---|---|---|
| `scid` | int | `0` | Spacecraft ID, 10 bits (0–1023), used when the metadata carries none; otherwise `ValueError`. |
| `vcid` | int | `0` | Virtual Channel ID, 3 bits (0–7), used when the metadata carries none; otherwise `ValueError`. |
| `frame_length` | int | `223` | The fixed frame length in bytes (4.1.1.2). 223 × interleaving depth fills Reed-Solomon (255,223) codewords; a shorter frame gives a shortened codeblock. At most 2048, and at least one byte more than the headers, OCF and FECF together; otherwise `ValueError`. |
| `secondary_header` | bool | `False` | Adds a Transfer Frame Secondary Header (4.1.3) and sets its flag. |
| `secondary_header_data` | str | `"00"` | Its data field in hexadecimal, 1–63 bytes, used when the metadata carries none; only with `secondary_header`. Otherwise `ValueError`. GRC shows it only while the secondary header is enabled. |
| `ocf` | bool | `False` | Adds a 4-byte Operational Control Field (4.1.5) and sets its flag. |
| `ocf_value` | int | `0` | Its content (e.g. a CLCW), 0–`0xFFFFFFFF`, used when the metadata carries none; only with `ocf`. Otherwise `ValueError`. GRC shows it only while the OCF is enabled. |
| `fecf` | bool | `False` | Adds the 2-byte FECF (4.1.6): CRC-16/CCITT-FALSE over the whole frame before it, the same CRC as the TC FECF (`FECF_CRC` in `tc_frame_encoder`). Usually off with Reed-Solomon. |

## Behavior / edge cases / current error handling

**Frame layout** (`build_frames`): primary header (6 bytes), secondary
header (identification byte with version `00` and the total length minus
one, then its data), data field, OCF, FECF, in that order without gaps
(4.1.1.1). The data field is what `frame_length` leaves after the other
fields.

**Primary header**: version `00`, SCID, VCID, OCF flag, master channel
frame count, virtual channel frame count, then the data field status for
packets: secondary header flag, synchronization flag `0`, packet order
flag `0`, segment length identifier `11` (4.1.2.7.3–4.1.2.7.5), and the
First Header Pointer.

**Splitting and fill**: the packet is split across as many frames as it
needs (4.2.2.2); the space left in the last frame is filled with
one-byte Encapsulation Idle Packets, `0xE0` (CCSDS 133.1-B; allowed by
4.2.2.5), so a gap of any size is filled exactly. A packet that fills its
frames exactly gets no fill.

**First Header Pointer** (4.1.2.7.6), counted from the first byte of the
data field: `0` in the first frame, where the packet starts; in the last
frame, the offset of the first idle packet if fill was needed;
`11111111111` (no packet starts) in every other frame. The OID value
`11111111110` is not produced: every frame carries packet data.

**Frame counts** (4.1.2.5, 4.1.2.6): modulo 256, starting at 0. The master
channel count runs per SCID, the virtual channel count per SCID and
VCID, over all frames the block has built, across PDUs. A dropped PDU uses
no counts.

**Error handling** (catch-log-drop,
[ADR-0003](../adr/0003-message-handler-error-policy.md)): a non-PDU, a
non-dict metadata, a non-u8vector or empty payload, a metadata override
out of range, of the wrong type, or (for `secondary_header`) of another
length than configured, or any failure while building or publishing, is
dropped and logged at `error` — a TX block, not exposed to raw RF data
([coding-standards.md](../coding-standards.md)). If a publish fails
partway, the frames already published stay sent.

## CCSDS reference

CCSDS 132.0-B-3 (TM Space Data Link Protocol, October 2021): 4.1.1 (frame
structure, fixed length), 4.1.2 and figures 4-2, 4-3 (primary header),
4.1.3 and figure 4-4 (secondary header), 4.1.4 (data field), 4.1.5
(OCF), 4.1.6 (FECF), 4.2.2 (packet processing: splitting, First Header
Pointer, idle packets). CCSDS 133.1-B (Encapsulation Packet Protocol) for
the one-byte Encapsulation Idle Packet.

## Known issues / TODOs

- Each PDU must hold exactly one packet: the First Header Pointer marks
  only where the PDU starts, so a second packet concatenated into the
  same PDU would start unmarked.
- No OID frames: the block sends frames only for PDUs, never on a timer.

## Test coverage

- `python/soarr/qa_tm_frame_generator.py` — 13 test methods
  (`test_001`–`test_013`): the primary header fields decoded bit by bit
  (`test_001`), a short packet in one idle-filled frame (`test_002`),
  splitting with the First Header Pointer and `burst_more` per frame
  (`test_003`), an exact fit without fill (`test_004`), the frame counts
  per master and virtual channel with the wrap at 256 across PDUs
  (`test_005`), SCID/VCID from the metadata and the metadata passed on
  (`test_006`), the OCF with its flag and a metadata value as PMT long and
  uint64 (`test_007`), the secondary header with its identification byte
  and a metadata value (`test_008`), the FECF against an independent
  bitwise CRC with every optional field in its place (`test_009`),
  malformed input and bad overrides dropped without using counts
  (`test_010`), a publish failure caught (`test_011`), invalid settings
  raising (`test_012`), and 223-byte frames through gr-satellites'
  Reed-Solomon encoder with the burst marker kept (`test_013`, skipped
  without gr-satellites).
