"""A small QR Code encoder (byte mode, versions 1-10, error correction L/M).

Used to show links on the 240x280 LCD (the Claude sign-in link, the desktop
IDE pairing link) so a phone can open them. Standard library only; the
algorithm follows ISO/IEC 18004 as explained by Project Nayuki's reference
implementation. Tests decode the output with OpenCV when it is installed.
"""

from __future__ import annotations

from typing import List

# version: {level: (ecc codewords per block, [(blocks, data codewords per block), ...])}
_BLOCKS = {
    1: {"L": (7, [(1, 19)]), "M": (10, [(1, 16)])},
    2: {"L": (10, [(1, 34)]), "M": (16, [(1, 28)])},
    3: {"L": (15, [(1, 55)]), "M": (26, [(1, 44)])},
    4: {"L": (20, [(1, 80)]), "M": (18, [(2, 32)])},
    5: {"L": (26, [(1, 108)]), "M": (24, [(2, 43)])},
    6: {"L": (18, [(2, 68)]), "M": (16, [(4, 27)])},
    7: {"L": (20, [(2, 78)]), "M": (18, [(4, 31)])},
    8: {"L": (24, [(2, 97)]), "M": (22, [(2, 38), (2, 39)])},
    9: {"L": (30, [(2, 116)]), "M": (22, [(3, 36), (2, 37)])},
    10: {"L": (18, [(2, 68), (2, 69)]), "M": (26, [(4, 43), (1, 44)])},
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38],
          8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
_FORMAT_BITS = {"L": 1, "M": 0}


def _gf_mul(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> List[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(len(result)):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < len(result):
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data: List[int], divisor: List[int]) -> List[int]:
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _capacity(version: int, level: str) -> int:
    return sum(n * size for n, size in _BLOCKS[version][level][1])


class QrCode:
    def __init__(self, data: bytes, level: str = "M"):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.version = 0
        for version in range(1, 11):
            count_bits = 8 if version < 10 else 16
            if 4 + count_bits + 8 * len(data) <= _capacity(version, level) * 8:
                self.version = version
                break
        if not self.version:
            if level != "L":
                self.__init__(data, "L")
                return
            raise ValueError("text too long for a small QR code")
        self.level = level
        self.size = self.version * 4 + 17
        size = self.size
        self.modules = [[False] * size for _ in range(size)]
        self._function = [[False] * size for _ in range(size)]
        self._draw_function_patterns()
        codewords = self._add_ecc(self._encode(data))
        self._draw_codewords(codewords)
        best, best_penalty = 0, None
        for mask in range(8):
            self._apply_mask(mask)
            self._draw_format(mask)
            penalty = self._penalty()
            if best_penalty is None or penalty < best_penalty:
                best, best_penalty = mask, penalty
            self._apply_mask(mask)          # undo (XOR)
        self._apply_mask(best)
        self._draw_format(best)
        self.mask = best

    # ------------------------------------------------------------ patterns
    def _set(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self._function[y][x] = True

    def _draw_function_patterns(self) -> None:
        size = self.size
        for i in range(size):
            self._set(6, i, i % 2 == 0)
            self._set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self._set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = _ALIGN[self.version]
        last = len(positions) - 1
        for i, ax in enumerate(positions):
            for j, ay in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self._set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self._draw_format(0)
        if self.version >= 7:
            rem = self.version
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.version << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = size - 11 + i % 3, i // 3
                self._set(a, b, bit)
                self._set(b, a, bit)

    def _draw_format(self, mask: int) -> None:
        size = self.size
        data = _FORMAT_BITS[self.level] << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412

        def bit(i: int) -> bool:
            return (bits >> i) & 1 == 1

        for i in range(6):
            self._set(8, i, bit(i))
        self._set(8, 7, bit(6))
        self._set(8, 8, bit(7))
        self._set(7, 8, bit(8))
        for i in range(9, 15):
            self._set(14 - i, 8, bit(i))
        for i in range(8):
            self._set(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self._set(8, size - 15 + i, bit(i))
        self._set(8, size - 8, True)

    # --------------------------------------------------------------- data
    def _encode(self, data: bytes) -> List[int]:
        bits: List[int] = []

        def put(value: int, length: int) -> None:
            bits.extend((value >> i) & 1 for i in reversed(range(length)))

        put(0b0100, 4)
        put(len(data), 8 if self.version < 10 else 16)
        for byte in data:
            put(byte, 8)
        capacity = _capacity(self.version, self.level) * 8
        put(0, min(4, capacity - len(bits)))
        put(0, (-len(bits)) % 8)
        out = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
        pad = 0xEC
        while len(out) < capacity // 8:
            out.append(pad)
            pad ^= 0xEC ^ 0x11
        return out

    def _add_ecc(self, data: List[int]) -> List[int]:
        ecc_len, groups = _BLOCKS[self.version][self.level]
        divisor = _rs_divisor(ecc_len)
        blocks, eccs = [], []
        index = 0
        for count, length in groups:
            for _ in range(count):
                block = data[index:index + length]
                index += length
                blocks.append(block)
                eccs.append(_rs_remainder(block, divisor))
        out = []
        for i in range(max(len(b) for b in blocks)):
            for block in blocks:
                if i < len(block):
                    out.append(block[i])
        for i in range(ecc_len):
            for ecc in eccs:
                out.append(ecc[i])
        return out

    def _draw_codewords(self, data: List[int]) -> None:
        size = self.size
        i = 0
        total = len(data) * 8
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self._function[y][x] and i < total:
                        self.modules[y][x] = (data[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def _apply_mask(self, mask: int) -> None:
        tests = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0,
                 lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0,
                 lambda x, y: (x // 3 + y // 2) % 2 == 0,
                 lambda x, y: x * y % 2 + x * y % 3 == 0,
                 lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
                 lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0]
        test = tests[mask]
        for y in range(self.size):
            row = self.modules[y]
            function = self._function[y]
            for x in range(self.size):
                if not function[x] and test(x, y):
                    row[x] = not row[x]

    def _penalty(self) -> int:
        size = self.size
        m = self.modules
        score = 0
        lines = [m[y] for y in range(size)] + [[m[y][x] for y in range(size)] for x in range(size)]
        finder_a = [True, False, True, True, True, False, True, False, False, False, False]
        finder_b = finder_a[::-1]
        for line in lines:
            run, color = 0, None
            for value in line:
                if value == color:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run, color = 1, value
            if run >= 5:
                score += 3 + run - 5
            for start in range(size - 10):
                window = line[start:start + 11]
                if window == finder_a or window == finder_b:
                    score += 40
        for y in range(size - 1):
            for x in range(size - 1):
                c = m[y][x]
                if c == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                    score += 3
        dark = sum(sum(1 for v in row if v) for row in m)
        total = size * size
        k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
        return score + max(0, k) * 10


def matrix(text: str, level: str = "M") -> List[List[bool]]:
    return QrCode(text.encode("utf-8"), level).modules
