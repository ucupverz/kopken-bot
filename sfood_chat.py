"""
Chat dengan kurir ShopeeFood lewat Telegram, diproses lewat API shptools.

- Pesan kurir diteruskan ke Telegram oleh tracker_worker (lihat sfood_tracker.py).
- User membalas dengan: reply ke pesan kurir, atau `/chat <pesan>`, atau `/chat <order_id> <pesan>`.
"""

import re
from html import escape

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationHandlerStop, ContextTypes

import os

import database as db
import sfood_shptools
from sfood_shptools import chat_send

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

ORDER_ID_RE = re.compile(r"Order ID: (\d+)")


def _active_shptools_orders(user_id):
    """Semua order aktif user. Chat lewat API shptools memakai order_id yang sama,
    jadi berlaku juga untuk order yang dilacak dari link shopeefood.co.id
    (selama order itu ada di akun shptools yang dipakai bot)."""
    return list(db.sfood_get_orders_by_user(user_id))


async def _send(update: Update, order_id: str, text: str):
    ok = await chat_send(order_id, text)
    if ok:
        await update.message.reply_text(
            f"✅ Terkirim ke kurir (order <code>{order_id}</code>)", parse_mode=ParseMode.HTML
        )
    else:
        text = (
            "❌ Gagal kirim chat ke kurir.\n"
            "Kemungkinan: kurir belum ter-assign, order tidak ada di akun shptools bot, "
            "atau login shptools bot habis."
        )
        if update.effective_user.id in ADMIN_IDS and sfood_shptools.LAST_ERROR:
            text += f"\n\nDetail (admin): {sfood_shptools.LAST_ERROR}"
        await update.message.reply_text(text)


async def cmd_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "Kirim chat ke kurir:\n"
            "<code>/chat pesan kamu</code>\n"
            "<code>/chat &lt;order_id&gt; pesan kamu</code>\n"
            "Atau reply langsung ke pesan kurir.",
            parse_mode=ParseMode.HTML,
        )
        return

    orders = _active_shptools_orders(user.id)
    if not orders:
        await update.message.reply_text("❌ Tidak ada order aktif.")
        return

    order_id = None
    if args[0].isdigit() and any(o["order_id"] == args[0] for o in orders):
        order_id = args[0]
        text = " ".join(args[1:]).strip()
    else:
        text = " ".join(args).strip()
        if len(orders) > 1:
            ids = "\n".join(f"<code>{o['order_id']}</code> — {escape(str(o['label'] or '-'))}" for o in orders)
            await update.message.reply_text(
                "Kamu punya lebih dari 1 order aktif. Pakai:\n"
                "<code>/chat &lt;order_id&gt; pesan</code>\n\n" + ids,
                parse_mode=ParseMode.HTML,
            )
            return
        order_id = orders[0]["order_id"]

    if not text:
        await update.message.reply_text("❌ Pesan kosong.")
        return
    await _send(update, order_id, text)


async def handle_chat_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Reply ke pesan kurir (yang memuat 'Order ID: ...') -> kirim ke kurir."""
    msg = update.message
    replied = msg.reply_to_message if msg else None
    if not replied or not replied.text:
        return
    m = ORDER_ID_RE.search(replied.text)
    if not m or "KURIR" not in replied.text:
        return

    order_id = m.group(1)
    user = update.effective_user
    if not any(o["order_id"] == order_id for o in _active_shptools_orders(user.id)):
        await msg.reply_text("❌ Order itu sudah selesai atau bukan milikmu.")
        raise ApplicationHandlerStop
    await _send(update, order_id, (msg.text or "").strip())
    raise ApplicationHandlerStop
