#!/usr/bin/env python3
"""Encode wallet-backend key bytes (version byte + payload, hex) as a Stellar strkey.

wallet-backend stores keys as <strkey version byte><raw payload>, so the strkey is
base32(version || payload || crc16_xmodem_le). Independent of wallet-backend's own code.
Usage: strkey.py <hex>
"""
import base64
import sys


def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


def encode(raw: bytes) -> str:
    crc = crc16_xmodem(raw)
    return base64.b32encode(raw + crc.to_bytes(2, "little")).decode().rstrip("=")


if __name__ == "__main__":
    print(encode(bytes.fromhex(sys.argv[1])))
