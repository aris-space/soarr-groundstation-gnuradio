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

BYTE_MIN = 0x00
BYTE_MAX = 0xFF


class acquisition_idle_sequencer(gr.sync_block):
    """
    Bridge cltu_framer's framed-PDU output into a continuous uint8 byte
    stream for the SDR/modulator downstream - the only stream block in
    this module.

    Flow:
    - Queue PDU payloads received on `in`, and track an acquisition
      request from `trigger_acq`
    - On each `work()` call, stream in priority order: finish any
      in-progress burst, start an acquisition burst, start a queued PDU
      burst, or emit idle-fill bytes
    """

    def __init__(self, idle_sequence=0xAA, acquisition_sequence=None, initial_acquisition=False, diff_encoded=False, tsb_tag_name=None, max_idle_chunk=64):
        """
        Args:
            idle_sequence (int): byte value repeated while idle (0-255).
            acquisition_sequence (list[int] | None): bytes streamed once,
                with priority, when an acquisition is pending (each 0-255).
                Defaults to `[0xAA] * 16` if None.
            initial_acquisition (bool): if True, an acquisition burst is
                emitted before the first PDU/idle byte.
            diff_encoded (bool): if True, overrides `idle_sequence` to
                `0xFF` and `acquisition_sequence` to all-`0xFF` (keeping
                only its length).
            tsb_tag_name (str | None): tag key marking stream-tagged-burst
                boundaries. If None, no tag is emitted.
            max_idle_chunk (int): max bytes emitted per idle-mode `work()`
                iteration; falls back to 64 if not a positive int.

        Raises:
            ValueError: idle_sequence, or any acquisition_sequence
                element, is outside the 0-255 range a uint8 stream
                requires.
        """
        gr.sync_block.__init__(self,
            name="acquisition_idle_sequencer",
            in_sig=None,
            out_sig=[np.uint8])

        if acquisition_sequence is None:
            acquisition_sequence = [0xAA] * 16

        if not (BYTE_MIN <= idle_sequence <= BYTE_MAX):
            raise ValueError(f"idle_sequence must be a single byte ({BYTE_MIN}-{BYTE_MAX}), got {idle_sequence}.")
        for value in acquisition_sequence:
            if not (BYTE_MIN <= value <= BYTE_MAX):
                raise ValueError(f"acquisition_sequence values must each be a single byte ({BYTE_MIN}-{BYTE_MAX}), got {value}.")

        if diff_encoded:
            self.idle_sequence = 0xFF
            acq_bytes_source = [0xFF] * len(acquisition_sequence)
        else:
            self.idle_sequence = idle_sequence
            acq_bytes_source = acquisition_sequence

        self._acq_bytes = bytes(acq_bytes_source)

        # None means "no tag emitted" - every add_item_tag call site must
        # check for this before calling, since GNU Radio's binding
        # crashes the process (not a catchable exception) if passed a
        # Python None instead of a real PMT symbol.
        self.tsb_tag_key = pmt.intern(tsb_tag_name) if tsb_tag_name else None

        self.message_port_register_in(pmt.intern("trigger_acq"))
        self.set_msg_handler(pmt.intern("trigger_acq"), self.handle_trigger_acq)
        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(pmt.intern("in"), self.handle_msg)

        self._pdu_queue = deque()

        # Maximum idle chunk size (bytes) to emit per loop iteration.
        # Keeps latency low by allowing work() to check for queued PDUs frequently.
        if max_idle_chunk and int(max_idle_chunk) > 0:
            self.max_idle_chunk = int(max_idle_chunk)
        else:
            self.max_idle_chunk = 64
            self.logger.debug(f"max_idle_chunk={max_idle_chunk!r} is not a positive int; using default 64.")

        # Acquisition has priority over queued PDUs but never cuts an active burst.
        self.pending_acquisition = bool(initial_acquisition)

        # State tracking for clean buffer transitions
        self.current_burst = b''
        self.burst_idx = 0

    def handle_trigger_acq(self, msg):
        """
        Args:
            msg (pmt_any): ignored; receipt alone triggers an acquisition
                burst on the next `work()` call.

        Publishes: nothing (this block's real output is the `out` stream
            port, not a message port).

        Drops when: never - this handler has nothing that can fail.
        """
        try:
            self.pending_acquisition = True
        except Exception as exc:
            self.logger.error(f"Failed to set pending_acquisition: {exc}")

    def handle_msg(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict (ignored) and u8vector
                payload; the payload bytes are queued for streaming.

        Publishes: nothing (this block's real output is the `out` stream
            port, not a message port).

        Drops when:
            - msg is not a PDU pair (error - malformed input at the TX boundary, not raw RF noise)
            - payload is not a u8vector (error - same)
            - queuing the payload fails (error - same)
        """
        if not pmt.is_pair(msg):
            self.logger.error("Input message is not a pair.")
            return

        vec = pmt.cdr(msg)
        if not pmt.is_u8vector(vec):
            self.logger.error("Input message body is not a PDU (u8vector).")
            return

        try:
            payload_bytes = bytes(pmt.u8vector_elements(vec))
            self._pdu_queue.append(payload_bytes)
            self.logger.trace(f"msg queued t={time.time():.3f} queue_len={len(self._pdu_queue)} size={len(payload_bytes)}")
        except Exception as exc:
            self.logger.error(f"Failed to queue message: {exc}")

    def _start_burst(self, burst_bytes, out_pos):
        """
        Args:
            burst_bytes (bytes): bytes to stream as one uninterrupted burst.
            out_pos (int): absolute stream item index the burst starts at,
                used for the boundary tag.

        Returns: None. Sets `current_burst`/`burst_idx` and, if
            `tsb_tag_key` is configured, tags the burst's start.
        """
        self.current_burst = burst_bytes
        self.burst_idx = 0
        burst_len_items = len(self.current_burst)
        if self.tsb_tag_key is not None:
            self.add_item_tag(0, out_pos, self.tsb_tag_key, pmt.from_long(burst_len_items))
        self.logger.trace(f"start_burst t={time.time():.3f} out_pos={out_pos} len={burst_len_items}")

    def work(self, input_items, output_items):
        """
        Args: standard gr.sync_block work() signature; `input_items` is
            unused (this block has no input stream).

        Returns:
            int: number of output items produced (always fills the
                buffer: acquisition/queued-PDU bursts, then idle-fill).
        """
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
                continue

            out_pos = self.nitems_written(0) + items_produced

            if self.pending_acquisition and len(self._acq_bytes) > 0:
                # Acquisition has priority over queued PDUs, but starts only between bursts.
                self._start_burst(self._acq_bytes, out_pos)
                self.pending_acquisition = False

            elif self._pdu_queue:
                self._start_burst(self._pdu_queue.popleft(), out_pos)

            else:
                # Idle mode: emit at most `max_idle_chunk` bytes per iteration so
                # we yield periodically and can service incoming PDUs quickly.
                space_left = n_out - items_produced
                chunk = min(space_left, self.max_idle_chunk)
                if self.tsb_tag_key is not None:
                    self.add_item_tag(0, out_pos, self.tsb_tag_key, pmt.from_long(chunk))
                out[items_produced:items_produced+chunk] = self.idle_sequence
                # debug: when emitting a full chunk (indicates large n_out), log timestamp
                if chunk >= self.max_idle_chunk:
                    self.logger.trace(f"idle_emit t={time.time():.3f} out_pos={out_pos} chunk={chunk} n_out={n_out}")
                items_produced += chunk

        return items_produced

