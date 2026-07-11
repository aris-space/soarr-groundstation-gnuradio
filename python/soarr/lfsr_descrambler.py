#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr
import pmt

class lfsr_descrambler(gr.basic_block):
    """
    docstring for block lfsr_descrambler
    """
    def __init__(self, mask:int=169,seed:int=255,register_length:int=8):
        gr.basic_block.__init__(self,
            name="LFSR Descrambler",
            in_sig=None,
            out_sig=None)
        
        self.mask = mask
        self.seed = seed
        self.register_length = register_length
        self.register = seed

        self._seed_bits = []
        self._seq_index = 0
        self._window = []
        self.reset_sequence()

        self.message_port_register_in(pmt.intern("in"))
        self.message_port_register_out(pmt.intern("out"))

        self.set_msg_handler(pmt.intern("in"), self.descramble_msg)

    def reset_sequence(self):
        if self.register_length != 8:
            raise ValueError("CCSDS randomizer requires register_length=8")

        self._seed_bits = [
            (self.seed >> (self.register_length - 1 - i)) & 1
            for i in range(self.register_length)
        ]
        self._seq_index = 0
        self._window = list(self._seed_bits)

    def _next_scramble_bit(self):
        if self._seq_index < self.register_length:
            bit = self._seed_bits[self._seq_index]
            self._seq_index += 1
            return bit

        next_bit = (
            self._window[6]
            ^ self._window[4]
            ^ self._window[3]
            ^ self._window[2]
            ^ self._window[1]
            ^ self._window[0]
        )
        self._window = self._window[1:] + [next_bit]
        self._seq_index += 1
        return next_bit

    def apply_descrambling(self, data):
        if self.register_length != 8:
            raise ValueError("CCSDS randomizer requires register_length=8")

        output = bytearray()

        for byte in data:
            out_byte = 0
            for i in range(8):
                scramble_bit = self._next_scramble_bit()

                data_bit = (byte >> (7 - i)) & 1
                res_bit = data_bit ^ scramble_bit
                out_byte = (out_byte << 1) | res_bit

            output.append(out_byte)

        return output
    

    def descramble_msg(self, msg):
        if not pmt.is_pair(msg):
            self.logger.error("Input message is not a PDU (pair).")
            return

        meta = pmt.car(msg)
        body = pmt.cdr(msg)

        if not pmt.is_u8vector(body):
            self.logger.error("Input message body is not a PDU (u8vector).")
            return

        pdu_data = pmt.u8vector_elements(body)

        if pmt.dict_has_key(meta, pmt.intern("scramble_reset")):
            if pmt.to_bool(pmt.dict_ref(meta, pmt.intern("scramble_reset"))):
                self.reset_sequence()
        elif pmt.dict_has_key(meta, pmt.intern("filled")):
            # Presence of 'filled' marks end-of-message in this chain.
            self.reset_sequence()
        
        try:
            descrambled_data = self.apply_descrambling(pdu_data)
        except ValueError as err:
            self.logger.error(str(err))
            return
        
        msg = pmt.cons(meta, pmt.init_u8vector(len(descrambled_data), descrambled_data))

        self.message_port_pub(pmt.intern("out"), msg)
        self.logger.debug(f"OK")

        return msg

