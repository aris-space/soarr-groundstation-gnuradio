#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

from construct import BitStruct, BitsInteger


TC_PRIMARY_HEADER_STRUCT = BitStruct(
    "tfvn" / BitsInteger(2),
    "bypass" / BitsInteger(1),
    "control" / BitsInteger(1),
    "reserved" / BitsInteger(2),
    "scid" / BitsInteger(10),
    "vcid" / BitsInteger(6),
    "frame_length" / BitsInteger(10),
    "frame_sequence_number" / BitsInteger(8),
)

SCID_MIN = 0
SCID_MAX = 0x3FF  # 10 bits
VCID_MIN = 0
VCID_MAX = 0x3F  # 6 bits
FRAME_LENGTH_MIN = 0
FRAME_LENGTH_MAX = 0x3FF  # 10 bits
FRAME_SEQUENCE_NUMBER_MIN = 0
FRAME_SEQUENCE_NUMBER_MAX = 0xFF  # 8 bits

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
        """
        Args:
            scid (int): default SCID used when metadata provides none.
            vcid (int): default VCID used when metadata provides none.
            is_crc_used (bool): if True, frame_length accounts for a
                2-byte FECF appended downstream (CCSDS 232.0-B-4) by a
                separate block (e.g. digital.crc_append) - this block
                does not append the FECF itself.

        Raises:
            ValueError: scid or vcid is outside its field's bit width
                (10 bits / 6 bits respectively).
        """
        gr.basic_block.__init__(
            self,
            name="TC Primary Header Adder",
            in_sig=None,
            out_sig=None,
        )

        if not (SCID_MIN <= scid <= SCID_MAX):
            raise ValueError(f"scid must be in range {SCID_MIN}-{SCID_MAX} (10 bits), got {scid}.")
        if not (VCID_MIN <= vcid <= VCID_MAX):
            raise ValueError(f"vcid must be in range {VCID_MIN}-{VCID_MAX} (6 bits), got {vcid}.")

        # Default field values
        self.tfvn = 0b00
        self.bypass = False
        self.control = False
        self.reserved = 0b00
        self.scid = scid
        self.vcid = vcid

        self.additional_crc_bytes = 2 if is_crc_used else 0

        # Message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        # Handlers
        self.set_msg_handler(pmt.intern("in"), self.build_header)


    def _try_get(self, meta, key, convert, type_name):
        """
        Args:
            meta (pmt_dict): dict to read key from.
            key (str): metadata key to extract.
            convert (callable): value_pmt -> converted value; may raise.
            type_name (str): human-readable type name, used in the error
                log if convert raises.

        Returns:
            the converted value, or None if meta isn't a dict, key is
                absent/nil, or convert() raises (logged at error - a
                present-but-unconvertible value is a real problem,
                unlike an absent key, which is expected when probing
                multiple possible metadata locations in order).
        """
        if not pmt.is_dict(meta):
            return None
        pmt_key = pmt.intern(key)
        if not pmt.dict_has_key(meta, pmt_key):
            return None
        value = pmt.dict_ref(meta, pmt_key, pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            return None
        try:
            return convert(value)
        except Exception:
            self.logger.error(f"Metadata key '{key}' present but not convertible to a {type_name}: {meta}")
            return None

    def _try_get_int(self, meta, key):
        return self._try_get(meta, key, lambda v: int(pmt.to_long(v)), "integer")

    def _try_get_bool(self, meta, key):
        return self._try_get(meta, key, pmt.to_bool, "boolean")

    def _lookup_with_fallback(self, dict_msg, tc_header, top_key, getter, nested_key=None):
        """
        Args:
            dict_msg (pmt_dict): top-level PDU metadata dict.
            tc_header (pmt_dict | PMT_NIL): dict_msg["telecommand"]["tc_header"], or PMT_NIL if absent.
            top_key (str): key to check at the top level.
            getter (callable): self._try_get_int or self._try_get_bool.
            nested_key (str | None): key to check in tc_header, if different from top_key.

        Returns:
            the extracted value, checked at dict_msg[top_key] first,
                falling back to tc_header[nested_key or top_key] only if
                the top-level key is genuinely absent/unconvertible. None
                if neither is present.
        """
        value = getter(dict_msg, top_key)
        if value is None and not pmt.eqv(tc_header, pmt.PMT_NIL):
            value = getter(tc_header, nested_key or top_key)
        return value

    def _get_tc_header(self, dict_msg):
        """
        Args:
            dict_msg (pmt_dict): top-level PDU metadata dict.

        Returns:
            pmt_dict: dict_msg["telecommand"]["tc_header"], or PMT_NIL if
                either level is absent or not a dict.
        """
        telecommand = pmt.dict_ref(dict_msg, pmt.intern("telecommand"), pmt.PMT_NIL)
        if pmt.eqv(telecommand, pmt.PMT_NIL) or not pmt.is_dict(telecommand):
            return pmt.PMT_NIL
        tc_header = pmt.dict_ref(telecommand, pmt.intern("tc_header"), pmt.PMT_NIL)
        if pmt.eqv(tc_header, pmt.PMT_NIL) or not pmt.is_dict(tc_header):
            return pmt.PMT_NIL
        return tc_header

    def _field_in_range(self, name, value, min_value, max_value):
        """
        Args:
            name (str): field name, used in the error log.
            value (int): value to check.
            min_value (int): inclusive lower bound.
            max_value (int): inclusive upper bound.

        Returns:
            bool: True if min_value <= value <= max_value; otherwise
                logs at error and returns False.
        """
        if min_value <= value <= max_value:
            return True
        self.logger.error(f"'{name}' value {value} is outside the valid range {min_value}-{max_value}.")
        return False

    def build_header(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload.
                `vcid_counter` (maps to frame_sequence_number) is
                required, checked at the top level first, falling back to
                `telecommand.tc_header.vcid_counter` only if absent.
                `scid`/`vcid`/`bypass`/`control`/`frame_length` are
                optional, same dual-lookup rule, defaulting to the
                constructor's values (and, for scid/vcid, written back
                into the outgoing metadata for downstream blocks) or a
                computed value (frame_length) if absent everywhere.

        Publishes:
            "out" (pmt_pair): PDU with the 5-byte TC primary header
                prepended to the payload. `bypass`, `control`,
                `frame_length`, and `vcid_counter` are removed from the
                metadata; `scid`/`vcid` are kept (used by later blocks).

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - metadata is not a dict (error - same)
            - payload is not a u8vector (error - same)
            - frame_length is present in metadata but not convertible to an integer (error - same)
            - vcid_counter is absent everywhere (error - same, this is the one required field)
            - scid, vcid, frame_length, or frame_sequence_number is outside its field's bit width (error - same; previously silently wrapped via bitmasking for frame_length/frame_sequence_number instead of being rejected)
            - header packing or publishing fails (error - same)
        """
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

        # Full body from here on wrapped in catch-log-drop, including the
        # final publish - a raise anywhere in here, including from
        # message_port_pub itself, must never escape this handler.
        try:
            payload_bytes = bytes(pmt.u8vector_elements(payload))

            tc_header = self._get_tc_header(dict_msg)

            # Check for VCID
            vcid = self._lookup_with_fallback(dict_msg, tc_header, "vcid", self._try_get_int)
            if vcid is None:
                # VCID is not provided in metadata; write default value to metadata for downstream blocks to use
                vcid = self.vcid
                dict_msg = pmt.dict_add(dict_msg, pmt.intern("vcid"), pmt.from_long(vcid))

            # Check for SCID
            scid = self._lookup_with_fallback(dict_msg, tc_header, "scid", self._try_get_int)
            if scid is None:
                # SCID is not provided in metadata; write default value to metadata for downstream blocks to use
                scid = self.scid
                dict_msg = pmt.dict_add(dict_msg, pmt.intern("scid"), pmt.from_long(scid))

            # Check for bypass/control flags. inject_db always renames
            # "bypass"/"control" to "bypass_flag"/"control_flag" when
            # nesting under telecommand.tc_header, so that's the only
            # nested key name this repo's real pipeline ever produces -
            # the nested lookup checks that renamed key specifically, not
            # the unrenamed one (which is never nested by anything in
            # this repo).
            bypass = self._lookup_with_fallback(dict_msg, tc_header, "bypass", self._try_get_bool, nested_key="bypass_flag")
            if bypass is None:
                bypass = self.bypass

            control = self._lookup_with_fallback(dict_msg, tc_header, "control", self._try_get_bool, nested_key="control_flag")
            if control is None:
                control = self.control

            # Check for frame_length
            if pmt.dict_has_key(dict_msg, pmt.intern("frame_length")):
                frame_length = self._try_get_int(dict_msg, "frame_length")
                if frame_length is None:
                    # Present but not convertible - _try_get_int already
                    # logged why; don't fall back to a computed value here,
                    # since the caller explicitly supplied (a broken) one.
                    return
            else:
                # frame_length counts from byte 6 onward, per CCSDS 232.0-B-4.
                frame_length = len(payload_bytes) + HEADER_BYTES - 1 + self.additional_crc_bytes
                self.logger.debug(f"'frame_length' not provided; computed from payload length: {frame_length}")

            # Extract vcid_counter (required) - maps to frame_sequence_number in CCSDS header
            frame_sequence_number = self._lookup_with_fallback(dict_msg, tc_header, "vcid_counter", self._try_get_int)
            if frame_sequence_number is None:
                self.logger.error("Metadata did not include vcid_counter.")
                return

            # Every field is explicitly validated against its bit width
            # here rather than silently masked/truncated at pack time -
            # scid/vcid/frame_length/frame_sequence_number can all come
            # from caller-supplied metadata, not just the (already
            # constructor-validated) defaults, and a value that doesn't
            # fit must be rejected loudly, not wrapped into a different,
            # wrong value.
            if not self._field_in_range("scid", scid, SCID_MIN, SCID_MAX):
                return
            if not self._field_in_range("vcid", vcid, VCID_MIN, VCID_MAX):
                return
            if not self._field_in_range("frame_length", frame_length, FRAME_LENGTH_MIN, FRAME_LENGTH_MAX):
                return
            if not self._field_in_range("frame_sequence_number", frame_sequence_number, FRAME_SEQUENCE_NUMBER_MIN, FRAME_SEQUENCE_NUMBER_MAX):
                return

            fields = {
                "tfvn": self.tfvn,
                "bypass": bypass,
                "control": control,
                "reserved": self.reserved,
                "scid": scid,
                "vcid": vcid,
                "frame_length": frame_length,
                "frame_sequence_number": frame_sequence_number,
            }
            header = TC_PRIMARY_HEADER_STRUCT.build(fields)

            # Remove the used keys from the dict to avoid confusion downstream.
            dict_msg = pmt.dict_delete(dict_msg, pmt.intern("bypass"))
            dict_msg = pmt.dict_delete(dict_msg, pmt.intern("control"))
            dict_msg = pmt.dict_delete(dict_msg, pmt.intern("frame_length"))
            dict_msg = pmt.dict_delete(dict_msg, pmt.intern("vcid_counter"))

            # VCID, SCID are used in future blocks (not deleted)

            frame = header + payload_bytes
            out_msg = pmt.cons(dict_msg, pmt.init_u8vector(len(frame), list(frame)))

            self.message_port_pub(pmt.intern("out"), out_msg)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to build header or publish message: {exc}")
            return
