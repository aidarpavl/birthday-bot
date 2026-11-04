"""Каналы отправки. Канал без настроек просто не запускается."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Callable

import requests

import telegram_bot
from core import Person


@dataclass(frozen=True)
class Result:
    status: str  # sent | failed | waiting | not_found | no_recipient | uncertain
    detail: str = ""


def send_sms(person: Person, text: str) -> Result:
    if not person.phone:
        return Result("no_recipient", "нет телефона")
    base = os.environ.get("MOBIZON_API_URL", "https://api.mobizon.kz").rstrip("/")
    payload = {"apiKey": os.environ["MOBIZON_API_KEY"], "recipient": person.phone, "text": text}
    sender = os.environ.get("MOBIZON_SENDER", "").strip()
    if sender:
        payload["from"] = sender
    try:
        response = requests.post(
            f"{base}/service/Message/SendSmsMessage",
            params={"output": "json", "api": "v1"}, data=payload, timeout=30,
        )
    except requests.Timeout:
        return Result("uncertain", "Mobizon не ответил вовремя")
    except requests.RequestException as error:
        return Result("failed", type(error).__name__)  # в тексте ошибки мог быть ключ
    try:
        body = response.json()
    except ValueError:
        return Result("failed", f"HTTP {response.status_code}")
    if body.get("code") == 0:
        return Result("sent", str((body.get("data") or {}).get("messageId", "")))
    return Result("failed", f"код {body.get('code')}: {body.get('message', '')}")


def send_telegram(person: Person, text: str) -> Result:
    if not person.telegram and not person.phone:
        return Result("no_recipient", "нет логина и телефона")
    chat_id = telegram_bot.chat_id_for(person, telegram_bot.read_registry())
    if not chat_id:
        return Result("waiting", "человек ещё не нажал Start у бота")
    try:
        telegram_bot.call("sendMessage", chat_id=chat_id, text=text)
    except TimeoutError:
        return Result("uncertain", "Telegram не ответил вовремя")
    except telegram_bot.TelegramApiError as error:
        if error.code in (400, 403):  # заблокировал бота или удалил аккаунт
            return Result("not_found", error.description)
        return Result("failed", f"код {error.code}: {error.description}")
    except telegram_bot.TelegramNetworkError as error:
        return Result("failed", str(error))
    return Result("sent")


def send_instagram(person: Person, text: str) -> Result:
    if not person.instagram:
        return Result("no_recipient", "нет логина")
    from instagrapi import Client

    client = Client()
    client.set_settings(json.loads(os.environ["INSTAGRAM_SESSION"]))
    try:
        user_id = client.user_id_from_username(person.instagram)
        client.direct_send(text, user_ids=[int(user_id)])
    except Exception as error:  # у библиотеки десятки своих исключений
        return Result("failed", type(error).__name__)
    return Result("sent")


CHANNELS: dict[str, tuple[Callable[[Person, str], Result], tuple[str, ...]]] = {
    "sms": (send_sms, ("MOBIZON_API_KEY",)),
    "telegram": (send_telegram, ("TELEGRAM_BOT_TOKEN",)),
    "instagram": (send_instagram, ("INSTAGRAM_SESSION",)),
}


def configured_channels() -> dict[str, Callable[[Person, str], Result]]:
    return {
        name: sender for name, (sender, required) in CHANNELS.items()
        if all(os.environ.get(key, "").strip() for key in required)
    }

