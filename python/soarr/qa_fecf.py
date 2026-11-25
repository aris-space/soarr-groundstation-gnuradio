#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest

from gnuradio.soarr.fecf import FECF_BYTES, FECF_CRC, fecf_crc


class qa_fecf(gr_unittest.TestCase):

    # CRC-16/CCITT-FALSE's check value: the CRC of "123456789" is 0x29B1.
    def test_001_check_value(self):
        self.assertEqual(fecf_crc().compute(list(b"123456789")), 0x29B1)

    # The parameters TC and TM frames use (232.0-B 4.1.4, 132.0-B-3 4.1.6).
    def test_002_parameters(self):
        self.assertEqual(FECF_BYTES, 2)
        self.assertEqual(FECF_CRC, {"num_bits": 16, "poly": 0x1021, "initial_value": 0xFFFF, "final_xor": 0x0000,
                                    "input_reflected": False, "result_reflected": False, "swap_endianness": False})


if __name__ == '__main__':
    gr_unittest.run(qa_fecf)
