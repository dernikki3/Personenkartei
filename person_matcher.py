"""
person_matcher.py
─────────────────
Mapped einen extrahierten Namen aus einem Kalendereintrag auf eine
Person ODER Gruppe in der Kartei-Datenbank.

Matching-Strategie (Reihenfolge):
  1. Blacklist-Treffer           (→ status='blacklisted', wird ignoriert)
  2. Exakter Treffer auf Gruppe  (case-insensitive, group_name)
  3. Exakter Namens-Treffer      (auf Person)
  4. Alias-Treffer               (gespeicherte Aliasse in persons.aliases)
  5. Vorname-Treffer             (erster Token des Personen-Namens)
  6. Fuzzy-Treffer               (Levenshtein ≤ 2, nur als Hinweis)

Rückgabe:
  MatchResult mit status IN {'exact', 'alias', 'firstname', 'group',
                              'blacklisted', 'ambiguous', 'not_found'}

Personen-Treffer liefern matched.kind == 'person', Gruppen 'group'.
"""

from __future__ import annotations
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Datenklassen
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PersonRecord:
    id:       int
    name:     str
    category: str
    aliases:  list[str] = field(default_factory=list)
    kind:     str       = 'person'      # 'person' oder 'group'

    @property
    def firstname(self) -> str:
        """Erster Token des Namens."""
        return self.name.strip().split()[0] if self.name.strip() else ''

    @property
    def all_identifiers(self) -> list[str]:
        """Alle Identifikatoren: vollständiger Name, Vorname, alle Aliasse."""
        ids = [self.name.strip(), self.firstname]
        ids.extend(self.aliases)
        return [i for i in ids if i]


@dataclass
class MatchResult:
    query:       str
    status:      str                           = 'not_found'
    # status: 'exact'|'alias'|'firstname'|'group'|'blacklisted'|'ambiguous'|'not_found'
    candidates:  list[PersonRecord]            = field(default_factory=list)
    matched:     Optional[PersonRecord]        = None
    fuzzy_hints: list[tuple[PersonRecord, int]] = field(default_factory=list)

    @property
    def needs_user_input(self) -> bool:
        return self.status in ('ambiguous', 'not_found')

    @property
    def is_resolved(self) -> bool:
        return self.matched is not None

    @property
    def is_blacklisted(self) -> bool:
        return self.status == 'blacklisted'


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    """Lowercase + Whitespace-Normalisierung."""
    return re.sub(r'\s+', ' ', (s or '').strip().lower())


def _levenshtein(a: str, b: str) -> int:
    """Berechnet die Levenshtein-Editierdistanz zwischen zwei Strings."""
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i]
        for j, cb in enumerate(b, 1):
            ins  = curr[j - 1] + 1
            dlt  = prev[j] + 1
            sub  = prev[j - 1] + (0 if ca == cb else 1)
            curr.append(min(ins, dlt, sub))
        prev = curr
    return prev[-1]


# ─────────────────────────────────────────────────────────────────────────────
# Blacklist
# ─────────────────────────────────────────────────────────────────────────────

class Blacklist:
    """
    Verwaltet Namen, die beim Kalender-Sync IMMER ignoriert werden sollen
    (z. B. "Kuckuck", "Hautarzt", …).

    Gespeichert in der Tabelle calendar_blacklist (siehe Migration in
    personenkartei.py).
    """

    def __init__(self, con: sqlite3.Connection):
        self.con = con
        self._ensure_table()
        self._cache: set[str] = set()
        self.reload()

    def _ensure_table(self) -> None:
        cur = self.con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS calendar_blacklist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TEXT
        )""")
        self.con.commit()

    def reload(self) -> None:
        cur = self.con.cursor()
        cur.execute("SELECT name FROM calendar_blacklist")
        self._cache = {_normalize(r[0]) for r in cur.fetchall()}

    def contains(self, name: str) -> bool:
        return _normalize(name) in self._cache

    def add(self, name: str) -> None:
        from datetime import datetime
        cleaned = (name or '').strip()
        if not cleaned:
            return
        cur = self.con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO calendar_blacklist (name, created_at) VALUES (?, ?)",
            (cleaned, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        self.con.commit()
        self.reload()

    def remove(self, name: str) -> None:
        cur = self.con.cursor()
        cur.execute("DELETE FROM calendar_blacklist WHERE LOWER(name)=LOWER(?)", (name,))
        self.con.commit()
        self.reload()

    def all(self) -> list[str]:
        cur = self.con.cursor()
        cur.execute("SELECT name FROM calendar_blacklist ORDER BY name")
        return [r[0] for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Haupt-Matcher
# ─────────────────────────────────────────────────────────────────────────────

class PersonMatcher:
    """
    Lädt alle Personen UND Gruppen aus der DB und bietet match_name() an.

    Verwendung:
        matcher = PersonMatcher(con)
        result  = matcher.match_name("Anna")
        if result.is_blacklisted:
            # → einfach überspringen
        elif result.needs_user_input:
            # Nutzer-Dialog öffnen
            ...
    """

    def __init__(self, con: sqlite3.Connection,
                 blacklist: Optional[Blacklist] = None):
        self.con       = con
        self.blacklist = blacklist or Blacklist(con)
        self._persons: list[PersonRecord] = []
        self._groups:  list[PersonRecord] = []
        self.reload()

    def reload(self) -> None:
        """Lädt Personen-, Gruppen- und Blacklist-Daten neu."""
        self._persons = self._load_persons()
        self._groups  = self._load_groups()
        self.blacklist.reload()

    def _load_persons(self) -> list[PersonRecord]:
        cur = self.con.cursor()
        # Nur "echte" Personen: keine Gruppen, nicht gelöscht.
        # Wir schützen uns gegen fehlende Spalten ('deleted'/'is_group') falls
        # die DB ein altes Schema hat.
        cur.execute("PRAGMA table_info(persons)")
        cols = {r[1] for r in cur.fetchall()}
        where = []
        if 'is_group' in cols: where.append("COALESCE(is_group,0) = 0")
        if 'deleted'  in cols: where.append("COALESCE(deleted,0) = 0")
        where_sql = " AND ".join(where) if where else "1=1"

        cur.execute(f"""
            SELECT id, name, category,
                   COALESCE(aliases, '') AS aliases
            FROM persons
            WHERE {where_sql}
            ORDER BY name
        """)
        records = []
        for pid, name, category, aliases_raw in cur.fetchall():
            aliases = [a.strip() for a in (aliases_raw or '').split(',') if a.strip()]
            records.append(PersonRecord(
                id=pid, name=name, category=category or '',
                aliases=aliases, kind='person'
            ))
        return records

    def _load_groups(self) -> list[PersonRecord]:
        """Lädt alle Gruppen aus persons WHERE is_group=1.

        Hinweis: Im aktuellen Schema werden Gruppen als Einträge in der
        persons-Tabelle mit is_group=1 gespeichert. Die separate
        groups-Tabelle ist Karteileiche und wird nicht verwendet.
        Aliasse werden wie bei Personen aus persons.aliases gelesen.
        """
        cur = self.con.cursor()
        cur.execute("PRAGMA table_info(persons)")
        cols = {r[1] for r in cur.fetchall()}
        if 'is_group' not in cols:
            return []
        where = ["COALESCE(is_group,0) = 1"]
        if 'deleted' in cols:
            where.append("COALESCE(deleted,0) = 0")
        where_sql = " AND ".join(where)

        cur.execute(f"""
            SELECT id, name, COALESCE(category,'group'),
                   COALESCE(aliases,'') AS aliases
            FROM persons
            WHERE {where_sql}
            ORDER BY name
        """)
        records = []
        for gid, gname, gtype, aliases_raw in cur.fetchall():
            aliases = [a.strip() for a in (aliases_raw or '').split(',') if a.strip()]
            records.append(PersonRecord(
                id=gid, name=gname, category=gtype,
                aliases=aliases, kind='group'
            ))
        return records

    def all_persons(self) -> list[PersonRecord]:
        return list(self._persons)

    def all_groups(self) -> list[PersonRecord]:
        return list(self._groups)

    def match_name(self, query: str) -> MatchResult:
        """
        Versucht query einer Person oder Gruppe zuzuordnen.

        Reihenfolge:
          0. Blacklist-Treffer → status='blacklisted'
          1. Exakter Gruppen-Treffer (auto-resolve)
          2. Exakter Personen-Treffer auf Name oder Alias
          3. Vorname-Treffer (eindeutig)
          4. Fuzzy-Treffer als Hinweis
          5. not_found / ambiguous
        """
        q_norm = _normalize(query)
        result = MatchResult(query=query)

        # ── Schritt 0: Blacklist ─────────────────────────────────────────────
        if self.blacklist.contains(query):
            result.status = 'blacklisted'
            return result

        # ── Schritt 1: Gruppen-Match (case-insensitive, über Name + Aliase) ─
        group_hits: list[PersonRecord] = []
        for g in self._groups:
            identifiers_norm = [_normalize(i) for i in g.all_identifiers]
            if q_norm in identifiers_norm:
                group_hits.append(g)
        if len(group_hits) == 1:
            result.status     = 'group'
            result.matched    = group_hits[0]
            result.candidates = group_hits
            return result
        if len(group_hits) > 1:
            result.status     = 'ambiguous'
            result.candidates = group_hits
            return result

        # ── Schritt 2: Exakter Personen-Treffer (Name oder Alias) ────────────
        exact: list[PersonRecord] = []
        for p in self._persons:
            identifiers_norm = [_normalize(i) for i in p.all_identifiers]
            if q_norm in identifiers_norm:
                exact.append(p)

        if len(exact) == 1:
            result.status     = 'exact'
            result.matched    = exact[0]
            result.candidates = exact
            return result

        if len(exact) > 1:
            # Prüfe ob auch der vollständige Name exakt passt → eindeutig
            full_match = [p for p in exact if _normalize(p.name) == q_norm]
            if len(full_match) == 1:
                result.status     = 'exact'
                result.matched    = full_match[0]
                result.candidates = full_match
                return result
            result.status     = 'ambiguous'
            result.candidates = exact
            return result

        # ── Schritt 3: Alias-Treffer (separate Prüfung, case-insensitive) ────
        alias_hits: list[PersonRecord] = []
        for p in self._persons:
            for alias in p.aliases:
                if _normalize(alias) == q_norm:
                    alias_hits.append(p)
                    break

        if len(alias_hits) == 1:
            result.status     = 'alias'
            result.matched    = alias_hits[0]
            result.candidates = alias_hits
            return result
        if len(alias_hits) > 1:
            result.status     = 'ambiguous'
            result.candidates = alias_hits
            return result

        # ── Schritt 4: Vorname-Treffer ───────────────────────────────────────
        firstname_hits: list[PersonRecord] = []
        for p in self._persons:
            if _normalize(p.firstname) == q_norm:
                firstname_hits.append(p)

        if len(firstname_hits) == 1:
            result.status     = 'firstname'
            result.matched    = firstname_hits[0]
            result.candidates = firstname_hits
            return result
        if len(firstname_hits) > 1:
            result.status     = 'ambiguous'
            result.candidates = firstname_hits
            return result

        # ── Schritt 5: Fuzzy (Levenshtein ≤ 2, nur als Hinweis) ──────────────
        fuzzy: list[tuple[PersonRecord, int]] = []
        for p in self._persons:
            min_dist = min(
                _levenshtein(q_norm, _normalize(i))
                for i in p.all_identifiers
            )
            if min_dist <= 2:
                fuzzy.append((p, min_dist))

        fuzzy.sort(key=lambda x: x[1])
        result.status      = 'not_found'
        result.fuzzy_hints = fuzzy[:3]
        return result

    def add_alias(self, person_id: int, alias: str) -> None:
        """Fügt einer Person einen neuen Alias hinzu."""
        cur = self.con.cursor()
        cur.execute("SELECT aliases FROM persons WHERE id=?", (person_id,))
        row = cur.fetchone()
        if not row:
            return
        existing = [a.strip() for a in (row[0] or '').split(',') if a.strip()]
        if alias.strip() not in existing:
            existing.append(alias.strip())
            cur.execute("UPDATE persons SET aliases=? WHERE id=?",
                        (','.join(existing), person_id))
            self.con.commit()
        self.reload()

    def create_person(self, name: str, alias: str | None = None) -> PersonRecord:
        """Legt eine neue Person in der DB an und gibt sie zurück."""
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        aliases_str = alias or ''
        cur = self.con.cursor()
        cur.execute("""
            INSERT INTO persons (name, category, aliases, created_at)
            VALUES (?, 'Bekannte', ?, ?)
        """, (name.strip(), aliases_str, now))
        self.con.commit()
        new_id = cur.lastrowid
        self.reload()
        return next(p for p in self._persons if p.id == new_id)


# ─────────────────────────────────────────────────────────────────────────────
# Selbsttest
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import sqlite3, tempfile, os

    tmp = tempfile.mktemp(suffix='.db')
    con = sqlite3.connect(tmp)
    con.execute("""CREATE TABLE persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, category TEXT, aliases TEXT DEFAULT '',
        is_group INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    con.executemany(
        "INSERT INTO persons (name, category, aliases, is_group) VALUES (?,?,?,?)", [
            ("Anna Schmidt",   "Freund",   "Anna,Annie",  0),
            ("Max Müller",     "Freund",   "Max",         0),
            ("Julius Berger",  "Familie",  "Juli",        0),
            ("Anna Braun",     "Bekannte", "",            0),
            ("Team Geo",       "team",     "TG,GeoTeam",  1),
            ("Projekt X",      "project",  "PX",          1),
        ])
    con.commit()

    matcher = PersonMatcher(con)
    matcher.blacklist.add("Kuckuck")
    matcher.blacklist.add("Hautarzt")
    matcher.reload()

    for q in ["Anna", "max", "Julius", "Juli", "Tina", "Ans",
              "Team Geo", "team geo", "TG", "GeoTeam", "Projekt X", "PX",
              "Kuckuck", "Hautarzt"]:
        r = matcher.match_name(q)
        print(f"'{q}' → {r.status:13s}", end='')
        if r.is_blacklisted:
            print(" (BLACKLISTED)")
        elif r.matched:
            print(f" {r.matched.name}  [{r.matched.kind}]")
        elif r.candidates:
            print(f" [{', '.join(p.name for p in r.candidates)}] (mehrdeutig)")
        elif r.fuzzy_hints:
            print(f" Fuzzy: {[p.name for p,_ in r.fuzzy_hints]}")
        else:
            print(" (nicht gefunden)")

    con.close()
    os.unlink(tmp)
