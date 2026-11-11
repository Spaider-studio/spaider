"""Sign in with ChatGPT (SIWC) for the optional ``openai_siwc`` preset (research S16–S21).

Flow (never started unattended; the user opens the URL in their browser and
grants consent themselves):

1. ``start_login``: PKCE S256 + state + nonce, a loopback listener on
   ``http://127.0.0.1:<port>/auth/callback`` and the authorize URL with the
   dynamic registration parameters (``client_id=dynamic_agent_client``,
   ``agent_name_hint``, ``ext_agent_host_id``).
2. The browser returns ``code`` + ``state`` to the loopback; the code is
   exchanged at the token endpoint with the verifier and ``resource``.
3. The ID token is verified against the issuer's JWKS (issuer, audience =
   issued client id, nonce). Tokens are stored host-bound (DPAPI) or, if that
   is unavailable, for the session only; refresh tokens never go into the
   portable vault.
4. ``access_token`` hands a valid token to the adapter and refreshes it
   serialized per connection; ``invalid_grant``/``refresh_token_*`` means
   "sign in again" – there is no silent fallback to API-key billing.

Status wording for the UI: ``needs_setup`` → „Einrichtung erforderlich“.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import jwt
from jwt import PyJWKClient

from .base import is_private_url
from .connections import ConnectionStore, ProviderConnection
from .credentials import CredentialError, CredentialStore
from .events import GenerationError

logger = logging.getLogger(__name__)

CALLBACK_HTML_OK = (
    "<!doctype html><html lang='de'><meta charset='utf-8'><title>SpAIder Foldable</title>"
    "<body style='font-family:system-ui;margin:3rem'><h2>Anmeldung abgeschlossen</h2>"
    "<p>Sie können dieses Fenster schließen und zu SpAIder Foldable zurückkehren.</p></body></html>"
)
CALLBACK_HTML_ERR = (
    "<!doctype html><html lang='de'><meta charset='utf-8'><title>SpAIder Foldable</title>"
    "<body style='font-family:system-ui;margin:3rem'><h2>Anmeldung nicht abgeschlossen</h2>"
    "<p>{detail}</p><p>Bitte in SpAIder Foldable erneut „Mit ChatGPT anmelden“ wählen.</p></body></html>"
)


@dataclass
class SiwcConfig:
    issuer: str = "https://auth.openai.com"
    authorize_path: str = "/api/accounts/authorize"
    token_path: str = "/api/accounts/oauth/token"
    scope: str = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
    resource: str = "https://api.openai.com/v1"
    dynamic_client_id: str = "dynamic_agent_client"
    agent_name_hint: str = "SpAIder Foldable"
    callback_path: str = "/auth/callback"
    login_timeout_s: int = 600
    verify_id_token: bool = True
    refresh_margin_s: int = 60
    allow_insecure_http: bool = False  # tests only: fake issuer on a private http URL

    @property
    def authorize_url(self) -> str:
        return self.issuer.rstrip("/") + self.authorize_path

    @property
    def token_url(self) -> str:
        return self.issuer.rstrip("/") + self.token_path

    @property
    def discovery_url(self) -> str:
        return self.issuer.rstrip("/") + "/.well-known/openid-configuration"

    @classmethod
    def from_extra(cls, extra: Optional[dict]) -> "SiwcConfig":
        cfg = cls()
        for k, v in (extra or {}).items():
            if hasattr(cfg, k) and not k.startswith("_"):
                setattr(cfg, k, v)
        return cfg


@dataclass
class PendingLogin:
    connection_id: str
    state: str
    nonce: str
    verifier: str
    redirect_uri: str
    port: int
    created_at: float
    server: Any = None
    future: Optional[asyncio.Future] = None
    task: Optional[asyncio.Task] = None
    error: Optional[str] = None
    done: bool = False


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _now() -> float:
    return time.time()


class SiwcService:
    def __init__(self, credentials: CredentialStore, connections: ConnectionStore, *, host_id: str = "", config: Optional[SiwcConfig] = None):
        self.credentials = credentials
        self.connections = connections
        self.host_id = host_id or "urn:uuid:00000000-0000-0000-0000-000000000000"
        self.config = config or SiwcConfig()
        self._pending: dict[str, PendingLogin] = {}
        self._last_error: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._client: Optional[httpx.AsyncClient] = None
        self._jwks: Optional[PyJWKClient] = None
        self._discovery: Optional[dict] = None

    # ---- helpers ------------------------------------------------------------------------
    def _cfg(self, conn: ProviderConnection) -> SiwcConfig:
        extra = (conn.extra or {}).get("siwc")
        if extra:
            return SiwcConfig.from_extra({**self.config.__dict__, **extra})
        return self.config

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=False, headers={"User-Agent": "SpAIder-Foldable/0.4"})
        return self._client

    def _check_url(self, url: str, cfg: SiwcConfig) -> None:
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" and not (cfg.allow_insecure_http and is_private_url(url)):
            raise GenerationError("bad_request", "Der Anmeldeserver muss über HTTPS erreichbar sein.")

    def _lock(self, conn_id: str) -> asyncio.Lock:
        lock = self._locks.get(conn_id)
        if lock is None:
            lock = self._locks[conn_id] = asyncio.Lock()
        return lock

    def _record(self, conn: ProviderConnection) -> Optional[dict]:
        if conn.auth_method != "oauth_siwc" or not conn.credential_ref:
            return None
        try:
            raw = self.credentials.get(conn.credential_ref)
        except CredentialError:
            return None
        if not raw:
            return None
        try:
            rec = json.loads(raw)
        except ValueError:
            return None
        return rec if isinstance(rec, dict) and rec.get("refresh_token") else None

    def _store(self, conn: ProviderConnection, rec: dict) -> ProviderConnection:
        storage = "host" if self.credentials.host.available() else "session"
        old = conn.credential_ref
        ref = self.credentials.put(json.dumps(rec), storage=storage, label=f"siwc:{conn.id}")
        if old and old != ref:
            try:
                self.credentials.delete(old)
            except CredentialError:
                pass
        return self.connections.update(conn.id, credential_ref=ref, credential_storage=storage, account_label=rec.get("account_label") or conn.account_label, auth_method="oauth_siwc")

    # ---- status -------------------------------------------------------------------------
    def status(self, conn: ProviderConnection) -> dict:
        pend = self._pending.get(conn.id)
        rec = self._record(conn)
        state = "needs_setup"
        if pend is not None and not pend.done:
            state = "pending"
        elif rec is not None:
            state = "signed_in"
        out = {
            "connection_id": conn.id,
            "state": state,
            "state_de": {"needs_setup": "Einrichtung erforderlich", "pending": "Anmeldung im Browser offen", "signed_in": "Angemeldet"}[state],
            "account_label": conn.account_label or (rec or {}).get("account_label") or "",
            "storage": conn.credential_storage if rec else "none",
            "expires_at": (rec or {}).get("expires_at"),
            "client_id_issued": bool((rec or {}).get("client_id")),
            "scope": (rec or {}).get("scope"),
            "last_error": self._last_error.get(conn.id),
            "pending_port": pend.port if pend is not None and not pend.done else None,
            "note_de": "Die Anmeldung läuft im Browser; SpAIder bestätigt nichts automatisch. Refresh-Token bleiben auf diesem Rechner (nicht im portablen Tresor).",
        }
        return out

    # ---- login --------------------------------------------------------------------------
    async def start_login(self, conn: ProviderConnection) -> dict:
        if conn.auth_method != "oauth_siwc":
            raise GenerationError("unsupported", "Diese Verbindung nutzt keine ChatGPT-Anmeldung.")
        cfg = self._cfg(conn)
        self._check_url(cfg.authorize_url, cfg)
        await self.cancel(conn.id)
        self._last_error.pop(conn.id, None)
        verifier = _b64url(secrets.token_bytes(48))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = _b64url(secrets.token_bytes(24))
        nonce = _b64url(secrets.token_bytes(24))
        loop = asyncio.get_running_loop()
        pending = PendingLogin(conn.id, state, nonce, verifier, "", 0, _now())
        pending.future = loop.create_future()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            except Exception:  # noqa: BLE001
                writer.close()
                return
            line = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            parts = line.split(" ")
            path = parts[1] if len(parts) >= 2 else "/"
            u = urllib.parse.urlsplit(path)
            status, body = 404, "<h2>Nicht gefunden</h2>"
            if u.path == cfg.callback_path:
                q = urllib.parse.parse_qs(u.query)
                st = (q.get("state") or [""])[0]
                if st != pending.state:
                    status, body = 400, CALLBACK_HTML_ERR.format(detail="Der Rückgabewert passt nicht zur laufenden Anmeldung (state).")
                elif q.get("error"):
                    detail = f"{q['error'][0]}: {(q.get('error_description') or [''])[0]}"
                    status, body = 400, CALLBACK_HTML_ERR.format(detail=detail)
                    if not pending.future.done():
                        pending.future.set_exception(GenerationError("unauthorized", f"Anmeldung abgelehnt: {detail}"))
                elif q.get("code"):
                    status, body = 200, CALLBACK_HTML_OK
                    if not pending.future.done():
                        pending.future.set_result(q["code"][0])
                else:
                    status, body = 400, CALLBACK_HTML_ERR.format(detail="Kein Autorisierungscode erhalten.")
            data = body.encode("utf-8")
            reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}[status]
            writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(data)}\r\nConnection: close\r\nCache-Control: no-store\r\n\r\n".encode("ascii") + data)
            try:
                await writer.drain()
            except Exception:  # noqa: BLE001
                pass
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        pending.server = server
        pending.port = server.sockets[0].getsockname()[1]
        pending.redirect_uri = f"http://127.0.0.1:{pending.port}{cfg.callback_path}"
        params = {
            "response_type": "code",
            "client_id": cfg.dynamic_client_id,
            "redirect_uri": pending.redirect_uri,
            "scope": cfg.scope,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "resource": cfg.resource,
            "agent_name_hint": cfg.agent_name_hint,
            "ext_agent_host_id": self.host_id,
        }
        url = cfg.authorize_url + "?" + urllib.parse.urlencode(params)
        self._pending[conn.id] = pending
        pending.task = asyncio.create_task(self._finish(conn.id, pending, cfg))
        logger.info("siwc: login started for connection %s (loopback port %d)", conn.id, pending.port)
        return {"authorize_url": url, "state": state, "port": pending.port, "expires_at": pending.created_at + cfg.login_timeout_s, "note_de": "Bitte die Adresse im Browser öffnen und die Anmeldung dort bestätigen."}

    async def _finish(self, conn_id: str, pending: PendingLogin, cfg: SiwcConfig) -> None:
        try:
            code = await asyncio.wait_for(pending.future, timeout=cfg.login_timeout_s)
            rec = await self._exchange(cfg, pending, code)
            conn = self.connections.get(conn_id)
            if conn is None:
                raise GenerationError("not_found", "Die Verbindung wurde zwischenzeitlich gelöscht.")
            self._store(conn, rec)
            logger.info("siwc: signed in for connection %s (%s)", conn_id, rec.get("account_label") or "ohne E-Mail")
        except asyncio.TimeoutError:
            pending.error = "Zeitüberschreitung: Die Anmeldung wurde im Browser nicht abgeschlossen."
        except asyncio.CancelledError:
            pending.error = "Anmeldung abgebrochen."
        except GenerationError as exc:
            pending.error = exc.message_de
        except Exception as exc:  # noqa: BLE001
            pending.error = f"Anmeldung fehlgeschlagen: {type(exc).__name__}"
            logger.warning("siwc: login failed for %s: %s", conn_id, exc)
        finally:
            pending.done = True
            if pending.error:
                self._last_error[conn_id] = pending.error
            if pending.server is not None:
                pending.server.close()
            if self._pending.get(conn_id) is pending:
                self._pending.pop(conn_id, None)

    async def _exchange(self, cfg: SiwcConfig, pending: PendingLogin, code: str) -> dict:
        self._check_url(cfg.token_url, cfg)
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": cfg.dynamic_client_id,
            "code_verifier": pending.verifier,
            "resource": cfg.resource,
        }
        try:
            r = await self._http().post(cfg.token_url, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise GenerationError("network", f"Der Anmeldeserver ist nicht erreichbar ({type(exc).__name__}).")
        if r.status_code >= 400:
            raise GenerationError("unauthorized", f"Token-Austausch abgelehnt (HTTP {r.status_code}): {_err_code(r)}", status=r.status_code)
        tok = r.json()
        if not tok.get("access_token") or not tok.get("refresh_token"):
            raise GenerationError("protocol", "Die Token-Antwort enthält kein Zugriffs- oder Refresh-Token.")
        client_id = tok.get("client_id") or ""
        claims: dict = {}
        if tok.get("id_token"):
            claims = await self._verify_id_token(cfg, tok["id_token"], pending.nonce, client_id or None)
            client_id = client_id or (claims.get("aud") if isinstance(claims.get("aud"), str) else (claims.get("aud") or [""])[0])
        if not client_id:
            raise GenerationError("protocol", "Der Anmeldeserver hat keine Client-ID ausgestellt.")
        return {
            "access_token": tok["access_token"],
            "refresh_token": tok["refresh_token"],
            "expires_at": _now() + float(tok.get("expires_in") or 3600),
            "client_id": client_id,
            "scope": tok.get("scope") or cfg.scope,
            "account_label": claims.get("email") or "ChatGPT-Konto",
            "issuer": cfg.issuer,
            "obtained_at": _now(),
        }

    async def _discover(self, cfg: SiwcConfig) -> dict:
        if self._discovery is None:
            try:
                r = await self._http().get(cfg.discovery_url)
                r.raise_for_status()
                self._discovery = r.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise GenerationError("network", f"OpenID-Konfiguration nicht abrufbar ({type(exc).__name__}).")
        return self._discovery

    async def _verify_id_token(self, cfg: SiwcConfig, id_token: str, nonce: str, audience: Optional[str]) -> dict:
        if not cfg.verify_id_token:
            return jwt.decode(id_token, options={"verify_signature": False, "verify_aud": False, "verify_exp": False})
        disc = await self._discover(cfg)
        jwks_uri = disc.get("jwks_uri")
        if not jwks_uri:
            raise GenerationError("protocol", "OpenID-Konfiguration ohne jwks_uri.")
        self._check_url(jwks_uri, cfg)
        if self._jwks is None or getattr(self._jwks, "uri", None) != jwks_uri:
            self._jwks = PyJWKClient(jwks_uri, cache_keys=True)
        try:
            key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, id_token)
            aud = audience
            if aud is None:
                unverified = jwt.decode(id_token, options={"verify_signature": False, "verify_aud": False, "verify_exp": False})
                aud = unverified.get("aud") if isinstance(unverified.get("aud"), str) else (unverified.get("aud") or [None])[0]
            claims = jwt.decode(id_token, key.key, algorithms=["RS256", "ES256"], audience=aud, issuer=disc.get("issuer") or cfg.issuer, options={"require": ["exp", "iat", "iss", "aud"]})
        except jwt.PyJWTError as exc:
            raise GenerationError("unauthorized", f"ID-Token ungültig ({type(exc).__name__}).")
        except Exception as exc:  # noqa: BLE001
            raise GenerationError("network", f"Schlüssel des Anmeldeservers nicht abrufbar ({type(exc).__name__}).")
        if claims.get("nonce") != nonce:
            raise GenerationError("unauthorized", "ID-Token ungültig (nonce).")
        return claims

    # ---- token provider for the registry --------------------------------------------------
    async def access_token(self, conn: ProviderConnection) -> str:
        rec = self._record(conn)
        if rec is None:
            raise GenerationError("credential", "Für diese Verbindung ist keine ChatGPT-Anmeldung hinterlegt. Unter „Verbindungen“ → „Mit ChatGPT anmelden“ wählen.")
        cfg = self._cfg(conn)
        if rec.get("expires_at", 0) - _now() > cfg.refresh_margin_s:
            return rec["access_token"]
        async with self._lock(conn.id):
            fresh = self.connections.get(conn.id)
            rec = self._record(fresh) if fresh else None
            if rec is None:
                raise GenerationError("credential", "ChatGPT-Anmeldung wurde entfernt; bitte erneut anmelden.")
            if rec.get("expires_at", 0) - _now() > cfg.refresh_margin_s:
                return rec["access_token"]
            rec = await self._refresh(cfg, fresh, rec)
            return rec["access_token"]

    async def _refresh(self, cfg: SiwcConfig, conn: ProviderConnection, rec: dict) -> dict:
        self._check_url(cfg.token_url, cfg)
        form = {"grant_type": "refresh_token", "refresh_token": rec["refresh_token"], "client_id": rec["client_id"], "resource": cfg.resource}
        try:
            r = await self._http().post(cfg.token_url, data=form, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise GenerationError("network", f"Token-Erneuerung nicht möglich ({type(exc).__name__}).", retryable=True)
        if r.status_code >= 400:
            code = _err_code(r)
            if r.status_code in (400, 401) and (code == "invalid_grant" or code.startswith("refresh_token_")):
                self._forget(conn)
                raise GenerationError("unauthorized", "Die ChatGPT-Anmeldung ist abgelaufen oder wurde widerrufen; bitte erneut anmelden.", status=r.status_code)
            raise GenerationError("server_error" if r.status_code >= 500 else "unauthorized", f"Token-Erneuerung abgelehnt (HTTP {r.status_code}): {code}", status=r.status_code, retryable=r.status_code >= 500)
        tok = r.json()
        if not tok.get("access_token"):
            raise GenerationError("protocol", "Die Erneuerungsantwort enthält kein Zugriffstoken.")
        new = {**rec, "access_token": tok["access_token"], "refresh_token": tok.get("refresh_token") or rec["refresh_token"], "expires_at": _now() + float(tok.get("expires_in") or 3600), "obtained_at": _now()}
        self._store(conn, new)
        logger.info("siwc: token refreshed for connection %s", conn.id)
        return new

    def _forget(self, conn: ProviderConnection) -> None:
        try:
            self.credentials.delete(conn.credential_ref)
        except CredentialError:
            pass
        self.connections.update(conn.id, credential_ref=None, credential_storage="none")

    # ---- logout / cancel / close ----------------------------------------------------------
    async def logout(self, conn: ProviderConnection) -> dict:
        await self.cancel(conn.id)
        rec = self._record(conn)
        revoked = False
        if rec is not None:
            cfg = self._cfg(conn)
            try:
                disc = await self._discover(cfg)
                endpoint = disc.get("revocation_endpoint")
                if endpoint:
                    self._check_url(endpoint, cfg)
                    r = await self._http().post(endpoint, data={"token": rec["refresh_token"], "token_type_hint": "refresh_token", "client_id": rec["client_id"]})
                    revoked = r.status_code < 400
            except GenerationError as exc:
                logger.warning("siwc: revocation skipped for %s: %s", conn.id, exc.message_de)
            except httpx.HTTPError as exc:
                logger.warning("siwc: revocation failed for %s: %s", conn.id, type(exc).__name__)
            self._forget(conn)
        self.connections.update(conn.id, account_label="")
        self._last_error.pop(conn.id, None)
        return {"ok": True, "revoked": revoked}

    async def cancel(self, conn_id: str) -> bool:
        pend = self._pending.pop(conn_id, None)
        if pend is None:
            return False
        if pend.task is not None and not pend.task.done():
            pend.task.cancel()
            try:
                await pend.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if pend.server is not None:
            pend.server.close()
            try:
                await asyncio.wait_for(pend.server.wait_closed(), 2)
            except Exception:  # noqa: BLE001
                pass
        return True

    async def aclose(self) -> None:
        for cid in list(self._pending):
            await self.cancel(cid)
        if self._client is not None:
            await self._client.aclose()
            self._client = None


def _err_code(r: httpx.Response) -> str:
    try:
        d = r.json()
        if isinstance(d, dict):
            e = d.get("error")
            if isinstance(e, dict):
                return str(e.get("code") or e.get("type") or e.get("message") or "")[:80]
            if e:
                return str(e)[:80]
    except ValueError:
        pass
    return (r.text or "")[:80]
