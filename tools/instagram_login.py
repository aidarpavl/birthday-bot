"""Один раз запустить на своём компьютере: выводит INSTAGRAM_SESSION.

Неофициальный способ, читайте раздел Instagram в README. Пароль нигде не сохраняется.
Строка даёт доступ к аккаунту. Храните её только в секретах GitHub.
"""
import getpass
import json

from instagrapi import Client

client = Client()
username = input("Логин Instagram: ").strip()
password = getpass.getpass("Пароль: ")
code = input("Код двухфакторной защиты (Enter, если нет): ").strip()
client.login(username, password, verification_code=code)
print("\nINSTAGRAM_SESSION:\n" + json.dumps(client.get_settings()))
