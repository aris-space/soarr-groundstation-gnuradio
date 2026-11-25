#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

from .fecf import FECF_BYTES, fecf_crc
from .tm_frame_encoder import FHP_NO_PACKET_START, OCF_BYTES, PRIMARY_HEADER_BYTES, SCID_MAX, VCID_MAX
from .tm_packets import is_idle, is_space_packet, packet_header_length, packet_length

FHP_ONLY_IDLE_DATA = 0x7FE  # 132.0-B-3 4.1.2.7.6.5
OUT = pmt.intern("out")
SPACE_PACKETS = pmt.intern("space_packets")
OCF = pmt.intern("ocf")


class _Partial:
    """A packet in progress: its bytes so far, its total length once its
    header is complete, and the metadata of the frame it starts in."""

    def __init__(self, data, meta):
        self.data = bytearray(data)
        self.meta = meta
        self.total = None
        self.update_total()

    def update_total(self):
        """Read the total length once the header is complete; False if the
        header has no usable length."""
        if self.total is None:
            needed = packet_header_length(self.data[0])
            if needed is not None and len(self.data) >= needed:
                self.total = packet_length(bytes(self.data[:needed]))
                return self.total is not None
        return True


class tm_frame_decoder(gr.basic_block):
    """
    Extract packets from CCSDS TM Transfer Frames (132.0-B-3): join the
    parts of a packet split across frames using the First Header Pointer
    and the packet's own length field, skip idle packets, and publish one
    PDU per packet - encapsulation packets on "out", Space Packets on
    "space_packets". The OCF of every frame comes out on "ocf".

    The decoder keeps a packet in progress and the last virtual channel
    frame count per SCID and VCID; a gap in the count discards the packet
    in progress, as does a First Header Pointer contradicting its length.
    """

    def __init__(self, fecf: bool = False, scid_filter_enable: bool = False, scid: int = 0x0,
                 vcid_filter_enable: bool = False, vcid: int = 0x0):
        """
        Args:
            fecf (bool): frames end with a 2-byte FECF (CRC-16), checked and
                removed; must match the TX setting.
            scid_filter_enable (bool): keep only frames of scid.
            scid (int): Spacecraft ID (10 bits) to keep, with the filter.
            vcid_filter_enable (bool): keep only frames of vcid.
            vcid (int): Virtual Channel ID (3 bits) to keep, with the filter.

        Raises:
            ValueError: scid or vcid doesn't fit its field.
        """
        gr.basic_block.__init__(self, name="TM Frame Decoder", in_sig=None, out_sig=None)

        if not 0 <= scid <= SCID_MAX:
            raise ValueError(f"scid must be 0-{SCID_MAX}, got {scid}.")
        if not 0 <= vcid <= VCID_MAX:
            raise ValueError(f"vcid must be 0-{VCID_MAX}, got {vcid}.")
        self.fecf = bool(fecf)
        self.scid = int(scid) if scid_filter_enable else None
        self.vcid = int(vcid) if vcid_filter_enable else None

        self._crc = fecf_crc()
        self._counts = {}    # (SCID, VCID) -> last virtual channel frame count
        self._partials = {}  # (SCID, VCID) -> _Partial

        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(OUT)
        self.message_port_register_out(SPACE_PACKETS)
        self.message_port_register_out(OCF)
        self.set_msg_handler(pmt.intern("in"), self.decode_frame)

    def decode_frame(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload, one
                TM Transfer Frame (e.g. from satellites.decode_rs).

        Publishes:
            "out" (pmt_pair): one PDU per encapsulation packet, the whole
                packet. Metadata keys: those of the frame it starts in, plus
                    scid, vcid, mc_frame_count, vc_frame_count (int): of that frame.
                    secondary_header (u8vector, optional): that frame's secondary header data field.
                    ocf (uint64, optional): that frame's OCF.
            "space_packets" (pmt_pair): one PDU per Space Packet, the whole
                packet, metadata as on "out".
            "ocf" (pmt_pair): one PDU per frame with an OCF: the frame's
                metadata plus scid, vcid, mc_frame_count, vc_frame_count;
                payload the 4 OCF bytes.

        Drops when:
            - msg is not a pair, its metadata not a dict, or its payload not a u8vector (warn - right after the channel decoder, can be the link)
            - the frame is too short, its version or secondary header version isn't 00, or its FECF doesn't match (warn - same); its frame count isn't kept
            - the frame is of another SCID or VCID with the filter on (debug - expected with a filter)
            - its data field holds only idle data, or only the end of a packet whose start wasn't received (debug - expected after a loss or at start-up, which is logged where it is detected)
            - the synchronization flag is 1 (warn - same); the OCF is still published, the data field dropped
            - frames were lost, or the First Header Pointer contradicts the packet in progress (warn - same); the packet is discarded
            - a packet has no usable length field (warn - same); the rest of the data field is dropped
            - decoding or publishing fails (warn - same); what was published stays sent
        """
        try:
            if not pmt.is_pair(msg) or not pmt.is_dict(pmt.car(msg)) or not pmt.is_u8vector(pmt.cdr(msg)):
                self.logger.warn("Input is not a PDU with a dict and a u8vector payload; dropped.")
                return
            frame = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            if len(frame) < PRIMARY_HEADER_BYTES:
                self.logger.warn(f"Frame of {len(frame)} bytes is shorter than the primary header; dropped.")
                return
            if frame[0] >> 6 != 0:
                self.logger.warn(f"Transfer frame version {frame[0] >> 6} is not 00; dropped.")
                return
            scid = ((frame[0] & 0x3F) << 4) | (frame[1] >> 4)
            vcid = (frame[1] >> 1) & 0b111
            if (self.scid is not None and scid != self.scid) or (self.vcid is not None and vcid != self.vcid):
                self.logger.debug(f"Frame of SCID {scid} VCID {vcid} filtered out; dropped.")
                return
            fields = self._fields(frame)
            if fields is None:
                return
            data, secondary, ocf, sync, fhp = fields
            mc_count, vc_count = frame[2], frame[3]

            meta = pmt.car(msg)
            for key, value in (("scid", scid), ("vcid", vcid), ("mc_frame_count", mc_count),
                               ("vc_frame_count", vc_count)):
                meta = pmt.dict_add(meta, pmt.intern(key), pmt.from_long(value))
            packet_meta = meta
            if secondary is not None:
                packet_meta = pmt.dict_add(packet_meta, pmt.intern("secondary_header"),
                                           pmt.init_u8vector(len(secondary), list(secondary)))
            if ocf is not None:
                packet_meta = pmt.dict_add(packet_meta, OCF, pmt.from_uint64(int.from_bytes(ocf, "big")))

            channel = (scid, vcid)
            last = self._counts.get(channel)
            self._counts[channel] = vc_count
            if last is not None and vc_count != (last + 1) % 256:
                lost = (vc_count - last - 1) % 256
                partial = self._partials.pop(channel, None)
                self.logger.warn(f"SCID {scid} VCID {vcid}: {lost} frame(s) lost"
                                 + (f", packet in progress ({len(partial.data)} bytes) discarded." if partial else "."))

            if ocf is not None:
                self.message_port_pub(OCF, pmt.cons(meta, pmt.init_u8vector(len(ocf), list(ocf))))
            if sync:
                self._discard(channel, "synchronization flag 1 (no packets)")
                self.logger.warn(f"SCID {scid} VCID {vcid}: synchronization flag 1; data field dropped.")
                return
            self._extract(channel, data, fhp, packet_meta)
        except Exception as exc:
            self.logger.warn(f"Failed to decode a TM frame or publish its packets: {exc}")

    def _fields(self, frame):
        """The data field, secondary header data, OCF bytes, synchronization
        flag and First Header Pointer of the frame; None (logged) if the
        frame is too short for its fields, its secondary header version
        isn't 00 or its FECF doesn't match."""
        end = len(frame)
        if self.fecf:
            if end < PRIMARY_HEADER_BYTES + FECF_BYTES:
                self.logger.warn(f"Frame of {end} bytes is too short for its FECF; dropped.")
                return None
            end -= FECF_BYTES
            if self._crc.compute(list(frame[:end])) != int.from_bytes(frame[end:], "big"):
                self.logger.warn("FECF does not match; frame dropped.")
                return None
        status = int.from_bytes(frame[4:6], "big")
        start = PRIMARY_HEADER_BYTES
        secondary = None
        if status >> 15:
            if end <= start:
                self.logger.warn("Frame is too short for its secondary header; dropped.")
                return None
            if frame[start] >> 6 != 0:
                self.logger.warn(f"Secondary header version {frame[start] >> 6} is not 00; frame dropped.")
                return None
            length = (frame[start] & 0x3F) + 1  # total length, identification byte included
            secondary = frame[start + 1:start + length]
            start += length
        ocf = None
        if frame[1] & 1:
            end -= OCF_BYTES
            ocf = frame[end:end + OCF_BYTES]
        if end < start:
            self.logger.warn(f"Frame of {len(frame)} bytes is too short for its headers, OCF and FECF; dropped.")
            return None
        return frame[start:end], secondary, ocf, (status >> 14) & 1, status & 0x7FF

    def _discard(self, channel, reason):
        partial = self._partials.pop(channel, None)
        if partial is not None:
            self.logger.warn(f"SCID {channel[0]} VCID {channel[1]}: {reason}; packet in progress "
                             f"({len(partial.data)} bytes) discarded.")

    def _extract(self, channel, data, fhp, meta):
        """Continue the packet in progress with the bytes before the First
        Header Pointer, then read the packets starting from it."""
        no_start = fhp in (FHP_NO_PACKET_START, FHP_ONLY_IDLE_DATA)
        if not no_start and fhp >= len(data):
            self._discard(channel, f"First Header Pointer {fhp} beyond the data field")
            self.logger.warn(f"First Header Pointer {fhp} is beyond the {len(data)}-byte data field; dropped.")
            return
        if fhp == FHP_ONLY_IDLE_DATA:
            self._discard(channel, "only idle data in the next frame")
            self.logger.debug(f"SCID {channel[0]} VCID {channel[1]}: frame of only idle data.")
            return

        partial = self._partials.pop(channel, None)
        if partial is None and (no_start or fhp > 0):
            self.logger.debug(f"SCID {channel[0]} VCID {channel[1]}: {len(data) if no_start else fhp} bytes end a "
                              f"packet whose start wasn't received; dropped.")
        if partial is not None:
            partial.data += data[:len(data) if no_start else fhp]
            if not partial.update_total():
                self.logger.warn("Packet in progress has no usable length field; discarded.")
            elif partial.total is not None and len(partial.data) == partial.total:
                self._publish(bytes(partial.data), partial.meta)
            elif no_start and (partial.total is None or len(partial.data) < partial.total):
                self._partials[channel] = partial
            else:
                self.logger.warn(f"SCID {channel[0]} VCID {channel[1]}: First Header Pointer contradicts the "
                                 f"packet in progress ({len(partial.data)} of {partial.total or '?'} bytes); discarded.")
        if no_start:
            return

        offset = fhp
        while offset < len(data):
            needed = packet_header_length(data[offset])
            if needed is not None and offset + needed > len(data):
                self._partials[channel] = _Partial(data[offset:], meta)
                return
            total = packet_length(data[offset:offset + needed]) if needed is not None else None
            if total is None:
                self.logger.warn(f"Packet at data field offset {offset} has no usable length field "
                                 f"(first byte {data[offset]:#04x}); rest of the data field dropped.")
                return
            if offset + total > len(data):
                self._partials[channel] = _Partial(data[offset:], meta)
                return
            self._publish(data[offset:offset + total], meta)
            offset += total

    def _publish(self, packet, meta):
        """Publish a complete packet on its port; idle packets are skipped."""
        if is_idle(packet):
            return
        port = SPACE_PACKETS if is_space_packet(packet[0]) else OUT
        self.message_port_pub(port, pmt.cons(meta, pmt.init_u8vector(len(packet), list(packet))))
