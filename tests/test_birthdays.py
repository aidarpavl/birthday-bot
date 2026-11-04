import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import channels  # noqa: E402
import core  # noqa: E402
import send_greetings  # noqa: E402
import telegram_bot  # noqa: E402
from channels import Result  # noqa: E402

CSV = (
    "№,Аты-жөні,Туған жылы,телефон,логин инстаграмм\n"
    "1,Тест Бір,04.11.1987,87001112233,@test_one\n"
    "2,Тест Екі,05.11.1977,+7 (700) 111-22-44,\n"
    "3,Тест Үш,01.01.2000,,\n"
    "4,Тест Төрт,31.12.1999,123,bad login!\n"
    "5,Тест Бес,29.02.2000,87001112255,\n"
    "6,Тест Алты,нет даты,87001112266,\n"
)


@pytest.fixture
def people():
    return core.parse_people(CSV)[0]


def recording_sender(calls, status="sent"):
    def sender(person, text):
        calls.append((person.number, text))
        return Result(status)
    return sender


def test_parsing_and_issues():
    people, issues = core.parse_people(CSV)
    assert [p.number for p in people] == ["1", "2", "3", "4", "5"]
    assert people[0].phone == "77001112233" and people[0].instagram == "test_one"
    assert people[1].phone == "77001112244"
    assert people[3].phone is None and people[3].instagram is None
    assert any("нет телефона" in i for i in issues)
    assert any("неверная дата" in i for i in issues)
    assert any("неверный логин" in i for i in issues)


@pytest.mark.parametrize("day,expected", [
    (date(2026, 11, 4), ["1"]),
    (date(2026, 11, 5), ["2"]),
    (date(2027, 1, 1), ["3"]),
    (date(2026, 2, 28), ["5"]),   # невисокосный год
    (date(2028, 2, 29), ["5"]),   # високосный
    (date(2028, 2, 28), []),
])
def test_birthdays_ignore_birth_year(people, day, expected):
    assert [p.number for p in core.birthdays_on(people, day)] == expected


def test_tomorrow_crosses_new_year(people):
    tomorrow = core.tomorrow_of(date(2026, 12, 31))
    assert [p.number for p in core.birthdays_on(people, tomorrow)] == ["3"]


def test_greeting_text(people):
    text = core.greeting_text(people[0])
    assert text == "Тест Бір сізді туған күніңізбен құттықтаймыз!!!"
    assert not any("a" <= c.lower() <= "z" for c in text)


def test_sends_only_today_and_never_twice(people, tmp_path):
    log, calls = tmp_path / "log.csv", []
    senders = {"sms": recording_sender(calls)}
    day = date(2026, 11, 4)
    assert send_greetings.run(people, day, senders, log_path=log) == 0
    assert send_greetings.run(people, day, senders, log_path=log) == 0
    assert calls == [("1", "Тест Бір сізді туған күніңізбен құттықтаймыз!!!")]
    # Через год снова поздравляем.
    send_greetings.run(people, date(2027, 11, 4), senders, log_path=log)
    assert len(calls) == 2


def test_failed_is_retried_but_uncertain_is_not(people, tmp_path):
    log, day = tmp_path / "log.csv", date(2026, 11, 4)
    failed, ok = [], []
    assert send_greetings.run(people, day, {"sms": recording_sender(failed, "failed")}, log_path=log) == 1
    send_greetings.run(people, day, {"sms": recording_sender(ok)}, log_path=log)
    assert len(ok) == 1

    log2, unsure, again = tmp_path / "log2.csv", [], []
    send_greetings.run(people, day, {"sms": recording_sender(unsure, "uncertain")}, log_path=log2)
    send_greetings.run(people, day, {"sms": recording_sender(again)}, log_path=log2)
    assert again == []


def test_one_channel_crash_does_not_stop_others(people, tmp_path):
    calls = []
    def broken(person, text):
        raise OSError("boom")
    failures = send_greetings.run(people, date(2026, 11, 4),
                                  {"telegram": broken, "sms": recording_sender(calls)},
                                  log_path=tmp_path / "log.csv")
    assert failures == 1 and len(calls) == 1


def test_dry_run_sends_and_logs_nothing(people, tmp_path):
    log, calls = tmp_path / "log.csv", []
    send_greetings.run(people, date(2026, 11, 4), {"sms": recording_sender(calls)},
                       dry_run=True, log_path=log)
    assert calls == [] and not log.exists()


def test_limit_blocks_mass_send(tmp_path):
    rows = "".join(f"{i},Тест {i},04.11.1990,8700111{i:04d},\n" for i in range(11))
    people, _ = core.parse_people("h\n" + rows)
    with pytest.raises(RuntimeError):
        send_greetings.run(people, date(2026, 11, 4), {"sms": recording_sender([])},
                           log_path=tmp_path / "log.csv")


def test_log_has_no_personal_data(people, tmp_path):
    log = tmp_path / "log.csv"
    send_greetings.run(people, date(2026, 11, 4), {"sms": recording_sender([])}, log_path=log)
    content = log.read_text(encoding="utf-8")
    assert "Тест" not in content and "7700" not in content


class FakeResponse:
    status_code = 200
    def __init__(self, body):
        self._body = body
    def json(self):
        return self._body


def test_mobizon_request_and_results(people, monkeypatch):
    monkeypatch.setenv("MOBIZON_API_KEY", "key")
    monkeypatch.setenv("MOBIZON_SENDER", "")
    seen = {}
    def fake_post(url, params, data, timeout):
        seen.update(url=url, params=params, data=data)
        return FakeResponse({"code": 0, "data": {"messageId": "42"}, "message": ""})
    monkeypatch.setattr(channels.requests, "post", fake_post)
    assert channels.send_sms(people[0], "текст") == Result("sent", "42")
    assert seen["url"] == "https://api.mobizon.kz/service/Message/SendSmsMessage"
    assert seen["data"] == {"apiKey": "key", "recipient": "77001112233", "text": "текст"}

    monkeypatch.setattr(channels.requests, "post",
                        lambda *a, **k: FakeResponse({"code": 2, "message": "нет денег"}))
    assert channels.send_sms(people[0], "т").status == "failed"

    def timeout(*a, **k):
        raise requests.Timeout()
    monkeypatch.setattr(channels.requests, "post", timeout)
    assert channels.send_sms(people[0], "т").status == "uncertain"
    assert channels.send_sms(people[2], "т").status == "no_recipient"


def test_channels_enabled_only_when_configured(monkeypatch):
    for key in ["MOBIZON_API_KEY", "TELEGRAM_BOT_TOKEN", "INSTAGRAM_SESSION"]:
        monkeypatch.delenv(key, raising=False)
    assert channels.configured_channels() == {}
    monkeypatch.setenv("MOBIZON_API_KEY", "x")
    assert list(channels.configured_channels()) == ["sms"]
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    assert list(channels.configured_channels()) == ["sms", "telegram"]


def test_telegram_link_opens_chat_with_encoded_text(people):
    link = core.telegram_link(people[0])
    assert link.startswith("https://t.me/+77001112233?text=")
    assert " " not in link
    assert unquote(link.split("text=")[1]) == core.greeting_text(people[0])
    assert core.telegram_link(people[2]) is None


def test_column_f_is_read():
    people, issues = core.parse_people(
        "h\n1,Тест,04.11.1990,87001112233,,@Good_Login\n2,Тест,04.11.1990,,,https://t.me/other_login\n"
        "3,Тест,04.11.1990,,,bad login\n"
    )
    assert [p.telegram for p in people] == ["Good_Login", "other_login", None]
    assert any("неверный логин Telegram" in i for i in issues)


def test_link_prefers_login(people):
    assert core.telegram_link(with_login(people[0], "good_login")).startswith("https://t.me/good_login?text=")


def with_login(person, login):
    return replace(person, telegram=login)


def test_link_prefers_login(people):
    assert core.telegram_link(with_login(people[0], "good_login")).startswith("https://t.me/good_login?text=")


class FakeBot:
    """Имитация Telegram: хранит очередь сообщений и всё, что бот отправил."""

    def __init__(self, monkeypatch, messages=()):
        self.updates = [{"update_id": i, "message": m} for i, m in enumerate(messages, 1)]
        self.sent, self.acked = [], None
        self.error = None
        monkeypatch.setenv("SHEET_ID", "secret-sheet")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TOKEN")
        monkeypatch.setattr(telegram_bot, "call", self)

    def __call__(self, method, **params):
        if method == "getUpdates":
            if "offset" in params:
                self.acked = params["offset"]
                return []
            return self.updates
        if self.error:
            raise self.error
        self.sent.append(params)
        return {}


def private(user_id, username=None, contact=None, text="/start"):
    message = {"chat": {"id": user_id, "type": "private"},
               "from": {"id": user_id, "username": username}, "text": text}
    if contact:
        message["contact"] = contact
    return message


def test_start_with_matching_login_connects(people, monkeypatch, tmp_path):
    path = tmp_path / "users.csv"
    people = [with_login(people[0], "Good_Login")] + people[1:]
    bot = FakeBot(monkeypatch, [private(100, username="good_login")])
    assert telegram_bot.sync_updates(people, path) == 1
    assert telegram_bot.chat_id_for(people[0], telegram_bot.read_registry(path)) == "100"
    assert bot.acked == 2
    assert bot.sent[0]["text"] == telegram_bot.CONNECTED_TEXT


def test_start_without_login_asks_for_phone(people, monkeypatch, tmp_path):
    bot = FakeBot(monkeypatch, [private(100, username="stranger")])
    assert telegram_bot.sync_updates(people, tmp_path / "users.csv") == 0
    assert bot.sent[0]["reply_markup"] == telegram_bot.PHONE_KEYBOARD


def test_own_contact_connects_by_phone(people, monkeypatch, tmp_path):
    path = tmp_path / "users.csv"
    contact = {"user_id": 100, "phone_number": "+77001112233"}
    FakeBot(monkeypatch, [private(100, contact=contact)])
    assert telegram_bot.sync_updates(people, path) == 1
    assert telegram_bot.chat_id_for(people[0], telegram_bot.read_registry(path)) == "100"


def test_someone_elses_contact_is_rejected(people, monkeypatch, tmp_path):
    path = tmp_path / "users.csv"
    contact = {"user_id": 999, "phone_number": "+77001112233"}
    FakeBot(monkeypatch, [private(100, contact=contact)])
    assert telegram_bot.sync_updates(people, path) == 0
    assert telegram_bot.chat_id_for(people[0], telegram_bot.read_registry(path)) is None


def test_unknown_phone_gets_explanation(people, monkeypatch, tmp_path):
    contact = {"user_id": 100, "phone_number": "+77770000000"}
    bot = FakeBot(monkeypatch, [private(100, contact=contact)])
    telegram_bot.sync_updates(people, tmp_path / "users.csv")
    assert bot.sent[0]["text"] == telegram_bot.UNKNOWN_TEXT


def test_registry_has_no_phone_or_login(people, monkeypatch, tmp_path):
    path = tmp_path / "users.csv"
    people = [with_login(people[0], "good_login")] + people[1:]
    FakeBot(monkeypatch, [private(100, username="good_login")])
    telegram_bot.sync_updates(people, path)
    content = path.read_text(encoding="utf-8")
    assert "7700" not in content and "good_login" not in content


def connect(person, path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "REGISTRY_PATH", path)
    telegram_bot.write_registry({key: "100" for key in telegram_bot.person_keys(person)}, path)
    monkeypatch.setattr(telegram_bot, "read_registry",
                        lambda p=path: {key: "100" for key in telegram_bot.person_keys(person)})


def test_bot_sends_greeting_to_connected_person(people, monkeypatch, tmp_path):
    bot = FakeBot(monkeypatch)
    connect(people[0], tmp_path / "users.csv", monkeypatch)
    assert channels.send_telegram(people[0], "т") == Result("sent")
    assert bot.sent == [{"chat_id": "100", "text": "т"}]


def test_not_started_waits_and_is_not_logged(people, monkeypatch, tmp_path):
    FakeBot(monkeypatch)
    monkeypatch.setattr(telegram_bot, "read_registry", lambda p=None: {})
    log = tmp_path / "log.csv"
    send_greetings.run(people, date(2026, 11, 4), {"telegram": channels.send_telegram}, log_path=log)
    assert not log.exists()
    # Нажал Start днём: при следующем запуске поздравление уходит.
    bot = FakeBot(monkeypatch)
    connect(people[0], tmp_path / "users.csv", monkeypatch)
    send_greetings.run(people, date(2026, 11, 4), {"telegram": channels.send_telegram}, log_path=log)
    assert len(bot.sent) == 1


@pytest.mark.parametrize("error,status", [
    (telegram_bot.TelegramApiError(403, "bot was blocked by the user"), "not_found"),
    (telegram_bot.TelegramApiError(429, "Too Many Requests"), "failed"),
    (telegram_bot.TelegramNetworkError("ConnectionError"), "failed"),
    (TimeoutError(), "uncertain"),
])
def test_bot_send_errors(people, monkeypatch, tmp_path, error, status):
    bot = FakeBot(monkeypatch)
    connect(people[0], tmp_path / "users.csv", monkeypatch)
    bot.error = error
    assert channels.send_telegram(people[0], "т").status == status


def test_retries_stop_after_three_failures(people, tmp_path):
    log, calls = tmp_path / "log.csv", []
    for _ in range(5):
        send_greetings.run(people, date(2026, 11, 4), {"sms": recording_sender(calls, "failed")}, log_path=log)
    assert len(calls) == 3


def test_token_never_leaks_in_network_errors(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "SECRET_TOKEN")
    def fail(url, **kwargs):
        raise requests.ConnectionError(f"failed: {url}")
    monkeypatch.setattr(telegram_bot.requests, "post", fail)
    with pytest.raises(telegram_bot.TelegramNetworkError) as caught:
        telegram_bot.call("getMe")
    assert "SECRET_TOKEN" not in str(caught.value)
    assert caught.value.__suppress_context__
