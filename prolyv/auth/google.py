"""Google sign-in with OpenID Connect: Authlib for the OAuth2 dance, joserfc for ID-token verification."""

from __future__ import annotations

import threading
import time

import httpx2 as httpx
from authlib.integrations.httpx_client import OAuth2Client
from joserfc import jwt
from joserfc.jwk import KeySet

from prolyv.auth.accounts import ExternalProfile
from prolyv.models import Provider

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPE = "openid email profile"
JWKS_TTL = 3600


class GoogleAuthError(Exception):
    """code: exchange_failed | bad_token | bad_nonce."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GoogleOIDC:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, transport=None) -> None:  # noqa: ANN001
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._transport = transport  # tests inject an httpx MockTransport
        self._jwks: KeySet | None = None
        self._jwks_fetched_at = 0.0
        self._lock = threading.Lock()

    def _oauth_client(self) -> OAuth2Client:
        kwargs = {"transport": self._transport} if self._transport is not None else {}
        return OAuth2Client(
            self.client_id, self.client_secret, redirect_uri=self.redirect_uri, scope=SCOPE, timeout=15, **kwargs
        )

    def authorization_url(self, state: str, nonce: str) -> str:
        with self._oauth_client() as client:
            url, _ = client.create_authorization_url(
                AUTH_URL, state=state, nonce=nonce, prompt="select_account", access_type="online"
            )
        return url

    def exchange_code(self, code: str) -> dict:
        try:
            with self._oauth_client() as client:
                token = client.fetch_token(TOKEN_URL, code=code, grant_type="authorization_code")
        except Exception as exc:
            raise GoogleAuthError("exchange_failed") from exc
        if not token.get("id_token"):
            raise GoogleAuthError("exchange_failed")
        return token

    def _fetch_jwks(self) -> KeySet:
        kwargs = {"transport": self._transport} if self._transport is not None else {}
        with httpx.Client(timeout=15, **kwargs) as client:
            response = client.get(JWKS_URL)
            response.raise_for_status()
            return KeySet.import_key_set(response.json())

    def jwks(self, force: bool = False) -> KeySet:
        with self._lock:
            if force or self._jwks is None or time.monotonic() - self._jwks_fetched_at > JWKS_TTL:
                self._jwks = self._fetch_jwks()
                self._jwks_fetched_at = time.monotonic()
            return self._jwks

    def verify_id_token(self, id_token: str, nonce: str) -> dict:
        """Signature against Google's JWKS, then iss / aud / exp / iat / sub and the nonce we sent."""
        try:
            token = jwt.decode(id_token, self.jwks(), algorithms=["RS256"])
        except Exception:
            try:  # key rotation: refresh the key set once and retry
                token = jwt.decode(id_token, self.jwks(force=True), algorithms=["RS256"])
            except Exception as exc:
                raise GoogleAuthError("bad_token") from exc
        registry = jwt.JWTClaimsRegistry(
            leeway=60,
            iss={"essential": True, "values": list(ISSUERS)},
            aud={"essential": True, "value": self.client_id},
            exp={"essential": True},
            iat={"essential": True},
            sub={"essential": True},
        )
        try:
            registry.validate(token.claims)
        except Exception as exc:
            raise GoogleAuthError("bad_token") from exc
        if not nonce or token.claims.get("nonce") != nonce:
            raise GoogleAuthError("bad_nonce")
        return dict(token.claims)

    @staticmethod
    def profile_from_claims(claims: dict) -> ExternalProfile:
        email = claims.get("email") if claims.get("email_verified") is True else None
        email = email.strip().lower() if email else None
        local_part = (email or "").split("@", 1)[0]
        name = (claims.get("name") or "").strip() or local_part or "Tea drinker"
        keep = ("sub", "email", "email_verified", "name", "given_name", "family_name", "picture", "locale", "hd")
        return ExternalProfile(
            provider=Provider.google,
            provider_user_id=str(claims["sub"]),
            display_name=name,
            preferred_handle=claims.get("given_name") or name or local_part,
            email=email,
            avatar_url=claims.get("picture"),
            raw={k: claims[k] for k in keep if k in claims},
        )
