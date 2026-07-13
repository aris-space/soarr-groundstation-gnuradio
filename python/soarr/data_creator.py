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

class data_creator(gr.basic_block):
    """
    Create test payloads for downstream blocks.

    Manual data can be an int, bytes/bytearray, or a list/array of bytes.
    If data is an int and data_length_bytes is set, the int is left-padded
    to that length using big-endian byte order - the one combination of
    data and data_length_bytes that's allowed together, since it's the
    only one with a single unambiguous meaning. For bytes/bytearray/
    array-like data, data_length_bytes must either be omitted or match
    the data's actual length exactly.

    Example:
        data=0x00010203, data_length_bytes=4 -> 00 01 02 03
    """
    def __init__(self, mode=0, data:int|None=None, data_length_bytes:int|None=None, scid=0, spi=0, bypass=False, control=False, vcid=0, vcid_counter=0, sdls_counter=0):
        """
        Args:
            mode (int): only 0 is implemented.
            data (int | bytes | bytearray | array-like | None): explicit
                payload. An int is left-padded to data_length_bytes (or
                its own minimal byte width if data_length_bytes is None).
            data_length_bytes (int | None): required if data is None
                (length of the randomly generated payload); optional if
                data is an int (pad width); must match data's actual
                length if data is bytes/bytearray/array-like.
            scid, spi (int): written into the output metadata.
            bypass, control (bool): written into the output metadata.
            vcid, vcid_counter, sdls_counter (int): written into the
                output metadata.

        Raises:
            ValueError: neither data nor data_length_bytes is provided,
                data is a negative int, or data_length_bytes disagrees
                with non-int data's actual length.
        """
        gr.basic_block.__init__(self,
            name="data_creator",
            in_sig=None,
            out_sig=None)

        self.mode = mode
        self.data = data
        self.data_length_bytes = data_length_bytes
        self.scid = scid
        self.spi = spi
        self.vcid_counter = vcid_counter
        self.sdls_counter = sdls_counter
        self.bypass = bypass
        self.control = control
        self.vcid = vcid

        if self.data is None and self.data_length_bytes is None:
            raise ValueError("Either data or data_length_bytes must be provided.")

        # Normalize provided data to a uint8 array when set explicitly.
        if self.data is not None:
            if isinstance(self.data, int):
                if self.data < 0:
                    raise ValueError("data must be non-negative when given as int")
                if self.data_length_bytes is None:
                    byte_len = max(1, (self.data.bit_length() + 7) // 8)
                else:
                    byte_len = int(self.data_length_bytes)
                self.data = self.data.to_bytes(byte_len, byteorder="big", signed=False)
            else:
                # bytes/bytearray/array-like: an explicit data_length_bytes
                # must agree with the actual length - there's no single
                # correct way to reconcile a mismatch (pad? truncate?).
                actual_len = len(self.data)
                if self.data_length_bytes is not None and actual_len != int(self.data_length_bytes):
                    raise ValueError(
                        f"data_length_bytes ({self.data_length_bytes}) does not match "
                        f"the length of the provided data ({actual_len})."
                    )
            if isinstance(self.data, (bytes, bytearray)):
                self.data = np.frombuffer(self.data, dtype=np.uint8)
            elif not isinstance(self.data, np.ndarray):
                self.data = np.array(self.data, dtype=np.uint8)
        else:
            # data not provided: create a random vector of the specified length
            self.data = np.random.randint(0, 256, size=self.data_length_bytes, dtype=np.uint8)

        # If length is not provided, use the length of the data
        if self.data_length_bytes is None:
            self.data_length_bytes = len(self.data)

        # Validate that the length of the data matches the specified length
        if len(self.data) != self.data_length_bytes:
            raise ValueError("Length of data does not match data_length_bytes.")

        self.message_port_register_in(pmt.intern("ping"))
        self.message_port_register_out(pmt.intern("out"))

        self.set_msg_handler(pmt.intern("ping"), self._choose_mode)

    def _choose_mode(self, msg):
        """
        Args:
            msg (pmt_any): ignored; receipt alone triggers generation.

        Publishes: nothing directly (delegates to generate_message for mode 0).

        Drops when:
            - mode is not 0 (error - unsupported configuration)
            - dispatching fails for any other internal reason (error - same)
        """
        try:
            if self.mode == 0:
                self.generate_message(msg)
            else:
                self.logger.error(f"Unsupported mode '{self.mode}'; only mode 0 is implemented.")
        except Exception as exc:
            self.logger.error(f"Failed to dispatch message: {exc}")

    def generate_message(self, msg):
        """
        Args:
            msg (pmt_any): ignored; content is never inspected.

        Publishes:
            "out" (pmt_pair): PDU with metadata dict (telecommand.tc_header.
                {scid,bypass_flag,control_flag,vcid,vcid_counter},
                sdls.security_header.{spi,security_param_index,sdls_counter})
                and the configured/generated payload.

        Drops when:
            - building or publishing the message fails for any reason (error)
        """
        try:
            # Create a PMT dictionary to hold the message fields
            msg_dict = pmt.make_dict()

            telecommand = pmt.make_dict()
            tc_header = pmt.make_dict()
            tc_header = pmt.dict_add(tc_header, pmt.intern("scid"), pmt.from_long(self.scid))
            tc_header = pmt.dict_add(tc_header, pmt.intern("bypass_flag"), pmt.from_bool(self.bypass))
            tc_header = pmt.dict_add(tc_header, pmt.intern("control_flag"), pmt.from_bool(self.control))
            tc_header = pmt.dict_add(tc_header, pmt.intern("vcid"), pmt.from_long(self.vcid))
            tc_header = pmt.dict_add(tc_header, pmt.intern("vcid_counter"), pmt.from_long(self.vcid_counter))
            telecommand = pmt.dict_add(telecommand, pmt.intern("tc_header"), tc_header)
            msg_dict = pmt.dict_add(msg_dict, pmt.intern("telecommand"), telecommand)

            sdls = pmt.make_dict()
            sdls_header = pmt.make_dict()
            sdls_header = pmt.dict_add(sdls_header, pmt.intern("spi"), pmt.from_long(self.spi))
            sdls_header = pmt.dict_add(sdls_header, pmt.intern("security_param_index"), pmt.from_long(self.spi))
            sdls_header = pmt.dict_add(sdls_header, pmt.intern("sdls_counter"), pmt.from_long(self.sdls_counter))
            sdls = pmt.dict_add(sdls, pmt.intern("security_header"), sdls_header)
            msg_dict = pmt.dict_add(msg_dict, pmt.intern("sdls"), sdls)

            # Create a PMT u8vector to hold the message data
            msg_vector = pmt.init_u8vector(self.data_length_bytes, self.data)

            # Combine the dictionary and vector into a single PMT pair
            msg_out = pmt.cons(msg_dict, msg_vector)

            # Send the message out
            self.message_port_pub(pmt.intern("out"), msg_out)
            self.logger.info("OK")
        except Exception as exc:
            self.logger.error(f"Failed to build or publish message: {exc}")

