from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db
from sfood_client import parse_tracker_url, fetch_tracking, parse_tracking


def sfood_keyboard():
    return ReplyKeyboardMarkup([
        ["🍔 Tambah Order", "📋 List Order"],
        ["❌ Stop Order", "🔙 Menu Utama"],
    ], resize_keyboard=True)


async def cmd_sfood(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🍔 <b>ShopeeFood Tracker</b>\n\n"
        "Aku bisa lacak pesanan ShopeeFood kamu realtime.\n\n"
        "<b>Cara pakai:</b>\n"
        "1. Buka app ShopeeFood → halaman tracking order\n"
        "2. Tap <b>Bagikan</b> → Copy link\n"
        "3. Kirim link ke bot ini\n\n"
        "<b>Format:</b>\n"
        "<code>&lt;URL&gt; &lt;label opsional&gt;</code>\n\n"
        "<b>Contoh:</b>\n"
        "<code>https://www.shopeefood.co.id/tracker?code=xxx&amp;orderId=yyy</code>\n"
        "atau\n"
        "<code>https://www.shopeefood.co.id/tracker?code=xxx&amp;orderId=yyy Corndog siang</code>\n\n"
        "<b>Commands:</b>\n"
        "/sfood — Menu ini\n"
        "/sfood_list — List order aktif\n"
        "/sfood_stop &lt;order_id&gt; — Stop 1 order\n"
        "/sfood_stopall — Stop semua order"
    )
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=sfood_keyboard()
    )


async def cmd_sfood_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    orders = db.sfood_get_orders_by_user(user.id)

    if not orders:
        await update.message.reply_text(
            "📋 Belum ada order yang dilacak.\n\n"
            "Kirim link tracker ShopeeFood untuk mulai."
        )
        return

    lines = [f"📋 <b>Order Aktif ({len(orders)})</b>\n"]
    for o in orders:
        lines.append(
            f"• <code>{o['order_id']}</code>\n"
            f"  📦 {o['label']}\n"
            f"  Status: {o['last_status_text'] or '-'}\n"
            f"  ETA: {o['last_eta'] or '-'}\n"
        )
    lines.append("\nStop 1 order: <code>/sfood_stop &lt;order_id&gt;</code>")
    lines.append("Stop semua: <code>/sfood_stopall</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_sfood_stop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not ctx.args:
        await update.message.reply_text(
            "Usage: <code>/sfood_stop &lt;order_id&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return
    order_id = ctx.args[0]
    ok = db.sfood_stop_order(order_id, user_id=user.id)
    if ok:
        await update.message.reply_text(
            f"✅ Order <code>{order_id}</code> dihentikan.",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            f"❌ Order <code>{order_id}</code> tidak ditemukan atau sudah stop.",
            parse_mode=ParseMode.HTML
        )


async def cmd_sfood_stopall(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    count = db.sfood_stop_all_user(user.id)
    await update.message.reply_text(f"✅ {count} order dihentikan.")


async def handle_sfood_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user

    if "shopeefood.co.id/tracker" not in text:
        return

    parts = text.split(maxsplit=1)
    url = parts[0]
    label = parts[1].strip() if len(parts) > 1 else None

    order_id, code = parse_tracker_url(url)
    if not order_id or not code:
        await update.message.reply_text(
            "❌ Link tidak valid. Pastikan link dari tombol <b>Bagikan</b> di app ShopeeFood.",
            parse_mode=ParseMode.HTML
        )
        return

    if db.sfood_order_exists(order_id):
        await update.message.reply_text(
            f"⚠️ Order <code>{order_id}</code> sudah dilacak.",
            parse_mode=ParseMode.HTML
        )
        return

    msg = await update.message.reply_text("🔍 Verifikasi link...")
    data = await fetch_tracking(order_id, code)
    if not data:
        await msg.edit_text("❌ Gagal akses link tracker. Pastikan link masih valid.")
        return

    parsed = parse_tracking(data)
    if not parsed:
        await msg.edit_text("❌ Tidak bisa baca data tracking.")
        return

    if not label:
        label = parsed["store_name"]

    db.sfood_add_order(
        user_id=user.id,
        order_id=order_id,
        code=code,
        label=label,
        last_status=parsed["status_code"],
        last_status_text=parsed["status_text"],
        last_eta=parsed["eta_text"],
        last_driver=parsed["driver_name"],
        last_driver_phone=parsed["driver_phone"],
    )

    await msg.edit_text(
        f"✅ <b>Order dilacak!</b>\n\n"
        f"📦 Label: {label}\n"
        f"🏪 Restoran: {parsed['store_name']}\n"
        f"🚗 Status: {parsed['status_text']}\n"
        f"⏱️ ETA: {parsed['eta_text']}\n"
        f"🧑 Driver: {parsed['driver_name']}\n"
        f"📞 HP: <code>{parsed['driver_phone']}</code>\n"
        f"🏍️ Plat: {parsed['driver_plate']}\n\n"
        f"Aku akan kirim notif setiap ada perubahan status.\n"
        f"Order ID: <code>{order_id}</code>",
        parse_mode=ParseMode.HTML
    )
    print(f"[SFOOD] ✅ New order tracked: {order_id} by user {user.id}")