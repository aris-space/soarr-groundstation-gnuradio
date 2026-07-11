#
# Copyright 2008,2009 Free Software Foundation, Inc.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

# The presence of this file turns this directory into a Python package

'''
This is the GNU Radio SOARR module. Place your Python package
description here (python/__init__.py).
'''
import os

# import pybind11 generated symbols into the soarr namespace
try:
    # this might fail if the module is python-only
    from .soarr_python import *
except ModuleNotFoundError:
    pass

# import any pure python here
from .cltu_framer import cltu_framer
from .bch_encoder import bch_encoder
from .lfsr_scrambler import lfsr_scrambler
from .tc_primary_header import tc_primary_header
from .db_client import db_client
from .sdls_authentication import sdls_authentication
from .sdls_encryption import sdls_encryption
from .inject_db import inject_db
from .sdls_header import sdls_header
from .encapsulation_header import encapsulation_header
from .ccsds_reader import ccsds_reader
from .data_creator import data_creator
from .bch_decoder import bch_decoder
from .cltu_deframer import cltu_deframer
from .lfsr_descrambler import lfsr_descrambler
from .ccsds_receiver import ccsds_receiver
from .sdls_decryption import sdls_decryption
from .sdls_authentication_verify import sdls_authentication_verify
from .acquisition_idle_sequencer import acquisition_idle_sequencer
from .system_tester import system_tester
