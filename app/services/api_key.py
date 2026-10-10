"""API key generation, hashing, and verification services."""

import hashlib
import hmac
import secrets
from typing import Tuple


API_KEY_PREFIX = "pb_live_"


def generate_api_key() -> Tuple[str, str, str]:
    """Generate a new secure API key.
    
    Returns:
        Tuple of (plaintext_key, key_hash, key_prefix)
    """
    random_part = secrets.token_urlsafe(32)
    plaintext_key = f"{API_KEY_PREFIX}{random_part}"
    key_hash = hash_api_key(plaintext_key)
    # Store recognizable prefix (e.g., pb_live_abc123) for UI display
    key_prefix = plaintext_key[:14]
    return plaintext_key, key_hash, key_prefix


def hash_api_key(plaintext_key: str) -> str:
    """Hash an API key using SHA-256 for deterministic O(1) indexed lookup."""
    return hashlib.sha256(plaintext_key.encode("utf-8")).hexdigest()


def verify_api_key(plaintext_key: str, stored_hash: str) -> bool:
    """Verify an API key against its stored SHA-256 hash in constant time."""
    computed_hash = hash_api_key(plaintext_key)
    return hmac.compare_digest(computed_hash, stored_hash)
