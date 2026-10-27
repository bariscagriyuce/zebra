# ZebraTool — Linux için Zebra Kurulum Aracı ve Etiket Tasarımcısı

Windows'a özel **Zebra Setup Utilities** ve **ZebraDesigner 3** yerine CachyOS/Arch üzerinde çalışan iki
uygulama. Python + PyQt6 ile yazıldı. Yazıcıyla doğrudan ZPL/SGD üzerinden konuşur, sürücü gerekmez.

## Kurulum

```bash
bash install.sh
```

Gerekli paketler (betik eksikse kendisi kurar): `python-pyqt6`, `zint`.
Kaldırmak için: `bash install.sh --uninstall`

Kurmadan denemek için: `sh bin/zebra-setup` ve `sh bin/zebra-designer`.

## Bağlantı türleri

| Tür | Adres | Okuma (durum/ayar) |
|-----|-------|--------------------|
| Ağ  | IP, port 9100 | Evet |
| USB | `/dev/usb/lp0` (kullanıcı `lp` grubunda olmalı) | Evet |
| CUPS | kuyruk adı (raw) | Hayır, yalnızca gönderim |

`socket://` adresli CUPS kuyrukları otomatik olarak doğrudan ağ yazıcısına çevrilir.

## Zebra Kurulum Aracı (`zebra-setup`)

- **Yazıcı bul:** CUPS kuyrukları, USB ve yerel ağda (port 9100) Zebra yazıcıları tarar.
- **Durum:** model, yazılım sürümü, seri no, çözünürlük, baskı uzunluğu sayacı; kafa açık / etiket bitti /
  ribon bitti / duraklatıldı uyarıları.
- **Ayarlar:** koyuluk, hız, baskı yöntemi (termal transfer / direkt termal), medya türü
  (boşluklu / siyah işaret / sürekli), baskı modu (yırtma / soyma / kesici), baskı genişliği, etiket
  uzunluğu, yırtma konumu, etiket üstü, sol kaydırma. Okur ve `^JUS` ile kalıcı kaydeder.
- **Eylemler:** kalibrasyon, yapılandırma/ağ etiketi, sensör profili, test etiketi, duraklat/devam,
  işleri iptal, yeniden başlat, fabrika ayarları.
- **Ağ:** kablolu / kablosuz IP ayarları (DHCP ya da sabit), Wi-Fi SSID ve parola.
- **Dosya gönder:** `.zpl`, `.prn`, font, grafik veya yazılım güncellemesi dosyasını olduğu gibi gönderir.
- **Yazıcı belleği:** E:, R: vb. sürücülerdeki dosyaları listeler, siler, kayıtlı formatı basar.
- **Terminal:** ham ZPL/SGD komutu gönderip yanıtı görür (hazır komut listesiyle).

## Zebra Etiket Tasarımcısı (`zebra-designer`)

- Gerçek ölçülü (mm ve nokta) tuval, ızgara ve ızgaraya yaslama, yakınlaştırma.
- Öğeler: **metin**, **barkod** (Code 128, Code 39, Code 93, EAN-13, EAN-8, UPC-A, Interleaved 2of5,
  QR, Data Matrix, PDF417), **kutu**, **çizgi** (yatay/dikey/çapraz), **elips**, **resim** (titreme ile
  siyah-beyaza çevrilir).
- Metinde yazıcının kendi fontu (Zebra 0) ya da herhangi bir sistem fontu; sistem fontları grafik
  olarak gönderilir, böylece Türkçe karakterler dahil ekranda ne görünüyorsa o basılır.
- Döndürme (0/90/180/270), paragraf (otomatik satır kaydırma, hizalama), ters baskı.
- **Değişkenli baskı:** metin/barkod içine `{AD}`, `{FIYAT}` gibi alanlar; yazdırırken elle, CSV
  dosyasından ya da sayaçla (`{SAYAC}`) doldurulur. Hazır alanlar: `{TARIH}`, `{SAAT}`, `{DATE}`, `{TIME}`.
- Geri al/yinele, kopyala/yapıştır, hizalama, öne/arkaya.
- Kaydetme biçimi `.zlbl` (JSON). ZPL ve PNG olarak dışa aktarma; F5 ile ZPL kodunu görme.
- Etiket ayarlarında “Yazıcıdan al” ile etiket boyutu yazıcıdan okunur.

### Kısayollar
`T` metin · `B` barkod · `Q` QR · `R` kutu · `L` çizgi · `E` elips · `I` resim ·
ok tuşları 1 nokta, `Shift`+ok 10 nokta · `Ctrl`+tekerlek yakınlaştır · `Ctrl+0` sığdır ·
`F5` ZPL · `Ctrl+P` yazdır.

## Bilinen sınırlar

- Ekrandaki “Zebra 0” fontu yazıcı fontunun yaklaşık bir benzeridir; genişlikler birkaç nokta
  farklı çıkabilir. Birebir sonuç gerekiyorsa sistem fontu seçin.
- QR kod ZPL'de döndürülemez (yazıcı kısıtı).
- ZebraDesigner `.nlbl` dosyaları açılamaz (kapalı biçim). ZPL içe aktarma yoktur.
- Windows sürücüsüne özgü özellikler (RFID kodlama, veritabanı bağlantısı) yoktur.
