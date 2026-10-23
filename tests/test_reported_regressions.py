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


def test_qris_provider_finds_delayed_custom_reply_among_new_messages():
    class Button:
        text = "Custom Nominal"

        def __init__(self):
            self.clicked = False

        async def click(self):
            self.clicked = True

    button = Button()
    unrelated = SimpleNamespace(id=11, out=False, buttons=None)
    reply = SimpleNamespace(id=12, out=False, buttons=[[button]])
    qris = SimpleNamespace(id=14, out=False, photo=object(), text="QRIS caption")

    class Client:
        def __init__(self, *args):
            self.responses = [[], [unrelated], [reply], [qris]]
            self.request_ids = []
            self.sent = []

        async def connect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def send_message(self, peer, text):
            self.sent.append(text)
            return SimpleNamespace(id=len(self.sent), out=True)

        async def get_messages(self, peer, **kwargs):
            self.request_ids.append(kwargs["min_id"])
            return self.responses.pop(0)

        async def download_media(self, message, as_bytes):
            return b"stub qris image"

        async def disconnect(self):
            pass

    client = Client()
    with patch.object(dehuyz_automation, "API_ID", 1), \
         patch.object(dehuyz_automation, "API_HASH", "stub"), \
         patch.object(dehuyz_automation, "TG_SESSION_STR", ""), \
         patch.object(dehuyz_automation, "TelegramClient", return_value=client), \
         patch.object(dehuyz_automation.asyncio, "sleep", new=AsyncMock()):
        status, result = run(dehuyz_automation.request_qris(20000))

    assert status == "success"
    assert result == {
        "image": b"stub qris image",
        "caption": "QRIS caption",
        "msg_id": 14,
    }
    assert client.sent == ["/topup", "20000"]
    assert button.clicked
    assert client.request_ids[:3] == [1, 1, 1]
    assert client.request_ids[-1] == 2


def test_qris_requests_serialize_shared_dehuyz_conversation():
    events = []

    async def fake_request(amount):
        events.append(("start", amount))
        await asyncio.sleep(0)
        events.append(("finish", amount))
        return "success", amount

    async def scenario():
        with patch.object(dehuyz_automation, "_request_qris", new=fake_request):
            return await asyncio.gather(
                dehuyz_automation.request_qris(5000),
                dehuyz_automation.request_qris(20000),
            )

    assert run(scenario()) == [("success", 5000), ("success", 20000)]
    assert events in (
        [("start", 5000), ("finish", 5000), ("start", 20000), ("finish", 20000)],
        [("start", 20000), ("finish", 20000), ("start", 5000), ("finish", 5000)],
    )


def test_payment_watcher_detects_provider_edit_to_original_qris_message():
    pending = SimpleNamespace(id=500, text="QRIS Rp 20.000", photo=object())
    paid = SimpleNamespace(id=500, text="Pembayaran berhasil, saldo Rp 20.000", photo=object())

    class Client:
        def __init__(self, *args):
            self.original_reads = 0

        async def connect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def get_messages(self, peer, **kwargs):
            if "ids" in kwargs:
                self.original_reads += 1
                return pending if self.original_reads == 1 else paid
            return []

        async def disconnect(self):
            pass

    client = Client()
    with patch.object(dehuyz_automation, "API_ID", 1), \
         patch.object(dehuyz_automation, "API_HASH", "stub"), \
         patch.object(dehuyz_automation, "TelegramClient", return_value=client), \
         patch.object(dehuyz_automation.asyncio, "sleep", new=AsyncMock()):
        result = run(dehuyz_automation.wait_for_payment(20000, 500))

    assert result == ("success", 500)
    assert client.original_reads == 2


def test_sqlite_qris_payment_credit_is_atomic_and_idempotent(tmp_path):
    database_path = tmp_path / "qris.sqlite3"
    with patch.dict(os.environ, {"SQLITE_DB_PATH": str(database_path)}), \
         patch.object(database, "DATABASE_URL", None):
        database.init_db()
        database.create_user(123, "tester", "Test")

        assert database.add_qris_saldo_once(123, 20000, 9001, "Topup QRIS Rp 20,000")
        assert not database.add_qris_saldo_once(123, 20000, 9001, "Topup QRIS Rp 20,000")

        assert database.get_user(123)["saldo"] == 20000
        assert len(database.get_user_transactions(123)) == 1


def test_confirmed_deposit_uses_receipt_id_and_only_notifies_once(tmp_path):
    database_path = tmp_path / "deposit.sqlite3"

    class Bot:
        def __init__(self):
            self.photos = 0
            self.captions = []
            self.messages = []

        async def send_photo(self, **kwargs):
            self.photos += 1
            return SimpleNamespace(message_id=700 + self.photos)

        async def edit_message_caption(self, **kwargs):
            self.captions.append(kwargs["caption"])

        async def send_message(self, chat_id, text, **kwargs):
            self.messages.append(text)

    async def scenario():
        bot = Bot()
        ctx = SimpleNamespace(user_data={})
        tasks = []
        original_create_task = asyncio.create_task

        def capture_task(coro):
            task = original_create_task(coro)
            tasks.append(task)
            return task

        with patch.dict(os.environ, {"SQLITE_DB_PATH": str(database_path)}), \
             patch.object(database, "DATABASE_URL", None), \
             patch.object(otp_handlers.db, "has_whatsapp_access", return_value=False), \
             patch.object(otp_handlers.dehuyz_automation, "request_qris", new=AsyncMock(
                 side_effect=[
                     ("success", {"image": b"qris", "caption": "QRIS", "msg_id": 500}),
                     ("success", {"image": b"qris", "caption": "QRIS", "msg_id": 501}),
                 ]
             )), \
             patch.object(otp_handlers.dehuyz_automation, "wait_for_payment",
                          new=AsyncMock(return_value=("success", 9001))), \
             patch.object(otp_handlers.asyncio, "create_task", side_effect=capture_task):
            database.init_db()
            database.create_user(123, "tester", "Test")
            for _ in range(2):
                await otp_handlers.process_qris_payment(bot, 123, 20000, ctx)
                await asyncio.gather(*tasks)
                tasks.clear()
            saldo = database.get_user(123)["saldo"]
            transaction_count = len(database.get_user_transactions(123))
        return bot, saldo, transaction_count

    bot, saldo, transaction_count = run(scenario())
    assert saldo == 20000
    assert transaction_count == 1
    assert sum("Deposit Berhasil" in text for text in bot.messages) == 1
    assert any("sudah diproses sebelumnya" in caption for caption in bot.captions)


def test_qris_provider_bounds_stalled_disconnect_cleanup():
    class Client:
        async def connect(self):
            raise RuntimeError("local stub connect failure")

        async def disconnect(self):
            await asyncio.Event().wait()

    client = Client()
    with patch.object(dehuyz_automation, "API_ID", 1), \
         patch.object(dehuyz_automation, "API_HASH", "stub"), \
         patch.object(dehuyz_automation, "TelegramClient", return_value=client), \
         patch.object(dehuyz_automation, "TELEGRAM_DISCONNECT_TIMEOUT", 0.01):
        status, detail = run(dehuyz_automation.request_qris(20000))

    assert status == "error"
    assert "local stub connect failure" in detail


def test_qris_provider_reports_missing_topup_response_after_bounded_wait():
    class Client:
        async def connect(self):
            pass

        async def is_user_authorized(self):
            return True

        async def send_message(self, peer, text):
            return SimpleNamespace(id=1, out=True)

        async def get_messages(self, peer, **kwargs):
            return []

        async def disconnect(self):
            pass

    with patch.object(dehuyz_automation, "API_ID", 1), \
         patch.object(dehuyz_automation, "API_HASH", "stub"), \
         patch.object(dehuyz_automation, "TelegramClient", return_value=Client()), \
         patch.object(dehuyz_automation, "DEHUYZ_REPLY_TIMEOUT", 0.01), \
         patch.object(dehuyz_automation, "DEHUYZ_POLL_INTERVAL", 0.002):
        status, detail = run(dehuyz_automation.request_qris(20000))

    assert status == "error"
    assert "tidak menampilkan tombol Custom/Nominal" in detail


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
