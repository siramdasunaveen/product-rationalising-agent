"""Fernet encryption for secrets at rest (connector credentials, API keys)."""

from cryptography.fernet import Fernet, InvalidToken

from app.ports.secrets import SecretError


class FernetCipher:
    def __init__(self, key: str) -> None:
        self._fernet: Fernet | None = None
        if key:
            try:
                self._fernet = Fernet(key.encode())
            except (ValueError, TypeError) as exc:
                raise SecretError("PRA_FERNET_KEY is not a valid Fernet key") from exc

    def _require(self) -> Fernet:
        if self._fernet is None:
            raise SecretError("PRA_FERNET_KEY is not set")
        return self._fernet

    def encrypt(self, plaintext: str) -> str:
        return self._require().encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._require().decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise SecretError("Cannot decrypt secret (wrong key or tampered value)") from exc
