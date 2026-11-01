"""Builds the kiro-account-lite JSON export from an OIDC token response."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from typing import Any, Optional

from .aws_oidc import RegisteredClient, TokenResponse, now_ms
from .config import Config


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_jwt_claims(token: str) -> dict[str, Any]:
    """Best-effort decode of a JWT payload (no signature verification).

    AWS SSO access tokens are opaque in many cases; if the token is not a
    readable JWT we simply return an empty dict and fall back to placeholders.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}
        payload = _b64url_decode(parts[1])
        return json.loads(payload)
    except (ValueError, binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _extract_identity(token: TokenResponse) -> tuple[Optional[str], Optional[str]]:
    """Try to pull (email, user_id) out of the access token claims."""
    claims = decode_jwt_claims(token.access_token)
    email = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("username")
    )
    user_id = (
        claims.get("sub")
        or claims.get("user_id")
        or claims.get("aws:identity_store_user_id")
    )
    return email, user_id


def build_export(
    config: Config,
    client: RegisteredClient,
    token: TokenResponse,
    *,
    email_override: Optional[str] = None,
) -> dict[str, Any]:
    """Assemble the kiro-account-lite v1.0.x document for a single account."""
    exported_at = now_ms()
    expires_at = exported_at + token.expires_in * 1000

    claim_email, claim_user_id = _extract_identity(token)
    email = email_override or claim_email or "unknown@placeholder.local"
    user_id = claim_user_id or str(uuid.uuid4())

    profile_arn = (
        f"arn:aws:codewhisperer:{config.region}:"
        f"{config.profile_arn_account_id}:profile/AAAACCCCXXXX"
    )

    account = {
        "id": str(uuid.uuid4()),
        "email": email,
        "idp": "BuilderId",
        "userId": user_id,
        "profileArn": profile_arn,
        "credentials": {
            "accessToken": token.access_token,
            "refreshToken": token.refresh_token,
            "clientId": client.client_id,
            "clientSecret": client.client_secret,
            "region": config.region,
            "expiresAt": expires_at,
            "authMethod": "IdC",
            "provider": "BuilderId",
        },
        # NOTE: subscription is cosmetic/placeholder metadata. AWS does not
        # return entitlement info in the token response; these come from config.
        "subscription": {
            "type": config.sub_type,
            "title": config.sub_title,
            "rawType": config.sub_raw_type,
            "daysRemaining": config.sub_days_remaining,
        },
        "status": "active",
    }

    return {
        "app": config.kiro_app,
        "version": config.kiro_version,
        "exportedAt": exported_at,
        "accounts": [account],
    }


def export_to_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, indent=2, ensure_ascii=False).encode("utf-8")
