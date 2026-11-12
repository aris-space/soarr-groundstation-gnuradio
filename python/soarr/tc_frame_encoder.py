#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import digital, gr

from .bch_encoder import bch_encoder
from .cltu_framer import cltu_framer
from .lfsr_scrambler import lfsr_scrambler
from .tc_primary_header import tc_primary_header

# CCSDS 232.0-B FECF: CRC-16/CCITT, preset all ones, no final XOR
# (CRC-16/CCITT-FALSE), as digital.crc_append/crc_check arguments.
FECF_CRC = dict(num_bits=16, poly=0x1021, initial_value=0xFFFF, final_xor=0x0000,
                input_reflected=False, result_reflected=False, swap_endianness=False)


class tc_frame_encoder(gr.hier_block2):
    """
    TC frame data field to CLTU (CCSDS 232.0-B / 231.0-B) in one block:
    TC primary header, FECF, randomizer, BCH (63,56) codewords, CLTU start
    and tail sequences.

    Flow (message ports): in -> tc_primary_header -> digital.crc_append ->
    lfsr_scrambler -> bch_encoder -> cltu_framer -> out. Everything CCSDS
    fixes is built in: the FECF CRC, the randomizer seed, the BCH
    polynomial, the CLTU sequences.
    """

    def __init__(self, scid: int = 0x0, vcid: int = 0x0):
        """
        Args:
            scid (int): Spacecraft ID (10 bits), used when the metadata
                carries none.
            vcid (int): Virtual Channel ID (6 bits), used when the metadata
                carries none.

        Raises:
            ValueError: scid or vcid doesn't fit its header field.
        """
        gr.hier_block2.__init__(self, "TC Frame Encoder", gr.io_signature(0, 0, 0), gr.io_signature(0, 0, 0))

        self.message_port_register_hier_in("in")
        self.message_port_register_hier_out("out")

        self.primary_header = tc_primary_header(scid=scid, vcid=vcid, is_crc_used=True)
        self.crc = digital.crc_append(FECF_CRC["num_bits"], FECF_CRC["poly"], FECF_CRC["initial_value"],
                                      FECF_CRC["final_xor"], FECF_CRC["input_reflected"],
                                      FECF_CRC["result_reflected"], FECF_CRC["swap_endianness"], 0)
        self.scrambler = lfsr_scrambler()
        self.bch = bch_encoder()
        self.framer = cltu_framer()

        self.msg_connect((self, "in"), (self.primary_header, "in"))
        self.msg_connect((self.primary_header, "out"), (self.crc, "in"))
        self.msg_connect((self.crc, "out"), (self.scrambler, "in"))
        self.msg_connect((self.scrambler, "out"), (self.bch, "message"))
        self.msg_connect((self.bch, "codewords"), (self.framer, "in"))
        self.msg_connect((self.framer, "out"), (self, "out"))
