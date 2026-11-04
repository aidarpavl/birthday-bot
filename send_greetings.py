"""Запускается каждый час из GitHub Actions: подключает людей к боту и поздравляет именинников."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Callable

import core
import telegram_bot
from channels import Result, configured_channels

Senders = dict[str, Callable[[core.Person, str], Result]]


def run(people: list[core.Person], day: date, senders: Senders, dry_run: bool = False,
        log_path: Path = core.LOG_PATH, limit: int = 10) -> int:
    """Возвращает число неудачных отправок."""
    celebrants = core.birthdays_on(people, day)
    print(f"{day}: именинников {len(celebrants)}; каналы: {', '.join(senders) or 'нет'}")
    if len(celebrants) > limit:
        # Таблицу может править кто угодно по ссылке: не тратим баланс на массовую рассылку.
        raise RuntimeError(f"Именинников {len(celebrants)} при лимите {limit}: отправка отменена")

    failures = 0
    log = core.read_log(log_path)
    for person in celebrants:
        text = core.greeting_text(person)
        for channel, sender in senders.items():
            label = f"№{person.number} {channel}"
            if core.already_done(log, day, person, channel):
                print(f"{label}: уже обработано")
                continue
            if dry_run:
                print(f"{label}: пробный режим, отправка пропущена. Текст: {text}")
                continue
            try:
                result = sender(person, text)
            except Exception as error:  # один канал не должен останавливать остальные
                result = Result("failed", type(error).__name__)
            if result.status == "waiting":  # проверим при следующем запуске, журнал не засоряем
                print(f"{label}: ожидание ({result.detail})")
                continue
            # Пишем сразу: при сбое дальше по списку повтор не пошлёт это сообщение ещё раз.
            core.append_log(day, person, channel, result.status, result.detail, log_path)
            print(f"{label}: {result.status} {result.detail}".rstrip())
            failures += result.status == "failed"
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="показать, но не отправлять")
    parser.add_argument("--date", help="ГГГГ-ММ-ДД, только вместе с --dry-run")
    args = parser.parse_args()
    if args.date and not args.dry_run:
        parser.error("--date работает только с --dry-run")

    day = date.fromisoformat(args.date) if args.date else core.today_local()
    people, issues = core.parse_people(core.load_sheet_csv(*core.sheet_settings_from_env()))
    if not people:
        raise RuntimeError("В таблице не найдено ни одного человека")
    print(f"Людей в таблице: {len(people)}, замечаний: {len(issues)}")
    if not args.dry_run and os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        try:
            print(f"Новых подключений к боту: {telegram_bot.sync_updates(people)}")
        except Exception as error:  # сбой бота не должен мешать SMS
            print(f"Бот: не удалось получить сообщения ({type(error).__name__})")
    limit = int(os.environ.get("MAX_RECIPIENTS_PER_DAY", "10"))
    failures = run(people, day, configured_channels(), args.dry_run, limit=limit)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
