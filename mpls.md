# Lab L3VPN over MPLS: ONT sampai PE Router

Dokumen ini membangun satu layanan pelanggan dari PC yang terhubung ke ONT, melewati OLT dan switch akses, lalu masuk ke VRF pada PE router. Dua site memakai VLAN dan subnet berbeda. PE menukar route pelanggan melalui MP-BGP VPNv4, sedangkan core memakai OSPF sebagai IGP dan MPLS LDP sebagai transport label.

## Tujuan pembelajaran

1. Memisahkan jaringan pelanggan dari global routing table dengan VRF.
2. Membedakan RD dan RT pada VPNv4.
3. Membentuk underlay IP dan LDP antar PE/P.
4. Membentuk MP-BGP VPNv4 antara PE1 dan PE2 menggunakan Loopback.
5. Mengikuti bagaimana route pelanggan dari satu site masuk ke VPN RIB site lainnya.

## 1. Topologi

Gunakan empat router dari topology simulator:

- **Router_5 = PE1**: gateway site PC_6.
- **Router_20 = P1**: provider/core router.
- **Router_23 = P2**: provider/core router.
- **Router_22 = PE2**: gateway site PC_11.

Jalur layanan pelanggan:

```text
PC_6 -- ONT_1 -- Splitter -- Huawei_3/OLT1 -- Switch_19 -- PE1
                                                        |
                                                       P1 -- P2 -- PE2
                                                                  |
PC_11 -- ONT_10 -- Splitter -- Huawei_8/OLT2 -- Switch_7 --------+
```

P1 dan P2 hanya membawa underlay/MPLS. Konfigurasi VRF pelanggan dibuat pada PE1 dan PE2.

### Port kabel yang disarankan

| Perangkat A | Port A | Perangkat B | Port B | Fungsi |
|---|---|---|---|---|
| PE1 / Router_5 | GE0/5/0 | P1 / Router_20 | GE0/5/0 | Core link 1 |
| P1 / Router_20 | GE0/5/1 | P2 / Router_23 | GE0/5/0 | Core link 2 |
| P2 / Router_23 | GE0/5/1 | PE2 / Router_22 | GE0/5/0 | Core link 3 |
| PE1 / Router_5 | GE0/0/0 | Switch_19 | GE1/0/1 | Customer-facing trunk to PE subinterface |
| Switch_19 | GE1/0/2 | Huawei_3 / OLT1 | 0/2/0 | OLT uplink |
| Huawei_3 / OLT1 | GPON 0/1/0 | Splitter_2 | IN-1 | GPON access |
| Splitter_2 | OUT-1 | ONT_1 | GPON | GPON access |
| ONT_1 | ETH-1 | PC_6 | Ethernet0 | Customer LAN |
| PE2 / Router_22 | GE0/0/0 | Switch_7 | GE1/0/1 | Customer-facing trunk to PE subinterface |
| Switch_7 | GE1/0/2 | Huawei_8 / OLT2 | 0/2/0 | OLT uplink |
| Huawei_8 / OLT2 | GPON 0/1/0 | Splitter_9 | IN-1 | GPON access |
| Splitter_9 | OUT-1 | ONT_10 | GPON | GPON access |
| ONT_10 | ETH-1 | PC_11 | Ethernet0 | Customer LAN |

Jika kabel yang sudah dibuat memakai port berbeda, gunakan nama port yang tampil saat kabel dipilih dan ganti nama interface pada contoh konfigurasi.

## 2. Rencana alamat

### Underlay dan loopback

| Router | Interface | Alamat | Terhubung ke |
|---|---|---|---|
| PE1 / Router_5 | LoopBack0 | 10.255.0.5/32 | BGP router ID, LSR ID |
| PE1 / Router_5 | GE0/5/0 | 10.0.0.1/30 | P1 |
| P1 / Router_20 | LoopBack0 | 10.255.0.20/32 | Router ID, LSR ID |
| P1 / Router_20 | GE0/5/0 | 10.0.0.2/30 | PE1 |
| P1 / Router_20 | GE0/5/1 | 10.0.0.5/30 | P2 |
| P2 / Router_23 | LoopBack0 | 10.255.0.23/32 | Router ID, LSR ID |
| P2 / Router_23 | GE0/5/0 | 10.0.0.6/30 | P1 |
| P2 / Router_23 | GE0/5/1 | 10.0.0.9/30 | PE2 |
| PE2 / Router_22 | LoopBack0 | 10.255.0.22/32 | BGP router ID, LSR ID |
| PE2 / Router_22 | GE0/5/0 | 10.0.0.10/30 | P2 |

### Site pelanggan

| Site | VLAN access | VRF | Gateway PE | PC |
|---|---:|---|---|---|
| Site 1 / OLT1 | 106 | CUST_A | 192.168.106.1/24 | PC_6: 192.168.106.6/24 |
| Site 2 / OLT2 | 111 | CUST_A | 192.168.111.1/24 | PC_11: 192.168.111.11/24 |

RT untuk dua site: `65000:100`. RD dibuat unik per PE: PE1 `65000:5`, PE2 `65000:22`. Gateway masing-masing menjadi default gateway PC.

## 3. Konfigurasi L2 akses pelanggan

VLAN pelanggan berjalan bertag dari OLT melewati switch sampai ke PE. PE memakai subinterface 802.1Q per VLAN, lalu menempatkan subinterface itu di VRF. Cara ini diperlukan karena simulator menerima `ip binding vpn-instance` pada subinterface.

### Switch_19

```text
system-view
vlan batch 106
interface GE1/0/1
 port link-type trunk
 port trunk allow-pass vlan 106
quit
interface GE1/0/2
 port link-type trunk
 port trunk allow-pass vlan 106
quit
save
```

### Switch_7

```text
system-view
vlan batch 111
interface GE1/0/1
 port link-type trunk
 port trunk allow-pass vlan 111
quit
interface GE1/0/2
 port link-type trunk
 port trunk allow-pass vlan 111
quit
save
```

## 4. Konfigurasi OLT, ONT, dan service-port

Uplink OLT ke switch menggunakan port Ethernet `0/2/0`. PON ke splitter menggunakan `0/1/0`. Contoh ini memakai DBA 10 Mbps, GEM 10, dan VLAN lokal yang berbeda pada tiap OLT.

### Huawei_3 / OLT1 / VLAN 106

Jika ONT_1 sudah terdaftar, jalankan `display ont info 0 all` dan catat ONT ID. Jika belum, aktifkan auto-find dan gunakan SN dari `display ont autofind 0` sebagai pengganti `<SN_ONT_1>`.

```text
enable
config
vlan 106 smart
port vlan 106 0/2 0
dba-profile add profile-name DBA_10M type3 assure 10240 max 10240
ont-lineprofile gpon profile-id 10
 tcont 4 dba-profile-name DBA_10M
 gem add 10 eth tcont 4
 gem mapping 10 0 vlan 106
 commit
quit
ont-srvprofile gpon profile-id 10
 ont-port eth adaptive
 port vlan eth 1 106
 commit
quit
interface gpon 0/1
 port 0 ont-auto-find enable
 display ont autofind 0
 ont add 0 1 sn-auth <SN_ONT_1> omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc SITE1
quit
traffic table ip index 10 cir 4096 priority user-cos 4 priority-policy local-Setting
service-port vlan 106 gpon 0/1/0 ont 1 gemport 10 inbound traffic-table index 10 outbound traffic-table index 10
save
```

Jika ONT sudah terdaftar, lewati `ont add` dan gunakan ID ONT aktual pada service-port.

### Huawei_8 / OLT2 / VLAN 111

```text
enable
config
vlan 111 smart
port vlan 111 0/2 0
dba-profile add profile-name DBA_10M type3 assure 10240 max 10240
ont-lineprofile gpon profile-id 10
 tcont 4 dba-profile-name DBA_10M
 gem add 10 eth tcont 4
 gem mapping 10 0 vlan 111
 commit
quit
ont-srvprofile gpon profile-id 10
 ont-port eth adaptive
 port vlan eth 1 111
 commit
quit
interface gpon 0/1
 port 0 ont-auto-find enable
 display ont autofind 0
 ont add 0 1 sn-auth <SN_ONT_10> omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc SITE2
quit
traffic table ip index 10 cir 4096 priority user-cos 4 priority-policy local-Setting
service-port vlan 111 gpon 0/1/0 ont 1 gemport 10 inbound traffic-table index 10 outbound traffic-table index 10
save
```

Ganti placeholder SN dan ONT ID jika perangkat pada topology sudah memiliki nilai yang berbeda.

## 5. Underlay OSPF pada PE dan P router

OSPF hanya membawa route infrastruktur: link /30 dan Loopback0. Jangan aktifkan OSPF pada interface fisik GE0/0/0 di PE; route pelanggan harus masuk ke VRF.

### PE1 / Router_5

```text
system-view
ospf 1 router-id 10.255.0.5
quit
interface LoopBack0
 ip address 10.255.0.5 255.255.255.255
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.1 255.255.255.252
 ospf enable 1 area 0
quit
```

### P1 / Router_20

```text
system-view
ospf 1 router-id 10.255.0.20
quit
interface LoopBack0
 ip address 10.255.0.20 255.255.255.255
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.2 255.255.255.252
 ospf enable 1 area 0
quit
interface GE0/5/1
 ip address 10.0.0.5 255.255.255.252
 ospf enable 1 area 0
quit
```

### P2 / Router_23

```text
system-view
ospf 1 router-id 10.255.0.23
quit
interface LoopBack0
 ip address 10.255.0.23 255.255.255.255
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.6 255.255.255.252
 ospf enable 1 area 0
quit
interface GE0/5/1
 ip address 10.0.0.9 255.255.255.252
 ospf enable 1 area 0
quit
```

### PE2 / Router_22

```text
system-view
ospf 1 router-id 10.255.0.22
quit
interface LoopBack0
 ip address 10.255.0.22 255.255.255.255
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.10 255.255.255.252
 ospf enable 1 area 0
quit
```

Pada semua router, `display ospf peer` harus menunjukkan neighbor OSPF di tiap core link. `display ip routing-table` harus menunjukkan route menuju Loopback PE lawan melalui underlay.

## 6. Aktifkan MPLS LDP

Setiap PE/P memakai Loopback0 sebagai LSR ID. Aktifkan MPLS LDP global, lalu pada setiap interface core aktifkan MPLS dan LDP. Jangan mengaktifkan MPLS pada interface pelanggan.

Contoh PE1:

```text
system-view
mpls lsr-id 10.255.0.5
mpls
mpls ldp
quit
interface GE0/5/0
 mpls
 mpls ldp
quit
```

Ulangi pada P1 dengan LSR ID `10.255.0.20` di GE0/5/0 dan GE0/5/1; P2 dengan LSR ID `10.255.0.23` di GE0/5/0 dan GE0/5/1; PE2 dengan LSR ID `10.255.0.22` di GE0/5/0.

Periksa adjacency dengan:

```text
display mpls ldp adjacency all
```

LDP adjacency harus terbentuk hanya pada PE1–P1, P1–P2, dan P2–PE2. LDP menyediakan label transport menuju LSR ID PE; label layanan VPNv4 dibawa oleh MP-BGP.

## 7. Buat VRF pelanggan pada kedua PE

### PE1 / Router_5

```text
system-view
ip vpn-instance CUST_A
 ipv4-family
 route-distinguisher 65000:5
 vpn-target 65000:100 import-extcommunity
 vpn-target 65000:100 export-extcommunity
quit
```

### PE2 / Router_22

```text
system-view
ip vpn-instance CUST_A
 ipv4-family
 route-distinguisher 65000:22
 vpn-target 65000:100 import-extcommunity
 vpn-target 65000:100 export-extcommunity
quit
```

RD membuat route VPN unik di MP-BGP walaupun dua pelanggan memakai prefix yang sama. RT menentukan VPN mana yang mengimpor/mengekspor route. Di lab ini kedua site memakai VRF yang sama dan RT yang sama supaya keduanya saling bertukar route.

## 8. Buat subinterface PE dan ikat ke VRF

Lakukan `ip binding vpn-instance` sebelum memasang alamat IP pada interface.

Subinterface memakai VLAN tag yang sama dengan service VLAN OLT. Konfigurasikan binding VRF sebelum alamat IP.

### PE1

```text
system-view
interface GE0/0/0.106
 vlan-type dot1q 106
 ip binding vpn-instance CUST_A
 ip address 192.168.106.1 255.255.255.0
quit
```

### PE2

```text
system-view
interface GE0/0/0.111
 vlan-type dot1q 111
 ip binding vpn-instance CUST_A
 ip address 192.168.111.1 255.255.255.0
quit
```

Route connected dari kedua interface ini menjadi route lokal VRF dan akan diiklankan sebagai VPNv4 jika MP-BGP VPNv4 aktif.

## 9. MP-BGP VPNv4 antara PE1 dan PE2

Gunakan iBGP AS 65000 dan Loopback0 sebagai sumber sesi. P router tidak perlu menjalankan BGP VPNv4.

### PE1 / Router_5

```text
system-view
bgp 65000
 router-id 10.255.0.5
 peer 10.255.0.22 as-number 65000
 peer 10.255.0.22 connect-interface LoopBack0
 ipv4-family vpnv4
 peer 10.255.0.22 enable
quit
save
```

### PE2 / Router_22

```text
system-view
bgp 65000
 router-id 10.255.0.22
 peer 10.255.0.5 as-number 65000
 peer 10.255.0.5 connect-interface LoopBack0
 ipv4-family vpnv4
 peer 10.255.0.5 enable
quit
save
```

Untuk sesi iBGP yang memakai Loopback, underlay harus memiliki route dua arah ke kedua Loopback. Jangan membuat sesi BGP ke alamat interface link /30 jika tujuan latihan adalah memahami peering PE loopback.

## 10. IP PC dan verifikasi route VPN

Isi properti PC:

- PC_6: IP `192.168.106.6`, mask `255.255.255.0`, gateway `192.168.106.1`.
- PC_11: IP `192.168.111.11`, mask `255.255.255.0`, gateway `192.168.111.1`.

Periksa urutannya:

1. `display ospf peer` pada PE/P: underlay OSPF neighbor aktif.
2. `display ip routing-table` pada PE: ada route underlay ke Loopback PE lain.
3. `display mpls ldp adjacency all`: LDP aktif di tiap link core.
4. `display bgp vpnv4 all peer` pada PE: peer VPNv4 berstatus Established.
5. `display bgp vpnv4 vpn-instance CUST_A routing-table` pada masing-masing PE: route site lokal dan route site lawan muncul.
6. `display current-configuration` pada PE: subinterface GE0/0/0.106 atau GE0/0/0.111 terikat ke `CUST_A`, bukan global table.

Route yang diharapkan di VPN RIB:

- PE1: `192.168.106.0/24` lokal dan `192.168.111.0/24` dari PE2.
- PE2: `192.168.111.0/24` lokal dan `192.168.106.0/24` dari PE1.

Pada perangkat Huawei nyata, uji forwarding pelanggan dengan ping antar-PC atau ping dengan VPN instance dari PE. Route control-plane saja belum membuktikan paket data berhasil lewat.

## 11. Alur paket dan label

Untuk PC_6 menuju PC_11:

1. PC_6 mengirim paket ke gateway 192.168.106.1.
2. ONT mengirim frame melalui GEM; OLT service-port memetakan layanan ke VLAN 106 pada uplink.
3. PE1 menerima frame pada subinterface GE0/0/0.106, melakukan routing di VRF `CUST_A`, lalu mencari route 192.168.111.0/24.
4. MP-BGP VPNv4 memberi PE1 route pelanggan dari PE2, beserta next-hop PE2 dan label VPN.
5. MPLS LDP memberi label transport menuju PE2 melalui P1/P2. Paket masuk core membawa label transport di luar dan label VPN di dalam.
6. P router hanya menukar label transport; PE2 menghapus label transport dan memakai label VPN untuk memilih VRF `CUST_A`.
7. PE2 meneruskan paket ke VLAN 111, OLT2 service-port/GEM, ONT_10, lalu PC_11. Paket balasan mengikuti route sebaliknya.

RD/RT dan label transport memiliki fungsi berbeda: RD membedakan route VPN, RT mengatur distribusi route antar-VRF, LDP mengantarkan paket ke PE, dan label VPN memilih tabel layanan pada PE tujuan.

## 12. OSPF atau IS-IS sebagai IGP

Jalankan satu IGP underlay pada jaringan produksi: OSPF **atau** IS-IS. Contoh utama di dokumen ini memakai OSPF. IS-IS juga dapat menggantikan OSPF dengan mengaktifkan proses IS-IS level 2 dan interface `isis enable 1` pada Loopback0 serta core link.

Catatan khusus simulator: status sesi BGP loopback saat ini memeriksa reachability melalui OSPF atau static route; pemeriksaan tersebut belum mengambil route dari IS-IS. Karena itu, gunakan OSPF untuk mengikuti langkah MP-BGP secara langsung di simulator. Pada jaringan nyata, IS-IS dapat membawa reachability Loopback PE tanpa mengaktifkan OSPF kedua.

## Batas simulasi saat ini

Simulator menyediakan konfigurasi VRF, route-target VPNv4, pertukaran route VPN, status peer BGP, dan adjacency LDP untuk latihan control-plane. Namun, simulator belum membentuk label stack MPLS aktual atau meneruskan paket data melalui label VPN/LDP secara penuh. Karena itu, pemeriksaan pada langkah 10 membuktikan control-plane route exchange; ping PC end-to-end melalui MPLS L3VPN belum dijamin berhasil oleh forwarding model saat ini.

Simpan topology setelah lab selesai agar konfigurasi perangkat dan kabel tersimpan bersama.
