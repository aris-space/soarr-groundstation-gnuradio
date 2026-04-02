#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import struct
import time
import pmt
import logging

from gnuradio import gr, gr_unittest, blocks
# from gnuradio.sage import cltuFramer
from sage import cltuFramer  # Import from the same directory, not from gnuradio.sage

Log = logging.getLogger("qa_cltuFramer")

START = 0xEB90
END = 0xC5C5_C5C5_C5C5_C579


class qa_cltuFramer(gr_unittest.TestCase):

    def setUp(self):
        self.tb = gr.top_block()

    def tearDown(self):
        self.tb = None

    def test_instance(self):
        Log.info("Testing CLTU Framer instantiation")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)

        self.assertIsNotNone(self.dut)
        self.assertEqual(self.dut.startSequence, START)
        self.assertEqual(self.dut.tailSequence, END)
        self.assertEqual(self.dut.name(), "CLTU Framer")
        # FIXME: Test will fail until you pass sensible arguments to the constructor

    def test_001_functionality_check(self):
        Log.info("Test 001: Functionality Check - Basic CLTU Framing")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        # Capture the output
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        # Create a test PDU with known content
        input_dict = pmt.make_dict()  # Create an empty PMT dictionary for metadata
        input_dict = pmt.dict_add(input_dict, pmt.intern("test_key"), pmt.from_long(777))  # Add some metadata
        input_data = [i%256 for i in range(1, 8)]  # Sample payload data
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        Log.info(f"Test data size is {len(input_data)} Bytes --> {len(input_data)*8} bits")

        # Run the flowgraph
        self.tb.start()

        # Send the test PDU to the DUT
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)


        time.sleep(0.5)  # Wait for the message to be processed

        self.tb.stop()  # Stop the flowgraph
        self.tb.wait()  # Wait for the flowgraph to finish stopping

        # Check the number of output messages
        number_of_messages = sink.num_messages()
        self.assertEqual(number_of_messages, 1)  # We expect exactly one message to be output

        # Get the first message from the sink
        result = sink.get_message(0)

        paylaod = bytes(pmt.u8vector_elements(pmt.cdr(result)))
        start_bytes = struct.pack('!H', START)
        tail_bytes = struct.pack('!Q', END)
        exprected_payload = start_bytes + bytes(input_data) + tail_bytes

        Log.info(f"Output payload size is {len(paylaod)} Bytes --> {len(paylaod)*8} bits")
        self.assertEqual(len(paylaod), len(exprected_payload))  # Check if the payload size is correct
        self.assertEqual(paylaod, exprected_payload)  # Check if the payload content is correct

        # Check if the metadata is preserved
        output_dict = pmt.car(result)
        self.assertTrue(pmt.is_dict(input_dict))  # Sanitiy check for the input metadata
        self.assertTrue(pmt.is_dict(output_dict))  # Check if the metadata is still a dictionary
        self.assertEqual(input_dict, output_dict)  # Check if the metadata dictionary is unchanged

    def test_002_empty_payload(self):
        Log.info("Test 002: Empty Payload Check - Should Not Produce Output")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        # Capture the output
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        # Create a test PDU with an empty payload
        input_dict = pmt.make_dict()  # Create an empty PMT dictionary for metadata
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(0, []))  # Empty payload

        # Run the flowgraph
        self.tb.start()

        # Send the test PDU to the DUT
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)

        time.sleep(0.5)  # Wait for the message to be processed

        self.tb.stop()  # Stop the flowgraph
        self.tb.wait()  # Wait for the flowgraph to finish stopping

        # Check that no output message was produced due to the empty payload
        self.assertEqual(sink.num_messages(), 0, "Es wurde fälschlicherweise eine PDU ausgegeben, obwohl die Payload leer war!")

    def test_003_wrong_payload_size(self):
        Log.info("Test 003: Wrong Payload Size Check - Should Not Produce Output")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        # Define output sink to capture the output
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        # Create a test PDU with incorrect payload size (e.g., 3 bytes instead of 7)
        input_dict = pmt.make_dict()
        input_data = [0x01, 0x02, 0x03] 
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        # 3. Flowgraph ausführen
        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2) # Kurz warten
        self.tb.stop()
        self.tb.wait()

        # No output should be produced due to the wrong payload size
        self.assertEqual(sink.num_messages(), 0, "Es wurde fälschlicherweise eine PDU ausgegeben, obwohl die Größe falsch war!")


        # Check if the error message was logged
        with self.assertLogs("gnuradio.sage.cltuFramer", level='ERROR') as cm:
             
             # Manual trigger: 
             self.dut.addSequences(input_pdu)
             
             # Check if the error message was logged
             self.assertTrue(any("Payload size is 3 bytes, expected 7 bytes" in s for s in cm.output))

    def test_004_non_u8vector_payload(self):
        Log.info("Test 004: Non-u8vector Payload Check - Should Not Produce Output")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        # Define output sink to capture the output
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        # Create a test PDU with a non-u8vector payload (e.g., a string)
        input_dict = pmt.make_dict()
        input_data = "This is not a u8vector"
        input_pdu = pmt.cons(input_dict, pmt.intern(input_data))

        # Run the flowgraph
        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2) # Kurz warten
        self.tb.stop()
        self.tb.wait()

        # No output should be produced due to the non-u8vector payload
        self.assertEqual(sink.num_messages(), 0, "Es wurde fälschlicherweise eine PDU ausgegeben, obwohl die Payload kein u8vector war!")

        # Check if the error message was logged
        with self.assertLogs("gnuradio.sage.cltuFramer", level='ERROR') as cm:
             
             # Manual trigger: 
             self.dut.addSequences(input_pdu)
             
             # Check if the error message was logged
             self.assertTrue(any("Input message body is not a PDU." in s for s in cm.output))

    def test_005_custom_sequences(self):
        Log.info("Test 005: Custom Start and Tail Sequences")
        CUSTOM_START = 0x1234
        CUSTOM_END = 0xABCD_ABCD_ABCD_ABCD
        self.dut = cltuFramer(startSequence=CUSTOM_START, tailSequence=CUSTOM_END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        input_dict = pmt.make_dict()
        input_data = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77]
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.3)
        self.tb.stop()
        self.tb.wait()

        number_of_messages = sink.num_messages()
        self.assertEqual(number_of_messages, 1)

        result = sink.get_message(0)
        payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))
        
        start_bytes = struct.pack('!H', CUSTOM_START)
        tail_bytes = struct.pack('!Q', CUSTOM_END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self.assertEqual(payload, expected_payload)
        Log.info("Custom sequences test passed")

    def test_006_runtime_sequence_modification(self):
        Log.info("Test 006: Runtime Sequence Modification")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        input_dict = pmt.make_dict()
        input_data = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x00]

        # First message with original sequences
        input_pdu_1 = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu_1)
        time.sleep(0.2)

        # Modify sequences at runtime
        NEW_START = 0x9999
        NEW_END = 0x7777_7777_7777_7777
        self.dut.startSequence = NEW_START
        self.dut.tailSequence = NEW_END

        # Second message with new sequences
        input_pdu_2 = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu_2)
        time.sleep(0.2)

        self.tb.stop()
        self.tb.wait()

        # Verify we have two messages
        self.assertEqual(sink.num_messages(), 2)

        # Check first message uses original sequences
        result_1 = sink.get_message(0)
        payload_1 = bytes(pmt.u8vector_elements(pmt.cdr(result_1)))
        start_bytes_orig = struct.pack('!H', START)
        tail_bytes_orig = struct.pack('!Q', END)
        expected_1 = start_bytes_orig + bytes(input_data) + tail_bytes_orig
        self.assertEqual(payload_1, expected_1)

        # Check second message uses new sequences
        result_2 = sink.get_message(1)
        payload_2 = bytes(pmt.u8vector_elements(pmt.cdr(result_2)))
        start_bytes_new = struct.pack('!H', NEW_START)
        tail_bytes_new = struct.pack('!Q', NEW_END)
        expected_2 = start_bytes_new + bytes(input_data) + tail_bytes_new
        self.assertEqual(payload_2, expected_2)

        Log.info("Runtime modification test passed")

    def test_007_multiple_consecutive_messages(self):
        Log.info("Test 007: Multiple Consecutive Messages")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        input_dict = pmt.make_dict()
        input_data = [0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE]

        self.tb.start()

        # Send multiple messages rapidly
        for i in range(5):
            input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))
            self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
            time.sleep(0.1)

        self.tb.stop()
        self.tb.wait()

        # Verify all messages were processed
        number_of_messages = sink.num_messages()
        self.assertEqual(number_of_messages, 5)

        # Verify all messages have correct format
        for i in range(5):
            result = sink.get_message(i)
            payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))
            start_bytes = struct.pack('!H', START)
            tail_bytes = struct.pack('!Q', END)
            expected_payload = start_bytes + bytes(input_data) + tail_bytes
            self.assertEqual(payload, expected_payload)

        Log.info("Multiple consecutive messages test passed")

    def test_008_all_zero_payload(self):
        Log.info("Test 008: All-Zero Payload")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        input_dict = pmt.make_dict()
        input_data = [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2)
        self.tb.stop()
        self.tb.wait()

        number_of_messages = sink.num_messages()
        self.assertEqual(number_of_messages, 1)

        result = sink.get_message(0)
        payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))
        
        start_bytes = struct.pack('!H', START)
        tail_bytes = struct.pack('!Q', END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self.assertEqual(payload, expected_payload)
        Log.info("All-zero payload test passed")

    def test_009_all_max_payload(self):
        Log.info("Test 009: All-Maximum (0xFF) Payload")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        input_dict = pmt.make_dict()
        input_data = [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2)
        self.tb.stop()
        self.tb.wait()

        number_of_messages = sink.num_messages()
        self.assertEqual(number_of_messages, 1)

        result = sink.get_message(0)
        payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))
        
        start_bytes = struct.pack('!H', START)
        tail_bytes = struct.pack('!Q', END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self.assertEqual(payload, expected_payload)
        Log.info("All-maximum payload test passed")

    def test_010_empty_metadata_dict(self):
        Log.info("Test 010: Empty Metadata Dictionary")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        # Create PDU with empty metadata dict
        input_dict = pmt.make_dict()
        input_data = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2)
        self.tb.stop()
        self.tb.wait()

        number_of_messages = sink.num_messages()
        self.assertEqual(number_of_messages, 1)

        result = sink.get_message(0)
        
        # Verify metadata is preserved (empty dict)
        output_dict = pmt.car(result)
        self.assertTrue(pmt.is_dict(output_dict))
        self.assertEqual(output_dict, input_dict)
        
        Log.info("Empty metadata dictionary test passed")

    def test_011_rich_metadata_preservation(self):
        Log.info("Test 011: Rich Metadata Preservation")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        # Create PDU with multiple metadata fields
        input_dict = pmt.make_dict()
        input_dict = pmt.dict_add(input_dict, pmt.intern("id"), pmt.from_long(123))
        input_dict = pmt.dict_add(input_dict, pmt.intern("timestamp"), pmt.from_double(1234567890.5))
        input_dict = pmt.dict_add(input_dict, pmt.intern("source"), pmt.intern("test_source"))
        
        input_data = [0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x11]
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2)
        self.tb.stop()
        self.tb.wait()

        number_of_messages = sink.num_messages()
        self.assertEqual(number_of_messages, 1)

        result = sink.get_message(0)
        payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))
        
        # Verify correct payload structure
        start_bytes = struct.pack('!H', START)
        tail_bytes = struct.pack('!Q', END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes
        self.assertEqual(payload, expected_payload)

        # Verify metadata is preserved exactly
        output_dict = pmt.car(result)
        self.assertEqual(output_dict, input_dict)
        
        Log.info("Rich metadata preservation test passed")

    def test_012_payload_size_boundary_6_bytes(self):
        Log.info("Test 012: Payload Size Boundary - 6 Bytes (Should Fail)")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        input_dict = pmt.make_dict()
        input_data = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06]  # 6 bytes instead of 7
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2)
        self.tb.stop()
        self.tb.wait()

        # Should not produce output
        self.assertEqual(sink.num_messages(), 0)

        # Verify error was logged
        with self.assertLogs("gnuradio.sage.cltuFramer", level='ERROR') as cm:
            self.dut.addSequences(input_pdu)
            self.assertTrue(any("Payload size is 6 bytes, expected 7 bytes" in s for s in cm.output))

        Log.info("Payload size boundary (6 bytes) test passed")

    def test_013_payload_size_boundary_8_bytes(self):
        Log.info("Test 013: Payload Size Boundary - 8 Bytes (Should Fail)")
        self.dut = cltuFramer(startSequence=START, tailSequence=END)
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        input_dict = pmt.make_dict()
        input_data = [0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08]  # 8 bytes instead of 7
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2)
        self.tb.stop()
        self.tb.wait()

        # Should not produce output
        self.assertEqual(sink.num_messages(), 0)

        # Verify error was logged
        with self.assertLogs("gnuradio.sage.cltuFramer", level='ERROR') as cm:
            self.dut.addSequences(input_pdu)
            self.assertTrue(any("Payload size is 8 bytes, expected 7 bytes" in s for s in cm.output))

        Log.info("Payload size boundary (8 bytes) test passed")

    def test_014_default_sequences(self):
        Log.info("Test 014: Default Sequences")
        # Create instance without specifying sequences (use defaults)
        self.dut = cltuFramer()
        
        sink = blocks.message_debug()
        self.tb.msg_connect(self.dut, 'pdu_out', sink, 'store')

        input_dict = pmt.make_dict()
        input_data = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77]
        input_pdu = pmt.cons(input_dict, pmt.init_u8vector(len(input_data), input_data))

        self.tb.start()
        self.dut.to_basic_block()._post(pmt.intern("pdu_in"), input_pdu)
        time.sleep(0.2)
        self.tb.stop()
        self.tb.wait()

        number_of_messages = sink.num_messages()
        self.assertEqual(number_of_messages, 1)

        result = sink.get_message(0)
        payload = bytes(pmt.u8vector_elements(pmt.cdr(result)))
        
        # Default sequences from class definition
        DEFAULT_START = 0xEB90
        DEFAULT_END = 0xC5C5_C5C5_C5C5_C579
        
        start_bytes = struct.pack('!H', DEFAULT_START)
        tail_bytes = struct.pack('!Q', DEFAULT_END)
        expected_payload = start_bytes + bytes(input_data) + tail_bytes

        self.assertEqual(payload, expected_payload)
        self.assertEqual(self.dut.startSequence, DEFAULT_START)
        self.assertEqual(self.dut.tailSequence, DEFAULT_END)
        
        Log.info("Default sequences test passed")

if __name__ == '__main__':
    gr_unittest.run(qa_cltuFramer)
