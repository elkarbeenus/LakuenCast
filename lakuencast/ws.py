"""Minimaler WebSocket-Server (RFC 6455) für die flüssige Handy-Steuerung.

Nur das Nötige: Handshake, Textnachrichten, Ping/Pong, Schließen.
"""

from __future__ import annotations

import base64
import hashlib
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def accept_key(client_key: str) -> str:
    return base64.b64encode(hashlib.sha1((client_key + GUID).encode()).digest()).decode()


def encode_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """Server→Client (unmaskiert)."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


def read_frame(rfile) -> tuple[int, bytes] | None:
    """Liest einen Frame vom Client. None = Verbindung zu."""
    head = rfile.read(2)
    if len(head) < 2:
        return None
    opcode = head[0] & 0x0F
    masked = head[1] & 0x80
    n = head[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", rfile.read(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", rfile.read(8))[0]
    if n > 1 << 20:
        return None
    mask = rfile.read(4) if masked else b"\0\0\0\0"
    data = bytearray(rfile.read(n))
    if len(data) < n:
        return None
    for i in range(n):
        data[i] ^= mask[i % 4]
    return opcode, bytes(data)


def mask_frame(payload: bytes, opcode: int = 0x1, mask: bytes = b"\x11\x22\x33\x44") -> bytes:
    """Client→Server (maskiert) – nur für Tests."""
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    else:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
