#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest

from gnuradio.soarr.tm_packets import is_idle, is_space_packet, packet_header_length, packet_length


class qa_tm_packets(gr_unittest.TestCase):

    # The header that holds the length, by version and (encapsulation)
    # length of length; no length field known for other versions.
    def test_001_header_length(self):
        for first_octet, expected in ((0xE0, 1), (0xFD, 2), (0xFE, 4), (0xFF, 8), (0x08, 6), (0x00, 6),
                                      (0x40, None), (0xA0, None)):
            self.assertEqual(packet_header_length(first_octet), expected, hex(first_octet))

    # Encapsulation: packet_length counts the whole packet. Space Packet:
    # 6-byte header + data length field + 1.
    def test_002_total_length(self):
        self.assertEqual(packet_length(bytes([0xE0])), 1)  # the 1-byte idle packet
        self.assertEqual(packet_length(bytes([0xFD, 10])), 10)
        self.assertEqual(packet_length(bytes.fromhex("FE000104")), 260)
        self.assertEqual(packet_length(bytes.fromhex("FF000000") + (70000).to_bytes(4, "big")), 70000)
        self.assertEqual(packet_length(bytes.fromhex("0864C000000F")), 6 + 15 + 1)
        self.assertIsNone(packet_length(bytes([0x40, 0, 0, 0, 0, 0])))  # unknown version
        self.assertIsNone(packet_length(bytes([0xFD, 1])))  # shorter than its own header

    def test_003_idle_and_space_packets(self):
        self.assertTrue(is_idle(bytes([0xE0])))
        self.assertTrue(is_idle(bytes([0xE1, 5])))  # protocol ID 000 with a length field
        self.assertFalse(is_idle(bytes([0xFD, 5])))
        self.assertTrue(is_idle(bytes.fromhex("0FFFC0000000")))  # APID all ones
        self.assertFalse(is_idle(bytes.fromhex("0864C0000000")))
        self.assertTrue(is_space_packet(0x08))
        self.assertFalse(is_space_packet(0xFD))


if __name__ == '__main__':
    gr_unittest.run(qa_tm_packets)
