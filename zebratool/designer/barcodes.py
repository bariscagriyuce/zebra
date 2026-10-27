"""Barkod önizlemesi (zint ile modül matrisi) ve ZPL komutları."""

import functools
import shutil
import subprocess

from PyQt6.QtGui import QImage

ZINT = shutil.which("zint")

# anahtar: (görünen ad, zint türü, 2B mi)
SYMBOLOGIES = {
    "code128": ("Code 128", "CODE128", False),
    "code39": ("Code 39", "CODE39", False),
    "code93": ("Code 93", "CODE93", False),
    "ean13": ("EAN-13", "EANX", False),
    "ean8": ("EAN-8", "EANX", False),
    "upca": ("UPC-A", "UPCA", False),
    "i2of5": ("Interleaved 2 of 5", "C25INTER", False),
    "qr": ("QR Code", "QRCODE", True),
    "datamatrix": ("Data Matrix", "DATAMATRIX", True),
    "pdf417": ("PDF417", "PDF417", True),
}

QR_ECC = [("L (%7)", "L"), ("M (%15)", "M"), ("Q (%25)", "Q"), ("H (%30)", "H")]
_QR_ECC_ZINT = {"L": "1", "M": "2", "Q": "3", "H": "4"}


class BarcodeError(Exception):
    pass


@functools.lru_cache(maxsize=256)
def matrix(sym: str, data: str, ecc: str = "M", columns: int = 0):
    """Barkodu modül matrisine çevir: satır listesi (her satır bool tuple).

    Doğrusal barkodlarda tek satır döner.
    """
    if not ZINT:
        raise BarcodeError("zint bulunamadı (sudo pacman -S zint)")
    if not data:
        raise BarcodeError("Veri boş")
    _, ztype, is2d = SYMBOLOGIES[sym]
    args = [ZINT, "-b", ztype, "-d", data, "--notext", "--scale=0.5",
            "--direct", "--filetype=png", "--whitesp=0", "--vwhitesp=0"]
    if not is2d:
        args.append("--height=1")
    if sym == "qr":
        args += ["--secure=" + _QR_ECC_ZINT.get(ecc, "2")]
    if sym == "pdf417" and columns:
        args += [f"--cols={columns}"]
    try:
        r = subprocess.run(args, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        raise BarcodeError(str(e)) from e
    if r.returncode not in (0, 2):  # 2 = uyarı
        raise BarcodeError(r.stderr.decode("utf-8", "replace").strip() or "zint hatası")
    img = QImage.fromData(r.stdout)
    if img.isNull():
        raise BarcodeError("zint çıktısı okunamadı")
    img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    rows = []
    for y in range(img.height()):
        rows.append(tuple(img.pixelColor(x, y).value() < 128 for x in range(img.width())))
    if not is2d:
        rows = [rows[0]]
    elif sym == "pdf417":
        # zint satırları birkaç piksel yüksekliğinde çizer; ardışık aynı satırları birleştir
        dedup = []
        for row in rows:
            if not dedup or dedup[-1] != row:
                dedup.append(row)
        rows = dedup
    # kenardaki boşlukları kırp
    cols = [any(r[x] for r in rows) for x in range(len(rows[0]))]
    if True in cols:
        x0 = cols.index(True)
        x1 = len(cols) - cols[::-1].index(True)
        rows = [r[x0:x1] for r in rows]
    if is2d:
        while rows and not any(rows[0]):
            rows.pop(0)
        while rows and not any(rows[-1]):
            rows.pop()
    return tuple(rows)


def human_readable(sym, data):
    """Önizlemede barkod altında gösterilecek metin."""
    if sym in ("ean13", "ean8", "upca"):
        return data
    if sym == "code39":
        return f"*{data}*"
    return data


def zpl(sym, o, p, data_field):
    """Barkod ZPL komutu. o: N/R/I/B yönü, p: özellik sözlüğü, data_field: hazır ^FD...^FS."""
    m = int(p.get("module", 2))
    h = int(p.get("height", 80))
    hr = "Y" if p.get("hr", True) else "N"
    ratio = float(p.get("ratio", 3.0))
    by = f"^BY{m},{ratio:.1f},{h}"
    if sym == "code128":
        return f"{by}^BC{o},{h},{hr},N,N,A{data_field}"
    if sym == "code39":
        return f"{by}^B3{o},N,{h},{hr},N{data_field}"
    if sym == "code93":
        return f"{by}^BA{o},{h},{hr},N,N{data_field}"
    if sym == "ean13":
        return f"{by}^BE{o},{h},{hr},N{data_field}"
    if sym == "ean8":
        return f"{by}^B8{o},{h},{hr},N{data_field}"
    if sym == "upca":
        return f"{by}^BU{o},{h},{hr},N,Y{data_field}"
    if sym == "i2of5":
        return f"{by}^B2{o},{h},{hr},N,N{data_field}"
    if sym == "qr":
        # ^BQ yalnızca normal yönü destekler; veri önüne hata düzeltme + 'A' (otomatik mod) eklenir
        ecc = p.get("ecc", "M")
        return f"^BQN,2,{m}" + data_field.replace("^FD", f"^FD{ecc}A,", 1)
    if sym == "datamatrix":
        return f"^BX{o},{m},200{data_field}"
    if sym == "pdf417":
        cols = int(p.get("columns", 0)) or ""
        return f"^BY{m}^B7{o},{max(1, h)},0,{cols},,N{data_field}"
    raise ValueError(sym)
