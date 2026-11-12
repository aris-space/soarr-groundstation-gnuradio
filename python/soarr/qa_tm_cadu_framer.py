#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest
import pmt

from gnuradio.soarr import cltu_burst_builder, tm_cadu_framer
from gnuradio.soarr.tm_cadu_framer import (ASM_32, ASM_64, ASM_96, ASM_128, ASM_192, ASM_EMBEDDED, ASMS)

# CCSDS 131.0-B-5, figures 9-1 to 9-6: the ASM bit patterns, first
# transmitted bit leftmost - transcribed from the figures, independently of
# the hexadecimal notation the block uses.
FIGURE_BITS = {
    ASM_32: "0001 1010 1100 1111 1111 1100 0001 1101",
    ASM_64: "0000001101000111011101101100011100100111001010001001010110110000",
    ASM_96: "001001011101010111000000110011101000100110010000111101101100100101000110000110111111011110011100",
    ASM_128: "0000001101000111011101101100011100100111001010001001010110110000"
             "1111110010111000100010010011100011011000110101110110101001001111",
    ASM_192: "001001011101010111000000110011101000100110010000111101101100100101000110000110111111011110011100"
             "110110100010101000111111001100010111011001101111000010010011011010111001111001000000100001100011",
    ASM_EMBEDDED: "0011 0101 0010 1110 1111 1000 0101 0011",
}


def _bits(data):
    return "".join(format(byte, "08b") for byte in data)


class qa_tm_cadu_framer(gr_unittest.TestCase):

    def _run(self, block, msg, handler="add_asm"):
        published = []
        block.message_port_pub = lambda port, m: published.append(m)
        getattr(block, handler)(msg)
        return published

    def _pdu(self, data, meta=None):
        meta = pmt.make_dict() if meta is None else meta
        return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))

    def _payload(self, msg):
        return bytes(pmt.u8vector_elements(pmt.cdr(msg)))

    def test_001_asms_match_the_standards_bit_patterns(self):
        self.assertEqual(set(ASMS), set(FIGURE_BITS))
        for asm, figure in FIGURE_BITS.items():
            self.assertEqual(_bits(bytes.fromhex(asm)), figure.replace(" ", ""), asm)

    # A CADU is the ASM and the codeblock, and nothing else (9.1.2, 9.4).
    def test_002_cadu_is_asm_then_codeblock(self):
        codeblock = bytes(range(255))
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("frame"), pmt.from_long(3))
        (out,) = self._run(tm_cadu_framer(), self._pdu(codeblock, meta))
        self.assertEqual(self._payload(out), bytes.fromhex("1ACFFC1D") + codeblock)
        self.assertTrue(pmt.equal(pmt.car(out), meta))

    def test_003_every_asm_option(self):
        codeblock = bytes([0xAB] * 10)
        for asm in ASMS:
            (out,) = self._run(tm_cadu_framer(asm=asm), self._pdu(codeblock))
            self.assertEqual(self._payload(out), bytes.fromhex(asm) + codeblock, asm)

    def test_004_length_check(self):
        on = tm_cadu_framer(length_check=True, codeblock_length=255)
        self.assertEqual(len(self._run(on, self._pdu(bytes(255)))), 1)
        self.assertEqual(self._run(on, self._pdu(bytes(223))), [])
        off = tm_cadu_framer(length_check=False, codeblock_length=255)
        self.assertEqual(len(self._run(off, self._pdu(bytes(100)))), 1)

    # Preamble and tail are the burst's, not the CADU's: CADU Framer then
    # Burst Builder gives 5 x 0x55, ASM, codeblock, 5 x 0x55.
    def test_005_preamble_and_tail_come_from_the_burst_builder(self):
        codeblock = bytes(range(255))
        (cadu,) = self._run(tm_cadu_framer(), self._pdu(codeblock))
        burst_builder = cltu_burst_builder(acquisition_length=5, tail_length=5, fill_byte=0x55)
        (burst,) = self._run(burst_builder, cadu, handler="build_burst")
        self.assertEqual(self._payload(burst), bytes([0x55] * 5) + bytes.fromhex("1ACFFC1D") + codeblock + bytes([0x55] * 5))

    def test_006_malformed_input_is_dropped(self):
        block = tm_cadu_framer()
        self.assertEqual(self._run(block, pmt.intern("not a pdu")), [])
        self.assertEqual(self._run(block, pmt.cons(pmt.make_dict(), pmt.intern("not bytes"))), [])
        self.assertEqual(self._run(block, self._pdu(b"")), [])

    def test_007_publish_failure_is_dropped_not_raised(self):
        block = tm_cadu_framer()

        def _raise(port, msg):
            raise RuntimeError("simulated publish failure")

        block.message_port_pub = _raise
        block.add_asm(self._pdu(bytes(10)))  # must not raise

    def test_008_invalid_settings_raise(self):
        for kwargs in ({"asm": "DEADBEEF"}, {"length_check": True, "codeblock_length": 0}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                tm_cadu_framer(**kwargs)


if __name__ == '__main__':
    gr_unittest.run(qa_tm_cadu_framer)
