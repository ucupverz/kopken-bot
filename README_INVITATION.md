# Akses Undangan WhatsApp OTP

Paket perubahan untuk proyek bot yang sudah ada. Bukan source seluruh proyek dari nol: modul wahub_client, dehuyz_automation, ShopeeFood, tarif, dan konfigurasi deployment tetap gunakan milik proyek sebelumnya.

## File

Ganti tiga file lama dengan file lengkap dari paket ini:
- bot_checker.py
- otp_handlers.py
- database.py

Tambahkan file baru di direktori yang sama:
- invite_access.py

Folder tests berisi pengujian offline, tidak wajib diunggah ke server produksi. Tidak ada dependency runtime pihak ketiga baru; gunakan requirements proyek yang sudah ada.

## Konfigurasi

1. Pastikan ADMIN_IDS di environment berisi ID numerik Telegram admin, dipisahkan koma, contoh `ADMIN_IDS=123456789,987654321`. Username tidak digunakan sebagai otorisasi. Tanpa ADMIN_IDS, tidak ada yang bisa generate kode.
2. Tetap gunakan BOT_TOKEN dan konfigurasi API/database proyek sebelumnya. Jangan menaruh kredensial di source atau chat.
3. BOOSTLY_API_KEY sekarang wajib di environment bila menggunakan cek Kopken. Fallback key yang tertanam dalam source lama telah dihapus dari file hasil.
4. PostgreSQL menggunakan DATABASE_URL yang sudah ada. Tanpa DATABASE_URL, SQLite menggunakan bot.db secara default. Opsional SQLITE_DB_PATH menentukan lokasi file SQLite; gunakan volume persisten agar data tetap ada setelah redeploy.
5. Backup database dan tiga file lama sebelum mengganti file. Paket ini tidak menyertakan database produksi, tidak menjalankan migrasi di server, dan tidak melakukan push/deploy.

## Pemasangan

1. Letakkan empat file Python di root proyek, sejajar dengan modul lama.
2. Pastikan environment di atas benar.
3. Restart/redeploy bot dengan proses deployment yang biasa digunakan.
4. Fungsi init_db pada startup otomatis membuat tabel whatsapp_invites dan whatsapp_access dengan CREATE TABLE IF NOT EXISTS. Tidak menghapus atau mengubah tabel lama.
5. Uji melalui chat pribadi bot dengan akun admin dan akun kedua sebelum digunakan luas.

Semua pengguna lama yang belum redeem juga akan terkunci untuk WhatsApp OTP. Admin tidak otomatis bypass: admin dapat generate lalu redeem satu kode untuk akunnya sendiri. Menu lain, saldo, VOC, dan tracker tetap memakai alur lama. Pesanan yang sudah berjalan di background tidak dibatalkan atau di-refund otomatis oleh fitur ini; jangan mengaktifkan pembatasan di tengah pesanan aktif bila pengguna tersebut belum diberi akses.

## Penggunaan Admin

Di chat pribadi bot:

```text
/generate_invite
/generate_invite 5
```

Default satu kode, maksimum 20 per perintah. Hanya ID dalam ADMIN_IDS yang diizinkan. Pesan keluaran dilindungi dari forwarding bawaan Telegram. Admin bisa menyalin kode secara manual untuk diberikan kepada penerima.

Kode format WA- diikuti 32 karakter heksadesimal acak (128 bit). Setiap kode hanya bisa ditebus satu pengguna. Database menyimpan hash SHA-256, bukan kode mentah. Kode mentah hanya ditampilkan saat pembuatan; tidak ada perintah melihat ulang kode. Kode yang sudah ditebus tidak dapat dipakai akun lain. Tidak ada kedaluwarsa atau revoke pada paket ini.

Kode WA- berbeda dari VOC saldo. Tombol lama "Redeem Invitation Code" tetap untuk VOC saldo, tidak membuka allowlist OTP. Untuk kode WA-, tekan WhatsApp OTP lalu kirim kode; kode WA- juga dapat dikirim langsung di chat pribadi bot.

## Alur User

1. /start lalu tekan WhatsApp OTP.
2. Jika belum masuk allowlist, bot meminta kode dan tidak mengambil layanan atau menjalankan pembelian.
3. Kirim kode dari admin. Kode benar menambahkan akses permanen dan membuka menu OTP.
4. Kode tidak valid/sudah digunakan ditolak. Ada jeda tiga detik antara percobaan gagal dalam sesi bot; jeda ini tidak persisten setelah restart dan bukan rate limiter lintas server.
5. /cancel, Batal, atau Kembali ke Menu membatalkan input dan state pembelian yang tertunda. Tombol menu lain tetap bisa dipakai. /start juga mereset input.
6. Kunjungan berikutnya tidak perlu memasukkan kode lagi selama database persisten yang sama dipakai.

Bot Telegram biasa tidak menyediakan kolom input di popup callback. Implementasi memakai popup pemberitahuan saat callback yang diblokir, kemudian pesan chat untuk mengetik kode. Untuk tombol reply keyboard WhatsApp OTP, prompt muncul langsung sebagai pesan chat. Fitur undangan dan WhatsApp OTP hanya dilayani di chat pribadi agar kode dan detail order tidak tampil di grup.

## Cakupan Guard

Guard bersama diperiksa di menu OTP, input jumlah/direct buy, tombol layanan reply keyboard lama, cek pesanan, riwayat inline, callback beli, cek Kopken dari order, beli filter/batal otomatis, aksi order termasuk resend/batal/selesai, ganti nomor, serta jalur direct-buy QRIS background. Guard tidak sekadar menyembunyikan tombol. Topup biasa tetap tersedia dan tidak menampilkan keyboard layanan OTP kepada akun yang belum berizin.

Perbaikan kompatibilitas kecil: import get_otp_keyboard yang dipakai router tombol layanan dilengkapi, dan transaksi VOC saldo pada SQLite diberi commit yang hilang di source lama agar saldo redeem benar-benar tersimpan. Logika VOC PostgreSQL tidak diubah.

Allowlist tersimpan per user ID Telegram, bukan username. Redeem dan pemberian akses berada dalam satu transaksi. SQLite memakai BEGIN IMMEDIATE; PostgreSQL memakai advisory transaction lock per pengguna dan conditional UPDATE kode yang belum dipakai. Ini mencegah dua akun mengambil kode yang sama dan satu akun menghabiskan dua kode secara bersamaan. Kegagalan pemberian akses mengembalikan claim kode lewat rollback.

## Verifikasi Offline

Jalankan di lingkungan pengujian, bukan database produksi:

```bash
python -m pip install pytest python-telegram-bot psycopg2-binary httpx fastapi uvicorn
python -m pytest -q tests/test_invitations.py
python -m compileall -q bot_checker.py otp_handlers.py database.py invite_access.py tests
```

Pengujian memakai SQLite temporer yang terisolasi. Modul integrasi proyek yang tidak disertakan dimock, termasuk provider OTP, pembayaran, tracker, dan tarif. Tidak ada permintaan API atau pesan Telegram nyata. Tes memeriksa single-use, hash, persistensi setelah reload, race dua akun dan dua kode untuk satu akun, rollback, guard semua handler relevan, router keyboard/jumlah, admin/non-admin, batas generate, chat grup, navigasi, serta keberlanjutan VOC saldo.

PostgreSQL disiapkan dalam kode tetapi belum diuji dengan server PostgreSQL nyata. Telegram live, deployment, dan integrasi provider/pembayaran produksi juga belum diverifikasi. Tes offline tidak membuktikan seluruh fitur proyek lama bebas bug.
