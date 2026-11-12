#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest
import numpy as np
import pmt

from gnuradio.soarr import tm_randomizer
from gnuradio.soarr.tm_randomizer import LFSR_17, LFSR_8, sequence_bytes

# CCSDS 131.0-B-5 10.4.3, note 2: the first 40 bits of each sequence.
FIRST_40_BITS = {
    LFSR_17: "0001 1100 0111 0001 1011 1001 0001 1011 1010 1001",
    LFSR_8: "1111 1111 0100 1000 0000 1110 1100 0000 1001 1010",
}
# h(x) without its x^d term, as the recurrence s[n+d] = XOR of s[n+k].
RECURRENCE = {LFSR_17: (14, 0), LFSR_8: (7, 5, 3, 0)}


def _bits(data):
    return np.unpackbits(np.frombuffer(bytes(data), dtype=np.uint8))


class qa_tm_randomizer(gr_unittest.TestCase):

    def _run(self, block, msg):
        published = []
        block.message_port_pub = lambda port, m: published.append(m)
        block.handle_msg(msg)
        return published

    def _pdu(self, data, meta=None):
        meta = pmt.make_dict() if meta is None else meta
        return pmt.cons(meta, pmt.init_u8vector(len(data), list(data)))

    def _payload(self, msg):
        return bytes(pmt.u8vector_elements(pmt.cdr(msg)))

    # Randomizing zeros yields the sequence itself: it must start with the
    # standard's 40 bits, for both sequences.
    def test_001_first_40_bits_match_the_standard(self):
        for register_length, expected in FIRST_40_BITS.items():
            (out,) = self._run(tm_randomizer(register_length=register_length), self._pdu(bytes(5)))
            self.assertEqual("".join(map(str, _bits(self._payload(out)))), expected.replace(" ", ""),
                             f"{register_length}-bit LFSR")

    # Each sequence follows its polynomial and has the full period of its
    # register, 2^n - 1 bits: 131071 for the 17-bit LFSR, 255 for the 8-bit.
    def test_002_recurrence_and_period(self):
        for degree, taps in RECURRENCE.items():
            period = 2 ** degree - 1
            bits = _bits(sequence_bytes(degree, (2 * period + 7) // 8 + 1))
            self.assertTrue(all(bits[n + degree] == np.bitwise_xor.reduce(bits[[n + k for k in taps]])
                                for n in range(len(bits) - degree)), f"{degree}-bit LFSR recurrence")
            self.assertTrue(np.array_equal(bits[:period], bits[period:2 * period]), f"{degree}-bit LFSR period")
            self.assertFalse(np.array_equal(bits[:64], bits[period // 2:period // 2 + 64]))

    # XOR is its own inverse, and the sequence restarts with every PDU:
    # the same block randomizes on TX and derandomizes on RX.
    def test_003_round_trip_and_restart_per_pdu(self):
        block = tm_randomizer()
        data = bytes(range(255))
        (first,) = self._run(block, self._pdu(data))
        (second,) = self._run(block, self._pdu(data))
        self.assertEqual(self._payload(first), self._payload(second))
        self.assertNotEqual(self._payload(first), data)
        (restored,) = self._run(block, self._pdu(self._payload(first)))
        self.assertEqual(self._payload(restored), data)

    def test_004_metadata_is_kept(self):
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("frame"), pmt.from_long(7))
        (out,) = self._run(tm_randomizer(), self._pdu(bytes(10), meta))
        self.assertTrue(pmt.equal(pmt.car(out), meta))

    # A codeblock longer than one period still gets the repeating sequence.
    def test_005_longer_than_the_8_bit_lfsr_period(self):
        (out,) = self._run(tm_randomizer(register_length=LFSR_8), self._pdu(bytes(70)))
        bits = _bits(self._payload(out))
        self.assertTrue(np.array_equal(bits[:255], bits[255:510]))

    def test_006_length_check(self):
        on = tm_randomizer(length_check=True, codeblock_length=255)
        self.assertEqual(len(self._run(on, self._pdu(bytes(255)))), 1)
        self.assertEqual(self._run(on, self._pdu(bytes(254))), [])
        off = tm_randomizer(length_check=False, codeblock_length=255)
        self.assertEqual(len(self._run(off, self._pdu(bytes(100)))), 1)

    def test_007_malformed_input_is_dropped(self):
        block = tm_randomizer()
        self.assertEqual(self._run(block, pmt.intern("not a pdu")), [])
        self.assertEqual(self._run(block, pmt.cons(pmt.make_dict(), pmt.intern("not bytes"))), [])

    def test_008_publish_failure_is_dropped_not_raised(self):
        block = tm_randomizer()

        def _raise(port, msg):
            raise RuntimeError("simulated publish failure")

        block.message_port_pub = _raise
        block.handle_msg(self._pdu(bytes(10)))  # must not raise

    def test_009_invalid_settings_raise(self):
        for kwargs in ({"register_length": 16}, {"length_check": True, "codeblock_length": 0},
                       {"length_check": True, "codeblock_length": -1}):
            with self.assertRaises(ValueError, msg=str(kwargs)):
                tm_randomizer(**kwargs)


if __name__ == '__main__':
    gr_unittest.run(qa_tm_randomizer)
