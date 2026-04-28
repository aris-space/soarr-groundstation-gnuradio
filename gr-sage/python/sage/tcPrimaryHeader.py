#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import logging

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


class tcPrimaryHeader(gr.basic_block):
    """
    Build the CCSDS TC Transfer Frame Primary Header (5 bytes).

    Flow:
    - Receive a PDU on `pdu_in`
    - Read frame fields (including `frame_sequence_number`) directly from metadata
    - Build header and emit final frame on `pdu_out`
    """

    def __init__(self, scid:int=0x0, vcid:int=0x0):
        gr.basic_block.__init__(
            self,
            name="TC Primary Header Adder",
            in_sig=None,
            out_sig=None,
        )

        self.logger = logging.getLogger("gnuradio.sage.tcPrimaryHeader")

        # Default field values
        self.tfvn = 0b00
        self.bypass = False
        self.control = False
        self.reserved = 0b00
        self.scid = scid
        self.vcid = vcid

        if TC_PRIMARY_HEADER_STRUCT is None:
            self.logger.warning("construct not installed; using manual header packing fallback.")

        # Message ports
        self.message_port_register_in(pmt.intern("pdu_in"))
        self.message_port_register_out(pmt.intern("pdu_out"))

        # Handlers
        self.set_msg_handler(pmt.intern("pdu_in"), self.build_header)


    def _pmt_dict_get_int(self, meta, key, default=None):
        if not pmt.is_dict(meta):
            self.logger.warning(f"Expected metadata to be a dict, but got: {meta}")
            return default

        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            self.logger.warning(f"Metadata missing expected key '{key}': {meta}")
            return default

        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            return default

        try:
            return int(pmt.to_long(value))
        except Exception:
            self.logger.warning(f"Failed to convert metadata key '{key}' to integer: {meta}")
            return default

    def _pmt_dict_get_bool(self, meta, key, default=None):
        if not pmt.is_dict(meta):
            self.logger.warning(f"Expected metadata to be a dict, but got: {meta}")
            return default

        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            self.logger.warning(f"Metadata missing expected key '{key}': {meta}")
            return default

        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            return default

        try:
            return pmt.to_bool(value)
        except Exception:
            self.logger.warning(f"Failed to convert metadata key '{key}' to boolean: {meta}")
            return default

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

        # Check for VCID
        vcid = self._pmt_dict_get_int(dict_msg, "vcid", None)
        if vcid is None:
            # VCID is not proviced in metadata; write default value to metadata for downstream blocks to use
            vcid = self.vcid
            dict_msg = pmt.dict_add(dict_msg, pmt.intern("vcid"), pmt.from_long(vcid))

        # Check for SCID
        scid = self._pmt_dict_get_int(dict_msg, "scid", None)
        if scid is None:
            # SCID is not proviced in metadata; write default value to metadata for downstream blocks to use
            scid = self.scid
            dict_msg = pmt.dict_add(dict_msg, pmt.intern("scid"), pmt.from_long(scid))

        # Check for bypass flag
        bypass = self._pmt_dict_get_bool(dict_msg, "bypass", self.bypass)

        # Check for control flag
        control = self._pmt_dict_get_bool(dict_msg, "control", self.control)

        # Check for frame_length
        frame_length = self._pmt_dict_get_int(dict_msg, "frame_length", None)
        if frame_length is None:
            self.logger.warning(f"Metadata missing 'frame_length' key; using payload length: {len(payload_bytes) + 5 - 1}")
            frame_length = len(payload_bytes) + 5 - 1

        # Extract vcid_counter (required) - maps to frame_sequence_number in CCSDS header
        frame_sequence_number = self._pmt_dict_get_int(dict_msg, "vcid_counter", None)
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
        self.message_port_pub(pmt.intern("pdu_out"), out_msg)
