"""Pure-Python Keccak-256 (the pre-NIST variant Ethereum uses).

`hashlib.sha3_256` is NIST SHA-3 with different padding, so it cannot be used
for Ethereum function selectors. Inputs here are tiny (signatures, call data),
so a pure-Python permutation is fast enough and keeps reads dependency-free.
"""

from __future__ import annotations

_MASK = (1 << 64) - 1
_RATE = 136  # bytes, for 256-bit output

_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

# Rho rotation offsets, flat-indexed as x + 5*y.
_ROTATIONS = (
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
)


def _rol(value: int, shift: int) -> int:
    if shift == 0:
        return value
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _keccak_f(state: list) -> list:
    for rc in _ROUND_CONSTANTS:
        # theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        state = [state[i] ^ d[i % 5] for i in range(25)]
        # rho + pi
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rol(state[x + 5 * y], _ROTATIONS[x + 5 * y])
        # chi
        state = [
            b[i] ^ ((~b[(i % 5 + 1) % 5 + 5 * (i // 5)] & _MASK) & b[(i % 5 + 2) % 5 + 5 * (i // 5)])
            for i in range(25)
        ]
        # iota
        state[0] ^= rc
    return state


def keccak256(data: bytes) -> bytes:
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % _RATE != 0:
        padded.append(0x00)
    padded[-1] |= 0x80

    state = [0] * 25
    for block_start in range(0, len(padded), _RATE):
        block = padded[block_start:block_start + _RATE]
        for lane in range(_RATE // 8):
            state[lane] ^= int.from_bytes(block[lane * 8:lane * 8 + 8], "little")
        state = _keccak_f(state)

    return b"".join(state[i].to_bytes(8, "little") for i in range(4))


def function_selector(signature: str) -> bytes:
    """First 4 bytes of keccak256 of a canonical function signature."""
    return keccak256(signature.encode("ascii"))[:4]
