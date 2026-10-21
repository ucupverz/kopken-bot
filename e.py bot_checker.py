warning: in the working copy of 'bot_checker.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'database.py', LF will be replaced by CRLF the next time Git touches it
warning: in the working copy of 'otp_handlers.py', LF will be replaced by CRLF the next time Git touches it
[1mdiff --git a/bot_checker.py b/bot_checker.py[m
[1mindex 6e00a48..5c4db68 100644[m
[1m--- a/bot_checker.py[m
[1m+++ b/bot_checker.py[m
[36m@@ -104,37 +104,43 @@[m [masync def bg_check_stuck_orders(app):[m
     while True:[m
         await asyncio.sleep(60)[m
         try:[m
[31m-            with db.get_conn() as conn:[m
[31m-                cursor = conn.execute("""[m
[31m-                    SELECT * FROM otp_orders [m
[31m-                    WHERE status = 'waiting' [m
[31m-                    AND otp IS NULL[m
[31m-                    AND created_at <= datetime('now', '-20 minutes')[m
[31m-                """)[m
[31m-                stuck_orders = cursor.fetchall()[m
[31m-                [m
[31m-                for order in stuck_orders:[m
[31m-                    oid = order['order_id'][m
[31m-                    uid = order['user_id'][m
[31m-                    price = order['price'][m
[31m-                    sname = order['service_name'][m
[31m-                    [m
[31m-                    print(f"[CRON] Refund otomatis order {oid} ({sname})")[m
[31m-                    [m
[31m-                    await wahub_client.cancel_order(oid)[m
[31m-                    db.add_saldo(uid, price, f"Refund Auto {sname}")[m
[31m-                    db.update_otp_order_status(oid, 'expired')[m
[31m-                    [m
[31m-                    try:[m
[31m-                        await app.bot.send_message([m
[31m-                            chat_id=uid, [m
[31m-                            text=f"⏰ <b>Waktu Habis</b>\n\nOrder <code>{oid}</code> tidak menerima OTP dalam 20 menit. Balance Rp {price:,} otomatis dikembalikan.", [m
[31m-                            parse_mode=ParseMode.HTML[m
[31m-                        )[m
[31m-                    except Exception:[m
[31m-                        pass[m
[31m-        except Exception as e:[m
[31m-            print(f"[CRON] Error: {e}")[m
[32m+[m[32m            stuck_orders = db.get_stuck_waiting_orders(minutes=20)[m
[32m+[m[32m            for order in stuck_orders:[m
[32m+[m[32m                oid = order['order_id'][m
[32m+[m[32m                uid = order['user_id'][m
[32m+[m[32m                sname = order['service_name'] or "OTP"[m
[32m+[m[32m                try:[m
[32m+[m[32m                    _, code = await asyncio.wait_for([m
[32m+[m[32m                        wahub_client.cancel_order(oid), timeout=25[m
[32m+[m[32m                    )[m
[32m+[m[32m                except Exception as exc:[m
[32m+[m[32m                    logger.warning("[CRON] Batal order %s gagal: %s", oid, exc)[m
[32m+[m[32m                    continue[m
[32m+[m
[32m+[m[32m                if code != 200:[m
[32m+[m[32m                    logger.warning("[CRON] Provider belum mengonfirmasi batal order %s (HTTP %s)", oid, code)[m
[32m+[m[32m                    continue[m
[32m+[m
[32m+[m[32m                changed, price = db.finish_otp_order_and_refund([m
[32m+[m[32m                    oid, uid, 'expired', f"Refund Auto {sname}"[m
[32m+[m[32m                )[m
[32m+[m[32m                if not changed:[m
[32m+[m[32m                    continue[m
[32m+[m
[32m+[m[32m                logger.info("[CRON] Refund otomatis order %s (%s)", oid, sname)[m
[32m+[m[32m                try:[m
[32m+[m[32m                    await app.bot.send_message([m
[32m+[m[32m                        chat_id=uid,[m
[32m+[m[32m                        text=([m
[32m+[m[32m                            f"⏰ <b>Waktu Habis</b>\n\nOrder <code>{oid}</code> "[m
[32m+[m[32m                            f"tidak menerima OTP. Balance Rp {price:,} otomatis dikembalikan."[m
[32m+[m[32m                        ),[m
[32m+[m[32m                        parse_mode=ParseMode.HTML,[m
[32m+[m[32m                    )[m
[32m+[m[32m                except Exception as exc:[m
[32m+[m[32m                    logger.warning("[CRON] Gagal mengirim notifikasi order %s: %s", oid, exc)[m
[32m+[m[32m        except Exception as exc:[m
[32m+[m[32m            logger.exception("[CRON] Gagal memeriksa order OTP: %s", exc)[m
 [m
 async def cmd_start(update, ctx):[m
     user = update.effective_user[m
[1mdiff --git a/database.py b/database.py[m
[1mindex f223150..cfcf736 100644[m
[1m--- a/database.py[m
[1m+++ b/database.py[m
[36m@@ -117,8 +117,11 @@[m [mdef add_saldo(user_id, amount, desc=""):[m
         else:[m
             ctx.execute("BEGIN IMMEDIATE")[m
             cur = ctx.execute("UPDATE users SET saldo = saldo + ? WHERE user_id=?", (amount, user_id))[m
[31m-            if cur.rowcount == 0: return False[m
[32m+[m[32m            if cur.rowcount == 0:[m
[32m+[m[32m                ctx.rollback()[m
[32m+[m[32m                return False[m
             ctx.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)", (user_id, 'IN', amount, desc))[m
[32m+[m[32m            ctx.commit()[m
             return True[m
 [m
 def deduct_saldo(user_id, amount, desc=""):[m
[36m@@ -135,8 +138,11 @@[m [mdef deduct_saldo(user_id, amount, desc=""):[m
         else:[m
             ctx.execute("BEGIN IMMEDIATE")[m
             cur = ctx.execute("UPDATE users SET saldo = saldo - ? WHERE user_id=? AND saldo >= ?", (amount, user_id, amount))[m
[31m-            if cur.rowcount == 0: return False[m
[32m+[m[32m            if cur.rowcount == 0:[m
[32m+[m[32m                ctx.rollback()[m
[32m+[m[32m                return False[m
             ctx.execute("INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)", (user_id, 'OUT', amount, desc))[m
[32m+[m[32m            ctx.commit()[m
             return True[m
 [m
 def create_otp_order(user_id, order_id, token, phone, service_name, price):[m
[36m@@ -207,6 +213,68 @@[m [mdef get_user_transactions(user_id, limit=10):[m
         else:[m
             return ctx.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()[m
 [m
[32m+[m[32mdef finish_otp_order_and_refund(order_id, user_id, new_status, description):[m
[32m+[m[32m    """Transition one waiting order and credit its refund exactly once."""[m
[32m+[m[32m    if DATABASE_URL:[m
[32m+[m[32m        with get_conn() as ctx:[m
[32m+[m[32m            conn, cur = ctx[m
[32m+[m[32m            cur.execute([m
[32m+[m[32m                "UPDATE otp_orders SET status=%s "[m
[32m+[m[32m                "WHERE order_id=%s AND user_id=%s AND status='waiting' RETURNING price",[m
[32m+[m[32m                (new_status, order_id, user_id),[m
[32m+[m[32m            )[m
[32m+[m[32m            row = cur.fetchone()[m
[32m+[m[32m            if not row:[m
[32m+[m[32m                return False, 0[m
[32m+[m[32m            amount = max(0, int(row['price'] or 0))[m
[32m+[m[32m            if amount:[m
[32m+[m[32m                cur.execute("UPDATE users SET saldo = saldo + %s WHERE user_id=%s", (amount, user_id))[m
[32m+[m[32m                if cur.rowcount != 1:[m
[32m+[m[32m                    raise RuntimeError(f"User {user_id} tidak ditemukan saat refund order {order_id}")[m
[32m+[m[32m                cur.execute([m
[32m+[m[32m                    "INSERT INTO transactions(user_id,type,amount,description) VALUES(%s,%s,%s,%s)",[m
[32m+[m[32m                    (user_id, 'IN', amount, description),[m
[32m+[m[32m                )[m
[32m+[m[32m            return True, amount[m
[32m+[m
[32m+[m[32m    with get_conn() as conn:[m
[32m+[m[32m        conn.execute("BEGIN IMMEDIATE")[m
[32m+[m[32m        try:[m
[32m+[m[32m            row = conn.execute([m
[32m+[m[32m                "SELECT price FROM otp_orders WHERE order_id=? AND user_id=? AND status='waiting'",[m
[32m+[m[32m                (order_id, user_id),[m
[32m+[m[32m            ).fetchone()[m
[32m+[m[32m            if not row:[m
[32m+[m[32m                conn.rollback()[m
[32m+[m[32m                return False, 0[m
[32m+[m
[32m+[m[32m            cur = conn.execute([m
[32m+[m[32m                "UPDATE otp_orders SET status=? "[m
[32m+[m[32m                "WHERE order_id=? AND user_id=? AND status='waiting'",[m
[32m+[m[32m                (new_status, order_id, user_id),[m
[32m+[m[32m            )[m
[32m+[m[32m            if cur.rowcount != 1:[m
[32m+[m[32m                conn.rollback()[m
[32m+[m[32m                return False, 0[m
[32m+[m
[32m+[m[32m            amount = max(0, int(row['price'] or 0))[m
[32m+[m[32m            if amount:[m
[32m+[m[32m                cur = conn.execute([m
[32m+[m[32m                    "UPDATE users SET saldo=saldo+? WHERE user_id=?", (amount, user_id)[m
[32m+[m[32m                )[m
[32m+[m[32m                if cur.rowcount != 1:[m
[32m+[m[32m                    raise RuntimeError(f"User {user_id} tidak ditemukan saat refund order {order_id}")[m
[32m+[m[32m                conn.execute([m
[32m+[m[32m                    "INSERT INTO transactions(user_id,type,amount,description) VALUES(?,?,?,?)",[m
[32m+[m[32m                    (user_id, 'IN', amount, description),[m
[32m+[m[32m                )[m
[32m+[m[32m            conn.commit()[m
[32m+[m[32m            return True, amount[m
[32m+[m[32m        except Exception:[m
[32m+[m[32m            conn.rollback()[m
[32m+[m[32m            raise[m
[32m+[m
[32m+[m
 def get_stuck_waiting_orders(minutes=20):[m
     with get_conn() as ctx:[m
         limit = int(time.time()) - (minutes * 60)[m
[1mdiff --git a/otp_handlers.py b/otp_handlers.py[m
[1mindex 1757150..200212a 100644[m
[1m--- a/otp_handlers.py[m
[1m+++ b/otp_handlers.py[m
[36m@@ -1,4 +1,6 @@[m
 import asyncio[m
[32m+[m[32mimport logging[m
[32m+[m[32mimport html[m
 import httpx[m
 import os[m
 import time[m
[36m@@ -12,6 +14,8 @@[m [mimport database as db[m
 import wahub_client[m
 import dehuyz_automation[m
 [m
[32m+[m[32mlogger = logging.getLogger(__name__)[m
[32m+[m
 QUICK_SERVICES = ["Kopken Gacha", "Kopken Asoy", "Shope Gacha", "Shope Asoy"] [m
 MARGIN = 700[m
 BIAYA_CEK_FRESH = 200[m
[36m@@ -276,94 +280,176 @@[m [masync def process_direct_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE, qty[m
     ctx.user_data['buy_state'] = None[m
     ctx.user_data['is_processing'] = False[m
 [m
[31m-# === TIMER 5 DETIK (HAPUS & KIRIM ULANG) ===[m
[32m+[m[32m# === TIMER COUNTDOWN: edit pesan yang sama agar tombol tetap aktif ===[m
 async def run_timer(bot, chat_id, msg_id, order_id, service_name, phone, total_time):[m
[31m-    start_time = time.time()[m
[31m-    current_msg_id = msg_id[m
[31m-    [m
[32m+[m[32m    start_time = time.monotonic()[m
[32m+[m
     while True:[m
[31m-        await asyncio.sleep(5) [m
[31m-        elapsed = int(time.time() - start_time)[m
[31m-        time_left = total_time - elapsed[m
[31m-        [m
[31m-        if time_left <= 0:[m
[31m-            try: await bot.delete_message(chat_id=chat_id, message_id=current_msg_id)[m
[31m-            except: pass[m
[31m-            break[m
[31m-            [m
[32m+[m[32m        await asyncio.sleep(5)[m
         order = db.get_otp_order(order_id)[m
         if not order or order['status'] != 'waiting':[m
[31m-            break[m
[31m-            [m
[31m-        ui_text = build_order_ui(order_id, service_name, phone, "Menunggu OTP", time_left)[m
[31m-        [m
[32m+[m[32m            return[m
[32m+[m
[32m+[m[32m        elapsed = int(time.monotonic() - start_time)[m
[32m+[m[32m        time_left = max(0, total_time - elapsed)[m
[32m+[m[32m        ui_text = build_order_ui([m
[32m+[m[32m            order_id, service_name, phone, "Menunggu OTP", time_left[m
[32m+[m[32m        )[m
         try:[m
[31m-            await bot.delete_message(chat_id=chat_id, message_id=current_msg_id)[m
[31m-            new_msg = await bot.send_message([m
[31m-                chat_id=chat_id, [m
[31m-                text=ui_text, [m
[32m+[m[32m            await bot.edit_message_text([m
[32m+[m[32m                chat_id=chat_id,[m
[32m+[m[32m                message_id=msg_id,[m
[32m+[m[32m                text=ui_text,[m
                 parse_mode=ParseMode.HTML,[m
[31m-                reply_markup=get_order_inline_keyboard("Menunggu OTP")[m
[32m+[m[32m                reply_markup=get_order_inline_keyboard("Menunggu OTP"),[m
             )[m
[31m-            current_msg_id = new_msg.message_id[m
[31m-        except Exception:[m
[31m-            pass[m
[32m+[m[32m        except Exception as exc:[m
[32m+[m[32m            # Jangan diam-diam membuat UI hilang; polling OTP tetap berjalan.[m
[32m+[m[32m            logger.warning("Gagal memperbarui timer order %s: %s", order_id, exc)[m
 [m
[31m-# === POLLING OTP 2 ===[m
[32m+[m[32m        # Biarkan pesan 00:00 terlihat sampai proses timeout mengonfirmasi[m
[32m+[m[32m        # pembatalan dan refund, alih-alih menghapus UI tanpa penjelasan.[m
[32m+[m[32m        if time_left <= 0:[m
[32m+[m[32m            return[m
[32m+[m
[32m+[m[32m# === POLLING OTP ===[m
 async def poll_otp_bg(bot, user_id, order_id, token, service_name, msg_id, phone):[m
     db_order = db.get_otp_order(order_id)[m
     if not db_order or db_order['status'] != 'waiting':[m
[31m-        return [m
[32m+[m[32m        return[m
 [m
     otp_pertama = None[m
     otp_kedua = None[m
[31m-    [m
[31m-    start_time = time.time()[m
[31m-    while time.time() - start_time < 1200: [m
[32m+[m[32m    start_time = time.monotonic()[m
[32m+[m
[32m+[m[32m    while time.monotonic() - start_time < 1200:[m
         db_order = db.get_otp_order(order_id)[m
         if not db_order or db_order['status'] != 'waiting':[m
[31m-            return [m
[32m+[m[32m            return[m
[32m+[m
[32m+[m[32m        try:[m
[32m+[m[32m            res, status = await wahub_client.check_otp(token, timeout=15)[m
[32m+[m[32m        except Exception as exc:[m
[32m+[m[32m            logger.warning("Gagal cek OTP order %s: %s", order_id, exc)[m
[32m+[m[32m            await asyncio.sleep(15)[m
[32m+[m[32m            continue[m
 [m
[31m-        res, status = await wahub_client.check_otp(token, timeout=15)[m
         if status == 200 and res.get('state') == 'success':[m
             otp_baru = res.get('otp')[m
[31m-            [m
[32m+[m[32m            if not otp_baru:[m
[32m+[m[32m                await asyncio.sleep(15)[m
[32m+[m[32m                continue[m
[32m+[m
             if otp_pertama is None:[m
[32m+[m[32m                # CAS mencegah OTP yang datang bersamaan dengan pembatalan[m
[32m+[m[32m                # mengubah order yang sudah dibatalkan kembali menjadi sukses.[m
[32m+[m[32m                if not db.mark_success_if_waiting(order_id, otp_baru):[m
[32m+[m[32m                    return[m
                 otp_pertama = otp_baru[m
[31m-                db.update_otp_order_status(order_id, 'success', otp_pertama)[m
[31m-                await wahub_client.mark_done(order_id)[m
[31m-                [m
[31m-                ui_text = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp_pertama)[m
                 try:[m
[31m-                    await bot.delete_message(chat_id=user_id, message_id=msg_id)[m
[31m-                    new_msg = await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Selesai"))[m
[31m-                    msg_id = new_msg.message_id [m
[31m-                except Exception: pass[m
[31m-                [m
[32m+[m[32m                    await wahub_client.mark_done(order_id)[m
[32m+[m[32m                except Exception as exc:[m
[32m+[m[32m                    logger.warning("Gagal menandai order %s selesai di provider: %s", order_id, exc)[m
[32m+[m
[32m+[m[32m                ui_text = build_order_ui([m
[32m+[m[32m                    order_id, service_name, phone, "Selesai", 0, otp_pertama[m
[32m+[m[32m                )[m
[32m+[m[32m                try:[m
[32m+[m[32m                    await bot.edit_message_text([m
[32m+[m[32m                        chat_id=user_id,[m
[32m+[m[32m                        message_id=msg_id,[m
[32m+[m[32m                        text=ui_text,[m
[32m+[m[32m                        parse_mode=ParseMode.HTML,[m
[32m+[m[32m                        reply_markup=get_order_inline_keyboard("Selesai"),[m
[32m+[m[32m                    )[m
[32m+[m[32m                except Exception:[m
[32m+[m[32m                    try:[m
[32m+[m[32m                        new_msg = await bot.send_message([m
[32m+[m[32m                            chat_id=user_id,[m
[32m+[m[32m                            text=ui_text,[m
[32m+[m[32m                            parse_mode=ParseMode.HTML,[m
[32m+[m[32m                            reply_markup=get_order_inline_keyboard("Selesai"),[m
[32m+[m[32m                        )[m
[32m+[m[32m                        msg_id = new_msg.message_id[m
[32m+[m[32m                    except Exception as exc:[m
[32m+[m[32m                        logger.warning("Gagal mengirim hasil OTP order %s: %s", order_id, exc)[m
[32m+[m
             elif otp_baru != otp_pertama and otp_kedua is None:[m
                 otp_kedua = otp_baru[m
[31m-                [m
[31m-                ui_text = build_order_ui(order_id, service_name, phone, "Selesai", 0, otp_pertama, otp_kedua)[m
[32m+[m[32m                ui_text = build_order_ui([m
[32m+[m[32m                    order_id, service_name, phone, "Selesai", 0,[m
[32m+[m[32m                    otp_pertama, otp_kedua[m
[32m+[m[32m                )[m
                 try:[m
[31m-                    await bot.delete_message(chat_id=user_id, message_id=msg_id)[m
[31m-                    await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Selesai"))[m
[31m-                except Exception: pass[m
[31m-                [m
[32m+[m[32m                    await bot.edit_message_text([m
[32m+[m[32m                        chat_id=user_id,[m
[32m+[m[32m                        message_id=msg_id,[m
[32m+[m[32m                        text=ui_text,[m
[32m+[m[32m                        parse_mode=ParseMode.HTML,[m
[32m+[m[32m                        reply_markup=get_order_inline_keyboard("Selesai"),[m
[32m+[m[32m                    )[m
[32m+[m[32m                except Exception as exc:[m
[32m+[m[32m                    logger.warning("Gagal memperbarui OTP kedua order %s: %s", order_id, exc)[m
[32m+[m
         await asyncio.sleep(15)[m
[31m-        [m
[32m+[m
     db_order = db.get_otp_order(order_id)[m
[31m-    if db_order and db_order['status'] == 'waiting':[m
[31m-        cxl_res, cxl_status = await wahub_client.cancel_order(order_id)[m
[31m-        if cxl_status == 200:[m
[31m-            db.update_otp_order_status(order_id, 'expired')[m
[31m-            db.add_saldo(user_id, db_order['price'], "Refund OTP Timeout")[m
[31m-            [m
[31m-            ui_text = f"⏰ <b>Waktu Habis</b>\n\nOTP tidak masuk. Balance Rp {db_order['price']:,} dikembalikan."[m
[31m-            try:[m
[31m-                await bot.delete_message(chat_id=user_id, message_id=msg_id)[m
[31m-                await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Selesai"))[m
[31m-            except Exception:[m
[31m-                pass[m
[32m+[m[32m    if not db_order or db_order['status'] != 'waiting':[m
[32m+[m[32m        return[m
[32m+[m
[32m+[m[32m    try:[m
[32m+[m[32m        cxl_res, cxl_status = await asyncio.wait_for([m
[32m+[m[32m            wahub_client.cancel_order(order_id), timeout=25[m
[32m+[m[32m        )[m
[32m+[m[32m    except Exception as exc:[m
[32m+[m[32m        logger.exception("Gagal mengonfirmasi timeout order %s", order_id)[m
[32m+[m[32m        cxl_res, cxl_status = {"message": str(exc)}, None[m
[32m+[m
[32m+[m[32m    if cxl_status == 200:[m
[32m+[m[32m        try:[m
[32m+[m[32m            refunded, refund = db.finish_otp_order_and_refund([m
[32m+[m[32m                order_id, user_id, 'expired', "Refund OTP Timeout"[m
[32m+[m[32m            )[m
[32m+[m[32m        except Exception as exc:[m
[32m+[m[32m            logger.exception("Provider membatalkan order %s tetapi refund database gagal", order_id)[m
[32m+[m[32m            refunded, refund = False, 0[m
[32m+[m[32m            ui_text = ([m
[32m+[m[32m                "⚠️ <b>Provider sudah menerima pembatalan, tetapi refund belum tercatat</b>\n\n"[m
[32m+[m[32m                "Jangan tekan batal lagi. Status disimpan untuk pemeriksaan ulang; silakan hubungi admin jika balance belum masuk."[m
[32m+[m[32m            )[m
[32m+[m[32m        else:[m
[32m+[m[32m            if refunded:[m
[32m+[m[32m                ui_text = ([m
[32m+[m[32m                    f"⏰ <b>Waktu Habis</b>\n\nOTP tidak masuk. "[m
[32m+[m[32m                    f"Balance Rp {refund:,} dikembalikan."[m
[32m+[m[32m                )[m
[32m+[m[32m            else:[m
[32m+[m[32m                ui_text = ([m
[32m+[m[32m                    f"ℹ️ <b>Order sudah diproses</b>\n\n"[m
[32m+[m[32m                    "Status pesanan berubah sebelum refund timeout; saldo tidak dikreditkan dua kali."[m
[32m+[m[32m                )[m
[32m+[m[32m    else:[m
[32m+[m[32m        payload = cxl_res if isinstance(cxl_res, dict) else {}[m
[32m+[m[32m        detail = payload.get('message') or payload.get('error') or f"HTTP {cxl_status}"[m
[32m+[m[32m        ui_text = ([m
[32m+[m[32m            f"⚠️ <b>Waktu OTP habis, pembatalan belum terkonfirmasi</b>\n\n"[m
[32m+[m[32m            f"Alasan: <code>{html.escape(str(detail))}</code>\n"[m
[32m+[m[32m            "Saldo belum dikembalikan. Sistem akan mencoba pemeriksaan ulang."[m
[32m+[m[32m        )[m
[32m+[m
[32m+[m[32m    try:[m
[32m+[m[32m        await bot.edit_message_text([m
[32m+[m[32m            chat_id=user_id,[m
[32m+[m[32m            message_id=msg_id,[m
[32m+[m[32m            text=ui_text,[m
[32m+[m[32m            parse_mode=ParseMode.HTML,[m
[32m+[m[32m            reply_markup=None,[m
[32m+[m[32m        )[m
[32m+[m[32m    except Exception:[m
[32m+[m[32m        try:[m
[32m+[m[32m            await bot.send_message(chat_id=user_id, text=ui_text, parse_mode=ParseMode.HTML)[m
[32m+[m[32m        except Exception as exc:[m
[32m+[m[32m            logger.warning("Gagal mengirim status akhir order %s: %s", order_id, exc)[m
 [m
 # === FITUR CEK RIWAYAT ===[m
 async def handle_history_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):[m
[36m@@ -393,130 +479,241 @@[m [masync def handle_history_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE[m
     await query.answer()[m
     await query.message.reply_text(text, parse_mode=ParseMode.HTML)[m
 [m
[31m-# === INI FIX BENGONG PAS BATAL & GANTI ===[m
[32m+[m[32m# === CALLBACK ORDER: status selalu terlihat dan refund idempotent ===[m
 async def handle_order_action_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):[m
     query = update.callback_query[m
[31m-    await query.answer()[m
[31m-    [m
[31m-    action = query.data.replace("otp_act_", "")[m
     user = update.effective_user[m
[31m-    last_order = ctx.user_data.get('last_order')[m
[31m-    [m
[32m+[m[32m    action = query.data.replace("otp_act_", "", 1)[m
[32m+[m
[32m+[m[32m    if action in ("batal", "ganti") and ctx.user_data.get('otp_action_busy'):[m
[32m+[m[32m        await query.answer("Aksi pesanan sedang diproses.", show_alert=True)[m
[32m+[m[32m        return[m
[32m+[m
[32m+[m[32m    await query.answer()[m
[32m+[m
     if action == "selesai":[m
         try:[m
             await query.delete_message()[m
         except Exception:[m
             pass[m
         return[m
[31m-        [m
[32m+[m
[32m+[m[32m    last_order = ctx.user_data.get('last_order')[m
     if not last_order:[m
[31m-        await query.message.reply_text("❌ Tidak ada pesanan aktif.")[m
[32m+[m[32m        try:[m
[32m+[m[32m            await query.edit_message_text("❌ Tidak ada pesanan aktif yang bisa diproses.")[m
[32m+[m[32m        except Exception:[m
[32m+[m[32m            await ctx.bot.send_message(chat_id=user.id, text="❌ Tidak ada pesanan aktif yang bisa diproses.")[m
         return[m
[31m-        [m
[32m+[m
     if action == "batal":[m
[31m-        # 1. Hapus Pesan UI lama biar bersih[m
[32m+[m[32m        order_id = last_order['order_id'][m
[32m+[m[32m        ctx.user_data['otp_action_busy'] = True[m
         try:[m
[31m-            await query.message.delete()[m
[31m-        except Exception:[m
[31m-            pass[m
[31m-            [m
[31m-        # 2. Potong ke API[m
[31m-        res, code = await wahub_client.cancel_order(last_order['order_id'])[m
[31m-        [m
[31m-        if code == 200:[m
[31m-            refund = last_order.get('price', 0)[m
[31m-            db.add_saldo(user.id, refund, "Refund Batal Pesanan")[m
[31m-            db.update_otp_order_status(last_order['order_id'], 'cancelled')[m
[31m-            ctx.user_data['last_order'] = None[m
[31m-            [m
[31m-            # 3. Kirim notif sukses[m
[31m-            await query.message.reply_text(f"✅ Pesanan dibatalkan. Balance Rp {refund:,} dikembalikan.")[m
[31m-            kb = await get_otp_keyboard()[m
[31m-            await query.message.reply_text("Menu OTP:", reply_markup=kb)[m
[31m-        else:[m
[31m-            await query.message.reply_text("❌ Gagal membatalkan pesanan.")[m
[31m-            [m
[31m-    elif action == "ganti":[m
[31m-        await handle_ganti_nomor_inline(update, ctx)[m
[31m-        [m
[31m-    elif action == "resend":[m
[31m-        res, code = await wahub_client.retry_otp(last_order['token'])[m
[31m-        if code == 200:[m
[31m-            await query.message.reply_text("✅ Permintaan OTP ulang dikirim. Mohon tunggu SMS/WA masuk.")[m
[31m-        else:[m
[31m-            await query.message.reply_text("❌ Gagal minta ulang OTP. Mungkin batas retry habis atau waktu sewa habis.")[m
[32m+[m[32m            try:[m
[32m+[m[32m                await query.edit_message_text([m
[32m+[m[32m                    "⏳ Sedang meminta pembatalan ke penyedia. Tunggu sebentar; status dan refund akan ditampilkan di sini."[m
[32m+[m[32m                )[m
[32m+[m[32m            except Exception as exc:[m
[32m+[m[32m                logger.warning("Tidak bisa menampilkan status batal order %s: %s", order_id, exc)[m
[32m+[m
[32m+[m[32m            try:[m
[32m+[m[32m                res, code = await asyncio.wait_for([m
[32m+[m[32m                    wahub_client.cancel_order(order_id), timeout=25[m
[32m+[m[32m                )[m
[32m+[m[32m            except Exception as exc:[m
[32m+[m[32m                logger.exception("Permintaan batal order %s gagal", order_id)[m
[32m+[m[32m                res, code = {"message": str(exc)}, None[m
[32m+[m
[32m+[m[32m            if code != 200:[m
[32m+[m[32m                payload = res if isinstance(res, dict) else {}[m
[32m+[m[32m                detail = payload.get('message') or payload.get('error') or payload.get('msg') or f"HTTP {code}"[m
[32m+[m[32m                text = ([m
[32m+[m[32m                    "❌ Pembatalan belum dikonfirmasi oleh penyedia. Saldo belum dikembalikan.\n\n"[m
[32m+[m[32m                    f"Alasan: <code>{html.escape(str(detail))}</code>\n"[m
[32m+[m[32m                    "Pesanan tetap tercatat agar tidak terjadi refund palsu."[m
[32m+[m[32m                )[m
[32m+[m[32m                try:[m
[32m+[m[32m                    await query.edit_message_text(text, parse_mode=ParseMode.HTML)[m
[32m+[m[32m                except Exception:[m
[32m+[m[32m                    await ctx.bot.send_message(chat_id=user.id, text=text, parse_mode=ParseMode.HTML)[m
[32m+[m[32m                return[m
[32m+[m
[32m+[m[32m            try:[m
[32m+[m[32m                changed, refund = db.finish_otp_order_and_refund([m
[32m+[m[32m                    order_id, user.id, 'cancelled', "Refund Batal Pesanan"[m
[32m+[m[32m                )[m
[32m+[m[32m            except Exception as exc:[m
[32m+[m[32m                logger.exception("Provider menerima batal order %s, tetapi refund database gagal", order_id)[m
[32m+[m[32m                text = ([m
[32m+[m[32m                    "⚠️ Provider menerima pembatalan, tetapi pencatatan refund mengalami error. "[m
[32m+[m[32m                    "Jangan tekan batal lagi; admin perlu memeriksa saldo/order.\n"[m
[32m+[m[32m                    f"Detail: {html.escape(str(exc))}"[m
[32m+[m[32m                )[m
[32m+[m[32m                try:[m
[32m+[m[32m                    await query.edit_message_text(text, parse_mode=ParseMode.HTML)[m
[32m+[m[32m                except Exception:[m
[32m+[m[32m                    await ctx.bot.send_message(chat_id=user.id, text=text, parse_mode=ParseMode.HTML)[m
[32m+[m[32m                return[m
[32m+[m[32m            if changed:[m
[32m+[m[32m                if ctx.user_data.get('last_order', {}).get('order_id') == order_id:[m
[32m+[m[32m                    ctx.user_data['last_order'] = None[m
[32m+[m[32m                text = f"✅ Pesanan dibatalkan dan refund Rp {refund:,} sudah masuk ke balance."[m
[32m+[m[32m            else:[m
[32m+[m[32m                current = db.get_otp_order(order_id)[m
[32m+[m[32m                status_now = current['status'] if current else "tidak ditemukan"[m
[32m+[m[32m                text = ([m
[32m+[m[32m                    "ℹ️ Penyedia menerima pembatalan, tetapi order sudah diproses sebelumnya. "[m
[32m+[m[32m                    f"Status database: <code>{status_now}</code>. Tidak ada refund kedua."[m
[32m+[m[32m                )[m
[32m+[m
[32m+[m[32m            try:[m
[32m+[m[32m                await query.edit_message_text(text, parse_mode=ParseMode.HTML)[m
[32m+[m[32m            except Exception:[m
[32m+[m[32m                await ctx.bot.send_message(chat_id=user.id, text=text, parse_mode=ParseMode.HTML)[m
[32m+[m[32m            await ctx.bot.send_message([m
[32m+[m[32m                chat_id=user.id,[m
[32m+[m[32m                text="Menu OTP:",[m
[32m+[m[32m                reply_markup=await get_otp_keyboard(),[m
[32m+[m[32m            )[m
[32m+[m[32m        finally:[m
[32m+[m[32m            ctx.user_data['otp_action_busy'] = False[m
[32m+[m[32m        return[m
[32m+[m
[32m+[m[32m    if action == "ganti":[m
[32m+[m[32m        ctx.user_data['otp_action_busy'] = True[m
[32m+[m[32m        try:[m
[32m+[m[32m            await handle_ganti_nomor_inline(update, ctx)[m
[32m+[m[32m        finally:[m
[32m+[m[32m            ctx.user_data['otp_action_busy'] = False[m
[32m+[m[32m        return[m
[32m+[m
[32m+[m[32m    if action == "resend":[m
[32m+[m[32m        try:[m
[32m+[m[32m            res, code = await wahub_client.retry_otp(last_order['token'])[m
[32m+[m[32m            text = ([m
[32m+[m[32m                "✅ Permintaan OTP ulang dikirim. Mohon tunggu SMS/WA masuk."[m
[32m+[m[32m                if code == 200 else[m
[32m+[m[32m                "❌ Gagal meminta OTP ulang. Batas retry mungkin habis atau waktu sewa selesai."[m
[32m+[m[32m            )[m
[32m+[m[32m        except Exception as exc:[m
[32m+[m[32m            logger.exception("Gagal meminta OTP ulang order %s", last_order.get('order_id'))[m
[32m+[m[32m            text = f"❌ Permintaan OTP ulang gagal: <code>{html.escape(str(exc))}</code>"[m
[32m+[m[32m        try:[m
[32m+[m[32m            await ctx.bot.send_message(chat_id=user.id, text=text, parse_mode=ParseMode.HTML)[m
[32m+[m[32m        except Exception as exc:[m
[32m+[m[32m            logger.warning("Tidak bisa mengirim status resend: %s", exc)[m
 [m
 async def handle_ganti_nomor_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE):[m
     query = update.callback_query[m
     user = update.effective_user[m
     last_order = ctx.user_data.get('last_order')[m
[31m-    [m
[32m+[m
[32m+[m[32m    async def show_status(text):[m
[32m+[m[32m        try:[m
[32m+[m[32m            await query.edit_message_text(text)[m
[32m+[m[32m        except Exception:[m
[32m+[m[32m            await ctx.bot.send_message(chat_id=user.id, text=text)[m
[32m+[m
     if not last_order:[m
[31m-        await query.message.reply_text("❌ Tidak ada pesanan aktif untuk diganti.")[m
[32m+[m[32m        await show_status("❌ Tidak ada pesanan aktif untuk diganti.")[m
         return[m
[31m-        [m
[32m+[m
     order_id_lama = last_order['order_id'][m
     display_name = last_order['service'][m
     harga = last_order['price'][m
[31m-    [m
[32m+[m
     map_back = {[m
         "kopken gacha": "kopken",[m
         "kopken asoy": "kopken filter",[m
         "shope gacha": "shopee",[m
[31m-        "shope asoy": "shopee filter"[m
[32m+[m[32m        "shope asoy": "shopee filter",[m
     }[m
     api_name = map_back.get(display_name.lower(), display_name.lower())[m
[31m-    [m
[31m-    services = await wahub_client.get_services()[m
[31m-    target_service = next((s for s in services if s['name'].strip().lower() == api_name), None)[m
[31m-    [m
[32m+[m
[32m+[m[32m    try:[m
[32m+[m[32m        services = await wahub_client.get_services()[m
[32m+[m[32m        target_service = next((s for s in services if s['name'].strip().lower() == api_name), None)[m
[32m+[m[32m    except Exception as exc:[m
[32m+[m[32m        logger.exception("Gagal mengambil stok saat ganti order %s", order_id_lama)[m
[32m+[m[32m        await show_status(f"❌ Gagal mengecek stok: {exc}")[m
[32m+[m[32m        return[m
[32m+[m
     if not target_service or target_service['stock'] < 1:[m
[31m-        await query.message.reply_text(f"❌ Maaf, stok {display_name} lagi kosong. Coba lagi nanti.")[m
[32m+[m[32m        await show_status(f"❌ Stok {display_name} kosong. Order lama belum dibatalkan.")[m
         return[m
 [m
     try:[m
[31m-        await query.delete_message()[m
[31m-    except Exception:[m
[31m-        pass[m
[31m-        [m
[31m-    await query.message.reply_text("⏳ Membatalkan nomor lama & memproses nomor baru...")[m
[31m-    [m
[31m-    cxl_res, cxl_code = await wahub_client.cancel_order(order_id_lama)[m
[31m-    [m
[31m-    db.add_saldo(user.id, harga, "Refund Ganti Nomor")[m
[32m+[m[32m        await query.edit_message_text("⏳ Meminta pembatalan nomor lama. Statusnya akan ditampilkan di sini…")[m
[32m+[m[32m    except Exception as exc:[m
[32m+[m[32m        logger.warning("Tidak bisa menampilkan status ganti order %s: %s", order_id_lama, exc)[m
[32m+[m
[32m+[m[32m    try:[m
[32m+[m[32m        _, cxl_code = await asyncio.wait_for(wahub_client.cancel_order(order_id_lama), timeout=25)[m
[32m+[m[32m    except Exception as exc:[m
[32m+[m[32m        logger.exception("Pembatalan order %s untuk ganti nomor gagal", order_id_lama)[m
[32m+[m[32m        await show_status(f"❌ Pembatalan belum terkonfirmasi; order lama tetap aktif dan saldo belum berubah. Alasan: {exc}")[m
[32m+[m[32m        return[m
[32m+[m
[32m+[m[32m    if cxl_code != 200:[m
[32m+[m[32m        await show_status(f"❌ Provider belum mengonfirmasi pembatalan (HTTP {cxl_code}). Order lama tetap aktif dan saldo belum berubah.")[m
[32m+[m[32m        return[m
[32m+[m
[32m+[m[32m    try:[m
[32m+[m[32m        changed, refund = db.finish_otp_order_and_refund([m
[32m+[m[32m            order_id_lama, user.id, 'cancelled', "Refund Ganti Nomor"[m
[32m+[m[32m        )[m
[32m+[m[32m    except Exception as exc:[m
[32m+[m[32m        logger.exception("Provider menerima batal order %s, tetapi refund ganti gagal tercatat", order_id_lama)[m
[32m+[m[32m        await show_status(f"⚠️ Provider menerima pembatalan, tetapi refund belum tercatat. Jangan ulangi aksi; admin perlu memeriksa order. Detail: {exc}")[m
[32m+[m[32m        return[m
[32m+[m[32m    if not changed:[m
[32m+[m[32m        current = db.get_otp_order(order_id_lama)[m
[32m+[m[32m        status_now = current['status'] if current else "tidak ditemukan"[m
[32m+[m[32m        await show_status(f"ℹ️ Order lama sudah diproses ({status_now}); nomor baru tidak dibuat dan tidak ada refund kedua.")[m
[32m+[m[32m        return[m
[32m+[m
[32m+[m[32m    if ctx.user_data.get('last_order', {}).get('order_id') == order_id_lama:[m
[32m+[m[32m        ctx.user_data['last_order'] = None[m
[32m+[m
     if not db.deduct_saldo(user.id, harga, f"Ganti Nomor {display_name}"):[m
[31m-        await query.message.reply_text("❌ Gagal memotong saldo Anda. Saldo tidak cukup atau sistem sedang sibuk.")[m
[32m+[m[32m        await show_status(f"❌ Order lama dibatalkan dan refund Rp {refund:,} masuk, tetapi saldo tidak cukup untuk membeli nomor pengganti.")[m
[32m+[m[32m        return[m
[32m+[m
[32m+[m[32m    try:[m
[32m+[m[32m        res, code = await wahub_client.rent_number(target_service['id'])[m
[32m+[m[32m    except Exception as exc:[m
[32m+[m[32m        db.add_saldo(user.id, harga, "Refund Gagal Ganti Nomor")[m
[32m+[m[32m        await show_status(f"❌ Gagal membuat nomor pengganti; Rp {harga:,} dikembalikan. Alasan: {exc}")[m
         return[m
 [m
[31m-    res, code = await wahub_client.rent_number(target_service['id'])[m
[31m-    [m
     if code == 200 and res.get('order_id'):[m
         new_oid = res['order_id'][m
         new_tkn = res.get('token')[m
         new_ph = res.get('phone')[m
[31m-        [m
[32m+[m
         db.create_otp_order(user.id, new_oid, new_tkn, new_ph, display_name, harga)[m
         ctx.user_data['last_order'] = {"order_id": new_oid, "token": new_tkn, "phone": new_ph, "service": display_name, "price": harga}[m
[31m-        [m
[32m+[m
         ui_text = build_order_ui(new_oid, display_name, new_ph, "Menunggu OTP", 1200)[m
[31m-        [m
[31m-        msg_ui = await query.message.reply_text(ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP"))[m
[32m+[m
[32m+[m[32m        msg_ui = await ctx.bot.send_message(chat_id=user.id, text=ui_text, parse_mode=ParseMode.HTML, reply_markup=get_order_inline_keyboard("Menunggu OTP"))[m
         ctx.user_data['ui_msg_id'] = msg_ui.message_id[m
         ctx.user_data['last_order']['msg_id'] = msg_ui.message_id[m
[31m-        [m
[32m+[m
         asyncio.create_task(run_timer(ctx.bot, user.id, msg_ui.message_id, new_oid, display_name, new_ph, 1200))[m
         asyncio.create_task(poll_otp_bg(ctx.bot, user.id, new_oid, new_tkn, display_name, msg_ui.message_id, new_ph))[m
[31m-        [m
[32m+[m
         if "kopken" in api_name.lower() and "filter" not in api_name.lower():[m
             promo_txt = f"💡 Nomor <code>{new_ph}</code> siap. Mau cek status Kopkennya sekarang?\n"[m
             promo_txt += f"*(Kamu punya 2x Free Trial cek gratis!)*"[m
             cek_kb = InlineKeyboardMarkup([[InlineKeyboardButton("☕ Cek Kopken Sekarang", callback_data=f"otp_cekkopken_{new_ph}")]])[m
             await ctx.bot.send_message(chat_id=user.id, text=promo_txt, parse_mode=ParseMode.HTML, reply_markup=cek_kb)[m
[31m-        [m
[32m+[m
     else:[m
         db.add_saldo(user.id, harga, "Refund Gagal Ganti Nomor")[m
         err_msg = res.get('message') or res.get('error') or "Unknown error"[m
[31m-        await query.message.reply_text(f"❌ Gagal mendapatkan nomor baru.\n<b>Alasan:</b> <code>{err_msg}</code>\n\nSaldo dikembalikan.", parse_mode=ParseMode.HTML)[m
[32m+[m[32m        await show_status(f"❌ Gagal mendapatkan nomor baru. Saldo Rp {harga:,} dikembalikan. Alasan: {err_msg}")[m
 [m
 async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):[m
     text = "💰 <b>Deposit Balance</b>\n\nMinimal deposit Rp 5.000.\nKetik jumlah deposit yang diinginkan (contoh: 10000):"[m
