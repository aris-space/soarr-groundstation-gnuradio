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
    Create test payloads for downstream blocks.

    Manual data can be an int, bytes/bytearray, or a list/array of bytes.
    If data is an int and data_length_bytes is set, the value is left-padded
    to that length using big-endian byte order.

    Example:
        data=0x00010203, data_length_bytes=4 -> 00 01 02 03
    """
    def __init__(self, mode=0, data:int|None=None, data_length_bytes:int|None=None, scid=0, spi=0, bypass=False, control=False, vcid=0, vcid_counter=0, sdls_counter=0):
        gr.basic_block.__init__(self,
            name="dataCreator",
            in_sig=None,
            out_sig=None)

        self.mode = mode
        self.data = data
        self.length = data_length_bytes
        self.scid = scid
        self.spi = spi
        self.vcid_counter = vcid_counter
        self.sdls_counter = sdls_counter
        self.bypass = bypass
        self.control = control
        self.vcid = vcid


        # Validate that either data or data_length_bytes is provided
        if self.data is not None and self.length is not None:
            raise ValueError("Either data or data_length_bytes must be provided.")

        # Normalize provided data to a uint8 array when set explicitly.
        if self.data is not None:
            if isinstance(self.data, int):
                if self.data < 0:
                    raise ValueError("data must be non-negative when given as int")
                if self.length is None:
                    byte_len = max(1, (self.data.bit_length() + 7) // 8)
                else:
                    byte_len = int(self.length)
                self.data = self.data.to_bytes(byte_len, byteorder="big", signed=False)
            if isinstance(self.data, (bytes, bytearray)):
                self.data = np.frombuffer(self.data, dtype=np.uint8)
            elif not isinstance(self.data, np.ndarray):
                self.data = np.array(self.data, dtype=np.uint8)

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

        telecommand = pmt.make_dict()
        tc_header = pmt.make_dict()
        tc_header = pmt.dict_add(tc_header, pmt.intern("scid"), pmt.from_long(self.scid))
        tc_header = pmt.dict_add(tc_header, pmt.intern("bypass_flag"), pmt.from_bool(self.bypass))
        tc_header = pmt.dict_add(tc_header, pmt.intern("control_flag"), pmt.from_bool(self.control))
        telecommand = pmt.dict_add(telecommand, pmt.intern("tc_header"), tc_header)
        msg_dict = pmt.dict_add(msg_dict, pmt.intern("telecommand"), telecommand)

        sdls = pmt.make_dict()
        sdls_header = pmt.make_dict()
        sdls_header = pmt.dict_add(sdls_header, pmt.intern("spi"), pmt.from_long(self.spi))
        sdls_header = pmt.dict_add(sdls_header, pmt.intern("security_param_index"), pmt.from_long(self.spi))
        sdls = pmt.dict_add(sdls, pmt.intern("security_header"), sdls_header)
        msg_dict = pmt.dict_add(msg_dict, pmt.intern("sdls"), sdls)

        # Create a PMT u8vector to hold the message data
        msg_vector = pmt.init_u8vector(self.length, self.data)

        # Combine the dictionary and vector into a single PMT pair
        msg_out = pmt.cons(msg_dict, msg_vector)

        # Send the message out
        self.message_port_pub(pmt.intern("out"), msg_out)
        self.logger.info(f"OK")
        

