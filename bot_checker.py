import os
import asyncio
import logging
import random
import string
import re
import time
import httpx
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode

import database as db

# ShopeeFood Tracker
from sfood_handlers import (
    cmd_sfood, cmd_sfood_list, cmd_sfood_stop,
    cmd_sfood_stopall, handle_sfood_message
)
from sfood_tracker import tracker_worker

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
NASKY_API_KEY = os.getenv("NASKY_API_KEY", "")
NASKY_BASE = "https://nasky.web.id"

ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]

DEFAULT_VOC_QUOTA = int(os.getenv("DEFAULT_VOC_QUOTA", "3"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# HELPERS
# ----------------------------------------------------------------------

def is_admin(user_id):
    return user_id in ADMIN_IDS


def normalize_phone(text: str) -> str:
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if t.startswith("0"):
        t = "62" + t[1:]
    return t


def format_phone_display(phone_62: str) -> str:
    if phone_62.startswith("62"):
        return "0" + phone_62[2:]
    return phone_62


def is_valid_phone(text: str) -> bool:
    t = text.strip().replace("+", "").replace(" ", "").replace("-", "")
    if not t.isdigit():
        return False
    if t.startswith("62"):
        return 10 <= len(t) <= 15
    if t.startswith("0"):
        return 10 <= len(t) <= 14
    return False


def gen_voc_code():
    chars = string.ascii_uppercase + string.digits
    rand = ''.join(random.choices(chars, k=6))
    return f"VOC-{rand}"


def sanitize_voc_input(text):
    t = text.strip().upper().replace(" ", "")
    if not re.match(r"^[A-Z0-9_-]+$", t):
        return None
    if len(t) < 3 or len(t) > 50:
        return None
    return t


def main_keyboard():
    return ReplyKeyboardMarkup([
        ["☕ Cek Nomor", "📊 Kuota Saya"],
        ["🔑 Pakai Kode VOC", "🍔 ShopeeFood"],
        ["❓ Bantuan"]
    ], resize_keyboard=True)


def admin_keyboard():
    return ReplyKeyboardMarkup([
        ["☕ Cek Nomor", "📊 Kuota Saya"],
        ["🔑 Pakai Kode VOC", "🍔 ShopeeFood"],
        ["🔧 Admin Panel", "❓ Bantuan"]
    ], resize_keyboard=True)


# ----------------------------------------------------------------------
# NASKY API
# ----------------------------------------------------------------------

async def nasky_cek_single(nomor: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.post(
                f"{NASKY_BASE}/api/cek",
                headers={"X-API-Key": NASKY_API_KEY},
                json={"code": "kopken", "nomor": nomor}
            )
            return r.json()
        except Exception as e:
            return {"success": False, "detail": f"Network error: {e}"}


# ----------------------------------------------------------------------
# USER COMMANDS
# ----------------------------------------------------------------------

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.create_user(user.id, user.username or "", user.first_name or "")
    u = db.get_user(user.id)

    kb = admin_keyboard() if is_admin(user.id) else main_keyboard()
    text = (
        f"Halo {user.first_name}! 👋\n\n"
        f"☕ *Bot Multi Fungsi*\n\n"
        f"📊 Kuota kamu: *{u['quota']}x cek*\n\n"
        f"*Fitur:*\n"
        f"• ☕ Cek Nomor Kopi Kenangan\n"
        f"• 🍔 ShopeeFood Tracker\n"
        f"• 🔑 Redeem kode VOC\n\n"
        f"Ketik `/help` untuk bantuan lengkap."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Bantuan*\n\n"
        "*☕ Cek Nomor Kopi Kenangan:*\n"
        "• Kirim 1 nomor atau banyak nomor (pisah baris)\n"
        "• Kuota berkurang hanya kalau nomor FRESH\n\n"
        "*🍔 ShopeeFood Tracker:*\n"
        "• `/sfood` — Menu tracker\n"
        "• Kirim link tracker + label → bot lacak otomatis\n\n"
        "*🔑 Kode VOC:*\n"
        "• Kirim kode VOC untuk dapat kuota cek\n\n"
        "*Commands:*\n"
        "/start — Menu utama\n"
        "/kuota — Cek kuota\n"
        "/sfood — ShopeeFood Tracker\n"
        "/help — Bantuan ini"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_kuota(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u = db.get_user(user.id)
    if not u:
        db.create_user(user.id, user.username or "", user.first_name or "")
        u = db.get_user(user.id)
    await update.message.reply_text(
        f"📊 *Kuota Kamu*\n\n"
        f"Sisa kuota: *{u['quota']}x cek*\n"
        f"Total cek: {u['total_cek']}x",
        parse_mode=ParseMode.MARKDOWN
    )


# ----------------------------------------------------------------------
# HANDLE MESSAGE
# ----------------------------------------------------------------------

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user = update.effective_user

    if not db.get_user(user.id):
        db.create_user(user.id, user.username or "", user.first_name or "")

    u = db.get_user(user.id)

    # === CEK LINK SHOPEEFOOD DULU ===
    if "shopeefood.co.id/tracker" in text:
        await handle_sfood_message(update, ctx)
        return

    # Tombol menu
    if text == "☕ Cek Nomor":
        if u["quota"] <= 0:
            await update.message.reply_text(
                "❌ Kuota kamu habis.\n\n"
                "Kirim kode VOC untuk dapat kuota baru, atau minta ke admin."
            )
        else:
            await update.message.reply_text(
                "Kirim nomor HP yang mau dicek, contoh:\n`081234567890`\n\n"
                "Bisa banyak nomor sekaligus (pisah baris).",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    if text == "📊 Kuota Saya":
        await cmd_kuota(update, ctx)
        return

    if text == "🔑 Pakai Kode VOC":
        await update.message.reply_text(
            "Kirim kode VOC kamu.\n\n"
            "Format: `VOC-XXXXXX`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if text == "🍔 ShopeeFood":
        await cmd_sfood(update, ctx)
        return

    if text == "❓ Bantuan":
        await cmd_help(update, ctx)
        return

    if text == "🔧 Admin Panel" and is_admin(user.id):
        await cmd_admin(update, ctx)
        return

    # Cek kode VOC
    text_upper = text.upper()
    if len(text_upper) >= 3 and re.match(r"^[A-Z0-9_-]+$", text_upper) and db.get_voc(text_upper):
        success, message, quota = db.use_voc(text_upper, user.id)
        if success:
            await update.message.reply_text(
                f"✅ *{message}*\n\n"
                f"Sekarang kamu punya *{db.get_user(user.id)['quota']}x* kuota cek.",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(f"❌ {message}")
        return

    # Cek nomor HP
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # === SINGLE CHECK ===
    if len(lines) == 1 and is_valid_phone(lines[0]):
        if u["quota"] <= 0:
            await update.message.reply_text(
                "❌ Kuota kamu habis.\n\n"
                "Pakai kode VOC baru untuk dapat kuota."
            )
            return

        msg = await update.message.reply_text("🔍 Mengecek...")
        start_time = time.time()
        result = await nasky_cek_single(normalize_phone(lines[0]))
        elapsed = time.time() - start_time

        if not result.get("success"):
            await msg.edit_text(f"❌ Error: {result.get('detail', 'Unknown')}")
            return

        d = result["data"]
        status = d["status"]
        phone_display = format_phone_display(d["phone"])

        if status == "fresh":
            db.use_quota(user.id)
            db.add_transaction(user.id, "cek", 1, "success", f"FRESH {d['phone']}")
        else:
            db.add_transaction(user.id, "cek", 0, "success", f"{status} {d['phone']}")

        new_quota = db.get_user(user.id)["quota"]

        if status == "fresh":
            reply = (
                f"📊 HASIL PENGECEKAN NOMOR\n\n"
                f"❌ BELUM TERDAFTAR [1]:\n"
                f"{phone_display}\n\n"
                f"⏱️ Waktu Pengecekan: {elapsed:.2f} detik\n"
                f"💳 Sisa Kuota: {new_quota} Kredit"
            )
        elif status == "registered":
            reply = (
                f"📊 HASIL PENGECEKAN NOMOR\n\n"
                f"✅ TERDAFTAR [1]:\n"
                f"{phone_display} | {d.get('customer_name') or '-'}\n\n"
                f"⏱️ Waktu Pengecekan: {elapsed:.2f} detik\n"
                f"💳 Sisa Kuota: {new_quota} Kredit"
            )
        else:
            reply = f"❓ Status tidak dikenal: `{status}`"

        await msg.edit_text(reply, parse_mode=ParseMode.MARKDOWN)
        return

    # === BULK CHECK ===
    valid = [normalize_phone(l) for l in lines if is_valid_phone(l)]
    if valid:
        if u["quota"] < len(valid):
            await update.message.reply_text(
                f"❌ Kuota tidak cukup.\n\n"
                f"Butuh: {len(valid)}x (max)\n"
                f"Kuota kamu: {u['quota']}x\n\n"
                f"Catatan: kuota hanya berkurang untuk nomor FRESH.\n"
                f"Nomor TERDAFTAR tidak mengurangi kuota."
            )
            return

        msg = await update.message.reply_text(
            f"🔍 Mengecek {len(valid)} nomor...\nMohon tunggu."
        )

        start_time = time.time()
        fresh_list = []
        reg_list = []
        invalid_list = []

        for nomor in valid:
            r = await nasky_cek_single(nomor)
            if not r.get("success"):
                invalid_list.append(nomor)
                continue
            d = r["data"]
            phone_display = format_phone_display(d["phone"])
            if d["status"] == "fresh":
                fresh_list.append(phone_display)
                db.use_quota(user.id)
                db.add_transaction(user.id, "cek", 1, "success", f"FRESH {d['phone']}")
            elif d["status"] == "registered":
                reg_list.append((phone_display, d.get("customer_name") or "-"))
                db.add_transaction(user.id, "cek", 0, "success", f"REG {d['phone']}")
            else:
                invalid_list.append(phone_display)

        elapsed = time.time() - start_time
        new_quota = db.get_user(user.id)["quota"]

        out_lines = []
        out_lines.append("📊 HASIL PENGECEKAN NOMOR")
        out_lines.append("")

        if reg_list:
            out_lines.append(f"✅ TERDAFTAR [{len(reg_list)}]:")
            for ph, nm in reg_list:
                out_lines.append(f"{ph} | {nm}")
            out_lines.append("")

        if fresh_list:
            out_lines.append(f"❌ BELUM TERDAFTAR [{len(fresh_list)}]:")
            for ph in fresh_list:
                out_lines.append(ph)
            out_lines.append("")

        if invalid_list:
            out_lines.append(f"⚠️ INVALID [{len(invalid_list)}]:")
            for ph in invalid_list:
                out_lines.append(ph)
            out_lines.append("")

        out_lines.append(f"⏱️ Waktu Pengecekan: {elapsed:.2f} detik")
        out_lines.append(f"💳 Sisa Kuota: {new_quota} Kredit")

        text_output = "\n".join(out_lines)
        await msg.edit_text(text_output)

        file_out = ["=" * 40, "HASIL PENGECEKAN NOMOR", "=" * 40, ""]
        if reg_list:
            file_out.append(f"TERDAFTAR [{len(reg_list)}]:")
            for ph, nm in reg_list:
                file_out.append(f"{ph} | {nm}")
            file_out.append("")
        if fresh_list:
            file_out.append(f"BELUM TERDAFTAR [{len(fresh_list)}]:")
            for ph in fresh_list:
                file_out.append(ph)
            file_out.append("")
        if invalid_list:
            file_out.append(f"INVALID [{len(invalid_list)}]:")
            for ph in invalid_list:
                file_out.append(ph)
            file_out.append("")
        file_out.append(f"Waktu Pengecekan: {elapsed:.2f} detik")
        file_out.append(f"Sisa Kuota: {new_quota} Kredit")

        file_bytes = "\n".join(file_out).encode("utf-8")
        await update.message.reply_document(
            document=file_bytes,
            filename="hasil_cek.txt"
        )
        return

    await update.message.reply_text(
        "❌ Format tidak dikenali.\n\n"
        "Kirim nomor HP (contoh `081234567890`) atau kode VOC (contoh `VOC-A1B2C3`)."
    )


# ----------------------------------------------------------------------
# ADMIN COMMANDS
# ----------------------------------------------------------------------

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = db.get_stats()
    text = (
        f"🔧 *Admin Panel*\n\n"
        f"👥 Total users: {s['total_users']}\n"
        f"☕ Total cek: {s['total_cek']}\n"
        f"🔑 VOC aktif: {s['active_voc']}\n"
        f"🔑 Total VOC: {s['total_voc']}\n\n"
        f"*Generate VOC:*\n"
        f"`/genvoc` — acak, kuota default\n"
        f"`/genvoc 5` — acak, kuota 5x\n"
        f"`/genvoc ZEFAAN-VIP` — custom, kuota default\n"
        f"`/genvoc ZEFAAN-VIP 10` — custom, kuota 10x\n\n"
        f"*Lihat Data:*\n"
        f"`/users` — List semua user bot\n"
        f"`/userinfo <user_id>` — Detail user\n"
        f"`/vocdetail <kode>` — Detail kode VOC\n"
        f"`/listvoc` — 20 kode VOC terakhir\n"
        f"`/stats` — Statistik\n\n"
        f"*Kelola:*\n"
        f"`/revoke <kode>` — Nonaktifkan kode\n"
        f"`/addkuota <user_id> <n>` — Tambah kuota user"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_genvoc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    args = ctx.args or []

    if not args:
        for _ in range(10):
            code = gen_voc_code()
            if db.create_voc(code, DEFAULT_VOC_QUOTA, update.effective_user.id):
                await update.message.reply_text(
                    f"✅ *Kode VOC dibuat (acak)*\n\n"
                    f"`{code}`\n\n"
                    f"Kuota: *{DEFAULT_VOC_QUOTA}x cek*",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
        await update.message.reply_text("❌ Gagal generate kode, coba lagi.")
        return

    if len(args) == 1 and args[0].isdigit():
        quota = int(args[0])
        if quota < 1 or quota > 9999:
            await update.message.reply_text("❌ Kuota harus antara 1-9999.")
            return
        for _ in range(10):
            code = gen_voc_code()
            if db.create_voc(code, quota, update.effective_user.id):
                await update.message.reply_text(
                    f"✅ *Kode VOC dibuat (acak)*\n\n"
                    f"`{code}`\n\n"
                    f"Kuota: *{quota}x cek*",
                    parse_mode=ParseMode.MARKDOWN
                )
                return
        await update.message.reply_text("❌ Gagal generate kode, coba lagi.")
        return

    if len(args) == 1:
        custom_code = sanitize_voc_input(args[0])
        if not custom_code:
            await update.message.reply_text(
                "❌ Kode custom tidak valid.\n\n"
                "*Aturan:*\n"
                "• Hanya huruf, angka, `-`, `_`\n"
                "• Tidak boleh spasi\n"
                "• Panjang 3-50 karakter",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if db.create_voc(custom_code, DEFAULT_VOC_QUOTA, update.effective_user.id):
            await update.message.reply_text(
                f"✅ *Kode VOC custom dibuat!*\n\n"
                f"`{custom_code}`\n\n"
                f"Kuota: *{DEFAULT_VOC_QUOTA}x cek*",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                f"❌ Kode `{custom_code}` sudah ada di database.",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    if len(args) >= 2:
        custom_code = sanitize_voc_input(args[0])
        if not custom_code:
            await update.message.reply_text(
                "❌ Kode custom tidak valid.\n\n"
                "Format: `/genvoc <KODE> <KUOTA>`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        if not args[1].isdigit():
            await update.message.reply_text(
                "❌ Kuota harus angka. Contoh: `/genvoc ZEFAAN-VIP 10`",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        quota = int(args[1])
        if quota < 1 or quota > 9999:
            await update.message.reply_text("❌ Kuota harus antara 1-9999.")
            return

        if db.create_voc(custom_code, quota, update.effective_user.id):
            await update.message.reply_text(
                f"✅ *Kode VOC custom dibuat!*\n\n"
                f"`{custom_code}`\n\n"
                f"Kuota: *{quota}x cek*",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                f"❌ Kode `{custom_code}` sudah ada di database.",
                parse_mode=ParseMode.MARKDOWN
            )
        return


async def cmd_listvoc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    rows = db.list_voc_codes(20)
    if not rows:
        await update.message.reply_text("Belum ada kode VOC.")
        return
    lines = ["🔑 *20 Kode VOC Terakhir*\n"]
    for r in rows:
        status = "🟢 aktif" if r["active"] else "🔴 terpakai"
        lines.append(f"`{r['code']}` — {r['quota']}x — {status}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_revoke(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/revoke VOC-XXXXXX`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    code = ctx.args[0].upper()
    db.revoke_voc(code)
    await update.message.reply_text(
        f"✅ Kode `{code}` dinonaktifkan.",
        parse_mode=ParseMode.MARKDOWN
    )


async def cmd_addkuota(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "Usage: `/addkuota <user_id> <n>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    try:
        uid = int(ctx.args[0])
        amt = int(ctx.args[1])
    except ValueError:
        await update.message.reply_text("❌ Format salah.")
        return
    if not db.get_user(uid):
        await update.message.reply_text("❌ User tidak ditemukan.")
        return
    db.add_quota(uid, amt)
    new_q = db.get_user(uid)["quota"]
    await update.message.reply_text(
        f"✅ Kuota user `{uid}` +{amt}. Total: {new_q}x",
        parse_mode=ParseMode.MARKDOWN
    )
    try:
        await ctx.bot.send_message(uid, f"✅ Kuota kamu +{amt}. Sisa: {new_q}x cek")
    except Exception:
        pass


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    s = db.get_stats()
    text = (
        f"📊 *Statistik*\n\n"
        f"👥 Users: {s['total_users']}\n"
        f"☕ Total cek: {s['total_cek']}\n"
        f"🔑 VOC aktif: {s['active_voc']}\n"
        f"🔑 VOC total: {s['total_voc']}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    users = db.list_all_users(limit=50)
    if not users:
        await update.message.reply_text("Belum ada user.")
        return

    import datetime
    lines = [f"👥 *Total Users: {len(users)}*\n"]

    for u in users:
        uname = f"@{u['username']}" if u['username'] else "(no username)"
        name = u['first_name'] or "(no name)"
        dt = datetime.datetime.fromtimestamp(u['created_at']).strftime("%d/%m %H:%M")
        lines.append(
            f"• {name} {uname}\n"
            f"  ID: `{u['user_id']}` | Kuota: {u['quota']}x | Cek: {u['total_cek']}x | {dt}"
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
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_userinfo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/userinfo <user_id>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        uid = int(ctx.args[0])
    except ValueError:
        await update.message.reply_text("❌ User ID harus angka.")
        return

    user, vocs = db.get_user_detail(uid)
    if not user:
        await update.message.reply_text(
            f"❌ User `{uid}` tidak ditemukan.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    import datetime
    dt = datetime.datetime.fromtimestamp(user['created_at']).strftime("%d/%m/%Y %H:%M")
    uname = f"@{user['username']}" if user['username'] else "(no username)"

    text = (
        f"👤 *Detail User*\n\n"
        f"ID: `{user['user_id']}`\n"
        f"Username: {uname}\n"
        f"Nama: {user['first_name'] or '-'}\n\n"
        f"📊 Kuota sisa: *{user['quota']}x*\n"
        f"☕ Total cek: {user['total_cek']}x\n"
        f"📅 Daftar: {dt}\n\n"
    )

    if vocs:
        text += f"🔑 *Kode VOC yang dipakai ({len(vocs)}):*\n"
        for v in vocs:
            vdt = datetime.datetime.fromtimestamp(v['used_at']).strftime("%d/%m %H:%M") if v['used_at'] else "-"
            text += f"• `{v['code']}` — {v['quota']}x — {vdt}\n"
    else:
        text += "🔑 Belum pernah redeem kode VOC."

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_vocdetail(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not ctx.args:
        await update.message.reply_text(
            "Usage: `/vocdetail <kode>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    code = ctx.args[0].upper()
    voc, user = db.get_voc_detail(code)

    if not voc:
        await update.message.reply_text(
            f"❌ Kode `{code}` tidak ditemukan.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    import datetime
    dt = datetime.datetime.fromtimestamp(voc['created_at']).strftime("%d/%m/%Y %H:%M")

    text = (
        f"🔑 *Detail Kode VOC*\n\n"
        f"Kode: `{voc['code']}`\n"
        f"Kuota: {voc['quota']}x\n"
        f"Dibuat: {dt}\n"
        f"Status: {'🟢 Aktif' if voc['active'] else '🔴 Terpakai'}\n\n"
    )

    if user:
        used_at = datetime.datetime.fromtimestamp(voc['used_at']).strftime("%d/%m/%Y %H:%M") if voc['used_at'] else "-"
        uname = f"@{user['username']}" if user['username'] else "(no username)"
        text += (
            f"👤 *Dipakai oleh:*\n"
            f"Nama: {user['first_name'] or '-'}\n"
            f"Username: {uname}\n"
            f"User ID: `{user['user_id']}`\n"
            f"Redeem: {used_at}"
        )
    else:
        text += "👤 Belum dipakai siapa pun."

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Error: {ctx.error}", exc_info=ctx.error)


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN belum di-set")
        return
    if not NASKY_API_KEY:
        print("❌ NASKY_API_KEY belum di-set")
        return

    db.init_db()

    async def post_init(app):
        # Start ShopeeFood tracker worker
        asyncio.create_task(tracker_worker(app.bot))
        print("[SFOOD] Tracker worker started")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("kuota", cmd_kuota))

    # ShopeeFood
    app.add_handler(CommandHandler("sfood", cmd_sfood))
    app.add_handler(CommandHandler("sfood_list", cmd_sfood_list))
    app.add_handler(CommandHandler("sfood_stop", cmd_sfood_stop))
    app.add_handler(CommandHandler("sfood_stopall", cmd_sfood_stopall))

    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("genvoc", cmd_genvoc))
    app.add_handler(CommandHandler("listvoc", cmd_listvoc))
    app.add_handler(CommandHandler("revoke", cmd_revoke))
    app.add_handler(CommandHandler("addkuota", cmd_addkuota))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(CommandHandler("userinfo", cmd_userinfo))
    app.add_handler(CommandHandler("vocdetail", cmd_vocdetail))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_error_handler(error_handler)

    print("🤖 Bot jalan... Ctrl+C untuk stop")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()