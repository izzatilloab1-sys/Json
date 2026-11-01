"""aiogram 3.x bot that runs the AWS SSO OIDC device-code flow per user."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.markdown import hbold, hcode, hlink

from .aws_oidc import (
    AuthorizationPending,
    AwsOidcClient,
    DeviceAuthorization,
    OidcError,
    RegisteredClient,
    SlowDown,
)
from .config import Config
from .exporter import build_export, export_to_bytes

logger = logging.getLogger("kiro_bot")

router = Router()

# Per-chat guard so a user can't launch two overlapping logins.
_active_logins: set[int] = set()


@dataclass
class Services:
    config: Config


def _allowed(config: Config, user_id: Optional[int]) -> bool:
    if not config.allowed_user_ids:
        return True
    return user_id in config.allowed_user_ids


def _login_keyboard(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔐 Open AWS login", url=url)],
            [InlineKeyboardButton(text="✅ I approved it", callback_data="approved")],
            [InlineKeyboardButton(text="✖️ Cancel", callback_data="cancel")],
        ]
    )


@router.message(Command("start", "login"))
async def cmd_start(message: Message, services: Services) -> None:
    user = message.from_user
    if not _allowed(services.config, user.id if user else None):
        await message.answer("⛔ You are not authorised to use this bot.")
        return

    chat_id = message.chat.id
    if chat_id in _active_logins:
        await message.answer("A login is already in progress for this chat. Finish or cancel it first.")
        return

    _active_logins.add(chat_id)
    try:
        await _run_login_flow(message, services.config)
    except Exception:  # noqa: BLE001 - surface any unexpected failure to the user
        logger.exception("Login flow crashed")
        await message.answer("❌ Something went wrong during login. Please try /login again.")
    finally:
        _active_logins.discard(chat_id)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "This bot logs you in to AWS Builder ID / IAM Identity Center using the "
        "official device-code flow and returns a kiro-account-lite JSON file.\n\n"
        f"{hbold('Commands')}\n"
        "/login or /start — begin a new login\n"
        "/help — show this message\n\n"
        "You will get a link and a one-time code. Open the link, sign in, and "
        "approve. The bot then sends back your export file.",
    )


@router.callback_query(F.data == "cancel")
async def on_cancel(callback: CallbackQuery) -> None:
    await callback.answer("Cancelled.")
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)


async def _run_login_flow(message: Message, config: Config) -> None:
    status = await message.answer("🔧 Registering OIDC client with AWS…")

    async with aiohttp.ClientSession() as http, AwsOidcClient(
        region=config.region, session=http
    ) as oidc:
        # 1. Register a public client.
        try:
            client = await oidc.register_client(config.client_name)
        except OidcError as err:
            await status.edit_text(f"❌ register_client failed: {hcode(str(err))}")
            return

        # 2. Start device authorization.
        try:
            device = await oidc.start_device_authorization(client, config.start_url)
        except OidcError as err:
            await status.edit_text(f"❌ start_device_authorization failed: {hcode(str(err))}")
            return

        link = hlink("Open AWS login page", device.verification_uri_complete)
        await status.edit_text(
            "🔐 " + hbold("Sign in to AWS") + "\n\n"
            f"1. {link}\n"
            f"2. Confirm this code is shown: {hcode(device.user_code)}\n"
            "3. Sign in and click " + hbold("Allow") + ".\n\n"
            f"Fallback URL: {hcode(device.verification_uri)}\n"
            f"This code expires in about {device.expires_in // 60} minutes.",
            reply_markup=_login_keyboard(device.verification_uri_complete),
            disable_web_page_preview=True,
        )

        # 3. Poll create_token until approved / expired.
        token = await _poll_for_token(oidc, client, device, status)
        if token is None:
            return

        await status.edit_text("✅ Authorised. Building your export file…", reply_markup=None)

        document = build_export(config, client, token)
        data = export_to_bytes(document)
        filename = f"kiro-account-{int(time.time() * 1000)}.json"

        account = document["accounts"][0]
        caption = (
            "✅ " + hbold("Login complete") + "\n"
            f"Email: {hcode(account['email'])}\n"
            f"Region: {hcode(config.region)}\n"
            f"Token valid for ~{token.expires_in // 60} min."
        )
        await message.answer_document(
            BufferedInputFile(data, filename=filename),
            caption=caption,
        )


async def _poll_for_token(
    oidc: AwsOidcClient,
    client: RegisteredClient,
    device: DeviceAuthorization,
    status: Message,
):
    interval = max(device.interval, 1)
    deadline = time.monotonic() + device.expires_in

    while time.monotonic() < deadline:
        await asyncio.sleep(interval)
        try:
            return await oidc.create_token(client, device.device_code)
        except AuthorizationPending:
            continue  # user hasn't approved yet
        except SlowDown:
            interval += 5  # back off as instructed by AWS
            continue
        except OidcError as err:
            if err.error in ("expired_token", "ExpiredTokenException"):
                await status.edit_text(
                    "⌛ The code expired before you approved it. Run /login to try again.",
                    reply_markup=None,
                )
            elif err.error in ("access_denied", "AccessDeniedException"):
                await status.edit_text(
                    "🚫 Authorization was denied. Run /login to try again.",
                    reply_markup=None,
                )
            else:
                await status.edit_text(
                    f"❌ Token request failed: {hcode(str(err))}",
                    reply_markup=None,
                )
            return None

    await status.edit_text(
        "⌛ Timed out waiting for approval. Run /login to try again.",
        reply_markup=None,
    )
    return None


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = Config.from_env()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)

    # Inject shared services into every handler via workflow data.
    services = Services(config=config)

    logger.info("Starting bot (region=%s, start_url=%s)", config.region, config.start_url)
    await dp.start_polling(bot, services=services)


if __name__ == "__main__":
    asyncio.run(main())
