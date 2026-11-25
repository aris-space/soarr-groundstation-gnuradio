#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import inspect

from gnuradio import digital, gr

from .ccsds_reader import ccsds_reader
from .ccsds_receiver import ccsds_receiver
from .cltu_deframer import cltu_deframer
from .tc_frame_encoder import FECF_CRC

# The CLTU start sequence (CCSDS 231.0-B), as cltu_deframer's default.
START_SEQUENCE = inspect.signature(cltu_deframer.__init__).parameters["start_sequence"].default
START_SEQUENCE_BITS = 16
TAG_NAME = "start"


class tc_frame_decoder(gr.hier_block2):
    """
    Received bit stream to parsed TC transfer frames (CCSDS 231.0-B /
    232.0-B) in one block: start-sequence correlator, CLTU deframer, frame
    receiver (BCH, derandomizer, reassembly), FECF check, frame parser.

    Flow: bit stream in -> digital.correlate_access_code_tag_bb ->
    cltu_deframer -> (messages) ccsds_receiver -> digital.crc_check, `ok`
    output only -> ccsds_reader -> out. Everything CCSDS fixes is built in:
    the CLTU sequences, the codeword size, the BCH code, the randomizer,
    the FECF CRC.
    """

    def __init__(self, threshold: int = 1, scid_filter_enable: bool = False, scid: int = 0,
                 vcid_filter_enable: bool = False, vcid: int = 0, sdls_mode: int = 3,
                 encapsulation_used: bool = True):
        """
        Args:
            threshold (int): bit errors allowed in the 16-bit start
                sequence (and the tail sequence), for both the correlator
                and the deframer.
            scid_filter_enable, scid: keep only frames of this SCID.
            vcid_filter_enable, vcid: keep only frames of this VCID.
            sdls_mode (int): 0 = no SDLS, 1 = encryption only,
                2 = authentication only, 3 = both - matching the sender's
                SDLS Apply (ccsds_reader's sdls_type).
            encapsulation_used (bool): frames carry an encapsulation
                packet (see ccsds_reader).

        Raises:
            ValueError: threshold is outside 0-16, or an inner block
                refuses its setting (sdls_mode outside 0-3, scid/vcid
                outside their header fields).
        """
        gr.hier_block2.__init__(self, "TC Frame Decoder",
                                gr.io_signature(1, 1, gr.sizeof_char), gr.io_signature(0, 0, 0))

        if not (0 <= threshold <= START_SEQUENCE_BITS):
            raise ValueError(f"threshold must be 0-{START_SEQUENCE_BITS} bits, got {threshold}.")

        self.message_port_register_hier_out("out")

        access_code = format(START_SEQUENCE, f"0{START_SEQUENCE_BITS}b")
        self.correlator = digital.correlate_access_code_tag_bb(access_code, threshold, TAG_NAME)
        self.deframer = cltu_deframer(threshold=threshold, input_packed=False, output_packed=True,
                                      tag_name=TAG_NAME)
        self.receiver = ccsds_receiver(scid=scid, vcid=vcid, scid_filter_enable=scid_filter_enable,
                                       vcid_filter_enable=vcid_filter_enable)
        self.crc = digital.crc_check(FECF_CRC["num_bits"], FECF_CRC["poly"], FECF_CRC["initial_value"],
                                     FECF_CRC["final_xor"], FECF_CRC["input_reflected"],
                                     FECF_CRC["result_reflected"], FECF_CRC["swap_endianness"], False, 0)
        self.parser = ccsds_reader(sdls_type=sdls_mode, encapsulation_used=encapsulation_used)

        self.connect(self, self.correlator, self.deframer)
        self.msg_connect((self.deframer, "out"), (self.receiver, "in"))
        self.msg_connect((self.receiver, "out"), (self.crc, "in"))
        self.msg_connect((self.crc, "ok"), (self.parser, "ccsds"))
        self.msg_connect((self.parser, "debug"), (self, "out"))
