# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adding recognition languages without a terminal.

Language data comes from the distribution's own ``tesseract-ocr-<lang>``
packages (about 1-2 MB each), installed through PackageKit, the service GNOME
Software uses. The system asks for the administrator password for each change;
Kingfisher itself never downloads anything. Packages are shared with other OCR
apps and receive updates with the rest of the system.

Where PackageKit is not available, the same packages can be installed with the
command shown in the Languages window.
"""

from dataclasses import dataclass

from gi.repository import GLib

PACKAGE_PREFIX = "tesseract-ocr-"
# Packages that are not a recognition language, or are not usable on their own.
NOT_LANGUAGES = {"all", "osd", "equ"}

# Most-read languages worldwide (by speakers and published text), offered first.
POPULAR = ("chi_sim", "spa", "hin", "ara", "fra", "por", "rus", "jpn", "deu", "ben",
           "kor", "ita", "tur", "vie", "ind", "urd", "tam", "tel", "mar", "pol")

# ISO 639-1 (system locales) to Tesseract codes, for "Suggested for you".
LOCALE_TO_CODE = {
    "af": "afr", "ar": "ara", "az": "aze", "be": "bel", "bg": "bul", "bn": "ben", "ca": "cat",
    "cs": "ces", "da": "dan", "de": "deu", "el": "ell", "en": "eng", "es": "spa", "et": "est",
    "fa": "fas", "fi": "fin", "fr": "fra", "gu": "guj", "he": "heb", "hi": "hin", "hr": "hrv",
    "hu": "hun", "id": "ind", "is": "isl", "it": "ita", "ja": "jpn", "kn": "kan", "ko": "kor",
    "lt": "lit", "lv": "lav", "ml": "mal", "mr": "mar", "ms": "msa", "ne": "nep", "nl": "nld",
    "nb": "nor", "nn": "nor", "no": "nor", "or": "ori", "pa": "pan", "pl": "pol", "pt": "por",
    "ro": "ron", "ru": "rus", "sk": "slk", "sl": "slv", "sr": "srp", "sv": "swe", "ta": "tam",
    "te": "tel", "th": "tha", "tr": "tur", "uk": "ukr", "ur": "urd", "vi": "vie",
}


def package_name(code):
    return PACKAGE_PREFIX + code.lower().replace("_", "-")


def code_from_package(name):
    """"tesseract-ocr-chi-sim" -> "chi_sim"; None for packages that are not a language."""
    if not name.startswith(PACKAGE_PREFIX):
        return None
    rest = name[len(PACKAGE_PREFIX):]
    if rest in NOT_LANGUAGES or rest.startswith("script-") or not rest.replace("-", "").isalpha():
        return None
    return rest.replace("-", "_")


def install_command(codes):
    return "sudo apt install " + " ".join(package_name(c) for c in codes)


def suggested_codes(language_names=None):
    """Languages of the user's own locales (``LANGUAGE``, ``LANG`` …), in order."""
    names = language_names if language_names is not None else GLib.get_language_names()
    codes = []
    for name in names:
        base = name.split(".")[0].split("@")[0].split("_")[0].lower()
        code = LOCALE_TO_CODE.get(base)
        if base == "zh":
            code = "chi_tra" if any(t in name for t in ("TW", "HK", "MO")) else "chi_sim"
        if code and code not in codes:
            codes.append(code)
    return codes


@dataclass
class LanguagePack:
    code: str
    package_id: str
    installed: bool
    size: int = 0          # download size in bytes, 0 if unknown


class PackageKitInstaller:
    """Lists, installs and removes language packs through PackageKit.

    Every method is asynchronous and calls back on the main loop. Listing is a
    read-only query and needs no password; installing and removing make the
    system ask for one."""

    def __init__(self):
        gi = __import__("gi")
        gi.require_version("PackageKitGlib", "1.0")
        from gi.repository import PackageKitGlib  # optional dependency
        self.pk = PackageKitGlib
        self.client = PackageKitGlib.Client()
        self.client.set_interactive(True)   # allow the password prompt

    @staticmethod
    def create():
        """The installer, or None if PackageKit is not available here."""
        try:
            return PackageKitInstaller()
        except (ImportError, ValueError, GLib.Error):
            return None

    # ------------------------------------------------------------- listing
    def list_packs(self, callback):
        """callback(list[LanguagePack] | None, error message | None)."""
        filters = self.pk.filter_bitfield_from_string("arch;newest")

        def on_search(client, result):
            try:
                results = client.generic_finish(result)
            except GLib.Error as err:
                callback(None, err.message)
                return
            packs = {}
            for pkg in results.get_package_array():
                code = code_from_package(pkg.get_name())
                if not code:
                    continue
                installed = pkg.get_info() == self.pk.InfoEnum.INSTALLED
                if code not in packs or installed:
                    packs[code] = LanguagePack(code, pkg.get_id(), installed)
            self._add_sizes(list(packs.values()), callback)

        self.client.search_names_async(filters, [PACKAGE_PREFIX], None, _ignore, (), on_search)

    def _add_sizes(self, packs, callback):
        if not packs:
            callback(packs, None)
            return

        def on_details(client, result):
            try:
                details = client.generic_finish(result).get_details_array()
                sizes = {d.get_package_id(): d.get_size() for d in details}
                for pack in packs:
                    pack.size = sizes.get(pack.package_id, 0)
            except GLib.Error:
                pass   # sizes are a nicety
            callback(packs, None)

        self.client.get_details_async([p.package_id for p in packs], None, _ignore, (), on_details)

    def find(self, names, callback):
        """Look up packages by name: callback(list[LanguagePack] | None, error).
        Each result's ``code`` is the package name."""
        filters = self.pk.filter_bitfield_from_string("arch;newest")

        def on_resolve(client, result):
            try:
                found = client.generic_finish(result).get_package_array()
            except GLib.Error as err:
                callback(None, err.message)
                return
            packs = {}
            for pkg in found:
                installed = pkg.get_info() == self.pk.InfoEnum.INSTALLED
                if pkg.get_name() not in packs or installed:
                    packs[pkg.get_name()] = LanguagePack(pkg.get_name(), pkg.get_id(), installed)
            self._add_sizes(list(packs.values()), callback)

        self.client.resolve_async(filters, list(names), None, _ignore, (), on_resolve)

    # ------------------------------------------------------ install/remove
    def install(self, packs, progress, callback):
        """progress(stage text); callback(error message or None)."""
        flags = self.pk.transaction_flag_bitfield_from_string("only-trusted")
        self.client.install_packages_async(flags, [p.package_id for p in packs], None,
                                           self._progress(progress), (), self._done(callback))

    def remove(self, packs, progress, callback):
        flags = self.pk.transaction_flag_bitfield_from_string("only-trusted")
        self.client.remove_packages_async(flags, [p.package_id for p in packs], False, False, None,
                                          self._progress(progress), (), self._done(callback))

    def _progress(self, progress):
        """Report the transaction's stage, not a percentage: PackageKit's
        percentage restarts for every stage (prepare, download, install), so
        a single number would jump back and forth."""
        shown = [None]

        def on_progress(prog, kind, *_):
            if kind != self.pk.ProgressType.STATUS:
                return
            text = next_stage(shown[0], stage_text(self.pk.StatusEnum, prog.get_property("status")))
            if text != shown[0]:
                shown[0] = text
                GLib.idle_add(lambda: progress(text) and False)
        return on_progress

    @staticmethod
    def _done(callback):
        def on_done(client, result):
            try:
                results = client.generic_finish(result)
                error = results.get_error_code()
                callback(error.get_details() if error else None)
            except GLib.Error as err:
                callback(friendly_error(err.message))
        return on_done


def stage_text(status_enum, status):
    """Plain words for a PackageKit transaction stage."""
    names = {
        "Waiting for password…": ("WAITING_FOR_AUTH",),
        "Waiting for updates…": ("WAIT", "WAITING_FOR_LOCK"),
        "Downloading…": ("DOWNLOAD", "DOWNLOAD_PACKAGELIST", "DOWNLOAD_REPOSITORY", "DOWNLOAD_FILELIST",
                         "DOWNLOAD_CHANGELOG", "DOWNLOAD_GROUP", "DOWNLOAD_UPDATEINFO", "REFRESH_CACHE"),
        "Installing…": ("INSTALL", "COMMIT", "COPY_FILES", "RUN_HOOK", "REPACKAGING", "UPDATE",
                        "CHECK_LIBRARIES", "CHECK_EXECUTABLE_FILES", "SIG_CHECK", "TEST_COMMIT"),
        "Removing…": ("REMOVE", "OBSOLETE"),
        "Finishing…": ("CLEANUP", "FINISHED", "SCAN_APPLICATIONS", "SCAN_PROCESS_LIST",
                       "GENERATE_PACKAGE_LIST"),
    }
    for text, members in names.items():
        if any(status == getattr(status_enum, m, None) for m in members):
            return text
    return "Preparing…"


STAGE_ORDER = ("Preparing…", "Downloading…", "Installing…", "Removing…", "Finishing…")
WAITING = ("Waiting for password…", "Waiting for updates…")


def next_stage(shown, new):
    """The text to show next. Stages only move forward, so a short "setup"
    status between downloading and installing never flashes "Preparing…"
    again; waiting messages may appear at any time."""
    if new in WAITING or shown is None or shown in WAITING:
        if shown in WAITING and new in STAGE_ORDER and new == "Preparing…":
            return shown   # still waiting until real work starts
        return new
    if STAGE_ORDER.index(new) < STAGE_ORDER.index(shown):
        return shown
    return new


def _ignore(*_):
    pass


def friendly_error(message):
    lowered = (message or "").lower()
    if "not authorized" in lowered or "authentication" in lowered or "cancel" in lowered:
        return "The password prompt was canceled"
    if "network" in lowered or "download" in lowered or "resolve" in lowered:
        return "Couldn't reach the software sources. Check your internet connection"
    return message or "Something went wrong"


def human_size(size):
    if size <= 0:
        return ""
    if size < 1024 * 1024:
        return f"{max(1, round(size / 1024))} KB"
    return f"{size / (1024 * 1024):.1f} MB"
