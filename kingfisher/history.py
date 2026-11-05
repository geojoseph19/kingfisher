# SPDX-FileCopyrightText: 2026 Geo Joseph
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small, private history of recent results.

Only text is kept — never the captured image. The file is readable by the
owner only, written atomically, and capped at ``MAX_ENTRIES`` entries.
"""

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gi.repository import GLib

HISTORY_FILE = Path(GLib.get_user_data_dir()) / "kingfisher" / "history.json"
MAX_ENTRIES = 20
MAX_TEXT = 20_000  # characters kept per entry


def relative_time(when, now=None):
    """Friendly timestamp for history: "Just now", "12 min ago", "Yesterday, 14:02"."""
    if when is None:
        return ""
    now = now or datetime.now(when.tzinfo)
    seconds = (now - when).total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    days = (now.date() - when.date()).days
    if days == 0:
        return when.strftime("Today, %H:%M")
    if days == 1:
        return when.strftime("Yesterday, %H:%M")
    return when.strftime("%-d %b, %H:%M")


def reflow_text(text):
    """Text-only version of the "Paragraphs" layout, for entries saved before
    both layouts were kept: lines within a block are joined, hyphenation at a
    line end is undone, and column spacing collapses to single spaces."""
    paragraphs = []
    for block in text.split("\n\n"):
        joined = ""
        for raw in block.splitlines():
            line = " ".join(raw.split())
            if not line:
                continue
            if joined.endswith("-") and len(joined) > 1 and joined[-2].isalpha() and line[:1].islower():
                joined = joined[:-1] + line
            else:
                joined = f"{joined} {line}" if joined else line
        if joined:
            paragraphs.append(joined)
    return "\n\n".join(paragraphs)


def day_label(when, now=None):
    """Heading for a group of results: "Today", "Yesterday", "Monday", "3 March 2026"."""
    if when is None:
        return "Earlier"
    now = now or datetime.now(when.tzinfo)
    days = (now.date() - when.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return when.strftime("%A")
    return when.strftime("%-d %B %Y")


def group_by_day(entries, now=None):
    """[(label, [entries…]), …] keeping the newest-first order."""
    groups = []
    for entry in entries:
        label = day_label(entry.created_local, now)
        if groups and groups[-1][0] == label:
            groups[-1][1].append(entry)
        else:
            groups.append((label, [entry]))
    return groups


def history_meta(entry):
    what = (entry.codes[0][0] if entry.codes and not entry.word_count
            else f"{entry.word_count} word{'s' if entry.word_count != 1 else ''}")
    stamp = relative_time(entry.created_local)
    return f"{stamp} · {what}" if stamp else what


@dataclass
class HistoryEntry:
    text: str                  # as on screen (line breaks and columns kept)
    paragraphs: str = ""       # the same text reflowed; empty for older entries
    word_count: int = 0
    confidence: float = 0.0
    languages: list = field(default_factory=list)
    codes: list = field(default_factory=list)   # [[kind, data], ...]
    source: str = "area"                         # area | screen | file | clipboard
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def has_formats(self):
        """Any entry with text can be shown in both layouts."""
        return bool(self.text.strip())

    def text_in(self, fmt):
        if fmt != "paragraphs" or not self.text.strip():
            return self.text
        # Older entries kept one layout; rebuild the other from the text.
        return self.paragraphs or reflow_text(self.text)

    @property
    def created_local(self):
        try:
            return datetime.fromisoformat(self.created).astimezone()
        except (TypeError, ValueError):
            return None

    @property
    def title(self):
        for line in self.text.splitlines():
            if line.strip():
                return line.strip()
        if self.codes:
            return f"{self.codes[0][0]}: {self.codes[0][1]}"
        return "Empty result"


def _entry_from_json(item):
    """A HistoryEntry from stored JSON, or None if the item is unusable.
    Wrongly typed fields fall back to their defaults instead of failing."""
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        return None
    fields = {"text": item["text"][:MAX_TEXT]}
    expected = {"paragraphs": str, "word_count": int, "confidence": (int, float), "languages": list,
                "source": str, "id": str, "created": str}
    for key, kind in expected.items():
        value = item.get(key)
        if isinstance(value, kind) and not isinstance(value, bool):
            fields[key] = value
    codes = item.get("codes")
    if isinstance(codes, list):
        fields["codes"] = [[str(c[0]), str(c[1])] for c in codes
                           if isinstance(c, list) and len(c) == 2]
    if not fields["text"].strip() and not fields.get("codes"):
        return None
    return HistoryEntry(**fields)


class History:
    def __init__(self, path=HISTORY_FILE, max_entries=MAX_ENTRIES):
        self.path = Path(path)
        self.max_entries = max_entries
        self._entries = None
        self._listeners = []

    # ------------------------------------------------------------- queries
    def entries(self):
        """Newest first."""
        if self._entries is None:
            self._entries = self._load()
        return list(self._entries)

    def __len__(self):
        return len(self.entries())

    def connect(self, callback):
        self._listeners.append(callback)

    def disconnect(self, callback):
        if callback in self._listeners:
            self._listeners.remove(callback)

    # ----------------------------------------------------------- mutations
    def add(self, entry):
        if not entry.text.strip() and not entry.codes:
            return None
        entry.text = entry.text[:MAX_TEXT]
        entry.paragraphs = entry.paragraphs[:MAX_TEXT]
        entries = [e for e in self.entries() if e.text != entry.text or e.codes != entry.codes]
        self._entries = ([entry] + entries)[: self.max_entries]
        self._save()
        return entry

    def remove(self, entry_id):
        self._entries = [e for e in self.entries() if e.id != entry_id]
        self._save()

    def restore(self, entries):
        """Put back a previous list of entries."""
        self._entries = list(entries)[: self.max_entries]
        self._save()

    def reinsert(self, removed):
        """Undo a removal: put back ``(position, entry)`` pairs where they
        were, keeping anything added or removed since."""
        entries = self.entries()
        present = {e.id for e in entries}
        for index, entry in sorted(removed, key=lambda pair: pair[0]):
            if entry.id not in present:
                entries.insert(min(index, len(entries)), entry)
                present.add(entry.id)
        self._entries = entries[: self.max_entries]
        self._save()

    def clear(self):
        self._entries = []
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._notify()

    # --------------------------------------------------------------- disk
    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return []
        items = raw.get("entries", []) if isinstance(raw, dict) else []
        entries = [entry for entry in map(_entry_from_json, items if isinstance(items, list) else [])
                   if entry is not None]
        return entries[: self.max_entries]

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # mkdir leaves an existing folder as it is (installers create it
            # world-readable); history is private, so tighten it.
            os.chmod(self.path.parent, 0o700)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "entries": [asdict(e) for e in self._entries]},
                          fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass
        self._notify()

    def _notify(self):
        for callback in list(self._listeners):
            callback()
