#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

from gnuradio import gr

from .db_client import db_client
from .inject_db import ROLES, inject_db

DB_TYPE_DUMMY = 0
DB_TYPE_YAML = 1
DB_TYPES = (DB_TYPE_DUMMY, DB_TYPE_YAML)


class key_database(gr.hier_block2):
    """
    Key injector and key store as one block: each PDU on `in` gets its keys
    and counters from the store and comes out on `out`.

    Flow (message ports): in -> inject_db -> out, with inject_db's
    db_call/db_callback loop to db_client wired inside.
    """

    def __init__(self, role: str = "tx", db_type: int = DB_TYPE_DUMMY, yaml_path: str = "",
                 auto_reset_counters: bool = False, scid: int = 0x155, spi: int = 1, vcid: int = 0x12):
        """
        Args:
            role (str): "tx" - the store assigns the per-frame counters;
                "rx" - the received frame's counters are kept and RX
                lookups don't count (see inject_db).
            db_type (int): 0 = dummy entry with the built-in test keys,
                1 = YAML file (real keys).
            yaml_path (str): the YAML file, for db_type 1.
            auto_reset_counters (bool): dummy entry only - restart the
                SDLS counter after its maximum, for long test runs (see
                db_client; refused with anything but the test keys).
            scid, spi, vcid (int): the dummy entry's SCID, SPI and VCID.

        Raises:
            ValueError: role is not "tx"/"rx", or db_type is not 0 or 1.
        """
        gr.hier_block2.__init__(self, "Key Database", gr.io_signature(0, 0, 0), gr.io_signature(0, 0, 0))

        if role not in ROLES:
            raise ValueError(f"role must be 'tx' or 'rx', got {role!r}.")
        if db_type not in DB_TYPES:
            raise ValueError(f"db_type must be 0 (dummy) or 1 (YAML), got {db_type}.")

        self.message_port_register_hier_in("in")
        self.message_port_register_hier_out("out")

        self.injector = inject_db(role=role)
        self.store = db_client(type=db_type, yaml_path=yaml_path, auto_reset_counters=auto_reset_counters,
                               scid=scid, spi=spi, vcid=vcid)

        self.msg_connect((self, "in"), (self.injector, "in"))
        self.msg_connect((self.injector, "db_call"), (self.store, "db_call"))
        self.msg_connect((self.store, "db_callback"), (self.injector, "db_callback"))
        self.msg_connect((self.injector, "out"), (self, "out"))
