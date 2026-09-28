#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2026 Yannick Kulli.
#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""CCSDS 133.1-B Encapsulation Packet header: the one definition of its wire
format, shared by every block that builds, parses, or rebuilds it.

Header layout (Table 4-2), selected by the 2-bit length-of-length code:

    LoL  bytes  fields after the first octet
    00   1      -                                (idle packet, no data)
    01   2      packet length (8 bit)
    10   4      user defined (4) + protocol id extension (4), packet length (16)
    11   8      user defined (4) + protocol id extension (4),
                CCSDS defined field (16), packet length (32)

First octet: packet version (3 bit), protocol id (3 bit), length of length
(2 bit). The packet length counts header plus data.
"""

from collections import namedtuple

from construct import Computed, If, Int8ub, Int16ub, Int32ub, Struct, Switch, this

PACKET_VERSION_NUMBER = 0b111
PROTOCOL_ID_IDLE = 0b000
PROTOCOL_ID_DATA = 0b111
PROTOCOL_ID_EXTENSION = 0b0000
CCSDS_DEFINED_FIELD = 0x0000

LengthOfLengthInfo = namedtuple("LengthOfLengthInfo", ["max_length", "header_length", "packet_length_bytes"])

# max_length: largest data field the variant can carry
LENGTH_OF_LENGTH_TABLE = {
    0b00: LengthOfLengthInfo(max_length=0, header_length=1, packet_length_bytes=0),
    0b01: LengthOfLengthInfo(max_length=2**8 - 3, header_length=2, packet_length_bytes=1),
    0b10: LengthOfLengthInfo(max_length=2**16 - 5, header_length=4, packet_length_bytes=2),
    0b11: LengthOfLengthInfo(max_length=2**32 - 9, header_length=8, packet_length_bytes=4),
}

# construct form of the header, for embedding in a larger frame format
# (ccsds_reader). Field names match parse_header.
HEADER_STRUCT = Struct(
    "first_octet" / Int8ub,
    "packet_version" / Computed(lambda ctx: (ctx.first_octet >> 5) & 0b111),
    "protocol_id" / Computed(lambda ctx: (ctx.first_octet >> 2) & 0b111),
    "length_of_length" / Computed(lambda ctx: ctx.first_octet & 0b11),
    "_user_defined_field_raw" / If(this.length_of_length >= 0b10, Int8ub),
    "user_defined_field" / Computed(
        lambda ctx: (ctx._user_defined_field_raw >> 4) & 0b1111 if ctx._user_defined_field_raw is not None else None
    ),
    "protocol_id_extension" / Computed(
        lambda ctx: ctx._user_defined_field_raw & 0b1111 if ctx._user_defined_field_raw is not None else None
    ),
    "ccsds_defined_field" / If(this.length_of_length >= 0b11, Int16ub),
    "packet_length" / If(
        this.length_of_length != 0b00,
        Switch(this.length_of_length, {0b01: Int8ub, 0b10: Int16ub, 0b11: Int32ub}),
    ),
)


def length_of_length_for(data_length: int) -> int:
    """
    Args:
        data_length (int): length of the encapsulated data field in bytes.

    Returns:
        int: the smallest length-of-length code that can carry it.

    Raises:
        ValueError: data_length exceeds the largest variant.
    """
    for lol, info in LENGTH_OF_LENGTH_TABLE.items():
        if data_length <= info.max_length:
            return lol
    raise ValueError(f"Data field of {data_length} bytes is too large for an encapsulation packet.")


def header_length(length_of_length: int) -> int:
    """
    Args:
        length_of_length (int): 2-bit length-of-length code.

    Returns:
        int: header length in bytes (1, 2, 4, or 8).
    """
    return LENGTH_OF_LENGTH_TABLE[length_of_length & 0b11].header_length


def build_header(fields) -> bytes:
    """
    Args:
        fields (dict): `length_of_length` (required) plus, as the variant
            needs them, `packet_length`, `user_defined_field`,
            `protocol_id_extension`, `ccsds_defined_field`. The first octet
            comes from `first_octet` if given, otherwise from
            `packet_version`/`protocol_id` (defaults: version 0b111, data).

    Returns:
        bytes: the header.

    Raises:
        KeyError: length_of_length is missing, or packet_length is missing
            for a variant that carries it.
    """
    lol = int(fields["length_of_length"]) & 0b11
    first_octet = fields.get("first_octet")
    if first_octet is None:
        version = int(fields.get("packet_version", PACKET_VERSION_NUMBER))
        protocol_id = int(fields.get("protocol_id", PROTOCOL_ID_DATA))
        first_octet = ((version & 0b111) << 5) | ((protocol_id & 0b111) << 2) | lol
    header = bytearray([int(first_octet) & 0xFF])

    if lol >= 0b10:
        user_defined = int(fields.get("user_defined_field") or 0) & 0b1111
        extension = int(fields.get("protocol_id_extension") or 0) & 0b1111
        header.append((user_defined << 4) | extension)
    if lol >= 0b11:
        header += int(fields.get("ccsds_defined_field") or 0).to_bytes(2, "big")
    width = LENGTH_OF_LENGTH_TABLE[lol].packet_length_bytes
    if width:
        header += int(fields["packet_length"]).to_bytes(width, "big")
    return bytes(header)


def header_for_payload(data_length: int, user_defined_field: int = 0) -> bytes:
    """
    Args:
        data_length (int): length of the data field the header will precede.
        user_defined_field (int): 4-bit value, used by the 4- and 8-byte
            variants only.

    Returns:
        bytes: the header for an encapsulation packet of that size - an
            idle packet header if data_length is 0.

    Raises:
        ValueError: data_length is too large (see length_of_length_for).
    """
    lol = length_of_length_for(data_length)
    return build_header({
        "length_of_length": lol,
        "packet_version": PACKET_VERSION_NUMBER,
        "protocol_id": PROTOCOL_ID_IDLE if data_length == 0 else PROTOCOL_ID_DATA,
        "user_defined_field": user_defined_field,
        "protocol_id_extension": PROTOCOL_ID_EXTENSION,
        "ccsds_defined_field": CCSDS_DEFINED_FIELD,
        "packet_length": header_length(lol) + data_length if lol != 0b00 else None,
    })


def parse_header(data: bytes) -> dict:
    """
    Args:
        data (bytes): bytes starting with an encapsulation packet header.

    Returns:
        dict: first_octet, packet_version, protocol_id, length_of_length,
            user_defined_field, protocol_id_extension, ccsds_defined_field,
            packet_length (None where the variant has no such field), and
            header_length.

    Raises:
        ValueError: data is shorter than the header its first octet announces.
    """
    if not data:
        raise ValueError("No data to parse an encapsulation header from.")
    length = header_length(data[0] & 0b11)
    if len(data) < length:
        raise ValueError(f"Encapsulation header needs {length} bytes, got {len(data)}.")
    parsed = HEADER_STRUCT.parse(bytes(data[:length]))
    fields = {key: parsed[key] for key in (
        "first_octet", "packet_version", "protocol_id", "length_of_length", "user_defined_field",
        "protocol_id_extension", "ccsds_defined_field", "packet_length")}
    fields["header_length"] = length
    return fields
