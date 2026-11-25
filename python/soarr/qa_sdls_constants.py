#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest

from gnuradio.soarr import sdls_constants as sc


class qa_sdls_constants(gr_unittest.TestCase):

    # The nonce and the counter together form exactly one AES block.
    def test_001_nonce_and_counter_fill_one_aes_block(self):
        self.assertEqual(sc.NONCE_LEN + sc.SDLS_COUNTER_BYTES, sc.AES_BLOCK_BYTES)

    # The counter range is exactly what SDLS_COUNTER_BYTES can hold.
    def test_002_counter_range_matches_its_width(self):
        self.assertEqual(sc.SDLS_COUNTER_MIN, 0)
        sc.SDLS_COUNTER_MAX.to_bytes(sc.SDLS_COUNTER_BYTES, "big")
        with self.assertRaises(OverflowError):
            (sc.SDLS_COUNTER_MAX + 1).to_bytes(sc.SDLS_COUNTER_BYTES, "big")


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_constants)
