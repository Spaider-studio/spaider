"""Sign in with ChatGPT: contract tests against a LOCAL fake issuer (no contact
with OpenAI). Covers PKCE/state/nonce, dynamic registration parameters, ID-token
verification via JWKS, host-bound storage, serialized refresh with rotation,
invalid_grant -> sign in again, logout with revocation, denied consent, https rule."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import urllib.parse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from spaider_foldable.providers.connections import ConnectionStore
from spaider_foldable.providers.credentials import CredentialStore
from spaider_foldable.providers.events import GenerationError
from spaider_foldable.providers.siwc import SiwcConfig, SiwcService
from tests.providers.fakeserver import FakeProvider, Scripted


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class FakeIssuer:
    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        self.fake = FakeProvider()
        self.authorize = []  # parsed queries
        self.tokens = []  # parsed forms
        self.revoked = []
        self.codes = {}
        self.refresh_ok = True
        self.refresh_count = 0
        self.client_id = "oaiapp_test123"
        self.base = ""

    def start(self):
        f = self.fake.start()
        self.base = f.base_url
        jwk = RSAAlgorithm.to_jwk(self.key.public_key())
        jwk = json.loads(jwk) if isinstance(jwk, str) else jwk
        jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
        disc = {"issuer": self.base, "jwks_uri": self.base + "/jwks", "revocation_endpoint": self.base + "/revoke", "token_endpoint": self.base + "/api/accounts/oauth/token"}
        f.on("GET", "/.well-known/openid-configuration", Scripted(200, "application/json", json.dumps(disc).encode()))
        f.on("GET", "/jwks", Scripted(200, "application/json", json.dumps({"keys": [jwk]}).encode()))
        f.on("GET", "/api/accounts/authorize", self._authorize)
        f.on("POST", "/api/accounts/oauth/token", self._token)
        f.on("POST", "/revoke", self._revoke)
        return self

    def stop(self):
        self.fake.stop()

    def _authorize(self, rec):
        q = {k: v[0] for k, v in urllib.parse.parse_qs(rec.query).items()}
        self.authorize.append(q)
        for k in ("response_type", "client_id", "redirect_uri", "scope", "state", "nonce", "code_challenge", "code_challenge_method", "resource", "agent_name_hint", "ext_agent_host_id"):
            assert q.get(k), f"missing {k}"
        assert q["client_id"] == "dynamic_agent_client" and q["code_challenge_method"] == "S256"
        assert q["redirect_uri"].startswith("http://127.0.0.1:") and q["redirect_uri"].endswith("/auth/callback")
        assert "chatgpt.tokens.use.direct" in q["scope"] and q["resource"] == "https://api.openai.com/v1"
        code = "code-" + _b64url(hashlib.sha256(q["state"].encode()).digest())[:10]
        self.codes[code] = q
        loc = q["redirect_uri"] + "?" + urllib.parse.urlencode({"code": code, "state": q["state"]})
        return Scripted(302, "text/html", b"", headers={"Location": loc})

    def _id_token(self, nonce: str, email="test@example.com") -> str:
        now = int(time.time())
        claims = {"iss": self.base, "aud": self.client_id, "sub": "user-1", "email": email, "nonce": nonce, "iat": now, "exp": now + 600}
        return jwt.encode(claims, self.pem, algorithm="RS256", headers={"kid": "k1"})

    def _token(self, rec):
        form = {k: v[0] for k, v in urllib.parse.parse_qs((rec.body or {}).get("_raw", "")).items()}
        self.tokens.append(form)
        if form.get("grant_type") == "authorization_code":
            q = self.codes.pop(form.get("code"), None)
            if q is None or form.get("client_id") != "dynamic_agent_client" or form.get("resource") != "https://api.openai.com/v1":
                return Scripted(400, "application/json", b'{"error":"invalid_grant"}')
            expected = _b64url(hashlib.sha256(form["code_verifier"].encode()).digest())
            if expected != q["code_challenge"] or form.get("redirect_uri") != q["redirect_uri"]:
                return Scripted(400, "application/json", b'{"error":"invalid_grant","error_description":"pkce"}')
            body = {"access_token": "at-1", "refresh_token": "rt-1", "id_token": self._id_token(q["nonce"]), "expires_in": 3600, "token_type": "Bearer", "client_id": self.client_id, "scope": q["scope"]}
            return Scripted(200, "application/json", json.dumps(body).encode())
        if form.get("grant_type") == "refresh_token":
            if not self.refresh_ok:
                return Scripted(400, "application/json", b'{"error":"invalid_grant"}')
            if form.get("client_id") != self.client_id or form.get("resource") != "https://api.openai.com/v1":
                return Scripted(400, "application/json", b'{"error":"invalid_client"}')
            self.refresh_count += 1
            n = self.refresh_count
            time.sleep(0.15)  # make concurrent callers overlap
            return Scripted(200, "application/json", json.dumps({"access_token": f"at-r{n}", "refresh_token": f"rt-r{n}", "expires_in": 3600}).encode())
        return Scripted(400, "application/json", b'{"error":"unsupported_grant_type"}')

    def _revoke(self, rec):
        form = {k: v[0] for k, v in urllib.parse.parse_qs((rec.body or {}).get("_raw", "")).items()}
        self.revoked.append(form)
        return Scripted(200, "application/json", b"{}")


def _expire(cs, creds, conn_id):
    """Rewrite the stored token record so the access token counts as expired."""
    c = cs.get(conn_id)
    rec = json.loads(creds.get(c.credential_ref))
    rec["expires_at"] = time.time() - 1
    ref = creds.put(json.dumps(rec), storage=c.credential_storage, label=f"siwc:{conn_id}")
    cs.update(conn_id, credential_ref=ref)


@pytest.fixture
def issuer():
    i = FakeIssuer().start()
    yield i
    i.stop()


@pytest.fixture
def setup(db, tmp_path, issuer):
    cs = ConnectionStore(db, "prof1")
    conn = cs.create(provider="openai_siwc", protocol="responses", name="ChatGPT", base_url="https://api.openai.com/v1", auth_method="oauth_siwc", execution="cloud")
    creds = CredentialStore(tmp_path / "host.json", tmp_path / "vault.json")
    cfg = SiwcConfig(issuer=issuer.base, allow_insecure_http=True, refresh_margin_s=60)
    svc = SiwcService(creds, cs, host_id="urn:uuid:11111111-2222-3333-4444-555555555555", config=cfg)
    return cs, conn, creds, svc


async def _browser_login(svc, conn, issuer, *, deny=False, wrong_state=False):
    started = await svc.start_login(conn)
    assert svc.status(conn)["state"] == "pending" and started["port"] > 0
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as c:
        r = await c.get(started["authorize_url"])
        assert r.status_code == 302
        loc = r.headers["location"]
        if wrong_state:
            loc = loc.replace(urllib.parse.quote(started["state"], safe=""), "WRONG").replace(started["state"], "WRONG")
        if deny:
            u = urllib.parse.urlsplit(loc)
            loc = urllib.parse.urlunsplit(u._replace(query=urllib.parse.urlencode({"state": started["state"], "error": "access_denied", "error_description": "Nutzer hat abgelehnt"})))
        r2 = await c.get(loc)
    for _ in range(100):
        st = svc.status(svc.connections.get(conn.id))
        if st["state"] != "pending":
            break
        await asyncio.sleep(0.05)
    return started, r2, svc.status(svc.connections.get(conn.id))


def test_full_login_refresh_logout(setup, issuer):
    cs, conn, creds, svc = setup

    async def run():
        started, r2, st = await _browser_login(svc, conn, issuer)
        assert r2.status_code == 200 and "Anmeldung abgeschlossen" in r2.text
        assert st["state"] == "signed_in" and st["account_label"] == "test@example.com" and st["client_id_issued"]
        assert issuer.authorize[0]["agent_name_hint"] == "SpAIder Foldable" and issuer.authorize[0]["ext_agent_host_id"].startswith("urn:uuid:")
        assert issuer.tokens[0]["grant_type"] == "authorization_code" and "code_verifier" in issuer.tokens[0]
        c = cs.get(conn.id)
        assert c.credential_ref and c.credential_storage in ("host", "session") and not c.credential_ref.startswith("vault:")
        # valid token: no refresh
        assert await svc.access_token(c) == "at-1" and issuer.refresh_count == 0
        # expired: one refresh for two concurrent callers (serialized), rotation stored
        _expire(cs, creds, conn.id)
        t1, t2 = await asyncio.gather(svc.access_token(cs.get(conn.id)), svc.access_token(cs.get(conn.id)))
        assert issuer.refresh_count == 1 and t1 == t2 == "at-r1"
        assert issuer.tokens[-1]["refresh_token"] == "rt-1" and issuer.tokens[-1]["client_id"] == issuer.client_id
        rec = json.loads(creds.get(cs.get(conn.id).credential_ref))
        assert rec["refresh_token"] == "rt-r1"
        # still valid now: no further refresh
        assert await svc.access_token(cs.get(conn.id)) == "at-r1" and issuer.refresh_count == 1
        # expired again -> second rotation
        _expire(cs, creds, conn.id)
        assert await svc.access_token(cs.get(conn.id)) == "at-r2"
        # revoked upstream -> sign in again, nothing silently falls back
        issuer.refresh_ok = False
        _expire(cs, creds, conn.id)
        with pytest.raises(GenerationError) as exc:
            await svc.access_token(cs.get(conn.id))
        assert exc.value.code == "unauthorized" and "erneut anmelden" in exc.value.message_de
        assert svc.status(cs.get(conn.id))["state"] == "needs_setup" and cs.get(conn.id).credential_ref is None
        # login again, then logout revokes the refresh token
        issuer.refresh_ok = True
        _, _, st = await _browser_login(svc, cs.get(conn.id), issuer)
        assert st["state"] == "signed_in"
        out = await svc.logout(cs.get(conn.id))
        assert out["revoked"] and issuer.revoked[-1]["token"] == "rt-1" and issuer.revoked[-1]["client_id"] == issuer.client_id
        c = cs.get(conn.id)
        assert c.credential_ref is None and c.account_label == "" and svc.status(c)["state"] == "needs_setup"
        await svc.aclose()

    asyncio.run(run())


def test_denied_consent_and_wrong_state(setup, issuer):
    cs, conn, creds, svc = setup

    async def run():
        _, r2, st = await _browser_login(svc, conn, issuer, deny=True)
        assert r2.status_code == 400 and st["state"] == "needs_setup" and "abgelehnt" in (st["last_error"] or "")
        started, r2, st = await _browser_login(svc, conn, issuer, wrong_state=True)
        assert r2.status_code == 400 and "state" in r2.text
        # the flow is still pending (no code accepted); cancel closes the listener
        assert st["state"] == "pending"
        assert await svc.cancel(conn.id) is True
        assert svc.status(cs.get(conn.id))["state"] == "needs_setup"
        with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
            async with httpx.AsyncClient(timeout=2) as c:
                await c.get(f"http://127.0.0.1:{started['port']}/auth/callback")
        await svc.aclose()

    asyncio.run(run())


def test_bad_id_token_is_rejected(setup, issuer):
    cs, conn, creds, svc = setup
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer.pem = other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())  # signed with a key not in JWKS

    async def run():
        _, _, st = await _browser_login(svc, conn, issuer)
        assert st["state"] == "needs_setup" and "ID-Token" in (st["last_error"] or "")
        assert cs.get(conn.id).credential_ref is None
        await svc.aclose()

    asyncio.run(run())


def test_https_required_and_wrong_auth_method(db, tmp_path):
    cs = ConnectionStore(db, "p")
    conn = cs.create(provider="openai_siwc", protocol="responses", name="x", base_url="https://api.openai.com/v1", auth_method="oauth_siwc", execution="cloud")
    svc = SiwcService(CredentialStore(tmp_path / "h.json", tmp_path / "v.json"), cs, config=SiwcConfig(issuer="http://127.0.0.1:9"))

    async def run():
        with pytest.raises(GenerationError) as exc:
            await svc.start_login(conn)
        assert exc.value.code == "bad_request"
        key_conn = cs.create(provider="openai", protocol="responses", name="k", base_url="https://api.openai.com/v1", auth_method="api_key", execution="cloud")
        with pytest.raises(GenerationError):
            await svc.start_login(key_conn)
        assert svc.status(conn)["state"] == "needs_setup" and svc.status(conn)["state_de"] == "Einrichtung erforderlich"
        with pytest.raises(GenerationError) as exc:
            await svc.access_token(conn)
        assert exc.value.code == "credential"

    asyncio.run(run())
