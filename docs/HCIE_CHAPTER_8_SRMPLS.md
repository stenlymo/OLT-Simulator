# HCIE Datacom Bab 8: SR-MPLS

Bab 8 terdiri dari L3VPNv4 over SR-MPLS Best Effort, SR-MPLS Traffic Engineering, dan SR-TE Policy. Simulator menggunakan konfigurasi IPv4, IS-IS, MPLS, dan BGP VPNv4 yang sudah ada, lalu memodelkan prefix-SID, adjacency-SID, SR LSP, TE tunnel, dan candidate path.

## SR-MPLS Best Effort

Pada setiap router core, pastikan alamat interface dan LoopBack0 sudah ada, kemudian konfigurasi IS-IS Level-2. Contoh inti pada setiap router:

```text
system-view
isis 1
 is-level level-2
 cost-style wide
 network-entity 49.0001.0001.0001.0001.00
 segment-routing mpls
 segment-routing global-block 16000 17000
quit
mpls lsr-id 10.0.0.1
mpls
segment-routing
quit
interface LoopBack0
 isis enable 1
 isis prefix-sid index 1
quit
```

Sesuaikan NET, LSR ID, dan prefix-SID index setiap perangkat. Untuk SR-MPLS BE, IS-IS harus memiliki route ke LoopBack0 tujuan pada setiap PE. L3VPNv4 dapat memakai MP-BGP VPNv4 seperti lab sebelumnya; transport VPN menerima SR-MPLS apabila SR aktif dan prefix-SID sudah dipasang.

Pemeriksaan:

```text
display tunnel-info all
display segment-routing prefix mpls forwarding
ping lsp segment-routing ip 10.0.0.4 32 version draft2
```

## SR-MPLS TE

Explicit path diatur dengan label prefix/adjacency. Contoh membuat path lalu menerapkannya ke tunnel:

```text
explicit-path PE1_PE4_Manual
 next sid label 16005 type prefix
 next sid label 142337 type adjacency
 next sid label 142338 type adjacency
quit
mpls
mpls te
quit
interface Tunnel10
 ip address unnumbered interface LoopBack0
 tunnel-protocol mpls te
 destination 10.0.0.4
 mpls te signal-protocol segment-routing
 mpls te tunnel-id 10
 mpls te path explicit-path PE1_PE4_Manual
quit
```

Tunnel berstatus UP bila tujuan dapat dicapai melalui IS-IS dan semua SID pada explicit path masih aktif. Adjacency-SID dapat dikonfigurasi di mode `segment-routing` menggunakan `ipv4 adjacency local-ip-addr <alamat-lokal> remote-ip-addr <alamat-peer> sid <label>`.

## SR-TE Policy

Buat segment list dan candidate path di mode `segment-routing`:

```text
segment-routing
 segment-list name PE1_PE4_1
  index 10 mpls label 16005
  index 20 mpls label 142338
  index 30 mpls label 16004
 quit
 sr-te policy p1 endpoint 10.0.0.4 color 100
  candidate-path preference 100
   segment-list PE1_PE4_1
  quit
 quit
quit
display sr-te policy-name p1
```

Tambahkan candidate path lain dengan preference berbeda untuk membuat jalur cadangan. Kandidat dengan preference tertinggi yang seluruh SID-nya aktif menjadi jalur utama. Jika adjacency SID pada jalur utama tidak lagi memiliki IS-IS adjacency aktif, simulator memilih kandidat aktif berikutnya. Gunakan `display tunnel-info all` dan `display sr-te policy-name <nama>` untuk memeriksa status.

Untuk memilih SR-TE sebagai transport L3VPN, buat tunnel selection policy lalu ikat ke VPN instance. Contoh:

```text
tunnel-policy p1
 tunnel select-seq sr-te load-balance-number 1 unmix
quit
ip vpn-instance vpna
 ipv4-family
  tnl-policy p1
  default-color 100
quit
```

Kebijakan ini membawa rute VPNv4 melalui SR-MPLS jika ada SR-TE policy aktif yang warnanya sama dengan `default-color`.

## Batas simulasi

Simulator memodelkan status control-plane dan pemilihan path. Ia tidak membentuk MPLS label stack dalam paket nyata atau memancarkan paket BFD/SR-TE. Untuk VPNv4, BGP, IS-IS, dan alamat CE tetap perlu dikonfigurasi seperti pada lab guide. Fitur ini mengikuti lab 8.1–8.3 pada [HCIE-Datacom V1.0 Lab Guide (VRP).pdf](../labguide/HCIE-Datacom%20V1.0%20Lab%20Guide%20(VRP).pdf).
