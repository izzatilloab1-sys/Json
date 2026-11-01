"""Configuration loaded from environment variables / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _parse_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.add(int(part))
            except ValueError:
                pass
    return ids


@dataclass
class Config:
    bot_token: str
    region: str
    client_name: str
    start_url: str

    kiro_app: str
    kiro_version: str

    sub_type: str
    sub_title: str
    sub_raw_type: str
    sub_days_remaining: int

    profile_arn_account_id: str

    allowed_user_ids: set[int] = field(default_factory=set)

    @classmethod
    def from_env(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN is not set. Copy .env.example to .env and fill it in."
            )
        return cls(
            bot_token=token,
            region=os.getenv("AWS_REGION", "us-east-1").strip(),
            client_name=os.getenv("CLIENT_NAME", "Kiro Manager Lite").strip(),
            start_url=os.getenv("START_URL", "https://view.awsapps.com/start").strip(),
            kiro_app=os.getenv("KIRO_APP", "kiro-account-lite").strip(),
            kiro_version=os.getenv("KIRO_VERSION", "1.0.23").strip(),
            sub_type=os.getenv("SUB_TYPE", "Pro_Max").strip(),
            sub_title=os.getenv("SUB_TITLE", "KIRO PRO MAX").strip(),
            sub_raw_type=os.getenv(
                "SUB_RAW_TYPE", "Q_DEVELOPER_STANDALONE_PRO_MAX"
            ).strip(),
            sub_days_remaining=int(os.getenv("SUB_DAYS_REMAINING", "8")),
            profile_arn_account_id=os.getenv(
                "PROFILE_ARN_ACCOUNT_ID", "000000000000"
            ).strip(),
            allowed_user_ids=_parse_ids(os.getenv("ALLOWED_USER_IDS", "")),
        )
