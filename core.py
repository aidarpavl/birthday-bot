"""Общая логика: чтение таблицы, сравнение дат, текст поздравления, журнал."""
from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

TIMEZONE = ZoneInfo("Asia/Qyzylorda")
GREETING_SUFFIX = "сізді туған күніңізбен құттықтаймыз!!!"
LOG_PATH = Path(__file__).with_name("sent_log.csv")
LOG_FIELDS = ["date", "person", "channel", "status", "detail"]

# Статусы, после которых повторно отправлять нельзя.
# uncertain: сервис не ответил вовремя, сообщение могло уйти — повтор дал бы дубль.
FINAL_STATUSES = {"sent", "not_found", "no_recipient", "uncertain"}
MAX_ATTEMPTS_PER_DAY = 3


@dataclass(frozen=True)
class Person:
    number: str
    name: str
    birth_date: date
    phone: str | None  # формат 7XXXXXXXXXX
    instagram: str | None
    telegram: str | None = None


def today_local() -> date:
    return datetime.now(TIMEZONE).date()


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits[0] in "78":
        return "7" + digits[1:]
    return None


def normalize_instagram(raw: str) -> str | None:
    value = (raw or "").strip()
    value = re.sub(r"^(https?://)?(www\.)?instagram\.com/", "", value, flags=re.I)
    value = value.split("?")[0].strip("/").lstrip("@")
    return value if re.fullmatch(r"[A-Za-z0-9._]{1,30}", value) else None


def normalize_telegram(raw: str) -> str | None:
    value = (raw or "").strip()
    value = re.sub(r"^(https?://)?(www\.)?(t|telegram)\.me/", "", value, flags=re.I)
    value = value.split("?")[0].strip("/").lstrip("@")
    return value if re.fullmatch(r"[A-Za-z0-9_]{4,32}", value) else None


def parse_people(csv_text: str) -> tuple[list[Person], list[str]]:
    """Столбцы по порядку: A №, B ФИО, C дата рождения, D телефон, E логин Instagram, F логин Telegram.

    Возвращает людей и список замечаний к таблице.
    """
    people: list[Person] = []
    issues: list[str] = []
    rows = list(csv.reader(io.StringIO(csv_text)))
    for line, row in enumerate(rows[1:], start=2):
        row = [cell.strip() for cell in row] + [""] * 6
        number, name, raw_date, raw_phone, raw_instagram, raw_telegram = row[:6]
        if not name:
            continue
        number = number or f"строка {line}"
        try:
            birth_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
        except ValueError:
            issues.append(f"№{number} {name}: неверная дата «{raw_date}» — человек пропущен")
            continue
        phone = normalize_phone(raw_phone)
        if not phone:
            detail = f"неверный телефон «{raw_phone}»" if raw_phone else "нет телефона"
            issues.append(f"№{number} {name}: {detail}")
        instagram = normalize_instagram(raw_instagram)
        if raw_instagram and not instagram:
            issues.append(f"№{number} {name}: неверный логин Instagram «{raw_instagram}»")
        telegram = normalize_telegram(raw_telegram)
        if raw_telegram and not telegram:
            issues.append(f"№{number} {name}: неверный логин Telegram «{raw_telegram}»")
        people.append(Person(number, name, birth_date, phone, instagram, telegram))
    return people, issues


def load_sheet_csv(sheet_id: str, gid: str = "0") -> str:
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export"
    response = requests.get(url, params={"format": "csv", "gid": gid}, timeout=30)
    response.raise_for_status()
    if "text/csv" not in response.headers.get("Content-Type", ""):
        raise RuntimeError(
            "Google вернул не таблицу. Проверьте доступ: "
            "«Все, у кого есть ссылка — Читатель»."
        )
    response.encoding = "utf-8"
    return response.text


def birthday_in_year(birth_date: date, year: int) -> date:
    try:
        return birth_date.replace(year=year)
    except ValueError:  # 29 февраля в невисокосный год отмечаем 28-го
        return date(year, 2, 28)


def is_birthday(person: Person, day: date) -> bool:
    return birthday_in_year(person.birth_date, day.year) == day


def birthdays_on(people: list[Person], day: date) -> list[Person]:
    return [p for p in people if is_birthday(p, day)]


def tomorrow_of(day: date) -> date:
    return day + timedelta(days=1)


def age_on(person: Person, day: date) -> int:
    return day.year - person.birth_date.year


def upcoming(people: list[Person], start: date, days: int) -> list[tuple[date, Person]]:
    found = []
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        found.extend((day, p) for p in birthdays_on(people, day))
    return found


def greeting_text(person: Person) -> str:
    return f"{person.name} {GREETING_SUFFIX}"


def telegram_link(person: Person) -> str | None:
    """Ссылка открывает чат с готовым текстом. Ключи не нужны. Логин важнее телефона."""
    target = person.telegram or (f"+{person.phone}" if person.phone else None)
    if not target:
        return None
    return f"https://t.me/{target}?text={quote(greeting_text(person))}"


def read_log(path: Path = LOG_PATH) -> list[dict[str, str]]:
    """В журнале только № из таблицы: ни имён, ни телефонов в репозиторий не попадает."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def append_log(day: date, person: Person, channel: str, status: str, detail: str = "",
               path: Path = LOG_PATH) -> None:
    is_new = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, LOG_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerow({"date": day.isoformat(), "person": person.number, "channel": channel,
                         "status": status, "detail": detail[:200]})


def already_done(log: list[dict[str, str]], day: date, person: Person, channel: str) -> bool:
    rows = [row for row in log
            if row["date"] == day.isoformat() and row["person"] == person.number
            and row["channel"] == channel]
    return len(rows) >= MAX_ATTEMPTS_PER_DAY or any(r["status"] in FINAL_STATUSES for r in rows)


def sheet_settings_from_env() -> tuple[str, str]:
    sheet_id = os.environ.get("SHEET_ID", "").strip()
    if not sheet_id:
        raise RuntimeError("Не задан SHEET_ID")
    return sheet_id, os.environ.get("SHEET_GID", "0").strip() or "0"
