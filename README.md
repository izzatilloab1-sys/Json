# Kiro Account Lite — Telegram login bot

A Telegram bot (aiogram 3.x + aiohttp) that runs the **AWS SSO OIDC
device-code flow** and returns a `kiro-account-lite` compatible JSON export.

The device-code grant is the same flow the AWS CLI and Kiro use for headless
sign-in: the user opens a verification URL, authenticates with their own
Builder ID / IAM Identity Center account, and clicks **Allow**. The bot never
sees or asks for the user's password — AWS handles authentication in the
browser and only returns tokens after explicit consent.

## How it works

```
/login
  └─ register_client            → clientId / clientSecret
  └─ start_device_authorization → userCode + verificationUriComplete
  └─ (user opens link, signs in, approves)
  └─ create_token (polled)      → accessToken / refreshToken / expiresIn
  └─ build + send kiro-account-<ts>.json
```

Retryable states are handled: `authorization_pending` (keep polling),
`slow_down` (back off the interval by +5s), and terminal states
`expired_token` / `access_denied` are reported back to the user.

## Project layout

```
.
├── run.py                 # entry point: python run.py
├── requirements.txt
├── .env.example           # copy to .env and fill in BOT_TOKEN
└── kiro_bot/
    ├── __init__.py
    ├── config.py          # env/.env config loader
    ├── aws_oidc.py        # async AWS SSO OIDC client (aiohttp, no boto3)
    ├── exporter.py        # builds the kiro-account-lite JSON
    └── bot.py             # aiogram handlers + device-flow orchestration
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # then edit .env and set BOT_TOKEN
python run.py
```

Get a bot token from [@BotFather](https://t.me/BotFather). In Telegram, send
`/login` to your bot and follow the link.

## Configuration (.env)

| Variable | Default | Notes |
|---|---|---|
| `BOT_TOKEN` | — | **Required.** From @BotFather. |
| `AWS_REGION` | `us-east-1` | OIDC endpoint region. |
| `CLIENT_NAME` | `Kiro Manager Lite` | Client name sent to `register_client`. |
| `START_URL` | `https://view.awsapps.com/start` | Builder ID default, or your IdC start URL. |
| `KIRO_APP` / `KIRO_VERSION` | `kiro-account-lite` / `1.0.23` | Export metadata. |
| `SUB_*` | Pro_Max / … | See note below. |
| `PROFILE_ARN_ACCOUNT_ID` | `000000000000` | Account-id segment of `profileArn`. |
| `ALLOWED_USER_IDS` | *(empty)* | Comma-separated Telegram user IDs; empty = allow all. |

## Notes on the data

- **`subscription` is placeholder metadata.** AWS does **not** return
  subscription/entitlement information in the OIDC token response. The
  `type` / `title` / `rawType` / `daysRemaining` fields are cosmetic values
  read from your `.env` so the file is format-compatible — they do not reflect
  a real AWS entitlement. Set them to whatever your downstream tooling expects.
- **`email` / `userId`** are extracted from the access-token JWT claims when
  the token is a readable JWT; otherwise safe placeholders are used (and the
  export still validates).
- `profileArn` uses a fixed placeholder profile id (`AAAACCCCXXXX`) and the
  configured account id. Replace with your real CodeWhisperer profile ARN if
  you have one.

## Security

- The exported JSON contains **live access and refresh tokens**. Treat the
  file as a secret — anyone with it can act as the signed-in account until the
  tokens expire/are revoked. Don't commit it or post it in shared chats.
- Use `ALLOWED_USER_IDS` to restrict who can run the flow through your bot.
- `.env` and any generated `kiro-account-*.json` are git-ignored.
```
