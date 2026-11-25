#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import time

from Crypto.Cipher import AES
from Crypto.Hash import CMAC
from gnuradio import blocks, gr, gr_unittest
import pmt

from gnuradio.soarr import (bch_encoder, encapsulation_header, encapsulation_packet, key_database,
                            lfsr_scrambler, sdls_apply, tc_frame_encoder)
from gnuradio.soarr.db_client import TEST_AUTH_KEY, TEST_CRYPT_KEY

SCID, VCID = 0x155, 0x12
START, TAIL = bytes.fromhex("eb90"), bytes.fromhex("c5c5c5c5c5c5c579")


def _crc16_ccitt_false(data):
    """CCSDS 232.0-B FECF, computed here independently: CRC-16/CCITT,
    preset all ones, no final XOR."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _tc_header(data_len, fsn, scid=SCID, vcid=VCID):
    """TFPH from its bit fields (CCSDS 232.0-B): version, bypass, control,
    spare all 0; frame length counts the 5 header and 2 FECF bytes, minus 1."""
    frame_length = 5 + data_len + 2 - 1
    value = (scid << 24) | (vcid << 18) | (frame_length << 8) | fsn
    return value.to_bytes(5, "big")


def _cltu(frame):
    """Scramble, BCH-encode and wrap a frame, using the scrambler's and the
    encoder's own (separately known-answer-tested) helpers."""
    scrambled = bytes(lfsr_scrambler().apply_scrambling(frame))
    encoder = bch_encoder()
    bits = encoder._bytes_to_bits(scrambled)
    bits, _ = encoder._apply_fill_bits(bits)
    codewords = bytearray()
    for start in range(0, len(bits), 56):
        info = encoder._bits_to_bytes(bits[start:start + 56])
        parity = encoder._compute_parity_bits(info)
        codewords += info + encoder._bits_to_bytes(list(parity) + [0])
    return START + bytes(codewords) + TAIL


class qa_tc_frame_encoder(gr_unittest.TestCase):

    def _run(self, head, tail, pdu, count=1):
        """Strobe the PDU into `head`, collect `count` outputs of `tail`."""
        tb = gr.top_block()
        source = blocks.message_strobe(pdu, 50)
        sink = blocks.message_debug()
        tb.msg_connect((source, "strobe"), (head, "in"))
        tb.msg_connect((tail, "out"), (sink, "store"))
        tb.start()
        for _ in range(300):
            if sink.num_messages() >= count:
                break
            time.sleep(0.01)
        tb.stop()
        tb.wait()
        self.assertGreaterEqual(sink.num_messages(), count)
        return tb, [bytes(pmt.u8vector_elements(pmt.cdr(sink.get_message(i)))) for i in range(count)]

    def test_001_cltu_matches_the_ccsds_construction(self):
        data = bytes(range(20))
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("vcid_counter"), pmt.from_long(5))
        dut = tc_frame_encoder(scid=SCID, vcid=VCID)
        _, (cltu,) = self._run(dut, dut, pmt.cons(meta, pmt.init_u8vector(len(data), list(data))))

        frame = _tc_header(len(data), fsn=5) + data
        frame += _crc16_ccitt_false(frame).to_bytes(2, "big")
        self.assertEqual(cltu, _cltu(frame))

    # The whole TX path from hierarchical blocks, recomputed bit for bit:
    # encapsulation, AES-CTR, CMAC, SDLS header, TC frame, FECF, CLTU.
    def test_002_full_tx_path(self):
        nonce = bytes(range(1, 15))
        payload = bytes([0xDE, 0xAD, 0xBE, 0xEF])

        tc = pmt.make_dict()
        for key, value in (("scid", SCID), ("vcid", VCID), ("vcid_counter", 0)):
            tc = pmt.dict_add(tc, pmt.intern(key), pmt.from_long(value))
        tc = pmt.dict_add(tc, pmt.intern("bypass_flag"), pmt.from_bool(False))
        tc = pmt.dict_add(tc, pmt.intern("control_flag"), pmt.from_bool(False))
        header = pmt.dict_add(pmt.make_dict(), pmt.intern("spi"), pmt.from_long(1))
        meta = pmt.dict_add(pmt.make_dict(), pmt.intern("telecommand"), pmt.dict_add(pmt.make_dict(), pmt.intern("tc_header"), tc))
        meta = pmt.dict_add(meta, pmt.intern("sdls"), pmt.dict_add(pmt.make_dict(), pmt.intern("security_header"), header))

        keys, encap, sdls, encoder = key_database(role="tx"), encapsulation_header(), sdls_apply(3, nonce), tc_frame_encoder()
        tb = gr.top_block()
        tb.msg_connect((keys, "out"), (encap, "in"))
        tb.msg_connect((encap, "out"), (sdls, "in"))
        tb.msg_connect((sdls, "out"), (encoder, "in"))
        source = blocks.message_strobe(pmt.cons(meta, pmt.init_u8vector(len(payload), list(payload))), 50)
        sink = blocks.message_debug()
        tb.msg_connect((source, "strobe"), (keys, "in"))
        tb.msg_connect((encoder, "out"), (sink, "store"))
        tb.start()
        for _ in range(300):
            if sink.num_messages():
                break
            time.sleep(0.01)
        tb.stop()
        tb.wait()
        self.assertGreaterEqual(sink.num_messages(), 1)
        cltu = bytes(pmt.u8vector_elements(pmt.cdr(sink.get_message(0))))

        counter = 0  # the key store's first SDLS counter and frame sequence number
        packet = encapsulation_packet.header_for_payload(len(payload)) + payload
        ciphertext = AES.new(bytes.fromhex(TEST_CRYPT_KEY), AES.MODE_CTR, nonce=nonce,
                             initial_value=counter).encrypt(packet)
        cmac = CMAC.new(bytes.fromhex(TEST_AUTH_KEY), ciphermod=AES)
        cmac.update(nonce + counter.to_bytes(2, "big") + ciphertext)
        data = (1).to_bytes(2, "big") + counter.to_bytes(2, "big") + ciphertext + cmac.digest()
        frame = _tc_header(len(data), fsn=counter) + data
        frame += _crc16_ccitt_false(frame).to_bytes(2, "big")
        self.assertEqual(cltu, _cltu(frame))


if __name__ == '__main__':
    gr_unittest.run(qa_tc_frame_encoder)
