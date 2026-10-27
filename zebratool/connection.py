"""Zebra yazıcılarla iletişim: ağ (TCP 9100), USB (/dev/usb/lpN) ve CUPS kuyruğu."""

import os
import select
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass, asdict


class ConnectionError_(Exception):
    pass


@dataclass
class PrinterConfig:
    name: str
    kind: str          # "network" | "usb" | "cups"
    address: str       # host, /dev/usb/lp0 ya da CUPS kuyruk adı
    port: int = 9100
    dpi: int = 203

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        return PrinterConfig(
            name=d.get("name", "Zebra"),
            kind=d.get("kind", "network"),
            address=d.get("address", ""),
            port=int(d.get("port", 9100)),
            dpi=int(d.get("dpi", 203)),
        )

    def describe(self):
        if self.kind == "network":
            return f"Ağ: {self.address}:{self.port}"
        if self.kind == "usb":
            return f"USB: {self.address}"
        return f"CUPS: {self.address}"


class Connection:
    """Tüm bağlantı türleri için ortak arayüz."""

    bidirectional = True

    def open(self):
        pass

    def close(self):
        pass

    def write(self, data: bytes):
        raise NotImplementedError

    def read(self, timeout=1.5, until=None) -> bytes:
        raise NotImplementedError

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # --- yardımcılar -------------------------------------------------------

    def send(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.write(data)

    def query(self, data, timeout=2.0, until=None) -> bytes:
        if not self.bidirectional:
            raise ConnectionError_("Bu bağlantı türü yazıcıdan yanıt okuyamaz (yalnızca gönderim).")
        self.send(data)
        return self.read(timeout=timeout, until=until)


def _read_loop(recv, timeout, until):
    """recv(kalan_süre) -> bytes|None; `until` koşulu sağlanınca ya da sessizlikte durur."""
    buf = b""
    deadline = time.monotonic() + timeout
    idle = 0.35  # ilk veriden sonra bu kadar sessizlik olursa bitti say
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        wait = min(remaining, idle) if buf else remaining
        chunk = recv(wait)
        if chunk is None:      # zaman aşımı
            if buf:
                break
            continue
        if chunk == b"":       # bağlantı kapandı
            break
        buf += chunk
        if until is not None and until(buf):
            break
    return buf


class NetworkConnection(Connection):
    def __init__(self, host, port=9100, connect_timeout=4.0):
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.sock = None

    def open(self):
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        except OSError as e:
            raise ConnectionError_(f"{self.host}:{self.port} adresine bağlanılamadı: {e}") from e

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def write(self, data):
        if not self.sock:
            self.open()
        self.sock.sendall(data)

    def read(self, timeout=1.5, until=None):
        def recv(wait):
            self.sock.settimeout(max(wait, 0.01))
            try:
                return self.sock.recv(65536)
            except socket.timeout:
                return None
        return _read_loop(recv, timeout, until)


class UsbConnection(Connection):
    """Linux usblp sürücüsü (/dev/usb/lpN). Kullanıcı 'lp' grubunda olmalı."""

    def __init__(self, device):
        self.device = device
        self.fd = None

    def open(self):
        try:
            self.fd = os.open(self.device, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as e:
            raise ConnectionError_(
                f"{self.device} için izin yok. Kullanıcınızı 'lp' grubuna ekleyin: "
                f"sudo usermod -aG lp $USER (sonra oturumu kapatıp açın)") from e
        except OSError as e:
            raise ConnectionError_(f"{self.device} açılamadı: {e}") from e

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def write(self, data):
        if self.fd is None:
            self.open()
        view = memoryview(data)
        deadline = time.monotonic() + 30
        while view:
            try:
                n = os.write(self.fd, view)
                view = view[n:]
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise ConnectionError_("USB yazma zaman aşımı")
                select.select([], [self.fd], [], 0.5)

    def read(self, timeout=1.5, until=None):
        def recv(wait):
            r, _, _ = select.select([self.fd], [], [], wait)
            if not r:
                return None
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                return None
            # usblp bazen veri yokken 0 bayt döndürür; kapanma sayma
            if data == b"":
                time.sleep(0.05)
                return None
            return data
        return _read_loop(recv, timeout, until)


class CupsConnection(Connection):
    """CUPS 'raw' kuyruğuna gönderim. Yanıt okunamaz."""

    bidirectional = False

    def __init__(self, queue):
        self.queue = queue
        self._buf = b""

    def open(self):
        self._buf = b""

    def write(self, data):
        self._buf += data

    def close(self):
        if not self._buf:
            return
        with tempfile.NamedTemporaryFile(suffix=".zpl", delete=False) as f:
            f.write(self._buf)
            path = f.name
        try:
            r = subprocess.run(["lp", "-d", self.queue, "-o", "raw", path],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                raise ConnectionError_(f"lp hatası: {r.stderr.strip() or r.stdout.strip()}")
        finally:
            os.unlink(path)
            self._buf = b""

    def read(self, timeout=1.5, until=None):
        return b""


def make_connection(cfg: PrinterConfig) -> Connection:
    if cfg.kind == "network":
        return NetworkConnection(cfg.address, cfg.port)
    if cfg.kind == "usb":
        return UsbConnection(cfg.address)
    if cfg.kind == "cups":
        return CupsConnection(cfg.address)
    raise ValueError(f"Bilinmeyen bağlantı türü: {cfg.kind}")
