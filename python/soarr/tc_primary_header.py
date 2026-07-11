#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

try:
    from construct import BitStruct, BitsInteger
except ImportError:
    BitStruct = None
    BitsInteger = None


def _build_tc_primary_header_struct():
    if BitStruct is None or BitsInteger is None:
        return None
    return BitStruct(
        "tfvn" / BitsInteger(2),
        "bypass" / BitsInteger(1),
        "control" / BitsInteger(1),
        "reserved" / BitsInteger(2),
        "scid" / BitsInteger(10),
        "vcid" / BitsInteger(6),
        "frame_length" / BitsInteger(10),
        "frame_sequence_number" / BitsInteger(8),
    )


TC_PRIMARY_HEADER_STRUCT = _build_tc_primary_header_struct()

TFVN_MASK = 0b11  # 2 bits
BYPASS_MASK = 0b1  # 1 bit
CONTROL_MASK = 0b1  # 1 bit
RESERVED_MASK = 0b11  # 2 bits
SCID_MASK = 0b11_1111_1111  # 10 bits
VCID_MASK = 0b11_1111  # 6 bits
FRAME_LENGTH_MASK = 0b11_1111_1111  # 10 bits for frame length
FRAME_SEQUENCE_NUMBER_MASK = 0b1111_1111  # 8 bits

HEADER_BYTES = 5


class tc_primary_header(gr.basic_block):
    """
    Build the CCSDS TC Transfer Frame Primary Header (5 bytes).

    Flow:
    - Receive a PDU on `in`
    - Read frame fields (including `frame_sequence_number`) directly from metadata
    - Build header and emit final frame on `out`
    """

    def __init__(self, scid:int=0x0, vcid:int=0x0, is_crc_used:bool=True):
        gr.basic_block.__init__(
            self,
            name="TC Primary Header Adder",
            in_sig=None,
            out_sig=None,
        )


        # Default field values
        self.tfvn = 0b00
        self.bypass = False
        self.control = False
        self.reserved = 0b00
        self.scid = scid
        self.vcid = vcid

        if is_crc_used:
            self.additional_crc_bytes = 2
        else:
            self.additional_crc_bytes = 0

        if TC_PRIMARY_HEADER_STRUCT is None:
            self.logger.warn("construct not installed; using manual header packing fallback.")

        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        # Handlers
        self.set_msg_handler(pmt.intern("in"), self.build_header)


    def _pmt_dict_get_int(self, meta, key, default=None):
        if not pmt.is_dict(meta):
            self.logger.warn(f"Expected metadata to be a dict, but got: {meta}")
            return default

        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            self.logger.warn(f"Metadata missing expected key '{key}': {meta}")
            return default

        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            return default

        try:
            return int(pmt.to_long(value))
        except Exception:
            self.logger.warn(f"Failed to convert metadata key '{key}' to integer: {meta}")
            return default

    def _pmt_dict_get_bool(self, meta, key, default=None):
        if not pmt.is_dict(meta):
            self.logger.warn(f"Expected metadata to be a dict, but got: {meta}")
            return default

        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            self.logger.warn(f"Metadata missing expected key '{key}': {meta}")
            return default

        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            return default

        try:
            return pmt.to_bool(value)
        except Exception:
            self.logger.warn(f"Failed to convert metadata key '{key}' to boolean: {meta}")
            return default

    def _try_get_int(self, meta, key):
        if not pmt.is_dict(meta):
            return None
        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            return None
        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            return None
        try:
            return int(pmt.to_long(value))
        except Exception:
            return None

    def _try_get_bool(self, meta, key):
        if not pmt.is_dict(meta):
            return None
        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            return None
        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            return None
        try:
            return pmt.to_bool(value)
        except Exception:
            return None

    def _get_tc_header(self, dict_msg):
        telecommand = pmt.dict_ref(dict_msg, pmt.intern("telecommand"), pmt.PMT_NIL)
        if pmt.eqv(telecommand, pmt.PMT_NIL) or not pmt.is_dict(telecommand):
            return pmt.PMT_NIL
        tc_header = pmt.dict_ref(telecommand, pmt.intern("tc_header"), pmt.PMT_NIL)
        if pmt.eqv(tc_header, pmt.PMT_NIL) or not pmt.is_dict(tc_header):
            return pmt.PMT_NIL
        return tc_header

    def _pack_header(self, fields):
        if TC_PRIMARY_HEADER_STRUCT is not None:
            return TC_PRIMARY_HEADER_STRUCT.build(fields)

        header_value = (
            ((fields["tfvn"] & TFVN_MASK) << 38)
            | ((fields["bypass"] & BYPASS_MASK) << 37)
            | ((fields["control"] & CONTROL_MASK) << 36)
            | ((fields["reserved"] & RESERVED_MASK) << 34)
            | ((fields["scid"] & SCID_MASK) << 24)
            | ((fields["vcid"] & VCID_MASK) << 18)
            | ((fields["frame_length"] & FRAME_LENGTH_MASK) << 8)
            | (fields["frame_sequence_number"] & FRAME_SEQUENCE_NUMBER_MASK)
        )
        return header_value.to_bytes(5, byteorder="big")

    def build_header(self, msg):

        if not pmt.is_pair(msg):
            self.logger.error("Input message is not a pair.")
            return
        

        dict_msg = pmt.car(msg)
        payload = pmt.cdr(msg)

        if not pmt.is_dict(dict_msg):
            self.logger.error("Input message metadata is not a dict.")
            return

        if not pmt.is_u8vector(payload):
            self.logger.error("Input message body is not a PDU (u8vector).")
            return
        
        payload_bytes = bytes(pmt.u8vector_elements(payload))

        tc_header = self._get_tc_header(dict_msg)

        # Check for VCID
        vcid = self._try_get_int(dict_msg, "vcid")
        if vcid is None and not pmt.eqv(tc_header, pmt.PMT_NIL):
            vcid = self._try_get_int(tc_header, "vcid")
        if vcid is None:
            # VCID is not proviced in metadata; write default value to metadata for downstream blocks to use
            vcid = self.vcid
            dict_msg = pmt.dict_add(dict_msg, pmt.intern("vcid"), pmt.from_long(vcid))

        # Check for SCID
        scid = self._try_get_int(dict_msg, "scid")
        if scid is None and not pmt.eqv(tc_header, pmt.PMT_NIL):
            scid = self._try_get_int(tc_header, "scid")
        if scid is None:
            # SCID is not proviced in metadata; write default value to metadata for downstream blocks to use
            scid = self.scid
            dict_msg = pmt.dict_add(dict_msg, pmt.intern("scid"), pmt.from_long(scid))

        # Check for bypass flag
        bypass = self._try_get_bool(dict_msg, "bypass")
        if bypass is None and not pmt.eqv(tc_header, pmt.PMT_NIL):
            bypass = self._try_get_bool(tc_header, "bypass")
        if bypass is None and not pmt.eqv(tc_header, pmt.PMT_NIL):
            bypass = self._try_get_bool(tc_header, "bypass_flag")
        if bypass is None:
            bypass = self.bypass

        # Check for control flag
        control = self._try_get_bool(dict_msg, "control")
        if control is None and not pmt.eqv(tc_header, pmt.PMT_NIL):
            control = self._try_get_bool(tc_header, "control")
        if control is None and not pmt.eqv(tc_header, pmt.PMT_NIL):
            control = self._try_get_bool(tc_header, "control_flag")
        if control is None:
            control = self.control

        # Check for frame_length
        if pmt.dict_has_key(dict_msg, pmt.intern("frame_length")):
            frame_length = self._pmt_dict_get_int(dict_msg, "frame_length", None)
            if frame_length is None:
                # if not provided: calculate it
                self.logger.debug(f"Metadata missing 'frame_length' key; using payload length: {len(payload_bytes) + HEADER_BYTES - 1 + self.additional_crc_bytes}")
        else:
            frame_length = len(payload_bytes) + HEADER_BYTES - 1 + self.additional_crc_bytes  # payload + header - 1 (since frame_length counts from byte 6) + optional CRC bytes

        # Extract vcid_counter (required) - maps to frame_sequence_number in CCSDS header
        frame_sequence_number = self._try_get_int(dict_msg, "vcid_counter")
        if frame_sequence_number is None and not pmt.eqv(tc_header, pmt.PMT_NIL):
            frame_sequence_number = self._try_get_int(tc_header, "vcid_counter")
        if frame_sequence_number is None:
            self.logger.error("Metadata did not include vcid_counter.")
            return

        fields = {
            "tfvn": self.tfvn,
            "bypass": bypass,
            "control": control,
            "reserved": self.reserved,
            "scid": scid,
            "vcid": vcid,
            "frame_length": frame_length & FRAME_LENGTH_MASK,
            "frame_sequence_number": frame_sequence_number & FRAME_SEQUENCE_NUMBER_MASK,
        }
        header = self._pack_header(fields)

        # Remove the used keys from the dict to avoid confusion downstream.
        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("bypass"))
        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("control"))
        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("frame_length"))
        dict_msg = pmt.dict_delete(dict_msg, pmt.intern("vcid_counter"))

        # VCID, SCID are used in future blocks (not deleted)

        frame = header + payload_bytes
        out_msg = pmt.cons(dict_msg, pmt.init_u8vector(len(frame), list(frame)))
        
        self.message_port_pub(pmt.intern("out"), out_msg)
        self.logger.info(f"OK")
