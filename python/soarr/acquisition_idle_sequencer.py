#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import numpy as np
from gnuradio import gr
import pmt
import time
from collections import deque

class acquisition_idle_sequencer(gr.sync_block):
    """
    Acquisition and Idle Sequencer (PDU to Stream Source)
    """
    def __init__(self, idle_sequence=0xAA, acquisition_sequence=[0xAA]*16, initial_acquisition=False, diff_encoded=False, tsb_tag_name=None, max_idle_chunk=64):
        # Changed to sync_block with NO input stream
        gr.sync_block.__init__(self,
            name="acquisition_idle_sequencer",
            in_sig=None, 
            out_sig=[np.uint8])

        if diff_encoded == True:
            self.idle_sequence = 0xFF
            self.acquisition_sequence = np.array([0xFF]*len(acquisition_sequence), dtype=np.uint8)
        else:
            self.idle_sequence = idle_sequence
            self.acquisition_sequence = np.array(acquisition_sequence, dtype=np.uint8)

        self._acq_bytes = bytes(self.acquisition_sequence.tolist())

        self.tsb_tag_key = pmt.intern(tsb_tag_name) if tsb_tag_name else None

        self.message_port_register_in(pmt.intern("trigger_acq"))
        self.set_msg_handler(pmt.intern("trigger_acq"), self.handle_trigger_acq)
        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(pmt.intern("in"), self.handle_msg)

        self._pdu_queue = deque()

        # Maximum idle chunk size (bytes) to emit per loop iteration.
        # Keeps latency low by allowing work() to check for queued PDUs frequently.
        self.max_idle_chunk = int(max_idle_chunk) if max_idle_chunk and int(max_idle_chunk) > 0 else 64

        # Acquisition has priority over queued PDUs but never cuts an active burst.
        self.pending_acquisition = bool(initial_acquisition)
        
        # State tracking for clean buffer transitions
        self.current_burst = b''
        self.burst_idx = 0

    def handle_trigger_acq(self, msg):
        self.pending_acquisition = True

    def handle_msg(self, msg):
        if pmt.is_pair(msg):
            vec = pmt.cdr(msg)
            if pmt.is_u8vector(vec):
                pdu_bytes = pmt.u8vector_elements(vec)
                b = bytes(pdu_bytes)
                self._pdu_queue.append(b)
                self.logger.trace(f"msg queued t={time.time():.3f} queue_len={len(self._pdu_queue)} size={len(b)}")

    def _start_burst(self, burst_bytes, out_pos):
        self.current_burst = burst_bytes
        self.burst_idx = 0
        burst_len_items = len(self.current_burst)
        self.add_item_tag(0, out_pos, self.tsb_tag_key, pmt.from_long(burst_len_items))
        self.logger.trace(f"start_burst t={time.time():.3f} out_pos={out_pos} len={burst_len_items}")

    def work(self, input_items, output_items):
        out = output_items[0]
        n_out = len(out)
        items_produced = 0

        while items_produced < n_out:
            if self.current_burst:
                # We are actively sending a burst; write what fits in this buffer
                todo = min(n_out - items_produced, len(self.current_burst) - self.burst_idx)
                out[items_produced:items_produced+todo] = np.frombuffer(self.current_burst[self.burst_idx:self.burst_idx+todo], dtype=np.uint8)
                
                items_produced += todo
                self.burst_idx += todo

                # Clear burst state when finished so we return to idle
                if self.burst_idx >= len(self.current_burst):
                    self.current_burst = b''

            elif self.pending_acquisition and len(self._acq_bytes) > 0:
                # Acquisition has priority over queued PDUs, but starts only between bursts.
                out_pos = self.nitems_written(0) + items_produced
                self._start_burst(self._acq_bytes, out_pos)
                self.pending_acquisition = False

            elif self._pdu_queue:
                out_pos = self.nitems_written(0) + items_produced
                self._start_burst(self._pdu_queue.popleft(), out_pos)

            else:
                # Idle mode: emit at most `max_idle_chunk` bytes per iteration so
                # we yield periodically and can service incoming PDUs quickly.
                space_left = n_out - items_produced
                out_pos = self.nitems_written(0) + items_produced
                chunk = min(space_left, self.max_idle_chunk)
                self.add_item_tag(0, out_pos, self.tsb_tag_key, pmt.from_long(chunk))
                out[items_produced:items_produced+chunk] = self.idle_sequence
                # debug: when emitting a full chunk (indicates large n_out), log timestamp
                if chunk >= self.max_idle_chunk:
                    self.logger.trace(f"idle_emit t={time.time():.3f} out_pos={out_pos} chunk={chunk} n_out={n_out}")
                items_produced += chunk

        return items_produced

