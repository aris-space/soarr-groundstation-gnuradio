#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr_unittest
import pmt

from gnuradio.soarr import (ccsds_reader, encapsulation_header, encapsulation_parser,
                            sdls_authentication, sdls_authentication_verify,
                            sdls_decryption, sdls_encryption)

AUTH_KEY = "FFEEDDCCBBAA99887766554433221100FFEEDDCCBBAA99887766554433221100"
CRYPT_KEY = "00112233445566778899AABBCCDDEEFF00112233445566778899AABBCCDDEEFF"
SPI = 1
COUNTER = 42


class qa_sdls_rx_chain(gr_unittest.TestCase):
    """TX encapsulation + SDLS blocks -> TC frame -> RX ccsds_reader ->
    SDLS verification/decryption (-> encapsulation_parser): the original
    payload must come back byte-identical."""

    def _call(self, block, handler, msg):
        """Run one handler and return what it published (at most one PDU)."""
        out = []
        original = block.message_port_pub
        block.message_port_pub = lambda port, m: out.append(m)
        try:
            getattr(block, handler)(msg)
        finally:
            block.message_port_pub = original
        return out

    def _meta(self, **keys):
        meta = pmt.make_dict()
        for key, value in keys.items():
            value = pmt.intern(value) if isinstance(value, str) else pmt.from_long(value)
            meta = pmt.dict_add(meta, pmt.intern(key), value)
        return meta

    def _transmit(self, payload, encryption):
        """Real TX blocks, then the frame fields the TFPH/SDLS header/CRC blocks add."""
        msg = pmt.cons(self._meta(crypt_key=CRYPT_KEY, auth_key=AUTH_KEY, sdls_counter=COUNTER),
                       pmt.init_u8vector(len(payload), list(payload)))
        (msg,) = self._call(encapsulation_header(), "add_header", msg)
        (msg,) = self._call(sdls_encryption(encryption_state=encryption), "add_encryption", msg)
        (msg,) = self._call(sdls_authentication(), "add_authentication", msg)
        protected = bytes(pmt.u8vector_elements(pmt.cdr(msg)))  # (cipher)text + 16-byte tag

        body = SPI.to_bytes(2, "big") + COUNTER.to_bytes(2, "big") + protected
        tfph = ccsds_reader().tc_header().build(dict(
            tfvn=0, bypass_flag=0, control_flag=0, reserve=0, scid=0x155, vcid=0,
            frame_length=5 + len(body) + 2 - 1, fsn=0))
        return tfph + body + b"\x00\x00"  # FECF: not checked by ccsds_reader

    def _receive(self, frame, sdls_type, with_parser):
        reader = ccsds_reader(sdls_type=sdls_type, encapsulation_used=True)
        (msg,) = self._call(reader, "decode_ccsds", pmt.cons(pmt.make_dict(),
                                                            pmt.init_u8vector(len(frame), list(frame))))
        # Keys as the RX inject_db/db_client would add them
        meta = pmt.dict_add(pmt.car(msg), pmt.intern("auth_key"), pmt.intern(AUTH_KEY))
        meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.intern(CRYPT_KEY))
        msg = pmt.cons(meta, pmt.cdr(msg))

        (msg,) = self._call(sdls_authentication_verify(), "verify_message", msg)
        (msg,) = self._call(sdls_decryption(decryption_state=sdls_type in (1, 3)), "decrypt_message", msg)
        if with_parser:
            out = self._call(encapsulation_parser(), "parse_packets", msg)
            self.assertEqual(len(out), 1)
            msg = out[0]
        return bytes(pmt.u8vector_elements(pmt.cdr(msg))), pmt.car(msg)

    def test_001_encryption_and_authentication_round_trip(self):
        # Encrypted: the encapsulation header stays in the data until
        # encapsulation_parser strips it after decryption.
        for size in (5, 250, 300, 900):  # 2- and 4-byte header variants
            with self.subTest(size=size):
                payload = bytes((i * 7) & 0xFF for i in range(size))
                received, meta = self._receive(self._transmit(payload, encryption=True),
                                               sdls_type=3, with_parser=True)
                self.assertEqual(received, payload)
                header = pmt.dict_ref(meta, pmt.intern("encapsulation_header"), pmt.PMT_NIL)
                self.assertEqual(pmt.to_long(pmt.dict_ref(header, pmt.intern("packet_length"), pmt.PMT_NIL)),
                                 size + (2 if size <= 253 else 4))

    def test_002_authentication_only_round_trip(self):
        # Not encrypted: ccsds_reader parses and strips the encapsulation
        # header itself; verification rebuilds it for the CMAC input.
        for size in (5, 300):
            with self.subTest(size=size):
                payload = bytes((i * 3) & 0xFF for i in range(size))
                received, meta = self._receive(self._transmit(payload, encryption=False),
                                               sdls_type=2, with_parser=False)
                self.assertEqual(received, payload)
                self.assertTrue(pmt.dict_has_key(meta, pmt.intern("encapsulation_header")))

    def test_003_tampered_ciphertext_is_rejected(self):
        frame = bytearray(self._transmit(bytes(64), encryption=True))
        frame[12] ^= 0x01  # inside the encrypted data field
        reader = ccsds_reader(sdls_type=3, encapsulation_used=True)
        (msg,) = self._call(reader, "decode_ccsds", pmt.cons(pmt.make_dict(),
                                                            pmt.init_u8vector(len(frame), list(frame))))
        meta = pmt.dict_add(pmt.car(msg), pmt.intern("auth_key"), pmt.intern(AUTH_KEY))
        self.assertEqual(self._call(sdls_authentication_verify(), "verify_message",
                                    pmt.cons(meta, pmt.cdr(msg))), [])


if __name__ == '__main__':
    gr_unittest.run(qa_sdls_rx_chain)
