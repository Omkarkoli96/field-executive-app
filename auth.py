"""
Executive PIN hashing.
Uses PBKDF2-HMAC-SHA256 (stdlib hashlib, no extra dependency) with a
random per-executive salt, so PINs are never stored in plain text and two
executives with the same PIN don't produce the same stored hash.
"""

import hashlib
import os
import secrets


def hash_pin(pin: str) -> tuple[str, str]:
    """Returns (salt_hex, hash_hex) for a new PIN."""
    salt = secrets.token_hex(16)
    pin_hash = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 100_000).hex()
    return salt, pin_hash


def verify_pin(pin: str, salt_hex: str, expected_hash_hex: str) -> bool:
    candidate = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt_hex), 100_000).hex()
    return secrets.compare_digest(candidate, expected_hash_hex)
