#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest

from gnuradio.soarr import encapsulation_packet as ep


class qa_encapsulation_packet(gr_unittest.TestCase):

    # Known answers (CCSDS 133.1-B): version 0b111, protocol id 0b111 (data)
    # or 0b000 (idle), then length of length. Packet length counts header
    # plus data.
    def test_001_known_headers(self):
        self.assertEqual(ep.header_for_payload(0), bytes([0xE0]))                      # idle, LoL 00
        self.assertEqual(ep.header_for_payload(10), bytes([0xFD, 12]))                 # LoL 01
        self.assertEqual(ep.header_for_payload(256), bytes.fromhex("fe000104"))       # LoL 10
        self.assertEqual(ep.header_for_payload(65532), bytes.fromhex("ff00000000010004"))  # LoL 11

    def test_002_length_of_length_boundaries(self):
        self.assertEqual(ep.length_of_length_for(0), 0b00)
        self.assertEqual(ep.length_of_length_for(1), 0b01)
        self.assertEqual(ep.length_of_length_for(253), 0b01)
        self.assertEqual(ep.length_of_length_for(254), 0b10)
        self.assertEqual(ep.length_of_length_for(65531), 0b10)
        self.assertEqual(ep.length_of_length_for(65532), 0b11)
        with self.assertRaises(ValueError):
            ep.length_of_length_for(2**32)

    def test_003_parse_round_trips_every_variant(self):
        for payload_length in (0, 1, 253, 254, 1000, 65531, 65532):
            with self.subTest(payload_length=payload_length):
                header = ep.header_for_payload(payload_length, user_defined_field=0b1010)
                fields = ep.parse_header(header + b"\x42\x43")
                self.assertEqual(fields["header_length"], len(header))
                self.assertEqual(fields["length_of_length"], ep.length_of_length_for(payload_length))
                self.assertEqual(fields["packet_version"], ep.PACKET_VERSION_NUMBER)
                if payload_length:
                    self.assertEqual(fields["packet_length"], len(header) + payload_length)
                if fields["length_of_length"] >= 0b10:
                    self.assertEqual(fields["user_defined_field"], 0b1010)
                # Rebuilding from the parsed fields gives the same bytes
                self.assertEqual(ep.build_header(fields), header)

    def test_004_header_length_per_variant(self):
        self.assertEqual([ep.header_length(lol) for lol in (0, 1, 2, 3)], [1, 2, 4, 8])

    def test_005_parse_rejects_truncated_header(self):
        with self.assertRaises(ValueError):
            ep.parse_header(b"")
        with self.assertRaises(ValueError):
            ep.parse_header(bytes.fromhex("fe00"))  # LoL 10 needs 4 bytes

    def test_006_header_struct_matches_parse_header(self):
        # ccsds_reader embeds HEADER_STRUCT in its frame format; it must
        # read the same fields as parse_header.
        for payload_length in (0, 10, 256, 65532):
            with self.subTest(payload_length=payload_length):
                header = ep.header_for_payload(payload_length)
                parsed = ep.HEADER_STRUCT.parse(header)
                fields = ep.parse_header(header)
                for key in ("length_of_length", "protocol_id", "packet_length", "user_defined_field"):
                    self.assertEqual(parsed[key], fields[key], key)
                self.assertEqual(ep.header_length(parsed.length_of_length), len(header))


if __name__ == '__main__':
    gr_unittest.run(qa_encapsulation_packet)
