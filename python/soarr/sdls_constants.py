#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""SDLS (CCSDS 355.0-B-1) field widths shared by every block that builds,
reads, or uses the per-message SDLS counter.

The counter is transmitted as the Security Header's IV field and fills the
low bytes of the 16-byte AES block that sdls_encryption/sdls_decryption use
as their CTR counter block and sdls_authentication/sdls_authentication_verify
authenticate; the fixed nonce fills the rest. Changing SDLS_COUNTER_BYTES
changes all of them together.
"""

SDLS_COUNTER_BYTES = 2
SDLS_COUNTER_MIN = 0
SDLS_COUNTER_MAX = (1 << (8 * SDLS_COUNTER_BYTES)) - 1

AES_BLOCK_BYTES = 16
NONCE_LEN = AES_BLOCK_BYTES - SDLS_COUNTER_BYTES

SPI_BYTES = 2
