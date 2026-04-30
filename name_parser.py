"""
name_parser.py
──────────────
Parst Kalendereintrags-Titel im Format:
    "Anna, Max & Julius: Café am Neckar"
    "Lena und Tom: Spaziergang am See"
    "Evtl. Anni: Kaffee"            (→ "Anni" wird erkannt, "Evtl." entfernt)
    "ABGESAGT Anni: Kaffee"         (→ wird ignoriert)

Gibt zurück: ParsedEvent mit
  - persons:        List[str]
  - treffen_titel:  str
  - raw_title:      str          (immer der vollständige Originaltitel)
  - is_valid:       bool
  - skip_reason:    str
  - cancelled:      bool         (True wenn ABGESAGT erkannt)

Behandelte Sonderfälle
──────────────────────
- ABGESAGT (case-insensitive) → Event wird ignoriert, auch mit Doppelpunkt
- Kein Doppelpunkt → ignorieren
- Leerer linker Teil → ignorieren
- Mehrfacher Doppelpunkt → nur am ersten splitten
- Trennzeichen: Komma, &, " und ", " +", "/"
- "Evtl. " / "evtl " als Präfix vor Personennamen wird entfernt
  (Platzhalter, keine Person/Alias)
- Whitespace-Normalisierung
- Optionale Orts-Extraktion aus dem Titel (Heuristik)
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


# Wörter, die auf einen Ort hindeuten (für Orts-Heuristik)
_LOCATION_HINTS = frozenset([
    "café", "cafe", "restaurant", "bar", "bistro", "kneipe", "biergarten",
    "see", "park", "strand", "markt", "museum", "kino", "theater",
    "platz", "brücke", "ufer", "garten", "schloss", "burg",
    "am", "beim", "im", "an der", "in der",
])

# Trennzeichen zwischen Personennamen.
# \bund\b nur einmal (Bug-Fix), Reihenfolge im Regex egal.
_SEPARATOR_RE = re.compile(
    r'\s*(?:&amp;|&|\bund\b|\+|,|/)\s*',
    re.IGNORECASE | re.UNICODE,
)

# Regex zum Erkennen / Entfernen des "Evtl."-Platzhalters am Namens-Anfang.
# Erkennt: "Evtl.", "Evtl ", "evtl.", "EVTL.", auch mit mehreren Leerzeichen.
_EVTL_PREFIX_RE = re.compile(
    r'^\s*evtl\.?\s+',
    re.IGNORECASE,
)

# Regex zum Erkennen von "ABGESAGT" irgendwo im Titel
# (case-insensitive, ganzes Wort).
_CANCELLED_RE = re.compile(
    r'\babgesagt\b',
    re.IGNORECASE,
)


@dataclass
class ParsedEvent:
    """Ergebnis der Titel-Analyse.

    raw_title bleibt IMMER der ungekürzte Originaltitel (für Logs/Anzeige).
    """
    raw_title:      str
    persons:        list[str]  = field(default_factory=list)
    treffen_titel:  str        = ""
    suggested_ort:  str | None = None
    is_valid:       bool       = True
    skip_reason:    str        = ""
    cancelled:      bool       = False


def parse_event_title(title: str) -> ParsedEvent:
    """
    Hauptfunktion: Parst einen Kalender-Titel.

    >>> r = parse_event_title("Anna, Max & Julius: Café am Neckar")
    >>> r.persons
    ['Anna', 'Max', 'Julius']
    >>> r.treffen_titel
    'Café am Neckar'

    >>> r = parse_event_title("ABGESAGT Anni: Kaffee")
    >>> r.is_valid
    False
    >>> r.cancelled
    True

    >>> r = parse_event_title("Evtl. Anni: Kaffee")
    >>> r.persons
    ['Anni']
    """
    raw = (title or "").strip()
    result = ParsedEvent(raw_title=raw)

    # ── ABGESAGT-Check (vor allem anderen) ───────────────────────────────────
    if _CANCELLED_RE.search(raw):
        result.is_valid    = False
        result.cancelled   = True
        result.skip_reason = "Termin abgesagt"
        return result

    # ── Validierung: Doppelpunkt vorhanden? ──────────────────────────────────
    if ':' not in raw:
        result.is_valid    = False
        result.skip_reason = "Kein Doppelpunkt im Titel"
        return result

    # ── Split am ERSTEN Doppelpunkt ──────────────────────────────────────────
    left, _, right = raw.partition(':')
    left  = left.strip()
    right = right.strip()

    if not left:
        result.is_valid    = False
        result.skip_reason = "Leerer Personenteil (links vom Doppelpunkt)"
        return result

    if not right:
        result.is_valid    = False
        result.skip_reason = "Leerer Titelanteil (rechts vom Doppelpunkt)"
        return result

    # ── Personennamen extrahieren ────────────────────────────────────────────
    raw_names = _SEPARATOR_RE.split(left)
    persons: list[str] = []
    for n in raw_names:
        cleaned = _normalize_name(n)
        if cleaned:
            persons.append(cleaned)

    if not persons:
        result.is_valid    = False
        result.skip_reason = "Keine Personennamen gefunden"
        return result

    result.persons       = persons
    result.treffen_titel = right

    # ── Optionale Orts-Heuristik ─────────────────────────────────────────────
    result.suggested_ort = _extract_suggested_location(right)

    return result


def _normalize_name(raw: str) -> str:
    """
    Bereinigt einen einzelnen Namen:
    - Entfernt überflüssige Leerzeichen
    - Entfernt "Evtl." / "evtl " als Präfix (Platzhalter, keine Person)
    - Entfernt Sonderzeichen am Rand
    - Capitalizes ersten Buchstaben
    """
    name = raw.strip()
    if not name:
        return ''

    # "Evtl. Anni" → "Anni"  (auch wenn jemand "Evtl. Evtl. Anni" tippt)
    while True:
        new = _EVTL_PREFIX_RE.sub('', name)
        if new == name:
            break
        name = new.strip()

    name = name.strip('.,;-–')
    name = re.sub(r'\s+', ' ', name)
    if not name:
        return ''
    # Ersten Buchstaben groß, Rest unverändert (Nachname-Zusammensetzungen)
    return name[0].upper() + name[1:] if len(name) > 1 else name.upper()


def _extract_suggested_location(title: str) -> str | None:
    """
    Heuristik: Enthält der Titelteil Ortsbegriffe?
    Gibt den gesamten Titel als Ort-Vorschlag zurück, wenn ein Hinweis
    gefunden wird – der Nutzer kann entscheiden.

    Nur aktiviert wenn der Titel kurz genug ist (≤ 50 Zeichen).
    """
    if len(title) > 50:
        return None
    tl = title.lower()
    for hint in _LOCATION_HINTS:
        if hint in tl:
            return title.strip()
    return None


# ── Batch-Verarbeitung ────────────────────────────────────────────────────────
def parse_events_batch(titles: list[str]) -> list[ParsedEvent]:
    """Parst eine Liste von Kalender-Titeln und gibt alle Ergebnisse zurück."""
    return [parse_event_title(t) for t in titles]


def filter_valid(events: list[ParsedEvent]) -> list[ParsedEvent]:
    """Gibt nur gültige (parsbare) Events zurück."""
    return [e for e in events if e.is_valid]


# ── Selbsttest ────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    testcases = [
        "Anna, Max & Julius: Café am Neckar",
        "Lena und Tom: Spaziergang am See",
        "Maria / Peter + Klaus: Abendessen Restaurant Zur Linde",
        "Kein Doppelpunkt vorhanden",
        ": Leerer linker Teil",
        "Anna: ",
        "Treffen mit Anna, Bea & Clara: Kino Metropol",
        "Anna, Max & Julius: sehr langer titel der über 50 zeichen hinausgeht und keinen ort enthält",
        "ABGESAGT Anni: Kaffee",
        "Anni: Kaffee ABGESAGT",
        "Evtl. Anni: Kaffee",
        "evtl Markus, Anna: Kino",
        "Evtl. Anni & Evtl. Markus: Bar",
    ]
    for tc in testcases:
        r = parse_event_title(tc)
        if r.cancelled:
            status = "🚫 (abgesagt)"
        elif r.is_valid:
            status = "✅"
        else:
            status = f"⛔ ({r.skip_reason})"
        print(f"{status:30s} {tc!r}")
        if r.is_valid:
            print(f"   Personen : {r.persons}")
            print(f"   Titel    : {r.treffen_titel}")
            if r.suggested_ort:
                print(f"   Ort?     : {r.suggested_ort}")
        print()
