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
    Reverses CCSDS 231.0-B-3 pseudo-randomization on a PDU's payload by
    XORing it against a running LFSR-generated sequence, the same fixed
    generator polynomial lfsr_scrambler applies on the TX side.
    """
    def __init__(self, seed:int=255,register_length:int=8):
        """
        Args:
            seed (int): Initial 8-bit LFSR register state. Only the low
                register_length bits are used.
            register_length (int): LFSR register width in bits. Only 8
                is supported (the CCSDS 231.0-B-3 randomizer is defined
                for an 8-bit register).

        Raises:
            ValueError: register_length is not 8.
        """
        gr.basic_block.__init__(self,
            name="LFSR Descrambler",
            in_sig=None,
            out_sig=None)

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
        """
        Reseeds the running LFSR sequence from `seed`, restarting bit
        generation from the beginning.

        Raises:
            ValueError: register_length is not 8.
        """
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
        """
        Args:
            data: sequence of payload bytes to descramble.

        Returns:
            bytearray: `data` XORed (MSB first) against the next
                len(data) * 8 bits pulled from the running randomizer
                sequence.

        Raises:
            ValueError: register_length is not 8.
        """
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
        """
        Args:
            msg (pmt_pair): PDU with metadata dict and u8vector payload.
                Metadata keys:
                    scramble_reset (bool, optional): if truthy, reseeds
                        the running LFSR sequence before descrambling.
                    filled (any, optional): if present at all (its value
                        is not inspected), reseeds the running LFSR
                        sequence before descrambling.

        Publishes:
            "out" (pmt_pair): PDU with the same metadata dict and the
                payload XORed against the running randomizer sequence;
                length preserved.

        Drops when:
            - msg is not a pair (warn - raw RF data, malformed before any structural check)
            - payload is not a u8vector (warn - same)
            - descrambling or publishing fails (warn - same)
        """
        if not pmt.is_pair(msg):
            self.logger.warn("Input message is not a PDU (pair).")
            return

        meta = pmt.car(msg)
        body = pmt.cdr(msg)

        if not pmt.is_u8vector(body):
            self.logger.warn("Input message body is not a PDU (u8vector).")
            return

        try:
            pdu_data = pmt.u8vector_elements(body)

            if pmt.dict_has_key(meta, pmt.intern("scramble_reset")):
                if pmt.to_bool(pmt.dict_ref(meta, pmt.intern("scramble_reset"))):
                    self.reset_sequence()
            elif pmt.dict_has_key(meta, pmt.intern("filled")):
                # Presence of 'filled' marks end-of-message in this chain.
                self.reset_sequence()

            descrambled_data = self.apply_descrambling(pdu_data)

            msg = pmt.cons(meta, pmt.init_u8vector(len(descrambled_data), descrambled_data))

            self.message_port_pub(pmt.intern("out"), msg)
            self.logger.debug("OK")

            return msg
        except Exception as exc:
            self.logger.warn(f"Failed to descramble or publish message: {exc}")
            return

