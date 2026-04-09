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

    def _pack_header(self, fields):
        if TC_PRIMARY_HEADER_STRUCT is not None:
            return TC_PRIMARY_HEADER_STRUCT.build(fields)

        header_value = (
            ((fields["tfvn"] & 0x3) << 38)
            | ((fields["bypass"] & 0x1) << 37)
            | ((fields["control"] & 0x1) << 36)
            | ((fields["reserved"] & 0x3) << 34)
            | ((fields["scid"] & 0x3FF) << 24)
            | ((fields["vcid"] & 0x3F) << 18)
            | ((fields["frame_length"] & 0x3FF) << 8)
            | (fields["frame_sequence_number"] & 0xFF)
        )
        return header_value.to_bytes(5, byteorder="big")

    def call_db(self, msg):
        # Backward-compatible alias for older tests/callers.
        self.build_header(msg)

    def build_header(self, msg):
        meta = pmt.car(msg)
        body = pmt.cdr(msg)

        if not pmt.is_u8vector(body):
            self.logger.error("Input message body is not a PDU (u8vector).")
            return

        payload_bytes = bytes(pmt.u8vector_elements(body))

        frame_sequence_number = self._pmt_dict_get_int(meta, "frame_sequence_number", None)
        if frame_sequence_number is None:
            self.logger.error("Metadata did not include frame_sequence_number.")
            return

        # Use PDU values when present, otherwise block defaults.
        vcid = self._pmt_dict_get_int(meta, "vcid", self.vcid) & 0x3F
        scid = self._pmt_dict_get_int(meta, "scid", self.scid) & 0x3FF

        bypass = self._pmt_dict_get_int(meta, "bypass", None)
        if bypass is None:
            bypass = self._pmt_dict_get_int(meta, "bypass_flag", None)
        if bypass is None:
            bypass = int(self.bypass)
        bypass = int(bool(bypass))

        control = self._pmt_dict_get_int(meta, "control", None)
        if control is None:
            control = self._pmt_dict_get_int(meta, "control_flag", None)
        if control is None:
            control = int(self.control)
        control = int(bool(control))

        frame_length = self._pmt_dict_get_int(meta, "frame_length", len(payload_bytes) + 5 - 1)

        fields = {
            "tfvn": self.tfvn,
            "bypass": bypass,
            "control": control,
            "reserved": self.reserved,
            "scid": scid,
            "vcid": vcid,
            "frame_length": frame_length & 0x3FF,
            "frame_sequence_number": frame_sequence_number & 0xFF,
        }
        header = self._pack_header(fields)

        frame = header + payload_bytes

        out_meta = meta if pmt.is_dict(meta) else pmt.make_dict()
        out_meta = pmt.dict_add(out_meta, pmt.intern("scid"), pmt.from_long(scid))
        out_meta = pmt.dict_add(out_meta, pmt.intern("vcid"), pmt.from_long(vcid))
        out_meta = pmt.dict_add(
            out_meta, pmt.intern("frame_sequence_number"), pmt.from_long(frame_sequence_number & 0xFF)
        )

        out_msg = pmt.cons(out_meta, pmt.init_u8vector(len(frame), list(frame)))
        self.message_port_pub(pmt.intern("pdu_out"), out_msg)
