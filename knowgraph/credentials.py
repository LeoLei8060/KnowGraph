"""Local credential storage for model API keys.

Priority is the environment, then an optional OS keyring, then a restricted
file under the private data directory. Keys never enter the vault or SQLite.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

ANTHROPIC_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_AUTH_ENV = "ANTHROPIC_AUTH_TOKEN"
SERVICE = "knowgraph"
ACCOUNT = "anthropic"


class CredentialStore:
    def __init__(self, home: Path, *, disable_keyring: bool | None = None) -> None:
        self.home = Path(home)
        self.file = self.home / "secrets" / "anthropic.key"
        self.provider_dir = self.home / "secrets" / "providers"
        if disable_keyring is None:
            disable_keyring = os.getenv("KNOWGRAPH_DISABLE_KEYRING") == "1"
        self._keyring = None
        if not disable_keyring:
            try:
                import keyring  # type: ignore

                self._keyring = keyring
            except Exception:
                self._keyring = None

    def _file_for(self, scope: str | None) -> Path:
        if scope:
            return self.provider_dir / f"{scope}.key"
        return self.file

    def _account_for(self, scope: str | None) -> str:
        return f"provider:{scope}" if scope else ACCOUNT

    def stored(self, scope: str | None = None) -> str | None:
        if self._keyring is not None:
            try:
                value = self._keyring.get_password(SERVICE, self._account_for(scope))
                if value:
                    return value.strip()
            except Exception:
                pass
        path = self._file_for(scope)
        if path.exists():
            try:
                value = path.read_text(encoding="utf-8").strip()
                return value or None
            except OSError:
                return None
        return None

    def get(self, scope: str | None = None, protocol: str = "anthropic") -> str | None:
        if protocol == "openai":
            env = os.getenv("OPENAI_API_KEY")
        else:
            env = os.getenv(ANTHROPIC_ENV) or os.getenv(ANTHROPIC_AUTH_ENV)
        return env or self.stored(scope)

    def source(self, scope: str | None = None, protocol: str = "anthropic") -> str:
        if protocol == "openai":
            if os.getenv("OPENAI_API_KEY"):
                return "env"
        elif os.getenv(ANTHROPIC_ENV) or os.getenv(ANTHROPIC_AUTH_ENV):
            return "env"
        if self.stored(scope):
            return "store"
        if protocol == "anthropic":
            profile = [
                Path.home() / ".config" / "anthropic" / "credentials.json",
                Path.home() / ".anthropic" / "credentials.json",
            ]
            if any(path.exists() for path in profile):
                return "profile"
        return "none"

    def hint(self, scope: str | None = None, protocol: str = "anthropic") -> str | None:
        value = self.get(scope, protocol)
        if not value:
            return None
        return "…" + value[-4:]

    def set(self, api_key: str, scope: str | None = None) -> None:
        key = (api_key or "").strip()
        if len(key) < 8 or any(ch.isspace() for ch in key):
            raise ValueError("API key format is invalid")
        path = self._file_for(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".key.",
            suffix=".tmp",
            delete=False,
        )
        temp_path = Path(handle.name)
        try:
            handle.write(key)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            try:
                os.chmod(temp_path, 0o600)
            except OSError:
                pass
            os.replace(temp_path, path)
        except Exception:
            handle.close()
            temp_path.unlink(missing_ok=True)
            raise
        if self._keyring is not None:
            try:
                self._keyring.set_password(SERVICE, self._account_for(scope), key)
            except Exception:
                pass

    def delete(self, scope: str | None = None) -> None:
        stored = self.stored(scope)
        if self._keyring is not None:
            try:
                self._keyring.delete_password(SERVICE, self._account_for(scope))
            except Exception:
                pass
        self._file_for(scope).unlink(missing_ok=True)
        if scope is None and stored and os.getenv(ANTHROPIC_ENV) == stored:
            os.environ.pop(ANTHROPIC_ENV, None)

    def migrate_legacy(self, scope: str) -> str | None:
        """Move the pre-provider single key into a provider scope, if present."""
        if self.stored(scope):
            return self.stored(scope)
        legacy = self.stored(None)
        if legacy:
            self.set(legacy, scope=scope)
            return legacy
        return None
