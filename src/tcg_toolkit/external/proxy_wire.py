"""Version 1: nonce || AES-GCM ciphertext || tag, with directional keys."""
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class Wire:
    def __init__(self, path):
        master = Path(path).read_bytes()
        if len(master) != 32:
            raise ValueError('Master key must contain exactly 32 raw bytes')
        self.keys = {
            direction: AESGCM(HKDF(algorithm=hashes.SHA256(), length=32,
                salt=b'lite-proxy-v1', info=direction.encode()).derive(master))
            for direction in ('request', 'response')
        }

    def seal(self, direction, payload):
        nonce = os.urandom(12)
        data = json.dumps(payload, separators=(',', ':'), allow_nan=False).encode()
        return nonce + self.keys[direction].encrypt(nonce, data, self.aad(direction))

    def open(self, direction, packet):
        if len(packet) < 28:
            raise ValueError('Truncated encrypted packet')
        return json.loads(self.keys[direction].decrypt(
            packet[:12], packet[12:], self.aad(direction)))

    @staticmethod
    def aad(direction):
        return f'lite-proxy-v1|POST|/encrypted-proxy|{direction}'.encode()
