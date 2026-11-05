# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local OCR engine built on the Tesseract LSTM recogniser.

Everything runs on this machine: the image is piped to a ``tesseract``
subprocess and nothing ever leaves the computer.
"""

import io
import os
import shutil
import signal
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from gi.repository import GLib
from PIL import Image, ImageOps

USER_TESSDATA = Path(GLib.get_user_data_dir()) / "kingfisher" / "tessdata"
SYSTEM_TESSDATA = [
    Path("/usr/share/tesseract-ocr/5/tessdata"),
    Path("/usr/share/tesseract-ocr/4.00/tessdata"),
    Path("/usr/share/tessdata"),
    Path("/usr/local/share/tessdata"),
]

PSM = {"auto": 3, "block": 6, "line": 7, "sparse": 11}

# Pixels budget for the upscaled image handed to Tesseract.
MAX_PIXELS = 8_000_000

LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl", "\u00a0": " "})

LANGUAGE_NAMES = {
    "afr": "Afrikaans", "ara": "Arabic", "aze": "Azerbaijani", "bel": "Belarusian",
    "ben": "Bengali", "bul": "Bulgarian", "cat": "Catalan", "ces": "Czech",
    "chi_sim": "Chinese (Simplified)", "chi_tra": "Chinese (Traditional)",
    "dan": "Danish", "deu": "German", "ell": "Greek", "eng": "English",
    "est": "Estonian", "fas": "Persian", "fin": "Finnish", "fra": "French",
    "guj": "Gujarati", "heb": "Hebrew", "hin": "Hindi", "hrv": "Croatian",
    "hun": "Hungarian", "ind": "Indonesian", "isl": "Icelandic", "ita": "Italian",
    "jpn": "Japanese", "kan": "Kannada", "kor": "Korean", "lav": "Latvian",
    "lit": "Lithuanian", "mal": "Malayalam", "mar": "Marathi", "msa": "Malay",
    "nep": "Nepali", "nld": "Dutch", "nor": "Norwegian", "ori": "Odia",
    "pan": "Punjabi", "pol": "Polish", "por": "Portuguese", "ron": "Romanian",
    "rus": "Russian", "slk": "Slovak", "slv": "Slovenian", "spa": "Spanish",
    "srp": "Serbian", "swe": "Swedish", "tam": "Tamil", "tel": "Telugu",
    "tha": "Thai", "tur": "Turkish", "ukr": "Ukrainian", "urd": "Urdu",
    "vie": "Vietnamese",
    # Less common languages, scripts and historic forms available as packages.
    "amh": "Amharic", "asm": "Assamese", "aze_cyrl": "Azerbaijani (Cyrillic)", "bod": "Tibetan",
    "bos": "Bosnian", "bre": "Breton", "ceb": "Cebuano", "chi_sim_vert": "Chinese (Simplified, vertical)",
    "chi_tra_vert": "Chinese (Traditional, vertical)", "chr": "Cherokee", "cos": "Corsican", "cym": "Welsh",
    "div": "Divehi", "dzo": "Dzongkha", "enm": "Middle English", "epo": "Esperanto", "eus": "Basque",
    "fao": "Faroese", "fil": "Filipino", "frk": "German (Fraktur)", "frm": "Middle French",
    "fry": "Western Frisian", "gla": "Scottish Gaelic", "gle": "Irish", "glg": "Galician",
    "grc": "Ancient Greek", "hat": "Haitian Creole", "hye": "Armenian", "iku": "Inuktitut",
    "ita_old": "Italian (Old)", "jav": "Javanese", "jpn_vert": "Japanese (vertical)", "kat": "Georgian",
    "kat_old": "Georgian (Old)", "kaz": "Kazakh", "khm": "Khmer", "kir": "Kyrgyz",
    "kmr": "Kurdish (Kurmanji)", "kor_vert": "Korean (vertical)", "lao": "Lao", "lat": "Latin",
    "ltz": "Luxembourgish", "mkd": "Macedonian", "mlt": "Maltese", "mon": "Mongolian", "mri": "Maori",
    "mya": "Burmese", "oci": "Occitan", "pus": "Pashto", "que": "Quechua", "san": "Sanskrit",
    "sin": "Sinhala", "snd": "Sindhi", "spa_old": "Spanish (Old)", "sqi": "Albanian",
    "srp_latn": "Serbian (Latin)", "sun": "Sundanese", "swa": "Swahili", "syr": "Syriac", "tat": "Tatar",
    "tgk": "Tajik", "tir": "Tigrinya", "ton": "Tongan", "uig": "Uyghur", "uzb": "Uzbek",
    "uzb_cyrl": "Uzbek (Cyrillic)", "yid": "Yiddish", "yor": "Yoruba",
}

HIDDEN_LANGS = {"osd", "equ"}


class OcrError(Exception):
    pass


class OcrCancelled(OcrError):
    pass


@dataclass
class Word:
    text: str
    conf: float
    left: float
    top: float
    width: float
    height: float
    block: int
    par: int
    line: int

    @property
    def right(self):
        return self.left + self.width

    @property
    def bottom(self):
        return self.top + self.height


@dataclass
class OcrResult:
    words: list
    width: int
    height: int
    languages: list
    elapsed: float
    codes: list = field(default_factory=list)   # QR codes and barcodes (codes.Code)
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def word_count(self):
        return len(self.words)

    @property
    def confidence(self):
        if not self.words:
            return 0.0
        return statistics.fmean(w.conf for w in self.words)

    def text(self, fmt="lines"):
        if fmt not in self._cache:
            builder = _build_paragraphs if fmt == "paragraphs" else _build_lines
            self._cache[fmt] = builder(self.words).translate(LIGATURES).strip("\n")
        return self._cache[fmt]


def looks_tabular(text):
    """Whether "As on Screen" text has real columns, so a monospace font helps
    keep them aligned. One indented line (a caption beside a picture, say) is
    not a table: it takes at least two lines with wide gaps between words."""
    lines = [line for line in text.splitlines() if line.strip()]
    gapped = sum(1 for line in lines if "   " in line.strip())
    return gapped >= 2 and gapped >= 0.25 * len(lines)


def language_name(code):
    parts = code.split("_")
    name = LANGUAGE_NAMES.get(code) or LANGUAGE_NAMES.get(parts[0])
    if name and code not in LANGUAGE_NAMES and len(parts) > 1:
        name = f"{name} ({parts[1]})"
    return name or code


# One deadline for a whole recognition, however many layout attempts it takes.
TOTAL_TIMEOUT = 90


class CancelToken:
    """Cancels one OCR job: it stops before its next step, and its running
    Tesseract process is killed. Each job has its own token, so jobs can
    never clear or steal each other's cancellation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._cancelled = False
        self._procs = set()

    @property
    def cancelled(self):
        return self._cancelled

    def cancel(self):
        with self._lock:
            self._cancelled = True
            procs = list(self._procs)
        for proc in procs:
            _kill(proc)

    def check(self):
        if self._cancelled:
            raise OcrCancelled()

    def attach(self, proc):
        with self._lock:
            self._procs.add(proc)
            cancelled = self._cancelled
        if cancelled:
            _kill(proc)

    def detach(self, proc):
        with self._lock:
            self._procs.discard(proc)


def _kill(proc):
    """Stop a Tesseract run, including anything it started: Tesseract may be a
    wrapper script, and a surviving child would keep the pipes open."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()


class TesseractEngine:
    def __init__(self):
        self.binary = os.environ.get("KINGFISHER_TESSERACT") or shutil.which("tesseract")

    # ------------------------------------------------------------------ setup
    @property
    def available(self):
        if not self.binary:   # look again: it may have been installed meanwhile
            self.binary = os.environ.get("KINGFISHER_TESSERACT") or shutil.which("tesseract")
        return bool(self.binary) and bool(self.languages())

    def tessdata_dirs(self):
        dirs = [USER_TESSDATA]
        env = os.environ.get("TESSDATA_PREFIX")
        if env:
            env_path = Path(env)
            dirs.append(env_path / "tessdata" if (env_path / "tessdata").is_dir() else env_path)
        dirs.extend(SYSTEM_TESSDATA)
        seen, result = set(), []
        for d in dirs:
            if d.is_dir() and d.resolve() not in seen:
                seen.add(d.resolve())
                result.append(d)
        return result

    def languages(self):
        langs = set()
        for d in self.tessdata_dirs():
            for f in d.glob("*.traineddata"):
                if f.stem not in HIDDEN_LANGS:
                    langs.add(f.stem)
        return sorted(langs, key=lambda c: (c != "eng", language_name(c).lower()))

    def version(self):
        if not self.binary:
            return None
        try:
            out = subprocess.run([self.binary, "--version"], capture_output=True, text=True,
                                 timeout=5, check=False)
            first = (out.stdout or out.stderr).splitlines()[0]
            return first.replace("tesseract", "").strip()
        except (OSError, IndexError, subprocess.SubprocessError):
            return None

    def has_best_models(self):
        return any(USER_TESSDATA.glob("*.traineddata")) if USER_TESSDATA.is_dir() else False

    def _tessdata_for(self, langs, model="fast"):
        dirs = self.tessdata_dirs()
        if model != "best":
            # High-accuracy models live in the user dir; skip them unless asked.
            dirs = [d for d in dirs if d != USER_TESSDATA] + [d for d in dirs if d == USER_TESSDATA]
        for d in dirs:
            if all((d / f"{lang}.traineddata").is_file() for lang in langs):
                return d
        return None

    # ------------------------------------------------------------ recognition
    def recognize(self, image, languages, layout="auto", enhance=True, model="fast", token=None):
        """Blocking OCR. Run from a worker thread; ``token`` (a CancelToken)
        lets the caller stop it."""
        token = token or CancelToken()
        if not self.binary:
            raise OcrError("Tesseract is not installed.")
        installed = set(self.languages())
        langs = [lang for lang in languages if lang in installed]
        if not langs:
            langs = ["eng"] if "eng" in installed else sorted(installed)[:1]
        if not langs:
            raise OcrError("No OCR language data is installed.")
        tessdata = self._tessdata_for(langs, model)
        if tessdata is None:
            raise OcrError("The selected languages are installed in different locations.")
        if image.width < 4 or image.height < 4:
            raise OcrError("The selected area is too small.")

        token.check()
        started = time.monotonic()
        deadline = started + TOTAL_TIMEOUT
        lo, hi = image.convert("L").getextrema()
        if hi - lo < 12:
            return OcrResult(words=[], width=image.width, height=image.height,
                             languages=langs, elapsed=time.monotonic() - started)
        prepared, scale, pad = prepare_image(image, enhance)
        png = io.BytesIO()
        prepared.save(png, format="PNG", compress_level=1)
        png = png.getvalue()

        psm = PSM.get(layout, 3)
        attempts = [psm] + ([6, 11] if layout == "auto" else [])
        words = []
        for mode in attempts:
            token.check()
            tsv = self._run(png, langs, mode, tessdata, token, deadline)
            words = _drop_icon_noise(_parse_tsv(tsv, scale, pad))
            if words:
                break

        return OcrResult(
            words=words, width=image.width, height=image.height,
            languages=langs, elapsed=time.monotonic() - started,
        )

    def _run(self, png, langs, psm, tessdata, token, deadline):
        cmd = [
            self.binary, "stdin", "stdout",
            "--tessdata-dir", str(tessdata),
            "-l", "+".join(langs),
            "--oem", "1",
            "--psm", str(psm),
            "--dpi", "300",
            "-c", "preserve_interword_spaces=1",
            "-c", "tessedit_create_txt=0",
            "-c", "tessedit_create_tsv=1",
        ]
        env = dict(os.environ)
        env.pop("TESSDATA_PREFIX", None)
        # Tesseract's OpenMP threading is slower than a single thread for
        # screen-sized images.
        env["OMP_THREAD_LIMIT"] = "1"
        token.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OcrError("Text recognition took too long and was stopped.")
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=env,
                start_new_session=True,      # its own process group, so cancel stops all of it
            )
        except OSError as err:
            raise OcrError(f"Could not start Tesseract: {err}") from err
        token.attach(proc)
        try:
            out, err = proc.communicate(png, timeout=remaining)
        except subprocess.TimeoutExpired as err:
            _kill(proc)
            proc.communicate()
            raise OcrError("Text recognition took too long and was stopped.") from err
        finally:
            token.detach(proc)
        token.check()
        if proc.returncode != 0:
            detail = err.decode("utf-8", "replace").strip().splitlines()
            raise OcrError(detail[-1] if detail else f"Tesseract failed ({proc.returncode}).")
        return out.decode("utf-8", "replace")


# ---------------------------------------------------------------- preprocessing
def prepare_image(image, enhance=True):
    """Convert a screen capture into what Tesseract reads best:
    dark text on a light background, large glyphs and a quiet border."""
    gray = image.convert("L")

    # Screen UIs are often light-on-dark. The background is the majority of
    # pixels, so the median tells us the polarity.
    hist = gray.histogram()
    half, acc, median = gray.width * gray.height / 2, 0, 0
    for value, count in enumerate(hist):
        acc += count
        if acc >= half:
            median = value
            break
    if median < 128:
        gray = ImageOps.invert(gray)

    scale = 1.0
    if enhance:
        pixels = gray.width * gray.height
        # Screen text is ~10-16 px tall; the LSTM model prefers 30+ px.
        scale = max(1.0, min(4.0, (MAX_PIXELS / max(pixels, 1)) ** 0.5))
        if scale > 1.01:
            size = (round(gray.width * scale), round(gray.height * scale))
            gray = gray.resize(size, Image.LANCZOS)
        gray = ImageOps.autocontrast(gray, cutoff=0.5)

    # Text touching the crop edge is a classic cause of missed characters.
    pad = max(10, round(12 * scale))
    gray = ImageOps.expand(gray, border=pad, fill=255)
    return gray, scale, pad


# ------------------------------------------------------------------ TSV parsing
def _parse_tsv(tsv, scale, pad):
    words = []
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        text = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not text or conf < 0:
            continue
        left, top, width, height = (int(c) for c in cols[6:10])
        words.append(Word(
            text=text, conf=conf,
            left=(left - pad) / scale, top=(top - pad) / scale,
            width=width / scale, height=height / scale,
            block=int(cols[2]), par=int(cols[3]), line=int(cols[4]),
        ))
    return words


def _drop_icon_noise(words):
    """UI icons get read as short junk tokens ("Q.", "CV", "¥"). Drop them
    when Tesseract itself is unsure and they carry no real content."""
    keep = []
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        prev = words[i - 1] if i else None
        line_start = prev is None or (prev.block, prev.par, prev.line) != (w.block, w.par, w.line)
        has_follower = nxt is not None and (nxt.block, nxt.par, nxt.line) == (w.block, w.par, w.line)
        if w.conf < 60 and not any(c.isalnum() for c in w.text):
            continue
        if w.conf < 60 and len(w.text) <= 2 and line_start and has_follower:
            continue
        keep.append(w)
    return keep


def _group_lines(words):
    lines, key, current = [], None, None
    for w in words:
        k = (w.block, w.par, w.line)
        if k != key:
            current = []
            lines.append(current)
            key = k
        current.append(w)
    return lines


def _char_width(words):
    samples = [w.width / len(w.text) for w in words if len(w.text) >= 2]
    if not samples:
        samples = [w.width / max(len(w.text), 1) for w in words]
    return max(statistics.median(samples), 1.0) if samples else 8.0


def _visual_rows(words):
    """Group Tesseract's lines into rows by their position on screen.

    Tesseract reads aligned columns (forms, tables, "label: value" lists) as
    separate blocks; merging lines that share a baseline puts each row back
    together the way it appears on screen."""
    lines = []
    for line in _group_lines(words):
        top = min(w.top for w in line)
        bottom = max(w.bottom for w in line)
        height = statistics.median(w.height for w in line)
        lines.append(((top + bottom) / 2, top, bottom, height, line))
    lines.sort(key=lambda item: item[0])

    rows = []
    for center, top, bottom, height, line in lines:
        if rows:
            row = rows[-1]
            if abs(center - row["center"]) < 0.5 * max(height, row["height"]):
                row["words"].extend(line)
                row["top"] = min(row["top"], top)
                row["bottom"] = max(row["bottom"], bottom)
                row["n"] += 1
                row["center"] += (center - row["center"]) / row["n"]
                continue
        rows.append({"center": center, "top": top, "bottom": bottom, "height": height,
                     "words": list(line), "n": 1})
    for row in rows:
        row["words"].sort(key=lambda w: w.left)
    return rows


def _build_lines(words):
    """Reproduce the on-screen layout: rows, indentation, column gaps and
    vertical spacing are preserved."""
    if not words:
        return ""
    char_w = _char_width(words)
    left_edge = min(w.left for w in words)
    out, prev = [], None
    for row in _visual_rows(words):
        if prev is not None and row["top"] - prev["bottom"] > 0.9 * max(row["height"], prev["height"]):
            out.append("")
        line = row["words"]
        indent = round((line[0].left - left_edge) / char_w)
        parts = [" " * max(0, min(indent, 60)) + line[0].text]
        for a, b in zip(line, line[1:], strict=False):
            gap = (b.left - a.right) / char_w
            spaces = round(gap) if gap >= 2.5 else 1
            parts.append(" " * max(1, min(spaces, 40)) + b.text)
        out.append("".join(parts).rstrip())
        prev = row
    return "\n".join(out)


def _build_paragraphs(words):
    """Reflow text: wrapped lines are joined into paragraphs and hyphenation
    is removed. Tesseract sometimes starts a new paragraph mid-sentence; a line
    in the same block that sits at normal line spacing and continues a
    hyphenated or unfinished sentence is joined back on."""
    if not words:
        return ""
    paragraphs = []  # [text, block, par, bottom, height, left]
    for line in _group_lines(words):
        text = " ".join(w.text for w in line)
        block, par = line[0].block, line[0].par
        top = min(w.top for w in line)
        left = line[0].left
        height = statistics.median(w.height for w in line)
        if paragraphs:
            prev = paragraphs[-1]
            same_par = (block, par) == (prev[1], prev[2])
            aligned = block == prev[1] or abs(left - prev[5]) < 1.5 * height
            continues = (aligned and 0 <= top - prev[3] < 0.8 * max(height, prev[4])
                         and (prev[0].endswith("-") or prev[0][-1:] not in ".!?:"))
            if same_par or continues:
                last = prev[0]
                if last.endswith("-") and len(last) > 1 and last[-2].isalpha() and text[:1].islower():
                    prev[0] = last[:-1] + text
                else:
                    prev[0] = f"{last} {text}"
                prev[1], prev[2], prev[3], prev[4] = block, par, max(w.bottom for w in line), height
                continue
        paragraphs.append([text, block, par, max(w.bottom for w in line), height, left])
    return "\n\n".join(p[0] for p in paragraphs)
