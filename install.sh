#!/bin/bash
# ZebraTool kurulumu (kullanıcı için, root gerekmez).
# Kaldırmak için: bash install.sh --uninstall
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/zebratool"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
MIME="$HOME/.local/share/mime/packages"

if [ "$1" = "--uninstall" ]; then
    rm -rf "$DEST"
    rm -f "$BIN/zebra-setup" "$BIN/zebra-designer"
    rm -f "$APPS/zebra-setup.desktop" "$APPS/zebra-designer.desktop"
    rm -f "$ICONS/zebra-setup.svg" "$ICONS/zebra-designer.svg" "$MIME/zebratool.xml"
    update-desktop-database "$APPS" 2>/dev/null || true
    update-mime-database "$HOME/.local/share/mime" 2>/dev/null || true
    echo "ZebraTool kaldırıldı. (Ayarlar: ~/.config/zebratool)"
    exit 0
fi

# --- bağımlılıklar
missing=()
python3 -c "import PyQt6.QtWidgets" 2>/dev/null || missing+=(python-pyqt6)
command -v zint >/dev/null || missing+=(zint)
if [ ${#missing[@]} -gt 0 ]; then
    echo "Eksik paketler: ${missing[*]}"
    echo "Kuruluyor: sudo pacman -S --needed ${missing[*]}"
    sudo pacman -S --needed "${missing[@]}"
fi

# --- dosyalar
mkdir -p "$DEST" "$BIN" "$APPS" "$ICONS" "$MIME"
rm -rf "$DEST/zebratool" "$DEST/bin"
cp -r "$SRC/zebratool" "$SRC/bin" "$DEST/"
find "$DEST" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
chmod +x "$DEST/bin/"*
ln -sf "$DEST/bin/zebra-setup" "$BIN/zebra-setup"
ln -sf "$DEST/bin/zebra-designer" "$BIN/zebra-designer"
cp "$SRC/zebratool/icons/"*.svg "$ICONS/"

cat > "$APPS/zebra-setup.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Zebra Setup Utility
Name[tr]=Zebra Kurulum Aracı
GenericName=Zebra Setup Utilities
Comment=Configure and calibrate Zebra label printers, send files
Comment[tr]=Zebra etiket yazıcılarını yapılandır, kalibre et, dosya gönder
Exec=$BIN/zebra-setup
Icon=zebra-setup
Categories=Utility;Settings;HardwareSettings;
Keywords=zebra;printer;yazıcı;etiket;zpl;
Terminal=false
EOF

cat > "$APPS/zebra-designer.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Zebra Label Designer
Name[tr]=Zebra Etiket Tasarımcısı
GenericName=ZebraDesigner
Comment=Design barcode labels and print them on Zebra printers
Comment[tr]=Barkodlu etiket tasarla ve Zebra yazıcıda bas
Exec=$BIN/zebra-designer %f
Icon=zebra-designer
MimeType=application/x-zebra-label;
Categories=Office;Graphics;
Keywords=zebra;label;etiket;barkod;barcode;zpl;
Terminal=false
EOF

cat > "$MIME/zebratool.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-zebra-label">
    <comment>Zebra etiketi</comment>
    <glob pattern="*.zlbl"/>
    <icon name="zebra-designer"/>
  </mime-type>
</mime-info>
EOF

update-desktop-database "$APPS" 2>/dev/null || true
update-mime-database "$HOME/.local/share/mime" 2>/dev/null || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Kuruldu."
echo "  Uygulama menüsünde: 'Zebra Kurulum Aracı' ve 'Zebra Etiket Tasarımcısı'"
echo "  Terminalden: zebra-setup / zebra-designer"

if ls /dev/usb/lp* >/dev/null 2>&1 && ! id -nG | grep -qw lp; then
    echo
    echo "USB yazıcı için kullanıcınızı 'lp' grubuna ekleyin:"
    echo "  sudo usermod -aG lp $USER   (ardından oturumu kapatıp açın)"
fi
