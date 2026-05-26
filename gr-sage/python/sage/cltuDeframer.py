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


class cltuDeframer(gr.basic_block):
    """
    Find CLTU frames using GNU Radio's Correlated Access Code Tags.
    
    When a tag is found, expects: 2 Bytes Start sequence, N Bytes data, 8 Bytes tail sequence.
    Validates start and tail sequences against expected values and warns if correlation 
    errors exceed threshold.

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
                 output_packed:bool=True, tag_name:str="start"):
        if payload_bytes <= 0:
            raise ValueError("payload_bytes must be positive")
        if threshold < 0:
            raise ValueError("threshold must be non-negative")

        gr.basic_block.__init__(self, name="cltuDeframer", in_sig=[np.uint8], out_sig=None)

        self.message_port_register_out(pmt.intern("out"))

        self.start_sequence = start_sequence
        self.tail_sequence = tail_sequence
        self.data_length = payload_bytes
        self.threshold = threshold
        self.input_packed = input_packed
        self.output_packed = output_packed
        self.tag_name = tag_name
        self.bit_order = "msb"
        self.tag_offset_bits = -15

        self._buffer = bytearray()
        self._bit_buffer = []
        # partial byte state when input_packed == False
        self._partial_byte = 0
        self._partial_count = 0
        self._start_bytes = struct.pack("!H", self.start_sequence & 0xFFFF)
        self._tail_bytes = struct.pack("!Q", self.tail_sequence & 0xFFFFFFFFFFFFFFFF)
        self._frame_length = len(self._start_bytes) + self.data_length + len(self._tail_bytes)
        self._frame_length_bits = self._frame_length * 8
        self._frame_length_items = self._frame_length if self.input_packed else self._frame_length_bits
        self._start_bits = self._bytes_to_bits(self._start_bytes)
        self._tail_bits = self._bytes_to_bits(self._tail_bytes)
        self._buffer_abs_start = 0
        self._pending_tags = deque()
        self._last_accepted_tag_abs = None

    def _bit_errors(self, left, right):
        """Calculate number of bit errors between two byte sequences."""
        return sum((l ^ r).bit_count() for l, r in zip(left, right))

    def _reverse_byte_bits(self, value):
        result = 0
        for bit in range(8):
            if value & (1 << bit):
                result |= 1 << (7 - bit)
        return result

    def _bytes_to_bits(self, data):
        bits = []
        for value in data:
            if self.bit_order == "msb":
                for bit in range(7, -1, -1):
                    bits.append((value >> bit) & 1)
            else:
                for bit in range(8):
                    bits.append((value >> bit) & 1)
        return bits

    def _pack_bits_to_bytes(self, bits):
        packed = bytearray()
        count = 0
        current = 0
        for bit in bits:
            if self.bit_order == "msb":
                current = (current << 1) | (bit & 1)
            else:
                current |= (bit & 1) << count
            count += 1
            if count == 8:
                packed.append(current)
                current = 0
                count = 0
        if count:
            if self.bit_order == "msb":
                current = current << (8 - count)
            packed.append(current)
        return bytes(packed)

    def _bit_error_positions(self, left, right):
        """Return bit indices that differ between two byte sequences."""
        positions = []
        for byte_index, (l, r) in enumerate(zip(left, right)):
            diff = l ^ r
            if diff == 0:
                continue
            if self.bit_order == "msb":
                for bit in range(7, -1, -1):
                    if diff & (1 << bit):
                        positions.append(byte_index * 8 + (7 - bit))
            else:
                for bit in range(8):
                    if diff & (1 << bit):
                        positions.append(byte_index * 8 + bit)
        return positions

    def _bits_to_string(self, bits):
        return "".join("1" if b else "0" for b in bits)

    def _validate_frame_bits(self, frame_bits):
        """
        Validate frame start and tail sequences at bit level.

        Returns: (is_valid, start_errors, tail_errors)
        """
        if len(frame_bits) != self._frame_length_bits:
            return False, -1, -1

        start_errors = sum(1 for a, b in zip(frame_bits[:16], self._start_bits) if a != b)
        tail_errors = sum(1 for a, b in zip(frame_bits[-64:], self._tail_bits) if a != b)

        is_valid = start_errors <= self.threshold and tail_errors <= self.threshold

        return is_valid, start_errors, tail_errors

    def _publish_payload(self, payload_bytes=None, payload_bits=None, start_errors=0, tail_errors=0):
        """Publish the extracted payload with optional correlation error metadata."""
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

        try:
            hex_str = payload_bytes.hex()
        except Exception:
            hex_str = repr(payload_bytes)
        
        self.logger.trace(f"Publishing payload: {hex_str} (start_err={start_errors}, tail_err={tail_errors})")
        
        # Create metadata dict with correlation error info
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("corr_start_errors"), pmt.from_long(start_errors))
        meta = pmt.dict_add(meta, pmt.intern("corr_tail_errors"), pmt.from_long(tail_errors))
        
        out_msg = pmt.cons(meta, pmt.init_u8vector(len(payload_bytes), list(payload_bytes)))
        self.message_port_pub(pmt.intern("out"), out_msg)
        self.logger.debug(f"OK")
        return out_msg

    def _process_tag(self, frame_start):
        """
        Process a frame detected by a correlated access code tag.
        
        Args:
            frame_start: The byte offset in the internal buffer where the frame starts
        """
        if self.input_packed:
            frame_end = frame_start + self._frame_length
            if frame_end > len(self._buffer):
                # Not enough data yet
                return False
            frame_bytes = bytes(self._buffer[frame_start:frame_end])
            frame_bits = self._bytes_to_bits(frame_bytes)
        else:
            frame_end = frame_start + self._frame_length_bits
            if frame_end > len(self._bit_buffer):
                # Not enough data yet
                return False
            frame_bits = self._bit_buffer[frame_start:frame_end]

        is_valid, start_errors, tail_errors = self._validate_frame_bits(frame_bits)
        
        if not is_valid:
            # Log warning about correlation errors
            start_positions = [i for i, (a, b) in enumerate(zip(frame_bits[:16], self._start_bits)) if a != b]
            tail_positions = [i for i, (a, b) in enumerate(zip(frame_bits[-64:], self._tail_bits)) if a != b]
            start_seen = self._bits_to_string(frame_bits[:16])
            tail_seen = self._bits_to_string(frame_bits[-64:])
            start_expected = self._bits_to_string(self._start_bits)
            tail_expected = self._bits_to_string(self._tail_bits)
            self.logger.warn(
                f"Frame correlation errors exceed threshold: "
                f"start_errors={start_errors} (threshold={self.threshold}), "
                f"tail_errors={tail_errors} (threshold={self.threshold}) "
                f"start_bits={start_positions} tail_bits={tail_positions}"
            )
            self.logger.warn(
                f"Start bits seen={start_seen} expected={start_expected}"
            )
            self.logger.warn(
                f"Tail bits seen={tail_seen} expected={tail_expected}"
            )
            return False
        
        # Extract and publish payload
        payload_start = 16
        payload_end = payload_start + (self.data_length * 8)
        payload_bits = frame_bits[payload_start:payload_end]
        payload = self._pack_bits_to_bytes(payload_bits)
        try:
            payload_hex = payload.hex()
        except Exception:
            payload_hex = repr(payload)
        self.logger.info(
            f"Frame accepted (start_err={start_errors}, tail_err={tail_errors}) payload={payload_hex}"
        )
        self._publish_payload(payload_bits=payload_bits, start_errors=start_errors, tail_errors=tail_errors)
        
        return True

    def process_bytes(self, incoming):
        """
        Process bytes without tags (for backward compatibility with tests).
        Searches for valid frames in a buffer.
        """
        if not incoming:
            return

        if self.input_packed:
            self._buffer.extend(incoming)
        else:
            # incoming is a sequence of bits (0/1) as uint8 values
            for b in incoming:
                self._bit_buffer.append(int(b) & 1)

        # Extract frames by searching for valid patterns
        if self.input_packed:
            while len(self._buffer) >= self._frame_length:
                frame_start = None
                limit = len(self._buffer) - self._frame_length + 1

                for offset in range(limit):
                    candidate_bytes = bytes(self._buffer[offset:offset + self._frame_length])
                    candidate_bits = self._bytes_to_bits(candidate_bytes)
                    is_valid, start_errors, tail_errors = self._validate_frame_bits(candidate_bits)
                    if is_valid:
                        frame_start = offset
                        break

                if frame_start is None:
                    # No valid frame found; keep last frame_length-1 bytes for next chunk
                    keep = self._frame_length - 1
                    if len(self._buffer) > keep:
                        del self._buffer[:-keep]
                    return

                frame_end = frame_start + self._frame_length
                payload_start = frame_start + len(self._start_bytes)
                payload_end = payload_start + self.data_length
                payload = bytes(self._buffer[payload_start:payload_end])
                candidate_bits = self._bytes_to_bits(bytes(self._buffer[frame_start:frame_end]))
                is_valid, start_errors, tail_errors = self._validate_frame_bits(candidate_bits)
                self._publish_payload(payload_bytes=payload, start_errors=start_errors, tail_errors=tail_errors)
                del self._buffer[:frame_end]
        else:
            while len(self._bit_buffer) >= self._frame_length_bits:
                frame_start = None
                limit = len(self._bit_buffer) - self._frame_length_bits + 1

                for offset in range(limit):
                    candidate_bits = self._bit_buffer[offset:offset + self._frame_length_bits]
                    is_valid, start_errors, tail_errors = self._validate_frame_bits(candidate_bits)
                    if is_valid:
                        frame_start = offset
                        break

                if frame_start is None:
                    keep = self._frame_length_bits - 1
                    if len(self._bit_buffer) > keep:
                        del self._bit_buffer[:-keep]
                    return

                frame_end = frame_start + self._frame_length_bits
                payload_start = 16
                payload_end = payload_start + (self.data_length * 8)
                candidate_bits = self._bit_buffer[frame_start:frame_end]
                is_valid, start_errors, tail_errors = self._validate_frame_bits(candidate_bits)
                payload_bits = candidate_bits[payload_start:payload_end]
                self._publish_payload(payload_bits=payload_bits, start_errors=start_errors, tail_errors=tail_errors)
                del self._bit_buffer[:frame_end]


    def general_work(self, input_items, output_items):
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
                if self._last_accepted_tag_abs is not None:
                    if tag_abs < self._last_accepted_tag_abs + self._frame_length_items:
                        continue
                bit_offset = int(tag.offset) * (8 if self.input_packed else 1)
                self.logger.debug(f"Tag '{self.tag_name}' found at bit offset {bit_offset}")
                if not self._pending_tags or self._pending_tags[-1] != tag_abs:
                    self._pending_tags.append(tag_abs)
                    if not self.input_packed:
                        self.logger.trace(
                            f"bit_buffer_len={len(self._bit_buffer)} buffer_abs_start={self._buffer_abs_start}"
                        )
                        end_pos = tag_abs - self._buffer_abs_start
                        start_pos = end_pos - 15
                        if start_pos >= 0 and end_pos < len(self._bit_buffer):
                            window_bits = self._bit_buffer[start_pos:end_pos + 1]
                            window_str = self._bits_to_string(window_bits)
                            self.logger.trace(
                                f"Access code window @tag (len=16): {window_str}"
                            )
        self._try_process_pending_tag()

        self.consume_each(items_processed)
        return 0

    def _try_process_pending_tag(self):
        """Process a pending tag if the full frame is buffered (helper for tests)."""
        while self._pending_tags:
            tag_abs = self._pending_tags[0]

            if self.input_packed:
                frame_start = (tag_abs - self._buffer_abs_start) + (self.tag_offset_bits // 8)
                frame_end = frame_start + self._frame_length
                if frame_start < 0:
                    self._pending_tags.popleft()
                    continue
                if frame_end > len(self._buffer):
                    return
                if self._process_tag(frame_start):
                    del self._buffer[:frame_end]
                    self._buffer_abs_start = self._buffer_abs_start + frame_end
                    self._last_accepted_tag_abs = tag_abs
                else:
                    drop = min(frame_start + 1, len(self._buffer))
                    del self._buffer[:drop]
                    self._buffer_abs_start += drop
                self._pending_tags.popleft()
            else:
                frame_start = (tag_abs - self._buffer_abs_start) + self.tag_offset_bits
                frame_end = frame_start + self._frame_length_bits
                if frame_start < 0:
                    self._pending_tags.popleft()
                    continue
                if frame_end > len(self._bit_buffer):
                    return

                candidates = [frame_start, frame_start - 1, frame_start + 1]
                accepted = False
                for candidate_start in candidates:
                    if candidate_start < 0:
                        continue
                    candidate_end = candidate_start + self._frame_length_bits
                    if candidate_end > len(self._bit_buffer):
                        continue
                    frame_bits = self._bit_buffer[candidate_start:candidate_end]
                    is_valid, start_errors, tail_errors = self._validate_frame_bits(frame_bits)
                    if not is_valid:
                        continue
                    payload_start = 16
                    payload_end = payload_start + (self.data_length * 8)
                    payload_bits = frame_bits[payload_start:payload_end]
                    self._publish_payload(payload_bits=payload_bits, start_errors=start_errors, tail_errors=tail_errors)
                    del self._bit_buffer[:candidate_end]
                    self._buffer_abs_start = self._buffer_abs_start + candidate_end
                    self._last_accepted_tag_abs = tag_abs
                    accepted = True
                    break

                if not accepted:
                    if self._process_tag(frame_start):
                        del self._bit_buffer[:frame_end]
                        self._buffer_abs_start = self._buffer_abs_start + frame_end
                        self._last_accepted_tag_abs = tag_abs
                    else:
                        drop = min(frame_start + 1, len(self._bit_buffer))
                        del self._bit_buffer[:drop]
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

