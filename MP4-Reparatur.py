# -*- coding: utf-8 -*-
"""
MP4-Reparatur – macht aus YouTube-Downloads saubere, handytaugliche H.265-MP4s.

Quellordner wählen → Analyse (ffprobe) → Umwandeln nach H.265 (NVENC oder x265) + AAC,
feste Bildrate, hvc1-Kennung, faststart. Zwei Ziel-Arten:
  • Fertig-Ordner: Ergebnisse nach <Quellordner>_FERTIG (oder frei gewählt), Quelle bleibt unberührt.
  • Original ersetzen: neue Datei kommt an die Stelle des Originals (erst nach bestandener Prüfung);
    das Original wird – Standard – in einen Aufhebe-Ordner verschoben (Ordnerstruktur in voller Tiefe).
Umgewandelt wird im Arbeitsordner (%USERPROFILE%\\MP4-Reparatur\\Arbeitsordner), nie halb auf der NAS.

ffmpeg/ffprobe werden wie beim YT-Downloader beim Start geprüft und bei Bedarf geladen.
Brenner23 · Tkinter
"""
import ctypes
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME = "MP4-Reparatur"
APP_VERSION = "1.0.2"
COPY_YEAR = "2026"

NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

if getattr(sys, "frozen", False):
    script_dir = sys._MEIPASS
    TOOLS_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                             "MP4-Reparatur", "bin")
else:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    TOOLS_DIR = os.path.join(script_dir, "_werkzeuge")

CONFIG_DIR = os.path.join(os.path.expanduser("~"), "MP4-Reparatur")
SETTINGS_FILE = os.path.join(CONFIG_DIR, "settings.json")
WORK_ROOT = os.path.join(CONFIG_DIR, "Arbeitsordner")
ICON_FILE = os.path.join(script_dir, "MP4-Reparatur.ico")

FFMPEG_RELEASE_URL = "https://www.gyan.dev/ffmpeg/builds/release-version"
FFMPEG_ZIP_URL = "https://github.com/GyanD/codexffmpeg/releases/download/{v}/ffmpeg-{v}-essentials_build.zip"
FFMPEG_GH_API = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"

VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".webm", ".mov", ".avi", ".flv", ".ts", ".3gp", ".wmv", ".mpg", ".mpeg"}
FERTIG_SUFFIX = "_FERTIG"
ORIGINAL_SUFFIX = "_ORIGINAL"
SKIP_DIRS = {"$recycle.bin", "system volume information", "@eadir", "#recycle", ".trash"}
QUALITIES = [("Hoch", 22), ("Ausgewogen", 25), ("Klein", 28)]
# Portion: >0 = so viele Dateien, <0 = so viele Minuten (nur Dateien anfangen, die laut Schätzung noch passen)
PORTIONS = [("Alle", 0), ("20", 20), ("50", 50), ("100", 100), ("2 Std", -120)]
# Startwerte für die Zeitschätzung: verarbeitete Bildpunkte pro Sekunde (werden nach jedem Lauf nachgelernt)
DEFAULT_PXRATE = {"gpu": 4.0e8, "cpu": 3.4e7}     # gemessen RTX 3050 / x265 medium, 480×360
DEFAULT_IO_RATE = 40e6                             # Byte/s für Lesen + Zurückschreiben (NAS)

DEFAULT_SETTINGS = {
    "theme": "dark", "source": "",
    "engine": "gpu", "quality": 25, "skip_done": True, "copy_clean": True,
    "deep_check": False, "set_date": True, "geometry": "",
    "mode": "fertig", "fertig_dir": "", "move_orig": False, "orig_dir": "",
    "pxrate_gpu": 0, "pxrate_cpu": 0, "io_rate": 0,
    "never_bigger": True, "size_factor": 0, "portion": 0, "lang": "de",
}
# Video-kbit/s je Qualitätsstufe bei 480×360×25 (gemessen x265 crf bzw. NVENC cq+5), wird mit Pixelrate^0.75 skaliert
QREF_KBPS = {22: 450, 25: 310, 28: 220}
QREF_PXS = 480 * 360 * 25
RESERVE = 2 * 1024 ** 3                            # so viel bleibt auf jedem Laufwerk immer frei

PALETTES = {
    "dark": dict(bg="#0e1014", surface="#161920", surface2="#1f232c", hover="#282d39", input="#12151b",
                 border="#262b35", border_strong="#3a4150", text="#e7e9ef", muted="#8a92a5",
                 accent="#e5484d", accent_hover="#f0595e", logbg="#0b0d11", ok="#3fb68b", warn="#f5a524",
                 sel="#3a1e22", off="#4a5060"),
    "light": dict(bg="#f3f4f7", surface="#ffffff", surface2="#f1f2f6", hover="#e7e9ef", input="#fafbfc",
                  border="#e2e5eb", border_strong="#c3c8d2", text="#161a22", muted="#677083",
                  accent="#dc3e44", accent_hover="#e5484d", logbg="#f7f8fa", ok="#1f9d6e", warn="#c27c00",
                  sel="#fbe3e4", off="#b9bfcb"),
}

FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI Semibold", 10)
FONT_CARD = ("Segoe UI Semibold", 12)
FONT_TITLE = ("Segoe UI Semibold", 20)
FONT_SMALL = ("Segoe UI", 9)
FONT_SECTION = ("Segoe UI Semibold", 8)
FONT_MONO = ("Cascadia Mono", 9)


PROJECT_URL = "https://github.com/brenner23/MP4-Reparatur"


# ── Sprache (DE/EN) ──────────────────────────────────────────────────────────
# Alle sichtbaren Texte laufen über T("deutscher Text {0}", wert). Neue Texte → hier in EN ergänzen.
LANG = "de"


def T(text, *args):
    t = EN.get(text, text) if LANG == "en" else text
    return t.format(*args) if args else t


EN = {
    # Kopf, Karten, Knöpfe
    "Version {0}  ·  H.265 für Handy & Heimkino": "Version {0}  ·  H.265 for phone & home cinema",
    "Quelle & Ziel": "Source & target", "Einstellungen": "Settings", "Dateien": "Files", "Reparatur": "Repair",
    "Protokoll": "Log", "Quellordner": "Source folder", "Durchsuchen": "Browse", "Öffnen": "Open",
    "Ändern": "Change", "Ziel": "Target", "In Fertig-Ordner": "To finished folder",
    "Original ersetzen": "Replace original", "Ordner analysieren": "Analyse folder",
    "Umwandeln mit": "Encode with", "Qualität": "Quality", "Hoch": "High", "Ausgewogen": "Balanced",
    "Klein": "Small", "Alle": "All", "2 Std": "2 hrs", "Portion – nicht alles auf einmal": "Batch – not all at once",
    "Reparatur starten": "Start repair", "Abbrechen": "Cancel", "Bereit": "Ready",
    "ausblenden ▴": "hide ▴", "einblenden ▾": "show ▾",
    "Quellordner (wird nur gelesen)": "Source folder (read only)",
    "Ordner / Laufwerk mit den Videos (z. B. Z:\\)": "Folder / drive with the videos (e.g. Z:\\)",
    "(automatisch)": "(automatic)",
    "Originale verschieben nach …": "Move originals to …",
    "Neue Datei ersetzt das Original an Ort und Stelle –\nerst nachdem sie fertig umgewandelt und geprüft ist.":
        "The new file replaces the original in place –\nonly after it has been fully converted and checked.",
    "Ordnerstruktur wird in voller Tiefe nachgebaut, je Datei nach getaner Arbeit.":
        "Folder structure is rebuilt at full depth, file by file once done.",
    "aus: Originale werden aufgehoben in  {0}": "off: originals are kept in  {0}",
    "aus: Originale bleiben, wo sie sind.": "off: originals stay where they are.",
    # Schalter
    "Fertige überspringen (Fortsetzen)": "Skip finished files (resume)",
    "Saubere Dateien unverändert mitkopieren": "Copy clean files unchanged",
    "Nie größer als das Original": "Never larger than the original",
    "Bild bleibt wie Original": "picture stays like the original",
    "Gründlich prüfen (komplett durchspielen)": "Thorough check (decode completely)",
    "langsamer": "slower", "Original-Datum übernehmen": "Keep original date",
    # Liste
    "Datei": "File", "Format": "Format", "Auflösung": "Resolution", "Befund": "Findings", "Status": "Status",
    "sauber": "clean", " · {0} gibt es schon": " · {0} already exists",
    "{0} als {1} getarnt": "{0} disguised as {1}", "Container {0}": "Container {0}", "Video {0}": "Video {0}",
    "HEVC ohne hvc1-Kennung": "HEVC without hvc1 tag", "Farbformat {0}": "Colour format {0}",
    "Bildrate schwankt": "Variable frame rate", "Ton {0}": "Audio {0}", "Endung {0}": "Extension {0}",
    "kein Video lesbar": "no readable video",
    "H.265 umwandeln": "convert to H.265", "neu verpacken": "remux", "schon gut": "already fine",
    "Name belegt": "name taken", "schon fertig": "already done", "kopieren": "copy", "auslassen": "skip",
    "Fehler": "Error", "läuft …": "running …", "abgebrochen": "cancelled", "kopiert": "copied",
    "✓ H.265": "✓ H.265", "✓ neu verpackt": "✓ remuxed",
    "{0} in der Liste  ·  {1} erledigt ausgeblendet": "{0} in the list  ·  {1} done and hidden",
    # Grafikkarte / ffmpeg
    "⧗ Grafikkarte …": "⧗ Graphics card …", "Prüfe Grafikkarte …": "Checking graphics card …",
    "NVIDIA NVENC · schnell, H.265 Main 8-Bit": "NVIDIA NVENC · fast, H.265 Main 8-bit",
    "x265 auf dem Prozessor · langsamer, etwas kleinere Dateien": "x265 on the CPU · slower, slightly smaller files",
    "GPU nicht nutzbar: {0}": "GPU not usable: {0}",
    "NVIDIA-Treiber zu alt (ffmpeg braucht ab {0})": "NVIDIA driver too old (ffmpeg needs {0} or newer)",
    "keine NVIDIA-Grafikkarte gefunden": "no NVIDIA graphics card found",
    "✓ GPU: NVIDIA NVENC (H.265)": "✓ GPU: NVIDIA NVENC (H.265)", "● GPU: {0} → CPU": "● GPU: {0} → CPU",
    "ffmpeg fehlt · keine Internetverbindung": "ffmpeg missing · no internet connection",
    "ffmpeg wird geladen ({0}) …": "Downloading ffmpeg ({0}) …", "lade ffmpeg {0} … {1} %": "downloading ffmpeg {0} … {1} %",
    "ffmpeg-Download fehlgeschlagen": "ffmpeg download failed",
    "ffmpeg-Download fehlgeschlagen: {0}": "ffmpeg download failed: {0}",
    "ffmpeg {0} · neu installiert": "ffmpeg {0} · newly installed", "ffmpeg {0} · {1} verfügbar": "ffmpeg {0} · {1} available",
    " · Update-Prüfung offline": " · update check offline", "ffmpeg {0} · aktualisiert": "ffmpeg {0} · updated",
    "ffmpeg wurde auf {0} aktualisiert.": "ffmpeg was updated to {0}.",
    "ffmpeg {0} · Update fehlgeschlagen": "ffmpeg {0} · update failed",
    "ffmpeg-Update fehlgeschlagen: {0}": "ffmpeg update failed: {0}",
    "✖ {0} – ohne ffmpeg kann nichts umgewandelt werden.": "✖ {0} – nothing can be converted without ffmpeg.",
    "Für ffmpeg gibt es eine neue Version.\n\nInstalliert:  {0}\nNeu:  {1}\n\nJetzt herunterladen? (ca. 100 MB, im Hintergrund)":
        "A new ffmpeg version is available.\n\nInstalled:  {0}\nNew:  {1}\n\nDownload now? (about 100 MB, in the background)",
    "Bitte erst nach der Reparatur aktualisieren.": "Please update after the repair has finished.",
    "Im Archiv fehlt: {0}": "Missing in the archive: {0}",
    # Dialoge
    "Wohin sollen die Originale verschoben werden?": "Where should the originals be moved to?",
    "Wohin sollen die fertigen Dateien?": "Where should the finished files go?",
    "Quellordner mit Videos wählen": "Choose the source folder with videos",
    "Das ist der Quellordner selbst – bitte einen anderen Ordner wählen.":
        "That is the source folder itself – please choose a different folder.",
    "Den Ordner gibt es (noch) nicht:\n{0}": "This folder does not exist (yet):\n{0}",
    "Quellordner nicht gefunden:\n{0}": "Source folder not found:\n{0}",
    "Das ist ein Fertig- bzw. Original-Ordner – bitte den Ordner mit den zu reparierenden Videos wählen.":
        "That is a finished or originals folder – please choose the folder with the videos to repair.",
    "↻ Neu analysieren": "↻ Analyse again",
    "Es läuft noch eine Reparatur.\nWirklich beenden?": "A repair is still running.\nQuit anyway?",
    "Bitte warten, bis die Reparatur fertig ist – dann kann die Sprache umgestellt werden.":
        "Please wait until the repair has finished – then the language can be switched.",
    # Analyse
    "=== Analyse: {0} ===": "=== Analysis: {0} ===", "suche Videos …": "searching for videos …",
    "{0} Dateien aus dem Analyse-Gedächtnis (unverändert, nicht neu gelesen).":
        "{0} files from the analysis memory (unchanged, not read again).",
    "0 von {0} gelesen": "0 of {0} read", "Analyse: {0} Videos gefunden": "Analysis: {0} videos found",
    "Keine Videodateien gefunden.": "No video files found.", "{0} von {1} gelesen": "{0} of {1} read",
    "analysiere … {0}/{1}": "analysing … {0}/{1}", "{0} von {1} Dateien gelesen": "{0} of {1} files read",
    "Analyse {0} %": "Analysis {0} %", "{0} Dateien · {1}": "{0} files · {1}",
    "{0} mit Problemen · {1} zu erledigen · Spielzeit {2}": "{0} with problems · {1} to do · playtime {2}",
    "{0} Dateien gelesen, {1} mit Problemen, {2} zu erledigen.": "{0} files read, {1} with problems, {2} to do.",
    "Nichts zu tun": "Nothing to do",
    # Zeit & Platz
    " · aus früheren Läufen gelernt": " · learned from earlier runs",
    "Portion: {0} von {1} Dateien  ·  alle zusammen {2}, ca. {3}": "Batch: {0} of {1} files  ·  all together {2}, approx. {3}",
    "Diese Portion: ca. {0} mit {1}  ·  fertig gegen {2}": "This batch: approx. {0} with {1}  ·  done around {2}",
    "{0} Dateien warten · {1}": "{0} files waiting · {1}",
    "Geschätzte Dauer: ca. {0} mit {1}  ·  fertig gegen {2}": "Estimated time: approx. {0} with {1}  ·  done around {2}",
    "Laufzeit {0}": "Elapsed {0}", "  ·  Rest ca. {0}  ·  fertig gegen {1}": "  ·  approx. {0} left  ·  done around {1}",
    "Alles zusammen: Rest ca. {0}  ·  fertig gegen {1}": "All together: approx. {0} left  ·  done around {1}",
    "{0}  frei {1}  →  danach ca. {2}  ({3})": "{0}  free {1}  →  afterwards approx. {2}  ({3})",
    "✖ {0} reicht nicht: braucht {1}, frei {2}": "✖ {0} not enough: needs {1}, free {2}",
    "Originale bleiben auf demselben Laufwerk – dort wird nichts frei. Tipp: „Originale verschieben nach …“ auf eine andere Platte.":
        "Originals stay on the same drive – no space is freed there. Tip: “Move originals to …” another drive.",
    "Platzbedarf: {0} → ca. {1} ({2} %)": "Space needed: {0} → approx. {1} ({2} %)",
    "  ·  Schätzung, wird mit jedem Lauf genauer": "  ·  estimate, gets more accurate with every run",
    "⚠ Es passen voraussichtlich nur {0} von {1} Dateien – danach hält das Tool an.":
        "⚠ Only about {0} of {1} files will fit – the tool will stop after that.",
    "Der Platz reicht nicht für alle Dateien:": "There is not enough space for all files:",
    "Voraussichtlich passen {0} von {1} Dateien.\nDas Tool arbeitet Datei für Datei und hält von selbst an, bevor ein Laufwerk unter {2} frei fällt.\n\nTrotzdem starten?":
        "About {0} of {1} files will fit.\nThe tool works file by file and stops by itself before a drive drops below {2} free.\n\nStart anyway?",
    "⚠ Platz auf {0} reicht nicht mehr für „{1}“ (es müssen {2} frei bleiben) – angehalten.":
        "⚠ Not enough space left on {0} for “{1}” ({2} must stay free) – stopped.",
    "Angehalten – Platz auf {0} wird knapp": "Stopped – running out of space on {0}",
    "Auf {0} wird der Platz knapp, deshalb wurde angehalten.\n\nAlles bis hierher ist fertig und sicher abgelegt. Platz schaffen (z. B. Originale auf eine andere Platte) und dann einfach neu analysieren – es geht an derselben Stelle weiter.":
        "Space on {0} is running out, so the repair was stopped.\n\nEverything up to here is finished and stored safely. Free up space (e.g. move originals to another drive) and simply analyse again – it continues at the same point.",
    # Reparatur
    "{0} Dateien werden repariert und ERSETZEN die Originale an Ort und Stelle.\n\nDie Originale werden verschoben nach:\n{1}\n(Ordnerstruktur bleibt erhalten)\n\nErsetzt wird erst, wenn die neue Datei geprüft ist.\n\nStarten?":
        "{0} files will be repaired and REPLACE the originals in place.\n\nThe originals will be moved to:\n{1}\n(folder structure is kept)\n\nA file is only replaced after the new one has been checked.\n\nStart?",
    "Nach getaner Arbeit werden die Originale verschoben nach:\n{0}\n\nStarten?":
        "When done, the originals will be moved to:\n{0}\n\nStart?",
    "Portion {0} von {1}": "batch {0} of {1}", "{0} Dateien": "{0} files",
    "=== Reparatur: {0} · {1} · Qualität {2} ===": "=== Repair: {0} · {1} · quality {2} ===",
    "Ziel: {0}": "Target: {0}", "Ziel: Originale ersetzen": "Target: replace originals",
    "Originale nach: {0}": "Originals to: {0}", "→ {0}  (sauber, wird kopiert)": "→ {0}  (clean, copying)",
    "GPU-Fehler ({0}) – nochmal mit CPU …": "GPU error ({0}) – retrying with CPU …",
    "ffmpeg Rückgabewert {0}": "ffmpeg exit code {0}", "Prüfung fehlgeschlagen: {0}": "Check failed: {0}",
    "Ablegen fehlgeschlagen: {0}": "Storing failed: {0}", "Original liegt sicher in {0}": "Original is safe in {0}",
    "Ergebnis ist {0} · {1}": "Result is {0} · {1}", "Länge {0} statt {1}": "Length {0} instead of {1}",
    "prüfe komplett …": "checking completely …", "Decoder meldet: {0}": "Decoder reports: {0}",
    "{0}× Echtzeit": "{0}× realtime", "Datei {0} von {1}  ·  gesamt {2} %": "File {0} of {1}  ·  total {2} %",
    "{0} von {1} verarbeitet": "{0} of {1} processed", "{0} ersetzt": "{0} replaced",
    "{0} umgewandelt": "{0} converted", "{0} kopiert": "{0} copied", "{0} Originale verschoben": "{0} originals moved",
    "{0} Fehler": "{0} errors", "Abgebrochen": "Cancelled", "Fertig": "Done", "Fertig mit Fehlern": "Done with errors",
    "{0} repariert · {1} Fehler": "{0} repaired · {1} errors",
    "=== {0}: {1} ok, {2} Fehler{3} · {4} ===": "=== {0}: {1} ok, {2} errors{3} · {4} ===",
    "Portion fertig: {0} Dateien in {1}.\n\nNoch {2} Dateien übrig (zusammen ca. {3}).\n\nMit der nächsten Portion weitermachen?\n({4} Dateien, ca. {5})":
        "Batch done: {0} files in {1}.\n\n{2} files left (approx. {3} in total).\n\nContinue with the next batch?\n({4} files, approx. {5})",
    "Pause – {0} Dateien übrig. Beim nächsten Mal geht es an dieser Stelle weiter.":
        "Paused – {0} files left. Next time it continues from here.",
    "breche ab …": "cancelling …",
    "Bereit – „Ordner analysieren“ klicken": "Ready – click “Analyse folder”",
}


# ── Hilfsfunktionen ──────────────────────────────────────────────────────────
def tool(name):
    return os.path.join(TOOLS_DIR, name)


def run_out(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           creationflags=NO_WINDOW, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return -1, str(e)


def ver_tuple(s):
    m = re.match(r"(\d+(?:\.\d+)*)", s or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def ffmpeg_version():
    if not os.path.exists(tool("ffmpeg.exe")):
        return None
    rc, out = run_out([tool("ffmpeg.exe"), "-version"], 30)
    m = re.search(r"ffmpeg version (\S+)", out)
    return m.group(1) if m else None


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def ffmpeg_remote_version():
    """Aktuelle ffmpeg-Version (gyan.dev, sonst GitHub). None, wenn nicht erreichbar."""
    try:
        v = http_get(FFMPEG_RELEASE_URL).strip()
        if re.fullmatch(r"\d+(\.\d+)*", v):
            return v
    except Exception:
        pass
    try:
        v = json.loads(http_get(FFMPEG_GH_API)).get("tag_name", "")
        return v if re.fullmatch(r"\d+(\.\d+)*", v) else None
    except Exception:
        return None


def http_download(url, target, progress=None):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=30) as r, open(target, "wb") as f:
        total, got = int(r.headers.get("content-length") or 0), 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total and progress:
                progress(got * 100 // total)


def install_ffmpeg(version, progress=None):
    """Lädt ffmpeg + ffprobe (gyan essentials) in den Werkzeug-Ordner. Wirft bei Fehlern."""
    os.makedirs(TOOLS_DIR, exist_ok=True)
    members = {"ffmpeg.exe", "ffprobe.exe"}
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    try:
        http_download(FFMPEG_ZIP_URL.format(v=version), tmp, progress)
        found = set()
        with zipfile.ZipFile(tmp) as z:
            for info in z.infolist():
                base = os.path.basename(info.filename)
                if base in members:
                    part = tool(base) + ".neu"
                    with z.open(info) as src, open(part, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    os.replace(part, tool(base))
                    found.add(base)
        if found != members:
            raise RuntimeError(T("Im Archiv fehlt: {0}", ", ".join(members - found)))
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def detect_nvenc():
    """(ok, hinweis) – testet hevc_nvenc mit einem winzigen Testbild."""
    rc, out = run_out([tool("ffmpeg.exe"), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                       "-i", "color=c=black:s=256x256:d=0.2", "-frames:v", "3", "-c:v", "hevc_nvenc",
                       "-f", "null", "-"], 30)
    if rc == 0:
        return True, ""
    m = re.search(r"minimum required Nvidia driver for nvenc is ([\d.]+)", out)
    if m:
        return False, ["NVIDIA-Treiber zu alt (ffmpeg braucht ab {0})", m.group(1).split('.')[0]]
    return False, ["keine NVIDIA-Grafikkarte gefunden"]


def pid_alive(pid):
    if os.name != "nt":
        return True
    h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        ctypes.windll.kernel32.CloseHandle(h)
        return True
    return False


def work_dir_path():
    return os.path.join(WORK_ROOT, str(os.getpid()))


def drive_of(path):
    """Laufwerk eines Pfads (C:, Z: oder \\\\server\\freigabe), groß geschrieben."""
    d = os.path.splitdrive(os.path.abspath(str(path)))[0]
    return d.upper() if len(d) == 2 else d.lower()


def disk_free(drive_or_path):
    p = Path(drive_or_path if not re.fullmatch(r"[A-Za-z]:", str(drive_or_path)) else str(drive_or_path) + "\\")
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return 0


def work_dir():
    d = work_dir_path()
    os.makedirs(d, exist_ok=True)
    return d


def clean_work_dirs(own=True):
    """Löscht Reste im Arbeitsordner: den eigenen (own=True) und die von beendeten Instanzen."""
    if not os.path.isdir(WORK_ROOT):
        return
    for name in os.listdir(WORK_ROOT):
        mine = name == str(os.getpid())
        dead = name.isdigit() and not pid_alive(int(name))
        if (mine and own) or (not mine and dead):
            shutil.rmtree(os.path.join(WORK_ROOT, name), ignore_errors=True)


CACHE_FILE = os.path.join(CONFIG_DIR, "analyse_gedaechtnis.json")
CACHE_VERSION = 3                                    # erhöhen, wenn analyze() neue Felder bekommt


def load_cache():
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_cache(cache):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = CACHE_FILE + ".neu"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, CACHE_FILE)
    except OSError:
        pass


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            s.update(json.load(f))
    except (OSError, ValueError):
        pass
    return s


def save_settings(s):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def frac(s):
    try:
        a, _, b = (s or "0/0").partition("/")
        return float(a) / float(b or 1) if float(b or 1) else 0.0
    except ValueError:
        return 0.0


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{n:.0f} {unit}"
        n /= 1024


def fmt_time(sec):
    sec = int(max(0, sec))
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d} h" if h else f"{m}:{s:02d}"


def short_path(p, n=78):
    """Langen Pfad in der Mitte kürzen, damit er in eine Zeile passt."""
    p = str(p)
    return p if len(p) <= n else p[:n // 2 - 1] + " … " + p[-(n // 2 - 2):]


def mix(c1, c2, t):
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


ISSUE_TEXT = {
    "disguised": "{0} als {1} getarnt", "container": "Container {0}", "video": "Video {0}",
    "hvc1": "HEVC ohne hvc1-Kennung", "pixfmt": "Farbformat {0}", "vfr": "Bildrate schwankt",
    "audio": "Ton {0}", "ext": "Endung {0}",
}


def issue_text(i):
    return T(ISSUE_TEXT.get(i[0], i[0]), *i[1:])


def issues_text(issues):
    return ", ".join(issue_text(i) for i in issues)


# ── Analyse ──────────────────────────────────────────────────────────────────
def probe(path):
    rc, out = run_out([tool("ffprobe.exe"), "-v", "error", "-show_format", "-show_streams",
                       "-of", "json", str(path)], 90)
    try:
        return json.loads(out[out.index("{"):]) if "{" in out else {}
    except ValueError:
        return {}


def analyze(path):
    """Liest Container/Codecs und listet, was am Handy Probleme macht."""
    data = probe(path)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    res = dict(ok=False, error="", issues=[], container="", vcodec="", acodec="", w=0, h=0,
               fps=0.0, rate="25/1", duration=0.0, has_audio=a is not None, v_copy=False, a_copy=False)
    if not v:
        res["error"] = "kein Video lesbar"               # wird mit T() angezeigt
        return res
    fname = fmt.get("format_name", "")
    res["container"] = "MP4" if "mp4" in fname else ("WebM/MKV" if "matroska" in fname else fname.upper()[:10])
    res["vcodec"] = v.get("codec_name", "?")
    res["acodec"] = a.get("codec_name", "") if a else ""
    res["w"], res["h"] = int(v.get("width") or 0), int(v.get("height") or 0)
    try:
        res["duration"] = float(fmt.get("duration") or v.get("duration") or 0)
    except ValueError:
        res["duration"] = 0.0

    r_fps, avg_fps = frac(v.get("r_frame_rate")), frac(v.get("avg_frame_rate"))
    if 1 <= r_fps <= 120:
        fps, rate = r_fps, v.get("r_frame_rate")
    elif 1 <= avg_fps <= 120:
        fps, rate = avg_fps, v.get("avg_frame_rate")
    else:
        fps, rate = 25.0, "25/1"
    res["fps"], res["rate"] = fps, rate
    vfr = avg_fps > 120 or (avg_fps > 0 and abs(avg_fps - r_fps) / max(r_fps, 1) > 0.02)

    issues = res["issues"]                         # als Kürzel, übersetzt wird erst bei der Anzeige (issue_text)
    ext = Path(path).suffix.lower()
    if res["container"] != "MP4":
        issues.append(["disguised", res["container"], ext] if ext in (".mp4", ".m4v") else ["container", res["container"]])
    if res["vcodec"] != "hevc":
        issues.append(["video", res["vcodec"].upper()])
    elif v.get("codec_tag_string") != "hvc1":
        issues.append(["hvc1"])
    if v.get("pix_fmt") not in ("yuv420p", "yuvj420p"):
        issues.append(["pixfmt", str(v.get("pix_fmt"))])
    if vfr:
        issues.append(["vfr"])
    if a and res["acodec"] != "aac":
        issues.append(["audio", res["acodec"].capitalize()])
    if ext not in (".mp4", ".m4v"):
        issues.append(["ext", ext])

    res["v_copy"] = res["vcodec"] == "hevc" and v.get("pix_fmt") == "yuv420p" and not vfr
    res["a_copy"] = res["acodec"] == "aac" and int(a.get("channels") or 2) <= 2 if a else False
    res["ok"] = not issues

    # Bitraten der Quelle (für die Größenschätzung und den Deckel „nie größer“)
    def kbps(x):
        try:
            return int(x) / 1000
        except (TypeError, ValueError):
            return 0.0
    akb = kbps(a.get("bit_rate")) if a else 0.0
    vkb = kbps(v.get("bit_rate"))
    if not vkb and res["duration"]:
        total = kbps(fmt.get("bit_rate")) or (os.path.getsize(path) * 8 / 1000 / res["duration"])
        vkb = max(total - (akb or (128 if a else 0)), 0)
    res["src_vkbps"], res["src_akbps"] = vkb, akb
    return res


def audio_kbps(a, cap):
    """AAC-Bitrate: 128k, mit Deckel nie über der Quelle (mind. 64k)."""
    if not cap or not a["src_akbps"]:
        return 128
    return int(min(128, max(64, a["src_akbps"])))


def video_cap(a, cap):
    """Höchst-Bitrate fürs Video (kbit/s) oder 0 = ohne Deckel."""
    return int(a["src_vkbps"] * 0.95) if cap and a["src_vkbps"] >= 50 else 0


def est_new_size(job, q, cap, factor):
    """Geschätzte Größe der neuen Datei in Byte."""
    a = job["ana"]
    if a["ok"] or a["v_copy"]:
        return job["size"]
    pxs = max(a["w"] * a["h"], 1) * (a["fps"] or 25)
    v = QREF_KBPS.get(q, 310) * (pxs / QREF_PXS) ** 0.75 * (factor or 1.0)
    vc = video_cap(a, cap)
    if vc:
        v = min(v, vc)
    if not a["has_audio"]:
        au = 0
    else:
        au = (a["src_akbps"] or 128) if a["a_copy"] else audio_kbps(a, cap)
    return int((v + au) * 1000 / 8 * a["duration"] * 1.01) + 200_000


# ── Eigene Bedienelemente ────────────────────────────────────────────────────
class FlatButton(tk.Label):
    """Flacher Knopf: kind = primary (Akzent), outline, ghost; selected für Segment-Knöpfe."""

    def __init__(self, master, text, command=None, kind="ghost", font=FONT, padx=16, pady=7):
        super().__init__(master, text=text, font=font, padx=padx, pady=pady, cursor="hand2",
                         highlightthickness=1, bd=0)
        self.command, self.kind = command, kind
        self.enabled, self.selected, self.hover, self.pal = True, False, False, None
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonRelease-1>", self._click)

    def _hover(self, on):
        self.hover = on
        self.repaint()

    def _click(self, e):
        if self.enabled and self.command and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.command()

    def set_enabled(self, on):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self.repaint()

    def set_selected(self, on):
        self.selected = on
        self.repaint()

    def repaint(self, pal=None):
        p = self.pal = pal or self.pal
        if not p:
            return
        h = self.hover and self.enabled
        if not self.enabled:
            bg, fg, bd = p["surface2"], p["muted"], p["border"]
        elif self.kind == "primary" or self.selected:
            bg = p["accent_hover"] if h else p["accent"]
            fg, bd = "#ffffff", bg
        elif self.kind == "outline":
            bg, fg, bd = (p["hover"] if h else p["surface"]), p["accent"], p["accent"]
        else:
            bg, fg, bd = (p["hover"] if h else p["surface2"]), p["text"], p["border"]
        self.configure(bg=bg, fg=fg, highlightbackground=bd, highlightcolor=bd)


class ToggleSwitch(tk.Canvas):
    """Kippschalter wie in 2.01 – animiert, an eine BooleanVar gebunden."""

    def __init__(self, master, variable, command=None, w=40, h=22):
        super().__init__(master, width=w, height=h, highlightthickness=0, bd=0, cursor="hand2")
        self.var, self.command, self.w, self.h = variable, command, w, h
        self.pos = 1.0 if variable.get() else 0.0
        self.pal, self.enabled, self._anim = None, True, None
        self.bind("<Button-1>", lambda e: self.toggle())
        self._trace = variable.trace_add("write", lambda *a: self._animate())
        # beim Neuaufbau (Sprachwechsel) die Variable wieder freigeben, sonst meldet sich der alte Schalter
        self.bind("<Destroy>", lambda e: self._untrace() if e.widget is self else None)

    def _untrace(self):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass
        if self._anim:
            self.after_cancel(self._anim)

    def toggle(self):
        if self.enabled:
            self.var.set(not self.var.get())
            if self.command:
                self.command()

    def set_enabled(self, on):
        self.enabled = on
        self.configure(cursor="hand2" if on else "arrow")
        self.draw()

    def _animate(self):
        target = 1.0 if self.var.get() else 0.0
        if self._anim:
            self.after_cancel(self._anim)

        def step():
            d = target - self.pos
            self.pos = target if abs(d) < 0.08 else self.pos + d * 0.45
            self.draw()
            self._anim = self.after(14, step) if self.pos != target else None
        step()

    def repaint(self, pal):
        self.pal = pal
        self.configure(bg=self.master["bg"])
        self.draw()

    def draw(self):
        if not self.pal:
            return
        p, w, h = self.pal, self.w, self.h
        self.delete("all")
        col = mix(p["off"], p["accent"], self.pos)
        if not self.enabled:
            col = mix(col, self["bg"], 0.55)
        r = h / 2
        self.create_oval(0, 0, h, h, fill=col, outline=col)
        self.create_oval(w - h, 0, w, h, fill=col, outline=col)
        self.create_rectangle(r, 0, w - r, h, fill=col, outline=col)
        x = 3 + self.pos * (w - h)
        self.create_oval(x, 3, x + h - 6, h - 3, fill="#ffffff", outline="#ffffff")


class ProgressBar(tk.Canvas):
    def __init__(self, master, height=10, muted=False):
        super().__init__(master, height=height, highlightthickness=0, bd=0)
        self.value, self.pal, self.thin = 0.0, None, muted
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, v):
        self.value = max(0.0, min(1.0, v))
        self.draw()

    def repaint(self, pal):
        self.pal = pal
        self.configure(bg=self.master["bg"])
        self.draw()

    def draw(self):
        if not self.pal:
            return
        p, w, h = self.pal, self.winfo_width(), int(self["height"])
        self.delete("all")
        track = p["hover"]
        fill = p["muted"] if self.thin else p["accent"]
        self._round(0, w, h, track)
        if self.value > 0:
            self._round(0, max(h, w * self.value), h, fill)

    def _round(self, x0, x1, h, col):
        r = h / 2
        self.create_oval(x0, 0, x0 + h, h, fill=col, outline=col)
        self.create_oval(x1 - h, 0, x1, h, fill=col, outline=col)
        self.create_rectangle(x0 + r, 0, x1 - r, h, fill=col, outline=col)


# ── Hauptfenster ─────────────────────────────────────────────────────────────
class App:
    def __init__(self, root):
        global LANG
        self.root = root
        self.s = load_settings()
        LANG = self.s.get("lang") if self.s.get("lang") in ("de", "en") else "de"
        self.q = queue.Queue()
        self.themed = []                 # (widget, rolle)
        self.custom = []                 # Widgets mit eigener repaint(pal)
        self.jobs = []
        self.busy = False                # Analyse oder Umwandlung läuft
        self.stop_evt = threading.Event()
        self.proc = None
        self.ffmpeg_ok = False
        self.nvenc_ok = False

        root.title(f"{APP_NAME} {APP_VERSION}")
        root.minsize(1060, 820)
        root.geometry(self.s.get("geometry") or "1240x960")
        if os.path.exists(ICON_FILE):
            try:
                root.iconbitmap(ICON_FILE)
            except tk.TclError:
                pass

        self.v_skip = tk.BooleanVar(value=self.s["skip_done"])
        self.v_copy = tk.BooleanVar(value=self.s["copy_clean"])
        self.v_deep = tk.BooleanVar(value=self.s["deep_check"])
        self.v_cap = tk.BooleanVar(value=self.s["never_bigger"])
        self.v_date = tk.BooleanVar(value=self.s["set_date"])
        self.v_dark = tk.BooleanVar(value=self.s["theme"] == "dark")
        self.v_move = tk.BooleanVar(value=self.s["move_orig"] and bool(self.s["orig_dir"]))
        self.v_src = tk.StringVar(value=self.s["source"])
        self.v_src.trace_add("write", lambda *a: self.on_src_changed())
        self.t_start = None              # Startzeit des laufenden Vorgangs (für die Uhr)
        self.eta = None

        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self.build()
        self.apply_theme()
        self.set_mode(self.s["mode"])
        self.set_engine(self.s["engine"])
        self.set_quality(self.s["quality"])
        self.set_portion(self.s.get("portion", 0))
        self.fit_window()
        self.set_running(False)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(100, self.poll)
        root.after(1000, self.tick)
        threading.Thread(target=self.startup, daemon=True).start()

    def fit_window(self):
        """Mindestgröße = was der Inhalt wirklich braucht, damit nie etwas überlappt oder abgeschnitten wird."""
        self.root.update_idletasks()
        need_w = max(1060, self.root.winfo_reqwidth())
        need_h = min(self.root.winfo_reqheight() + 30, self.root.winfo_screenheight() - 110)   # Taskleiste
        self.root.minsize(need_w, need_h)
        if self.root.winfo_height() < need_h:
            self.root.geometry(f"{max(self.root.winfo_width(), need_w)}x{need_h}")
        if self.root.winfo_rooty() + need_h > self.root.winfo_screenheight() - 60:
            self.root.geometry(f"+{max(self.root.winfo_rootx() - 8, 0)}+10")    # nicht hinter die Taskleiste

    # ── Aufbau ───────────────────────────────────────────────────────────────
    def reg(self, w, role):
        self.themed.append((w, role))
        return w

    def card(self, parent, title, right=None):
        f = self.reg(tk.Frame(parent, highlightthickness=1, bd=0), "card")
        head = self.reg(tk.Frame(f), "surface")
        head.pack(fill="x", padx=18, pady=(14, 8))
        self.reg(tk.Label(head, text=T(title), font=FONT_CARD), "text").pack(side="left")
        body = self.reg(tk.Frame(f), "surface")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        f.head, f.body = head, body
        return f

    def section(self, parent, text):
        return self.reg(tk.Label(parent, text=T(text).upper(), font=FONT_SECTION, anchor="w"), "muted")

    def button(self, parent, text, cmd, kind="ghost", **kw):
        b = FlatButton(parent, T(text), cmd, kind, **kw)
        self.custom.append(b)
        return b

    def switch_row(self, parent, text, var, hint=""):
        row = self.reg(tk.Frame(parent), "surface")
        sw = ToggleSwitch(row, var, command=self.on_switch)
        self.custom.append(sw)
        sw.pack(side="left", padx=(0, 12))
        lab = self.reg(tk.Label(row, text=T(text), font=FONT, anchor="w", cursor="hand2"), "text")
        lab.pack(side="left")
        lab.bind("<Button-1>", lambda e: sw.toggle())
        if hint:
            self.reg(tk.Label(row, text=T(hint), font=FONT_SMALL), "muted").pack(side="left", padx=(8, 0))
        row.switch = sw
        return row

    def build(self):
        root = self.root
        # Statuszeile zuerst packen, damit sie bei kleinem Fenster nicht weggedrückt wird
        self.status = self.reg(tk.Frame(root, height=26), "status")
        self.status.pack(fill="x", side="bottom")
        self.lbl_ff = self.reg(tk.Label(self.status, text="⧗ ffmpeg …", font=FONT_SMALL), "status_lbl")
        self.lbl_ff.pack(side="left", padx=(12, 18), pady=3)
        self.lbl_gpu = self.reg(tk.Label(self.status, text=T("⧗ Grafikkarte …"), font=FONT_SMALL), "status_lbl")
        self.lbl_gpu.pack(side="left", pady=3)
        link = self.reg(tk.Label(self.status, text="github.com/brenner23/MP4-Reparatur", font=FONT_SMALL,
                                 cursor="hand2"), "status_link")
        link.pack(side="right", padx=(0, 12))
        link.bind("<Button-1>", lambda e: webbrowser.open(PROJECT_URL))
        self.reg(tk.Label(self.status, text=f"Brenner23 · (C){COPY_YEAR}  ·", font=FONT_SMALL),
                 "status_lbl").pack(side="right", padx=(12, 4))
        self.lbl_counts = self.reg(tk.Label(self.status, text="", font=FONT_B), "status_lbl")
        self.lbl_counts.pack(side="right", padx=18)

        outer = self.reg(tk.Frame(root), "bg")
        outer.pack(fill="both", expand=True, padx=20, pady=(16, 0))

        # Kopf
        head = self.reg(tk.Frame(outer), "bg")
        head.pack(fill="x", pady=(0, 14))
        self.logo = tk.Canvas(head, width=46, height=46, highlightthickness=0, bd=0)
        self.logo.pack(side="left")
        tbox = self.reg(tk.Frame(head), "bg")
        tbox.pack(side="left", padx=(12, 0))
        self.reg(tk.Label(tbox, text=APP_NAME, font=FONT_TITLE), "bg_text").pack(anchor="w")
        self.reg(tk.Label(tbox, text=T("Version {0}  ·  H.265 für Handy & Heimkino", APP_VERSION),
                          font=FONT_SMALL), "bg_muted").pack(anchor="w")
        tsw = self.reg(tk.Frame(head), "bg")
        tsw.pack(side="right")
        lang = self.reg(tk.Frame(head), "bg")             # Sprache DE | EN
        lang.pack(side="right", padx=(0, 22))
        self.lang_btns = {}
        for code in ("de", "en"):
            bt = FlatButton(lang, code.upper(), lambda c=code: self.set_lang(c), padx=10, pady=4)
            self.custom.append(bt)
            bt.pack(side="left")
            bt.set_selected(code == LANG)
            self.lang_btns[code] = bt
        self.reg(tk.Label(tsw, text="☀", font=("Segoe UI Symbol", 12)), "bg_muted").pack(side="left", padx=6)
        self.theme_sw = ToggleSwitch(tsw, self.v_dark, command=self.apply_theme, w=46, h=26)
        self.custom.append(self.theme_sw)
        self.theme_sw.pack(side="left")
        self.reg(tk.Label(tsw, text="☾", font=("Segoe UI Symbol", 12)), "bg_muted").pack(side="left", padx=6)

        body = self.reg(tk.Frame(outer), "bg")
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3, uniform="c")
        body.columnconfigure(1, weight=2, uniform="c")
        body.rowconfigure(1, weight=1)

        # Quelle & Ziel
        c = self.card(body, "Quelle & Ziel")
        c.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 14))
        b = c.body
        self.lbl_src_head = self.section(b, "Quellordner")
        self.lbl_src_head.pack(fill="x")
        r = self.reg(tk.Frame(b), "surface")
        r.pack(fill="x", pady=(4, 10))
        self.src_entry = self.reg(tk.Entry(r, textvariable=self.v_src, font=FONT, relief="flat",
                                           highlightthickness=1), "entry")
        self.src_entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(0, 8))
        self.btn_browse = self.button(r, "Durchsuchen", self.browse)
        self.btn_browse.pack(side="left", padx=(0, 6))
        self.button(r, "Öffnen", lambda: self.open_dir(self.v_src.get())).pack(side="left")

        r = self.reg(tk.Frame(b), "surface")
        r.pack(fill="x")
        self.section(r, "Ziel").pack(side="left")
        seg = self.reg(tk.Frame(b), "surface")
        seg.pack(fill="x", pady=(4, 6))
        self.btn_m_fertig = self.button(seg, "In Fertig-Ordner", lambda: self.set_mode("fertig"), padx=14)
        self.btn_m_repl = self.button(seg, "Original ersetzen", lambda: self.set_mode("replace"), padx=14)
        self.btn_m_fertig.pack(side="left")
        self.btn_m_repl.pack(side="left")
        r = self.reg(tk.Frame(b), "surface")
        r.pack(fill="x", pady=(0, 10))
        self.lbl_target = self.reg(tk.Label(r, text="", font=FONT_SMALL, anchor="w", justify="left"), "text")
        self.lbl_target.pack(side="left", fill="x", expand=True)
        self.btn_t_open = self.button(r, "Öffnen", lambda: self.open_dir(self.fertig_dir()), pady=4)
        self.btn_t_open.pack(side="right")
        self.btn_t_change = self.button(r, "Ändern", self.choose_fertig, pady=4)
        self.btn_t_change.pack(side="right", padx=(0, 6))

        r = self.reg(tk.Frame(b), "surface")
        r.pack(fill="x", pady=(0, 2))
        self.move_sw = ToggleSwitch(r, self.v_move, command=self.on_move_switch)
        self.custom.append(self.move_sw)
        self.move_sw.pack(side="left", padx=(0, 12))
        lab = self.reg(tk.Label(r, text=T("Originale verschieben nach …"), font=FONT, cursor="hand2"), "text")
        lab.pack(side="left")
        lab.bind("<Button-1>", lambda e: self.move_sw.toggle())
        self.btn_o_open = self.button(r, "Öffnen", lambda: self.open_dir(self.orig_dir()), pady=4)
        self.btn_o_open.pack(side="right")
        self.btn_o_change = self.button(r, "Ändern", self.choose_orig, pady=4)
        self.btn_o_change.pack(side="right", padx=(0, 6))
        self.lbl_orig = self.reg(tk.Label(b, text="", font=FONT_SMALL, anchor="w", justify="left",
                                          wraplength=560), "muted")
        self.lbl_orig.pack(fill="x", padx=(52, 0), pady=(0, 12))
        r = self.reg(tk.Frame(b), "surface")
        r.pack(fill="x")
        self.btn_analyze = self.button(r, "Ordner analysieren", self.start_analyze, "outline", font=FONT_B)
        self.btn_analyze.pack(side="left")
        self.lbl_summary = self.reg(tk.Label(r, text="", font=FONT_SMALL, anchor="w"), "muted")
        self.lbl_summary.pack(side="left", padx=14)

        # Einstellungen
        c = self.card(body, "Einstellungen")
        c.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 14))
        b = c.body
        grid = self.reg(tk.Frame(b), "surface")
        grid.pack(fill="x")
        col1 = self.reg(tk.Frame(grid), "surface")
        col1.pack(side="left", anchor="n")
        self.section(col1, "Umwandeln mit").pack(anchor="w")
        seg = self.reg(tk.Frame(col1), "surface")
        seg.pack(anchor="w", pady=(4, 0))
        self.btn_gpu = self.button(seg, "GPU", lambda: self.set_engine("gpu"), padx=18)
        self.btn_cpu = self.button(seg, "CPU", lambda: self.set_engine("cpu"), padx=18)
        self.btn_gpu.pack(side="left")
        self.btn_cpu.pack(side="left")
        col2 = self.reg(tk.Frame(grid), "surface")
        col2.pack(side="left", anchor="n", padx=(26, 0))
        self.section(col2, "Qualität").pack(anchor="w")
        seg = self.reg(tk.Frame(col2), "surface")
        seg.pack(anchor="w", pady=(4, 0))
        self.q_btns = {}
        for name, val in QUALITIES:
            bt = self.button(seg, name, lambda v=val: self.set_quality(v), padx=12)
            bt.pack(side="left")
            self.q_btns[val] = bt
        self.lbl_engine = self.reg(tk.Label(b, text=T("Prüfe Grafikkarte …"), font=FONT_SMALL, anchor="w",
                                            justify="left"), "muted")
        self.lbl_engine.pack(fill="x", pady=(8, 8))
        self.section(b, "Portion – nicht alles auf einmal").pack(anchor="w")
        seg = self.reg(tk.Frame(b), "surface")
        seg.pack(anchor="w", pady=(4, 10))
        self.p_btns = {}
        for name, val in PORTIONS:
            bt = self.button(seg, name, lambda v=val: self.set_portion(v), padx=12)
            bt.pack(side="left")
            self.p_btns[val] = bt
        self.switches = []
        for text, var, hint in (
                ("Fertige überspringen (Fortsetzen)", self.v_skip, ""),
                ("Saubere Dateien unverändert mitkopieren", self.v_copy, ""),
                ("Nie größer als das Original", self.v_cap, "Bild bleibt wie Original"),
                ("Gründlich prüfen (komplett durchspielen)", self.v_deep, "langsamer"),
                ("Original-Datum übernehmen", self.v_date, "")):
            row = self.switch_row(b, text, var, hint)
            row.pack(fill="x", pady=3)
            self.switches.append(row.switch)

        # Dateiliste
        c = self.card(body, "Dateien")
        c.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(0, 14))
        self.lbl_count = self.reg(tk.Label(c.head, text="", font=FONT_SMALL), "muted")
        self.lbl_count.pack(side="right")
        self.space_box = self.reg(tk.Frame(c.body), "surface")
        self.space_box.pack(fill="x", pady=(0, 8))
        self.lbl_space = self.reg(tk.Label(self.space_box, text="", font=FONT_SMALL, anchor="w", justify="left"),
                                  "text")
        self.lbl_space.pack(fill="x")
        tf = self.reg(tk.Frame(c.body), "surface")
        tf.pack(fill="both", expand=True)
        cols = ("datei", "format", "aufl", "fps", "befund", "status")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", style="R.Treeview", selectmode="browse",
                                 height=6)                # wächst mit dem Fenster mit
        for cid, text, w, anchor, stretch in (
                ("datei", "Datei", 230, "w", True), ("format", "Format", 100, "w", False),
                ("aufl", "Auflösung", 72, "center", False), ("fps", "fps", 36, "center", False),
                ("befund", "Befund", 170, "w", True), ("status", "Status", 105, "w", False)):
            self.tree.heading(cid, text=T(text), anchor=anchor)
            self.tree.column(cid, width=w, anchor=anchor, stretch=stretch, minwidth=40)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview, style="R.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # Fortschritt
        c = self.card(body, "Reparatur")
        c.grid(row=1, column=1, sticky="nsew", padx=(8, 0), pady=(0, 14))
        b = c.body
        self.lbl_file = self.reg(tk.Label(b, text=T("Bereit"), font=FONT_B, anchor="w"), "text")
        self.lbl_file.pack(fill="x")
        self.pb_file = ProgressBar(b, 6)
        self.custom.append(self.pb_file)
        self.pb_file.pack(fill="x", pady=(8, 5))
        self.lbl_detail = self.reg(tk.Label(b, text="", font=FONT_SMALL, anchor="w"), "muted")
        self.lbl_detail.pack(fill="x")
        self.pb_total = ProgressBar(b, 3, muted=True)
        self.custom.append(self.pb_total)
        self.pb_total.pack(fill="x", pady=(10, 5))
        self.lbl_total = self.reg(tk.Label(b, text="", font=FONT_SMALL, anchor="w"), "muted")
        self.lbl_total.pack(fill="x")
        self.lbl_times = self.reg(tk.Label(b, text="", font=FONT_B, anchor="w", justify="left", wraplength=420),
                                  "text")
        self.lbl_times.pack(fill="x", pady=(6, 0))
        spacer = self.reg(tk.Frame(b), "surface")
        spacer.pack(fill="both", expand=True)
        r = self.reg(tk.Frame(b), "surface")
        r.pack(fill="x", side="bottom", before=self.lbl_file)    # Knöpfe zuerst → werden nie abgeschnitten
        self.btn_start = self.button(r, "Reparatur starten", self.start_convert, "primary",
                                     font=("Segoe UI Semibold", 12), pady=11)
        self.btn_start.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self.btn_stop = self.button(r, "Abbrechen", self.stop, pady=11)
        self.btn_stop.pack(side="left")

        # Protokoll
        c = self.card(outer, "Protokoll")
        c.pack(fill="x", pady=(0, 12), side="bottom", before=body)
        self.btn_log = self.reg(tk.Label(c.head, text=T("ausblenden ▴"), font=FONT_SMALL, cursor="hand2"), "muted")
        self.btn_log.pack(side="right")
        self.btn_log.bind("<Button-1>", lambda e: self.toggle_log())
        self.log_frame = self.reg(tk.Frame(c.body, highlightthickness=1), "logframe")
        self.log_frame.pack(fill="x")
        self.log_text = self.reg(tk.Text(self.log_frame, height=5, font=FONT_MONO, relief="flat", wrap="word",
                                         padx=12, pady=8, state="disabled"), "log")
        self.log_text.pack(fill="x")

    # ── Aussehen ─────────────────────────────────────────────────────────────
    def apply_theme(self):
        theme = "dark" if self.v_dark.get() else "light"
        self.s["theme"] = theme
        p = self.pal = PALETTES[theme]
        for w, role in self.themed:
            if role == "bg":
                w.configure(bg=p["bg"])
            elif role == "bg_text":
                w.configure(bg=p["bg"], fg=p["text"])
            elif role == "bg_muted":
                w.configure(bg=p["bg"], fg=p["muted"])
            elif role == "card":
                w.configure(bg=p["surface"], highlightbackground=p["border"], highlightcolor=p["border"])
            elif role == "surface":
                w.configure(bg=p["surface"])
            elif role == "text":
                w.configure(bg=p["surface"], fg=p["text"])
            elif role == "muted":
                w.configure(bg=p["surface"], fg=p["muted"])
            elif role == "entry":
                w.configure(bg=p["input"], fg=p["text"], insertbackground=p["text"],
                            disabledbackground=p["surface2"], disabledforeground=p["muted"],
                            highlightbackground=p["border_strong"], highlightcolor=p["accent"],
                            selectbackground=p["accent"], selectforeground="#ffffff")
            elif role == "logframe":
                w.configure(bg=p["logbg"], highlightbackground=p["border"], highlightcolor=p["border"])
            elif role == "log":
                w.configure(bg=p["logbg"], fg=p["text"], insertbackground=p["text"],
                            selectbackground=p["accent"])
            elif role == "status":
                w.configure(bg=p["surface"])
            elif role == "status_lbl":
                w.configure(bg=p["surface"], fg=p["muted"])
            elif role == "status_link":
                w.configure(bg=p["surface"], fg=p["accent"])
        self.root.configure(bg=p["bg"])
        for w in self.custom:
            w.repaint(p)
        self.draw_logo()
        st = self.style
        st.configure("R.Treeview", background=p["input"], fieldbackground=p["input"], foreground=p["text"],
                     bordercolor=p["border"], borderwidth=0, rowheight=26, font=FONT_SMALL)
        st.map("R.Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["text"])])
        st.configure("R.Treeview.Heading", background=p["surface2"], foreground=p["muted"], relief="flat",
                     bordercolor=p["border"], font=FONT_SECTION, padding=(6, 5))
        st.map("R.Treeview.Heading", background=[("active", p["hover"])])
        st.layout("R.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("R.Vertical.TScrollbar", background=p["surface2"], troughcolor=p["input"],
                     bordercolor=p["input"], arrowcolor=p["muted"], lightcolor=p["surface2"],
                     darkcolor=p["surface2"], gripcount=0)
        st.map("R.Vertical.TScrollbar", background=[("active", p["hover"])])
        for tag, col in (("ok", p["ok"]), ("prob", p["warn"]), ("done", p["ok"]), ("err", p["accent"]),
                         ("run", p["text"]), ("skip", p["muted"])):
            self.tree.tag_configure(tag, foreground=col)
        self.tree.tag_configure("run", background=p["sel"])
        self.log_text.tag_configure("ok", foreground=p["ok"])
        self.log_text.tag_configure("warn", foreground=p["warn"])
        self.log_text.tag_configure("err", foreground=p["accent"])
        self.log_text.tag_configure("muted", foreground=p["muted"])
        self.save()

    def draw_logo(self):
        p, c = self.pal, self.logo
        c.configure(bg=p["bg"])
        c.delete("all")
        a = p["accent"]
        for x0, y0, x1, y1 in ((0, 0, 20, 20), (26, 0, 46, 20), (0, 26, 20, 46), (26, 26, 46, 46)):
            c.create_oval(x0, y0, x1, y1, fill=a, outline=a)
        c.create_rectangle(10, 0, 36, 46, fill=a, outline=a)
        c.create_rectangle(0, 10, 46, 36, fill=a, outline=a)
        c.create_polygon(17, 12, 17, 34, 35, 23, fill="#ffffff", outline="#ffffff")
        c.create_text(39, 39, text="✓", fill="#ffffff", font=("Segoe UI Symbol", 9, "bold"))

    def set_engine(self, eng):
        if eng == "gpu" and not self.nvenc_ok and self.ffmpeg_ok:
            eng = "cpu"
        self.s["engine"] = eng
        self.btn_gpu.set_selected(eng == "gpu")
        self.btn_cpu.set_selected(eng == "cpu")
        self.update_engine_label()
        self.show_estimate()
        self.save()

    def update_engine_label(self, hint=None):
        if hint is not None:
            self._engine_hint = hint                  # Liste [Text, Werte…] → erst hier übersetzen
        h = T(*getattr(self, "_engine_hint", None) or ["Prüfe Grafikkarte …"])
        if self.s["engine"] == "gpu":
            txt = T("NVIDIA NVENC · schnell, H.265 Main 8-Bit") if self.nvenc_ok else h
        else:
            txt = T("x265 auf dem Prozessor · langsamer, etwas kleinere Dateien")
            if getattr(self, "_engine_hint", None) and not self.nvenc_ok and self.ffmpeg_ok:
                txt += "\n" + T("GPU nicht nutzbar: {0}", h)
        self.lbl_engine.configure(text=txt)

    def set_quality(self, val):
        self.s["quality"] = val
        for v, b in self.q_btns.items():
            b.set_selected(v == val)
        self.show_estimate()
        self.save()

    def toggle_log(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.pack_forget()
            self.btn_log.configure(text=T("einblenden ▾"))
        else:
            self.log_frame.pack(fill="x")
            self.btn_log.configure(text=T("ausblenden ▴"))

    # ── Allgemeines ──────────────────────────────────────────────────────────
    def save(self):
        self.s.update(skip_done=self.v_skip.get(), copy_clean=self.v_copy.get(), deep_check=self.v_deep.get(),
                      set_date=self.v_date.get(), source=self.v_src.get().strip(), move_orig=self.v_move.get(),
                      never_bigger=self.v_cap.get())
        save_settings(self.s)

    def log(self, text, tag=None):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text + "\n", tag or ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def src_path(self):
        src = self.v_src.get().strip()
        return Path(src) if src else None

    def beside(self, suffix):
        """<Quelle><suffix> neben der Quelle – bei einem Laufwerk (Z:\\) als Unterordner darin."""
        p = self.src_path()
        if not p:
            return ""
        if p.parent == p:                             # Laufwerkswurzel
            return str(p / suffix)
        return str(p.parent / (p.name + suffix))

    def fertig_dir(self):
        return self.s.get("fertig_dir") or self.beside(FERTIG_SUFFIX)

    def orig_dir(self):
        """Wohin Originale wandern ('' = sie bleiben liegen)."""
        if self.v_move.get() and self.s.get("orig_dir"):
            return self.s["orig_dir"]
        return self.beside(ORIGINAL_SUFFIX) if self.s["mode"] == "replace" else ""

    def on_src_changed(self):
        self.s["fertig_dir"] = ""                     # neuer Quellordner → Ziel wieder automatisch
        if self.jobs and not self.busy:               # Liste gehört zum alten Ordner
            self.jobs = []
            self.tree.delete(*self.tree.get_children())
            self.lbl_summary.configure(text="")
            self.lbl_count.configure(text="")
            self.btn_analyze.configure(text=T("Ordner analysieren"))
            self.set_running(False)
            self.show_estimate()
        self.update_target()

    def set_mode(self, mode, keep_jobs=False):
        changed = mode != self.s["mode"]
        self.s["mode"] = mode
        self.btn_m_fertig.set_selected(mode == "fertig")
        self.btn_m_repl.set_selected(mode == "replace")
        self.update_target()
        if changed and not keep_jobs:
            self.reclassify()                         # Analyse bleibt, nur Ziel/Status neu
        self.save()

    def update_target(self):
        self.lbl_src_head.configure(text=T("Quellordner (wird nur gelesen)").upper() if self.s["mode"] == "fertig"
                                    else T("Ordner / Laufwerk mit den Videos (z. B. Z:\\)").upper())
        if self.s["mode"] == "fertig":
            auto = "" if self.s.get("fertig_dir") else "  " + T("(automatisch)")
            self.lbl_target.configure(text=f"→  {short_path(self.fertig_dir(), 64) or '–'}{auto}")
            self.btn_t_open.pack(side="right")
            self.btn_t_change.pack(side="right", padx=(0, 6))
        else:
            self.lbl_target.configure(text=T("Neue Datei ersetzt das Original an Ort und Stelle –\n"
                                             "erst nachdem sie fertig umgewandelt und geprüft ist."))
            self.btn_t_change.pack_forget()
            self.btn_t_open.pack_forget()
        o = self.orig_dir()
        if self.v_move.get():
            txt = f"→  {short_path(o)}\n" + T("Ordnerstruktur wird in voller Tiefe nachgebaut, je Datei nach getaner Arbeit.")
        elif self.s["mode"] == "replace":
            txt = T("aus: Originale werden aufgehoben in  {0}", short_path(o, 64))
        else:
            txt = T("aus: Originale bleiben, wo sie sind.")
        self.lbl_orig.configure(text=txt)
        for btn in (self.btn_o_change, self.btn_o_open):
            btn.set_enabled(self.v_move.get() and not self.busy)
        self.show_space()

    def on_move_switch(self):
        if self.v_move.get() and not self.choose_orig():
            self.v_move.set(False)                    # Dialog abgebrochen → Schalter zurück
        self.update_target()
        self.save()

    def choose_orig(self):
        d = filedialog.askdirectory(initialdir=self.s.get("orig_dir") or None, parent=self.root,
                                    title=T("Wohin sollen die Originale verschoben werden?"))
        if not d:
            return False
        d = os.path.normpath(d)
        bad = self.check_dir_conflict(d)
        if bad:
            messagebox.showwarning(APP_NAME, bad, parent=self.root)
            return False
        self.s["orig_dir"] = d
        self.update_target()
        self.save()
        return True

    def choose_fertig(self):
        d = filedialog.askdirectory(initialdir=self.fertig_dir() or None, parent=self.root,
                                    title=T("Wohin sollen die fertigen Dateien?"))
        if d:
            d = os.path.normpath(d)
            bad = self.check_dir_conflict(d)
            if bad:
                messagebox.showwarning(APP_NAME, bad, parent=self.root)
                return
            self.s["fertig_dir"] = d
            self.update_target()
            self.save()
            self.reclassify()                         # neues Ziel → „schon fertig?“ neu prüfen

    def check_dir_conflict(self, d):
        src = self.src_path()
        if src and os.path.normcase(os.path.abspath(d)) == os.path.normcase(os.path.abspath(src)):
            return T("Das ist der Quellordner selbst – bitte einen anderen Ordner wählen.")
        return ""

    def target_dir(self):
        return self.fertig_dir()

    def browse(self):
        d = filedialog.askdirectory(initialdir=self.v_src.get() or None, title=T("Quellordner mit Videos wählen"))
        if d:
            self.v_src.set(os.path.normpath(d))
            self.save()
            self.start_analyze()                      # aktiv gewählt → sofort analysieren (beim Start nicht)

    def open_dir(self, d):
        if d and os.path.isdir(d):
            os.startfile(d)
        elif d:
            messagebox.showinfo(APP_NAME, T("Den Ordner gibt es (noch) nicht:\n{0}", d), parent=self.root)

    def set_running(self, on):
        self.busy = on
        ready = self.ffmpeg_ok and not on
        self.btn_start.set_enabled(ready and any(j["todo"] for j in self.jobs))
        self.btn_analyze.set_enabled(ready)
        self.btn_browse.set_enabled(not on)
        self.btn_stop.set_enabled(on)
        self.btn_gpu.set_enabled(not on and self.nvenc_ok)
        self.btn_cpu.set_enabled(not on)
        for b in list(self.q_btns.values()) + list(self.p_btns.values()) + [self.btn_m_fertig, self.btn_m_repl, self.btn_t_change]:
            b.set_enabled(not on)
        self.move_sw.set_enabled(not on)
        for btn in (self.btn_o_change, self.btn_o_open):
            btn.set_enabled(self.v_move.get() and not on)
        self.src_entry.configure(state="disabled" if on else "normal")
        for sw in self.switches:
            sw.set_enabled(not on)

    def poll(self):
        try:
            while True:
                kind, *args = self.q.get_nowait()
                getattr(self, "on_" + kind)(*args)
        except queue.Empty:
            pass
        self.root.after(80, self.poll)

    def post(self, kind, *args):
        self.q.put((kind, *args))

    # ── Start: Werkzeuge prüfen ──────────────────────────────────────────────
    def startup(self):
        print(f"[{APP_NAME}] Version {APP_VERSION} · Werkzeuge: {TOOLS_DIR}")
        os.makedirs(CONFIG_DIR, exist_ok=True)
        clean_work_dirs(own=False)
        local = ffmpeg_version()
        remote = ffmpeg_remote_version()
        if not local or not os.path.exists(tool("ffprobe.exe")):
            if not remote:
                self.post("tools", None, T("ffmpeg fehlt · keine Internetverbindung"), "err")
                return
            self.post("log", T("ffmpeg wird geladen ({0}) …", remote), "muted")
            try:
                install_ffmpeg(remote, lambda pc: self.post("tools", None, T("lade ffmpeg {0} … {1} %", remote, pc), "busy"))
            except Exception as e:
                self.post("tools", None, T("ffmpeg-Download fehlgeschlagen"), "err")
                self.post("log", T("ffmpeg-Download fehlgeschlagen: {0}", e), "err")
                return
            local = ffmpeg_version()
            self.post("tools", local, T("ffmpeg {0} · neu installiert", local), "ok")
        elif remote and ver_tuple(remote) > ver_tuple(local):
            self.post("tools", local, T("ffmpeg {0} · {1} verfügbar", local, remote), "warn")
            self.post("ffmpeg_update", local, remote)
        else:
            self.post("tools", local, f"ffmpeg {local}" + ("" if remote else T(" · Update-Prüfung offline")), "ok")
        ok, hint = detect_nvenc()
        self.post("nvenc", ok, hint)

    def on_tools(self, version, text, state):
        self.ff_status = (text, state)                # für den Neuaufbau beim Sprachwechsel
        icon = {"ok": "✓", "warn": "●", "err": "✖", "busy": "⧗"}[state]
        col = {"ok": self.pal["ok"], "warn": self.pal["warn"], "err": self.pal["accent"], "busy": self.pal["muted"]}[state]
        self.lbl_ff.configure(text=f"{icon} {text}", fg=col if state != "ok" else self.pal["muted"])
        if version:
            first = not self.ffmpeg_ok
            self.ffmpeg_ok = True
            self.set_running(self.busy)
            # bewusst KEINE automatische Analyse: bei ganzen Laufwerken/NAS würde das ungefragt lange laufen
            if first and not self.jobs and not self.busy:
                self.lbl_file.configure(text=T("Bereit – „Ordner analysieren“ klicken"))
        elif state == "err":
            self.log(T("✖ {0} – ohne ffmpeg kann nichts umgewandelt werden.", text), "err")

    def on_ffmpeg_update(self, local, remote):
        if messagebox.askyesno(APP_NAME, T("Für ffmpeg gibt es eine neue Version.\n\nInstalliert:  {0}\n"
                                           "Neu:  {1}\n\nJetzt herunterladen? (ca. 100 MB, im Hintergrund)",
                                           local, remote), parent=self.root):
            if self.busy:
                messagebox.showinfo(APP_NAME, T("Bitte erst nach der Reparatur aktualisieren."), parent=self.root)
                return

            def job():
                try:
                    install_ffmpeg(remote, lambda pc: self.post("tools", None, T("lade ffmpeg {0} … {1} %", remote, pc), "busy"))
                    v = ffmpeg_version()
                    self.post("tools", v, T("ffmpeg {0} · aktualisiert", v), "ok")
                    self.post("log", T("ffmpeg wurde auf {0} aktualisiert.", v), "ok")
                except Exception as e:
                    self.post("tools", local, T("ffmpeg {0} · Update fehlgeschlagen", local), "warn")
                    self.post("log", T("ffmpeg-Update fehlgeschlagen: {0}", e), "err")
            threading.Thread(target=job, daemon=True).start()

    def on_nvenc(self, ok, hint):
        self.nvenc_ok = ok
        self.nvenc_hint = hint
        self.lbl_gpu.configure(text=(T("✓ GPU: NVIDIA NVENC (H.265)") if ok else T("● GPU: {0} → CPU", T(*hint))))
        if not ok and self.s["engine"] == "gpu":
            self.set_engine("cpu")
        self.update_engine_label(hint)
        self.set_running(self.busy)

    def on_log(self, text, tag=None):
        self.log(text, tag)

    # ── Analyse ──────────────────────────────────────────────────────────────
    def start_analyze(self):
        src = self.v_src.get().strip()
        if self.busy or not self.ffmpeg_ok:
            return
        if not os.path.isdir(src):
            messagebox.showwarning(APP_NAME, T("Quellordner nicht gefunden:\n{0}", src), parent=self.root)
            return
        if Path(src).name.endswith((FERTIG_SUFFIX, ORIGINAL_SUFFIX)):
            messagebox.showwarning(APP_NAME, T("Das ist ein Fertig- bzw. Original-Ordner – bitte den Ordner "
                                               "mit den zu reparierenden Videos wählen."), parent=self.root)
            return
        self.save()
        self.tree.delete(*self.tree.get_children())
        self.jobs = []
        self.hidden = 0
        self.lbl_summary.configure(text=T("suche Videos …"))
        self.log(T("=== Analyse: {0} ===", src))
        excl = [d for d in (self.fertig_dir() if self.s["mode"] == "fertig" else "", self.orig_dir()) if d]
        self.stop_evt.clear()
        self.set_running(True)
        self.t_start, self.eta = time.time(), None
        self.pb_file.set(0)
        self.pb_total.set(0)
        threading.Thread(target=self.analyze_worker, args=(src, self.s["mode"], self.fertig_dir(), excl),
                         daemon=True).start()

    def analyze_worker(self, src, mode, dst, excl):
        excl = [os.path.normcase(os.path.abspath(d)) + os.sep for d in excl]
        files = []
        for root, dirs, names in os.walk(src):
            dirs[:] = sorted(d for d in dirs if d.lower() not in SKIP_DIRS
                             and not d.endswith((FERTIG_SUFFIX, ORIGINAL_SUFFIX))
                             and not any((os.path.normcase(os.path.join(root, d)) + os.sep).startswith(e)
                                         for e in excl))
            files += [Path(root, n) for n in names
                      if Path(n).suffix.lower() in VIDEO_EXT and not n.endswith(".part.mp4")]
            if self.stop_evt.is_set():
                break
        files.sort(key=lambda p: str(p).lower())
        self.post("analyze_count", len(files))
        cache = load_cache()
        hits = 0
        for i, p in enumerate(files, 1):
            if self.stop_evt.is_set():
                break
            rel = p.relative_to(src)
            out = Path(dst) / rel.with_suffix(".mp4") if mode == "fertig" else p.with_suffix(".mp4")
            try:
                st = p.stat()
                size, mtime = st.st_size, int(st.st_mtime)
            except OSError:
                size = mtime = 0
            key = os.path.normcase(str(p))
            c = cache.get(key)
            if c and c[0] == size and c[1] == mtime and c[2].get("v") == CACHE_VERSION:
                ana = c[2]                            # unverändert seit letzter Analyse → nicht neu lesen
                hits += 1
            else:
                ana = analyze(p)
                ana["v"] = CACHE_VERSION
                if not ana["error"]:
                    cache[key] = [size, mtime, ana]
            job = dict(src=p, rel=rel, out=out, size=size, ana=ana, state="", todo=True, mode=mode)
            self.post("analyze_row", job, i, len(files))
        save_cache(cache)
        if hits:
            self.post("log", T("{0} Dateien aus dem Analyse-Gedächtnis (unverändert, nicht neu gelesen).", hits), "muted")
        self.post("analyze_done")

    def on_analyze_count(self, n):
        self.lbl_count.configure(text=T("0 von {0} gelesen", n))
        self.lbl_file.configure(text=T("Analyse: {0} Videos gefunden", n))
        if not n:
            self.log(T("Keine Videodateien gefunden."), "warn")

    def row_values(self, job):
        """Zeile der Dateiliste – in der aktuellen Sprache (Status/Befund werden erst hier übersetzt)."""
        a = job["ana"]
        if a["error"]:
            fmt, befund = "?", T(a["error"])
        else:
            fmt = f"{a['container']} · {a['vcodec'].upper()}"
            befund = issues_text(a["issues"]) if a["issues"] else T("sauber")
            if job.get("name_taken"):
                befund += T(" · {0} gibt es schon", job["out"].name)
        return (str(job["rel"]), fmt, f"{a['w']}×{a['h']}" if a["w"] else "",
                f"{a['fps']:.0f}" if a["fps"] else "", befund, T(job["state"]))

    def classify(self, job):
        """Ziel, Status und „zu tun?“ einer Datei nach den aktuellen Einstellungen (Ziel-Art, Schalter).
        Die Analyse selbst hängt nicht davon ab – beim Umschalten wird nur das hier neu berechnet."""
        a, mode = job["ana"], self.s["mode"]
        job.update(mode=mode, todo=True, name_taken=False)
        job["out"] = (Path(self.fertig_dir()) / job["rel"].with_suffix(".mp4") if mode == "fertig"
                      else job["src"].with_suffix(".mp4"))
        if a["error"]:
            job.update(state="Fehler", todo=False, tag="err")
            return
        tag = "ok" if a["ok"] else "prob"
        action = "H.265 umwandeln" if not a["v_copy"] else "neu verpacken"
        if mode == "replace":
            if a["ok"]:
                job.update(state="schon gut", todo=False)
            elif job["out"] != job["src"] and job["out"].exists():
                job.update(state="Name belegt", todo=False, name_taken=True)
                tag = "err"
            else:
                job["state"] = action
        elif self.v_skip.get() and job["out"].exists():
            job.update(state="schon fertig", todo=False)
            tag = "skip"
        elif a["ok"]:
            job.update(state="kopieren" if self.v_copy.get() else "auslassen", todo=self.v_copy.get())
        else:
            job["state"] = action
        job["tag"] = tag

    def reclassify(self):
        """Nach Wechsel von Ziel/Fertig-Ordner/Schaltern: Liste behalten, nur Status neu – keine neue Analyse."""
        if not self.jobs or self.busy:
            return
        for j in self.jobs:
            if j.get("hidden") or j.get("tag") == "done":   # schon erledigt → bleibt erledigt
                continue
            self.classify(j)
            if "iid" in j and self.tree.exists(j["iid"]):
                self.tree.item(j["iid"], values=self.row_values(j), tags=(j["tag"],))
        self.summary_texts()
        self.show_hidden_count()
        self.lbl_file.configure(text=T("Bereit") if any(j["todo"] for j in self.jobs) else T("Nichts zu tun"))
        self.set_running(False)
        self.show_estimate()

    def on_analyze_row(self, job, i, n):
        self.jobs.append(job)
        self.classify(job)
        job["iid"] = self.tree.insert("", "end", values=self.row_values(job), tags=(job["tag"],))
        el = time.time() - self.t_start
        self.eta = time.time() + el / i * (n - i)
        self.pb_total.set(i / n)
        self.pb_file.set(i / n)
        self.lbl_count.configure(text=T("{0} von {1} gelesen", i, n))
        self.lbl_summary.configure(text=T("analysiere … {0}/{1}", i, n))
        self.lbl_detail.configure(text=T("{0} von {1} Dateien gelesen", i, n))
        self.lbl_total.configure(text=T("Analyse {0} %", f"{i / n * 100:.0f}"))

    def summary_texts(self):
        n = len(self.jobs)
        prob = sum(1 for j in self.jobs if not j["ana"]["ok"] and not j["ana"]["error"])
        todo = [j for j in self.jobs if j["todo"]]
        dur = sum(j["ana"]["duration"] for j in todo)
        size = sum(j["size"] for j in self.jobs)
        self.lbl_count.configure(text=T("{0} Dateien · {1}", n, fmt_size(size)))
        self.lbl_summary.configure(text=T("{0} mit Problemen · {1} zu erledigen · Spielzeit {2}",
                                          prob, len(todo), fmt_time(dur)))
        self.btn_analyze.configure(text=T("↻ Neu analysieren"))   # Liste da → Knopf lädt denselben Ordner neu
        return n, prob, todo

    def on_analyze_done(self):
        self.set_running(False)
        self.t_start = self.eta = None
        n, prob, todo = self.summary_texts()
        counts = {}
        for j in self.jobs:
            for iss in j["ana"]["issues"]:
                key = issue_text(iss[:2] + [".mp4"] if iss[0] == "disguised" else iss)
                counts[key] = counts.get(key, 0) + 1
        self.log(T("{0} Dateien gelesen, {1} mit Problemen, {2} zu erledigen.", n, prob, len(todo)))
        for k, c in sorted(counts.items(), key=lambda x: -x[1]):
            self.log(f"   {c:4d}×  {k}", "muted")
        self.lbl_file.configure(text=T("Bereit") if todo else T("Nichts zu tun"))
        self.pb_file.set(0)
        self.pb_total.set(0)
        self.lbl_detail.configure(text="")
        self.show_estimate()
        self.fit_window()                             # Platzanzeige kann mehrere Zeilen bekommen
        if todo:
            self.log(self.lbl_times["text"], "muted")

    # ── Zeitschätzung ────────────────────────────────────────────────────────
    def engine_now(self):
        return "gpu" if self.s["engine"] == "gpu" and self.nvenc_ok else "cpu"

    def job_cost(self, job, engine):
        """Geschätzte Sekunden für eine Datei (Umwandeln + Lesen/Schreiben)."""
        a = job["ana"]
        px = self.s.get(f"pxrate_{engine}") or DEFAULT_PXRATE[engine]
        io = self.s.get("io_rate") or DEFAULT_IO_RATE
        if a["ok"] or a["v_copy"]:
            return job["size"] * 2 / io + 1
        work = a["duration"] * max(a["w"] * a["h"], 1) * (a["fps"] or 25)
        return work / px + job["size"] * 0.6 / io + 1

    def show_estimate(self):
        todo = [j for j in self.jobs if j["todo"]]
        if self.busy:
            return
        self.show_space()
        if not todo:
            self.lbl_total.configure(text="")
            self.lbl_times.configure(text="")
            return
        eng = self.engine_now()
        est_all = sum(self.job_cost(j, eng) for j in todo)
        part = self.pick_portion(todo, eng)
        est = sum(self.job_cost(j, eng) for j in part)
        learned = T(" · aus früheren Läufen gelernt") if self.s.get(f"pxrate_{eng}") else ""
        end = time.strftime("%H:%M", time.localtime(time.time() + est))
        size = fmt_size(sum(j['size'] for j in todo))
        if len(part) < len(todo):
            self.lbl_total.configure(text=T("Portion: {0} von {1} Dateien  ·  alle zusammen {2}, ca. {3}",
                                            len(part), len(todo), size, fmt_time(est_all)))
            self.lbl_times.configure(text=T("Diese Portion: ca. {0} mit {1}  ·  fertig gegen {2}",
                                            fmt_time(est), eng.upper(), end) + learned)
        else:
            self.lbl_total.configure(text=T("{0} Dateien warten · {1}", len(todo), size))
            self.lbl_times.configure(text=T("Geschätzte Dauer: ca. {0} mit {1}  ·  fertig gegen {2}",
                                            fmt_time(est), eng.upper(), end) + learned)

    def pick_portion(self, todo, eng):
        """Die nächste Portion: die ersten N Dateien bzw. so viele, wie in die Zeit passen (mind. 1)."""
        p = self.s.get("portion") or 0
        if p > 0:
            return todo[:p]
        if p < 0:
            budget, acc, part = -p * 60, 0.0, []
            for j in todo:
                c = self.job_cost(j, eng)
                if part and acc + c > budget:
                    break
                part.append(j)
                acc += c
            return part
        return todo

    def set_portion(self, val):
        self.s["portion"] = val
        for v, b in self.p_btns.items():
            b.set_selected(v == val)
        self.show_estimate()
        self.save()

    def tick(self):
        """Läuft jede Sekunde: Laufzeit und Restzeit live anzeigen."""
        if self.busy and self.t_start:
            el = time.time() - self.t_start
            txt = T("Laufzeit {0}", fmt_time(el))
            if self.eta:
                rest = max(0, self.eta - time.time())
                txt += T("  ·  Rest ca. {0}  ·  fertig gegen {1}", fmt_time(rest),
                         time.strftime('%H:%M', time.localtime(self.eta)))
                if getattr(self, "eta_all", None):
                    end_all = time.strftime("%d.%m. %H:%M", time.localtime(self.eta_all))
                    txt += "\n" + T("Alles zusammen: Rest ca. {0}  ·  fertig gegen {1}",
                                    fmt_time(max(0, self.eta_all - time.time())), end_all)
            self.lbl_times.configure(text=txt)
        self.root.after(1000, self.tick)

    # ── Platz ────────────────────────────────────────────────────────────────
    def job_space(self, job, new, mode, orig, fertig):
        """{Laufwerk: Byte}, die für genau diese Datei im ungünstigsten Moment frei sein müssen,
        und {Laufwerk: Byte}, um die sich der Füllstand danach dauerhaft ändert."""
        sd = drive_of(job["src"])
        od = drive_of(orig) if orig else ""
        peak, delta = {drive_of(work_dir_path()): new}, {}
        if mode == "fertig":
            fd = drive_of(fertig)
            peak[fd] = peak.get(fd, 0) + new
            delta[fd] = delta.get(fd, 0) + new
        else:                                             # neue Datei liegt kurz neben dem Original
            peak[sd] = peak.get(sd, 0) + new
            delta[sd] = delta.get(sd, 0) + new - (job["size"] if od and od != sd else 0)
        if od and od != sd:                               # Original wandert auf ein anderes Laufwerk
            peak[od] = peak.get(od, 0) + job["size"]
            delta[od] = delta.get(od, 0) + job["size"]
            if mode == "fertig":
                delta[sd] = delta.get(sd, 0) - job["size"]
        return peak, delta

    def space_plan(self, todo):
        """Spielt alle Dateien der Reihe nach durch: Platz vorher/nachher je Laufwerk, wie viele passen."""
        mode, orig, fertig = self.s["mode"], self.orig_dir(), self.fertig_dir()
        q, cap, fac = self.s["quality"], self.v_cap.get(), self.s.get("size_factor") or 1.0
        free, used, fit, stuck = {}, {}, 0, False
        old_total = new_total = 0
        for j in todo:
            new = est_new_size(j, q, cap, fac)
            old_total += j["size"]
            new_total += new
            peak, delta = self.job_space(j, new, mode, orig, fertig)
            for d in set(peak) | set(delta):
                if d not in free:
                    free[d] = disk_free(d)
                    used[d] = 0
            if not stuck and all(free[d] - used[d] - p >= RESERVE for d, p in peak.items()):
                fit += 1
            else:
                stuck = True
            for d, x in delta.items():
                used[d] += x
        problems, lines = [], []
        for d in sorted(free):
            if not used[d] and d == drive_of(work_dir_path()):
                continue
            after = free[d] - used[d]
            sign = "+" if used[d] > 0 else "−"
            txt = T("{0}  frei {1}  →  danach ca. {2}  ({3})", d, fmt_size(free[d]), fmt_size(max(after, 0)),
                    sign + fmt_size(abs(used[d])))
            if after < RESERVE:
                problems.append(T("✖ {0} reicht nicht: braucht {1}, frei {2}", d, fmt_size(used[d]), fmt_size(free[d])))
                txt = "✖ " + txt
            else:
                txt = "✓ " + txt
            lines.append(txt)
        hint = ""
        sd, od = drive_of(self.v_src.get() or "C:\\"), drive_of(orig) if orig else ""
        if mode == "replace" and od == sd:
            hint = T("Originale bleiben auf demselben Laufwerk – dort wird nichts frei. "
                     "Tipp: „Originale verschieben nach …“ auf eine andere Platte.")
        return dict(ok=not problems and fit == len(todo), problems=problems, lines=lines, fit=fit,
                    old=old_total, new=new_total, hint=hint)

    def show_space(self):
        todo = [j for j in self.jobs if j["todo"]]
        if self.busy:
            return
        if not todo:
            self.lbl_space.configure(text="")
            return
        p = self.space_plan(todo)
        diff = (p["new"] - p["old"]) / p["old"] * 100 if p["old"] else 0
        head = (T("Platzbedarf: {0} → ca. {1} ({2} %)", fmt_size(p['old']), fmt_size(p['new']), f"{diff:+.0f}")
                + ("" if self.s.get("size_factor") else T("  ·  Schätzung, wird mit jedem Lauf genauer")))
        txt = "\n".join([head] + p["lines"] + ([p["hint"]] if p["hint"] else []))
        if not p["ok"]:
            txt += "\n" + T("⚠ Es passen voraussichtlich nur {0} von {1} Dateien – danach hält das Tool an.",
                            p['fit'], len(todo))
        col = self.pal["ok"] if p["ok"] and not p["hint"] else (self.pal["warn"] if p["ok"] else self.pal["accent"])
        self.lbl_space.configure(text=txt, fg=col)

    def on_switch(self):
        self.save()
        self.reclassify()                             # „überspringen“/„mitkopieren“ ändern den Status
        self.show_estimate()

    # ── Umwandeln ────────────────────────────────────────────────────────────
    def start_convert(self, cont=False):
        """cont=True: nächste Portion nach „Weitermachen?“ – ohne die Sicherheitsfragen noch einmal."""
        all_todo = [j for j in self.jobs if j["todo"]]
        if self.busy or not all_todo:
            return
        todo = self.pick_portion(all_todo, self.engine_now())
        self.portion_rest = len(all_todo) - len(todo)
        mode, orig = self.s["mode"], self.orig_dir()
        plan = self.space_plan(todo)
        if cont:
            pass
        elif not plan["ok"]:
            msg = (T("Der Platz reicht nicht für alle Dateien:") + "\n\n" + "\n".join(plan["problems"]) + "\n\n" +
                   T("Voraussichtlich passen {0} von {1} Dateien.\nDas Tool arbeitet Datei für Datei und hält von "
                     "selbst an, bevor ein Laufwerk unter {2} frei fällt.\n\nTrotzdem starten?",
                     plan['fit'], len(todo), fmt_size(RESERVE)))
            if not messagebox.askyesno(APP_NAME, msg, parent=self.root):
                return
        if cont:
            pass
        elif mode == "replace":
            msg = T("{0} Dateien werden repariert und ERSETZEN die Originale an Ort und Stelle.\n\n"
                    "Die Originale werden verschoben nach:\n{1}\n(Ordnerstruktur bleibt erhalten)\n\n"
                    "Ersetzt wird erst, wenn die neue Datei geprüft ist.\n\nStarten?", len(todo), orig)
            if not messagebox.askyesno(APP_NAME, msg, parent=self.root):
                return
        elif orig and not messagebox.askyesno(
                APP_NAME, T("Nach getaner Arbeit werden die Originale verschoben nach:\n{0}\n\nStarten?", orig),
                parent=self.root):
            return
        self.save()
        self.stop_evt.clear()
        self.set_running(True)
        eng = self.engine_now()
        opts = dict(gpu=eng == "gpu", q=self.s["quality"], deep=self.v_deep.get(), date=self.v_date.get(),
                    mode=mode, orig=orig, src_root=self.v_src.get().strip(), engine=eng, cap=self.v_cap.get(),
                    fertig=self.fertig_dir())
        for j in todo:
            j["est_new"] = est_new_size(j, opts["q"], opts["cap"], self.s.get("size_factor") or 1.0)
            j["est_raw"] = est_new_size(j, opts["q"], opts["cap"], 1.0)
        if mode == "fertig":
            os.makedirs(self.fertig_dir(), exist_ok=True)
        q_name = next(n for n, v in QUALITIES if v == opts["q"])
        portion = (T("Portion {0} von {1}", len(todo), len(all_todo)) if self.portion_rest
                   else T("{0} Dateien", len(todo)))
        self.log(T("=== Reparatur: {0} · {1} · Qualität {2} ===", portion,
                   "NVENC (GPU)" if opts['gpu'] else "x265 (CPU)", T(q_name)))
        self.log(T("Ziel: {0}", self.fertig_dir()) if mode == "fertig" else T("Ziel: Originale ersetzen"), "muted")
        if orig:
            self.log(T("Originale nach: {0}", orig), "muted")
        costs = [self.job_cost(j, eng) for j in todo]
        if not cont:                                  # Gesamtstand über alle Portionen dieser Sitzung
            self.sess = dict(total=sum(self.job_cost(j, eng) for j in all_todo), done=0.0,
                             n_all=len(all_todo), n_done=0)
        self.t_start = time.time()
        self.eta = self.t_start + sum(costs)
        self.eta_all = None
        threading.Thread(target=self.convert_worker, args=(todo, costs, opts, dict(self.sess)), daemon=True).start()

    def convert_worker(self, todo, costs, opts, sess):
        print(f"[{APP_NAME}] Version {APP_VERSION} · Reparatur von {len(todo)} Dateien")
        total_cost = sum(costs) or 1
        done_cost, t0 = 0.0, time.time()
        ok = fail = 0
        size_in = size_out = 0
        self.learn = dict(work=0.0, enc_s=0.0, bytes=0, io_s=0.0, est=0, act=0)
        cnt = dict(n=len(todo), done=0, conv=0, copied=0, replaced=0, moved=0, failed=0)
        self.post("counts", dict(cnt), opts)
        wd = work_dir()
        space_stop = ""
        for idx, (job, cost) in enumerate(zip(todo, costs), 1):
            if self.stop_evt.is_set():
                break
            # Platz-Wächter: diese Datei nur anfangen, wenn danach überall noch RESERVE frei bleibt
            peak, _ = self.job_space(job, int(job["est_new"] * 1.2), opts["mode"], opts["orig"], opts["fertig"])
            short = [d for d, need in peak.items() if disk_free(d) - need < RESERVE]
            if short:
                space_stop = ", ".join(short)
                self.post("log", T("⚠ Platz auf {0} reicht nicht mehr für „{1}“ (es müssen {2} frei bleiben) – "
                                   "angehalten.", space_stop, job['rel'], fmt_size(RESERVE)), "err")
                break
            dur = max(job["ana"]["duration"], 1)

            def prog(sec, speed, _base=done_cost, _idx=idx, _job=job, _dur=dur, _cost=cost):
                f_file = min(sec / _dur, 1)
                done_now = _base + _cost * f_file
                f_total = done_now / total_cost
                el = time.time() - t0
                # anfangs Schätzung, mit wachsendem Fortschritt immer mehr die echte Geschwindigkeit
                w = min(1.0, f_total * 10)
                ratio = w * (el / max(done_now, 1e-6)) + (1 - w) if f_total > 0 else 1.0
                rest = (total_cost - done_now) * ratio
                rest_all = rest + (sess["total"] - sess["done"] - total_cost) * ratio
                f_all = (sess["done"] + done_now) / max(sess["total"], 1e-6)
                now = time.time()
                self.post("progress", _job, sess["n_done"] + _idx, sess["n_all"], _idx, len(todo), f_file,
                          min(f_all, 1), speed, now + rest, now + max(rest_all, rest))

            self.post("job_state", job, "läuft …", "run")
            prog(0, "")
            res, msg = self.do_job(job, opts, wd, prog)
            done_cost += cost
            if res == "ok":
                ok += 1
                size_in += job["size"]
                size_out += job.get("new_size", 0)
                cnt["copied" if job["ana"]["ok"] else "conv"] += 1
                cnt["replaced"] += job.get("replaced", False)
                cnt["moved"] += job.get("moved", False)
                self.post("job_state", job, msg, "done")
            elif res == "stop":
                self.post("job_state", job, "abgebrochen", "skip")
                break
            else:
                fail += 1
                cnt["failed"] += 1
                self.post("job_state", job, "Fehler", "err")
                self.post("log", f"✖ {job['rel']}: {msg}", "err")
            cnt["done"] += 1
            self.post("counts", dict(cnt), opts)
        clean_work_dirs(own=True)
        self.post("portion_done", done_cost, cnt["done"])
        self.post("convert_done", ok, fail, size_in, size_out, time.time() - t0,
                  self.stop_evt.is_set() or bool(space_stop), opts["engine"])
        if space_stop:
            self.post("space_stop", space_stop)

    def do_job(self, job, opts, wd, prog):
        a = job["ana"]
        part = Path(wd) / f"job_{os.getpid()}.mp4"
        part.unlink(missing_ok=True)
        if a["ok"]:                                   # schon sauber → unverändert kopieren
            self.post("log", T("→ {0}  (sauber, wird kopiert)", job['rel']), "muted")
            return self.finish(job, part, opts, "kopiert", copy_only=True)

        self.post("log", f"▶ {job['rel']}  ({issues_text(a['issues'])})")
        use_gpu = opts["gpu"]
        t = time.time()
        rc, err = self.run_ffmpeg(self.build_cmd(job, part, use_gpu, opts["q"], opts["cap"]), a["duration"], prog)
        if rc != 0 and rc != "stop" and use_gpu and not a["v_copy"]:
            self.post("log", "   " + T("GPU-Fehler ({0}) – nochmal mit CPU …", err[-1] if err else rc), "warn")
            t = time.time()
            rc, err = self.run_ffmpeg(self.build_cmd(job, part, False, opts["q"], opts["cap"]), a["duration"], prog)
        if rc == "stop":
            part.unlink(missing_ok=True)
            return "stop", ""
        if rc != 0:
            part.unlink(missing_ok=True)
            return "err", " | ".join(err[-3:]) or T("ffmpeg Rückgabewert {0}", rc)
        if not a["v_copy"]:
            self.learn["work"] += a["duration"] * max(a["w"] * a["h"], 1) * (a["fps"] or 25)
            self.learn["enc_s"] += time.time() - t

        problem = self.verify(job, part, opts["deep"])
        if problem:
            part.unlink(missing_ok=True)
            return "err", T("Prüfung fehlgeschlagen: {0}", problem)
        return self.finish(job, part, opts, "✓ H.265" if not a["v_copy"] else "✓ neu verpackt")

    def finish(self, job, part, opts, label, copy_only=False):
        """Legt das Ergebnis ab (Fertig-Ordner oder anstelle des Originals) und verschiebt ggf. das Original."""
        src, out = job["src"], job["out"]
        tmp = out.with_name(out.stem + ".part.mp4")
        st = src.stat()
        t = time.time()
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src if copy_only else part, tmp)    # erst komplett ablegen …
            if opts["date"]:
                os.utime(tmp, (st.st_atime, st.st_mtime))
            if opts["orig"]:                                   # … dann Original wegräumen …
                dest = Path(opts["orig"]) / src.relative_to(opts["src_root"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    dest = dest.with_name(f"{dest.stem}_{time.strftime('%Y%m%d-%H%M%S')}{dest.suffix}")
                shutil.move(str(src), str(dest))
            os.replace(tmp, out)                               # … und erst jetzt umbenennen
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            if opts["orig"] and not src.exists() and "dest" in locals() and dest.exists():
                self.post("log", "   " + T("Original liegt sicher in {0}", dest), "warn")
            return "err", T("Ablegen fehlgeschlagen: {0}", e)
        finally:
            part.unlink(missing_ok=True)
        self.learn["bytes"] += st.st_size + out.stat().st_size
        self.learn["io_s"] += time.time() - t
        job["new_size"] = out.stat().st_size
        if not copy_only and not job["ana"]["v_copy"]:
            self.learn["est"] += job.get("est_raw", 0)
            self.learn["act"] += job["new_size"]
        job["replaced"] = opts["mode"] == "replace"
        job["moved"] = bool(opts["orig"])
        ratio = job["new_size"] / job["size"] * 100 if job["size"] else 0
        self.post("log", f"   {T(label)} · {fmt_size(job['new_size'])} ({ratio:.0f} %)", "ok")
        return "ok", label

    def build_cmd(self, job, part, use_gpu, q, cap=True):
        a = job["ana"]
        vc = video_cap(a, cap)
        # großer Puffer: kurze Spitzen (Action) dürfen mehr, im Mittel bleibt es unter dem Original
        limit = ["-maxrate", f"{vc}k", "-bufsize", f"{vc * 4}k"] if vc else []
        cmd = [tool("ffmpeg.exe"), "-hide_banner", "-nostdin", "-y", "-v", "error", "-nostats",
               "-progress", "pipe:1", "-fflags", "+genpts", "-i", str(job["src"]),
               "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-map_metadata", "0", "-map_chapters", "-1"]
        if a["v_copy"]:
            cmd += ["-c:v", "copy"]
        else:
            gop = str(max(1, round(a["fps"] * 2)))     # alle 2 s ein Schlüsselbild → schnelles Spulen
            if use_gpu:                                # NVENC-cq +5 ≈ gleiche Dateigröße wie x265-crf
                cmd += ["-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(q + 5),
                        "-b:v", "0", "-profile:v", "main", "-g", gop] + limit
            else:
                cmd += ["-c:v", "libx265", "-preset", "medium", "-crf", str(q), "-profile:v", "main",
                        "-g", gop, "-x265-params", "log-level=error"] + limit
            cmd += ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p",
                    "-fps_mode", "cfr", "-r", a["rate"]]
        cmd += ["-tag:v", "hvc1"]
        if a["has_audio"]:
            if a["a_copy"]:
                cmd += ["-c:a", "copy"]
            else:
                cmd += ["-c:a", "aac", "-b:a", f"{audio_kbps(a, cap)}k", "-ac", "2",
                        "-af", "aresample=async=1:first_pts=0"]
        cmd += ["-movflags", "+faststart", "-f", "mp4", str(part)]
        return cmd

    def run_ffmpeg(self, cmd, duration, prog):
        """Startet ffmpeg mit -progress pipe:1. Gibt (rc | 'stop', Fehlerzeilen) zurück."""
        errs, last = [], 0.0
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW, bufsize=1)
        except OSError as e:
            return -1, [str(e)]
        speed = ""
        for line in self.proc.stdout:
            line = line.strip()
            m = re.fullmatch(r"([a-z_0-9]+)=(.*)", line)
            if m:
                k, v = m.groups()
                if k == "speed":
                    speed = v
                elif k == "out_time_us" and v.isdigit():
                    sec = int(v) / 1e6
                    if sec - last >= 0.5 or sec >= duration:
                        last = sec
                        prog(sec, speed)
            elif line:
                errs.append(line)
        rc = self.proc.wait()
        self.proc = None
        if self.stop_evt.is_set():
            return "stop", errs
        return rc, errs

    def verify(self, job, part, deep):
        """Prüft die neue Datei: H.265/hvc1 in MP4, gleiche Länge, optional komplett decodierbar."""
        b = analyze(part)
        if b["error"]:
            return T(b["error"])
        if b["container"] != "MP4" or b["vcodec"] != "hevc":
            return T("Ergebnis ist {0} · {1}", b['container'], b['vcodec'])
        if b["issues"]:
            return issues_text(b["issues"])
        d0, d1 = job["ana"]["duration"], b["duration"]
        if d0 and abs(d0 - d1) > max(2.0, d0 * 0.01):
            return T("Länge {0} statt {1}", fmt_time(d1), fmt_time(d0))
        if deep:
            self.post("detail", T("prüfe komplett …"))
            rc, out = run_out([tool("ffmpeg.exe"), "-v", "error", "-i", str(part), "-map", "0", "-f", "null", "-"], 3600)
            if rc != 0 or out.strip():
                return T("Decoder meldet: {0}", (out.strip().splitlines() or [str(rc)])[-1])
        return ""

    def on_progress(self, job, idx, n, p_idx, p_n, f_file, f_all, speed, eta, eta_all):
        """Oberer Balken = aktuelle Datei, unterer Balken = ganze Liste (über alle Portionen)."""
        self.lbl_file.configure(text=job["src"].name)
        self.pb_file.set(f_file)
        self.pb_total.set(f_all)
        sp = "  ·  " + T("{0}× Echtzeit", speed.strip().rstrip('x')) if speed and speed not in ("N/A", "0x") else ""
        self.lbl_detail.configure(text=f"{f_file * 100:.0f} %{sp}")
        portion = "  ·  " + T("Portion {0} von {1}", p_idx, p_n) if p_n < n else ""
        self.lbl_total.configure(text=T("Datei {0} von {1}  ·  gesamt {2} %", idx, n, f"{f_all * 100:.1f}") + portion)
        self.eta = eta
        self.eta_all = eta_all if p_n < n else None
        self.root.title(f"{f_all * 100:.0f} % · {APP_NAME}")

    def on_portion_done(self, cost, n):
        self.sess["done"] += cost
        self.sess["n_done"] += n

    def on_counts(self, c, opts):
        """Zähler unten rechts in der Statuszeile."""
        parts = [T("{0} von {1} verarbeitet", c['done'], c['n'])]
        if opts["mode"] == "replace":
            parts.append(T("{0} ersetzt", c['replaced']))
        else:
            parts.append(T("{0} umgewandelt", c['conv']))
            if c["copied"]:
                parts.append(T("{0} kopiert", c['copied']))
        if opts["orig"]:
            parts.append(T("{0} Originale verschoben", c['moved']))
        if c["failed"]:
            parts.append(T("{0} Fehler", c['failed']))
        self.lbl_counts.configure(text="  ·  ".join(parts), fg=self.pal["accent"] if c["failed"] else self.pal["text"])
        self.lbl_counts_text = "  ·  ".join(parts)

    def on_space_stop(self, drives):
        self.lbl_file.configure(text=T("Angehalten – Platz auf {0} wird knapp", drives))
        messagebox.showwarning(APP_NAME, T("Auf {0} wird der Platz knapp, deshalb wurde angehalten.\n\n"
                                           "Alles bis hierher ist fertig und sicher abgelegt. Platz schaffen "
                                           "(z. B. Originale auf eine andere Platte) und dann einfach neu "
                                           "analysieren – es geht an derselben Stelle weiter.", drives), parent=self.root)

    def on_detail(self, text):
        self.lbl_detail.configure(text=text)

    def on_job_state(self, job, text, tag):
        if tag in ("done", "err"):                    # Fehler nicht in jeder Portion neu versuchen
            job["todo"] = False
        job["state"], job["tag"] = text, tag
        if "iid" in job and self.tree.exists(job["iid"]):
            self.tree.set(job["iid"], "status", T(text))
            self.tree.item(job["iid"], tags=(tag,))
            if tag == "run":
                self.tree.see(job["iid"])
            elif tag == "done":                       # kurz das ✓ zeigen, dann aus der Liste nehmen
                self.root.after(1500, lambda iid=job["iid"]: self.hide_row(iid))

    def hide_row(self, iid):
        if self.tree.exists(iid):
            self.tree.delete(iid)
            for j in self.jobs:
                if j.get("iid") == iid:
                    j["hidden"] = True
        self.hidden = getattr(self, "hidden", 0) + 1
        self.show_hidden_count()

    def show_hidden_count(self):
        if getattr(self, "hidden", 0):
            shown = len(self.tree.get_children())
            self.lbl_count.configure(text=T("{0} in der Liste  ·  {1} erledigt ausgeblendet", shown, self.hidden))

    def on_convert_done(self, ok, fail, size_in, size_out, secs, stopped, engine):
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.t_start = self.eta = None
        ln = getattr(self, "learn", {})               # gemessene Geschwindigkeit für die nächste Schätzung merken
        if ln.get("enc_s", 0) > 20:
            rate, key = ln["work"] / ln["enc_s"], f"pxrate_{engine}"
            self.s[key] = round(rate if not self.s.get(key) else 0.5 * self.s[key] + 0.5 * rate)
        if ln.get("est", 0) > 0 and ln.get("act", 0) > 0:          # wie gut passte die Größenschätzung?
            f = ln["act"] / ln["est"]
            self.s["size_factor"] = round(f if not self.s.get("size_factor") else 0.5 * self.s["size_factor"] + 0.5 * f, 3)
        if ln.get("io_s", 0) > 5:
            rate = ln["bytes"] / ln["io_s"]
            self.s["io_rate"] = round(rate if not self.s.get("io_rate") else 0.5 * self.s["io_rate"] + 0.5 * rate)
        self.set_running(False)
        self.lbl_times.configure(text=T("Laufzeit {0}", fmt_time(secs)))
        self.pb_file.set(1 if not stopped else 0)
        head = T("Abgebrochen") if stopped else (T("Fertig") if not fail else T("Fertig mit Fehlern"))
        self.lbl_file.configure(text=head)
        ratio = f" · {fmt_size(size_in)} → {fmt_size(size_out)}" if size_in else ""
        self.lbl_detail.configure(text=T("{0} repariert · {1} Fehler", ok, fail) + ratio)
        self.lbl_total.configure(text=getattr(self, "lbl_counts_text", ""))
        self.log(T("=== {0}: {1} ok, {2} Fehler{3} · {4} ===", head, ok, fail, ratio, fmt_time(secs)),
                 "err" if fail else "ok")
        self.save()
        self.show_space()                             # Platzanzeige auf den Rest aktualisieren (oder leeren)
        rest = [j for j in self.jobs if j["todo"]]
        if not stopped and getattr(self, "portion_rest", 0) and rest:
            eng = self.engine_now()
            nxt = self.pick_portion(rest, eng)
            est_n = sum(self.job_cost(j, eng) for j in nxt)
            est_all = sum(self.job_cost(j, eng) for j in rest)
            if messagebox.askyesno(APP_NAME, T("Portion fertig: {0} Dateien in {1}.\n\n"
                                               "Noch {2} Dateien übrig (zusammen ca. {3}).\n\n"
                                               "Mit der nächsten Portion weitermachen?\n({4} Dateien, ca. {5})",
                                               ok, fmt_time(secs), len(rest), fmt_time(est_all), len(nxt),
                                               fmt_time(est_n)), parent=self.root):
                self.root.after(200, lambda: self.start_convert(cont=True))
            else:
                self.log(T("Pause – {0} Dateien übrig. Beim nächsten Mal geht es an dieser Stelle weiter.", len(rest)),
                         "muted")
                self.show_estimate()

    def stop(self):
        if not self.busy:
            return
        self.stop_evt.set()
        p = self.proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except OSError:
                pass
        self.lbl_file.configure(text=T("breche ab …"))

    # ── Sprache umschalten ───────────────────────────────────────────────────
    def set_lang(self, code):
        global LANG
        if code == LANG:
            return
        if self.busy:
            messagebox.showinfo(APP_NAME, T("Bitte warten, bis die Reparatur fertig ist – dann kann die Sprache "
                                            "umgestellt werden."), parent=self.root)
            return
        LANG = self.s["lang"] = code
        self.save()
        self.rebuild()

    def rebuild(self):
        """Oberfläche in der neuen Sprache neu aufbauen – Liste, Protokoll und Status bleiben erhalten."""
        log = self.log_text.get("1.0", "end-1c")
        log_open = self.log_frame.winfo_ismapped()
        geo = self.root.geometry()
        for w in self.root.winfo_children():
            w.destroy()
        self.themed, self.custom = [], []
        self.build()
        self.apply_theme()
        self.set_mode(self.s["mode"], keep_jobs=True)
        self.set_engine(self.s["engine"])
        self.set_quality(self.s["quality"])
        self.set_portion(self.s.get("portion", 0))
        self.log_text.configure(state="normal")
        self.log_text.insert("end", log + "\n" if log else "")
        self.log_text.configure(state="disabled")
        self.log_text.see("end")
        if not log_open:
            self.toggle_log()
        for j in self.jobs:
            if not j.get("hidden"):
                j["iid"] = self.tree.insert("", "end", values=self.row_values(j), tags=(j.get("tag", ""),))
        if self.jobs:
            self.summary_texts()
            self.show_hidden_count()
        if hasattr(self, "ff_status"):
            self.on_tools(None, *self.ff_status) if self.ff_status[1] != "ok" else self.lbl_ff.configure(
                text=f"✓ {self.ff_status[0]}", fg=self.pal["muted"])
        if hasattr(self, "nvenc_hint"):
            self.lbl_gpu.configure(text=(T("✓ GPU: NVIDIA NVENC (H.265)") if self.nvenc_ok
                                         else T("● GPU: {0} → CPU", T(*self.nvenc_hint))))
            self.update_engine_label()
        self.set_running(False)
        self.show_estimate()
        self.root.geometry(geo)
        self.fit_window()

    def on_close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, T("Es läuft noch eine Reparatur.\nWirklich beenden?"),
                                                 parent=self.root):
            return
        self.stop()
        self.s["geometry"] = self.root.geometry()
        self.save()
        clean_work_dirs(own=True)
        self.root.destroy()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    print(f"[{APP_NAME}] Version {APP_VERSION} gestartet")
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
