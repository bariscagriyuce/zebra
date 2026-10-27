"""Kayıtlı yazıcılar, yazıcı keşfi ve Zebra SGD/ZPL sorgu yardımcıları."""

import concurrent.futures
import glob
import ipaddress
import json
import os
import re
import socket
import subprocess
from urllib.parse import urlparse

from .connection import PrinterConfig, make_connection, NetworkConnection, UsbConnection

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "zebratool")
PRINTERS_FILE = os.path.join(CONFIG_DIR, "printers.json")


# --- Kayıtlı yazıcılar -----------------------------------------------------

def load_printers():
    try:
        with open(PRINTERS_FILE, encoding="utf-8") as f:
            return [PrinterConfig.from_dict(d) for d in json.load(f)]
    except (OSError, ValueError):
        return []


def save_printers(printers):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(PRINTERS_FILE, "w", encoding="utf-8") as f:
        json.dump([p.to_dict() for p in printers], f, indent=2, ensure_ascii=False)


# --- SGD / ZPL yardımcıları --------------------------------------------------

def _quoted_done(buf):
    return buf.count(b'"') >= 2


def getvar(conn, name, timeout=2.0):
    raw = conn.query(f'! U1 getvar "{name}"\r\n', timeout=timeout, until=_quoted_done)
    m = re.search(rb'"(.*?)"', raw, re.S)
    if not m:
        return None
    val = m.group(1).decode("utf-8", "replace")
    return None if val == "?" else val


def setvar(conn, name, value):
    conn.send(f'! U1 setvar "{name}" "{value}"\r\n')


def sgd_do(conn, name, value=""):
    conn.send(f'! U1 do "{name}" "{value}"\r\n')


def host_identification(conn):
    """~HI -> {'model', 'firmware', 'dpmm', 'memory'}"""
    raw = conn.query("~HI\r\n", until=lambda b: b"\x03" in b)
    txt = raw.decode("latin-1").strip("\x02\x03\r\n ")
    parts = [p.strip() for p in txt.split(",")]
    info = {"raw": txt}
    if len(parts) >= 3:
        info["model"] = parts[0]
        info["firmware"] = parts[1]
        info["dpmm"] = parts[2]
        if len(parts) > 3:
            info["memory"] = parts[3]
    return info


def identify(conn):
    """~HI dene; yanıt yoksa (ör. kontrol öneki değiştirilmiş) SGD ile model/firmware oku."""
    try:
        info = host_identification(conn)
    except Exception:
        info = {}
    if info.get("model"):
        return info
    model = getvar(conn, "device.product_name", timeout=1.5)
    if not model:
        return {}
    info = {"model": model, "firmware": getvar(conn, "appl.name", timeout=1.2) or "?"}
    return info


def config_dump(conn):
    """^HH yapılandırma metnini {ETİKET: değer} sözlüğüne çevir."""
    raw = conn.query("^XA^HH^XZ", timeout=4.0, until=lambda b: b"\x03" in b).decode("latin-1")
    cfg = {}
    for line in raw.splitlines():
        line = line.strip("\x02\x03 \r")
        # etiket sağda, tek boşluklu kelimelerden oluşur; değerle arasında en az iki boşluk vardır
        m = re.match(r"(.+?)\s{2,}([A-Z][A-Z0-9./&-]*(?: [A-Z0-9./&-]+)*)$", line)
        if m:
            cfg[m.group(2).strip()] = m.group(1).strip()
    return cfg


def control_prefix(cfg):
    """^HH çıktısından kontrol önekini (~) bul. Dönüş: karakter kodu ya da None."""
    val = cfg.get("CONTROL PREFIX") or cfg.get("CONTROL CHAR")
    if not val:
        return None
    m = re.search(r"([0-9A-F]{2})H", val)
    return int(m.group(1), 16) if m else None


def dpi_from_config(cfg):
    m = re.search(r"(\d+)\s*/MM", cfg.get("RESOLUTION", ""))
    if m:
        return {6: 152, 8: 203, 12: 300, 24: 600}.get(int(m.group(1)))
    return None


PRINT_MODES = {"0": "Geri sarma", "1": "Soyma (Peel-off)", "2": "Yırtma (Tear-off)",
               "3": "Kesici (Cutter)", "4": "Aplikatör", "5": "Gecikmeli kesici",
               "6": "Astarsız soyma", "7": "Astarsız geri sarma", "8": "Kısmi kesici",
               "9": "RFID", "K": "Kiosk"}


def host_status(conn):
    """~HS çıktısını çözümle."""
    raw = conn.query("~HS", timeout=2.5, until=lambda b: b.count(b"\x03") >= 3)
    lines = re.findall(rb"\x02(.*?)\x03", raw, re.S)
    if len(lines) < 2:
        raise ValueError(f"Beklenmeyen ~HS yanıtı: {raw!r}")
    a = lines[0].decode("latin-1").split(",")
    b = lines[1].decode("latin-1").split(",")
    st = {
        "paper_out": a[1] == "1",
        "paused": a[2] == "1",
        "label_length": int(a[3]) if a[3].isdigit() else None,
        "formats_in_buffer": int(a[4]) if a[4].isdigit() else 0,
        "buffer_full": a[5] == "1",
        "partial_format": a[7] == "1",
        "corrupt_ram": a[9] == "1" if len(a) > 9 else False,
        "under_temp": a[10] == "1" if len(a) > 10 else False,
        "over_temp": a[11] == "1" if len(a) > 11 else False,
        "head_open": b[2] == "1",
        "ribbon_out": b[3] == "1",
        "thermal_transfer": b[4] == "1",
        "print_mode": PRINT_MODES.get(b[5], b[5]),
        "label_waiting": b[7] == "1",
        "labels_remaining": int(b[8]) if b[8].isdigit() else 0,
        "graphics_stored": int(b[10]) if len(b) > 10 and b[10].isdigit() else 0,
    }
    return st


def status_summary(st):
    problems = []
    if st["head_open"]:
        problems.append("Kafa açık")
    if st["paper_out"]:
        problems.append("Etiket bitti")
    if st["ribbon_out"]:
        problems.append("Ribon bitti")
    if st["paused"]:
        problems.append("Duraklatıldı")
    if st["buffer_full"]:
        problems.append("Arabellek dolu")
    if st["over_temp"]:
        problems.append("Kafa aşırı sıcak")
    if st["under_temp"]:
        problems.append("Kafa soğuk")
    if st["corrupt_ram"]:
        problems.append("RAM bozuk")
    return problems


# --- Keşif ------------------------------------------------------------------

def discover_cups():
    """CUPS kuyruklarını listele. socket:// olanları doğrudan ağ yazıcısı olarak döndür."""
    found = []
    try:
        out = subprocess.run(["lpstat", "-v"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return found
    for line in out.splitlines():
        m = re.match(r"device for (\S+):\s*(\S+)", line) or re.match(r"(\S+) için aygıt:\s*(\S+)", line)
        if not m:
            continue
        queue, uri = m.group(1).rstrip(":"), m.group(2)
        u = urlparse(uri)
        if u.scheme == "socket" and u.hostname:
            found.append(PrinterConfig(name=queue, kind="network", address=u.hostname, port=u.port or 9100))
        else:
            found.append(PrinterConfig(name=queue, kind="cups", address=queue))
    return found


def discover_usb():
    found = []
    for dev in sorted(glob.glob("/dev/usb/lp*")):
        name = f"USB {os.path.basename(dev)}"
        try:
            with UsbConnection(dev) as c:
                info = identify(c)
                if info.get("model"):
                    name = info["model"]
        except Exception:
            pass
        found.append(PrinterConfig(name=name, kind="usb", address=dev))
    return found


def _local_networks():
    nets = []
    try:
        out = subprocess.run(["ip", "-o", "-4", "addr", "show"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return nets
    for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", out):
        ip, prefix = m.group(1), int(m.group(2))
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        # büyük ağlarda yalnızca kendi /24'ümüzü tara
        net = ipaddress.ip_network(f"{ip}/{max(prefix, 24)}", strict=False)
        nets.append(net)
    return nets


def _probe(host, port=9100):
    try:
        with socket.create_connection((str(host), port), timeout=0.4):
            pass
    except OSError:
        return None
    # yalnızca ~HI'ya yanıt veren (Zebra) aygıtları döndür; 9100'ü açık diğer yazıcıları atla
    try:
        with NetworkConnection(str(host), port, connect_timeout=1.5) as c:
            info = identify(c)
    except Exception:
        return None
    if not info.get("model"):
        return None
    dpi = 203
    try:
        dpi = {6: 152, 8: 203, 12: 300, 24: 600}.get(int(info.get("dpmm", 8)), 203)
    except ValueError:
        pass
    return PrinterConfig(name=info["model"], kind="network", address=str(host), port=port, dpi=dpi)


def discover_network(progress=None):
    hosts = [h for net in _local_networks() for h in net.hosts()]
    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=96) as ex:
        futs = [ex.submit(_probe, h) for h in hosts]
        for i, f in enumerate(concurrent.futures.as_completed(futs)):
            r = f.result()
            if r:
                found.append(r)
            if progress:
                progress(i + 1, len(futs))
    return sorted(found, key=lambda p: ipaddress.ip_address(p.address))


def open_printer(cfg: PrinterConfig):
    return make_connection(cfg)
