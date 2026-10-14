import datetime

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

import database as db


def fmt_rupiah(n):
    """Format angka jadi Rp X.XXX"""
    if n is None:
        return "-"
    return f"Rp {n:,}".replace(",", ".")


async def cmd_tarif(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /tarif <biaya>
    Contoh: /tarif 1000
    """
    user = update.effective_user

    if not ctx.args:
        await update.message.reply_text(
            "📊 <b>Input Biaya Layanan ShopeeFood</b>\n\n"
            "Format: <code>/tarif &lt;angka&gt;</code>\n\n"
            "Contoh:\n"
            "<code>/tarif 1000</code>\n"
            "<code>/tarif 5000</code>",
            parse_mode=ParseMode.HTML
        )
        return

    try:
        biaya = int(ctx.args[0].replace(".", "").replace(",", ""))
    except ValueError:
        await update.message.reply_text("❌ Format salah. Contoh: <code>/tarif 1000</code>", parse_mode=ParseMode.HTML)
        return

    if biaya < 0 or biaya > 1000000:
        await update.message.reply_text("❌ Biaya tidak wajar. Range: Rp 0 - Rp 1.000.000")
        return

    # Simpan ke DB
    db.tarif_add(user.id, biaya)

    # Cek rata-rata jam sekarang
    jam = datetime.datetime.now().hour
    avg, count = db.tarif_get_avg_for_hour(jam)

    if avg and count >= 2:
        threshold_murah = avg * 0.7
        threshold_mahal = avg * 1.3

        if biaya <= threshold_murah:
            kategori = "🔥 SANGAT MURAH"
            emoji = "🎉"
            pesan = "Waktu yang tepat untuk pesan!"
        elif biaya < avg:
            kategori = "✅ MURAH"
            emoji = "👍"
            pesan = "Tarif di bawah rata-rata."
        elif biaya >= threshold_mahal:
            kategori = "⚠️ MAHAL"
            emoji = "❌"
            pesan = "Tunggu jam murah untuk pesan."
        else:
            kategori = "⚪ NORMAL"
            emoji = "ℹ️"
            pesan = "Tarif normal."

        await update.message.reply_text(
            f"{emoji} <b>Tarif Dicatat!</b>\n\n"
            f"💰 Biaya: <b>{fmt_rupiah(biaya)}</b>\n"
            f"⏰ Jam: {jam:02d}:00\n"
            f"📈 Rata-rata jam ini: {fmt_rupiah(avg)}\n"
            f"📌 Kategori: <b>{kategori}</b>\n\n"
            f"{pesan}",
            parse_mode=ParseMode.HTML
        )
    else:
        await update.message.reply_text(
            f"✅ <b>Tarif Dicatat!</b>\n\n"
            f"💰 Biaya: <b>{fmt_rupiah(biaya)}</b>\n"
            f"⏰ Jam: {jam:02d}:00\n\n"
            f"📊 Data jam ini belum cukup untuk analisa.\n"
            f"Minimal 2 input untuk hitung rata-rata.",
            parse_mode=ParseMode.HTML
        )


async def cmd_tarif_cek(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /tarif_cek
    Cek status tarif sekarang berdasarkan input terakhir.
    """
    latest = db.tarif_get_latest(user_id=update.effective_user.id)

    if not latest:
        await update.message.reply_text(
            "📊 Belum ada data tarif.\n\n"
            "Kirim <code>/tarif &lt;biaya&gt;</code> dulu.\n"
            "Contoh: <code>/tarif 1000</code>",
            parse_mode=ParseMode.HTML
        )
        return

    biaya = latest["biaya"]
    jam = datetime.datetime.now().hour
    avg, count = db.tarif_get_avg_for_hour(jam)

    now_ts = int(datetime.datetime.now().timestamp())
    age_min = (now_ts - latest["timestamp"]) // 60

    text = (
        "📊 <b>STATUS BIAYA LAYANAN SHOPEEFOOD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ Waktu : {datetime.datetime.now().strftime('%H:%M')} WIB\n"
        f"💰 Biaya Terakhir : <b>{fmt_rupiah(biaya)}</b>\n"
        f"⏱️ Umur data : {age_min} menit lalu\n\n"
    )

    if avg and count >= 2:
        text += f"📈 Rata-rata jam {jam:02d}:00 : {fmt_rupiah(avg)}\n"

        threshold_murah = avg * 0.7
        threshold_mahal = avg * 1.3

        if biaya <= threshold_murah:
            kategori = "🔥 SANGAT MURAH"
            pesan = "✅ Saat ini tarif murah. Waktu yang tepat untuk pesan!"
        elif biaya < avg:
            kategori = "✅ MURAH"
            pesan = "👍 Tarif di bawah rata-rata."
        elif biaya >= threshold_mahal:
            kategori = "⚠️ MAHAL"
            pesan = "❌ Tarif mahal. Tunggu jam murah untuk pesan."
        else:
            kategori = "⚪ NORMAL"
            pesan = "ℹ️ Tarif normal. Tunggu waktu murah untuk hemat."

        diff_pct = int((biaya - avg) / avg * 100) if avg > 0 else 0
        text += f"📌 Kategori : <b>{kategori}</b>\n"
        text += f"📉 Selisih : {diff_pct:+d}% vs rata-rata\n\n"
        text += pesan
    else:
        text += "📊 Data historis jam ini belum cukup.\n"
        text += "Kirim lebih banyak <code>/tarif</code> untuk analisa."

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_tarif_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /tarif_stats
    Statistik lengkap.
    """
    s = db.tarif_get_stats()

    if s["total"] == 0:
        await update.message.reply_text(
            "📊 Belum ada data tarif.\n\n"
            "Kirim <code>/tarif &lt;biaya&gt;</code> dulu.",
            parse_mode=ParseMode.HTML
        )
        return

    text = "📊 <b>STATISTIK BIAYA LAYANAN SHOPEEFOOD</b>\n"
    text += "━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📈 Total data : {s['total']} input\n"
    text += f"💰 Rata-rata : {fmt_rupiah(s['avg'])}\n"
    text += f"🟢 Terendah : {fmt_rupiah(s['min'])}\n"
    text += f"🔴 Tertinggi : {fmt_rupiah(s['max'])}\n\n"

    if s["cheapest"]:
        text += "🏆 <b>JAM TERMURAH:</b>\n"
        for h in s["cheapest"]:
            text += f"   • {h['jam']:02d}:00 → {fmt_rupiah(int(h['avg_b']))} ({h['cnt']}x)\n"
        text += "\n"

    if s["expensive"]:
        text += "⚠️ <b>JAM TERMAHAL:</b>\n"
        for h in s["expensive"]:
            text += f"   • {h['jam']:02d}:00 → {fmt_rupiah(int(h['avg_b']))} ({h['cnt']}x)\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)