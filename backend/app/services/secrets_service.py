"""
SecretsService — AES-256-GCM envelope encryption for connector credentials.

Security model
--------------
Connector credentials (API keys, OAuth tokens, passwords) are encrypted
at rest in ``ConnectorSecret.ciphertext`` using AES-256-GCM.

Key material
~~~~~~~~~~~~
A 32-byte Master Key (KEK — Key Encryption Key) is read from
``settings.connector_secret_key``, which must be a Base64-encoded 32-byte
value set via the ``CONNECTOR_SECRET_KEY`` environment variable.

Generate a suitable key with::

    python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"

Never commit the raw key to version control.  In production, inject it via
a secrets manager (Vault, AWS Secrets Manager, GCP Secret Manager, etc.).

Nonce / IV
~~~~~~~~~~
A fresh 12-byte (96-bit) nonce is generated via ``os.urandom(12)`` for
every ``encrypt`` call.  NIST SP 800-38D specifies 96-bit as the canonical
AES-GCM IV length: it is used directly as the initial counter block without
an extra GHASH derivation step, preserving the standard security bounds.
With 32-byte random nonces and ~2³² operations per key, nonce-collision
probability stays below the 2⁻³² safety threshold.

The nonce is stored alongside the ciphertext in ``ConnectorSecret.nonce``
as a plain ``bytes`` column — it is NOT secret and does NOT need to be
encrypted.

Authentication tag
~~~~~~~~~~~~~~~~~~
AES-GCM appends a 128-bit authentication tag to the ciphertext.
``AESGCM.decrypt`` raises ``cryptography.exceptions.InvalidTag`` if the
ciphertext or nonce has been tampered with — this exception propagates up
unchanged so callers can treat it as a hard auth failure.

What is NEVER done here
~~~~~~~~~~~~~~~~~~~~~~~
- Credentials are never logged (not even at DEBUG level).
- Plaintext dicts are never stored or cached beyond the caller's scope.
- The master key is never serialised back to a string after initial decode.
"""
from __future__ import annotations

import base64
import json
import logging
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

logger = logging.getLogger(__name__)

# AES-GCM nonce size mandated by NIST SP 800-38D §8.2
_NONCE_BYTES = 12

# AES-256 requires exactly 32 bytes
_KEY_BYTES = 32


class SecretsServiceError(Exception):
    """Raised for configuration or cryptographic errors in SecretsService."""


class SecretsService:
    """
    Stateless AES-256-GCM encryption/decryption service for connector secrets.

    The service is intentionally stateless beyond the master key: it holds no
    per-connector state, no caches, and no open file handles.  A single
    process-level instance is sufficient (see module-level ``secrets_service``
    below), but the class is also safe to instantiate per-request.

    Parameters
    ----------
    master_key_b64:
        Base64-encoded 32-byte key.  Defaults to ``settings.connector_secret_key``.
        Pass an explicit value only in tests.

    Raises
    ------
    SecretsServiceError
        If ``CONNECTOR_SECRET_KEY`` is missing or decodes to a value that is
        not exactly 32 bytes.  Raised at construction time so the application
        fails fast on misconfiguration rather than silently at first use.
    """

    __slots__ = ("_master_key",)

    def __init__(self, master_key_b64: str | None = None) -> None:
        raw_b64 = master_key_b64 or settings.connector_secret_key
        if not raw_b64:
            raise SecretsServiceError(
                "CONNECTOR_SECRET_KEY is not set. "
                "Generate one with: "
                "python -c \"import secrets,base64; "
                "print(base64.b64encode(secrets.token_bytes(32)).decode())\""
            )

        try:
            key_bytes: bytes = base64.b64decode(raw_b64)
        except Exception as exc:
            raise SecretsServiceError(
                "CONNECTOR_SECRET_KEY is not valid Base64."
            ) from exc

        if len(key_bytes) != _KEY_BYTES:
            raise SecretsServiceError(
                f"CONNECTOR_SECRET_KEY must decode to exactly {_KEY_BYTES} bytes "
                f"(AES-256); got {len(key_bytes)} bytes."
            )

        # Store as raw bytes — never re-serialise to a string
        self._master_key: bytes = key_bytes
        logger.debug("SecretsService initialised (key_bytes=%d).", _KEY_BYTES)

    # ------------------------------------------------------------------
    # Encryption
    # ------------------------------------------------------------------

    def encrypt(self, payload: dict) -> tuple[bytes, bytes]:
        """
        Serialise *payload* to JSON and encrypt with AES-256-GCM.

        A unique 12-byte nonce is generated per call via ``os.urandom``.

        Parameters
        ----------
        payload:
            Arbitrary dict of connector credentials (e.g.
            ``{"api_key": "...", "refresh_token": "..."}``)

        Returns
        -------
        tuple[bytes, bytes]
            ``(ciphertext, nonce)`` — both values must be persisted in
            ``ConnectorSecret``.  The ciphertext includes the 16-byte
            AES-GCM authentication tag appended by the ``cryptography``
            library.

        Notes
        -----
        - ``payload`` is never logged.
        - The plaintext bytes are not retained after this method returns.
        """
        plaintext: bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        nonce: bytes = os.urandom(_NONCE_BYTES)
        aesgcm = AESGCM(self._master_key)
        ciphertext: bytes = aesgcm.encrypt(nonce, plaintext, associated_data=None)
        logger.debug("SecretsService.encrypt: encrypted %d-byte payload.", len(plaintext))
        # Explicitly overwrite local reference (belt-and-suspenders; CPython
        # may not GC immediately but this signals intent clearly)
        del plaintext
        return ciphertext, nonce

    # ------------------------------------------------------------------
    # Decryption
    # ------------------------------------------------------------------

    def decrypt(self, ciphertext: bytes, nonce: bytes) -> dict:
        """
        Decrypt *ciphertext* and return the original credentials dict.

        Parameters
        ----------
        ciphertext:
            Raw bytes as stored in ``ConnectorSecret.ciphertext``.
            Includes the appended 128-bit GCM authentication tag.
        nonce:
            12-byte nonce as stored in ``ConnectorSecret.nonce``.

        Returns
        -------
        dict
            Decrypted credentials.  **The caller owns this dict and is
            responsible for not logging or persisting its contents.**

        Raises
        ------
        cryptography.exceptions.InvalidTag
            If the ciphertext or nonce has been tampered with, or if the
            wrong key is used.  Propagated unchanged — callers must treat
            this as a hard authentication failure.
        SecretsServiceError
            If the decrypted bytes cannot be decoded as UTF-8 JSON.  This
            indicates data corruption rather than a key mismatch.
        """
        aesgcm = AESGCM(self._master_key)

        try:
            plaintext: bytes = aesgcm.decrypt(nonce, ciphertext, associated_data=None)
        except InvalidTag:
            # Do NOT include ciphertext, nonce, or key material in this log line
            logger.error(
                "SecretsService.decrypt: AES-GCM authentication tag verification FAILED. "
                "The ciphertext may have been tampered with or the key is incorrect."
            )
            raise  # propagate — callers decide how to surface this

        try:
            credentials: dict = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecretsServiceError(
                "Decrypted bytes are not valid UTF-8 JSON. "
                "The stored secret may be corrupt."
            ) from exc
        finally:
            del plaintext  # belt-and-suspenders: clear plaintext bytes ASAP

        logger.debug("SecretsService.decrypt: decryption successful.")
        return credentials


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------
# Instantiated lazily so that missing CONNECTOR_SECRET_KEY only raises when
# the service is first used, not at import time (which would break tests and
# migrations that set up the environment differently).

_service_instance: SecretsService | None = None


def get_secrets_service() -> SecretsService:
    """
    Return the process-level ``SecretsService`` singleton.

    Use this in FastAPI ``Depends`` or anywhere a shared instance is needed::

        from app.services.secrets_service import get_secrets_service

        svc = get_secrets_service()
        ciphertext, nonce = svc.encrypt({"api_key": "..."})

    The instance is created on first call and reused thereafter.
    Raises ``SecretsServiceError`` on first call if ``CONNECTOR_SECRET_KEY``
    is absent or malformed.
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = SecretsService()
    return _service_instance
