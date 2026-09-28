#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import struct
from collections import deque

import numpy as np
import pmt
from gnuradio import gr

# A TC transfer frame is at most 1024 bytes = ceil(1024 * 8 / 56) BCH codewords.
DEFAULT_MAX_CODEWORDS = 147

# _scan_cltu results
_INCOMPLETE = "incomplete"  # more input is needed to decide
_NO_START = "no_start"      # start sequence doesn't match within threshold
_NO_TAIL = "no_tail"        # no tail sequence within max_codewords codewords
_EMPTY = "empty"            # tail directly follows the start sequence
_OK = "ok"                  # a complete CLTU


class cltu_deframer(gr.basic_block):
    """
    Find CLTUs (CCSDS 231.0-B-4) in a byte or bit stream and publish their
    codewords.

    A CLTU is a start sequence, one or more codewords, and a tail sequence
    (Figure 5-1). After a start sequence, codewords are read one by one
    until a block matches the tail sequence; the CLTU's codewords are
    published only once its tail is found, each as its own PDU (the form
    ccsds_receiver and bch_decoder expect). Start and tail sequences are
    accepted with up to `threshold` bit errors.

    CLTU starts are located either by GNU Radio's Correlate Access Code -
    Tag stream tags (general_work, the scheduler's path) or by searching the
    buffer directly (process_bytes).

        input_packed:
            - Yes: stream items are bytes (8 bits packed into one byte).
            - No: stream items are bits (one bit per byte, values 0/1).

        output_packed:
            - Yes: payload is published as bytes (8 bits packed into one byte).
            - No: payload is published as bits (one bit per byte, values 0/1).

        Tag offsets reported in logs are always in bits. If `input_packed` is True,
        offsets are multiplied by 8; if False, offsets already represent bits.
    """

    def __init__(self, start_sequence:int=0xEB90, tail_sequence:int=0xC5C5C5C5C5C5C579,
                 payload_bytes:int=8, threshold:int=2, input_packed:bool=True,
                 output_packed:bool=True, tag_name:str="start",
                 max_codewords:int=DEFAULT_MAX_CODEWORDS):
        """
        Args:
            start_sequence (int): CCSDS 231.0-B-4 CLTU start sequence (16 bits).
            tail_sequence (int): CCSDS 231.0-B-4 CLTU tail sequence (64 bits).
            payload_bytes (int): codeword length in bytes (8 for the
                BCH (63,56) code, including its filler bit).
            threshold (int): max bit errors tolerated in the start or
                tail sequence for a CLTU to still be accepted.
            input_packed (bool): True if stream items are bytes (8 bits
                packed per item); False if stream items are individual
                bits (one bit per item, values 0/1).
            output_packed (bool): True to publish the payload as bytes;
                False to publish it as individual bits.
            tag_name (str): stream tag key the tag-based detection path
                (general_work) looks for.
            max_codewords (int): most codewords one CLTU may hold. A start
                sequence with no tail within this many codewords is
                discarded as a false detection. Default 147: a maximum-size
                1024-byte TC transfer frame.

        Raises:
            ValueError: payload_bytes or max_codewords isn't positive, or
                threshold is negative.
        """
        if payload_bytes <= 0:
            raise ValueError("payload_bytes must be positive")
        if threshold < 0:
            raise ValueError("threshold must be non-negative")
        if max_codewords <= 0:
            raise ValueError("max_codewords must be positive")

        gr.basic_block.__init__(self, name="cltu_deframer", in_sig=[np.uint8], out_sig=None)

        self.message_port_register_out(pmt.intern("out"))

        self.start_sequence = start_sequence
        self.tail_sequence = tail_sequence
        self.data_length = payload_bytes
        self.threshold = threshold
        self.input_packed = input_packed
        self.output_packed = output_packed
        self.tag_name = tag_name
        self.max_codewords = max_codewords
        self.tag_offset_bits = -15

        self._buffer = bytearray()
        self._bit_buffer = []
        self._start_bytes = struct.pack("!H", self.start_sequence & 0xFFFF)
        self._tail_bytes = struct.pack("!Q", self.tail_sequence & 0xFFFFFFFFFFFFFFFF)
        self._start_bits = self._bytes_to_bits(self._start_bytes)
        self._tail_bits = self._bytes_to_bits(self._tail_bytes)
        self._codeword_bits = self.data_length * 8
        self._buffer_abs_start = 0
        self._pending_tags = deque()
        # absolute item index just past the last accepted CLTU; tags before
        # it (e.g. a start-like pattern inside a codeword) are ignored
        self._last_accepted_end_abs = None

    def _bytes_to_bits(self, data):
        bits = []
        for value in data:
            for bit in range(7, -1, -1):
                bits.append((value >> bit) & 1)
        return bits

    def _pack_bits_to_bytes(self, bits):
        packed = bytearray()
        count = 0
        current = 0
        for bit in bits:
            current = (current << 1) | (bit & 1)
            count += 1
            if count == 8:
                packed.append(current)
                current = 0
                count = 0
        if count:
            current = current << (8 - count)
            packed.append(current)
        return bytes(packed)

    def _count_errors(self, bits, expected):
        return sum(1 for a, b in zip(bits, expected) if a != b)

    def _scan_cltu(self, bits, start):
        """
        Walk a candidate CLTU whose start sequence begins at `bits[start]`.

        Args:
            bits (list[int]): bit buffer, one 0/1 per element.
            start (int): index where the start sequence is expected.

        Returns:
            tuple: (status, codewords, end, start_errors, tail_errors).
                status is one of _INCOMPLETE, _NO_START, _NO_TAIL, _EMPTY,
                _OK. For _OK, codewords is the list of codewords (each a
                list of bits) and end is the index just past the tail
                sequence; otherwise codewords and end are None.
        """
        start_end = start + len(self._start_bits)
        if start_end > len(bits):
            return _INCOMPLETE, None, None, None, None

        start_errors = self._count_errors(bits[start:start_end], self._start_bits)
        if start_errors > self.threshold:
            return _NO_START, None, None, start_errors, None

        codewords = []
        pos = start_end
        tail_len = len(self._tail_bits)
        while True:
            if pos + tail_len > len(bits):
                return _INCOMPLETE, None, None, start_errors, None
            tail_errors = self._count_errors(bits[pos:pos + tail_len], self._tail_bits)
            if tail_errors <= self.threshold:
                if not codewords:
                    return _EMPTY, None, None, start_errors, tail_errors
                return _OK, codewords, pos + tail_len, start_errors, tail_errors
            if len(codewords) >= self.max_codewords:
                return _NO_TAIL, None, None, start_errors, None
            if pos + self._codeword_bits > len(bits):
                return _INCOMPLETE, None, None, start_errors, None
            codewords.append(bits[pos:pos + self._codeword_bits])
            pos += self._codeword_bits

    def _publish_payload(self, payload_bytes=None, payload_bits=None, start_errors=0, tail_errors=0,
                         scramble_reset=True):
        """
        Args:
            payload_bytes (bytes | None): payload as bytes; ignored if
                payload_bits is given.
            payload_bits (list[int] | None): payload as a list of 0/1
                bits; takes priority over payload_bytes if both are given.
            start_errors (int): bit-error count against the expected
                start sequence, written into the output metadata.
            tail_errors (int): bit-error count against the expected
                tail sequence, written into the output metadata.
            scramble_reset (bool): written into the output metadata; True
                for a CLTU's first codeword, where CCSDS de-randomization
                restarts (lfsr_descrambler honors this key).

        Publishes:
            "out" (pmt_pair): PDU with `corr_start_errors`/`corr_tail_errors`/
                `scramble_reset` metadata and the payload, packed as bytes
                or left as individual bits per `output_packed`.

        Returns:
            pmt_pair: the same PDU published on "out".
        """
        if payload_bits is not None:
            if self.output_packed:
                payload_bytes = self._pack_bits_to_bytes(payload_bits)
            else:
                payload_bytes = bytes(payload_bits)
        elif payload_bytes is None:
            payload_bytes = b""
        elif not self.output_packed:
            unpacked = []
            for value in payload_bytes:
                for shift in range(7, -1, -1):
                    unpacked.append((value >> shift) & 1)
            payload_bytes = bytes(unpacked)

        self.logger.trace(f"Publishing payload: {payload_bytes.hex()} (start_err={start_errors}, tail_err={tail_errors})")

        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("corr_start_errors"), pmt.from_long(start_errors))
        meta = pmt.dict_add(meta, pmt.intern("corr_tail_errors"), pmt.from_long(tail_errors))
        meta = pmt.dict_add(meta, pmt.intern("scramble_reset"), pmt.from_bool(scramble_reset))

        out_msg = pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
        self.message_port_pub(pmt.intern("out"), out_msg)
        return out_msg

    def _publish_cltu(self, codewords, start_errors, tail_errors):
        """
        Args:
            codewords (list[list[int]]): the CLTU's codewords, each a list of bits.
            start_errors (int): start-sequence bit errors, for metadata.
            tail_errors (int): tail-sequence bit errors, for metadata.

        Publishes:
            "out" (pmt_pair): one PDU per codeword, in order (see
                _publish_payload); only the first has scramble_reset True.
        """
        self.logger.info(
            f"CLTU accepted: {len(codewords)} codeword(s), start_err={start_errors}, tail_err={tail_errors}"
        )
        for index, codeword in enumerate(codewords):
            self._publish_payload(payload_bits=codeword, start_errors=start_errors,
                                  tail_errors=tail_errors, scramble_reset=(index == 0))

    def process_bytes(self, incoming):
        """
        Search incoming data for CLTUs without stream tags and publish
        every complete one found. Data after the last complete CLTU is kept
        for the next call, so a CLTU may arrive split across calls. In
        packed mode, CLTUs are searched at byte boundaries only.

        Args:
            incoming: bytes (input_packed) or 0/1 bit values (not packed).
        """
        if not incoming:
            return

        if self.input_packed:
            self._buffer.extend(incoming)
            bits = self._bytes_to_bits(self._buffer)
            step = 8
        else:
            for b in incoming:
                self._bit_buffer.append(int(b) & 1)
            bits = self._bit_buffer
            step = 1

        offset = 0
        while offset + len(self._start_bits) <= len(bits):
            status, codewords, end, start_errors, tail_errors = self._scan_cltu(bits, offset)
            if status == _INCOMPLETE:
                break
            if status == _OK:
                self._publish_cltu(codewords, start_errors, tail_errors)
                offset = end
                continue
            if status == _NO_TAIL:
                self.logger.debug(f"Start sequence at bit {offset} has no tail within {self.max_codewords} codewords")
            offset += step

        if self.input_packed:
            del self._buffer[:offset // 8]
        else:
            del self._bit_buffer[:offset]

    def general_work(self, input_items, output_items):
        """
        Args: standard gr.basic_block general_work() signature;
            `output_items` is unused (this block has no stream output).

        Returns:
            int: always 0 (nothing produced on a stream output port -
            this block's real output is the "out" message port).
            Consumes every item read via `consume_each`.
        """
        if not input_items:
            return 0

        incoming = bytes(input_items[0])
        if not incoming:
            return 0

        # Convert incoming items to buffer, accounting for packing
        items_processed = 0
        buffer_was_empty = (len(self._buffer) == 0) if self.input_packed else (len(self._bit_buffer) == 0)
        if self.input_packed:
            self._buffer.extend(incoming)
            items_processed = len(incoming)
        else:
            # incoming is a sequence of bits (0/1) as uint8 values
            for b in incoming:
                self._bit_buffer.append(int(b) & 1)
                items_processed += 1

        # Get the absolute item number of the first item in this call
        abs_item_start = self.nitems_read(0)

        if buffer_was_empty:
            self._buffer_abs_start = abs_item_start

        # Look for tags in this input chunk
        tags = self.get_tags_in_range(0, abs_item_start, abs_item_start + len(incoming))

        for tag in tags:
            try:
                tag_key = pmt.symbol_to_string(tag.key) if pmt.is_symbol(tag.key) else str(tag.key)
            except Exception:
                tag_key = str(tag.key)

            if tag_key == self.tag_name:
                tag_abs = int(tag.offset)
                if self._last_accepted_end_abs is not None and tag_abs < self._last_accepted_end_abs:
                    continue
                bit_offset = int(tag.offset) * (8 if self.input_packed else 1)
                self.logger.debug(f"Tag '{self.tag_name}' found at bit offset {bit_offset}")
                if not self._pending_tags or self._pending_tags[-1] != tag_abs:
                    self._pending_tags.append(tag_abs)
        self._try_process_pending_tag()
        self._trim_untagged_buffer()

        self.consume_each(items_processed)
        return 0

    def _trim_untagged_buffer(self):
        """With no tag pending, keep only the few items a tag arriving in
        the next chunk could still need (its start sequence may begin up to
        16 bits before it), so an untagged stream doesn't grow the buffer
        forever."""
        if self._pending_tags:
            return
        if self.input_packed:
            keep = 3
            drop = max(0, len(self._buffer) - keep)
            del self._buffer[:drop]
        else:
            keep = len(self._start_bits) + 8
            drop = max(0, len(self._bit_buffer) - keep)
            del self._bit_buffer[:drop]
        self._buffer_abs_start += drop

    def _try_process_pending_tag(self):
        """Process pending tags in order, while each one's CLTU can be decided
        from the buffered data (helper for tests)."""
        while self._pending_tags:
            tag_abs = self._pending_tags[0]

            if self.input_packed:
                frame_start = (tag_abs - self._buffer_abs_start) + (self.tag_offset_bits // 8)
                if frame_start < 0:
                    self._pending_tags.popleft()
                    continue
                bits = self._bytes_to_bits(self._buffer[frame_start:])
                candidates = [0]
            else:
                frame_start = (tag_abs - self._buffer_abs_start) + self.tag_offset_bits
                if frame_start < 0:
                    self._pending_tags.popleft()
                    continue
                bits = self._bit_buffer
                # the correlator's tag may be off by one bit
                candidates = [c for c in (frame_start, frame_start - 1, frame_start + 1) if c >= 0]

            results = [(c, self._scan_cltu(bits, c)) for c in candidates]
            accepted = next(((c, r) for c, r in results if r[0] == _OK), None)
            if accepted is None and any(r[0] == _INCOMPLETE for _, r in results):
                return  # wait for more data

            if accepted is not None:
                _, (_, codewords, end, start_errors, tail_errors) = accepted
                self._publish_cltu(codewords, start_errors, tail_errors)
                if self.input_packed:
                    consumed = frame_start + end // 8
                    del self._buffer[:consumed]
                else:
                    consumed = end
                    del self._bit_buffer[:consumed]
                self._buffer_abs_start += consumed
                self._last_accepted_end_abs = self._buffer_abs_start
            else:
                status, _, _, start_errors, tail_errors = results[0][1]
                self.logger.warn(
                    f"CLTU rejected at tag offset {tag_abs}: {status} "
                    f"(start_errors={start_errors}, tail_errors={tail_errors}, threshold={self.threshold}, "
                    f"max_codewords={self.max_codewords})"
                )
                buffer = self._buffer if self.input_packed else self._bit_buffer
                drop = min(frame_start + 1, len(buffer))
                del buffer[:drop]
                self._buffer_abs_start += drop

            self._pending_tags.popleft()

            while self._pending_tags and self._pending_tags[0] < self._buffer_abs_start:
                self._pending_tags.popleft()

    def process_pending_tag(self):
        """Public helper to process a pending tag (used by unit tests)."""
        self._try_process_pending_tag()

    def test_set_bit_buffer(self, bit_stream, buffer_abs_start=0):
        """Public helper to set bit buffer state for unit tests."""
        self._bit_buffer = list(bit_stream)
        self._buffer_abs_start = int(buffer_abs_start)

    def test_set_pending_tag(self, tag_offset):
        """Public helper to set pending tag for unit tests."""
        self._pending_tags = deque([int(tag_offset)])
