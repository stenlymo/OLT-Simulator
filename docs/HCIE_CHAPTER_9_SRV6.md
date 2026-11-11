# HCIE Datacom Bab 9: SRv6

Bab ini menambahkan model control-plane SRv6 ke simulator: locator, End/End.DT4/End.DT6 local SID, SRv6 Best-Effort sebagai transport VPNv4, dan SRv6-TE policy dengan candidate path. Simulasi status didasarkan pada konfigurasi interface IPv6, adjacency IS-IS IPv6, route underlay, locator, dan segment list.

> Catatan: ini model control-plane untuk latihan CLI. Simulator tidak membuat paket SRH atau meneruskan paket IPv6 secara byte-level. Fungsi SID layanan juga dibentuk deterministik dari locator untuk kebutuhan pembelajaran.

## Membuat locator

Aktifkan SRv6, buat locator, lalu pasang locator pada proses IS-IS. Ulangi konfigurasi dengan prefix unik pada setiap PE/P:

```text
system-view
segment-routing ipv6
locator LOC1
 ipv6-prefix 2001:db8:1:: 64
quit
isis 1
 ipv6 enable topology ipv6
 segment-routing ipv6 locator LOC1
quit
```

Pastikan tiap link core memiliki alamat IPv6 dan IS-IS IPv6 aktif di kedua ujungnya. Locator harus tidak tumpang tindih. Simulator membentuk local SID berikut dari alamat network locator: function `0x100` untuk End, `0x200` untuk End.DT4, dan `0x300` untuk End.DT6.

## L3VPNv4 over SRv6 Best-Effort

Pada tiap PE, ikat locator ke VPN instance dan aktifkan distribusi route VPNv4 lewat BGP seperti konfigurasi L3VPN yang sudah ada:

```text
ip vpn-instance CUST_A
 ipv4-family
  route-distinguisher 65000:10
  vpn-target 65000:10 both
  segment-routing ipv6 locator LOC1
  segment-routing ipv6 best-effort
  srv6 service-type vpnv4
quit
```

Lengkapi BGP VPNv4 peer/route-target, antarmuka CE dalam VPN instance, serta reachability IPv6 antar-PE melalui IS-IS. `srv6 service-type vpnv4` memilih perilaku service SID End.DT4 pada model simulator.

## SRv6-TE policy untuk VPN

Buat daftar SID dan policy pada router yang mengirim traffic. SID segment harus berada di dalam locator SRv6 yang sudah dikonfigurasi pada salah satu perangkat:

```text
segment-list SL_PE2
 index 10 sid ipv6 2001:db8:2::100
quit
srv6-te policy POLICY_PE2 endpoint 2001:db8:2::1 color 100
 candidate-path preference 100
  segment-list SL_PE2
quit
```

Ikat policy pada VPN instance:

```text
ip vpn-instance CUST_A
 ipv4-family
  segment-routing ipv6 policy POLICY_PE2
quit
```

Jika candidate path policy sedang Up, VPN transport menggunakan policy tersebut. Jika policy belum Up, route VPN yang memerlukan policy itu tidak dipertukarkan sebagai transport aktif.

## Pemeriksaan

```text
display segment-routing ipv6 locator
display segment-routing ipv6 local-sid
display segment-routing ipv6 forwarding
display srv6-te policy
display srv6-te policy POLICY_PE2
display bgp vpnv4 vpn-instance CUST_A routing-table
```

Locator/SID tampil `Up` saat SRv6 diaktifkan. Policy menjadi `Up` jika endpoint memiliki route underlay IPv6 dan seluruh SID candidate valid serta tercakup locator. Bila `Down`, cek IPv6 IS-IS adjacency, route ke endpoint, segment-list, dan konsistensi locator di PE tujuan.
