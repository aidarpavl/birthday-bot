"""Страница Streamlit: напоминания о днях рождения и статус поздравлений."""
from __future__ import annotations

import hmac
import os

import streamlit as st

import core
import telegram_bot
from channels import Result, send_telegram

CHANNEL_NAMES = {"sms": "SMS", "telegram": "Telegram", "instagram": "Instagram"}
STATUS_NAMES = {
    "sent": "✅ отправлено",
    "failed": "❌ ошибка, будет повтор",
    "not_found": "⚠️ недоступен (заблокировал бота или нет в сервисе)",
    "no_recipient": "➖ нет контакта",
    "uncertain": "⚠️ нет ответа сервиса, проверьте вручную",
}


def secret(name: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(name, default)).strip()
    except Exception:  # файла с секретами нет
        return default


def require_password() -> None:
    password = secret("APP_PASSWORD")
    if not password:
        st.error("Задайте APP_PASSWORD в настройках приложения: на странице личные данные.")
        st.stop()
    if st.session_state.get("authorized"):
        return
    entered = st.text_input("Пароль", type="password")
    if entered and hmac.compare_digest(entered.encode(), password.encode()):
        st.session_state["authorized"] = True
        st.rerun()
    if entered:
        st.error("Неверный пароль")
    st.stop()


@st.cache_data(ttl=300, show_spinner="Читаю таблицу…")
def load(sheet_id: str, gid: str):
    return core.parse_people(core.load_sheet_csv(sheet_id, gid))


@st.cache_data(ttl=3600, show_spinner=False)
def bot_username(_token: str) -> str:
    return telegram_bot.call("getMe")["username"]


def bot_panel(people: list[core.Person], token: str) -> None:
    st.subheader("Telegram-бот")
    if not token:
        st.info("Создайте бота в @BotFather и добавьте его токен в Secrets: TELEGRAM_BOT_TOKEN.")
        return
    try:
        username = bot_username(token)
    except telegram_bot.TelegramApiError:
        st.error("Telegram не принял TELEGRAM_BOT_TOKEN. Скопируйте токен из @BotFather заново.")
        return
    except Exception as error:
        st.warning(f"Не удалось связаться с Telegram: {type(error).__name__}")
        return
    registry = telegram_bot.read_registry()
    waiting = [p for p in people if not telegram_bot.chat_id_for(p, registry)]
    st.write(f"Подключились: **{len(people) - len(waiting)} из {len(people)}**. "
             "Бот может написать только тем, кто один раз нажал Start.")
    st.write("Отправьте людям эту ссылку:")
    st.code(f"https://t.me/{username}", language=None)
    if waiting:
        with st.expander(f"Ещё не подключились: {len(waiting)}"):
            for person in waiting:
                st.write(person.name)


def telegram_buttons(person: core.Person, text: str, token: str, sent: bool) -> None:
    key = f"telegram_{person.number}"
    result = st.session_state.get(key)
    if token:
        chat_id = telegram_bot.chat_id_for(person, telegram_bot.read_registry())
        done = sent or bool(result and result.status != "failed")
        if st.button("✈️ Отправить через бота", key=f"{key}_button", disabled=done or not chat_id):
            with st.spinner("Отправляю…"):
                try:
                    result = send_telegram(person, text)
                except Exception as error:  # только тип: в тексте может быть токен
                    result = Result("failed", type(error).__name__)
            st.session_state[key] = result
        if result:
            label = "❌ ошибка" if result.status == "failed" else STATUS_NAMES.get(result.status, result.status)
            st.write(f"Отправка через бота: {label} {result.detail}".rstrip())
        elif not chat_id:
            st.caption("Кнопка заработает, когда человек нажмёт Start у бота.")
        elif not done:
            st.caption("Рассылка отправляет сама каждый час с 09:17. Кнопка нужна, только если она ещё не запускалась.")
    link = core.telegram_link(person)
    if link:
        st.link_button("Открыть чат в Telegram с готовым текстом", link)


def show_today(person: core.Person, today, log: list[dict[str, str]], token: str) -> None:
    st.success(f"🎉 **{person.name}** — сегодня день рождения, {core.age_on(person, today)} лет")
    rows = {r["channel"]: r for r in log
            if r["date"] == today.isoformat() and r["person"] == person.number}
    for channel, title in CHANNEL_NAMES.items():
        row = rows.get(channel)
        if row:
            status = STATUS_NAMES.get(row["status"], row["status"])
        elif channel == "telegram" and token and not telegram_bot.chat_id_for(person, telegram_bot.read_registry()):
            status = "⏳ ждём, пока человек нажмёт Start у бота"
        else:
            status = "⏳ ещё не отправлялось"
        st.write(f"{title}: {status}")
    text = core.greeting_text(person)
    st.code(text, language=None, wrap_lines=True)
    telegram_row = rows.get("telegram")
    telegram_buttons(person, text, token, bool(telegram_row and telegram_row["status"] == "sent"))
    if person.instagram:
        st.link_button(f"Открыть чат в Instagram: @{person.instagram}",
                       f"https://ig.me/m/{person.instagram}")


def main() -> None:
    st.set_page_config(page_title="Туған күндер", page_icon="🎂")
    st.title("🎂 Туған күндер")
    require_password()

    sheet_id = secret("SHEET_ID")
    if not sheet_id:
        st.error("Задайте SHEET_ID в настройках приложения.")
        st.stop()
    token = secret("TELEGRAM_BOT_TOKEN")
    os.environ["SHEET_ID"] = sheet_id  # ключ для списка подключившихся к боту
    if token:
        os.environ["TELEGRAM_BOT_TOKEN"] = token
    try:
        people, issues = load(sheet_id, secret("SHEET_GID", "0") or "0")
    except Exception as error:
        st.error(f"Не удалось прочитать таблицу: {error}")
        st.stop()

    today = core.today_local()
    tomorrow = core.tomorrow_of(today)
    st.caption(f"Сегодня {today:%d.%m.%Y}, людей в таблице: {len(people)}")

    st.subheader("Завтра")
    tomorrow_people = core.birthdays_on(people, tomorrow)
    for person in tomorrow_people:
        st.warning(f"🔔 Завтра, {tomorrow:%d.%m}, день рождения у **{person.name}** "
                   f"({core.age_on(person, tomorrow)} лет)")
    if not tomorrow_people:
        st.write("Завтра дней рождения нет.")

    st.subheader("Сегодня")
    today_people = core.birthdays_on(people, today)
    log = core.read_log()
    for person in today_people:
        show_today(person, today, log, token)
    if not today_people:
        st.write("Сегодня дней рождения нет.")

    st.subheader("Ближайшие 30 дней")
    soon = [(d, p) for d, p in core.upcoming(people, today, 30) if d > tomorrow]
    if soon:
        st.dataframe(
            [{"Дата": f"{d:%d.%m}", "ФИО": p.name, "Исполнится": core.age_on(p, d)}
             for d, p in soon],
            hide_index=True,
        )
    else:
        st.write("Других дней рождения в ближайшие 30 дней нет.")

    bot_panel(people, token)

    if issues:
        with st.expander(f"Замечания к таблице: {len(issues)}"):
            for issue in issues:
                st.write(issue)


main()
