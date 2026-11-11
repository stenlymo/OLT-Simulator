# HCIE Datacom Bab 6: VXLAN-based Virtualized Campus Deployment

Bab 6 pada lab guide mengandalkan iMaster NCE untuk onboarding dan orkestrasi. Simulator ini memodelkan inventaris fabric dan validasi hasil konfigurasi, sedangkan konfigurasi forwarding tetap memakai CLI router/switch yang tersedia (VXLAN, EVPN, DHCP, routing, NAT, WLAN).

## Inventaris fabric

Pada perangkat fabric, masuk ke system view dan tandai nama fabric serta role perangkat:

```text
system-view
campus fabric HQ role border as 100
```

Role yang didukung: `border`, `edge`, `extended`, `access`, `wac`, dan `egress`. Jalankan pada setiap perangkat yang menjadi anggota fabric, sesuaikan rolenya.

Daftarkan VN yang direncanakan pada perangkat fabric yang relevan:

```text
campus vn OA bd 100 vni 10100 vlan 100 gateway 172.17.10.254/24
campus vn RD bd 300 vni 10300 vlan 300 gateway 172.17.30.254/24
```

Gateway bersifat opsional untuk perangkat yang tidak menyediakan gateway VN. Buat VN yang sama pada perangkat fabric lainnya dengan nilai BD, VNI, dan VLAN yang konsisten.

## Validasi deployment

```text
display campus
display campus vn OA
display vxlan tunnel
display bgp evpn all routing-table
```

`display campus` membandingkan rencana VN di seluruh perangkat dengan bridge-domain/VNI, NVE, dan VBDIF yang benar-benar dikonfigurasi. Status `Incomplete` menyertakan sebabnya. `Ready` hanya berarti konfigurasi lokal tersebut cocok dengan rencana; konektivitas end-to-end tetap harus diverifikasi dengan tunnel, routing, dan ping.

Contoh konfigurasi VXLAN dasar yang divalidasi oleh inventaris VN:

```text
bridge-domain 100
 vxlan vni 10100
quit
interface Nve1
 source 3.3.3.1
 vni 10100 head-end peer-list 3.3.3.2 protocol bgp
quit
interface Vbdif100
 ip address 172.17.10.254 255.255.255.0
```

Pada perangkat egress seperti AR3, konfigurasi Bab 6 dapat memakai fitur yang sudah ada: VLAN/VLANIF dan DHCP untuk jaringan onboarding serta pengguna, static route dan NAT untuk akses jaringan eksternal. WAC/AP dan SSID menggunakan konfigurasi WLAN simulator.

## Batas simulasi

Simulator tidak menjalankan iMaster NCE, proses provisioning cloud, RADIUS/Portal eksternal, atau perangkat fisik. Perintah `campus` adalah metadata deployment khusus simulator, bukan perintah VRP pada perangkat Huawei. Autentikasi, DHCP, VXLAN/EVPN, dan WLAN dapat diuji melalui model layanan dan konfigurasi perangkat masing-masing.
