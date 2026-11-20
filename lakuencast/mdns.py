"""Chromecasts im Heimnetz finden (mDNS / DNS-SD, ohne externe Bibliotheken).

Wir schicken eine Anfrage nach "_googlecast._tcp.local" an die mDNS-Gruppe
224.0.0.251:5353 und sammeln die Antworten (PTR, SRV, TXT, A).
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass, field

SERVICE = "_googlecast._tcp.local"
MDNS_ADDR = ("224.0.0.251", 5353)

T_A, T_PTR, T_TXT, T_SRV = 1, 12, 16, 33


@dataclass
class Device:
    id: str
    name: str
    host: str
    port: int = 8009
    model: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "host": self.host, "port": self.port, "model": self.model}


def build_query(name: str = SERVICE, qtype: int = T_PTR) -> bytes:
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    q = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    return header + q + struct.pack(">HH", qtype, 1)


def _read_name(buf: bytes, pos: int) -> tuple[str, int]:
    labels = []
    jumped = False
    end = pos
    for _ in range(128):
        length = buf[pos]
        if length == 0:
            pos += 1
            break
        if length & 0xC0 == 0xC0:
            ptr = ((length & 0x3F) << 8) | buf[pos + 1]
            if not jumped:
                end = pos + 2
            jumped = True
            pos = ptr
            continue
        pos += 1
        labels.append(buf[pos:pos + length].decode("utf-8", "replace"))
        pos += length
    if not jumped:
        end = pos
    return ".".join(labels), end


def parse_response(buf: bytes) -> list[tuple[str, int, object]]:
    """Gibt eine Liste (name, typ, daten) aller Antwort-Einträge zurück."""
    _, _, qd, an, ns, ar = struct.unpack(">HHHHHH", buf[:12])
    pos = 12
    for _ in range(qd):
        _, pos = _read_name(buf, pos)
        pos += 4
    records = []
    for _ in range(an + ns + ar):
        name, pos = _read_name(buf, pos)
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", buf[pos:pos + 10])
        pos += 10
        rdata_start = pos
        data: object = None
        if rtype == T_PTR:
            data, _ = _read_name(buf, pos)
        elif rtype == T_SRV:
            _prio, _weight, port = struct.unpack(">HHH", buf[pos:pos + 6])
            target, _ = _read_name(buf, pos + 6)
            data = (target, port)
        elif rtype == T_TXT:
            txt = {}
            p = pos
            while p < rdata_start + rdlen:
                ln = buf[p]
                entry = buf[p + 1:p + 1 + ln].decode("utf-8", "replace")
                p += 1 + ln
                if "=" in entry:
                    k, v = entry.split("=", 1)
                    txt[k] = v
            data = txt
        elif rtype == T_A and rdlen == 4:
            data = socket.inet_ntoa(buf[pos:pos + 4])
        records.append((name, rtype, data))
        pos = rdata_start + rdlen
    return records


def devices_from_records(records: list[tuple[str, int, object]], sender_ip: str = "") -> list[Device]:
    ptrs = [d for n, t, d in records if t == T_PTR and n.lower() == SERVICE]
    srv = {n: d for n, t, d in records if t == T_SRV}
    txt = {n: d for n, t, d in records if t == T_TXT}
    a = {n: d for n, t, d in records if t == T_A}
    found = []
    for inst in ptrs:
        target, port = srv.get(inst, ("", 8009))  # type: ignore[misc]
        host = a.get(target) or sender_ip
        info = txt.get(inst, {}) or {}
        if not host:
            continue
        found.append(Device(
            id=info.get("id", inst),
            name=info.get("fn") or inst.split(".")[0],
            host=host,  # type: ignore[arg-type]
            port=port,
            model=info.get("md", ""),
            extra=info,
        ))
    return found


def discover(timeout: float = 3.0) -> list[Device]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
    sock.bind(("", 0))
    sock.settimeout(0.3)
    query = build_query()
    devices: dict[str, Device] = {}
    end = time.time() + timeout
    next_send = 0.0
    try:
        while time.time() < end:
            if time.time() >= next_send:
                try:
                    sock.sendto(query, MDNS_ADDR)
                except OSError:
                    pass
                next_send = time.time() + 1.0
            try:
                data, addr = sock.recvfrom(9000)
            except socket.timeout:
                continue
            try:
                for d in devices_from_records(parse_response(data), addr[0]):
                    devices[d.id] = d
            except (IndexError, struct.error, UnicodeDecodeError):
                continue
    finally:
        sock.close()
    return sorted(devices.values(), key=lambda d: d.name.lower())
