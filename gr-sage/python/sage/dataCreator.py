#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


from gnuradio import gr
import pmt
import numpy as np

class dataCreator(gr.basic_block):
    """
    docstring for block dataCreator
    """
    def __init__(self, mode=0, data:int|None=None, data_length_bytes:int|None=None, scid=0, spi=0, bypass=False, control=False):
        gr.basic_block.__init__(self,
            name="dataCreator",
            in_sig=None,
            out_sig=None)

        self.mode = mode
        self.data = data
        self.length = data_length_bytes
        self.scid = scid
        self.spi = spi
        self.bypass = bypass
        self.control = control


        # Validate that either data or data_length_bytes is provided
        if self.data is not None and self.length is not None:
            raise ValueError("Either data or data_length_bytes must be provided.")

        # If data is not provided, create a random vector of the specified length
        if self.data is None:
            # create random vector of length self.length
            self.data = np.random.randint(0, 256, size=self.length, dtype=np.uint8)

        # If length is not provided, use the length of the data
        if self.length is None:
            self.length = len(self.data)

        # Validate that the length of the data matches the specified length
        if len(self.data) != self.length:
            raise ValueError("Length of data does not match data_length_bytes.")


        self.message_port_register_in(pmt.intern("ping"))
        self.message_port_register_out(pmt.intern("out"))

        self.set_msg_handler(pmt.intern("ping"), self._choose_mode)

    def _choose_mode(self, msg):
        if self.mode == 0:
            self.generate_message(msg)
        else:
            raise NotImplementedError("Only mode 0 is implemented in this example.")

    def generate_message(self, msg):
        # Create a PMT dictionary to hold the message fields
        msg_dict = pmt.make_dict()
        msg_dict = pmt.dict_add(msg_dict, pmt.intern("scid"), pmt.from_long(self.scid))
        msg_dict = pmt.dict_add(msg_dict, pmt.intern("spi"), pmt.from_long(self.spi))
        msg_dict = pmt.dict_add(msg_dict, pmt.intern("bypass"), pmt.from_bool(self.bypass))
        msg_dict = pmt.dict_add(msg_dict, pmt.intern("control"), pmt.from_bool(self.control))

        # Create a PMT u8vector to hold the message data
        msg_vector = pmt.init_u8vector(self.length, self.data)

        # Combine the dictionary and vector into a single PMT pair
        msg_out = pmt.cons(msg_dict, msg_vector)

        # Send the message out
        self.message_port_pub(pmt.intern("out"), msg_out)
        self.logger.info(f"OK")
        

