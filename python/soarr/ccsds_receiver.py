#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


from construct import BitStruct, BitsInteger
import numpy
from gnuradio import gr
import pmt

from gnuradio.soarr import bch_decoder, lfsr_descrambler

MAX_FRAME_SIZE = 1024  # Maximum expected frame size in bytes

MESSAGE_TYPE_TC = 0
MESSAGE_TYPE_TM = 1
MESSAGE_TYPE_FIXED = 2

LENGTH_TYPE_TC_FIELD = 0
LENGTH_TYPE_ENCAPSULATION_FIELD = 1

DESCRAMBLING_ACTIVE = True


class ccsds_receiver(gr.basic_block):
    """
    docstring for block ccsds_receiver
    """
    def __init__(self, message_type:int=0,field_type:int=0, fixed_byte_length:int = 0, scid:int = 0, vcid:int = 0, scid_filter_enable:bool = False, vcid_filter_enable:bool = False):
        gr.basic_block.__init__(self,
            name="ccsds_receiver",
            in_sig=None,
            out_sig=None)
        
        # ENUM: 0 => TC, 1 => TM, 2 => Fixed Length
        if message_type < MESSAGE_TYPE_TC or message_type > MESSAGE_TYPE_FIXED:
            raise ValueError(f"Invalid message type: {message_type}. Must be between 0 and 2.")
        
        # ENUM: 0 => TC field, 1 => Encapsulation Field, 2 => fixed length (input)
        if field_type < LENGTH_TYPE_TC_FIELD or field_type > LENGTH_TYPE_ENCAPSULATION_FIELD:
            raise ValueError(f"Invalid length type: {field_type}. Must be between 0 and 1.")
        
        self.message_type = message_type
        self.initial_message_type = message_type
        self.field_type = field_type

        self.fixed_byte_length = fixed_byte_length
        self.remaining_fixed_bytes = fixed_byte_length


        if scid_filter_enable:
            self.scid = scid
        else:
            self.logger.info("SCID filtering is disabled. All frames will be accepted regardless of SCID.")
            self.scid = None

        if vcid_filter_enable:
            self.vcid = vcid
        else:
            self.logger.info("VCID filtering is disabled. All frames will be accepted regardless of VCID.")
            self.vcid = None

        self.length_found = False
        self.total_frame_length = 0
        self.remaining_frame_length = 0
        self.frame_buffer = bytearray()


        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        self.set_msg_handler(pmt.intern("in"), self.receiver)

        self.bch_decoder = bch_decoder(mode=0, generator_polynomial=0xC5, primitive_polynomial=0x43)
        self.lfsr_descrambler = lfsr_descrambler(169, 255, 8)
        
    def _bytes_to_bits(self, data_bytes):
        bits = []
        for byte in data_bytes:
            for bit_idx in range(7, -1, -1):
                bits.append((byte >> bit_idx) & 1)
        return bits
    
    
    def _bits_to_bytes(self, bits):
        output = bytearray()
        for start in range(0, len(bits), 8):
            value = 0
            for bit in bits[start:start + 8]:
                value = (value << 1) | bit
            output.append(value)
        return bytes(output)


    def _checkmsg(self, msg) -> bool:
        if not pmt.is_pair(msg):
            self.logger.error("Received message is not a PMT pair. Discarding message.")
            return False# Ignore non-PDU messages

        meta = pmt.car(msg)
        payload = pmt.cdr(msg)

        if not pmt.is_u8vector(payload):
            self.logger.error("Received payload is not a u8vector. Discarding message.")
            return False# Ignore payloads that are not byte vectors
        
        if not pmt.is_dict(meta):
            self.logger.error("Received metadata is not a dict. Discarding message.")
            return False  # Ignore metadata that is not a dict
        
        # is always 8 Bytes
        payload_bytes = bytes(pmt.u8vector_elements(payload))

        if len(payload_bytes) != 8:
            self.logger.error(f"Received payload length {len(payload_bytes)} does not match expected 8 bytes for length_type=0. Discarding frame.")
            return False  # Ignore frames that don't match expected length for length_type=0
        
        return True

    def _readInputMsg(self, msg)-> None | bytes:
        
        if not self._checkmsg(msg):
            return None  # Message is not valid, ignore it

        msg = self.bch_decoder.error_correction_mode(msg)
        if msg is None:
            self.logger.error("BCH decoding failed. Discarding message.")
            return None  # BCH decoding failed, ignore message
        
        if DESCRAMBLING_ACTIVE:
            # If we are waiting for a new frame, reset the descrambler sequence
            # before descrambling because this message is a candidate for the first codeword.
            if not self.length_found and self.message_type != MESSAGE_TYPE_FIXED:
                self.lfsr_descrambler.reset_sequence()

            msg = self.lfsr_descrambler.descramble_msg(msg)
            if msg is None:
                self.logger.error("LFSR descrambling failed. Discarding message.")
                return None  # LFSR descrambling failed, ignore message
        

        # if not self._checkmsg(msg):
        #     return None  # Message is not valid after decoding/descrambling, ignore it
        
        meta = pmt.car(msg)
        payload = pmt.cdr(msg)

        payload_bytes = bytes(pmt.u8vector_elements(payload))

        return payload_bytes

    def tc_header(self):
        """
        Transfer Frame Primary Header (TFPH) according to CCSDS 232.0-B-4
        Total: 5 bytes (40 bits)
        """
        return BitStruct(
            "tfvn" / BitsInteger(2),                    # Transfer Frame Version Number (2 bits)
            "bypass_flag" / BitsInteger(1),             # Bypass Flag (1 bit): 0b0=Type-A, 0b1=Type-B
            "control_flag" / BitsInteger(1),            # Control Flag (1 bit): 0b0=Type-D, 0b1=Type-C
            "reserve" / BitsInteger(2),                 # Reserved for future use (2 bits)
            "scid" / BitsInteger(10),                   # Spacecraft Identifier (10 bits)
            "vcid" / BitsInteger(6),                    # Virtual Channel Identification (6 bits)
            "frame_length" / BitsInteger(10),           # Frame Length in bytes including TFPH and Frame Error Control Field (10 bits)
            "frame_sequence_number" / BitsInteger(8),   # Frame Sequence Number (8 bits)
        )

    def _searchTFPH(self, cltu_frame:bytearray)-> None | int:
        # Implementation for searching TFPH
        tfph = self.tc_header().parse(cltu_frame[:6])

        self.logger.debug(f"Parsed TFPH fields - TFVN: {tfph.tfvn}, Bypass: {tfph.bypass_flag}, Control: {tfph.control_flag}, Reserve: {tfph.reserve}, SCID: {tfph.scid}, VCID: {tfph.vcid}, Frame Length: {tfph.frame_length}, Frame Sequence Number: {tfph.frame_sequence_number}")   

        if tfph.tfvn != 0:
            # Not a valid TFPH, continue searching
            self.logger.info(f"Invalid TFVN: expected 0, got {tfph.tfvn}. Continuing search.")
            return None
        
        if tfph.reserve != 0:
            # Reserved bits should be zero, continue searching
            self.logger.info(f"Invalid reserve bits: expected 0, got {tfph.reserve}. Continuing search.")
            return None
        
        if tfph.frame_length < 5 or tfph.frame_length > MAX_FRAME_SIZE:
            # Invalid frame length, continue searching
            self.logger.info(f"Invalid frame length: expected 5-{MAX_FRAME_SIZE}, got {tfph.frame_length}. Continuing search.")
            return None
        
        # We are now shure, that we have a valid TFPH, now check if SCID and VCID match (if configured)
        
        if self.scid is not None:
            if tfph.scid != self.scid:
                self.logger.info(f"SCID mismatch: expected {self.scid}, got {tfph.scid}. Continuing search.")
                return None
        
        if self.vcid is not None:
            if tfph.vcid != self.vcid:
                self.logger.info(f"VCID mismatch: expected {self.vcid}, got {tfph.vcid}. Continuing search.")
                return None
            
        return tfph.frame_length

    def _handleMessageTypeTC(self, payload_bytes:bytes)-> None:
        if self.field_type == LENGTH_TYPE_TC_FIELD:
            frame_length_bytes = self._searchTFPH(payload_bytes)
            if frame_length_bytes is not None:
                self.logger.info(f"Valid TFPH found with frame length: {frame_length_bytes} bytes")

                # Found a valid TFPH
                self.total_frame_length = frame_length_bytes
                self.remaining_frame_length = frame_length_bytes + 1 # +1 to get actual byte length
                self.length_found = True
            else:
                # No valid TFPH found, continue accumulating data
                self.logger.debug("No valid TFPH found in current payload, continuing to accumulate data.")
                return
                    
    def _handleMessageTypeFixed(self, payload_bytes:bytes)-> None:
        self.frame_buffer.extend(payload_bytes)
        self.remaining_fixed_bytes -= len(payload_bytes)

        if len(self.frame_buffer) >= self.fixed_byte_length:
            self._publishFrame(self.fixed_byte_length - 1) # -1 because _publishFrame adds +1
            self.remaining_fixed_bytes = self.fixed_byte_length
            self.lfsr_descrambler.reset_sequence()  # Reset LFSR sequence for next frame
            
        return

    def _recordCurrentMessage(self, payload_bytes:bytes)-> None:
        self.frame_buffer.extend(payload_bytes)
        self.remaining_frame_length -= len(payload_bytes)

    def _publishFrame(self,data_length:int):
        """Publish the complete frame and reset the buffer."""
        frame_data = bytes(self.frame_buffer[:data_length+1])  # +1 to include the last byte that made the length reach 0 or below
        metadata = pmt.make_dict()  # Empty metadata for now, can be extended with relevant info if needed
        metadata = pmt.dict_add(metadata, pmt.intern("frame_length"), pmt.from_long(data_length + 1))
        self.message_port_pub(pmt.intern("out"), pmt.cons(metadata, pmt.init_u8vector(len(frame_data), list(frame_data))))
        self.frame_buffer = bytearray()  # Clear buffer for next frame

    def receiver(self, msg):
        """Saves the received messages in a buffer"""
        
        payload_bytes = self._readInputMsg(msg)
        if payload_bytes is None:
            if self.length_found:
                self.logger.error("Error during frame accumulation. Aborting current frame.")
                self.length_found = False
                self.remaining_frame_length = 0
                self.total_frame_length = 0
                self.frame_buffer = bytearray()
                self.lfsr_descrambler.reset_sequence()
            return  # Message was invalid or not processable, ignore it
        
        if not self.length_found: # Only search if not found yet
            if self.message_type == MESSAGE_TYPE_TC:
                if self.field_type == LENGTH_TYPE_TC_FIELD:
                    self._handleMessageTypeTC(payload_bytes)
                elif self.field_type == LENGTH_TYPE_ENCAPSULATION_FIELD:
                    raise NotImplementedError("Length type 'Encapsulation Field' is not implemented yet.")

            if self.message_type == MESSAGE_TYPE_TM:
                raise NotImplementedError("Message type TM is not implemented yet.")
        
            if self.message_type == MESSAGE_TYPE_FIXED:
                self._handleMessageTypeFixed(payload_bytes)


        if not self.length_found: # if still not fund, don't capture
            # Don't need to add to buffer
            # No need to check for frame completion
            return


        # Add payload to buffer if TFPH already is found
        if self.remaining_frame_length > 0:
            self.logger.debug(f"Accumulating frame data, remaining frame length: {self.remaining_frame_length} bytes")
            self._recordCurrentMessage(payload_bytes)

        
        # Check frame completion based on the active message mode.
        if self.message_type == MESSAGE_TYPE_TC and self.remaining_frame_length <= 0:
            self.logger.info(f"Complete frame received, publishing frame with {self.total_frame_length} length.")
            self._publishFrame(self.total_frame_length)
            self.logger.info(f"OK")
            self.length_found = False
            self.remaining_frame_length = 0
            self.total_frame_length = 0
            self.lfsr_descrambler.reset_sequence()  # Reset LFSR sequence for next frame
            self.logger.debug(f"OK")
            return msg

        if self.message_type == MESSAGE_TYPE_FIXED and self.remaining_fixed_bytes <= 0:
            self.logger.info(f"Complete frame received, publishing frame with {self.fixed_byte_length} length.")
            self._publishFrame(self.fixed_byte_length)
            self.length_found = False
            self.remaining_fixed_bytes = self.fixed_byte_length
            self.total_frame_length = 0
            self.lfsr_descrambler.reset_sequence()  # Reset LFSR sequence for next frame
            self.logger.debug(f"OK")
            return msg
            
