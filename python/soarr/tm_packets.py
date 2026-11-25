#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""The packets a TM Transfer Frame carries, as far as the frame layer needs
them: how long a packet is and whether it is idle. Shared by
tm_frame_encoder and tm_frame_decoder, so both delimit packets by the
same rules.

The first 3 bits of a packet are its version:

    111  Encapsulation Packet (CCSDS 133.1-B), header 1, 2, 4 or 8 bytes
         (encapsulation_packet); packet_length counts the whole packet.
    000  Space Packet (CCSDS 133.0-B-2), 6-byte primary header; the packet
         data length field (bytes 4-5) counts the data field minus 1.

Any other version has no length field the frame layer knows.
"""

from .encapsulation_packet import PACKET_VERSION_NUMBER, PROTOCOL_ID_IDLE, header_length, parse_header

SPACE_PACKET_VERSION = 0b000  # CCSDS 133.0-B-2 4.1.3.2
SPACE_PACKET_HEADER_BYTES = 6
SPACE_PACKET_IDLE_APID = 0x7FF  # CCSDS 133.0-B-2 4.1.3.3.4.4


def is_space_packet(first_octet: int) -> bool:
    """
    Args:
        first_octet (int): the first byte of a packet.

    Returns:
        bool: True for a Space Packet (version 000).
    """
    return first_octet >> 5 == SPACE_PACKET_VERSION


def packet_header_length(first_octet: int):
    """
    Args:
        first_octet (int): the first byte of a packet.

    Returns:
        int or None: how many bytes of the packet hold its length - the
            encapsulation header or the Space Packet primary header; None
            for any other version.
    """
    version = first_octet >> 5
    if version == PACKET_VERSION_NUMBER:
        return header_length(first_octet & 0b11)
    if version == SPACE_PACKET_VERSION:
        return SPACE_PACKET_HEADER_BYTES
    return None


def packet_length(header: bytes):
    """
    Args:
        header (bytes): the start of a packet, at least
            packet_header_length bytes of it.

    Returns:
        int or None: the packet's total length in bytes, header included;
            None if its version has no known length field or the length is
            shorter than the header.
    """
    needed = packet_header_length(header[0])
    if needed is None:
        return None
    if is_space_packet(header[0]):
        total = SPACE_PACKET_HEADER_BYTES + int.from_bytes(header[4:6], "big") + 1
    else:
        total = parse_header(header[:needed])["packet_length"] or 1  # the 1-byte idle packet has no length field
    return total if total >= needed else None


def is_idle(header: bytes) -> bool:
    """
    Args:
        header (bytes): the start of a packet, at least
            packet_header_length bytes of it.

    Returns:
        bool: True for an idle packet - an Encapsulation Packet with
            protocol ID 000, or a Space Packet with APID all ones.
    """
    if is_space_packet(header[0]):
        return ((header[0] & 0b111) << 8) | header[1] == SPACE_PACKET_IDLE_APID
    return header[0] >> 5 == PACKET_VERSION_NUMBER and (header[0] >> 2) & 0b111 == PROTOCOL_ID_IDLE
