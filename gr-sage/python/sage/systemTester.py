#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#


import json
import os
import threading
import time
from collections import deque

from gnuradio import gr
import pmt


WAITING_TRIGGER_MODE = 0
AUTOMATIC_MODE = 1


class SystemTester(gr.basic_block):
    """Track bit error rate, message error rate, and lost packets for a loopback test.
    
    Synchronized directly via the 'vcid_counter' embedded inside the 'tc_header' metadata.
    """

    def __init__(self, repetitions=100, mode=0, timeout_s=1.0, stats_path="systemTester_stats.json"):
        gr.basic_block.__init__(self,
            name="SystemTester",
            in_sig=None,
            out_sig=None)

        if mode not in (WAITING_TRIGGER_MODE, AUTOMATIC_MODE):
            raise ValueError(f"Invalid mode: {mode}. Expected 0 (waiting trigger) or 1 (automatic).")

        self.repetitions = int(repetitions)
        self.mode = int(mode)
        self.timeout_s = float(timeout_s)
        self.stats_path = stats_path

        self.message_port_register_in(pmt.intern("start"))
        self.message_port_register_in(pmt.intern("original"))
        self.message_port_register_in(pmt.intern("transmitted"))
        self.message_port_register_in(pmt.intern("received"))

        self.message_port_register_out(pmt.intern("trigger"))

        self.set_msg_handler(pmt.intern("start"), self.handle_start)
        self.set_msg_handler(pmt.intern("original"), self.handle_original)
        self.set_msg_handler(pmt.intern("transmitted"), self.handle_transmitted)
        self.set_msg_handler(pmt.intern("received"), self.handle_received)

        self._lock = threading.RLock()
        self._pending_order = deque()
        self._entries = {}

        self._stats = {
            "generated_packets": 0,
            "original_packets": 0,
            "transmitted_packets": 0,
            "received_packets": 0,
            "lost_packets": 0,
            "message_errors": 0,
            "bit_errors": 0,
            "compared_bits": 0,
            "ignored_starts": 0,
        }

    def _extract_payload(self, msg):
        if not pmt.is_pair(msg):
            return None

        payload = pmt.cdr(msg)
        if not pmt.is_u8vector(payload):
            return None

        return bytes(pmt.u8vector_elements(payload))

    def _extract_vcid_counter(self, msg):
        """Helper to navigate the PMT dictionary structure to find the vcid_counter.
        
        Structure: meta -> telecommand -> tc_header -> vcid_counter
        """
        if not pmt.is_pair(msg):
            return None
        
        meta = pmt.car(msg)
        if not pmt.is_dict(meta):
            return None

        # Extract telecommand dictionary
        telecommand = pmt.dict_ref(meta, pmt.intern("telecommand"), pmt.PMT_NIL)
        if not pmt.is_dict(telecommand):
            return None

        # Extract tc_header dictionary
        tc_header = pmt.dict_ref(telecommand, pmt.intern("tc_header"), pmt.PMT_NIL)
        if not pmt.is_dict(tc_header):
            return None

        # Extract vcid_counter value
        vcid_counter_pmt = pmt.dict_ref(tc_header, pmt.intern("vcid_counter"), pmt.PMT_NIL)
        if pmt.is_integer(vcid_counter_pmt):
            return pmt.to_long(vcid_counter_pmt)
            
        return None

    def _payload_to_pmt(self, payload_bytes):
        return pmt.init_u8vector(len(payload_bytes), list(payload_bytes))

    def _make_trigger_msg(self, packet_id):
        meta = pmt.make_dict()
        meta = pmt.dict_add(meta, pmt.intern("packet_id"), pmt.from_long(packet_id))
        meta = pmt.dict_add(meta, pmt.intern("mode"), pmt.from_long(self.mode))
        return pmt.cons(meta, pmt.PMT_NIL)

    def _get_stats_snapshot(self):
        with self._lock:
            snapshot = dict(self._stats)
            compared_bits = snapshot["compared_bits"]
            received_packets = snapshot["received_packets"]
            snapshot["bit_error_rate"] = (snapshot["bit_errors"] / compared_bits) if compared_bits else 0.0
            snapshot["message_error_rate"] = (snapshot["message_errors"] / received_packets) if received_packets else 0.0
            snapshot["lost_packet_rate"] = (snapshot["lost_packets"] / snapshot["generated_packets"]) if snapshot["generated_packets"] else 0.0
            return snapshot

    def get_stats(self):
        return self._get_stats_snapshot()

    def _save_stats(self):
        if not self.stats_path:
            return

        stats = self._get_stats_snapshot()
        directory = os.path.dirname(self.stats_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        with open(self.stats_path, "w", encoding="utf-8") as stats_file:
            json.dump(stats, stats_file, indent=2, sort_keys=True)

    def _print_stats(self, prefix):
        stats = self._get_stats_snapshot()
        self.logger.info(
            f"{prefix}: generated={stats['generated_packets']} received={stats['received_packets']} "
            f"lost={stats['lost_packets']} message_errors={stats['message_errors']} "
            f"bit_errors={stats['bit_errors']} compared_bits={stats['compared_bits']} "
            f"ber={stats['bit_error_rate']:.6e} mer={stats['message_error_rate']:.6e}"
        )

    def _update_stats(self):
        self._print_stats("SystemTester stats")
        self._save_stats()

    def _start_timeout_timer(self, packet_id):
        if self.timeout_s <= 0:
            return None

        timer = threading.Timer(self.timeout_s, self._timeout_packet, args=(packet_id,))
        timer.daemon = True
        timer.start()
        return timer

    def _get_or_create_entry(self, packet_id, source):
        """Fetch tracking item by vcid_counter id, or create it if missing."""
        if packet_id in self._entries:
            return self._entries[packet_id]

        entry = {
            "packet_id": packet_id,
            "source": source,
            "created_at": time.time(),
            "original": None,
            "transmitted": None,
            "received": None,
            "completed": False,
            "timer": None,
        }

        entry["timer"] = self._start_timeout_timer(packet_id)
        self._pending_order.append(packet_id)
        self._entries[packet_id] = entry
        self._stats["generated_packets"] += 1
        return entry

    def _bit_errors(self, left_bytes, right_bytes):
        bit_errors = 0
        shared_length = min(len(left_bytes), len(right_bytes))

        for index in range(shared_length):
            bit_errors += (left_bytes[index] ^ right_bytes[index]).bit_count()

        if len(left_bytes) != len(right_bytes):
            longer = left_bytes if len(left_bytes) > len(right_bytes) else right_bytes
            for byte in longer[shared_length:]:
                bit_errors += int(byte).bit_count()

        compared_bits = max(len(left_bytes), len(right_bytes)) * 8
        return bit_errors, compared_bits

    def _finalize_entry(self, entry, received_payload):
        reference_payload = entry["original"] if entry["original"] is not None else entry["transmitted"]

        if reference_payload is None:
            self._stats["lost_packets"] += 1
            self.logger.warn(f"Packet id={entry['packet_id']} completed without a reference payload; marking as lost.")
            return

        self._stats["received_packets"] += 1
        bit_errors, compared_bits = self._bit_errors(reference_payload, received_payload)
        self._stats["bit_errors"] += bit_errors
        self._stats["compared_bits"] += compared_bits

        if bit_errors > 0 or len(reference_payload) != len(received_payload):
            self._stats["message_errors"] += 1
            self.logger.info(
                f"Packet id={entry['packet_id']} has payload errors: "
                f"reference_len={len(reference_payload)} received_len={len(received_payload)} bit_errors={bit_errors}."
            )
        else:
            self.logger.info(f"Packet id={entry['packet_id']} payload matched exactly.")

        entry["completed"] = True
        if entry["timer"] is not None:
            entry["timer"].cancel()

        self._update_stats()

    def _timeout_packet(self, packet_id):
        with self._lock:
            entry = self._entries.get(packet_id)
            if entry is None or entry["completed"]:
                return

            entry["completed"] = True
            self._stats["lost_packets"] += 1
            self.logger.warn(f"Packet id={packet_id} timed out after {self.timeout_s:.3f}s and was marked as lost.")
            self._update_stats()

    def handle_start(self, msg):
        """Note: handle_start doesn't see the generated metadata structure yet.
        It generates a trigger message based on basic count, but the stream parsing ports 
        will automatically track using vcid_counter if it's extracted there instead.
        """
        with self._lock:
            if self.repetitions > 0 and self._stats["generated_packets"] >= self.repetitions:
                self.logger.info("Requested start ignored because the configured repetition limit was reached.")
                return

            if self.mode == WAITING_TRIGGER_MODE:
                has_outstanding = any(not entry["completed"] for entry in self._entries.values())
                if has_outstanding:
                    self._stats["ignored_starts"] += 1
                    self.logger.info("Waiting-trigger mode is busy; ignoring start request until the active packet completes.")
                    self._update_stats()
                    return

            # Temporary incremental tracking key if created via internal trigger port
            # (Will synchronize seamlessly when incoming streams report the true vcid_counter)
            fallback_id = len(self._entries)
            entry = self._get_or_create_entry(fallback_id, "start")

        self.logger.debug(f"Emitting trigger for packet id={entry['packet_id']}")
        self.message_port_pub(pmt.intern("trigger"), self._make_trigger_msg(entry['packet_id']))

    def handle_original(self, msg):
        payload_bytes = self._extract_payload(msg)
        if payload_bytes is None:
            self.logger.error("Received original message without a PDU payload. Ignoring it.")
            return

        vcid_counter = self._extract_vcid_counter(msg)
        if vcid_counter is None:
            self.logger.warn("Could not extract vcid_counter from original metadata path; fallback to stream ordering logic.")
            return

        with self._lock:
            entry = self._get_or_create_entry(vcid_counter, "original")
            entry["original"] = payload_bytes
            self._stats["original_packets"] += 1
            self.logger.debug(f"Stored original payload for packet id={entry['packet_id']} ({len(payload_bytes)} bytes).")

    def handle_transmitted(self, msg):
        payload_bytes = self._extract_payload(msg)
        if payload_bytes is None:
            self.logger.error("Received transmitted message without a PDU payload. Ignoring it.")
            return

        vcid_counter = self._extract_vcid_counter(msg)
        if vcid_counter is None:
            self.logger.warn("Could not extract vcid_counter from transmitted metadata path.")
            return

        with self._lock:
            entry = self._get_or_create_entry(vcid_counter, "transmitted")
            entry["transmitted"] = payload_bytes
            self._stats["transmitted_packets"] += 1
            self.logger.debug(f"Stored transmitted payload for packet id={entry['packet_id']} ({len(payload_bytes)} bytes).")

    def handle_received(self, msg):
        payload_bytes = self._extract_payload(msg)
        if payload_bytes is None:
            self.logger.error("Received message without a PDU payload. Ignoring it.")
            return

        vcid_counter = self._extract_vcid_counter(msg)
        if vcid_counter is None:
            self.logger.warn("Could not extract vcid_counter from received metadata path.")
            return

        with self._lock:
            entry = self._get_or_create_entry(vcid_counter, "received")
            entry["received"] = payload_bytes
            self.logger.debug(f"Stored received payload for packet id={entry['packet_id']} ({len(payload_bytes)} bytes).")
            self._finalize_entry(entry, payload_bytes)


systemTester = SystemTester