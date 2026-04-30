#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import struct
import pmt
from gnuradio import gr



class cltuFramer(gr.basic_block):
    """
    docstring for block cltuFramer
    """
    def __init__(self, startSequence:int=0xEB90, tailSequence:int=0xC5C5C5C5C5C5C579):
        gr.basic_block.__init__(self,
            name="CLTU Framer",
            in_sig=None,
            out_sig=None)
        
        self.logger.info(f"Logger for CLTU Framer: {self.logger}")
        

        # Message Ports
        self.message_port_register_in(pmt.intern("pdu_in"))
        self.message_port_register_out(pmt.intern("pdu_out"))
        self.set_msg_handler(pmt.intern("pdu_in"), self.addSequences)

        # Enables change during runtime
        self.startSequence = startSequence
        self.tailSequence = tailSequence
    

    def addSequences(self, msg):
        # Konstanten laut CCSDS 231.0-B-4
        START_SEQ = struct.pack('!H', self.startSequence)
        # Die Tail Sequence ist das "End-of-Transmission" Muster
        TAIL_SEQ = struct.pack('!Q', self.tailSequence)
        
        # unpack PDU to get meta and body
        meta = pmt.car(msg)
        body_pmt = pmt.cdr(msg)

        if not pmt.is_u8vector(body_pmt):
            self.logger.error("Input message body is not a PDU.")
            return

        # Convert body to bytes for easy concatenation
        payload_bytes = bytes(pmt.u8vector_elements(body_pmt))

        # Check payload size
        if len(payload_bytes) != 8:
            self.logger.error(f"Payload size is {len(payload_bytes)} bytes, expected 8 bytes")
            return
        
        # CLTU build: [Start] [BCH-Blocks] [Tail]
        full_cltu = START_SEQ + payload_bytes + TAIL_SEQ
        
        # pack to PDU and send it out
        out_msg = pmt.cons(meta, pmt.init_u8vector(len(full_cltu), list(full_cltu)))
        self.message_port_pub(pmt.intern("pdu_out"), out_msg)

