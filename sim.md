# Lab: PC_6 ke PC_11 melalui OLT, OSPF, IS-IS, dan MPLS

Contoh ini mengikuti topologi pada gambar: Router_5 dan Router_22 adalah router tepi pelanggan. Router_20, Router_23, dan Router_21 membentuk backbone dengan link silang/redundan. OLT memakai VLAN berbeda di tiap lokasi.

## 1. Port dan kabel

Pilih port berikut saat membuat kabel. Jika kabel pada topology yang ada memakai port lain, arahkan konfigurasi interface ke nama port pada tooltip kabel.

| Perangkat A | Port A | Perangkat B | Port B |
|---|---|---|---|
| Router_5 | GE0/5/0 | Router_20 | GE0/5/0 |
| Router_5 | GE0/5/1 | Router_21 | GE0/5/2 |
| Router_20 | GE0/5/1 | Router_23 | GE0/5/0 |
| Router_20 | GE0/5/2 | Router_22 | GE0/5/1 |
| Router_23 | GE0/5/1 | Router_21 | GE0/5/0 |
| Router_21 | GE0/5/1 | Router_22 | GE0/5/0 |
| Router_5 | GE0/0/0 | Switch_19 | GE1/0/1 |
| Router_22 | GE0/0/0 | Switch_7 | GE1/0/1 |
| Switch_19 | GE1/0/2 | Huawei_3 (OLT 1) | 0/2/0 |
| Switch_7 | GE1/0/2 | Huawei_8 (OLT 2) | 0/2/0 |
| Huawei_3 | GPON 0/1/0 | Splitter_2 | IN-1 |
| Splitter_2 | OUT-1 | ONT_1 | GPON |
| ONT_1 | ETH-1 | PC_6 | Ethernet0 |
| Huawei_8 | GPON 0/1/0 | Splitter_9 | IN-1 |
| Splitter_9 | OUT-1 | ONT_10 | GPON |
| ONT_10 | ETH-1 | PC_11 | Ethernet0 |

Pada OLT, kabel GPON `0/1/0` dikonfigurasi melalui `interface gpon 0/1`, port `0`.

Link backbone membentuk ring Router_20–Router_5–Router_21–Router_22–Router_20, dengan jalur tambahan Router_20–Router_23–Router_21. Karena ada beberapa jalur, OSPF/IS-IS dapat memilih route alternatif jika metriknya mendukung.

## 2. Skema IP

| Router | Interface | IP/mask |
|---|---|---|
| Router_5 | LoopBack0 | 10.255.0.5/32 |
| Router_5 | GE0/5/0 | 10.0.0.1/30 |
| Router_5 | GE0/5/1 | 10.0.0.18/30 |
| Router_5 | GE0/0/0 | 192.168.106.1/24 |
| Router_20 | LoopBack0 | 10.255.0.20/32 |
| Router_20 | GE0/5/0 | 10.0.0.2/30 |
| Router_20 | GE0/5/1 | 10.0.0.5/30 |
| Router_20 | GE0/5/2 | 10.0.0.21/30 |
| Router_23 | LoopBack0 | 10.255.0.23/32 |
| Router_23 | GE0/5/0 | 10.0.0.6/30 |
| Router_23 | GE0/5/1 | 10.0.0.9/30 |
| Router_21 | LoopBack0 | 10.255.0.21/32 |
| Router_21 | GE0/5/0 | 10.0.0.10/30 |
| Router_21 | GE0/5/1 | 10.0.0.13/30 |
| Router_21 | GE0/5/2 | 10.0.0.17/30 |
| Router_22 | LoopBack0 | 10.255.0.22/32 |
| Router_22 | GE0/5/0 | 10.0.0.14/30 |
| Router_22 | GE0/5/1 | 10.0.0.22/30 |
| Router_22 | GE0/0/0 | 192.168.111.1/24 |
| PC_6 | Ethernet0 | 192.168.106.6/24, gateway 192.168.106.1 |
| PC_11 | Ethernet0 | 192.168.111.11/24, gateway 192.168.111.1 |

VLAN 106 hanya dipakai pada sisi OLT 1; VLAN 111 hanya pada sisi OLT 2. Gateway/router interface dan subnet PC juga berbeda, sehingga paket antar-PC perlu dirutekan.

## 3. Switch akses

### Switch_19

GE1/0/1 menuju Router_5 dibuat access VLAN 106. GE1/0/2 menuju OLT dibuat trunk VLAN 106.

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
save
```

### Switch_7

```text
system-view
vlan batch 111
interface GE1/0/1
 port link-type access
 port default vlan 111
quit
interface GE1/0/2
 port link-type trunk
 port trunk allow-pass vlan 111
quit
save
```

## 4. OLT 1: Huawei_3 menuju PC_6

Jika ONT_1 sudah terdaftar, gunakan `display ont info 0 all` untuk melihat ID dan lewati `ont add`. Jika belum, aktifkan auto-find dan ambil SN dengan `display ont autofind 0`; ganti `<SN_ONT_1>` di bawah dengan SN yang tampil.

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
 ont add 0 1 sn-auth <SN_ONT_1> omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc PC6
quit
traffic table ip index 10 cir 4096 priority user-cos 4 priority-policy local-Setting
service-port vlan 106 gpon 0/1/0 ont 1 gemport 10 inbound traffic-table index 10 outbound traffic-table index 10
save
```

Jika ID ONT bukan `1`, ubah parameter `ont 1` di service-port. Uplink OLT ke Switch_19 harus memakai `0/2/0`.

## 5. OLT 2: Huawei_8 menuju PC_11

Gunakan VLAN 111. Ganti `<SN_ONT_10>` dengan SN hasil auto-find, atau lewati `ont add` bila ONT sudah terdaftar.

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
 ont add 0 1 sn-auth <SN_ONT_10> omci ont-lineprofile-id 10 ont-srvprofile-id 10 desc PC11
quit
traffic table ip index 10 cir 4096 priority user-cos 4 priority-policy local-Setting
service-port vlan 111 gpon 0/1/0 ont 1 gemport 10 inbound traffic-table index 10 outbound traffic-table index 10
save
```

Jika ID ONT bukan `1`, ganti ID pada service-port. Uplink OLT ke Switch_7 harus memakai `0/2/0`.

## 6. Router backbone: OSPF, IS-IS, dan MPLS LDP

Di setiap router aktifkan OSPF area 0 untuk route IPv4 pelanggan, IS-IS level 2 pada link router-ke-router, dan MPLS LDP pada semua link router-ke-router. Port tambahan pada gambar adalah Router_5–Router_21 dan Router_20–Router_22; keduanya mendapat subnet /30 unik. Interface yang menghadap switch/OLT tidak dimasukkan ke IS-IS atau MPLS.

### Router_5

```text
system-view
isis 1
 is-level level-2
 cost-style wide
 network-entity 49.0001.0000.0000.0005.00
quit
ospf 1 router-id 10.255.0.5
quit
mpls lsr-id 10.255.0.5
mpls
mpls ldp
quit
interface LoopBack0
 ip address 10.255.0.5 255.255.255.255
 isis enable 1
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.1 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/5/1
 ip address 10.0.0.18 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/0/0
 ip address 192.168.106.1 255.255.255.0
 ospf enable 1 area 0
quit
save
```

### Router_20

```text
system-view
isis 1
 is-level level-2
 cost-style wide
 network-entity 49.0001.0000.0000.0020.00
quit
ospf 1 router-id 10.255.0.20
quit
mpls lsr-id 10.255.0.20
mpls
mpls ldp
quit
interface LoopBack0
 ip address 10.255.0.20 255.255.255.255
 isis enable 1
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.2 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/5/1
 ip address 10.0.0.5 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/5/2
 ip address 10.0.0.21 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
save
```

### Router_23

```text
system-view
isis 1
 is-level level-2
 cost-style wide
 network-entity 49.0001.0000.0000.0023.00
quit
ospf 1 router-id 10.255.0.23
quit
mpls lsr-id 10.255.0.23
mpls
mpls ldp
quit
interface LoopBack0
 ip address 10.255.0.23 255.255.255.255
 isis enable 1
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.6 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/5/1
 ip address 10.0.0.9 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
save
```

### Router_21

```text
system-view
isis 1
 is-level level-2
 cost-style wide
 network-entity 49.0001.0000.0000.0021.00
quit
ospf 1 router-id 10.255.0.21
quit
mpls lsr-id 10.255.0.21
mpls
mpls ldp
quit
interface LoopBack0
 ip address 10.255.0.21 255.255.255.255
 isis enable 1
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.10 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/5/1
 ip address 10.0.0.13 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/5/2
 ip address 10.0.0.17 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
save
```

### Router_22

```text
system-view
isis 1
 is-level level-2
 cost-style wide
 network-entity 49.0001.0000.0000.0022.00
quit
ospf 1 router-id 10.255.0.22
quit
mpls lsr-id 10.255.0.22
mpls
mpls ldp
quit
interface LoopBack0
 ip address 10.255.0.22 255.255.255.255
 isis enable 1
 ospf enable 1 area 0
quit
interface GE0/5/0
 ip address 10.0.0.14 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/5/1
 ip address 10.0.0.22 255.255.255.252
 isis enable 1
 ospf enable 1 area 0
 mpls
 mpls ldp
quit
interface GE0/0/0
 ip address 192.168.111.1 255.255.255.0
 ospf enable 1 area 0
quit
save
```

## 7. Alamat PC dan uji ping

Buka properti PC, isi alamat statis, lalu simpan:

- **PC_6:** IP `192.168.106.6`, mask `255.255.255.0`, gateway `192.168.106.1`.
- **PC_11:** IP `192.168.111.11`, mask `255.255.255.0`, gateway `192.168.111.1`.

Dari pengaturan PC_6, ping `192.168.111.11`. Dari PC_11, ping `192.168.106.6` untuk memastikan jalur pergi dan balik aktif.

Sebelum ping, periksa pada router:

```text
display ospf peer
display isis peer
display mpls ldp adjacency all
display ip routing-table
```

OSPF, IS-IS, dan MPLS LDP seharusnya membentuk adjacency pada tiap link router-ke-router. Jumlah peer langsung yang diharapkan: Router_5 = 2, Router_20 = 3, Router_23 = 2, Router_21 = 3, Router_22 = 2. Router_5 harus memiliki route OSPF ke `192.168.111.0/24`, dan Router_22 harus memiliki route ke `192.168.106.0/24`. Setiap pasangan link harus memakai subnet /30 yang berbeda seperti pada tabel alamat.

## Catatan model simulator

Pada contoh ini, **OSPF yang menyediakan routing IP untuk ping PC ke PC**. IS-IS dan MPLS LDP diaktifkan pada backbone untuk latihan protokol; ping PC simulator belum memodelkan forwarding paket berlabel MPLS secara penuh. Jadi konfigurasi ini bukan MPLS L3VPN yang mengisolasi tabel routing pelanggan.

Jika ping timeout, cek urutan ini: IP/mask/gateway PC; status ONT dan service-port; VLAN OLT (106 atau 111); VLAN access/trunk pada switch; port dan IP router sesuai tooltip kabel; lalu adjacency dan route OSPF di kedua router tepi. Terakhir gunakan **Save Topology** agar perangkat, kabel, dan konfigurasi tersimpan bersama.
