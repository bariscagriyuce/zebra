# ZebraTool — Zebra Setup Utility and Label Designer for Linux

[Türkçe](README.md) | **English**

Two applications for CachyOS/Arch that replace the Windows-only **Zebra Setup Utilities** and
**ZebraDesigner 3**. Written in Python + PyQt6. They talk to the printer directly over ZPL/SGD, so no
driver is needed.

The user interface is available in **English** and **Turkish**. The language follows the system locale
and can be changed from the **Language / Dil** menu (the application restarts).

## Installation

```bash
bash install.sh
```

Required packages (the script installs them if missing): `python-pyqt6`, `zint`.
To uninstall: `bash install.sh --uninstall`

To try without installing: `sh bin/zebra-setup` and `sh bin/zebra-designer`.

## Connection types

| Type | Address | Read back (status/settings) |
|------|---------|-----------------------------|
| Network | IP, port 9100 | Yes |
| USB | `/dev/usb/lp0` (user must be in the `lp` group) | Yes |
| CUPS | queue name (raw) | No, send only |

CUPS queues with a `socket://` URI are automatically turned into direct network printers.

## Zebra Setup Utility (`zebra-setup`)

- **Find printers:** scans CUPS queues, USB and the local network (port 9100) for Zebra printers.
- **Status:** model, firmware version, serial number, resolution, print length counter; warnings for
  head open / media out / ribbon out / paused.
- **Settings:** darkness, speed, print method (thermal transfer / direct thermal), media type
  (gap / black mark / continuous), print mode (tear-off / peel-off / cutter), print width, label
  length, tear-off position, label top, left shift. Reads them from the printer and saves them
  permanently with `^JUS`.
- **Actions:** calibration, configuration/network label, sensor profile, test label, pause/resume,
  cancel all jobs, restart, factory defaults.
- **Network:** wired / wireless IP settings (DHCP or static), Wi-Fi SSID and password.
- **Send file:** sends a `.zpl`, `.prn`, font, graphic or firmware file to the printer as-is.
- **Printer memory:** lists the files on E:, R: and other drives, deletes them, prints a stored format.
- **Terminal:** sends raw ZPL/SGD commands and shows the response (with a list of ready-made commands).

## Zebra Label Designer (`zebra-designer`)

- True-scale canvas (mm and dots), grid with snapping, zoom.
- Elements: **text**, **barcode** (Code 128, Code 39, Code 93, EAN-13, EAN-8, UPC-A, Interleaved 2of5,
  QR, Data Matrix, PDF417), **box**, **line** (horizontal/vertical/diagonal), **ellipse**, **image**
  (converted to black and white with dithering).
- Text can use the printer's built-in font (Zebra 0) or any system font. System fonts are sent as
  graphics, so what you see on screen is what gets printed, including non-ASCII characters.
- Rotation (0/90/180/270), paragraphs (automatic word wrap, alignment), reverse print.
- **Variable printing:** put fields such as `{NAME}` or `{PRICE}` in text or barcode data; they are filled
  in by hand, from a CSV file or by a counter (`{COUNTER}`) when printing. Built-in fields: `{DATE}`,
  `{TIME}` (Turkish aliases `{SAYAC}`, `{TARIH}`, `{SAAT}` also work).
- Undo/redo, copy/paste, alignment, bring to front / send to back.
- Save format is `.zlbl` (JSON). Export to ZPL and PNG; press F5 to see the ZPL code.
- In the label settings, “Read from printer” reads the label size from the printer.

### Shortcuts
`T` text · `B` barcode · `Q` QR · `R` box · `L` line · `E` ellipse · `I` image ·
arrow keys move 1 dot, `Shift`+arrow 10 dots · `Ctrl`+wheel zoom · `Ctrl+0` fit ·
`F5` ZPL · `Ctrl+P` print.

## Known limitations

- The on-screen “Zebra 0” font only approximates the printer font; widths may differ by a few dots.
  Pick a system font if you need an exact match.
- QR codes cannot be rotated in ZPL (a printer limitation).
- ZebraDesigner `.nlbl` files cannot be opened (closed format). There is no ZPL import.
- Features specific to the Windows driver (RFID encoding, database connections) are not available.
- Some older printers (e.g. GT800) may have their control prefix changed from `~`. Those printers do
  not answer `~` commands; the setup utility then reads their settings from `^HH`, shows a warning, and
  offers a button to restore the prefix to `~`.
