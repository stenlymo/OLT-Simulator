# Huawei SmartAX GPON OLT Network Simulator

Simulator GUI untuk latihan konfigurasi Huawei SmartAX GPON OLT. Aplikasi
memakai Python, PyQt6, dan SQLite; mendukung topologi FTTH, CLI OLT, konfigurasi
ONT, layanan DHCP/PPPoE, serta beberapa fitur simulasi jaringan.

## Panduan instalasi Windows dari Google Drive

Langkah berikut ditujukan untuk pengguna yang mengunduh salinan folder proyek
dari Google Drive. Tidak perlu memasang Git.

### 1. Unduh dan ekstrak proyek

1. Unduh file ZIP proyek dari Google Drive.
2. Di File Explorer, klik kanan ZIP lalu pilih **Extract All / Ekstrak Semua**.
3. Simpan folder hasil ekstrak di lokasi yang bisa ditulis, misalnya
   `C:\olt-simulator` atau folder Documents.
4. Pastikan file `main.py`, `requirements.txt`, dan folder `gui`, `modules`,
   serta `api` ada di dalam folder yang sama.

> Jangan membagikan folder `venv` milik komputer pembuat. Virtual environment
> berisi file khusus mesin/lokasi Python tersebut; setiap pengguna membuat
> environment baru dengan langkah di bawah. File database `olt_simulator.db`
> boleh disertakan bila ingin membagikan konfigurasi/topologi awal. Hilangkan
> file itu dari salinan bila setiap orang harus mulai dengan database baru.

### 2. Pasang Python

Pasang Python **3.10 atau lebih baru** untuk Windows dari
[python.org](https://www.python.org/downloads/windows/). Saat installer muncul,
aktifkan pilihan **Add python.exe to PATH**, lalu selesaikan instalasi.

Buka PowerShell baru dan pastikan Python tersedia:

```powershell
py --version
```

Jika perintah `py` tidak ditemukan, coba `python --version`. Pastikan versi yang
muncul 3.10 atau lebih baru.

### 3. Buka PowerShell pada folder proyek

Di File Explorer, buka folder proyek hasil ekstrak, klik kanan area kosong,
lalu pilih **Open in Terminal**. Atau buka PowerShell dan pindah ke foldernya:

```powershell
cd C:\olt-simulator
```

Ganti jalur tersebut jika folder disimpan di tempat lain. Perintah berikutnya
harus dijalankan dari folder yang berisi `main.py`.

### 4. Buat virtual environment

```powershell
py -m venv .venv
```

Aktifkan environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

Jika PowerShell menolak aktivasi karena kebijakan skrip, aktivasi tidak wajib;
lanjutkan instalasi menggunakan `.\.venv\Scripts\python.exe` pada langkah 5.

### 5. Pasang dependensi

Dengan environment aktif:

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Jika environment tidak diaktifkan, gunakan:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Instalasi PyQt6 mungkin memerlukan koneksi internet dan beberapa menit.

### 6. Jalankan simulator

```powershell
python main.py
```

Atau jika environment tidak diaktifkan:

```powershell
.\.venv\Scripts\python.exe main.py
```

Jendela **Huawei SmartAX OLT Network Simulator** akan terbuka. Tarik perangkat
dari **Device Palette** ke kanvas, hubungkan kabel, lalu klik dua kali OLT untuk
membuka console CLI. Pilih **Save Topology** untuk menyimpan topologi dan
konfigurasi ke database SQLite.

### 7. Jalankan kembali setelah instalasi pertama

Setiap kali ingin menggunakan simulator:

1. Buka PowerShell di folder proyek.
2. Aktifkan environment dengan `.\.venv\Scripts\Activate.ps1` atau panggil
   Python di dalam `.venv` secara langsung.
3. Jalankan `python main.py`.

Versi singkat tanpa aktivasi:

```powershell
cd C:\olt-simulator
.\.venv\Scripts\python.exe main.py
```

### Masalah umum

- **`py` / `python` tidak dikenali** — pasang ulang Python dan centang **Add
  python.exe to PATH**, lalu buka PowerShell baru.
- **`No module named PyQt6`** — pastikan perintah instalasi dependensi selesai
  tanpa error dan dijalankan menggunakan Python dari `.venv` yang sama.
- **Jendela langsung tertutup atau muncul traceback** — jalankan dari PowerShell,
  bukan dengan klik ganda `main.py`, agar pesan error tetap terlihat.
- **Konfigurasi/topologi lama masih muncul** — simulator membaca
  `olt_simulator.db` di folder proyek. Simpan salinan database sebelum
  menggantinya; untuk mulai baru, tutup aplikasi lalu pindahkan atau hapus file
  database tersebut.

## Menjalankan REST API (opsional)

REST API tidak diperlukan untuk memakai GUI. Untuk web NMS/API, buka PowerShell
di folder proyek dan jalankan:

```powershell
.\.venv\Scripts\python.exe run_api.py
```

Lalu buka `http://127.0.0.1:8080/`. Gunakan `--port 9000` untuk memilih port
lain. API dan GUI memakai file SQLite yang sama jika dijalankan dari folder
proyek yang sama.

## Persyaratan singkat

- Windows 10/11 64-bit.
- Python 3.10 atau lebih baru.
- Koneksi internet saat pertama memasang paket Python.
- Ruang disk untuk Python, PyQt6, dan database SQLite.

Setelah instalasi, GUI dapat digunakan secara lokal tanpa koneksi internet.

---

## Dokumentasi fitur dan contoh konfigurasi

- [Panduan perhitungan GPON link budget](docs/link_budget.md)

## ✨ Perintah CLI Tambahan (modul `modules/commands/extended.py`)

Perintah berikut sebelumnya belum ada dan sekarang tersedia di GUI console
maupun CLI terminal:

| Perintah | Fungsi |
|---|---|
| `display version` | Versi software OLT (MA5800 V100R026C00) |
| `display current-configuration` | Seluruh konfigurasi aktif (sysname, VLAN, VLANIF, route, profil, service-port) |
| `display current-configuration ont <f/s/p> <ont-id>` | Konfigurasi satu ONT |
| `display ip routing-table` | Tabel routing (Direct dari VLANIF + Static dari `ip route-static`) |
| `display ont optical-info <port> {all\|<id>}` | Daya optik Rx/Tx per ONT (dari DB, fallback simulasi deterministik) |
| `display ont wan-info <f/s/p> <ont-id>` | Daftar WAN/service-port ONT beserta IP dan VLAN |
| `ont deactivate <port> <ont-id>` | Menonaktifkan ONT (control flag `deactive`, offline) |
| `ont activate <port> <ont-id>` | Mengaktifkan kembali ONT |
| `ont delete <port> <ont-id>` | Menghapus ONT dan melepas SN kembali ke `ont_sn_pool` |
| `lldp enable [port]` / `lldp disable` / `undo lldp enable` | Mengaktifkan/mematikan LLDP (disimpan di `olt_system_config`) |
| `display lldp neighbor [port]` | Tetangga LLDP dari topologi (`olt_device_links`) |
| `display port state {all\|<port>}` | Status port uplink (up/down, duplex, speed, tipe fiber) |
| `display port opticstate <port>` | Diagnostik modul optik (suhu, tegangan, bias, Tx/Rx) |

Perintah tak dikenal sekarang menampilkan
`Error: Unrecognized command found at '^' position.` seperti perangkat asli.

## 🔌 ONT Auto-Find Mengikuti Kondisi Kabel

`display ont autofind [<port>|all]` dan `ont confirm` kini memeriksa
**konektivitas fisik** (`olt_device_links`, `is_up = 1`) dari OLT ke ONT,
termasuk jalur lewat splitter/OTB/roset:

- kabel tersambung → SN ONT muncul di autofind;
- kabel dicabut, link LOS (`is_up = 0`), atau beda port GPON → tidak ada ONT;
- menelusuri rantai OLT → OTB → splitter → roset → ONT (BFS), bukan hanya link langsung;
- ONT yang terhubung tetapi belum punya baris di `olt_onts` (mis. hasil
  *Load Topology*) otomatis didaftarkan dengan SN dari `ont_sn_pool`, jadi tetap
  terdeteksi tanpa perlu kabel dibuat ulang;
- hanya node ONT (`ONT_*`/yang sudah terdaftar) yang dianggap ONT — splitter,
  OTB, dan roset tidak ikut terdaftar;
- ONT yang sudah `ont add`/`ont confirm` otomatis hilang dari daftar autofind;
- alias `display ont-auto-find` dan `display ont auto-find` diterima;
- tabel `olt_autofind` disinkronkan setiap kali perintah dijalankan, sehingga
  `ont confirm` menolak SN yang tidak sedang terhubung.

## 🌐 REST API untuk Web Application

Tersedia server API bawaan (hanya stdlib, tanpa dependensi tambahan) sehingga
topologi dan konfigurasi OLT/ONT bisa dibaca dan diubah dari web app.

```powershell
python run_api.py                 # http://127.0.0.1:8080 (pindah port otomatis bila sibuk)
python run_api.py --port 9000     # port lain
python run_api.py --strict-port   # gagal bila port tidak tersedia
python run_api.py --db lain.db --token rahasia   # DB & token Bearer opsional
```

> Jika `8080` diblokir Windows (`WinError 10013`), biasanya port dipakai
> layanan lain — sering `iphlpsvc`/WinNAT (WSL2, Docker, Hyper-V). Server
> otomatis mencoba `8081`, `8082`, … dan mencetak URL yang dipakai. Cek pemakai
> port dengan `netstat -ano | findstr :8080`, lalu
> `tasklist /svc /FI "PID eq <pid>"`.

- **Halaman NMS**: <http://127.0.0.1:8080/> — pilih **OLT** dari topologi, klik
  **ONT Autofind** untuk tabel `Number | F/S/P | Ont SN | VendorID | Ont Version |
  Ont SoftwareVersion | Ont EquipmentID | Ont autofind time`, plus console CLI
  untuk query/konfigurasi.
- **Deep link**: `http://127.0.0.1:8080/?node=Huawei_1&autofind=1`
- **Dokumentasi endpoint**: <http://127.0.0.1:8080/api/v1/docs>

`GET /nodes/{node}/autofind` dan perintah CLI `display ont autofind [port|all]`
menghitung ONT dari **konektivitas fisik** (BFS: OLT → splitter/roset → ONT),
lalu menyinkronkan `olt_onts`/`olt_autofind`, sehingga hasil API sama dengan
tampilan terminal GUI.

Halaman NMS memakai **auto-refresh 5 detik** (bisa dimatikan) dan respons API
selalu `Cache-Control: no-store`, jadi tabel ikut berubah saat topologi
ditambah/dicabut. Saat start, server mencetak path database yang dipakai:

```
Database: C:\olt-simulator\olt_simulator.db
```

Jika path itu bukan database utama Anda, server akan menampilkan data lama —
hentikan proses lama lalu jalankan ulang dari folder proyek (atau pakai
`--db`).

| Method | Endpoint | Keterangan |
|---|---|---|
| GET | `/api/v1/health` | Status server |
| GET | `/api/v1/topology` | Node + link topologi |
| GET | `/api/v1/nodes` | Daftar node |
| GET | `/api/v1/nodes/{node}` | Inventaris lengkap (board, VLAN, VLANIF, route, ONT, service-port, profil) |
| GET | `/api/v1/nodes/{node}/onts` | Daftar ONT |
| GET | `/api/v1/nodes/{node}/onts/{id}` | Detail satu ONT |
| GET | `/api/v1/nodes/{node}/autofind` | ONT terhubung yang belum diprovisioning |
| GET | `/api/v1/nodes/{node}/{boards,vlans,vlanifs,routes,service-ports,profiles,ports,links}` | Potongan inventaris |
| POST | `/api/v1/nodes/{node}/cli` | Jalankan satu perintah CLI |
| POST | `/api/v1/nodes/{node}/cli/batch` | Jalankan beberapa perintah berurutan |
| POST | `/api/v1/sessions` | Buat sesi CLI |
| DELETE | `/api/v1/sessions/{id}` | Hapus sesi |

### Contoh

```bash
curl http://127.0.0.1:8080/api/v1/nodes/Huawei_1/onts

curl -X POST http://127.0.0.1:8080/api/v1/nodes/Huawei_1/cli \
  -H "Content-Type: application/json" \
  -d '{"command":"enable"}'

curl -X POST http://127.0.0.1:8080/api/v1/nodes/Huawei_1/cli \
  -H "Content-Type: application/json" -H "X-Session-Id: <id>" \
  -d '{"command":"config"}'
```

```javascript
// web app: pertahankan mode CLI lewat header X-Session-Id
const res = await fetch("http://127.0.0.1:8080/api/v1/nodes/Huawei_1/cli", {
  method: "POST",
  headers: { "Content-Type": "application/json", "X-Session-Id": sessionId },
  body: JSON.stringify({ command: "display ont info 0 all" }),
});
const { ok, data } = await res.json();
console.log(data.prompt, data.output);
```

Catatan: API dan GUI memakai database SQLite yang sama (aman untuk baca/tulis
bergantian); jalankan API di mesin yang sama agar data topologi selalu sinkron.
CORS terbuka (`*`) untuk pengembangan web app lokal.

## 🧩 Provisioning ONT (DBA → Line Profile → Service Profile → ONT → Service Port)

Urutan perintah berikut sudah diuji end-to-end lewat API maupun terminal GUI
(contoh: SN `414C434CB46C2E75`, OLT `Huawei_1`, F/S/P `0/1/3`, VLAN `20`):

```text
enable
config
dba-profile add profile-id 40 profile-name WEB_DBA type4 max 1024000
ont-lineprofile gpon profile-id 40 profile-name WEB_LP
tcont 1 dba-profile-id 40
gem add 1 eth tcont 1 encrypt on
gem mapping 1 0 vlan 20
commit
quit
ont-srvprofile gpon profile-id 40 profile-name WEB_SP
ont-port eth 4 pots 2
commit
quit
vlan 20 smart
interface gpon 0/1
ont add 3 0 sn-auth 414C434CB46C2E75 omci ont-lineprofile-id 40 ont-srvprofile-id 40 desc WEB-ONT
quit
service-port 120 vlan 20 gpon 0/1/3 ont 0 gemport 1 multi-service user-vlan 20 tag-transform translate
```

Lewat web/API bisa satu batch (sesi CLI dipertahankan otomatis):

```bash
curl -X POST http://127.0.0.1:8081/api/v1/nodes/Huawei_1/cli/batch \
  -H "Content-Type: application/json" \
  -d '{"commands":["enable","config","display ont info 3 0"]}'
```

Setelah `ont add`, ONT otomatis hilang dari daftar autofind dan muncul di
`GET /nodes/{node}/onts` dengan `control_flag = active`.

Verifikasi lewat CLI (semua varian didukung):

```text
display ont info all          # seluruh ONT di OLT ini
display ont info 3 all        # semua ONT di port 0/1/3
display ont info 3 0          # ONT 0 pada port 0/1/3
display ont info 0/1/3 0      # format F/S/P lengkap
```

## 📡 DHCP Server, BRAS/PPPoE, dan WAN ONT

Device **DHCP Server** dan **PPPoE Server (BRAS)** tersedia di Device Palette;
hubungkan keduanya ke OLT (port uplink `0/2/0`–`0/2/3`), lalu **klik dua kali**
pada node untuk membuka dialog konfigurasinya:

- **DHCP Server** — form pool (nama, **Local IP server**, start/end IP, gateway,
  DNS, VLAN) + daftar lease aktif.
- **PPPoE Server (BRAS)** — **Local IP server**, tab *Akun PPPoE*
  (tambah/ubah/hapus user) dan tab *Sesi Aktif*.
- **ONT** — dialog properti ONT memiliki bagian **PPPoE Client (WAN)**:
  username, password, VLAN, tombol **Simpan / Connect / Disconnect**, serta
  status *Connected — IP …* atau *Disconnected*.

IP lokal server dan status PPPoE ONT juga tampil langsung pada node di kanvas
(baris kecil di bawah nama perangkat) dan disegarkan otomatis tiap 3 detik.

Konfigurasi juga bisa lewat REST API atau panel **“Layanan WAN — PPPoE / DHCP”**
di halaman NMS.

```bash
# pool DHCP di node DHCP_1
curl -X POST http://127.0.0.1:8081/api/v1/nodes/DHCP_1/dhcp \
  -H "Content-Type: application/json" \
  -d '{"start_ip":"10.20.0.10","end_ip":"10.20.0.20","gateway":"10.20.0.1","dns":"8.8.8.8","vlan":20}'

# akun PPPoE di node BRAS_1
curl -X POST http://127.0.0.1:8081/api/v1/nodes/BRAS_1/pppoe-users \
  -H "Content-Type: application/json" \
  -d '{"username":"budi@isp","password":"rahasia","profile":"20M","vlan":20}'
```

Konfigurasi sisi ONT lewat CLI (GUI console maupun API):

```text
interface gpon 0/1
ont pppoe-config 6 0 user budi@isp password rahasia vlan 20
ont pppoe-connect 6 0
display ont wan-info 6 0
display dhcp lease
display pppoe session
ont pppoe-disconnect 6 0
```

### IPoE (VLAN + MAC) dan DHCP Option 82

Selain PPPoE, ONT bisa memakai **IPoE** — cukup VLAN dan MAC address:

```text
dhcp option82 enable                     # global config (enable|disable|forward|rebuild)
ont ipoe-config 6 0 vlan 20 mac 00:11:22:33:44:55
ont ipoe-connect 6 0
display ont wan-info 6 0
ont ipoe-disconnect 6 0
```

Saat option82 aktif, lease DHCP menyimpan **circuit-id** (`OLT:port:ont`) dan
**remote-id** (MAC ONT) sehingga DHCP server/BRAS dapat mengidentifikasi user.
Option82 juga bisa per port: `dhcp option82 port 0/1/0 enable`.

API: `POST /nodes/{node}/onts/{id}/ipoe` dengan body
`{"port":"0/1/6","vlan":20,"mac":"00:11:22:33:44:55"}`.

### ONT port native-vlan (bridge) + PPPoE dari PC

Sesuai dokumen `ont port native-vlan`, port LAN ONT bisa di-bridge ke VLAN
tertentu (untagged), sehingga PC di belakang ONT bisa dial PPPoE sendiri:

```text
interface gpon 0/1
ont port native-vlan 3 0 eth 1 vlan 20
display ont port native-vlan
```

Lalu pada **PC** (klik dua kali node PC) isi PPPoE username/password dan klik
**Connect** — PC melakukan dial melalui ONT bridge ke BRAS, lalu mendapat IP
dari DHCP server. Status tampil di dialog PC dan pada node (`10.20.0.10`).

Syarat: ONT online, native-vlan port ONT sudah diset, akun PPPoE ada di BRAS,
dan DHCP pool tersedia.

Alur simulasi: periksa konektivitas ONT (kabel/LOS) → autentikasi kredensial ke
BRAS → bila cocok, IP dialokasikan dari pool DHCP server → `olt_onts.wan_ip`
dan `wan_status = online`. Password salah → `auth-failed`, tanpa IP.

**Banyak ONT sekaligus**: setiap ONT (PPPoE maupun IPoE) mendapat IP unik dari
pool; BRAS menampilkan seluruh sesi aktif dalam satu tabel — kolom *Type*
(PPPoE/IPoE), *Circuit-ID* dan *Remote-ID* (Option 82). Disconnect satu ONT
hanya melepas lease ONT tersebut; ONT lain tetap online. Kapasitas dibatasi
ukuran pool DHCP.

Tabel baru: `sim_dhcp_pools`, `sim_bras_users`, `sim_leases`, `sim_sessions`;
kolom ONT: `pppoe_user`, `pppoe_pass`, `wan_vlan`, `wan_ip`, `wan_status`.

## 🔌 Native VLAN / Access Port

Mengikuti dokumen `native-vlan`: port ETH bisa dijadikan **access port** dengan
native VLAN (paket VLAN tersebut keluar *untagged*).

```text
config
vlan 20 smart
port vlan 20 0/2 0          # port harus jadi anggota VLAN dulu
interface eth 0/2
native-vlan 0 vlan 20       # 0/2/0 = access, VLAN 20 untagged
display native-vlan
undo native-vlan 0
```

Perilaku topologi:

- **OLT ↔ PC / DHCP Server / BRAS** otomatis memakai port ETH `0/2/0`–`0/2/3`
  dan dikonfigurasi sebagai **access port** (VLAN 20 native/untagged) saat
  kabel dibuat.
- **OLT ↔ router** tetap **trunk** (VLAN bertag); log menampilkan mode trunk.
- API: `GET /nodes/{node}/native-vlans`.

Setelah access port + `interface vlanif20` diberi IP, OLT bisa **ping** ke IP
lokal DHCP/BRAS selama satu subnet dengan VLANIF dan port-nya access:

```text
interface vlanif20
 ip address 10.20.0.1 255.255.255.0
 quit
ping 10.20.0.254
```

Perintah display mengikuti dokumen Huawei:

```text
display vlan 20            # detail: nama, tipe, atribut, port
display vlan all           # ringkasan semua VLAN
display vlan number        # statistik tipe VLAN
display port vlan 0/2/0    # daftar VLAN port + Native VLAN
```

## 🧪 Uji Cepat

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\venv\Scripts\python.exe .\tmp\probe_cli.py        # inventaris perintah
.\venv\Scripts\python.exe .\tmp\verify_features.py  # 23 pemeriksaan fitur + regresi
.\venv\Scripts\python.exe .\tmp\verify_autofind.py  # 8 skenario kabel/autofind
.\venv\Scripts\python.exe .\tmp\verify_api.py       # 16 pemeriksaan REST API
.\venv\Scripts\python.exe .\tmp\verify_services.py  # DHCP/PPPoE (CLI + HTTP)
.\venv\Scripts\python.exe .\tmp\verify_services_gui.py  # perintah PPPoE di terminal GUI
```

## Port mirroring dan Wireshark simulator

Tambahkan **Wireshark Packet Analyzer** dari Device Palette, lalu hubungkan
port `Ethernet0`-nya ke salah satu uplink OLT `0/2/0`–`0/2/3`. Di terminal
OLT, mirror traffic dari uplink sumber ke port yang terhubung ke Wireshark:

```text
config
interface eth 0/2
mirror port 0 1 all
display mirror port
```

Angka `0` dan `1` adalah port sumber dan tujuan pada board `0/2`; pilih port
tujuan yang benar-benar tersambung ke Wireshark. Arah dapat diatur sebagai
`ingress`, `egress`, atau `all`. Hapus konfigurasi dengan
`undo mirror port 0 1 all` atau `undo mirror port`. Double-click Wireshark
untuk membuka capture; aktivitas PPPoE menampilkan PADI, PADO, PADR, PADS,
negosiasi PPP, dan event IPv4. Capture berisi ringkasan paket simulasi
terpilih, bukan perekaman semua frame. Konfigurasi mirror bersifat sementara
dan tidak masuk ke file konfigurasi.


