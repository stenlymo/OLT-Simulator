# Panduan Huawei Datacom: Router dan Switch

Dokumen ini menjelaskan cara memakai perangkat **Huawei Router (eNSP-AR)**
dan **Huawei Switch (eNSP-LSW)** di OLT Simulator. Fokusnya adalah latihan CLI,
VLAN, routing, dan integrasi perangkat Datacom dengan OLT/FTTH.

## 1. Unduh dan jalankan simulator

Unduh ZIP dari [repositori OLT-Simulator](https://github.com/stenlymo/OLT-Simulator)
melalui **Code → Download ZIP**, lalu ekstrak. Pastikan folder `gui`, `modules`,
`api`, dan `icon` serta file `main.py` dan `requirements.txt` tetap berada di
folder proyek.

> Ikon dalam folder `icon` dipakai untuk menggambar perangkat di kanvas. Jangan
> menghapus atau memindahkan folder tersebut.

Pasang Python 3.10 atau lebih baru, lalu jalankan di PowerShell dari folder
proyek:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python main.py
```

Panduan instalasi lengkap dan opsi REST API ada di [README.md](README.md).

## 2. Tambahkan perangkat dan kabel

1. Jalankan simulator, lalu pilih **Huawei Router (eNSP-AR)** atau
   **Huawei Switch (eNSP-LSW)** dari Device Palette.
2. Tambahkan perangkat ke kanvas. Klik dua kali perangkat untuk membuka CLI.
3. Hubungkan port Ethernet yang sesuai. Catat nama port pada tooltip kabel;
   nama interface router/switch harus mengikuti port tersebut.
4. Ulangi langkah yang sama untuk OLT, PC, access point, atau perangkat lain
   yang diperlukan oleh topologi.
5. Pilih **Save Topology** setelah konfigurasi agar perangkat, kabel, dan data
   topologi tersimpan di database.

## 3. Login pertama dan mode CLI

Pada router atau switch yang belum memiliki password, CLI meminta Anda membuat
password saat pertama dibuka. Masukkan password 8–16 karakter dan konfirmasikan.
Password tersebut tersimpan untuk perangkat itu di database simulator.

Prompt mengikuti gaya Huawei:

```text
<HUAWEI>                 User view
[HUAWEI]                 System view
[HUAWEI-GE0/0/1]         Interface view
```

Perintah umum:

```text
system-view              # masuk ke konfigurasi global
sysname R1               # ubah nama perangkat
quit                     # kembali satu tingkat
return                   # kembali ke user view
save                     # simpan konfigurasi perangkat
display current-configuration
```

Perintah OLT memakai mode dan sintaks tersendiri; untuk OLT, ikuti contoh
di [README.md](README.md), [sim.md](sim.md), atau [mpls.md](mpls.md).

## 4. Contoh awal: VLAN pada switch

Contoh ini membuat VLAN 106, menjadikan GE1/0/1 access, dan GE1/0/2 trunk.
Sesuaikan port dan VLAN dengan tooltip kabel serta desain lab Anda.

```text
system-view
vlan batch 106
interface GE1/0/1
 port link-type access
 port default vlan 106
quit
interface GE1/0/2
 port link-type trunk
 port trunk allow-pass vlan 106
quit
display vlan
display current-configuration
save
```

Untuk menghubungkan switch ke OLT melalui trunk, pastikan VLAN yang sama
diizinkan pada port switch dan sudah dibuat/diikat ke uplink pada OLT. Untuk
integrasi router, tentukan apakah link memakai access VLAN dengan interface
fisik atau trunk VLAN dengan subinterface dot1q.

## 5. Contoh awal: alamat IP router

Alamat IP dikonfigurasi pada interface Layer 3 yang benar. Port dan alamat
berikut hanya contoh; gunakan port aktual pada kabel Anda.

```text
system-view
interface GE0/5/0
 undo portswitch
 ip address 10.0.0.1 255.255.255.252
quit
display ip interface brief
display ip routing-table
save
```

Untuk link point-to-point, ujung router lain harus memakai alamat dari subnet
yang sama, misalnya `10.0.0.2/30`. Pastikan kabel berstatus UP dan kedua ujung
menggunakan port serta kecepatan yang kompatibel sebelum menguji ping.

## 6. Fitur Datacom yang tersedia

Kemampuan yang dimodelkan simulator untuk latihan meliputi:

- **Switching** — VLAN access/trunk, STP, Ethernet link aggregation/LACP,
  VLANIF, dan koneksi ke OLT.
- **IPv4** — alamat interface, route connected/direct, static route, OSPF,
  IS-IS, BGP, DHCP, ACL, dan NAT.
- **IPv6** — alamat interface, IPv6 routing, OSPFv3, dan fitur terkait.
- **MPLS dan VPN** — MPLS/LDP, L3VPN/MP-BGP VPNv4, EVPN, VXLAN, SR-MPLS,
  dan SRv6.
- **Akses jaringan** — WLAN/Fit AP, DHCP, dan integrasi topologi dengan OLT,
  ONT, PC, BRAS, serta server.

Sintaks dan cakupan tiap perintah dapat berbeda dari versi VRP perangkat nyata.
Gunakan `?` atau `display` yang tersedia pada CLI untuk memeriksa konfigurasi
dan status yang dimodelkan.

## 7. Contoh lab lanjutan

- [sim.md](sim.md) — contoh topologi PC_6 ke PC_11 melalui switch, OLT,
  router, OSPF, IS-IS, dan MPLS LDP.
- [mpls.md](mpls.md) — lab L3VPN dari ONT sampai PE, dengan VLAN site berbeda,
  OSPF underlay, LDP, VRF, dan MP-BGP VPNv4.
- [HCIE Bab 6: Campus deployment](docs/HCIE_CHAPTER_6.md)
- [HCIE Bab 8: SR-MPLS](docs/HCIE_CHAPTER_8_SRMPLS.md)
- [HCIE Bab 9: SRv6](docs/HCIE_CHAPTER_9_SRV6.md)

## 8. Catatan tentang hasil simulasi

Simulator ditujukan untuk pembelajaran CLI, konfigurasi, status protokol, dan
perilaku topologi yang didukung. Untuk beberapa teknologi advanced—termasuk
MPLS, SR-MPLS, SRv6, EVPN, dan VXLAN—sebagian fungsi merupakan model
control-plane dan belum membentuk atau meneruskan paket dengan format wire
seperti perangkat produksi. Keberhasilan adjacency atau route tidak selalu
membuktikan forwarding data-plane end-to-end.

Jika hasil lab tidak sesuai, periksa berurutan: status kabel dan port, alamat
IP/mask, VLAN access/trunk, interface yang dipilih, adjacency protokol, tabel
routing, lalu batas model simulator pada dokumen fitur terkait.
