#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr

from .sdls_apply import MODE_AUTHENTICATION, MODE_BOTH, MODE_ENCRYPTION, MODES
from .sdls_authentication_verify import sdls_authentication_verify
from .sdls_constants import NONCE_LEN
from .sdls_decryption import sdls_decryption


class sdls_process(gr.hier_block2):
    """
    SDLS ProcessSecurity (CCSDS 355.0-B-1), the receiving end: verify and
    strip the MAC, then decrypt the payload, as one block.

    Flow (message ports): in -> sdls_authentication_verify ->
    sdls_decryption -> out. A frame whose MAC fails is dropped there. A step
    the mode leaves out passes the payload through; each step still removes
    its own key from the metadata.
    """

    def __init__(self, mode: int = MODE_BOTH, nonce: bytes = b"\x00" * NONCE_LEN):
        """
        Args:
            mode (int): 1 = encryption only, 2 = authentication only,
                3 = both - the same values as sdls_apply on TX and
                ccsds_reader's sdls_type.
            nonce (bytes): the fixed 14-byte nonce TX used for both the
                encryption and the MAC.

        Raises:
            ValueError: mode is not 1, 2 or 3, or nonce is not 14 bytes.
            TypeError: nonce is not bytes-like.
        """
        gr.hier_block2.__init__(self, "SDLS Process", gr.io_signature(0, 0, 0), gr.io_signature(0, 0, 0))

        if mode not in MODES:
            raise ValueError(f"mode must be 1 (encryption), 2 (authentication) or 3 (both), got {mode}.")
        self.mode = mode

        self.message_port_register_hier_in("in")
        self.message_port_register_hier_out("out")

        self.verification = sdls_authentication_verify(mode in (MODE_AUTHENTICATION, MODE_BOTH), nonce)
        self.decryption = sdls_decryption(mode in (MODE_ENCRYPTION, MODE_BOTH), nonce)

        self.msg_connect((self, "in"), (self.verification, "in"))
        self.msg_connect((self.verification, "out"), (self.decryption, "in"))
        self.msg_connect((self.decryption, "out"), (self, "out"))
