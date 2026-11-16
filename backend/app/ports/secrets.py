from typing import Protocol


class SecretError(Exception):
    """Cannot encrypt/decrypt (missing or wrong key, tampered value)."""


class SecretCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, token: str) -> str: ...
