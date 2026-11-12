#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import digital, gr
import pmt

from .tc_frame_encoder import FECF_CRC

SCID_MAX = 0x3FF  # 10 bits
VCID_MAX = 0x7    # 3 bits
OCF_MAX = 0xFFFFFFFF  # 4 bytes
PRIMARY_HEADER_BYTES = 6
OCF_BYTES = 4
FECF_BYTES = 2
SECONDARY_HEADER_DATA_MAX = 63  # bytes, after the identification byte
FRAME_LENGTH_MAX = 2048  # bytes; keeps every data field offset below the reserved FHP values
FHP_NO_PACKET_START = 0x7FF  # 132.0-B-3 4.1.2.7.6.4
SEGMENT_LENGTH_ID = 0b11     # 132.0-B-3 4.1.2.7.5.2, synchronization flag 0
IDLE_PACKET = 0xE0  # CCSDS 133.1-B: a one-byte Encapsulation Idle Packet
BURST_MORE = pmt.intern("burst_more")


class tm_frame_generator(gr.basic_block):
    """
    Build CCSDS TM Transfer Frames (132.0-B-3) of a fixed length from one
    packet per PDU: primary header, optional secondary header, data field,
    optional Operational Control Field and Frame Error Control Field.

    Each PDU is one burst: a packet longer than the data field is split
    across frames (4.2.2.2), the last frame filled with Encapsulation Idle
    Packets (4.2.2.5). The First Header Pointer points to the first packet
    starting in each frame - the packet itself, an idle packet, or none.
    Every frame but the last carries the metadata `burst_more` = True, so
    the Burst Builder sends the CADUs of one PDU as one burst.
    """

    def __init__(self, scid: int = 0x0, vcid: int = 0x0, frame_length: int = 223,
                 secondary_header: bool = False, secondary_header_data: str = "00",
                 ocf: bool = False, ocf_value: int = 0x0, fecf: bool = False):
        """
        Args:
            scid (int): Spacecraft ID (10 bits), used when the metadata
                carries none.
            vcid (int): Virtual Channel ID (3 bits), used when the metadata
                carries none.
            frame_length (int): the fixed frame length in bytes, at most
                2048; 223 * interleaving depth fills Reed-Solomon codewords.
            secondary_header (bool): add a Transfer Frame Secondary Header.
            secondary_header_data (str): its data field in hexadecimal, 1 to
                63 bytes, used when the metadata carries none; only with
                secondary_header.
            ocf (bool): add an Operational Control Field.
            ocf_value (int): its 4 bytes, used when the metadata carries
                none; only with ocf.
            fecf (bool): add a Frame Error Control Field (CRC-16).

        Raises:
            ValueError: scid, vcid or ocf_value doesn't fit its field, the
                secondary header data isn't 1-63 bytes of hexadecimal, or
                frame_length leaves no data byte or exceeds 2048.
        """
        gr.basic_block.__init__(self, name="TM Frame Generator", in_sig=None, out_sig=None)

        if not 0 <= scid <= SCID_MAX:
            raise ValueError(f"scid must be 0-{SCID_MAX}, got {scid}.")
        if not 0 <= vcid <= VCID_MAX:
            raise ValueError(f"vcid must be 0-{VCID_MAX}, got {vcid}.")
        self.scid = int(scid)
        self.vcid = int(vcid)
        self.secondary_header = bool(secondary_header)
        self.ocf = bool(ocf)
        self.fecf = bool(fecf)

        self._secondary_header_data = b""
        if self.secondary_header:
            try:
                self._secondary_header_data = bytes.fromhex(secondary_header_data)
            except (TypeError, ValueError):
                raise ValueError(f"secondary_header_data must be hexadecimal, got {secondary_header_data!r}.") from None
            if not 1 <= len(self._secondary_header_data) <= SECONDARY_HEADER_DATA_MAX:
                raise ValueError(f"secondary_header_data must be 1-{SECONDARY_HEADER_DATA_MAX} bytes, "
                                 f"got {len(self._secondary_header_data)}.")
        if self.ocf and not 0 <= ocf_value <= OCF_MAX:
            raise ValueError(f"ocf_value must fit 4 bytes (0-{OCF_MAX:#x}), got {ocf_value}.")
        self.ocf_value = int(ocf_value)

        overhead = (PRIMARY_HEADER_BYTES + (1 + len(self._secondary_header_data) if self.secondary_header else 0)
                    + (OCF_BYTES if self.ocf else 0) + (FECF_BYTES if self.fecf else 0))
        if not overhead < frame_length <= FRAME_LENGTH_MAX:
            raise ValueError(f"frame_length must be {overhead + 1}-{FRAME_LENGTH_MAX} bytes with these fields, "
                             f"got {frame_length}.")
        self.frame_length = int(frame_length)
        self._data_field_length = self.frame_length - overhead

        self._crc = digital.crc(FECF_CRC["num_bits"], FECF_CRC["poly"], FECF_CRC["initial_value"],
                                FECF_CRC["final_xor"], FECF_CRC["input_reflected"], FECF_CRC["result_reflected"])
        self._mc_counts = {}  # per SCID (master channel)
        self._vc_counts = {}  # per (SCID, VCID)

        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self.build_frames)

    def _metadata_int(self, meta, key, default, maximum):
        """The integer (pmt long or uint64) under key, default if absent;
        None (logged) if it isn't an integer in 0-maximum. A 4-byte OCF above
        0x7FFFFFFF needs uint64: a pmt long is 32 bits on Windows."""
        value = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
        if pmt.eq(value, pmt.PMT_NIL):
            return default
        number = (pmt.to_long(value) if pmt.is_integer(value)
                  else pmt.to_uint64(value) if pmt.is_uint64(value) else None)
        if number is None or not 0 <= number <= maximum:
            self.logger.error(f"Metadata '{key}' must be an integer 0-{maximum}, got {value}; dropped.")
            return None
        return number

    def _metadata_secondary_header(self, meta):
        """The secondary header data from the metadata, the configured one if
        absent; None (logged) if it isn't bytes of the configured length."""
        value = pmt.dict_ref(meta, pmt.intern("secondary_header"), pmt.PMT_NIL)
        if pmt.eq(value, pmt.PMT_NIL):
            return self._secondary_header_data
        if not pmt.is_u8vector(value) or pmt.length(value) != len(self._secondary_header_data):
            self.logger.error(f"Metadata 'secondary_header' must be {len(self._secondary_header_data)} bytes "
                              f"(fixed per channel, 132.0-B-3 4.1.3.1.6), got {value}; dropped.")
            return None
        return bytes(pmt.u8vector_elements(value))

    def build_frames(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload, one
                packet (e.g. from Encapsulation Header Adder). Optional
                metadata: `scid`, `vcid`, `ocf` (int) and `secondary_header`
                (u8vector of the configured length) replace the block's
                values for this PDU.

        Publishes:
            "out" (pmt_pair): one PDU per frame, frame_length bytes, in
                order, each with the input metadata plus `burst_more`:
                True on every frame but the last, False on the last.

        Drops when:
            - msg is not a pair, its metadata not a dict, or its payload not a u8vector (error - malformed input at the TX boundary, not raw RF noise)
            - the payload is empty (error - same, nothing to send)
            - a metadata override is out of range or of the wrong type or length (error - same)
            - building or publishing fails (error - same); frames already published stay sent
        """
        try:
            if not pmt.is_pair(msg) or not pmt.is_dict(pmt.car(msg)) or not pmt.is_u8vector(pmt.cdr(msg)):
                self.logger.error("Input is not a PDU with a dict and a u8vector payload; dropped.")
                return
            meta = pmt.car(msg)
            packet = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            if not packet:
                self.logger.error("Received an empty PDU; nothing to send.")
                return

            scid = self._metadata_int(meta, "scid", self.scid, SCID_MAX)
            vcid = self._metadata_int(meta, "vcid", self.vcid, VCID_MAX)
            ocf_value = self._metadata_int(meta, "ocf", self.ocf_value, OCF_MAX) if self.ocf else None
            secondary = self._metadata_secondary_header(meta) if self.secondary_header else b""
            if scid is None or vcid is None or (self.ocf and ocf_value is None) or secondary is None:
                return

            length = self._data_field_length
            count = -(-len(packet) // length)
            data = packet + bytes([IDLE_PACKET]) * (count * length - len(packet))
            frames = []
            for index in range(count):
                if index == 0:
                    fhp = 0
                elif index == count - 1 and len(packet) < len(data):
                    fhp = len(packet) - index * length  # the first idle packet
                else:
                    fhp = FHP_NO_PACKET_START
                frames.append(self._frame(scid, vcid, fhp, secondary, data[index * length:(index + 1) * length],
                                          ocf_value, index))
            self._mc_counts[scid] = (self._mc_counts.get(scid, 0) + count) % 256
            self._vc_counts[(scid, vcid)] = (self._vc_counts.get((scid, vcid), 0) + count) % 256

            for index, frame in enumerate(frames):
                frame_meta = pmt.dict_add(meta, BURST_MORE, pmt.from_bool(index < count - 1))
                self.message_port_pub(pmt.intern("out"), pmt.cons(frame_meta, pmt.init_u8vector(len(frame), list(frame))))
        except Exception as exc:
            self.logger.error(f"Failed to build or publish TM frames: {exc}")

    def _frame(self, scid, vcid, fhp, secondary, data_field, ocf_value, index):
        """Frame number index of the PDU; build_frames advances the frame
        counts once all of its frames are built, so a dropped PDU uses none."""
        mc_count = (self._mc_counts.get(scid, 0) + index) % 256
        vc_count = (self._vc_counts.get((scid, vcid), 0) + index) % 256
        status = ((int(self.secondary_header) << 15) | (SEGMENT_LENGTH_ID << 11) | fhp)
        header = (((scid << 4) | (vcid << 1) | int(self.ocf)).to_bytes(2, "big")
                  + bytes([mc_count, vc_count]) + status.to_bytes(2, "big"))
        frame = header
        if self.secondary_header:
            frame += bytes([len(secondary)]) + secondary  # version 00, total length - 1
        frame += data_field
        if self.ocf:
            frame += ocf_value.to_bytes(OCF_BYTES, "big")
        if self.fecf:
            frame += self._crc.compute(list(frame)).to_bytes(FECF_BYTES, "big")
        return frame
