#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
from gnuradio import gr
import pmt

class lfsr_scrambler(gr.basic_block):
    def __init__(self, mask=0xA9,seed=0xFF,register_length=8):
        gr.basic_block.__init__(self,
            name="LFSR Scrambler",
            in_sig=None,
            out_sig=None)

        
        # Store parameters
        self.mask = mask
        self.seed = seed
        self.reg_length = register_length

        # Define message ports
        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))
        
        # Register handler for incoming messages
        self.set_msg_handler(pmt.intern("in"), self.handle_msg)

    def handle_msg(self, msg):
        # 1. Unpack the PDU (metadata and data vector)
        meta = pmt.car(msg)
        body = pmt.cdr(msg)

        if not pmt.is_u8vector(body):
            self.logger.error("Input message body is not a PDU (u8vector).")
            return

        pdu_data = pmt.u8vector_elements(body)
        
        # 2. Apply the scrambler logic
        # Use the configured parameters
        try:
            scrambled_data = self.apply_scrambling(pdu_data)
        except ValueError as err:
            self.logger.error(str(err))
            return
        
        # 3. Create and publish a new PDU
        new_pdu = pmt.cons(meta, pmt.init_u8vector(len(scrambled_data), scrambled_data))

        self.message_port_pub(pmt.intern("out"), new_pdu)
        self.logger.info(f"OK")
        

    def apply_scrambling(self, data):
        # CCSDS bit transition generator sequence (h(x) = x^8 + x^6 + x^4 + x^3 + x^2 + x + 1)
        # represented as recurrence over generated scramble bits:
        # b[n] = b[n-2] ^ b[n-4] ^ b[n-5] ^ b[n-6] ^ b[n-7] ^ b[n-8]
        if self.reg_length != 8:
            raise ValueError("CCSDS randomizer requires register_length=8")

        total_bits = len(data) * 8
        scramble_seq = [
            (self.seed >> (self.reg_length - 1 - i)) & 1
            for i in range(self.reg_length)
        ]

        while len(scramble_seq) < total_bits:
            n = len(scramble_seq)
            next_bit = (
                scramble_seq[n - 2]
                ^ scramble_seq[n - 4]
                ^ scramble_seq[n - 5]
                ^ scramble_seq[n - 6]
                ^ scramble_seq[n - 7]
                ^ scramble_seq[n - 8]
            )
            scramble_seq.append(next_bit)

        output = bytearray()
        seq_idx = 0

        for byte in data:
            out_byte = 0
            for i in range(8):
                scramble_bit = scramble_seq[seq_idx]
                seq_idx += 1
                
                # Data bit (MSB first)
                data_bit = (byte >> (7 - i)) & 1
                
                # XOR the data and scrambler bits
                res_bit = data_bit ^ scramble_bit
                out_byte = (out_byte << 1) | res_bit
            
            output.append(out_byte)
        return output
