#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

from . import encapsulation_packet


class encapsulation_parser(gr.basic_block):
    """
    Split CCSDS 133.1-B encapsulation packets and strip their headers - the
    RX counterpart of encapsulation_header.

    Flow:
    - Receive a PDU on `in` whose payload is one or more encapsulation
      packets back to back (a TC frame's data field, after SDLS
      verification and decryption)
    - For each packet: parse its header, publish its data field on `out`
      with the header fields added to the metadata as
      `encapsulation_header`; idle packets are skipped
    """

    def __init__(self):
        """
        Args: none.
        """
        gr.basic_block.__init__(self,
            name="encapsulation_parser",
            in_sig=None,
            out_sig=None)

        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        self.set_msg_handler(pmt.intern("in"), self.parse_packets)

    def _header_meta(self, fields):
        """
        Args:
            fields (dict): header fields from encapsulation_packet.parse_header.

        Returns:
            pmt_dict: the header fields as PMT integers; fields the header
                variant doesn't carry (None) are left out.
        """
        meta = pmt.make_dict()
        for key, value in fields.items():
            if key != "header_length" and value is not None:
                meta = pmt.dict_add(meta, pmt.intern(key), pmt.from_long(int(value)))
        return meta

    def parse_packets(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload,
                one or more encapsulation packets back to back.

        Publishes:
            "out" (pmt_pair): one PDU per data packet - the input metadata
                plus `encapsulation_header` (dict of the parsed header
                fields: first_octet, packet_version, protocol_id,
                length_of_length, packet_length, and, where the header
                variant carries them, user_defined_field,
                protocol_id_extension, ccsds_defined_field), paired with
                the packet's data field. Idle packets publish nothing.

        Drops when:
            - msg is not a PDU pair, metadata is not a dict, or payload is not a u8vector (error - this block sits downstream of ccsds_reader, not on raw RF data)
            - the payload is empty (error - same)
            - a packet header is truncated, or its packet length is shorter than its header or longer than the remaining data - that packet and everything after it (error - same); packets before it are still published
            - an internal failure occurs while parsing or publishing (error - same)
        """
        try:
            if not pmt.is_pair(msg):
                self.logger.error(f"Received non-PDU message: {msg}")
                return
            meta = pmt.car(msg)
            body = pmt.cdr(msg)
            if not pmt.is_dict(meta):
                self.logger.error(f"Received PDU with non-dict metadata: {meta}")
                return
            if not pmt.is_u8vector(body):
                self.logger.error(f"Received PDU with non-u8vector payload: {body}")
                return

            data = bytes(pmt.u8vector_elements(body))
            if not data:
                self.logger.error("Received empty payload; no encapsulation packet to parse.")
                return

            pos = 0
            while pos < len(data):
                fields = encapsulation_packet.parse_header(data[pos:])
                header_len = fields["header_length"]
                # The 1-byte variant has no length field: an idle packet of header only.
                packet_len = fields["packet_length"] if fields["packet_length"] is not None else header_len
                if packet_len < header_len or pos + packet_len > len(data):
                    self.logger.error(
                        f"Encapsulation packet at byte {pos} declares length {packet_len}, but "
                        f"{len(data) - pos} bytes remain (header {header_len}); dropping the rest."
                    )
                    return

                if fields["protocol_id"] != encapsulation_packet.PROTOCOL_ID_IDLE:
                    packet_data = data[pos + header_len:pos + packet_len]
                    out_meta = pmt.dict_add(meta, pmt.intern("encapsulation_header"), self._header_meta(fields))
                    self.message_port_pub(
                        pmt.intern("out"),
                        pmt.cons(out_meta, pmt.init_u8vector(len(packet_data), list(packet_data))),
                    )
                pos += packet_len

            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to parse or publish encapsulation packets: {exc}")
