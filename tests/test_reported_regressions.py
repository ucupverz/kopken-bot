import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bot_checker
import database
import dehuyz_automation
import otp_handlers


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def run(coro):
    return asyncio.run(coro)


def test_shopeefood_reply_keyboard_labels_route_to_handlers():
    labels_and_handlers = (
        ("🍔 Tambah Order", "cmd_sfood"),
        ("📋 List Order", "cmd_sfood_list"),
        ("❌ Stop Order", "cmd_sfood_stop_prompt"),
    )
    for label, handler_name in labels_and_handlers:
        message = FakeMessage(label)
        user = SimpleNamespace(id=123, username="tester", first_name="Test")
        update = SimpleNamespace(
            message=message,
            effective_message=message,
            effective_user=user,
            effective_chat=SimpleNamespace(type="private"),
            callback_query=None,
        )
        ctx = SimpleNamespace(user_data={}, args=[])
        handler = AsyncMock()
        with patch.object(bot_checker.db, "get_user", return_value={"user_id": 123}), \
             patch.object(bot_checker, handler_name, handler):
            run(bot_checker.handle_message(update, ctx))
        handler.assert_awaited_once_with(update, ctx)
        assert message.replies == []


def test_qris_provider_connect_failure_is_returned_and_client_cleaned_up():
    class Client:
        def __init__(self, *args):
            self.disconnect_called = False

        async def connect(self):
            raise RuntimeError("local stub connect failure")

        async def disconnect(self):
            self.disconnect_called = True

    client = Client()
    with patch.object(dehuyz_automation, "API_ID", 1), \
         patch.object(dehuyz_automation, "API_HASH", "stub"), \
         patch.object(dehuyz_automation, "TelegramClient", return_value=client):
        status, detail = run(dehuyz_automation.request_qris(20000))
    assert status == "error"
    assert "local stub connect failure" in detail
    assert client.disconnect_called


def test_topup_provider_failure_updates_processing_message():
    status_message = SimpleNamespace(edit_text=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={})

    with patch.object(otp_handlers.dehuyz_automation, "request_qris",
                      new=AsyncMock(side_effect=RuntimeError("stub failure"))):
        run(otp_handlers.process_qris_payment(
            bot, 123, 20000, ctx, status_message=status_message
        ))

    status_message.edit_text.assert_awaited_once()
    assert "Gagal membuat QRIS" in status_message.edit_text.await_args.args[0]
    assert "stub failure" in status_message.edit_text.await_args.args[0]
    bot.send_message.assert_not_awaited()


def test_topup_provider_timeout_updates_processing_message():
    status_message = SimpleNamespace(edit_text=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock())
    ctx = SimpleNamespace(user_data={})

    with patch.object(otp_handlers.dehuyz_automation, "request_qris",
                      new=AsyncMock(side_effect=asyncio.TimeoutError)):
        run(otp_handlers.process_qris_payment(
            bot, 123, 20000, ctx, status_message=status_message
        ))

    assert "90 detik" in status_message.edit_text.await_args.args[0]
    bot.send_message.assert_not_awaited()


def test_sqlite_sfood_actions_are_user_scoped_and_init_preserves_existing_rows(tmp_path):
    database_path = tmp_path / "existing.sqlite3"
    with patch.dict(os.environ, {"SQLITE_DB_PATH": str(database_path)}), \
         patch.object(database, "DATABASE_URL", None):
        database.init_db()
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO users(user_id, username, first_name, saldo) VALUES(?,?,?,?)",
                (123, "tester", "Test", 45000),
            )
            conn.execute(
                "INSERT INTO otp_orders(order_id, user_id, status, otp) VALUES(?,?,?,?)",
                ("otp-1", 123, "waiting", "123456"),
            )

        database.init_db()
        assert database.get_user(123)["saldo"] == 45000
        assert database.get_otp_order("otp-1")["otp"] == "123456"

        assert database.sfood_add_order(
            123, "sf-1", "code", "Lunch", 10, "Dalam perjalanan", "15 menit",
            "Courier", "0812000000",
        )
        assert not database.sfood_add_order(456, "sf-1", "other", "Duplicate")
        assert [row["order_id"] for row in database.sfood_get_orders_by_user(123)] == ["sf-1"]
        assert database.sfood_get_orders_by_user(456) == []
        assert not database.sfood_stop_order("sf-1", user_id=456)
        assert database.sfood_stop_order("sf-1", user_id=123)
        assert database.sfood_get_orders_by_user(123) == []
        assert database.sfood_order_exists("sf-1")
