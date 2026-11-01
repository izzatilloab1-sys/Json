"""Async client for the AWS SSO OIDC device authorization grant.

Implements the three documented OIDC calls used by the device-code flow:

  1. register_client            -> clientId / clientSecret
  2. start_device_authorization -> deviceCode / userCode / verification URIs
  3. create_token               -> accessToken / refreshToken / expiresIn

The flow is the same one the AWS CLI and Kiro use for headless login: the
user explicitly opens the verification URL, authenticates, and approves the
request. No credentials are collected or transmitted by this bot.

Endpoints are called directly over HTTPS with aiohttp (no boto3 dependency).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

# Default scopes requested for a Kiro / CodeWhisperer client.
DEFAULT_SCOPES = [
    "codewhisperer:completions",
    "codewhisperer:analysis",
    "codewhisperer:conversations",
    "codewhisperer:transformations",
    "codewhisperer:taskassist",
]

GRANT_TYPE_DEVICE_CODE = "urn:ietf:params:oauth:grant-type:device_code"


class OidcError(Exception):
    """Raised for terminal (non-retryable) OIDC errors."""

    def __init__(self, error: str, description: str = "", status: int = 0):
        self.error = error
        self.description = description
        self.status = status
        super().__init__(f"{error}: {description}".strip(": "))


class AuthorizationPending(Exception):
    """User has not yet approved the request; keep polling."""


class SlowDown(Exception):
    """Server asked us to increase the polling interval."""


@dataclass
class RegisteredClient:
    client_id: str
    client_secret: str
    client_secret_expires_at: int  # epoch seconds


@dataclass
class DeviceAuthorization:
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int  # seconds
    interval: int  # seconds between polls


@dataclass
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int  # seconds
    token_type: str
    raw: dict[str, Any]


class AwsOidcClient:
    """Thin async wrapper around the AWS SSO OIDC HTTP API for one region."""

    def __init__(self, region: str = "us-east-1", session: Optional[aiohttp.ClientSession] = None):
        self.region = region
        self.base_url = f"https://oidc.{region}.amazonaws.com"
        self._session = session
        self._owns_session = session is None

    async def __aenter__(self) -> "AwsOidcClient":
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("AwsOidcClient used outside of an 'async with' block")
        return self._session

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        async with self.session.post(url, json=payload) as resp:
            # OIDC returns JSON bodies for both success and error responses.
            try:
                data = await resp.json(content_type=None)
            except Exception:
                text = await resp.text()
                raise OidcError("invalid_response", text, resp.status)

            if resp.status >= 400:
                error = data.get("error", "") or data.get("__type", "unknown_error")
                description = data.get("error_description", "") or data.get("message", "")
                # Normalise the retryable device-flow signals.
                if error in ("authorization_pending", "AuthorizationPendingException"):
                    raise AuthorizationPending()
                if error in ("slow_down", "SlowDownException"):
                    raise SlowDown()
                raise OidcError(error, description, resp.status)

            return data

    async def register_client(
        self,
        client_name: str,
        scopes: Optional[list[str]] = None,
        client_type: str = "public",
    ) -> RegisteredClient:
        payload = {
            "clientName": client_name,
            "clientType": client_type,
            "scopes": scopes or DEFAULT_SCOPES,
        }
        data = await self._post("/client/register", payload)
        return RegisteredClient(
            client_id=data["clientId"],
            client_secret=data["clientSecret"],
            client_secret_expires_at=int(data.get("clientSecretExpiresAt", 0)),
        )

    async def start_device_authorization(
        self,
        client: RegisteredClient,
        start_url: str,
    ) -> DeviceAuthorization:
        payload = {
            "clientId": client.client_id,
            "clientSecret": client.client_secret,
            "startUrl": start_url,
        }
        data = await self._post("/device_authorization", payload)
        return DeviceAuthorization(
            device_code=data["deviceCode"],
            user_code=data["userCode"],
            verification_uri=data["verificationUri"],
            verification_uri_complete=data.get(
                "verificationUriComplete", data["verificationUri"]
            ),
            expires_in=int(data.get("expiresIn", 600)),
            interval=int(data.get("interval", 5)),
        )

    async def create_token(
        self,
        client: RegisteredClient,
        device_code: str,
    ) -> TokenResponse:
        """Single create_token attempt.

        Raises AuthorizationPending / SlowDown for retryable states, or
        OidcError (e.g. expired_token, access_denied) for terminal states.
        """
        payload = {
            "clientId": client.client_id,
            "clientSecret": client.client_secret,
            "grantType": GRANT_TYPE_DEVICE_CODE,
            "deviceCode": device_code,
        }
        data = await self._post("/token", payload)
        return TokenResponse(
            access_token=data["accessToken"],
            refresh_token=data.get("refreshToken", ""),
            expires_in=int(data.get("expiresIn", 0)),
            token_type=data.get("tokenType", "Bearer"),
            raw=data,
        )

    async def refresh_token(
        self,
        client: RegisteredClient,
        refresh_token: str,
    ) -> TokenResponse:
        payload = {
            "clientId": client.client_id,
            "clientSecret": client.client_secret,
            "grantType": "refresh_token",
            "refreshToken": refresh_token,
        }
        data = await self._post("/token", payload)
        return TokenResponse(
            access_token=data["accessToken"],
            refresh_token=data.get("refreshToken", refresh_token),
            expires_in=int(data.get("expiresIn", 0)),
            token_type=data.get("tokenType", "Bearer"),
            raw=data,
        )


def now_ms() -> int:
    return int(time.time() * 1000)
