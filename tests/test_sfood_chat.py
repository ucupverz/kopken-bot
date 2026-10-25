import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sfood_chat
import sfood_shptools
import sfood_tracker


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


def test_poll_chat_forwards_only_driver_and_advances_cursor(monkeypatch):
    sfood_tracker._chat_cursor.clear()
    calls = []

    async def fake_open(oid, did):
        return True

    async def fake_fetch(oid, after):
        calls.append(after)
        return [
            {"id": "1", "from": "driver", "type": "text", "text": "ok ka <3"},
            {"id": "2", "from": "buyer", "type": "text", "text": "halo"},
        ]

    monkeypatch.setattr(sfood_shptools, "chat_open", fake_open)
    monkeypatch.setattr(sfood_shptools, "chat_fetch", fake_fetch)
    bot = FakeBot()
    order = {"order_id": "99", "user_id": 5, "code": "shptools"}
    parsed = {"driver_id": "7", "driver_name": "HENDRA"}
    asyncio.run(sfood_tracker._poll_chat(bot, order, parsed))
    assert len(bot.sent) == 1
    assert "ok ka &lt;3" in bot.sent[0][1] and "Order ID: <code>99</code>" in bot.sent[0][1]
    assert sfood_tracker._chat_cursor["99"] == "2"
    asyncio.run(sfood_tracker._poll_chat(bot, order, parsed))
    assert calls == ["0", "2"]


def test_reply_regex_matches_forwarded_message():
    text = "💬 PESAN KURIR (HENDRA)\n\nok ka\n\nReply pesan ini untuk membalas.\nOrder ID: 3318571777395712141"
    assert sfood_chat.ORDER_ID_RE.search(text).group(1) == "3318571777395712141"
