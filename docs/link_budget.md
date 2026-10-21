# GPON Link Budget Simulator

Dokumen ini menjelaskan angka yang ditampilkan pada **ONT Properties → Link
Budget Simulation (End-to-End)**. Contoh memakai dua topologi yang dikirim pada
screenshot.

## Model dan rumus simulator

Simulator menghitung daya dua arah secara terpisah:

```text
Fiber loss       = total panjang fiber (km) × 0.35 dB/km
Splitter loss    = jumlah loss semua splitter pada jalur
Connector loss   = jumlah segmen kabel × 0.35 dB per sambungan
Path loss        = fiber loss + splitter loss + connector loss

ONT Rx (1490 nm) = OLT Tx − path loss − safety margin 2.00 dB
OLT Rx (1310 nm) = ONT Tx − path loss
```

Nilai daya awal simulator yang digunakan pada contoh:

- OLT Tx untuk Class C+: **+5.00 dBm**.
- ONT Tx GPON Class B+: **+2.26 dBm**, mengikuti contoh pembacaan `opticalinfo.txt.txt`.
- Atenuasi fiber: **0.35 dB/km**.
- Loss konektor: **0.35 dB untuk setiap segmen kabel**.
- Safety margin: **2.00 dB**, diterapkan pada estimasi daya downstream ONT Rx.
- Splitter: 1:4 = **7.25 dB**, 1:8 = **10.50 dB**, 1:32 = **17.50 dB**.

Loss konektor adalah nilai simulasi per sambungan kabel (pasangan konektor
terpasang). Nilai tersebut tidak bergantung pada ada atau tidaknya perangkat
OTB/roset. Dengan demikian, topologi sederhana yang memiliki dua segmen kabel
menghasilkan `2 × 0.35 = 0.70 dB`. Topologi yang memiliki lima segmen kabel
menghasilkan `5 × 0.35 = 1.75 dB`.

## Contoh 1 — OLT → Splitter 1:32 → ONT

Jalur pada gambar memiliki dua segmen kabel dan panjang total 6.000 km.

| Komponen | Perhitungan | Loss |
| --- | --- | ---: |
| Fiber | 6.000 km × 0.35 dB/km | 2.10 dB |
| Splitter 1:32 | Nilai model splitter | 17.50 dB |
| Konektor | 2 segmen × 0.35 dB | 0.70 dB |
| **Path loss** | 2.10 + 17.50 + 0.70 | **20.30 dB** |
| Safety margin downstream | Cadangan desain | 2.00 dB |

```text
ONT Rx  = +5.00 − 20.30 − 2.00 = −17.30 dBm
OLT Rx  = +2.26 − 20.30       = −18.04 dBm
```

Angka ini cocok dengan screenshot pertama. `0.70 dB` berasal dari kabel
OLT–splitter dan splitter–ONT, masing-masing dimodelkan memiliki loss konektor
0.35 dB.

## Contoh 2 — OLT → OTB → Splitter 1:4 → Splitter 1:8 → Roset → ONT

Jalur pada gambar memiliki lima segmen kabel dan panjang total 5.270 km.

| Komponen | Perhitungan | Loss |
| --- | --- | ---: |
| Fiber | 5.270 km × 0.35 dB/km = 1.8445 dB | 1.84 dB |
| Splitter 1:4 | Nilai model splitter | 7.25 dB |
| Splitter 1:8 | Nilai model splitter | 10.50 dB |
| **Total splitter** | 7.25 + 10.50 | **17.75 dB** |
| Konektor | 5 segmen × 0.35 dB | 1.75 dB |
| **Path loss** | 1.8445 + 17.75 + 1.75 | **21.3445 dB** |
| Safety margin downstream | Cadangan desain | 2.00 dB |

```text
ONT Rx  = +5.00 − 21.3445 − 2.00 = −18.3445 ≈ −18.34 dBm
OLT Rx  = +2.26 − 21.3445       = −19.0845 ≈ −19.08 dBm
```

Angka ini cocok dengan screenshot kedua. Rugi konektornya lebih besar karena
jalur melewati lima segmen kabel: OLT–OTB, OTB–splitter 1:4, splitter 1:4–
splitter 1:8, splitter 1:8–roset, dan roset–ONT.

## Batas penerimaan dan interpretasi

Spesifikasi yang diberikan untuk ONT Class B+ menyebut sensitivitas Rx **−27
dBm** dan overload **−8 dBm**. Kedua contoh berada di rentang itu:

| Jalur | ONT Rx | Margin di atas sensitivitas −27 dBm | Jarak ke overload −8 dBm |
| --- | ---: | ---: | ---: |
| Contoh 1 | −17.30 dBm | 9.70 dB | 9.30 dB |
| Contoh 2 | −18.34 dBm | 8.66 dB | 10.34 dB |

Dokumen Huawei optical-info memberi contoh rentang alarm OLT Rx Class B+ **−29
sampai −7 dBm**. Dengan rentang referensi tersebut, nilai OLT Rx contoh 1
adalah −18.04 dBm dan contoh 2 −19.08 dBm, keduanya masih di dalam rentang.

Nilai ini adalah simulasi link budget, bukan pengukuran DDM perangkat nyata.
Loss konektor 0.35 dB per segmen, daya Tx, dan safety margin merupakan asumsi
model; hasil instalasi sebenarnya dipengaruhi jumlah konektor aktual, kualitas
sambungan, panjang gelombang, jenis fiber, splitter, dan nilai Tx/Rx perangkat.
