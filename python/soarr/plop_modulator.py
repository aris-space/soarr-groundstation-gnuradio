#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import threading
from collections import deque

import numpy as np
from gnuradio import gr
from gnuradio.filter import firdes
import pmt

PLOP1 = 1  # carrier on only while a CLTU burst is sent
PLOP2 = 2  # carrier always on, idle sequence between CLTUs
BYTE_MIN = 0x00
BYTE_MAX = 0xFF
IDLE_CHUNK_BYTES = 8  # idle bytes modulated per step while the PLOP-2 carrier idles
IDLE_WAIT_S = 0.01    # how long work() waits for a CLTU or mode change when there is nothing to send


class plop_modulator(gr.sync_block):
    """
    BPSK-modulate CLTUs for the SDR with switchable CCSDS 231.0-B physical
    layer operations procedure (PLOP) - the low-latency transmitter end of
    the TX chain.

    - PLOP-1: each CLTU is sent as one burst (acquisition sequence, CLTU,
      idle tail), marked with tx_sob/tx_eob tags; between bursts the block
      outputs nothing, so the USRP switches the transmitter off.
    - PLOP-2: the carrier stays on with the idle sequence; a CLTU is
      inserted at the next byte boundary. The carrier starts with the
      acquisition sequence (tx_sob) and, on a switch to PLOP-1, ends with
      the idle tail (tx_eob).

    Acquisition, idle, and CLTU bits go through one continuous differential
    encoder and pulse-shaping filter, so switching between them keeps the
    carrier phase-continuous. Idle is generated only as the SDR consumes
    samples, so a new CLTU waits behind at most one sample buffer (about
    20 ms at 400 kS/s) instead of queued idle bytes.

    The mode can be changed while running: `mode` message port or set_mode().
    """

    def __init__(self, mode:int=PLOP2, samples_per_symbol:int=40, excess_bw:float=0.35,
                 differential:bool=True, acquisition_length:int=64, tail_length:int=4,
                 fill_byte:int=0xAA, amplitude:float=1.0):
        """
        Args:
            mode (int): 1 = PLOP-1 (bursts), 2 = PLOP-2 (continuous carrier).
            samples_per_symbol (int): output samples per BPSK symbol.
            excess_bw (float): root-raised-cosine roll-off (0 < excess_bw <= 1).
            differential (bool): differentially encode the bits (NRZ-M),
                matching a differential BPSK receiver.
            acquisition_length (int): acquisition sequence length in bytes,
                sent at every carrier start (0 = none).
            tail_length (int): idle bytes sent before the carrier ends
                (0 = none).
            fill_byte (int): byte for acquisition, idle, and tail. With
                differential encoding 0xFF is used instead, which becomes
                alternating symbols on the channel.
            amplitude (float): output amplitude of a full-scale symbol.

        Raises:
            ValueError: mode is not 1 or 2, samples_per_symbol < 2,
                excess_bw outside (0, 1], a length is negative, or fill_byte
                is outside 0-255.
        """
        if mode not in (PLOP1, PLOP2):
            raise ValueError(f"mode must be 1 (PLOP-1) or 2 (PLOP-2), got {mode}.")
        if int(samples_per_symbol) < 2:
            raise ValueError(f"samples_per_symbol must be at least 2, got {samples_per_symbol}.")
        if not (0 < excess_bw <= 1):
            raise ValueError(f"excess_bw must be in (0, 1], got {excess_bw}.")
        if acquisition_length < 0 or tail_length < 0:
            raise ValueError("acquisition_length and tail_length must be >= 0.")
        if not (BYTE_MIN <= fill_byte <= BYTE_MAX):
            raise ValueError(f"fill_byte must be a single byte ({BYTE_MIN}-{BYTE_MAX}), got {fill_byte}.")

        gr.sync_block.__init__(self, name="plop_modulator", in_sig=None, out_sig=[np.complex64])

        self.samples_per_symbol = int(samples_per_symbol)
        self.differential = bool(differential)
        self.amplitude = float(amplitude)
        taps = np.array(firdes.root_raised_cosine(1.0, self.samples_per_symbol, 1.0, excess_bw,
                                                  11 * self.samples_per_symbol + 1))
        # Unit gain for a constant symbol train, so a symbol comes out at +/-1
        self.taps = taps * self.samples_per_symbol / taps.sum()
        self.flush_length = len(self.taps) - 1

        fill = BYTE_MAX if self.differential else fill_byte
        self._acquisition = bytes([fill]) * int(acquisition_length)
        self._tail = bytes([fill]) * int(tail_length)
        self._idle = bytes([fill]) * IDLE_CHUNK_BYTES

        self._requested_mode = mode
        self._carrier_on = False
        self._cltus = deque()
        self._fifo = deque()
        self._fifo_len = 0
        self._pending_tags = deque()
        self._generated = 0
        self._reset_modulator()
        self._wake = threading.Event()

        self.message_port_register_in(pmt.intern("in"))
        self.set_msg_handler(pmt.intern("in"), self.handle_cltu)
        self.message_port_register_in(pmt.intern("mode"))
        self.set_msg_handler(pmt.intern("mode"), self.handle_mode)

    @property
    def mode(self):
        return self._requested_mode

    def set_mode(self, mode):
        """
        Args:
            mode (int): 1 = PLOP-1, 2 = PLOP-2. Takes effect at the next
                boundary: a running PLOP-2 carrier ends after its current
                CLTU or idle chunk; a PLOP-1 burst in progress is finished
                first.

        Raises:
            ValueError: mode is not 1 or 2.
        """
        mode = int(mode)
        if mode not in (PLOP1, PLOP2):
            raise ValueError(f"mode must be 1 (PLOP-1) or 2 (PLOP-2), got {mode}.")
        self._requested_mode = mode
        self._wake.set()

    def handle_mode(self, msg):
        """
        Args:
            msg (pmt_integer): 1 = PLOP-1, 2 = PLOP-2.

        Publishes: nothing (this block's output is the `out` stream).

        Drops when:
            - msg is not an integer, or not 1 or 2 (error - control input, not RF data)
        """
        try:
            if not pmt.is_integer(msg):
                self.logger.error(f"mode message must be an integer (1 or 2), got {msg}")
                return
            self.set_mode(pmt.to_long(msg))
            self.logger.info(f"PLOP mode set to {self._requested_mode}")
        except Exception as exc:
            self.logger.error(f"Failed to set PLOP mode: {exc}")

    def handle_cltu(self, msg):
        """
        Args:
            msg (pmt_pair): PDU with metadata dict (ignored) and u8vector
                payload, one CLTU from cltu_framer.

        Publishes: nothing (the CLTU is queued for the `out` stream).

        Drops when:
            - msg is not a PDU pair, or payload is not a u8vector (error - malformed input at the TX boundary, not raw RF noise)
            - payload is empty (error - same)
        """
        try:
            if not pmt.is_pair(msg) or not pmt.is_u8vector(pmt.cdr(msg)):
                self.logger.error("Input message is not a PDU with a u8vector payload.")
                return
            cltu = bytes(pmt.u8vector_elements(pmt.cdr(msg)))
            if not cltu:
                self.logger.error("Received empty CLTU; nothing to send.")
                return
            self._cltus.append(cltu)
            self._wake.set()
        except Exception as exc:
            self.logger.error(f"Failed to queue CLTU: {exc}")

    # --- modulation -----------------------------------------------------

    def _reset_modulator(self):
        self._history = np.zeros(len(self.taps) - 1)
        self._last_bit = 0

    def _modulate(self, data):
        bits = np.unpackbits(np.frombuffer(data, dtype=np.uint8))
        if self.differential:
            bits = np.bitwise_xor.accumulate(np.concatenate(([self._last_bit], bits)).astype(np.uint8))[1:]
            self._last_bit = int(bits[-1])
        upsampled = np.zeros(len(bits) * self.samples_per_symbol)
        upsampled[::self.samples_per_symbol] = 2.0 * bits - 1.0
        return self._filter(upsampled)

    def _filter(self, upsampled):
        x = np.concatenate((self._history, upsampled))
        self._history = x[len(x) - (len(self.taps) - 1):]
        return np.convolve(x, self.taps, mode="valid")

    def _push(self, samples):
        samples = (samples * self.amplitude).astype(np.complex64)
        self._fifo.append(samples)
        self._fifo_len += len(samples)
        self._generated += len(samples)

    def _start_carrier(self):
        self._reset_modulator()
        self._pending_tags.append((self._generated, "tx_sob"))
        self._carrier_on = True
        if self._acquisition:
            self._push(self._modulate(self._acquisition))

    def _end_carrier(self):
        if self._tail:
            self._push(self._modulate(self._tail))
        self._push(self._filter(np.zeros(self.flush_length)))  # let the pulse filter ring out
        self._pending_tags.append((self._generated - 1, "tx_eob"))
        self._carrier_on = False
        self._reset_modulator()

    def _produce(self, wanted):
        """Fill the sample FIFO to at least `wanted` samples, or until there
        is nothing to send (PLOP-1 with no CLTU queued)."""
        while self._fifo_len < wanted:
            requested = self._requested_mode
            if self._carrier_on:  # continuous PLOP-2 carrier
                if requested == PLOP1:
                    self._end_carrier()
                elif self._cltus:
                    self._push(self._modulate(self._cltus.popleft()))
                else:
                    self._push(self._modulate(self._idle))
            elif requested == PLOP2:
                self._start_carrier()
            elif self._cltus:  # PLOP-1: one CLTU, one burst
                self._start_carrier()
                self._push(self._modulate(self._cltus.popleft()))
                self._end_carrier()
            else:
                return

    def work(self, input_items, output_items):
        """
        Args: standard gr.sync_block work() signature; `input_items` is
            unused (this block has no input stream).

        Returns:
            int: samples produced. In PLOP-1 with nothing to send, waits up
                to IDLE_WAIT_S for a CLTU or mode change and returns 0.
        """
        out = output_items[0]
        n_out = len(out)
        self._produce(n_out)
        if self._fifo_len == 0:
            self._wake.wait(IDLE_WAIT_S)
            self._wake.clear()
            self._produce(n_out)
            if self._fifo_len == 0:
                return 0

        start = self.nitems_written(0)
        produced = 0
        while produced < n_out and self._fifo:
            chunk = self._fifo[0]
            take = min(len(chunk), n_out - produced)
            out[produced:produced + take] = chunk[:take]
            produced += take
            if take == len(chunk):
                self._fifo.popleft()
            else:
                self._fifo[0] = chunk[take:]
        self._fifo_len -= produced

        while self._pending_tags and self._pending_tags[0][0] < start + produced:
            offset, key = self._pending_tags.popleft()
            self.add_item_tag(0, offset, pmt.intern(key), pmt.PMT_T)
        return produced
