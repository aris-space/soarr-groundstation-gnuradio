#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr

from .sdls_authentication import sdls_authentication
from .sdls_constants import NONCE_LEN
from .sdls_encryption import sdls_encryption
from .sdls_header import sdls_header

# SDLS modes, the same values as ccsds_reader's sdls_type. Without SDLS
# this block isn't used, so there is no "none".
MODE_ENCRYPTION = 1
MODE_AUTHENTICATION = 2
MODE_BOTH = 3
MODES = (MODE_ENCRYPTION, MODE_AUTHENTICATION, MODE_BOTH)


class sdls_apply(gr.hier_block2):
    """
    SDLS ApplySecurity (CCSDS 355.0-B-1), the sending end: encrypt the
    payload, append the MAC, and prepend the security header, as one block.

    Flow (message ports): in -> sdls_encryption -> sdls_authentication ->
    sdls_header -> out. A step the mode leaves out passes the payload
    through; each step still removes its own key from the metadata.
    """

    def __init__(self, mode: int = MODE_BOTH, nonce: bytes = b"\x00" * NONCE_LEN):
        """
        Args:
            mode (int): 1 = encryption only, 2 = authentication only,
                3 = both - the values of ccsds_reader's sdls_type on RX.
            nonce (bytes): the fixed 14-byte nonce both the encryption and
                the MAC use; RX must be configured with the same value.

        Raises:
            ValueError: mode is not 1, 2 or 3, or nonce is not 14 bytes.
            TypeError: nonce is not bytes-like.
        """
        gr.hier_block2.__init__(self, "SDLS Apply", gr.io_signature(0, 0, 0), gr.io_signature(0, 0, 0))

        if mode not in MODES:
            raise ValueError(f"mode must be 1 (encryption), 2 (authentication) or 3 (both), got {mode}.")
        self.mode = mode

        self.message_port_register_hier_in("in")
        self.message_port_register_hier_out("out")

        self.encryption = sdls_encryption(mode in (MODE_ENCRYPTION, MODE_BOTH), nonce)
        self.authentication = sdls_authentication(mode in (MODE_AUTHENTICATION, MODE_BOTH), nonce)
        self.header = sdls_header()

        self.msg_connect((self, "in"), (self.encryption, "in"))
        self.msg_connect((self.encryption, "out"), (self.authentication, "in"))
        self.msg_connect((self.authentication, "out"), (self.header, "in"))
        self.msg_connect((self.header, "out"), (self, "out"))
