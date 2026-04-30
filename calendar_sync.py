"""
calendar_sync.py
────────────────
Google-Kalender-Synchronisation für die PersonenKartei.

Ablauf:
  1. OAuth2 Desktop-Flow → Token cachen in token.json
  2. Events im Zeitraum [-months_back, +months_forward] laden
     (nur Events mit ':' im Titel)
  3. Für jedes Event: name_parser → person_matcher
     - Cancelled (ABGESAGT) → übersprungen
     - Blacklisted Namen   → übersprungen
     - Gruppen-Match exakt → automatisch zugeordnet
     - Bestehender manueller Eintrag mit identischem Datum:
         → Merge-Dialog (Ort/Themen ergänzen), kein Duplikat
  4. Konflikte → Qt-Dialoge (PersonResolveDialog)
  5. Treffen werden mit is_draft=1 + quelle='kalender' eingetragen
  6. last_contact wird nie auf ein zukünftiges Datum gesetzt
  7. Zukunfts-Gruppen-Treffen → pending_attendance=1 (UI fragt später nach)
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone, date
from typing import Callable, Optional

# Interne Module
from name_parser    import parse_event_title, ParsedEvent
from person_matcher import PersonMatcher, MatchResult, PersonRecord, Blacklist

# ── Google-API Credentials-Datei ─────────────────────────────────────────────
CREDENTIALS_FILE = "google_credentials.json"
TOKEN_FILE       = "google_token.json"
SCOPES           = ["https://www.googleapis.com/auth/calendar.readonly"]


# ── Daten-Klassen ─────────────────────────────────────────────────────────────

@dataclass
class CalendarEvent:
    google_id:   str
    title:       str
    start:       datetime
    end:         datetime | None
    location:    str | None
    is_all_day:  bool
    parsed:      ParsedEvent | None = None


@dataclass
class MergeProposal:
    """Wird an den Merge-Callback übergeben, wenn ein bestehender manueller
    Eintrag denselben Person+Datum-Schlüssel hat. Keine Information aus dem
    bestehenden Eintrag wird überschrieben."""
    existing_shared_id:  int
    existing_title:      str
    existing_content:    str
    existing_date:       str
    person_id:           int
    person_name:         str
    new_event_title:     str
    new_treffen_titel:   str
    new_ort:             str | None


@dataclass
class GroupAttendanceRequest:
    """Wird an den UI-Callback gegeben, wenn ein Gruppen-Kalender-Eintrag
    in der Vergangenheit liegt: 'wer war dabei?'"""
    group_id:        int
    group_name:      str
    member_ids:      list[int]   # alle aktuellen Mitglieder
    member_names:    list[str]   # parallel zu member_ids
    event_title:     str
    event_date:      str         # YYYY-MM-DD


@dataclass
class SyncResult:
    imported:           int = 0
    skipped_duplicate:  int = 0
    skipped_invalid:    int = 0
    skipped_cancelled:  int = 0
    skipped_blacklist:  int = 0
    new_persons:        int = 0
    manual_mappings:    int = 0
    group_mappings:     int = 0
    merged:             int = 0
    pending_attendance: int = 0
    errors:             list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"✅ {self.imported} Treffen importiert (als Drafts)",
            f"📁 {self.group_mappings} Gruppen-Treffen",
            f"⏳ {self.pending_attendance} Gruppen-Termine warten auf Anwesenheit",
            f"🔄 {self.merged} mit bestehenden Einträgen ergänzt",
            f"👤 {self.new_persons} neue Personen angelegt",
            f"🔗 {self.manual_mappings} manuell zugeordnet",
            f"⏭️  {self.skipped_duplicate} bereits vorhanden",
            f"🚫 {self.skipped_cancelled} abgesagt",
            f"🙈 {self.skipped_blacklist} auf Blacklist",
            f"⛔ {self.skipped_invalid} ignoriert",
        ]
        if self.errors:
            lines.append(f"⚠️  {len(self.errors)} Fehler")
        return "\n".join(lines)


# ── Google-Auth ───────────────────────────────────────────────────────────────

def _check_google_libs() -> bool:
    try:
        import googleapiclient   # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        return True
    except ImportError:
        return False


def _get_google_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Keine Google-Credentials gefunden.\n"
                    f"Bitte '{CREDENTIALS_FILE}' im Programmordner ablegen.\n\n"
                    "Anleitung:\n"
                    "1. console.cloud.google.com → Neues Projekt\n"
                    "2. APIs & Dienste → Google Calendar API aktivieren\n"
                    "3. Anmeldedaten → OAuth-Client-ID (Desktop) erstellen\n"
                    "4. JSON herunterladen und als 'google_credentials.json' speichern"
                )
            flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)


# ── Event-Laden ───────────────────────────────────────────────────────────────

def _fetch_events(
    service,
    months_back:    int = 12,
    months_forward: int = 0,
) -> list[CalendarEvent]:
    """Lädt Events im Zeitfenster [-months_back, +months_forward]."""
    now          = datetime.now(timezone.utc)
    time_min     = now - timedelta(days=months_back * 31)
    time_min_str = time_min.isoformat()
    time_max_str = (
        (now + timedelta(days=months_forward * 31)).isoformat()
        if months_forward > 0 else None
    )

    events_raw = []
    page_token = None

    while True:
        params = dict(
            calendarId   = 'primary',
            timeMin      = time_min_str,
            singleEvents = True,
            orderBy      = 'startTime',
            maxResults   = 250,
        )
        if time_max_str:
            params['timeMax'] = time_max_str
        if page_token:
            params['pageToken'] = page_token

        response  = service.events().list(**params).execute()
        items     = response.get('items', [])
        events_raw.extend(items)

        page_token = response.get('nextPageToken')
        if not page_token:
            break

    result: list[CalendarEvent] = []
    for ev in events_raw:
        title = (ev.get('summary') or '').strip()
        if ':' not in title:
            continue

        gid      = ev.get('id', '')
        location = (ev.get('location') or '').strip() or None

        start_raw  = ev.get('start', {})
        is_all_day = 'date' in start_raw and 'dateTime' not in start_raw

        if is_all_day:
            start_str = start_raw.get('date', '')
            try:
                start = datetime.strptime(start_str, "%Y-%m-%d")
            except ValueError:
                continue
        else:
            start_str = start_raw.get('dateTime', '')
            try:
                start = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
                start = start.replace(tzinfo=None)
            except ValueError:
                continue

        result.append(CalendarEvent(
            google_id  = gid,
            title      = title,
            start      = start,
            end        = None,
            location   = location,
            is_all_day = is_all_day,
        ))

    return result


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _is_duplicate(cur: sqlite3.Cursor, google_event_id: str) -> bool:
    """Prüft ob ein Event bereits importiert wurde
    (via shared_entries.google_calendar_event_id)."""
    cur.execute("""
        SELECT COUNT(*) FROM shared_entries
        WHERE google_calendar_event_id = ?
    """, (google_event_id,))
    return cur.fetchone()[0] > 0


def _ensure_columns(con: sqlite3.Connection) -> None:
    """Idempotent: stellt sicher, dass die nötigen Zusatzspalten existieren.
    Wird in personenkartei.py auch in der Migration gesetzt — hier als
    Sicherheitsnetz."""
    cur = con.cursor()
    cur.execute("PRAGMA table_info(shared_entries)")
    cols = {r[1] for r in cur.fetchall()}
    additions = [
        ('google_calendar_event_id', 'TEXT'),
        ('pending_attendance',       'INTEGER DEFAULT 0'),
        ('group_id',                 'INTEGER'),
        ('quelle',                   "TEXT DEFAULT 'manuell'"),
        ('is_draft',                 'INTEGER DEFAULT 0'),
    ]
    for name, dtype in additions:
        if name not in cols:
            cur.execute(f"ALTER TABLE shared_entries ADD COLUMN {name} {dtype}")
    con.commit()


def _today_iso() -> str:
    return date.today().strftime("%Y-%m-%d")


def _is_future(date_iso: str) -> bool:
    """True wenn date_iso > heute."""
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    except ValueError:
        return False
    return d > date.today()


def _capped_for_last_contact(date_iso: str) -> str | None:
    """Gibt date_iso zurück, wenn es ≤ heute ist; sonst None.
    last_contact darf nie in der Zukunft liegen (Punkt 2)."""
    if _is_future(date_iso):
        return None
    return date_iso


def _find_existing_manual_entry(
    cur: sqlite3.Cursor,
    person_id: int,
    edate: str,
) -> Optional[tuple[int, str, str]]:
    """Sucht einen bestehenden manuellen Treffen-Eintrag derselben Person am
    gleichen Datum (Punkt 4).

    Rückgabe: (shared_entry_id, title, content) oder None.
    Manuell heißt: quelle != 'kalender' (also 'manuell', NULL oder anderes).
    """
    cur.execute("""
        SELECT se.id, se.title, se.content
        FROM entries e
        JOIN shared_entries se ON e.shared_entry_id = se.id
        WHERE e.person_id = ?
          AND se.entry_date = ?
          AND COALESCE(se.quelle, 'manuell') != 'kalender'
          AND COALESCE(se.type, '') IN ('Treffen', 'Telefonat')
        ORDER BY se.id DESC
        LIMIT 1
    """, (person_id, edate))
    row = cur.fetchone()
    return tuple(row) if row else None


def _parse_treffen_content(content: str) -> dict:
    """Zerlegt einen gespeicherten Treffen-Content im Format
    'Ort: ... | Themen: ... | Ergebnis/Gefühl: ...' in ein Dict."""
    parts = {"Ort": "", "Themen": "", "Ergebnis/Gefühl": ""}
    for piece in (content or '').split('|'):
        if ':' in piece:
            k, v = piece.split(':', 1)
            k = k.strip()
            if k in parts:
                parts[k] = v.strip()
    return parts


def _format_treffen_content(d: dict) -> str:
    return (f"Ort: {d.get('Ort','')} | "
            f"Themen: {d.get('Themen','')} | "
            f"Ergebnis/Gefühl: {d.get('Ergebnis/Gefühl','')}")


def _merge_into_existing(
    con: sqlite3.Connection,
    existing_shared_id: int,
    new_ort: str | None,
    new_themen: str,
) -> None:
    """Ergänzt LEERE Felder im bestehenden Eintrag mit neuen Werten.
    Bestehende Werte werden nie überschrieben (Punkt 4)."""
    cur = con.cursor()
    cur.execute("SELECT content FROM shared_entries WHERE id=?", (existing_shared_id,))
    row = cur.fetchone()
    if not row:
        return
    parts = _parse_treffen_content(row[0] or '')

    changed = False
    if not parts['Ort'] and (new_ort or ''):
        parts['Ort'] = new_ort or ''
        changed = True
    if not parts['Themen'] and (new_themen or ''):
        parts['Themen'] = new_themen or ''
        changed = True

    if changed:
        new_content = _format_treffen_content(parts)
        cur.execute(
            "UPDATE shared_entries SET content=?, updated_at=? WHERE id=?",
            (new_content, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             existing_shared_id)
        )
        con.commit()


# ── Treffen anlegen (Personen) ────────────────────────────────────────────────

def _create_treffen(
    con:        sqlite3.Connection,
    event:      CalendarEvent,
    parsed:     ParsedEvent,
    person_ids: list[int],
    location:   str | None,
    is_draft:   int = 1,
) -> None:
    """Legt ein Treffen als shared_entry + entries für alle Personen an.
    Standard: is_draft=1 (Punkt 1)."""
    cur   = con.cursor()
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    edate = event.start.strftime("%Y-%m-%d")

    ort_part = location or parsed.suggested_ort or ''
    content  = _format_treffen_content({
        'Ort': ort_part,
        'Themen': parsed.treffen_titel,
        'Ergebnis/Gefühl': '',
    })

    cur.execute(
        f"SELECT name FROM persons WHERE id IN ({','.join('?'*len(person_ids))})",
        person_ids
    )
    participant_names = ", ".join(r[0] for r in cur.fetchall())

    cur.execute("""
        INSERT INTO shared_entries
          (type, title, content, entry_date, created_at, updated_at,
           participants, tags, google_calendar_event_id, quelle, is_draft)
        VALUES ('Treffen', ?, ?, ?, ?, ?, ?, 'google_calendar', ?, 'kalender', ?)
    """, (
        parsed.treffen_titel, content, edate, now, now,
        participant_names, event.google_id, is_draft
    ))
    shared_id = cur.lastrowid

    capped = _capped_for_last_contact(edate)
    for pid in person_ids:
        cur.execute("""
            INSERT INTO entries (person_id, shared_entry_id, hidden, pinned)
            VALUES (?, ?, 0, 0)
        """, (pid, shared_id))
        # last_contact NIE auf Zukunfts-Datum setzen (Punkt 2)
        if capped:
            cur.execute("""
                UPDATE persons SET last_contact=?
                WHERE id=? AND (last_contact IS NULL OR last_contact < ?)
            """, (capped, pid, capped))

    con.commit()


# ── Gruppen-Treffen anlegen ───────────────────────────────────────────────────

def _create_group_treffen(
    con:        sqlite3.Connection,
    event:      CalendarEvent,
    parsed:     ParsedEvent,
    group_id:   int,
    member_ids: list[int],
    location:   str | None,
    is_draft:   int = 1,
    pending_attendance: int = 0,
) -> int:
    """Legt ein Gruppen-Treffen für die übergebenen Mitglieder an.
    Wenn pending_attendance=1 → kein Eintrag pro Mitglied (passiert später,
    wenn der Nutzer die Anwesenheit bestätigt). Sonst: Eintrag pro Mitglied
    in member_ids.

    Rückgabe: shared_entry_id
    """
    cur   = con.cursor()
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    edate = event.start.strftime("%Y-%m-%d")

    cur.execute("SELECT name FROM persons WHERE id=?", (group_id,))
    grow = cur.fetchone()
    group_name = grow[0] if grow else f"Gruppe #{group_id}"

    # Teilnehmer-String: bei pending = "Gruppe (Anwesenheit ausstehend)"
    if pending_attendance:
        participants = f"{group_name} (Anwesenheit ausstehend)"
    else:
        cur.execute(
            f"SELECT name FROM persons WHERE id IN ({','.join('?'*len(member_ids))})"
            if member_ids else "SELECT name FROM persons WHERE 0",
            member_ids
        )
        names = [r[0] for r in cur.fetchall()]
        participants = (f"{group_name} ({', '.join(names)})"
                        if names else group_name)

    ort_part = location or parsed.suggested_ort or ''
    content  = _format_treffen_content({
        'Ort': ort_part,
        'Themen': parsed.treffen_titel,
        'Ergebnis/Gefühl': '',
    }) + f" | Gruppe: {group_name}"

    cur.execute("""
        INSERT INTO shared_entries
          (type, title, content, entry_date, created_at, updated_at,
           participants, tags, google_calendar_event_id, quelle,
           is_draft, group_id, pending_attendance)
        VALUES ('Treffen', ?, ?, ?, ?, ?, ?, 'google_calendar,gruppe',
                ?, 'kalender', ?, ?, ?)
    """, (
        parsed.treffen_titel, content, edate, now, now,
        participants, event.google_id, is_draft, group_id, pending_attendance
    ))
    shared_id = cur.lastrowid

    # Eintrag in der Gruppen-Kartei selbst (immer, auch bei pending)
    # Die Gruppe ist als persons-Datensatz mit is_group=1 gespeichert,
    # also können wir einen entries-Eintrag mit person_id=group_id anlegen.
    cur.execute("""
        INSERT INTO entries (person_id, shared_entry_id, hidden, pinned)
        VALUES (?, ?, 0, 0)
    """, (group_id, shared_id))

    if not pending_attendance and member_ids:
        capped = _capped_for_last_contact(edate)
        for pid in member_ids:
            cur.execute("""
                INSERT INTO entries (person_id, shared_entry_id, hidden, pinned)
                VALUES (?, ?, 0, 0)
            """, (pid, shared_id))
            if capped:
                cur.execute("""
                    UPDATE persons SET last_contact=?
                    WHERE id=? AND (last_contact IS NULL OR last_contact < ?)
                """, (capped, pid, capped))

    con.commit()
    return shared_id


# ── Sync-Orchestrierung ───────────────────────────────────────────────────────

class CalendarSyncer:
    """
    Steuert den gesamten Sync-Ablauf.

    resolve_callback(match_result, event_title) -> int | None
        Rückgabewerte:
          int  →  person_id (positiv) ODER  -group_id (negativ)
          NEW_PERSON (-1)   →  neue Person anlegen
          BLACKLIST  (-2)   →  Name auf Blacklist setzen
          None →  ignorieren

    merge_callback(MergeProposal) -> bool
        True  → ergänzen, kein Duplikat anlegen
        False → trotzdem als neuen Eintrag importieren

    group_attendance_callback(GroupAttendanceRequest) -> list[int] | None
        Rückgabe:
          list of person_id  → diese Mitglieder waren dabei
          None                → ignorieren
        Wird NUR für Vergangenheits-Gruppen-Treffen aufgerufen.

    progress_callback(current, total, desc) -> None
    """

    NEW_PERSON = -1
    BLACKLIST  = -2

    def __init__(
        self,
        con:                       sqlite3.Connection,
        months_back:               int = 12,
        months_forward:            int = 0,
        resolve_callback:          Optional[Callable] = None,
        merge_callback:            Optional[Callable] = None,
        group_attendance_callback: Optional[Callable] = None,
        progress_callback:         Optional[Callable] = None,
    ):
        self.con               = con
        self.months_back       = months_back
        self.months_forward    = months_forward
        self.resolve_callback  = resolve_callback  or (lambda r, t: None)
        self.merge_callback    = merge_callback    or (lambda p: True)
        self.group_attendance_callback = (
            group_attendance_callback or (lambda req: req.member_ids)
        )
        self.progress_callback = progress_callback or (lambda a, b, c: None)
        self.blacklist         = Blacklist(con)
        self.matcher           = PersonMatcher(con, blacklist=self.blacklist)
        _ensure_columns(con)

    # ── Hilfsmethoden ────────────────────────────────────────────────────────

    def _resolve_group_members(self, group_id: int) -> tuple[list[int], list[str]]:
        """Aktive Mitglieder einer Gruppe (left_at IS NULL)."""
        cur = self.con.cursor()
        cur.execute("PRAGMA table_info(group_members)")
        cols = {r[1] for r in cur.fetchall()}
        left_filter = " AND (gm.left_at IS NULL OR gm.left_at = '')" if 'left_at' in cols else ""

        cur.execute(f"""
            SELECT gm.person_id, p.name
            FROM group_members gm
            JOIN persons p ON gm.person_id = p.id
            WHERE gm.group_id = ?{left_filter}
            ORDER BY p.name
        """, (group_id,))
        rows = cur.fetchall()
        return [r[0] for r in rows], [r[1] for r in rows]

    def _process_group_event(
        self,
        event:      CalendarEvent,
        parsed:     ParsedEvent,
        group_id:   int,
        result:     'SyncResult',
    ) -> None:
        """Behandelt ein Gruppen-Event: Vergangenheit → Häkchen-Dialog,
        Zukunft → pending_attendance."""
        cur = self.con.cursor()
        cur.execute("SELECT name FROM persons WHERE id=?", (group_id,))
        grow = cur.fetchone()
        group_name = grow[0] if grow else "?"

        member_ids, member_names = self._resolve_group_members(group_id)
        edate = event.start.strftime("%Y-%m-%d")

        if _is_future(edate):
            # Zukunft → pending_attendance, keine Personen-Einträge
            try:
                _create_group_treffen(
                    self.con, event, parsed,
                    group_id, member_ids, event.location,
                    is_draft=1, pending_attendance=1,
                )
                result.imported           += 1
                result.group_mappings     += 1
                result.pending_attendance += 1
            except Exception as e:
                result.errors.append(f"Fehler bei '{event.title}': {e}")
            return

        # Vergangenheit/heute → Häkchen-Dialog
        if not member_ids:
            # Gruppe ohne aktive Mitglieder → trotzdem Eintrag (ohne Personen)
            try:
                _create_group_treffen(
                    self.con, event, parsed,
                    group_id, [], event.location, is_draft=1,
                )
                result.imported       += 1
                result.group_mappings += 1
            except Exception as e:
                result.errors.append(f"Fehler bei '{event.title}': {e}")
            return

        req = GroupAttendanceRequest(
            group_id     = group_id,
            group_name   = group_name,
            member_ids   = member_ids,
            member_names = member_names,
            event_title  = event.title,
            event_date   = edate,
        )
        attended = self.group_attendance_callback(req)

        if attended is None:
            # Nutzer hat ignoriert
            result.skipped_invalid += 1
            return

        try:
            _create_group_treffen(
                self.con, event, parsed,
                group_id, attended, event.location,
                is_draft=1, pending_attendance=0,
            )
            result.imported       += 1
            result.group_mappings += 1
        except Exception as e:
            result.errors.append(f"Fehler bei '{event.title}': {e}")

    def _try_merge(
        self,
        event:        CalendarEvent,
        parsed:       ParsedEvent,
        person_ids:   list[int],
        result:       'SyncResult',
    ) -> bool:
        """Versucht, das Event in einen bestehenden manuellen Eintrag zu
        mergen (Punkt 4). Wenn alle person_ids bereits einen manuellen
        Eintrag am selben Datum haben → mergen, kein neuer Eintrag.

        Rückgabe: True wenn gemerged (kein neuer Eintrag mehr nötig).
        """
        if not person_ids:
            return False
        cur   = self.con.cursor()
        edate = event.start.strftime("%Y-%m-%d")

        merge_targets: list[tuple[int, int]] = []  # (person_id, shared_id)
        for pid in person_ids:
            existing = _find_existing_manual_entry(cur, pid, edate)
            if existing:
                merge_targets.append((pid, existing[0]))

        # Strategie: NUR wenn JEDE betroffene Person einen bestehenden
        # manuellen Eintrag am selben Tag hat → mergen.
        # Wenn nur ein Teil → normalen Insert machen (kein Merge,
        # damit Personen ohne bestehenden Eintrag nicht leer ausgehen).
        if len(merge_targets) != len(person_ids):
            return False

        # Pro betroffenem shared_entry den Nutzer fragen
        seen_shared_ids = set()
        any_yes = False
        for pid, sid in merge_targets:
            if sid in seen_shared_ids:
                continue
            seen_shared_ids.add(sid)
            cur.execute("SELECT title, content FROM shared_entries WHERE id=?", (sid,))
            r = cur.fetchone()
            if not r:
                continue
            ex_title, ex_content = r
            cur.execute("SELECT name FROM persons WHERE id=?", (pid,))
            pname = cur.fetchone()[0]

            proposal = MergeProposal(
                existing_shared_id = sid,
                existing_title     = ex_title or '',
                existing_content   = ex_content or '',
                existing_date      = edate,
                person_id          = pid,
                person_name        = pname,
                new_event_title    = event.title,
                new_treffen_titel  = parsed.treffen_titel,
                new_ort            = event.location or parsed.suggested_ort,
            )
            if self.merge_callback(proposal):
                _merge_into_existing(
                    self.con, sid,
                    proposal.new_ort, proposal.new_treffen_titel,
                )
                result.merged += 1
                any_yes = True
                # Trotzdem die google_id auf dem bestehenden Eintrag setzen,
                # damit dieses Event beim nächsten Sync als 'duplicate' gilt.
                cur.execute("""UPDATE shared_entries
                    SET google_calendar_event_id=?
                    WHERE id=? AND (google_calendar_event_id IS NULL
                                    OR google_calendar_event_id = '')""",
                    (event.google_id, sid))
                self.con.commit()

        return any_yes

    # ── Haupt-Sync ───────────────────────────────────────────────────────────

    def sync(self) -> SyncResult:
        result = SyncResult()
        cur    = self.con.cursor()

        # Google verbinden
        try:
            service = _get_google_service()
        except FileNotFoundError as e:
            result.errors.append(str(e))
            return result
        except Exception as e:
            result.errors.append(f"Google Auth fehlgeschlagen: {e}")
            return result

        # Events laden
        try:
            events = _fetch_events(
                service,
                months_back    = self.months_back,
                months_forward = self.months_forward,
            )
        except Exception as e:
            result.errors.append(f"Events konnten nicht geladen werden: {e}")
            return result

        total = len(events)
        self.progress_callback(0, total, f"{total} Events geladen …")

        for idx, event in enumerate(events):
            self.progress_callback(idx + 1, total, f"Verarbeite: {event.title}")

            if _is_duplicate(cur, event.google_id):
                result.skipped_duplicate += 1
                continue

            parsed = parse_event_title(event.title)

            if parsed.cancelled:
                result.skipped_cancelled += 1
                continue
            if not parsed.is_valid:
                result.skipped_invalid += 1
                continue

            event.parsed = parsed

            # ── Gruppen-Auto-Erkennung ──────────────────────────────────────
            joined_left = ", ".join(parsed.persons)
            grp_match = self.matcher.match_name(joined_left)
            group_id  = None
            if grp_match.is_resolved and grp_match.matched.kind == 'group':
                group_id = grp_match.matched.id
            elif len(parsed.persons) == 1:
                single = self.matcher.match_name(parsed.persons[0])
                if single.is_resolved and single.matched.kind == 'group':
                    group_id = single.matched.id

            if group_id is not None:
                self._process_group_event(event, parsed, group_id, result)
                continue

            # ── Personen matchen ────────────────────────────────────────────
            person_ids: list[int] = []
            any_person_seen     = False

            for name in parsed.persons:
                match = self.matcher.match_name(name)

                if match.is_blacklisted:
                    result.skipped_blacklist += 1
                    continue
                any_person_seen = True

                if match.is_resolved and match.matched.kind == 'person':
                    if match.matched.id not in person_ids:
                        person_ids.append(match.matched.id)

                elif match.is_resolved and match.matched.kind == 'group':
                    # Gruppe als Teilnehmer in Multi-Personen-Event
                    member_ids, _ = self._resolve_group_members(match.matched.id)
                    for mid in member_ids:
                        if mid not in person_ids:
                            person_ids.append(mid)

                elif match.needs_user_input:
                    resolved = self.resolve_callback(match, event.title)

                    if resolved is None:
                        pass
                    elif resolved == self.BLACKLIST:
                        self.blacklist.add(name)
                        result.skipped_blacklist += 1
                    elif resolved == self.NEW_PERSON:
                        new_person = self.matcher.create_person(name, alias=name)
                        if new_person.id not in person_ids:
                            person_ids.append(new_person.id)
                        result.new_persons += 1
                    elif resolved < 0:
                        # Gruppe (-group_id)
                        gid = -resolved
                        member_ids, _ = self._resolve_group_members(gid)
                        for mid in member_ids:
                            if mid not in person_ids:
                                person_ids.append(mid)
                        result.group_mappings += 1
                    else:
                        if resolved not in person_ids:
                            person_ids.append(resolved)
                        self.matcher.add_alias(resolved, name)
                        result.manual_mappings += 1

            if not person_ids:
                if not any_person_seen:
                    # alle waren blacklisted → schon gezählt
                    pass
                else:
                    result.skipped_invalid += 1
                continue

            # ── Punkt 4: Merge mit bestehendem manuellen Eintrag? ───────────
            if self._try_merge(event, parsed, person_ids, result):
                continue

            # ── Treffen anlegen ─────────────────────────────────────────────
            ort = event.location or parsed.suggested_ort
            try:
                _create_treffen(self.con, event, parsed, person_ids, ort, is_draft=1)
                result.imported += 1
            except Exception as e:
                result.errors.append(f"Fehler bei '{event.title}': {e}")

        self.progress_callback(total, total, "Synchronisation abgeschlossen")
        return result


# ── Hilfsfunktion für Pending-Attendance-Auflösung ────────────────────────────

def list_pending_attendance(con: sqlite3.Connection) -> list[dict]:
    """Gibt alle Gruppen-Treffen zurück, die in der Vergangenheit liegen UND
    noch pending_attendance=1 haben. Wird vom UI beim Start aufgerufen."""
    _ensure_columns(con)
    cur = con.cursor()
    today = _today_iso()
    cur.execute("""
        SELECT se.id, se.title, se.entry_date, se.group_id, p.name
        FROM shared_entries se
        LEFT JOIN persons p ON se.group_id = p.id
        WHERE COALESCE(se.pending_attendance,0) = 1
          AND se.entry_date <= ?
          AND se.entry_date IS NOT NULL
          AND se.entry_date != ''
        ORDER BY se.entry_date DESC
    """, (today,))
    out = []
    for sid, title, edate, gid, gname in cur.fetchall():
        out.append({
            'shared_entry_id': sid,
            'title':           title,
            'entry_date':      edate,
            'group_id':        gid,
            'group_name':      gname or '?',
        })
    return out


def resolve_pending_attendance(
    con:        sqlite3.Connection,
    shared_id:  int,
    member_ids: list[int],
) -> None:
    """Trägt die Anwesenden für ein pending Gruppen-Treffen ein.
    Setzt pending_attendance=0 und legt entries-Zeilen pro Mitglied an.
    Stellt außerdem sicher, dass die Gruppe selbst einen entries-Eintrag
    hat (für ältere Einträge, die noch ohne Gruppen-Eintrag angelegt wurden)."""
    cur = con.cursor()
    cur.execute("""
        SELECT entry_date, group_id FROM shared_entries WHERE id=?
    """, (shared_id,))
    row = cur.fetchone()
    if not row:
        return
    edate, gid = row

    # Existierende Person-Verknüpfungen abfragen
    cur.execute("SELECT person_id FROM entries WHERE shared_entry_id=?", (shared_id,))
    existing_pids = {r[0] for r in cur.fetchall()}

    # Gruppen-Eintrag nachtragen, falls er fehlt (Backward-Kompat)
    if gid is not None and gid not in existing_pids:
        cur.execute("""
            INSERT INTO entries (person_id, shared_entry_id, hidden, pinned)
            VALUES (?, ?, 0, 0)
        """, (gid, shared_id))
        existing_pids.add(gid)

    capped = _capped_for_last_contact(edate)
    for pid in member_ids:
        if pid in existing_pids:
            continue
        cur.execute("""
            INSERT INTO entries (person_id, shared_entry_id, hidden, pinned)
            VALUES (?, ?, 0, 0)
        """, (pid, shared_id))
        if capped:
            cur.execute("""
                UPDATE persons SET last_contact=?
                WHERE id=? AND (last_contact IS NULL OR last_contact < ?)
            """, (capped, pid, capped))

    # Teilnehmer-String aktualisieren
    if gid is not None:
        cur.execute("SELECT name FROM persons WHERE id=?", (gid,))
        gnrow = cur.fetchone()
        gname = gnrow[0] if gnrow else "?"
    else:
        gname = "Gruppe"

    if member_ids:
        cur.execute(
            f"SELECT name FROM persons WHERE id IN ({','.join('?'*len(member_ids))})",
            member_ids,
        )
        names = [r[0] for r in cur.fetchall()]
        new_participants = f"{gname} ({', '.join(names)})"
    else:
        new_participants = gname

    cur.execute("""
        UPDATE shared_entries
        SET pending_attendance=0,
            participants=?,
            updated_at=?
        WHERE id=?
    """, (
        new_participants,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        shared_id,
    ))
    con.commit()
