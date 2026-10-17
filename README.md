# ScanNama PDF

Web scanner yang berjalan di laptop: crop dokumen, filter **No Shadow**, OCR nomor kartu dan nomor kunjungan, lalu ekspor PDF dengan nama otomatis.

## Alur penggunaan

1. Tekan **Ambil / pilih foto dokumen**. Foto langsung di-crop, diberi filter, dan ditambahkan sebagai halaman tanpa membuka editor manual.
2. Angka besar pada dokumen, misalnya `697`, langsung dibaca dan dimasukkan ke kolom nomor kartu.
3. Jika crop otomatis perlu diperbaiki, tekan **Atur** pada thumbnail halaman.
4. Tekan **Foto dekat No. Kunjungan / Resep** dan foto label beserta nomornya. Nomor langsung dibaca setelah foto dipilih.
5. Periksa nama `nomorkartu nomorkunjungan.pdf`, kemudian siapkan dan bagikan PDF. Nomor kartu dan nomor kunjungan dipisahkan oleh satu spasi.

### Berbagi dari Safari iPhone

Jangan membuka PDF lalu memakai tombol Share milik tampilan PDF Safari karena Safari dapat membagikan URL-nya. Gunakan tombol aplikasi:

1. Tekan **Siapkan PDF untuk dibagikan** dan tunggu sampai selesai.
2. Tekan **Bagikan PDF sekarang**.
3. Aplikasi hanya mengirim objek `File` bertipe `application/pdf` ke iOS Share Sheet, tanpa URL maupun teks judul tambahan. Nama PDF tetap tersimpan pada properti nama file.

Dua ketukan digunakan supaya izin user-activation Safari tidak kedaluwarsa selama server membuat PDF.

Setelah satu pasien selesai, tekan **Kosongkan & scan baru**. Tombol ini membersihkan halaman scan, foto OCR, kedua nomor, dan file share dari sesi browser setelah meminta konfirmasi. PDF yang sudah diunduh atau dibagikan tidak ikut dihapus.

### Checklist bulanan dan pencegahan duplikat

Setelah menu Share berhasil selesai, nomor pasien dan nomor kunjungan/resep dicatat pada checklist periode aktif. Progres dihitung berdasarkan nomor pasien yang berbeda, sehingga membagikan ulang nomor yang sama tidak menggandakan hitungan.

- Peringatan kuning muncul jika nomor pasien pernah dibagikan tetapi nomor kunjungan/resep berbeda.
- Peringatan merah muncul jika pasangan nomor pasien dan nomor kunjungan/resep sama persis.
- Tekan **Mulai bulan baru** untuk mengganti periode aktif dan target. Riwayat bulan lama tetap tersedia pada pilihan arsip.
- Tombol **Batalkan** pada checklist menghapus tanda selesai nomor tersebut hanya dari periode yang sedang aktif.

Riwayat checklist disimpan di `data/scan_history.sqlite3` pada laptop. Jangan menghapus file ini jika riwayat masih diperlukan, dan sertakan file tersebut dalam pencadangan rutin.

## Menggabungkan dua PDF di laptop

Buka `http://127.0.0.1:8000/merge` atau klik **Gabungkan PDF** pada bagian atas halaman scanner di laptop.

1. Pilih PDF pertama sebagai dokumen utama. Pratinjau muncul di bawah pilihan file; gunakan pilihan nomor halaman atau tombol panah untuk memeriksa setiap halaman.
2. Jika ada halaman terbalik, buka halaman tersebut lalu tekan **Putar halaman ini 180°**. Pratinjau langsung mengikuti orientasi hasil. Ulangi pada halaman lain bila perlu; pilihan halaman sebelumnya tetap tersimpan. Tekan **Kembalikan halaman ini** untuk membatalkan satu rotasi, atau **Reset semua rotasi** untuk membatalkan semuanya.
3. Pilih PDF kedua sebagai lampiran. Pratinjau PDF kedua juga tersedia, dan aplikasi langsung membaca Kreditur, Nomor Faktur, Nomor Penerimaan Barang, dan Tanggal Penerimaan Barang dari PDF tersebut. Tekan **Perbesar** pada pratinjau untuk melihat isi halaman dengan lebih jelas.
4. Konfirmasi atau edit kolom **Nama file hasil**.
5. Tekan **Gabungkan & pilih folder**. Pada Chrome atau Edge desktop, tentukan folder dan nama melalui dialog penyimpanan.

Rotasi hanya diterapkan pada halaman PDF pertama yang dipilih, dan ditambahkan terhadap orientasi halaman yang sudah ada. File sumber tidak ditimpa; teks dan gambar PDF tetap dipertahankan tanpa dirender ulang untuk hasil gabungan. Mengganti PDF pertama atau menekan **Kosongkan pilihan** akan menghapus pilihan rotasinya. Pratinjau memakai PDF.js yang disertakan lokal di `app/static/vendor/pdfjs`, sehingga tidak perlu mengambil library dari CDN saat digunakan.

Seluruh halaman PDF kedua ditempatkan setelah PDF pertama. Aplikasi memprioritaskan text layer PDF kedua, kemudian mencoba OCR pada gambar halaman jika teks tidak tersedia. Screenshot atau `Ctrl + V` tetap tersedia sebagai fallback di panel **PDF kedua tidak terbaca?**. Nama otomatis memakai format `KTD_nomorfaktur_nomorpenerimaan_DDMMYY.pdf`; tidak ada kode lokasi bawaan sehingga nama tetap cocok untuk kebutuhan selain DPS. `PT`, `P`, `TBK`, dan tanda `&` tidak dimasukkan ke singkatan kreditur. Nama tetap dapat diedit sebelum download. Jika file dengan nama yang sama sudah ada di folder Downloads, browser atau Windows dapat menambahkan `(1)` agar file lama tidak tertimpa.

Untuk foto yang berisi beberapa kertas bertumpuk, server memakai gabungan area teks sebagai batas crop. Untuk satu lembar yang tepinya terlihat jelas, server memakai deteksi empat sisi kertas.

OCR memakai label `No Kunjungan` atau `No Resep` sebagai jangkar, kemudian membaca area sempit di sebelah kanannya. Format `0226U0180926P000471` serta pola 12 digit + `Y` + 6 digit seperti `220208010926Y003452` dan `022600040926Y000202` didukung. Jika label gagal terbaca, hanya kandidat yang cocok dengan salah satu pola tersebut yang diterima sehingga nomor kartu BPJS atau tanggal tidak salah dipilih.

Nomor kunjungan/resep wajib **tepat 19 karakter**, termasuk huruf dan angka. Hasil OCR yang kurang atau lebih panjang ditolak tanpa dipotong atau ditambah karakter. Pembacaan fokus tetap mencoba membaca ulang; jika gagal, foto ulang atau isi manual. Kolom input menampilkan hitungan karakter, dan pembuatan PDF melalui halaman scanner serta pencatatan baru memerlukan nomor yang valid. Riwayat lama tetap dapat dibaca.

Nomor kartu/pasien hanya diambil dari **foto dokumen pertama**. Kandidat harus berupa angka murni berukuran besar; tanggal, nomor telepon, dan angka yang bercampur teks ditolak. Halaman PDF berikutnya tidak dapat menimpa nomor yang sudah terbaca.

## Menjalankan di Windows

### Cara paling mudah

Klik dua kali **MULAI.bat**. Satu launcher ini akan:

- menyalakan server scanner/OCR dan pembuat PDF;
- mengaktifkan halaman penggabung PDF;
- membuat Cloudflare Quick Tunnel;
- membuka scanner di browser laptop; dan
- menyalin URL Cloudflare ke clipboard untuk dibuka dari HP.

Alamat lokal scanner adalah `http://127.0.0.1:8000/`, sedangkan penggabung PDF berada di `http://127.0.0.1:8000/merge`. Untuk mematikan server dan tunnel, klik dua kali **BERHENTI.bat**. Jangan menjalankan `run.ps1` atau Uvicorn lagi ketika launcher sudah aktif karena port `8000` hanya dapat dipakai satu server.

Cloudflare Quick Tunnel memiliki URL acak yang berubah setiap kali dinyalakan kembali dan tidak memiliki login bawaan. Jangan membagikan URL tersebut. Untuk penggunaan rutin dengan data pasien, gunakan named tunnel yang dilindungi Cloudflare Access.

### Cara manual

Gunakan Python 3.11–3.13. Dari PowerShell di folder proyek:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Buka `http://127.0.0.1:8000`. Model OCR dimuat saat server pertama kali melakukan pemeriksaan kesehatan, jadi startup pertama dapat lebih lambat.

## Membuka dari HP

Untuk tes sementara, buka terminal PowerShell kedua:

```powershell
cloudflared tunnel --url http://localhost:8000
```

Buka URL `https://...trycloudflare.com` yang muncul. Quick Tunnel hanya untuk pengembangan. Untuk penggunaan data pasien, gunakan named tunnel, domain sendiri, dan lindungi dengan Cloudflare Access.

## Menjalankan tes

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

Tes browser pratinjau dan rotasi bersifat opsional. Instal `playwright` dengan `.\.venv\Scripts\python.exe -m pip install playwright` dan pastikan Microsoft Edge tersedia, lalu jalankan `.\.venv\Scripts\python.exe -m pytest tests/test_merge_browser.py`. Tes ini memeriksa navigasi halaman, rotasi dan pembatalannya, isi hasil unduhan, penggantian file, tampilan layar kecil, serta pemulihan setelah PDF tidak valid.

## Privasi

Server tidak menyimpan foto secara permanen. Upload diproses di laptop; buffer upload berukuran besar dapat masuk ke folder temporer sistem operasi dan akan dilepas setelah permintaan selesai. Checklist menyimpan periode, nomor pasien, nomor kunjungan/resep, nama PDF, dan waktu Share pada database lokal. Saat digunakan melalui Cloudflare Tunnel, upload tetap melewati jaringan Cloudflare menuju laptop, sehingga akses wajib dilindungi dan kebijakan fasilitas kesehatan tetap harus dipatuhi.
