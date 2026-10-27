"""ZPL üretim yardımcıları."""

from PyQt6.QtGui import QImage


def field_data(text: str) -> str:
    """^FD içeriği. ^ ~ \\ karakterleri ^FH ile onaltılık kaçışla yazılır.

    Dönüş: (önek, veri) -- önek ya "" ya da "^FH\\\\" olur.
    """
    if not any(c in text for c in "^~\\"):
        return "", text
    out = []
    for c in text:
        if c in "^~\\":
            out.append("\\%02X" % ord(c))
        else:
            out.append(c)
    return "^FH\\", "".join(out)


def fd(text: str) -> str:
    prefix, data = field_data(text)
    return f"{prefix}^FD{data}^FS"


def qimage_to_gf(img: QImage, threshold=128):
    """QImage'i ^GFA komutuna çevir. Dönüş: (zpl_komutu, genişlik, yükseklik).

    Siyah (koyu) pikseller basılır; saydam pikseller beyaz sayılır.
    """
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    bpr = (w + 7) // 8
    rows = []
    prev = None
    for y in range(h):
        row = bytearray(bpr)
        for x in range(w):
            px = img.pixel(x, y)
            a = (px >> 24) & 0xFF
            r, g, b = (px >> 16) & 0xFF, (px >> 8) & 0xFF, px & 0xFF
            lum = (r * 299 + g * 587 + b * 114) // 1000
            # saydamlığı beyaz üzerine karıştır
            lum = (lum * a + 255 * (255 - a)) // 255
            if lum < threshold:
                row[x >> 3] |= 0x80 >> (x & 7)
        hexrow = row.hex().upper()
        # ZPL satır sıkıştırması: aynı satır tekrarında ':' kullan
        if hexrow == prev:
            rows.append(":")
        else:
            rows.append(_compress_row(hexrow))
            prev = hexrow
    total = bpr * h
    return f"^GFA,{total},{total},{bpr},{''.join(rows)}", w, h


_COUNT_CHARS = "GHIJKLMNOPQRSTUVWXY"   # 1..19
_COUNT_TENS = "ghijklmnopqrstuvwxyz"   # 20..400


def _repeat_code(n):
    """1 <= n <= 419"""
    s = ""
    if n >= 20:
        s += _COUNT_TENS[n // 20 - 1]
        n %= 20
    if n:
        s += _COUNT_CHARS[n - 1]
    return s


def _compress_row(hexrow):
    """ZPL ^GF ASCII sıkıştırması (tekrar sayaçları, ',' = satır sonu 0, '!' = satır sonu F)."""
    stripped = hexrow.rstrip("0")
    if not stripped:
        return ","
    tail = ""
    if len(stripped) < len(hexrow):
        tail = ","
    else:
        s2 = hexrow.rstrip("F")
        if not s2:
            return "!"
        if len(s2) < len(hexrow) - 1:
            stripped, tail = s2, "!"
    out = []
    i = 0
    while i < len(stripped):
        c = stripped[i]
        j = i
        while j < len(stripped) and stripped[j] == c:
            j += 1
        n = j - i
        while n > 0:
            m = min(n, 419)
            out.append((_repeat_code(m) + c) if m > 1 else c)
            n -= m
        i = j
    return "".join(out) + tail
