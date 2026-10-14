import datetime

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db


def ts_to_wib(ts):
    """Convert unix timestamp ke WIB string."""
    if not ts:
        return "-"
    try:
        dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone(datetime.timedelta(hours=7)))
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return "-"


async def cmd_sfood_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """List semua user yang pakai ShopeeFood Tracker."""
    admin_ids = ctx.bot_data.get("admin_ids", [])
    if update.effective_user.id not in admin_ids:
        await update.message.reply_text("❌ Cuma admin yang bisa akses.")
        return

    users = db.sfood_list_all_users()
    if not users:
        await update.message.reply_text("📋 Belum ada user yang pakai ShopeeFood Tracker.")
        return

    global_stats = db.sfood_get_global_stats()

    lines = [
        f"👥 <b>Users ShopeeFood Tracker</b>",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Total user: <b>{global_stats['total_users']}</b>",
        f"Total order: <b>{global_stats['total_orders']}</b>",
        f"Aktif: {global_stats['active_orders']} | Selesai: {global_stats['completed_orders']}",
        "",
        f"<b>Daftar User:</b>",
    ]

    for i, u in enumerate(users, 1):
        uname = f"@{u['username']}" if u['username'] else "(no username)"
        name = u['first_name'] or "(no name)"
        last = ts_to_wib(u['last_order_at'])

        lines.append(
            f"\n{i}. <b>{name}</b> {uname}\n"
            f"   🆔 <code>{u['user_id']}</code>\n"
            f"   📦 Total: {u['total_orders']} | Aktif: {u['active_orders']}\n"
            f"   🕐 Terakhir: {last}"
        )

    text = "\n".join(lines)

    if len(text) > 4000:
        chunks = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 4000:
                chunks.append(current)
                current = line + "\n"
            else:
                current += line + "\n"
        if current:
            chunks.append(current)
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_sfood_leaderboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Leaderboard user ShopeeFood."""
    admin_ids = ctx.bot_data.get("admin_ids", [])
    if update.effective_user.id not in admin_ids:
        await update.message.reply_text("❌ Cuma admin yang bisa akses.")
        return

    board = db.sfood_get_leaderboard(limit=20)
    if not board:
        await update.message.reply_text("📋 Belum ada data leaderboard.")
        return

    lines = [
        "🏆 <b>LEADERBOARD SHOPEEFOOD</b>",
        "━━━━━━━━━━━━━━━━━━━━",
        "Top 20 user paling banyak pesan:",
        "",
    ]

    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(board, 1):
        uname = f"@{u['username']}" if u['username'] else "(no username)"
        name = u['first_name'] or "(no name)"

        if i <= 3:
            prefix = medals[i-1]
        else:
            prefix = f"{i}."

        lines.append(
            f"{prefix} <b>{name}</b> {uname}\n"
            f"   🆔 <code>{u['user_id']}</code>\n"
            f"   📦 Total: <b>{u['total_orders']}</b> order\n"
        )

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_sfood_userinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Info detail 1 user ShopeeFood."""
    admin_ids = ctx.bot_data.get("admin_ids", [])
    if update.effective_user.id not in admin_ids:
        await update.message.reply_text("❌ Cuma admin yang bisa akses.")
        return

    if not ctx.args:
        await update.message.reply_text(
            "Usage: <code>/sfood_userinfo &lt;user_id&gt;</code>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        user_id = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID harus angka.")
        return

    u = db.get_user(user_id)
    if not u:
        await update.message.reply_text(f"❌ User <code>{user_id}</code> tidak ditemukan.", parse_mode=ParseMode.HTML)
        return

    history = db.sfood_get_user_history(user_id, limit=20)

    uname = f"@{u['username']}" if u['username'] else "(no username)"
    name = u['first_name'] or "(no name)"

    text = (
        f"👤 <b>Detail User ShopeeFood</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"User ID: <code>{u['user_id']}</code>\n"
        f"Username: {uname}\n"
        f"Nama: {name}\n"
        f"Kuota cek Kopi Kenangan: {u['quota']}x\n\n"
    )

    if history:
        active = [h for h in history if h['active'] == 1]
        text += f"📦 <b>Order ({len(history)} total, {len(active)} aktif):</b>\n\n"
        for h in history[:10]:
            status_icon = "🟢" if h['active'] == 1 else "⚪"
            text += (
                f"{status_icon} <code>{h['order_id']}</code>\n"
                f"   📦 {h['label']}\n"
                f"   Status: {h['last_status_text'] or '-'}\n"
                f"   🕐 {ts_to_wib(h['created_at'])}\n\n"
            )
        if len(history) > 10:
            text += f"... dan {len(history) - 10} order lainnya\n"
    else:
        text += "📦 Belum ada order ShopeeFood."

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)