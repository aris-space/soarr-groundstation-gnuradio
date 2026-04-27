#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr, gr_unittest, digital
import pmt
import time
from types import MethodType
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Hash import CMAC

from gnuradio.sage import Injectdb
from gnuradio.sage import bchEncoder
from gnuradio.sage import cltuFramer
from gnuradio.sage import dbClient
from gnuradio.sage import encapsulationHeader
from gnuradio.sage import lfsrScrambler
from gnuradio.sage import sdlsAuthentication
from gnuradio.sage import sdlsEncryption
from gnuradio.sage import sdlsHeader
from gnuradio.sage import tcPrimaryHeader


LAYOUT_DB_TYPE = 0
LAYOUT_SDLS_ENABLED = True
LAYOUT_TC_SCID = 0 # Default
LAYOUT_TC_VCID = 0 # Default

SCRAMBLER_MASK = 0xA9
SCRAMBLER_SEED = 0xFF

BCH_POLYNOMIAL = 0xC5

CLTU_START_SEQUENCE = 0xEB90
CLTU_TAIL_SEQUENCE = 0xC5C5C5C5C5C5C579

CRC_APPEND_NUM_BITS = 32
CRC_APPEND_POLYNOMIAL = 0x4C11DB7
CRC_APPEND_INITIAL_VALUE = 0xFFFFFFFF
CRC_APPEND_FINAL_XOR = 0xFFFFFFFF
CRC_APPEND_INPUT_REFLECTED = True
CRC_APPEND_RESULT_REFLECTED = True
CRC_APPEND_SWAP_ENDIANNESS = False
CRC_APPEND_SKIP_HEADER_BYTES = 0


class layout(gr.top_block):
    """Test layout wiring all non-qa SAGE blocks left-to-right, top-to-bottom."""

    def __init__(self):
        """Instantiate the full SAGE message-flow test layout."""
        gr.top_block.__init__(self, "sage_layout_test")

        # Row 1: Inject DB path
        self.inject_db = Injectdb()
        self.db_client = dbClient(type=LAYOUT_DB_TYPE)
        self.encapsulation_header = encapsulationHeader(user_defined_field=0)

        # Row 2: SDLS protection path
        self.sdls_encryption = sdlsEncryption(state=LAYOUT_SDLS_ENABLED)
        self.sdls_authentication = sdlsAuthentication(state=LAYOUT_SDLS_ENABLED)
        self.sdls_header = sdlsHeader(iv_length_bytes=2)

        # Row 3: TC framing path
        self.tc_primary_header = tcPrimaryHeader(scid=LAYOUT_TC_SCID, vcid=LAYOUT_TC_VCID)
        self.crc_append = digital.crc_append(
            CRC_APPEND_NUM_BITS,
            CRC_APPEND_POLYNOMIAL,
            CRC_APPEND_INITIAL_VALUE,
            CRC_APPEND_FINAL_XOR,
            CRC_APPEND_INPUT_REFLECTED,
            CRC_APPEND_RESULT_REFLECTED,
            CRC_APPEND_SWAP_ENDIANNESS,
            CRC_APPEND_SKIP_HEADER_BYTES,
        )

        # Row 4: Channel coding path
        self.lfsr_scrambler = lfsrScrambler(mask=SCRAMBLER_MASK, seed=SCRAMBLER_SEED, register_length=8)
        self.bch_encoder = bchEncoder(polynomial=BCH_POLYNOMIAL)
        self.cltu_framer = cltuFramer(startSequence=CLTU_START_SEQUENCE, tailSequence=CLTU_TAIL_SEQUENCE)

        # Internal DB request/response wiring.
        self.msg_connect((self.inject_db, "db_call"), (self.db_client, "db_call"))
        self.msg_connect((self.db_client, "db_callback"), (self.inject_db, "db_callback"))

        # Left-to-right, top-to-bottom functional flow.
        self.msg_connect((self.inject_db, "out"), (self.encapsulation_header, "in"))
        self.msg_connect((self.encapsulation_header, "out"), (self.sdls_encryption, "in"))
        self.msg_connect((self.sdls_encryption, "out"), (self.sdls_authentication, "in"))
        self.msg_connect((self.sdls_authentication, "out"), (self.sdls_header, "in"))
        self.msg_connect((self.sdls_header, "out"), (self.tc_primary_header, "pdu_in"))
        self.msg_connect((self.tc_primary_header, "pdu_out"), (self.crc_append, "in"))
        self.msg_connect((self.crc_append, "out"), (self.lfsr_scrambler, "pdu_in"))
        self.msg_connect((self.lfsr_scrambler, "pdu_out"), (self.bch_encoder, "message"))
        self.msg_connect((self.bch_encoder, "codewords"), (self.cltu_framer, "pdu_in"))

class qa_layoutTest(gr_unittest.TestCase):

    def setUp(self):
        """Create a fresh flowgraph for each test case."""
        self.tb = layout()

    def tearDown(self):
        """Stop and release the test flowgraph after each test case."""
        if self.tb is not None:
            try:
                self.tb.stop()
                self.tb.wait()
            except Exception:
                pass
        self.tb = None

    def test_instance(self):
        """Test that layout can be instantiated."""
        self.assertIsNotNone(self.tb)
        self.assertIsNotNone(self.tb.inject_db)
        self.assertIsNotNone(self.tb.db_client)
        self.assertIsNotNone(self.tb.encapsulation_header)
        self.assertIsNotNone(self.tb.sdls_encryption)
        self.assertIsNotNone(self.tb.sdls_authentication)
        self.assertIsNotNone(self.tb.sdls_header)
        self.assertIsNotNone(self.tb.tc_primary_header)
        self.assertIsNotNone(self.tb.crc_append)
        self.assertIsNotNone(self.tb.lfsr_scrambler)
        self.assertIsNotNone(self.tb.bch_encoder)
        self.assertIsNotNone(self.tb.cltu_framer)


    def test_001_simple_setup(self):
        """Test full flowgraph can start and stop cleanly."""
        self.tb.start()
        self.tb.stop()
        self.tb.wait()

    def _pmt_get_int(self, meta, key):
        """Read an integer PMT value from metadata or fail the test."""
        value = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
        if pmt.eqv(value, pmt.PMT_NIL):
            self.fail(f"Missing expected metadata key '{key}'")

        try:
            return int(pmt.to_long(value))
        except Exception:
            try:
                return int(pmt.to_uint64(value))
            except Exception as exc:
                self.fail(f"Metadata key '{key}' is not an integer PMT: {exc}")

    def _capture_port(self, block, port_name, captured):
        """Capture and forward messages published on a specific port."""
        original_pub = block.message_port_pub

        def _capture(port, msg):
            """Record matching port traffic while preserving the original publish call."""
            if pmt.eqv(port, pmt.intern(port_name)):
                captured.append(msg)
            original_pub(port, msg)

        block.message_port_pub = _capture
        return original_pub

    def _capture_specific_port(self, block, port_name, captured):
        """Capture messages from one port without forwarding them onward."""
        original_pub = block.message_port_pub

        def _capture(port, msg):
            """Record matching port traffic for later assertions."""
            if pmt.eqv(port, pmt.intern(port_name)):
                captured.append(msg)

        block.message_port_pub = _capture
        return original_pub

    def _restore_port(self, block, original_pub):
        """Restore the block's original message publisher."""
        block.message_port_pub = original_pub

    def _make_pdu(self, meta=None, payload_bytes=b""):
        """Build a GNU Radio PDU from metadata and payload bytes."""
        if meta is None:
            meta = pmt.make_dict()
        return pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))

    def _bind_passthrough(self, block, method_name, in_port, out_port):
        """Replace a block handler with a pass-through shim for topology testing."""
        def _handler(this, msg):
            """Forward the incoming message to the configured output port."""
            this.message_port_pub(pmt.intern(out_port), msg)

        _handler.__name__ = method_name
        setattr(block, method_name, MethodType(_handler, block))
        block.set_msg_handler(pmt.intern(in_port), getattr(block, method_name))

    def test_002_end_to_end_message_routing(self):
        """Inject one PDU and verify it reaches final output through all wired stages."""

        FRAME_SEQUENCE_NUMBER = 7
        PAYLOAD_BYTES = bytes([0xDE, 0xAD, 0xBE, 0xEF])

        # Replace all real handlers with pass-through shims to verify the message routing and integrity through the layout.
        self._bind_passthrough(self.tb.inject_db, "send_db_call", "in", "db_call")
        self._bind_passthrough(self.tb.db_client, "make_db_call", "db_call", "db_callback")
        self._bind_passthrough(self.tb.inject_db, "send_msg_out", "db_callback", "out")
        self._bind_passthrough(self.tb.encapsulation_header, "add_header", "in", "out")
        self._bind_passthrough(self.tb.sdls_encryption, "add_encryption", "in", "out")
        self._bind_passthrough(self.tb.sdls_authentication, "add_authentication", "in", "out")
        self._bind_passthrough(self.tb.sdls_header, "add_header", "in", "out")
        self._bind_passthrough(self.tb.tc_primary_header, "build_header", "pdu_in", "pdu_out")
        self._bind_passthrough(self.tb.lfsr_scrambler, "handle_msg", "pdu_in", "pdu_out")
        self._bind_passthrough(self.tb.bch_encoder, "encodeBCH", "message", "codewords")
        self._bind_passthrough(self.tb.cltu_framer, "addSequences", "pdu_in", "pdu_out")

        # Capture the pdu_out outputs
        captured = []
        original_pub = self._capture_port(self.tb.cltu_framer, "pdu_out", captured)

        # Build a test PDU with metadata and payload to inject into the flowgraph.
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_sequence_number"), pmt.from_long(FRAME_SEQUENCE_NUMBER))
        payload = pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES))
        pdu_in = pmt.cons(meta, payload)

        # Start the flowgraph and inject the test PDU
        self.tb.start()
        self.tb.inject_db.to_basic_block()._post(pmt.intern("in"), pdu_in)

        for _ in range(50):
            if captured:
                break
            time.sleep(0.01)

        self.tb.stop()
        self.tb.wait()

        # Restore the original publisher for the final block to avoid side effects on other tests.
        self._restore_port(self.tb.cltu_framer, original_pub)

        # Verify the captured message. With the real GNU Radio CRC append block in the chain,
        # payload should be the original payload plus a 4-byte CRC while metadata is preserved.
        self.assertEqual(len(captured), 1)
        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertEqual(self._pmt_get_int(out_meta, "frame_sequence_number"), FRAME_SEQUENCE_NUMBER)
        self.assertEqual(out_bytes[:len(PAYLOAD_BYTES)], PAYLOAD_BYTES)
        self.assertEqual(len(out_bytes), len(PAYLOAD_BYTES) + 4)


    def test_003_dbclient_distinct_spi_entries_for_same_scid(self):
        """Verify one SCID can resolve to multiple distinct SPI entries with unique material."""

        TEST_SCID = 341
        TEST_SPI_1 = 1
        TEST_SPI_2 = 2
        TEST_VCID_1 = 18
        TEST_VCID_2 = 5
        TEST_SDLS_COUNTER_1 = 0
        TEST_SDLS_COUNTER_2 = 120
        TEST_VCID_COUNTER_1 = 0
        TEST_VCID_COUNTER_2 = 44

        # Use a real dbClient instance with the example YAML
        # to verify it can resolve two distinct SPI entries for the same SCID and that the returned metadata contains expected fields with different values.
        yaml_path = Path(__file__).resolve().parents[2] / "examples" / "dbClient_example.yaml"
        client = dbClient(type=1, yaml_path=str(yaml_path))

        # Capture the db_callback outputs
        captured = []
        original_pub = self._capture_specific_port(client, "db_callback", captured)

        def _query(scid, spi):
            """Issue a DB query for one SCID/SPI pair."""
            meta = pmt.make_dict()
            meta = pmt.dict_add(meta, pmt.intern("scid"), pmt.from_long(scid))
            meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.from_long(spi))
            msg = pmt.cons(meta, pmt.PMT_NIL)
            client.make_db_call(msg)

        try:
            _query(TEST_SCID, TEST_SPI_1) # SCID 341 with SPI 1 should return one entry
            _query(TEST_SCID, TEST_SPI_2) # SCID 341 with SPI 2 should return an other entry
        finally:
            self._restore_port(client, original_pub)

        # Wait for both callbacks to be captured
        self.assertEqual(len(captured), 2)

        first_meta = pmt.car(captured[0])
        second_meta = pmt.car(captured[1])

        # Verify both entries have the same SCID but different VCIDs
        self.assertEqual(self._pmt_get_int(first_meta, "vcid"), TEST_VCID_1)
        self.assertEqual(self._pmt_get_int(second_meta, "vcid"), TEST_VCID_2)

        # Verify both entries have cryptographic keys and that they are different
        first_crypt = pmt.symbol_to_string(pmt.dict_ref(first_meta, pmt.intern("crypt_key"), pmt.PMT_NIL))
        second_crypt = pmt.symbol_to_string(pmt.dict_ref(second_meta, pmt.intern("crypt_key"), pmt.PMT_NIL))
        first_auth = pmt.symbol_to_string(pmt.dict_ref(first_meta, pmt.intern("auth_key"), pmt.PMT_NIL))
        second_auth = pmt.symbol_to_string(pmt.dict_ref(second_meta, pmt.intern("auth_key"), pmt.PMT_NIL))

        self.assertNotEqual(first_crypt, second_crypt)
        self.assertNotEqual(first_auth, second_auth)

        # Verify both entries have sequence counters and that they are different
        first_sdls = self._pmt_get_int(first_meta, "sdls_counter")
        second_sdls = self._pmt_get_int(second_meta, "sdls_counter")
        first_vcid_counter = self._pmt_get_int(first_meta, "vcid_counter")
        second_vcid_counter = self._pmt_get_int(second_meta, "vcid_counter")

        self.assertEqual(first_sdls, TEST_SDLS_COUNTER_1)
        self.assertEqual(first_vcid_counter, TEST_VCID_COUNTER_1)
        self.assertEqual(second_sdls, TEST_SDLS_COUNTER_2)
        self.assertEqual(second_vcid_counter, TEST_VCID_COUNTER_2)

    def test_004_tc_primary_header_real_handler(self):
        """Verify the real TC primary header block populates SCID/VCID defaults and keeps upstream sequence metadata."""

        TEST_SCID = 0x155
        TEST_VCID = 0x12
        TEST_SEQUENCE_NUMBER = 9
        PAYLOAD_BYTES = bytes([0x11, 0x22, 0x33])

        block = tcPrimaryHeader(scid=TEST_SCID, vcid=TEST_VCID)
        captured = []
        original_pub = self._capture_specific_port(block, "pdu_out", captured)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_sequence_number"), pmt.from_long(TEST_SEQUENCE_NUMBER))
        payload = pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES))
        msg = pmt.cons(meta, payload)

        try:
            block.build_header(msg)
        finally:
            self._restore_port(block, original_pub)

        self.assertEqual(len(captured), 1)

        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertEqual(self._pmt_get_int(out_meta, "scid"), TEST_SCID)
        self.assertEqual(self._pmt_get_int(out_meta, "vcid"), TEST_VCID)
        self.assertEqual(self._pmt_get_int(out_meta, "frame_sequence_number"), TEST_SEQUENCE_NUMBER)
        self.assertEqual(out_bytes[-len(PAYLOAD_BYTES):], PAYLOAD_BYTES)

    def test_005_sdls_header_real_handler(self):
        """Verify the real SDLS header block prepends SPI and IV bytes and removes consumed metadata."""

        FRAME_ID = 21
        SPI_BYTES = bytes([0x12, 0x34])
        IV_BYTES = bytes([0xAB, 0xCD])
        PAYLOAD_BYTES = bytes([0x55, 0x66, 0x77])

        block = sdlsHeader(iv_length_bytes=2)
        captured = []
        original_pub = self._capture_specific_port(block, "out", captured)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("spi"), pmt.init_u8vector(len(SPI_BYTES), list(SPI_BYTES)))
        meta = pmt.dict_add(
            meta,
            pmt.intern("initialization_vector"),
            pmt.init_u8vector(len(IV_BYTES), list(IV_BYTES)),
        )
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(FRAME_ID))
        msg = pmt.cons(meta, pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES)))

        try:
            block.add_header(msg)
        finally:
            self._restore_port(block, original_pub)

        self.assertEqual(len(captured), 1)

        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("spi")))
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("initialization_vector")))
        self.assertEqual(out_bytes, SPI_BYTES + IV_BYTES + PAYLOAD_BYTES)

    def test_006_sdls_encryption_real_handler(self):
        """Verify the real SDLS encryption block encrypts with AES-CTR and removes the key from metadata."""

        FRAME_ID = 42
        KEY_BYTES = bytes(range(32))
        COUNTER = 23
        PAYLOAD_BYTES = bytes([0x10, 0x20, 0x30, 0x40, 0x50])

        block = sdlsEncryption(state=True, nonce=b"\x00" * 14)
        captured = []
        original_pub = self._capture_specific_port(block, "out", captured)
        expected_ciphertext = AES.new(KEY_BYTES, AES.MODE_CTR, nonce=b"\x00" * 14, initial_value=COUNTER).encrypt(PAYLOAD_BYTES)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("crypt_key"), pmt.intern(KEY_BYTES.hex().upper()))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(COUNTER))
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(FRAME_ID))
        msg = pmt.cons(meta, pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES)))

        try:
            block.add_encryption(msg)
        finally:
            self._restore_port(block, original_pub)

        self.assertEqual(len(captured), 1)

        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("sdls_counter")))
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("crypt_key")))
        self.assertEqual(self._pmt_get_int(out_meta, "sdls_counter"), COUNTER)
        self.assertEqual(out_bytes, expected_ciphertext)

    def test_007_sdls_authentication_real_handler(self):
        """Verify the real SDLS authentication block appends a CMAC tag and removes the auth key."""

        FRAME_ID = 99
        KEY_BYTES = bytes(range(32, 64))
        COUNTER = 31
        PAYLOAD_BYTES = bytes([0xA1, 0xB2, 0xC3, 0xD4])

        block = sdlsAuthentication(state=True, nonce=b"\x00" * 14)
        captured = []
        original_pub = self._capture_specific_port(block, "out", captured)
        mac_input = b"\x00" * 14 + COUNTER.to_bytes(2, byteorder="big", signed=False) + PAYLOAD_BYTES
        expected_tag = CMAC.new(KEY_BYTES, ciphermod=AES)
        expected_tag.update(mac_input)
        expected_tag_bytes = expected_tag.digest()

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("auth_key"), pmt.intern(KEY_BYTES.hex().upper()))
        meta = pmt.dict_add(meta, pmt.intern("sdls_counter"), pmt.from_long(COUNTER))
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(FRAME_ID))
        msg = pmt.cons(meta, pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES)))

        try:
            block.add_authentication(msg)
        finally:
            self._restore_port(block, original_pub)

        self.assertEqual(len(captured), 1)

        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("sdls_counter")))
        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertFalse(pmt.dict_has_key(out_meta, pmt.intern("auth_key")))
        self.assertEqual(self._pmt_get_int(out_meta, "sdls_counter"), COUNTER)
        self.assertEqual(out_bytes, PAYLOAD_BYTES + expected_tag_bytes)

    def test_008_encapsulation_header_real_handler(self):
        """Verify the real encapsulation header block prepends the expected CCSDS header bytes."""

        FRAME_ID = 77
        PAYLOAD_BYTES = bytes([0x01, 0x02, 0x03])

        block = encapsulationHeader(user_defined_field=0)
        captured = []
        original_pub = self._capture_specific_port(block, "out", captured)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(FRAME_ID))
        msg = pmt.cons(meta, pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES)))

        try:
            block.add_header(msg)
        finally:
            self._restore_port(block, original_pub)

        self.assertEqual(len(captured), 1)

        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertEqual(out_bytes, bytes([0xFD, 0x05]) + PAYLOAD_BYTES)

    def test_009_lfsr_scrambler_real_handler(self):
        """Verify the real LFSR scrambler XORs payload bytes with the CCSDS randomizer sequence."""

        FRAME_ID = 88
        PAYLOAD_BYTES = bytes([0x00])

        block = lfsrScrambler(mask=0xA9, seed=0xFF, register_length=8)
        captured = []
        original_pub = self._capture_specific_port(block, "pdu_out", captured)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(FRAME_ID))
        msg = pmt.cons(meta, pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES)))

        try:
            block.handle_msg(msg)
        finally:
            self._restore_port(block, original_pub)

        self.assertEqual(len(captured), 1)

        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertEqual(out_bytes, bytes([0xFF]))

    def test_010_bch_encoder_real_handler(self):
        """Verify the real BCH encoder emits a 64-bit codeword with expected parity bits for one code block."""

        FRAME_ID = 101
        PAYLOAD_BYTES = bytes([0x00] * 7)
        EXPECTED_PARITY_BYTE = 0xFE

        block = bchEncoder(polynomial=0xC5)
        captured = []
        original_pub = self._capture_specific_port(block, "codewords", captured)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(FRAME_ID))
        msg = pmt.cons(meta, pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES)))

        try:
            block.encodeBCH(msg)
        finally:
            self._restore_port(block, original_pub)

        self.assertEqual(len(captured), 1)

        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertEqual(len(out_bytes), 8)
        self.assertEqual(out_bytes[:7], PAYLOAD_BYTES)
        self.assertEqual(out_bytes[7], EXPECTED_PARITY_BYTE)

    def test_011_cltu_framer_real_handler(self):
        """Verify the real CLTU framer prepends the start sequence and appends the tail sequence."""

        FRAME_ID = 202
        START_SEQUENCE = 0xEB90
        TAIL_SEQUENCE = 0xC5C5C5C5C5C5C579
        PAYLOAD_BYTES = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07])

        block = cltuFramer(startSequence=START_SEQUENCE, tailSequence=TAIL_SEQUENCE)
        captured = []
        original_pub = self._capture_specific_port(block, "pdu_out", captured)

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("frame_id"), pmt.from_long(FRAME_ID))
        msg = pmt.cons(meta, pmt.init_u8vector(len(PAYLOAD_BYTES), list(PAYLOAD_BYTES)))

        try:
            block.addSequences(msg)
        finally:
            self._restore_port(block, original_pub)

        self.assertEqual(len(captured), 1)

        out_msg = captured[0]
        out_meta = pmt.car(out_msg)
        out_body = pmt.cdr(out_msg)
        out_bytes = bytes(pmt.u8vector_elements(out_body))

        self.assertTrue(pmt.dict_has_key(out_meta, pmt.intern("frame_id")))
        self.assertEqual(out_bytes, bytes.fromhex("EB90") + PAYLOAD_BYTES + bytes.fromhex("C5C5C5C5C5C5C579"))

if __name__ == '__main__':
    gr_unittest.run(qa_layoutTest)
