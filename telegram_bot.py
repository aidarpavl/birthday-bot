"""Telegram-бот: кто подключился к боту и как ему написать.

Бот не может написать человеку первым и не находит людей по номеру. Человек должен
один раз нажать Start. Подключение проверяется по логину из столбца F или по номеру
из столбца D, который человек отправляет боту кнопкой.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import os
from pathlib import Path

import requests

import core

REGISTRY_PATH = Path(__file__).with_name("telegram_users.csv")

CONNECTED_TEXT = "✅ Сіз қосылдыңыз! Туған күніңізде осында құттықтау аласыз.\n\nВы подключены."
ASK_PHONE_TEXT = ("Төмендегі батырма арқылы нөміріңізді жіберіңіз.\n\n"
                  "Отправьте свой номер кнопкой ниже.")
UNKNOWN_TEXT = "Бұл нөмір тізімде жоқ.\n\nЭтого номера нет в списке."
PHONE_KEYBOARD = {
    "keyboard": [[{"text": "📱 Нөмірді жіберу / Отправить номер", "request_contact": True}]],
    "resize_keyboard": True,
    "one_time_keyboard": True,
}


class TelegramApiError(Exception):
    def __init__(self, code: int | None, description: str):
        super().__init__(description)
        self.code = code
        self.description = description


class TelegramNetworkError(Exception):
    """Без текста исходной ошибки: в нём есть адрес запроса вместе с токеном."""


def call(method: str, **params):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}"
    try:
        response = requests.post(url, json=params, timeout=30)
    except requests.Timeout:
        raise TimeoutError("Telegram не ответил вовремя") from None
    except requests.RequestException as error:
        raise TelegramNetworkError(type(error).__name__) from None
    try:
        body = response.json()
    except ValueError:
        raise TelegramNetworkError(f"HTTP {response.status_code}") from None
    if not body.get("ok"):
        raise TelegramApiError(body.get("error_code"), str(body.get("description", "")))
    return body["result"]


def _key(kind: str, value: str) -> str:
    """В репозитории нет ни телефонов, ни логинов: только ключи от них."""
    secret = os.environ["SHEET_ID"].strip().encode()
    return hmac.new(secret, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:32]


def person_keys(person: core.Person) -> list[str]:
    keys = []
    if person.phone:
        keys.append(_key("phone", person.phone))
    if person.telegram:
        keys.append(_key("login", person.telegram.lower()))
    return keys


def read_registry(path: Path = REGISTRY_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        return {row["key"]: row["chat_id"] for row in csv.DictReader(handle)}


def write_registry(registry: dict[str, str], path: Path = REGISTRY_PATH) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["key", "chat_id"])
        writer.writerows(sorted(registry.items()))


def chat_id_for(person: core.Person, registry: dict[str, str]) -> str | None:
    return next((registry[key] for key in person_keys(person) if key in registry), None)


def _reply(chat_id: int, text: str, keyboard: dict | None = None) -> None:
    markup = keyboard or {"remove_keyboard": True}
    try:
        call("sendMessage", chat_id=chat_id, text=text, reply_markup=markup)
    except (TelegramApiError, TelegramNetworkError, TimeoutError):
        pass  # один недоступный человек не должен мешать остальным


def _match(message: dict, people: list[core.Person]) -> core.Person | None:
    sender = message.get("from") or {}
    contact = message.get("contact")
    # Принимаем только собственный номер: чужой контакт переслать нельзя.
    if contact and contact.get("user_id") == sender.get("id"):
        phone = core.normalize_phone(contact.get("phone_number", ""))
        found = next((p for p in people if phone and p.phone == phone), None)
        if found:
            return found
    username = (sender.get("username") or "").lower()
    if username:
        return next((p for p in people if p.telegram and p.telegram.lower() == username), None)
    return None


def sync_updates(people: list[core.Person], path: Path = REGISTRY_PATH) -> int:
    """Обрабатывает новые сообщения боту. Возвращает число новых подключений."""
    updates = call("getUpdates", timeout=0, allowed_updates=["message"])
    registry = read_registry(path)
    added = 0
    for update in updates:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            continue
        person = _match(message, people)
        if person:
            is_new = _is_new(person, chat["id"], registry)
            for key in person_keys(person):
                registry[key] = str(chat["id"])
            added += is_new
            _reply(chat["id"], CONNECTED_TEXT)
        elif message.get("contact"):
            _reply(chat["id"], UNKNOWN_TEXT)
        else:
            _reply(chat["id"], ASK_PHONE_TEXT, PHONE_KEYBOARD)
    if added:
        write_registry(registry, path)
    if updates:  # подтверждаем, только когда всё сохранено
        call("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0, limit=1)
    return added


def _is_new(person: core.Person, chat_id: int, registry: dict[str, str]) -> bool:
    return chat_id_for(person, registry) != str(chat_id)
