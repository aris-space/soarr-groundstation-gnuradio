#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""The Frame Error Control Field: one definition of its CRC, shared by the
TC and TM frame blocks.

TC (CCSDS 232.0-B 4.1.4) and TM (132.0-B-3 4.1.6) use the same 2-byte
CRC-16/CCITT-FALSE: polynomial 0x1021, preset all ones, no reflection, no
final XOR, over the whole frame before the FECF.
"""

from typing import TypedDict

from gnuradio import digital

FECF_BYTES = 2


class CrcParameters(TypedDict):
    """The arguments of digital.crc, crc_append and crc_check, by name."""
    num_bits: int
    poly: int
    initial_value: int
    final_xor: int
    input_reflected: bool
    result_reflected: bool
    swap_endianness: bool


FECF_CRC: CrcParameters = {"num_bits": 16, "poly": 0x1021, "initial_value": 0xFFFF, "final_xor": 0x0000,
                           "input_reflected": False, "result_reflected": False, "swap_endianness": False}


def fecf_crc():
    """
    Returns:
        digital.crc: the FECF CRC, whose compute(list_of_bytes) gives the
            16-bit FECF of the frame bytes before it.
    """
    return digital.crc(FECF_CRC["num_bits"], FECF_CRC["poly"], FECF_CRC["initial_value"],
                       FECF_CRC["final_xor"], FECF_CRC["input_reflected"], FECF_CRC["result_reflected"])
