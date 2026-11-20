"""Kleiner QR-Code-Erzeuger (Byte-Modus, Fehlerkorrektur M, Version 1–10).

Reicht für eine Adresse wie http://192.168.178.20:47800/remote?k=… und braucht
keine externe Bibliothek. encode(text) -> Liste von Zeilen mit True = schwarz.
"""

from __future__ import annotations

# (Datenkapazität in Codewörtern, EC-Codewörter pro Block, Blöcke Gruppe 1, Daten/Block G1, Blöcke G2, Daten/Block G2)
_EC_M = {
    1: (16, 10, 1, 16, 0, 0), 2: (28, 16, 1, 28, 0, 0), 3: (44, 26, 1, 44, 0, 0),
    4: (64, 18, 2, 32, 0, 0), 5: (86, 24, 2, 43, 0, 0), 6: (108, 16, 4, 27, 0, 0),
    7: (124, 18, 4, 31, 0, 0), 8: (154, 22, 2, 38, 2, 39), 9: (182, 22, 3, 36, 2, 37),
    10: (216, 26, 4, 43, 1, 44),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
          8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}

# Galois-Feld GF(256)
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> list[int]:
    g = [1]
    for i in range(n):
        g = [a ^ b for a, b in zip(g + [0], [0] + [_gmul(c, _EXP[i]) for c in g])]
    return g


def _rs_ec(data: list[int], n: int) -> list[int]:
    gen = _rs_generator(n)
    res = list(data) + [0] * n
    for i in range(len(data)):
        c = res[i]
        if c:
            for j in range(1, len(gen)):
                res[i + j] ^= _gmul(gen[j], c)
    return res[len(data):]


def _bits(value: int, length: int) -> list[int]:
    return [(value >> (length - 1 - i)) & 1 for i in range(length)]


def _codewords(text: str) -> tuple[int, list[int]]:
    data = text.encode("utf-8")
    for version in range(1, 11):
        cap = _EC_M[version][0]
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + 8 * len(data) <= cap * 8:
            break
    else:
        raise ValueError("Text zu lang für den QR-Code")
    cap, ecn, b1, d1, b2, d2 = _EC_M[version]
    bits = [0, 1, 0, 0] + _bits(len(data), count_bits)
    for byte in data:
        bits += _bits(byte, 8)
    bits += [0] * min(4, cap * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = [0xEC, 0x11]
    i = 0
    while len(words) < cap:
        words.append(pad[i % 2])
        i += 1
    blocks = []
    pos = 0
    for count, size in ((b1, d1), (b2, d2)):
        for _ in range(count):
            blocks.append(words[pos:pos + size])
            pos += size
    ecs = [_rs_ec(b, ecn) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ecn):
        out += [e[i] for e in ecs]
    return version, out


def _format_int(mask: int) -> int:
    data = (0b00 << 3) | mask  # Fehlerkorrektur M = 00
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return ((data << 10) | rem) ^ 0x5412


def _version_int(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return (version << 12) | rem


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _build(version: int, words: list[int], mask: int):
    n = 17 + 4 * version
    m = [[None] * n for _ in range(n)]

    def finder(r, c):
        for i in range(-1, 8):
            for j in range(-1, 8):
                rr, cc = r + i, c + j
                if 0 <= rr < n and 0 <= cc < n:
                    m[rr][cc] = (0 <= i <= 6 and j in (0, 6)) or (0 <= j <= 6 and i in (0, 6)) or \
                                (2 <= i <= 4 and 2 <= j <= 4)

    finder(0, 0)
    finder(0, n - 7)
    finder(n - 7, 0)
    for i in range(8, n - 8):
        m[6][i] = m[i][6] = i % 2 == 0
    pos = _ALIGN[version]
    last = len(pos) - 1
    for a, r in enumerate(pos):
        for b, c in enumerate(pos):
            if (a, b) in ((0, 0), (0, last), (last, 0)):
                continue
            for i in range(-2, 3):
                for j in range(-2, 3):
                    m[r + i][c + j] = max(abs(i), abs(j)) != 1

    def setm(x, y, v):  # x = Spalte, y = Zeile
        m[y][x] = bool(v)

    fmt = _format_int(mask)
    bit = lambda v, i: (v >> i) & 1  # noqa: E731
    for i in range(6):
        setm(8, i, bit(fmt, i))
    setm(8, 7, bit(fmt, 6))
    setm(8, 8, bit(fmt, 7))
    setm(7, 8, bit(fmt, 8))
    for i in range(9, 15):
        setm(14 - i, 8, bit(fmt, i))
    for i in range(8):
        setm(n - 1 - i, 8, bit(fmt, i))
    for i in range(8, 15):
        setm(8, n - 15 + i, bit(fmt, i))
    setm(8, n - 8, 1)  # dunkles Modul
    if version >= 7:
        vb = _version_int(version)
        for i in range(18):
            a, b = n - 11 + i % 3, i // 3
            setm(a, b, bit(vb, i))
            setm(b, a, bit(vb, i))

    bits = []
    for w in words:
        bits += _bits(w, 8)
    idx = 0
    up = True
    c = n - 1
    while c > 0:
        if c == 6:
            c -= 1
        rows = range(n - 1, -1, -1) if up else range(n)
        for r in rows:
            for cc in (c, c - 1):
                if m[r][cc] is None:
                    bit = bits[idx] if idx < len(bits) else 0
                    idx += 1
                    m[r][cc] = bool(bit) ^ _MASKS[mask](r, cc)
        up = not up
        c -= 2
    return m


def _penalty(m) -> int:
    n = len(m)
    score = 0
    for grid in (m, list(zip(*m))):
        for row in grid:
            run = 1
            for i in range(1, n):
                if row[i] == row[i - 1]:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run = 1
            if run >= 5:
                score += run - 2
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r + 1][c] == m[r][c + 1] == m[r + 1][c + 1]:
                score += 3
    dark = sum(sum(1 for v in row if v) for row in m)
    score += abs(dark * 20 // (n * n) - 10) * 10
    return score


def encode(text: str) -> list[list[bool]]:
    version, words = _codewords(text)
    best = min((_build(version, words, mk) for mk in range(8)), key=_penalty)
    return [[bool(v) for v in row] for row in best]


def to_svg(matrix: list[list[bool]], scale: int = 8, border: int = 4) -> str:
    n = len(matrix)
    size = (n + 2 * border) * scale
    rects = "".join(
        f'<rect x="{(c + border) * scale}" y="{(r + border) * scale}" width="{scale}" height="{scale}"/>'
        for r, row in enumerate(matrix) for c, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}">'
            f'<rect width="100%" height="100%" fill="#fff"/><g fill="#000">{rects}</g></svg>')
