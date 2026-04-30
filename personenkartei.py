# PersonenKartei v2.0
# Strukturiert in drei Schichten: Datenbank/Logik/Oberfläche
# Duplikate bereinigt, neue Features: Draft-Modus, Backup, Dublettenprüfung,
# fällige Kontakte-Highlighting, Geburtstags-Anzeige

# ===========================================================================
# SCHICHT 1: DATENBANK & DATENMODELL
# ===========================================================================

import sys
import sqlite3
import os
import shutil
import json
import re
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
import base64
from collections import defaultdict

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QListWidget, QListWidgetItem,
    QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTextEdit, QLineEdit,
    QComboBox, QMessageBox, QSplitter, QFormLayout, QDialog,
    QDialogButtonBox, QInputDialog, QCheckBox, QScrollArea, QGroupBox,
    QCalendarWidget, QFileDialog, QRadioButton, QButtonGroup, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QProgressDialog, QMenu,
    QTreeWidget, QTreeWidgetItem, QSlider, QSpinBox, QAbstractItemView,
    QFrame, QProgressBar
)
from PySide6.QtCore import Qt, QDate, QUrl, Signal, QThread, QTimer
from PySide6.QtGui import (
    QFont, QTextCharFormat, QColor, QDesktopServices, QKeySequence,
    QShortcut, QAction, QPixmap, QImage, QTextCursor
)
import csv
import subprocess
import platform
import locale

try:
    locale.setlocale(locale.LC_TIME, 'de_DE.UTF-8')
except:
    try:
        locale.setlocale(locale.LC_TIME, 'German')
    except:
        pass

# ── Konstanten ──────────────────────────────────────────────────────────────
DB_NAME        = "personenkartei.db"
FILES_DIR      = "kartei_dateien"
AUDIO_DIR      = os.path.join(FILES_DIR, "audio")
THUMBNAILS_DIR = os.path.join(FILES_DIR, "thumbnails")
BACKUP_DIR     = "kartei_backups"
CONFIG_FILE    = "kartei_config.json"

for _d in [FILES_DIR, AUDIO_DIR, THUMBNAILS_DIR, BACKUP_DIR]:
    os.makedirs(_d, exist_ok=True)


# ── Verschlüsselung ──────────────────────────────────────────────────────────
class Encryption:
    def __init__(self, password=None):
        self.password = password
        self.enabled  = password is not None

    def encrypt(self, text):
        if not self.enabled or not text:
            return text
        try:
            from cryptography.fernet import Fernet
            key   = hashlib.sha256(self.password.encode()).digest()
            cipher = Fernet(base64.urlsafe_b64encode(key))
            return cipher.encrypt(text.encode()).decode()
        except ImportError:
            return text

    def decrypt(self, text):
        if not self.enabled or not text:
            return text
        try:
            from cryptography.fernet import Fernet
            key   = hashlib.sha256(self.password.encode()).digest()
            cipher = Fernet(base64.urlsafe_b64encode(key))
            return cipher.decrypt(text.encode()).decode()
        except:
            return text


# ── Konfiguration ────────────────────────────────────────────────────────────
class Config:
    def __init__(self):
        self.data = self._load()

    def _load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {
            'encryption_enabled': False,
            'encryption_password': None,
            'contact_intervals': {},
            'shortcuts': {'new_entry': 'Ctrl+N', 'search': 'Ctrl+F', 'save': 'Ctrl+S'},
            'ai_enabled': True,
            'auto_tags': True,
            'saved_searches': [],
        }

    def save(self):
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, indent=2)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()


# ── Datenbank – Schema & Migration ───────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()

    cur.execute("""CREATE TABLE IF NOT EXISTS persons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        hometown TEXT, residence TEXT, occupation TEXT,
        friend_group TEXT, relationship TEXT, topics TEXT,
        birthday TEXT, name_day TEXT, notes TEXT,
        favorite INTEGER DEFAULT 0,
        last_contact TEXT,
        tags TEXT,
        contact_interval_days INTEGER DEFAULT 30,
        created_at TEXT,
        is_group INTEGER DEFAULT 0
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS shared_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        title TEXT, content TEXT NOT NULL,
        entry_date TEXT, created_at TEXT, updated_at TEXT,
        participants TEXT, tags TEXT,
        security_level INTEGER DEFAULT 0,
        encrypted INTEGER DEFAULT 0,
        linked_entries TEXT, ai_suggested_tags TEXT,
        is_draft INTEGER DEFAULT 0
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER NOT NULL,
        shared_entry_id INTEGER NOT NULL,
        hidden INTEGER DEFAULT 0,
        pinned INTEGER DEFAULT 0,
        FOREIGN KEY(person_id) REFERENCES persons(id),
        FOREIGN KEY(shared_entry_id) REFERENCES shared_entries(id)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS entry_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shared_entry_id INTEGER NOT NULL,
        type TEXT NOT NULL, title TEXT, content TEXT NOT NULL,
        entry_date TEXT, version_date TEXT, version_comment TEXT,
        FOREIGN KEY(shared_entry_id) REFERENCES shared_entries(id)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS entry_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shared_entry_id INTEGER NOT NULL,
        filename TEXT NOT NULL, original_name TEXT NOT NULL,
        file_type TEXT DEFAULT 'document', uploaded_at TEXT,
        has_thumbnail INTEGER DEFAULT 0,
        FOREIGN KEY(shared_entry_id) REFERENCES shared_entries(id)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS entry_audio (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shared_entry_id INTEGER NOT NULL,
        filename TEXT NOT NULL, duration INTEGER,
        transcription TEXT, uploaded_at TEXT,
        FOREIGN KEY(shared_entry_id) REFERENCES shared_entries(id)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_id INTEGER,
        reminder_date TEXT NOT NULL, title TEXT NOT NULL,
        description TEXT, recurring TEXT, created_at TEXT,
        completed INTEGER DEFAULT 0,
        FOREIGN KEY(person_id) REFERENCES persons(id)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        person_a_id INTEGER NOT NULL, person_b_id INTEGER NOT NULL,
        relationship_type TEXT, description TEXT, created_at TEXT,
        auto_created INTEGER DEFAULT 0,
        FOREIGN KEY(person_a_id) REFERENCES persons(id),
        FOREIGN KEY(person_b_id) REFERENCES persons(id)
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, description TEXT,
        type TEXT DEFAULT 'group', created_at TEXT
    )""")

    cur.execute("""CREATE TABLE IF NOT EXISTS group_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL, person_id INTEGER NOT NULL,
        joined_at TEXT,
        FOREIGN KEY(group_id) REFERENCES groups(id),
        FOREIGN KEY(person_id) REFERENCES persons(id)
    )""")

    con.commit()
    con.close()


def migrate_database():
    con = sqlite3.connect(DB_NAME)
    cur = con.cursor()

    def _add_cols(table, cols_defs):
        cur.execute(f"PRAGMA table_info({table})")
        existing = {r[1] for r in cur.fetchall()}
        for col, dflt in cols_defs:
            if col not in existing:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {dflt}")
                con.commit()

    # Basis-Erweiterungen & Google Contacts Erweiterungen
    _add_cols('persons', [
        ('contact_interval_days', 'INTEGER DEFAULT 30'),
        ('is_group',              'INTEGER DEFAULT 0'),
        ('created_at',            "TEXT"),
        ('aliases',               "TEXT DEFAULT ''"),
        ('deleted',               'INTEGER DEFAULT 0'),
        ('emails',                'TEXT'),
        ('telefonnummern',        'TEXT'),
        ('arbeitgeber',           'TEXT'),
        ('position',              'TEXT'),
        ('hochschule',            'TEXT'),
        ('studiengang',           'TEXT'),
        ('notizen_google',        'TEXT'),
        ('profilfoto_pfad',       'TEXT')
    ])
    
    # WhatsApp & Erweiterte Treffen-Informationen
    _add_cols('shared_entries', [
        ('security_level',           'INTEGER DEFAULT 0'),
        ('encrypted',                'INTEGER DEFAULT 0'),
        ('linked_entries',           'TEXT'),
        ('ai_suggested_tags',        'TEXT'),
        ('is_draft',                 'INTEGER DEFAULT 0'),
        ('google_calendar_event_id', 'TEXT'),
        ('initiator_person_id',      'INTEGER'),
        ('kommunikation_danach',     'INTEGER DEFAULT 0'),
        ('quelle',                   'TEXT DEFAULT "manuell"'),
        ('kontext',                  'TEXT'),
        ('wahrscheinlichkeit',       'REAL'),
        # Schritt-2-Erweiterungen:
        ('pending_attendance',       'INTEGER DEFAULT 0'),  # Punkt 7
        ('group_id',                 'INTEGER')             # Punkt 7
    ])

    # group_members: Austrittsdatum (Punkt 6)
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='group_members'")
    if cur.fetchone():
        _add_cols('group_members', [
            ('left_at', 'TEXT'),  # NULL = aktuell aktiv
            ('note',    'TEXT')   # optionale Notiz
        ])

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='relationships'")
    if cur.fetchone():
        _add_cols('relationships', [('auto_created', 'INTEGER DEFAULT 0')])

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entry_audio'")
    if not cur.fetchone():
        cur.execute("""CREATE TABLE entry_audio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shared_entry_id INTEGER NOT NULL,
            filename TEXT NOT NULL, duration INTEGER,
            transcription TEXT, uploaded_at TEXT,
            FOREIGN KEY(shared_entry_id) REFERENCES shared_entries(id)
        )""")
        con.commit()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entry_files'")
    if cur.fetchone():
        _add_cols('entry_files', [('has_thumbnail', 'INTEGER DEFAULT 0')])

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reminders'")
    if cur.fetchone():
        _add_cols('reminders', [('completed', 'INTEGER DEFAULT 0')])

    # Kalender-Blacklist (Namen, die beim Sync immer ignoriert werden)
    cur.execute("""CREATE TABLE IF NOT EXISTS calendar_blacklist (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        created_at TEXT
    )""")
    con.commit()

    # Punkt 2: last_contact darf nie in der Zukunft liegen.
    # Bestehende Daten heilen: Wo last_contact > heute → auf größtes
    # vergangenes/heutiges entry_date eines Treffens dieser Person setzen,
    # sonst NULL.
    today_iso = datetime.now().strftime("%Y-%m-%d")
    cur.execute("""
        SELECT id FROM persons
        WHERE last_contact IS NOT NULL
          AND last_contact != ''
          AND length(last_contact) = 10
          AND last_contact > ?
    """, (today_iso,))
    pids_to_heal = [r[0] for r in cur.fetchall()]
    for pid in pids_to_heal:
        cur.execute("""
            SELECT MAX(se.entry_date)
            FROM entries e
            JOIN shared_entries se ON e.shared_entry_id = se.id
            WHERE e.person_id = ?
              AND se.entry_date IS NOT NULL
              AND se.entry_date != ''
              AND length(se.entry_date) = 10
              AND se.entry_date <= ?
              AND COALESCE(se.type,'') IN ('Treffen','Telefonat')
        """, (pid, today_iso))
        max_past = cur.fetchone()[0]
        cur.execute("UPDATE persons SET last_contact=? WHERE id=?",
                    (max_past, pid))
    if pids_to_heal:
        print(f"   ↺ {len(pids_to_heal)} Personen: last_contact aus Zukunft korrigiert")
    con.commit()

    # Backward-Kompat: bei alten Gruppen-Treffen, die noch keinen
    # entries-Datensatz für die Gruppe selbst haben, nachtragen.
    # (Damit Gruppen-Karteieinträge in der Gruppen-Kartei sichtbar werden.)
    cur.execute("""
        SELECT se.id, se.group_id
        FROM shared_entries se
        WHERE se.group_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM entries e
            WHERE e.shared_entry_id = se.id
              AND e.person_id = se.group_id
          )
    """)
    missing_group_entries = cur.fetchall()
    for sid, gid in missing_group_entries:
        cur.execute("""
            INSERT INTO entries (person_id, shared_entry_id, hidden, pinned)
            VALUES (?, ?, 0, 0)
        """, (gid, sid))
    if missing_group_entries:
        print(f"   ↺ {len(missing_group_entries)} Gruppen-Treffen: "
              "Eintrag in Gruppen-Kartei nachgetragen")
    con.commit()

    # FTS und andere Bereinigungen (wie im Original beibehalten)
    _init_fulltext_search(con)
    con.close()
    print("✅ Migration inklusive WhatsApp & Google API abgeschlossen")

def _normalize_date_sql(date_str) -> str | None:
    """Wie normalize_date(), aber ohne datetime-Import-Abhängigkeit im Modul-Scope.
    Wird nur während migrate_database() verwendet."""
    if not date_str:
        return None
    import re as _re
    from datetime import datetime as _dt, timedelta as _td
    s = str(date_str).strip()
    today = _dt.now()
    kw = {'heute': today, 'morgen': today + _td(1),
          'gestern': today - _td(1), 'vorgestern': today - _td(2)}
    if s.lower() in kw:
        return kw[s.lower()].strftime("%Y-%m-%d")
    if _re.match(r'^\d{4}-\d{2}-\d{2}', s):
        try: _dt.strptime(s[:10], "%Y-%m-%d"); return s[:10]
        except ValueError: pass
    m = _re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100: y += 2000 if y < 50 else 1900
        try: return _dt(y, mo, d).strftime("%Y-%m-%d")
        except ValueError: pass
    return None


def _init_fulltext_search(con):
    cur = con.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='persons_fts'")
    if not cur.fetchone():
        cur.execute("""CREATE VIRTUAL TABLE persons_fts USING fts5(
            person_id UNINDEXED, name, category, occupation, topics, notes,
            tokenize='porter unicode61'
        )""")
        cur.execute("""INSERT INTO persons_fts(person_id,name,category,occupation,topics,notes)
            SELECT id,name,category,occupation,topics,notes FROM persons""")

    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entries_fts'")
    if not cur.fetchone():
        cur.execute("""CREATE VIRTUAL TABLE entries_fts USING fts5(
            entry_id UNINDEXED, shared_entry_id UNINDEXED,
            person_name, title, content, type, entry_date,
            tokenize='porter unicode61'
        )""")
        cur.execute("""INSERT INTO entries_fts(entry_id,shared_entry_id,person_name,title,content,type,entry_date)
            SELECT e.id,e.shared_entry_id,p.name,se.title,se.content,se.type,se.entry_date
            FROM entries e
            JOIN persons p ON e.person_id=p.id
            JOIN shared_entries se ON e.shared_entry_id=se.id""")

    cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='persons_ai'")
    if not cur.fetchone():
        cur.execute("""CREATE TRIGGER persons_ai AFTER INSERT ON persons BEGIN
            INSERT INTO persons_fts(person_id,name,category,occupation,topics,notes)
            VALUES(new.id,new.name,new.category,new.occupation,new.topics,new.notes);
        END""")
        cur.execute("""CREATE TRIGGER persons_au AFTER UPDATE ON persons BEGIN
            DELETE FROM persons_fts WHERE person_id=old.id;
            INSERT INTO persons_fts(person_id,name,category,occupation,topics,notes)
            VALUES(new.id,new.name,new.category,new.occupation,new.topics,new.notes);
        END""")
    con.commit()


# ===========================================================================
# SCHICHT 2: LOGIK / SERVICES
# ===========================================================================

# ── Hilfsfunktionen ──────────────────────────────────────────────────────────
def normalize_date(date_str) -> str | None:
    """
    Normalisiert JEDEN Datumswert nach ISO YYYY-MM-DD.
    Versteht: ISO, DD.MM.YYYY, DD.MM.YY, 'heute', 'morgen', 'gestern'.
    Gibt None zurück wenn das Datum nicht erkennbar ist.
    """
    if not date_str:
        return None
    s = str(date_str).strip()
    today = datetime.now()
    # Natürlichsprachliche Kürzel
    mapping = {
        'heute':      today,
        'morgen':     today + timedelta(days=1),
        'gestern':    today - timedelta(days=1),
        'vorgestern': today - timedelta(days=2),
    }
    if s.lower() in mapping:
        return mapping[s.lower()].strftime("%Y-%m-%d")
    # ISO: YYYY-MM-DD (evtl. mit Uhrzeit dahinter)
    if re.match(r'^\d{4}-\d{2}-\d{2}', s):
        try:
            datetime.strptime(s[:10], "%Y-%m-%d")
            return s[:10]
        except ValueError:
            pass
    # Deutsch: D.M.YYYY oder DD.MM.YYYY oder DD.MM.YY
    m = re.match(r'^(\d{1,2})\.(\d{1,2})\.(\d{2,4})$', s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000 if y < 50 else 1900
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def format_date_german(date_str):
    """Konvertiert Datum zu deutschem Format (DD. Monat YYYY).
    Versteht alle Formate die normalize_date() kennt."""
    if not date_str:
        return "Ohne Datum"
    iso = normalize_date(date_str)
    if iso:
        try:
            dt = datetime.strptime(iso, "%Y-%m-%d")
            months = ["Januar","Februar","März","April","Mai","Juni",
                      "Juli","August","September","Oktober","November","Dezember"]
            return f"{dt.day}. {months[dt.month-1]} {dt.year}"
        except ValueError:
            pass
    return str(date_str)  # Fallback: Rohwert anzeigen


def generate_version_comment(old_content, new_content, old_title, new_title,
                              old_date="", new_date="",
                              old_files=0, new_files=0,
                              old_participants="", new_participants="",
                              old_audio=0, new_audio=0):
    """Generiert einen lesbaren Änderungskommentar für Versionseinträge."""
    changes = []
    if old_title != new_title:
        changes.append(f"Titel geändert: '{old_title}' → '{new_title}'")
    if old_date != new_date:
        changes.append(f"Datum geändert: '{old_date}' → '{new_date}'")

    if old_participants != new_participants:
        old_p = set(old_participants.split(", ")) if old_participants else set()
        new_p = set(new_participants.split(", ")) if new_participants else set()
        added   = new_p - old_p
        removed = old_p - new_p
        if added:   changes.append(f"Teilnehmer +: {', '.join(added)}")
        if removed: changes.append(f"Teilnehmer −: {', '.join(removed)}")

    file_diff  = new_files - old_files
    audio_diff = new_audio - old_audio
    if file_diff  > 0: changes.append(f"+{file_diff} Datei(en)")
    elif file_diff < 0: changes.append(f"{file_diff} Datei(en)")
    if audio_diff > 0: changes.append(f"+{audio_diff} Audio")
    elif audio_diff < 0: changes.append(f"{audio_diff} Audio")

    diff = len(new_content) - len(old_content)
    if diff > 50:      changes.append(f"Text +{diff} Zeichen")
    elif diff < -50:   changes.append(f"Text {diff} Zeichen")
    elif diff != 0:    changes.append("Text bearbeitet")

    return " | ".join(changes) if changes else "Bearbeitet"


def detect_sensitive_data(text):
    patterns = {
        'E-Mail':     r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b',
        'Telefon':    r'\b(\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3,4}[-.\s]?\d{4}\b',
        'IBAN':       r'\b[A-Z]{2}\d{2}[A-Z0-9]{1,30}\b',
        'Passwort':   r'(password|passwort|pwd|kennwort)[\s:=]+\S+',
    }
    return [k for k, p in patterns.items() if re.search(p, text, re.IGNORECASE)]


def create_thumbnail(image_path, thumbnail_path, size=(200, 200)):
    try:
        img = QImage(image_path)
        if not img.isNull():
            scaled = img.scaled(size[0], size[1], Qt.KeepAspectRatio, Qt.SmoothTransformation)
            scaled.save(thumbnail_path)
            return True
    except:
        pass
    return False


def parse_whatsapp_chat(file_path):
    entries = []
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        current = None
        pattern = r'(\d{1,2}\.\d{1,2}\.\d{2,4}),?\s+(\d{1,2}:\d{2})\s*[-–]\s*([^:]+):\s*(.*)'
        for line in lines:
            m = re.match(pattern, line)
            if m:
                if current: entries.append(current)
                date_str, time_str, sender, message = m.groups()
                try:
                    parts = date_str.split('.')
                    if len(parts[2]) == 2: parts[2] = '20' + parts[2]
                    dt = datetime.strptime(f"{parts[0]}.{parts[1]}.{parts[2]}", "%d.%m.%Y")
                    fmt_date = dt.strftime("%Y-%m-%d")
                except:
                    fmt_date = date_str
                current = {'date': fmt_date, 'time': time_str,
                           'sender': sender.strip(), 'message': message.strip()}
            elif current:
                current['message'] += '\n' + line.strip()
        if current: entries.append(current)
    except Exception as e:
        print(f"WhatsApp parse error: {e}")
    return entries


# ── Backup-Service ────────────────────────────────────────────────────────────
class BackupService:
    @staticmethod
    def create_backup():
        """Erstellt eine zeitgestempelte Kopie der Datenbank + Dateien."""
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(BACKUP_DIR, f"backup_{ts}")
        os.makedirs(dst, exist_ok=True)

        shutil.copy2(DB_NAME, os.path.join(dst, DB_NAME))
        if os.path.exists(FILES_DIR):
            shutil.copytree(FILES_DIR, os.path.join(dst, FILES_DIR), dirs_exist_ok=True)

        # Älteste Backups entfernen (>10 behalten)
        backups = sorted(Path(BACKUP_DIR).iterdir())
        while len(backups) > 10:
            shutil.rmtree(backups.pop(0))

        return dst

    @staticmethod
    def list_backups():
        return sorted(
            [d for d in Path(BACKUP_DIR).iterdir() if d.is_dir()],
            reverse=True
        )

    @staticmethod
    def restore_backup(backup_dir):
        shutil.copy2(os.path.join(backup_dir, DB_NAME), DB_NAME)
        src_files = os.path.join(backup_dir, FILES_DIR)
        if os.path.exists(src_files):
            if os.path.exists(FILES_DIR):
                shutil.rmtree(FILES_DIR)
            shutil.copytree(src_files, FILES_DIR)


# ── KI-Tag-Vorschläge ────────────────────────────────────────────────────────
class AITagSuggestion:
    _KEYWORDS = {
        'arbeit':     ['job','arbeit','projekt','meeting','kunde','büro','kollege'],
        'privat':     ['urlaub','hobby','freizeit','sport','reise'],
        'wichtig':    ['wichtig','dringend','asap','deadline','eilig'],
        'familie':    ['familie','eltern','kind','geschwister','mama','papa'],
        'gesundheit': ['arzt','krankenhaus','medizin','therapie','gesundheit','krank'],
        'finanzen':   ['geld','rechnung','zahlung','kredit','bank','euro'],
        'essen':      ['restaurant','essen','kochen','rezept','cafe'],
        'kultur':     ['kino','theater','museum','ausstellung','konzert','buch'],
        'sport':      ['fitness','training','laufen','sport','gym'],
        'geburtstag': ['geburtstag','birthday','feier','party'],
    }

    @staticmethod
    def suggest_tags(text, title=""):
        combined = (title + " " + text).lower()
        return [tag for tag, kws in AITagSuggestion._KEYWORDS.items()
                if any(kw in combined for kw in kws)]


# ── Duplettenprüfung ──────────────────────────────────────────────────────────
class DuplicateChecker:
    @staticmethod
    def find_duplicates(cur, name):
        """Gibt Liste ähnlicher Namen zurück (exakte + enthaltene Treffer)."""
        cur.execute("SELECT id, name, category FROM persons WHERE LOWER(name)=LOWER(?)", (name,))
        exact = cur.fetchall()
        cur.execute("SELECT id, name, category FROM persons WHERE LOWER(name) LIKE LOWER(?)",
                    (f"%{name}%",))
        similar = [r for r in cur.fetchall() if r not in exact]
        return exact, similar


# ── Volltext-Suche ────────────────────────────────────────────────────────────
class FullTextSearch:
    def __init__(self, con):
        self.con = con
        self.cur = con.cursor()

    def search(self, query, filters=None):
        if filters is None: filters = {}
        results = {'persons': [], 'entries': [], 'files': []}
        if filters.get('search_persons', True):
            results['persons'] = self._search_persons(query, filters)
        if filters.get('search_entries', True):
            results['entries'] = self._search_entries(query, filters)
        if filters.get('search_files', False):
            results['files'] = self._search_files(query, filters)
        return results

    def _search_persons(self, query, filters):
        try:
            sql = """SELECT p.id,p.name,p.category,p.occupation,p.topics,p.notes,
                       snippet(persons_fts,1,'<<<','>>>','...',32) as ns,
                       snippet(persons_fts,3,'<<<','>>>','...',64) as os,
                       snippet(persons_fts,4,'<<<','>>>','...',64) as ts,
                       snippet(persons_fts,5,'<<<','>>>','...',64) as no_s, rank
                FROM persons_fts JOIN persons p ON persons_fts.person_id=p.id
                WHERE persons_fts MATCH ?"""
            params = [query]
            if filters.get('category'):
                sql += " AND p.category=?"
                params.append(filters['category'])
            sql += " ORDER BY rank LIMIT 50"
            return [{'id':r[0],'name':r[1],'category':r[2],'occupation':r[3],
                     'topics':r[4],'notes':r[5],
                     'snippets':{'name':r[6],'occupation':r[7],'topics':r[8],'notes':r[9]},
                     'relevance':r[10]} for r in self.cur.execute(sql, params)]
        except Exception as e:
            print(f"FTS persons error: {e}")
            return []

    def _search_entries(self, query, filters):
        try:
            sql = """SELECT e.id,e.shared_entry_id,se.type,se.title,se.content,
                       se.entry_date,p.name,
                       snippet(entries_fts,3,'<<<','>>>','...',80),
                       snippet(entries_fts,4,'<<<','>>>','...',150), rank
                FROM entries_fts
                JOIN entries e ON entries_fts.entry_id=e.id
                JOIN shared_entries se ON e.shared_entry_id=se.id
                JOIN persons p ON e.person_id=p.id
                WHERE entries_fts MATCH ?"""
            params = [query]
            if filters.get('date_from'):
                sql += " AND se.entry_date>=?"; params.append(filters['date_from'])
            if filters.get('date_to'):
                sql += " AND se.entry_date<=?"; params.append(filters['date_to'])
            if filters.get('person_id'):
                sql += " AND e.person_id=?"; params.append(filters['person_id'])
            sql += " ORDER BY rank LIMIT 100"
            return [{'entry_id':r[0],'shared_entry_id':r[1],'type':r[2],
                     'title':r[3],'content':r[4],'date':r[5],'person':r[6],
                     'snippets':{'title':r[7],'content':r[8]},'relevance':r[9]}
                    for r in self.cur.execute(sql, params)]
        except Exception as e:
            print(f"FTS entries error: {e}")
            return []

    def _search_files(self, query, filters):
        sql = """SELECT ef.id,ef.original_name,ef.shared_entry_id,se.title,p.name
            FROM entry_files ef
            JOIN shared_entries se ON ef.shared_entry_id=se.id
            JOIN entries e ON se.id=e.shared_entry_id
            JOIN persons p ON e.person_id=p.id
            WHERE LOWER(ef.original_name) LIKE ? LIMIT 50"""
        return [{'file_id':r[0],'filename':r[1],'shared_entry_id':r[2],
                 'entry_title':r[3],'person':r[4]}
                for r in self.cur.execute(sql, (f"%{query.lower()}%",))]


# ===========================================================================
# SCHICHT 3: OBERFLÄCHE / DIALOGE
# ===========================================================================

# ── Smart Date Input ─────────────────────────────────────────────────────────
class SmartDateEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Datum (heute, gestern, 15.01, ...)")
        self.editingFinished.connect(self._parse)
        self._calendar_btn = QPushButton("📅", self)
        self._calendar_btn.setFixedSize(25, 25)
        self._calendar_btn.clicked.connect(self._show_calendar)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._calendar_btn.move(self.width() - 30, 2)

    def _parse(self):
        text  = self.text().strip().lower()
        today = datetime.now()
        if not text: return
        try:
            mapping = {'heute': today, 'gestern': today - timedelta(1),
                       'vorgestern': today - timedelta(2), 'morgen': today + timedelta(1)}
            if text in mapping:
                date = mapping[text]
            elif text.count('.') == 1:
                d, m = text.split('.')
                date = datetime(today.year, int(m), int(d))
            elif text.count('.') == 2:
                d, m, y = text.split('.')
                if len(y) == 2: y = "20" + y
                date = datetime(int(y), int(m), int(d))
            elif '-' in text:
                date = datetime.strptime(text, "%Y-%m-%d")
            else: return
            self.setText(date.strftime("%Y-%m-%d"))
        except: pass

    def _show_calendar(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("Datum wählen")
        lay = QVBoxLayout(dlg)
        cal = QCalendarWidget()
        try:
            cd = datetime.strptime(self.text(), "%Y-%m-%d")
            cal.setSelectedDate(QDate(cd.year, cd.month, cd.day))
        except: pass
        lay.addWidget(cal)
        bl = QHBoxLayout()
        ok = QPushButton("OK")
        ok.clicked.connect(lambda: (self.setText(cal.selectedDate().toString("yyyy-MM-dd")), dlg.accept()))
        ca = QPushButton("Abbrechen"); ca.clicked.connect(dlg.reject)
        bl.addWidget(ok); bl.addWidget(ca); lay.addLayout(bl)
        dlg.exec()


# ── Tag-Editor ────────────────────────────────────────────────────────────────
class TagEditor(QWidget):
    tagsChanged = Signal(list)

    def __init__(self, cur, parent=None):
        super().__init__(parent)
        self.cur  = cur
        self.tags = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0,0,0,0)
        self._input = QLineEdit()
        self._input.setPlaceholderText("Tag hinzufügen…")
        self._input.returnPressed.connect(self.add_tag)
        lay.addWidget(self._input)
        self._container = QWidget()
        self._tag_layout = QHBoxLayout(self._container)
        self._tag_layout.setContentsMargins(0,0,0,0)
        lay.addWidget(self._container)
        lay.addStretch()

    def add_tag(self):
        t = self._input.text().strip()
        if t and t not in self.tags:
            self.tags.append(t)
            self._refresh()
            self._input.clear()
            self.tagsChanged.emit(self.tags)

    def remove_tag(self, tag):
        if tag in self.tags:
            self.tags.remove(tag)
            self._refresh()
            self.tagsChanged.emit(self.tags)

    def set_tags(self, tags_str):
        self.tags = [t.strip() for t in (tags_str or "").split(',') if t.strip()]
        self._refresh()

    def get_tags_string(self):
        return ','.join(self.tags) if self.tags else None

    def _refresh(self):
        for i in reversed(range(self._tag_layout.count())):
            w = self._tag_layout.itemAt(i).widget()
            if w: w.setParent(None)
        for tag in self.tags:
            chip = QWidget()
            chip.setStyleSheet("background:#3498db;border-radius:10px;padding:2px 8px;")
            cl = QHBoxLayout(chip); cl.setContentsMargins(5,2,5,2)
            lbl = QLabel(tag); lbl.setStyleSheet("color:white;font-size:11px;")
            btn = QPushButton("×"); btn.setFixedSize(16,16)
            btn.setStyleSheet("QPushButton{background:transparent;color:white;border:none;font-weight:bold;}"
                              "QPushButton:hover{background:#e74c3c;}")
            btn.clicked.connect(lambda _, t=tag: self.remove_tag(t))
            cl.addWidget(lbl); cl.addWidget(btn)
            self._tag_layout.addWidget(chip)


# ── Sicherheitsstufen-Dialog ──────────────────────────────────────────────────
class SecurityLevelDialog(QDialog):
    def __init__(self, parent, current_level=0):
        super().__init__(parent)
        self.setWindowTitle("🔒 Sicherheitsstufe")
        self.resize(400, 280)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<h3>Sicherheitsstufe wählen:</h3>"))
        self._group = QButtonGroup()
        for lvl, icon, label in [
            (0, "🟢", "Normal – keine Einschränkungen"),
            (1, "🟡", "Ausgeblendet – nur mit Filter sichtbar"),
            (2, "🟠", "Verschlüsselt – Inhalt wird verschlüsselt"),
            (3, "🔴", "Privat – höchste Sicherheit"),
        ]:
            rb = QRadioButton(f"{icon} {label}")
            rb.setChecked(lvl == current_level)
            self._group.addButton(rb, lvl)
            lay.addWidget(rb)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def get_level(self):
        return self._group.checkedId()


# ── Draft-Übersicht ───────────────────────────────────────────────────────────
class DraftOverviewDialog(QDialog):
    """Zeigt alle Einträge mit is_draft=1 übersichtlich an."""

    def __init__(self, parent, cur, con):
        super().__init__(parent)
        self.setWindowTitle("📋 Draft-Übersicht – noch unvollständige Einträge")
        self.resize(800, 550)
        self.cur = cur; self.con = con
        self.main_window = parent

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<h3>📋 Draft-Einträge (noch zu vervollständigen)</h3>"))
        lay.addWidget(QLabel("Hier siehst du alle Einträge, die als Draft markiert sind. "
                             "Doppelklick öffnet den Eintrag zum Bearbeiten."))

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._open_entry)
        self._data = []
        self._load()
        lay.addWidget(self._list)

        bl = QHBoxLayout()
        open_btn  = QPushButton("✏️ Öffnen"); open_btn.clicked.connect(self._open_selected)
        undraft   = QPushButton("✅ Draft-Status entfernen"); undraft.clicked.connect(self._undraft)
        close_btn = QPushButton("Schließen"); close_btn.clicked.connect(self.accept)
        for b in [open_btn, undraft, close_btn]: bl.addWidget(b)
        lay.addLayout(bl)

    def _load(self):
        self._list.clear(); self._data.clear()
        self.cur.execute("""
            SELECT se.id, se.type, se.title, se.entry_date, se.created_at, p.name
            FROM shared_entries se
            JOIN entries e ON se.id = e.shared_entry_id
            JOIN persons p ON e.person_id = p.id
            WHERE se.is_draft = 1
            ORDER BY se.created_at DESC
        """)
        for sid, etype, title, edate, created, pname in self.cur.fetchall():
            icon = {"Notiz":"📝","Treffen":"👥","Telefonat":"📞","KI":"🤖"}.get(etype, "📄")
            date_str = format_date_german(edate) if edate else f"erstellt {created[:10]}"
            self._list.addItem(f"{icon} [{pname}] {title or '(kein Titel)'} – {date_str}")
            self._data.append(sid)

        if not self._data:
            self._list.addItem("✅ Keine Draft-Einträge vorhanden")

    def _get_entry_id_for_shared(self, shared_id):
        self.cur.execute("SELECT id FROM entries WHERE shared_entry_id=? LIMIT 1", (shared_id,))
        r = self.cur.fetchone()
        return r[0] if r else None

    def _open_entry(self, item):
        idx = self._list.row(item)
        if idx < len(self._data):
            sid = self._data[idx]
            eid = self._get_entry_id_for_shared(sid)
            if eid:
                dlg = EditEntryDialog(self, eid, self.cur, self.con)
                dlg.exec()
                self._load()

    def _open_selected(self):
        item = self._list.currentItem()
        if item: self._open_entry(item)

    def _undraft(self):
        idx = self._list.currentRow()
        if idx < 0 or idx >= len(self._data):
            QMessageBox.warning(self, "Fehler", "Bitte Eintrag auswählen"); return
        sid = self._data[idx]
        self.con.execute("UPDATE shared_entries SET is_draft=0 WHERE id=?", (sid,))
        self.con.commit()
        self._load()


# ── Backup-Dialog ─────────────────────────────────────────────────────────────
class BackupDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("💾 Backup verwalten")
        self.resize(500, 400)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("<h3>💾 Datenbank-Backup</h3>"))
        create_btn = QPushButton("🔒 Jetzt Backup erstellen")
        create_btn.setStyleSheet("background:#27ae60;color:white;font-weight:bold;padding:8px;")
        create_btn.clicked.connect(self._create)
        lay.addWidget(create_btn)

        lay.addWidget(QLabel("<b>Vorhandene Backups:</b>"))
        self._list = QListWidget()
        self._refresh()
        lay.addWidget(self._list)

        bl = QHBoxLayout()
        restore_btn = QPushButton("♻️ Wiederherstellen"); restore_btn.clicked.connect(self._restore)
        close_btn   = QPushButton("Schließen");           close_btn.clicked.connect(self.accept)
        bl.addWidget(restore_btn); bl.addWidget(close_btn)
        lay.addLayout(bl)

    def _refresh(self):
        self._list.clear()
        for b in BackupService.list_backups():
            self._list.addItem(str(b.name))

    def _create(self):
        try:
            dst = BackupService.create_backup()
            self._refresh()
            QMessageBox.information(self, "Backup erstellt", f"Backup gespeichert:\n{dst}")
        except Exception as e:
            QMessageBox.critical(self, "Fehler", f"Backup fehlgeschlagen:\n{e}")

    def _restore(self):
        current = self._list.currentRow()
        backups = BackupService.list_backups()
        if current < 0 or current >= len(backups):
            QMessageBox.warning(self, "Fehler", "Bitte Backup auswählen"); return
        reply = QMessageBox.question(self, "Wiederherstellen",
            f"Backup '{backups[current].name}' wirklich wiederherstellen?\n"
            "Die aktuelle Datenbank wird überschrieben!",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                BackupService.restore_backup(str(backups[current]))
                QMessageBox.information(self, "Erfolg",
                    "Backup wiederhergestellt.\nBitte Anwendung neu starten.")
            except Exception as e:
                QMessageBox.critical(self, "Fehler", f"Wiederherstellung fehlgeschlagen:\n{e}")


# ── Duplettenprüfungs-Dialog ──────────────────────────────────────────────────
class DuplicateWarningDialog(QDialog):
    """Zeigt bei Namensgleichheit eine Warnung an und fragt wie vorzugehen ist."""

    def __init__(self, parent, name, exact, similar):
        super().__init__(parent)
        self.setWindowTitle("⚠️ Mögliche Dublette")
        self.resize(480, 320)
        self._choice = 'cancel'
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>Beim Anlegen von '{name}' wurden ähnliche Einträge gefunden:</b>"))

        if exact:
            lay.addWidget(QLabel("<b>🔴 Exakte Treffer:</b>"))
            for _, ename, cat in exact:
                lay.addWidget(QLabel(f"   • {ename} ({cat})"))
        if similar:
            lay.addWidget(QLabel("<b>🟡 Ähnliche Namen:</b>"))
            for _, ename, cat in similar:
                lay.addWidget(QLabel(f"   • {ename} ({cat})"))

        lay.addSpacing(10)
        lay.addWidget(QLabel("Wie möchtest du vorgehen?"))
        bl = QHBoxLayout()
        create_btn = QPushButton("➕ Trotzdem anlegen")
        create_btn.setStyleSheet("background:#e74c3c;color:white;")
        create_btn.clicked.connect(lambda: self._select('create'))
        cancel_btn = QPushButton("❌ Abbrechen")
        cancel_btn.clicked.connect(lambda: self._select('cancel'))
        bl.addWidget(create_btn); bl.addWidget(cancel_btn)
        lay.addLayout(bl)

    def _select(self, choice):
        self._choice = choice
        self.accept()

    def get_choice(self): return self._choice


# ── Eintrag bearbeiten (vollständige, einzige Version) ────────────────────────
class EditEntryDialog(QDialog):
    """Zentraler Dialog zum Erstellen und Bearbeiten von Einträgen.
    Unterstützt alle Felder, Versionshistorie, Draft-Modus, Sicherheitsstufe,
    Verlinkungen, Dateien und Audio."""

    def __init__(self, parent, entry_id, cur, con, encryption=None):
        super().__init__(parent)
        self.setWindowTitle("📝 Eintrag bearbeiten")
        self.resize(800, 950)
        self.entry_id   = entry_id
        self.cur        = cur
        self.con        = con
        self.encryption = encryption or Encryption()

        self.cur.execute("SELECT shared_entry_id, pinned FROM entries WHERE id=?", (entry_id,))
        result = self.cur.fetchone()
        if not result:
            QMessageBox.critical(self, "Fehler", "Eintrag nicht gefunden")
            self.reject(); return
        self.shared_entry_id, self.pinned = result

        self.cur.execute("""
            SELECT type, title, content, entry_date, created_at, updated_at,
                   participants, tags, security_level, encrypted, linked_entries, is_draft
            FROM shared_entries WHERE id=?
        """, (self.shared_entry_id,))
        r = self.cur.fetchone()
        if not r:
            self.reject(); return

        etype, title, content, entry_date, created_at, updated_at, \
            participants, tags, security_level, encrypted, linked_entries, is_draft = r

        if encrypted and self.encryption.enabled:
            content = self.encryption.decrypt(content)
            if title: title = self.encryption.decrypt(title)

        self.original_title        = title or ""
        self.original_content      = content or ""
        self.original_date         = entry_date or ""
        self.original_etype        = etype
        self.original_participants = participants or ""
        self.original_tags         = tags or ""
        self.security_level        = security_level or 0

        self.cur.execute("SELECT COUNT(*) FROM entry_files WHERE shared_entry_id=?", (self.shared_entry_id,))
        self.original_file_count = self.cur.fetchone()[0]
        self.cur.execute("SELECT COUNT(*) FROM entry_audio WHERE shared_entry_id=?", (self.shared_entry_id,))
        self.original_audio_count = self.cur.fetchone()[0]

        # ── Layout aufbauen ──────────────────────────────────────────────────
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content_widget = QWidget()
        lay = QVBoxLayout(content_widget)
        scroll.setWidget(content_widget)
        outer = QVBoxLayout(self)
        outer.addWidget(scroll)

        # Sicherheit
        sec_colors  = {0:"#90EE90",1:"#FFD700",2:"#FFA500",3:"#FF6347"}
        sec_labels  = {0:"Normal",1:"Ausgeblendet",2:"Verschlüsselt",3:"Privat"}
        self._sec_lbl = QLabel(f"🔒 Sicherheit: {sec_labels[self.security_level]}")
        self._sec_lbl.setStyleSheet(f"background:{sec_colors[self.security_level]};"
                                    "padding:5px;border-radius:3px;")
        sec_btn = QPushButton("🔐 Sicherheitsstufe ändern")
        sec_btn.clicked.connect(self._change_security)
        lay.addWidget(self._sec_lbl); lay.addWidget(sec_btn)

        # Draft-Checkbox
        self.draft_cb = QCheckBox("📋 Als Draft markieren (noch zu vervollständigen)")
        self.draft_cb.setChecked(bool(is_draft))
        self.draft_cb.setStyleSheet("font-weight:bold;color:#e67e22;")
        lay.addWidget(self.draft_cb)

        # Pin
        self.pinned_cb = QCheckBox("📌 Eintrag anpinnen (immer oben)")
        self.pinned_cb.setChecked(self.pinned == 1)
        lay.addWidget(self.pinned_cb)

        # Titel
        lay.addWidget(QLabel("<b>Titel</b>"))
        self.title_edit = QLineEdit(title or "")
        lay.addWidget(self.title_edit)

        # Content nach Typ
        if etype in ["Treffen","Telefonat","KI"]:
            parts = {"Ort":"","Themen":"","Ergebnis/Gefühl":""}
            try:
                for part in (content or "").split("|"):
                    if ":" in part:
                        k, v = part.split(":",1)
                        if k.strip() in parts: parts[k.strip()] = v.strip()
            except: parts["Themen"] = content or ""

            if etype == "Treffen":
                lay.addWidget(QLabel("<b>Ort</b>"))
                self.ort_edit = QLineEdit(parts["Ort"])
                lay.addWidget(self.ort_edit)

            lay.addWidget(QLabel("<b>Themen</b>"))
            self.themen_edit = QTextEdit(parts["Themen"])
            self.themen_edit.setTabChangesFocus(True)
            lay.addWidget(self.themen_edit)
            lay.addWidget(QLabel("<b>Ergebnis / Gefühl</b>"))
            self.gespraech_edit = QTextEdit(parts["Ergebnis/Gefühl"])
            self.gespraech_edit.setTabChangesFocus(True)
            lay.addWidget(self.gespraech_edit)
        else:
            lay.addWidget(QLabel("<b>Text</b>"))
            self.content_edit = QTextEdit(content or "")
            self.content_edit.setTabChangesFocus(True)
            lay.addWidget(self.content_edit)

        # Datum
        date_lay = QHBoxLayout()
        self.date_edit = QLineEdit(entry_date or "")
        date_btn = QPushButton("📅")
        date_btn.clicked.connect(self._select_date)
        date_lay.addWidget(self.date_edit); date_lay.addWidget(date_btn)
        lay.addWidget(QLabel("<b>Datum</b>")); lay.addLayout(date_lay)

        # Tags
        lay.addWidget(QLabel("<b>🏷️ Tags (kommagetrennt)</b>"))
        self.tags_edit = QLineEdit(tags or "")
        lay.addWidget(self.tags_edit)

        # Verlinkungen
        lay.addWidget(QLabel("<b>🔗 Verlinkte Einträge</b>"))
        self.linked_list = QListWidget()
        self.linked_data: list[int] = []
        self._load_linked_entries()
        lay.addWidget(self.linked_list)
        ll = QHBoxLayout()
        add_link_btn = QPushButton("➕ Verlinkung"); add_link_btn.clicked.connect(self._add_link)
        rem_link_btn = QPushButton("🗑️ Entfernen");  rem_link_btn.clicked.connect(self._remove_link)
        ll.addWidget(add_link_btn); ll.addWidget(rem_link_btn); lay.addLayout(ll)

        # Dateien
        lay.addWidget(QLabel("<b>📎 Dateien</b>"))
        self.files_list = QListWidget()
        self.file_data: list = []
        self._load_files()
        lay.addWidget(self.files_list)
        fl = QHBoxLayout()
        add_file_btn = QPushButton("➕ Datei"); add_file_btn.clicked.connect(self._add_file)
        rem_file_btn = QPushButton("🗑️ Entfernen"); rem_file_btn.clicked.connect(self._remove_file)
        fl.addWidget(add_file_btn); fl.addWidget(rem_file_btn); lay.addLayout(fl)

        # Audio – Aufnahme + Verwaltung
        audio_hdr = QHBoxLayout()
        audio_hdr.addWidget(QLabel("<b>🎤 Sprachaufnahmen</b>"))
        rec_btn = QPushButton("⏺️ Neue Aufnahme")
        rec_btn.setStyleSheet("background:#e74c3c;color:white;font-weight:bold;padding:4px 10px;")
        rec_btn.clicked.connect(self.record_audio)
        audio_hdr.addWidget(rec_btn); audio_hdr.addStretch()
        lay.addLayout(audio_hdr)

        self.audio_list = QListWidget()
        self.audio_list.setMaximumHeight(100)
        self.audio_data: list = []
        self._load_audio()
        lay.addWidget(self.audio_list)
        al = QHBoxLayout()
        trans_btn  = QPushButton("📝 Transkribieren"); trans_btn.clicked.connect(self._transcribe_audio)
        play_btn   = QPushButton("▶️ Abspielen");      play_btn.clicked.connect(self._play_selected_audio)
        rem_aud    = QPushButton("🗑️ Entfernen");      rem_aud.clicked.connect(self._remove_audio)
        al.addWidget(trans_btn); al.addWidget(play_btn); al.addWidget(rem_aud)
        lay.addLayout(al)

        # Teilnehmer
        lay.addWidget(QLabel("<b>👥 Teilnehmer</b>"))
        self.participants_list = QListWidget()
        self.participant_ids: list[int] = []
        self._load_participants()
        lay.addWidget(self.participants_list)
        pl = QHBoxLayout()
        add_p = QPushButton("➕ Person"); add_p.clicked.connect(self._add_participant)
        rem_p = QPushButton("🗑️ Entfernen"); rem_p.clicked.connect(self._remove_participant)
        pl.addWidget(add_p); pl.addWidget(rem_p); lay.addLayout(pl)

        # Meta
        changed = "Nie" if (updated_at == created_at or not updated_at) else updated_at
        lay.addWidget(QLabel(f"<i>Erstellt: {created_at or '?'} | Geändert: {changed} | ID: {entry_id}</i>"))

        ver_btn = QPushButton("📜 Versionshistorie")
        ver_btn.clicked.connect(self._show_versions)
        lay.addWidget(ver_btn)

        # Aktions-Buttons
        btn_row = QHBoxLayout()
        save_btn   = QPushButton("💾 Speichern");  save_btn.clicked.connect(self.save)
        save_btn.setStyleSheet("background:#27ae60;color:white;font-weight:bold;padding:6px;")
        hide_btn   = QPushButton("👁️ Ausblenden"); hide_btn.clicked.connect(self._hide_entry)
        cancel_btn = QPushButton("Abbrechen");      cancel_btn.clicked.connect(self.reject)
        for b in [save_btn, hide_btn, cancel_btn]: btn_row.addWidget(b)
        outer.addLayout(btn_row)

    # ── interne Hilfsmethoden ────────────────────────────────────────────────
    def _change_security(self):
        dlg = SecurityLevelDialog(self, self.security_level)
        if dlg.exec():
            old_level = self.security_level
            self.security_level = dlg.get_level()
            sec_colors = {0:"#90EE90",1:"#FFD700",2:"#FFA500",3:"#FF6347"}
            sec_labels = {0:"Normal",1:"Ausgeblendet",2:"Verschlüsselt",3:"Privat"}
            self._sec_lbl.setText(f"🔒 Sicherheit: {sec_labels[self.security_level]}")
            self._sec_lbl.setStyleSheet(f"background:{sec_colors[self.security_level]};"
                                        "padding:5px;border-radius:3px;")

            # Sofortige Effekte je nach Stufe
            effects = []
            if self.security_level >= 1:
                effects.append("• Eintrag wird ausgeblendet (nur mit 'Ausgeblendete zeigen' sichtbar)")
            if self.security_level >= 2:
                effects.append("• Inhalt wird beim Speichern verschlüsselt")
                if not self.encryption.enabled:
                    effects.append("  ⚠️ Kein Passwort gesetzt – Verschlüsselung inaktiv!")
            if self.security_level == 3:
                effects.append("• Eintrag wird vom Export ausgeschlossen")
                effects.append("• Höchste Sicherheitsstufe aktiv")
            if self.security_level == 0 and old_level > 0:
                effects.append("• Eintrag wird wieder sichtbar")
                effects.append("• Inhalt wird unverschlüsselt gespeichert")

            if effects:
                QMessageBox.information(
                    self, f"Sicherheitsstufe: {sec_labels[self.security_level]}",
                    "Auswirkungen:\n" + "\n".join(effects))

            # hidden-Flag sofort anpassen (entry-Tabelle)
            new_hidden = 1 if self.security_level >= 1 else 0
            self.cur.execute("UPDATE entries SET hidden=? WHERE id=?",
                             (new_hidden, self.entry_id))
            self.con.commit()

    def _select_date(self):
        cal = QCalendarWidget()
        dlg = QDialog(self); dlg.setWindowTitle("Datum wählen")
        l = QVBoxLayout(dlg); l.addWidget(cal)
        ok = QPushButton("OK"); ok.clicked.connect(dlg.accept); l.addWidget(ok)
        if dlg.exec():
            self.date_edit.setText(cal.selectedDate().toString("yyyy-MM-dd"))

    def _load_linked_entries(self):
        self.linked_list.clear(); self.linked_data.clear()
        self.cur.execute("SELECT linked_entries FROM shared_entries WHERE id=?",
                         (self.shared_entry_id,))
        r = self.cur.fetchone()
        if r and r[0]:
            for lid in [int(x) for x in r[0].split(',') if x.strip().isdigit()]:
                self.cur.execute("""
                    SELECT se.title, se.type, se.entry_date, p.name
                    FROM shared_entries se
                    JOIN entries e ON se.id=e.shared_entry_id
                    JOIN persons p ON e.person_id=p.id
                    WHERE se.id=? LIMIT 1
                """, (lid,))
                row = self.cur.fetchone()
                if row:
                    t, et, ed, pn = row
                    self.linked_list.addItem(f"🔗 {pn}: {t or et} ({format_date_german(ed)})")
                    self.linked_data.append(lid)

    def _add_link(self):
        self.cur.execute("""
            SELECT se.id, se.title, se.type, se.entry_date, p.name
            FROM shared_entries se
            JOIN entries e ON se.id=e.shared_entry_id
            JOIN persons p ON e.person_id=p.id
            WHERE se.id!=? GROUP BY se.id ORDER BY se.entry_date DESC LIMIT 100
        """, (self.shared_entry_id,))
        rows = self.cur.fetchall()
        if not rows:
            QMessageBox.information(self, "Info", "Keine weiteren Einträge vorhanden"); return
        items = [f"{pn}: {t or et} ({format_date_german(ed)})" for _, t, et, ed, pn in rows]
        item, ok = QInputDialog.getItem(self, "Verlinken", "Eintrag:", items, 0, False)
        if ok:
            idx = items.index(item)
            sid = rows[idx][0]
            if sid not in self.linked_data:
                self.linked_data.append(sid)
                self.linked_list.addItem(f"🔗 {item}")

    def _remove_link(self):
        row = self.linked_list.currentRow()
        if 0 <= row < len(self.linked_data):
            del self.linked_data[row]; self.linked_list.takeItem(row)

    def _load_participants(self):
        self.participants_list.clear(); self.participant_ids.clear()
        self.cur.execute("""
            SELECT p.id, p.name FROM persons p
            JOIN entries e ON p.id=e.person_id
            WHERE e.shared_entry_id=? ORDER BY p.name
        """, (self.shared_entry_id,))
        for pid, pname in self.cur.fetchall():
            self.participants_list.addItem(f"👤 {pname}")
            self.participant_ids.append(pid)

    def _add_participant(self):
        self.cur.execute("SELECT id, name FROM persons ORDER BY name")
        available = [(pid, n) for pid, n in self.cur.fetchall()
                     if pid not in self.participant_ids]
        if not available:
            QMessageBox.information(self, "Info", "Alle Personen bereits Teilnehmer"); return
        names = [n for _, n in available]
        name, ok = QInputDialog.getItem(self, "Teilnehmer", "Person:", names, 0, False)
        if ok:
            pid = available[names.index(name)][0]
            self.cur.execute("INSERT INTO entries (person_id, shared_entry_id, hidden, pinned) VALUES (?,?,0,0)",
                             (pid, self.shared_entry_id))
            self.con.commit(); self._update_participants_str(); self._load_participants()

    def _remove_participant(self):
        row = self.participants_list.currentRow()
        if row < 0 or row >= len(self.participant_ids):
            QMessageBox.warning(self, "Fehler", "Bitte Teilnehmer auswählen"); return
        if len(self.participant_ids) <= 1:
            QMessageBox.warning(self, "Fehler", "Mindestens ein Teilnehmer erforderlich"); return
        reply = QMessageBox.question(self, "Entfernen", "Teilnehmer entfernen?",
                                     QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            pid = self.participant_ids[row]
            self.cur.execute("DELETE FROM entries WHERE person_id=? AND shared_entry_id=?",
                             (pid, self.shared_entry_id))
            self.con.commit(); self._update_participants_str(); self._load_participants()

    def _update_participants_str(self):
        self.cur.execute("""SELECT p.name FROM persons p
            JOIN entries e ON p.id=e.person_id
            WHERE e.shared_entry_id=? ORDER BY p.name""", (self.shared_entry_id,))
        names = [r[0] for r in self.cur.fetchall()]
        s = ", ".join(names) if len(names) > 1 else None
        self.cur.execute("UPDATE shared_entries SET participants=? WHERE id=?",
                         (s, self.shared_entry_id))
        self.con.commit()

    def _load_files(self):
        self.files_list.clear(); self.file_data.clear()
        self.cur.execute("""
            SELECT id, filename, original_name, uploaded_at, file_type, has_thumbnail
            FROM entry_files WHERE shared_entry_id=?
        """, (self.shared_entry_id,))
        for fid, fname, orig, up, ftype, thumb in self.cur.fetchall():
            icon = "🖼️" if ftype and ftype.startswith('image/') else "📎"
            self.files_list.addItem(f"{icon} {orig} ({up})")
            self.file_data.append((fid, fname, orig, thumb))
        self.files_list.itemDoubleClicked.connect(self._open_file)

    def _open_file(self, item):
        idx = self.files_list.row(item)
        if idx < len(self.file_data):
            fid, fname, orig, thumb = self.file_data[idx]
            path = os.path.join(FILES_DIR, fname)
            if os.path.exists(path):
                try:
                    if platform.system() == 'Windows': os.startfile(path)
                    elif platform.system() == 'Darwin': subprocess.run(['open', path])
                    else: subprocess.run(['xdg-open', path])
                except Exception as e:
                    QMessageBox.warning(self, "Fehler", str(e))
            else:
                QMessageBox.warning(self, "Fehler", "Datei nicht gefunden")

    def _add_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Dateien auswählen")
        for path in paths:
            try:
                base   = os.path.basename(path)
                newname = f"{datetime.now().strftime('%Y-%m-%d')}_{base}"
                dest   = os.path.join(FILES_DIR, newname)
                shutil.copy2(path, dest)
                import mimetypes
                ftype, _ = mimetypes.guess_type(path)
                thumb = 0
                if ftype and ftype.startswith('image/'):
                    tp = os.path.join(THUMBNAILS_DIR, f"thumb_{newname}")
                    if create_thumbnail(dest, tp): thumb = 1
                self.cur.execute("""INSERT INTO entry_files
                    (shared_entry_id, filename, original_name, file_type, uploaded_at, has_thumbnail)
                    VALUES (?,?,?,?,?,?)""",
                    (self.shared_entry_id, newname, base, ftype,
                     datetime.now().strftime("%Y-%m-%d %H:%M:%S"), thumb))
                self.con.commit()
            except Exception as e:
                QMessageBox.critical(self, "Fehler", str(e))
        self._load_files()

    def _remove_file(self):
        row = self.files_list.currentRow()
        if 0 <= row < len(self.file_data):
            fid, fname, orig, thumb = self.file_data[row]
            if QMessageBox.question(self, "Entfernen", f"'{orig}' entfernen?",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                for p in [os.path.join(FILES_DIR, fname),
                           os.path.join(THUMBNAILS_DIR, f"thumb_{fname}")]:
                    if os.path.exists(p): os.remove(p)
                self.cur.execute("DELETE FROM entry_files WHERE id=?", (fid,))
                self.con.commit(); self._load_files()

    def _load_audio(self):
        self.audio_list.clear(); self.audio_data.clear()
        self.cur.execute("""SELECT id, filename, duration, transcription, uploaded_at
            FROM entry_audio WHERE shared_entry_id=?""", (self.shared_entry_id,))
        for aid, fname, dur, trans, up in self.cur.fetchall():
            mark = " [📝]" if trans else ""
            dur_str = f"{dur}s" if dur else "?"
            self.audio_list.addItem(f"🎤 {up}  ⏱ {dur_str}{mark}")
            self.audio_data.append((aid, fname, trans))
        # Doppelklick = Abspielen
        try: self.audio_list.itemDoubleClicked.disconnect()
        except: pass
        self.audio_list.itemDoubleClicked.connect(self._play_audio)

    def record_audio(self):
        """Öffnet den Aufnahme-Dialog und speichert die Aufnahme."""
        dlg = AudioRecorderDialog(self)
        if dlg.exec():
            af = dlg.get_audio_file()
            trans = dlg.get_transcription()
            if af and os.path.exists(af):
                try:
                    duration = getattr(dlg, 'elapsed_seconds', None)
                    self.cur.execute(
                        """INSERT INTO entry_audio
                           (shared_entry_id, filename, duration, transcription, uploaded_at)
                           VALUES (?,?,?,?,?)""",
                        (self.shared_entry_id, os.path.basename(af), duration,
                         trans, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    self.con.commit()
                    self._load_audio()
                    if trans:
                        # Transkription optional in den Content übernehmen
                        if QMessageBox.question(
                                self, "Transkription",
                                "Transkription in den Eintrag übernehmen?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                            if hasattr(self, 'content_edit'):
                                cur = self.content_edit.textCursor()
                                cur.movePosition(QTextCursor.End)
                                cur.insertText(f"\n\n[Aufnahme {datetime.now().strftime('%H:%M')}]\n{trans}")
                            elif hasattr(self, 'themen_edit'):
                                self.themen_edit.append(f"\n[Aufnahme] {trans}")
                except Exception as e:
                    QMessageBox.critical(self, "Fehler",
                                         f"Aufnahme konnte nicht gespeichert werden:\n{e}")

    def _play_selected_audio(self):
        """Spielt die aktuell gewählte Aufnahme ab."""
        item = self.audio_list.currentItem()
        if item:
            self._play_audio(item)

    def _play_audio(self, item):
        idx = self.audio_list.row(item)
        if idx < len(self.audio_data):
            aid, fname, trans = self.audio_data[idx]
            if trans: QMessageBox.information(self, "Transkript", trans)
            path = os.path.join(AUDIO_DIR, fname)
            if os.path.exists(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _transcribe_audio(self):
        row = self.audio_list.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Fehler", "Aufnahme auswählen"); return
        aid, fname, trans = self.audio_data[row]
        new_t, ok = QInputDialog.getMultiLineText(self, "Transkription", "Transkript:", trans or "")
        if ok:
            self.cur.execute("UPDATE entry_audio SET transcription=? WHERE id=?", (new_t, aid))
            self.con.commit(); self._load_audio()

    def _remove_audio(self):
        row = self.audio_list.currentRow()
        if 0 <= row < len(self.audio_data):
            aid, fname, _ = self.audio_data[row]
            if QMessageBox.question(self, "Entfernen", "Aufnahme entfernen?",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                path = os.path.join(AUDIO_DIR, fname)
                if os.path.exists(path): os.remove(path)
                self.cur.execute("DELETE FROM entry_audio WHERE id=?", (aid,))
                self.con.commit(); self._load_audio()

    def _show_versions(self):
        VersionHistoryDialog(self, self.shared_entry_id, self.cur, self.con).exec()
        self.cur.execute("SELECT title, content FROM shared_entries WHERE id=?",
                         (self.shared_entry_id,))
        r = self.cur.fetchone()
        if r:
            self.title_edit.setText(r[0] or "")
            if hasattr(self, "content_edit"):
                self.content_edit.setPlainText(r[1] or "")

    def _hide_entry(self):
        if QMessageBox.question(self, "Ausblenden",
                                "Eintrag ausblenden?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.cur.execute("UPDATE entries SET hidden=1 WHERE id=?", (self.entry_id,))
            self.con.commit(); self.accept()

    def _collect_content(self):
        if hasattr(self, "ort_edit"):
            if self.original_etype == "Treffen":
                return (f"Ort: {self.ort_edit.text()} | "
                        f"Themen: {self.themen_edit.toPlainText()} | "
                        f"Ergebnis/Gefühl: {self.gespraech_edit.toPlainText()}")
            else:
                return (f"Themen: {self.themen_edit.toPlainText()} | "
                        f"Ergebnis/Gefühl: {self.gespraech_edit.toPlainText()}")
        return self.content_edit.toPlainText()

    def save(self):
        content   = self._collect_content()
        new_title = self.title_edit.text().strip()
        new_date  = self.date_edit.text().strip()
        new_tags  = self.tags_edit.text().strip()
        new_draft = 1 if self.draft_cb.isChecked() else 0

        # Sensible Daten
        sensitive = detect_sensitive_data(content + " " + new_title)
        if sensitive and self.security_level == 0:
            if QMessageBox.warning(self, "⚠️ Sensible Daten",
                                   f"Gefundene Daten: {', '.join(sensitive)}\n"
                                   "Höhere Sicherheitsstufe setzen?",
                                   QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                self._change_security()

        self.cur.execute("UPDATE entries SET pinned=? WHERE id=?",
                         (1 if self.pinned_cb.isChecked() else 0, self.entry_id))

        self.cur.execute("SELECT COUNT(*) FROM entry_files WHERE shared_entry_id=?",
                         (self.shared_entry_id,))
        cur_files = self.cur.fetchone()[0]
        self.cur.execute("SELECT COUNT(*) FROM entry_audio WHERE shared_entry_id=?",
                         (self.shared_entry_id,))
        cur_audio = self.cur.fetchone()[0]
        self.cur.execute("SELECT participants FROM shared_entries WHERE id=?",
                         (self.shared_entry_id,))
        cur_parts = (self.cur.fetchone() or ("",))[0] or ""

        # Version anlegen wenn geändert
        if (new_title != self.original_title or content != self.original_content or
                new_date != self.original_date or cur_files != self.original_file_count or
                cur_parts != self.original_participants or cur_audio != self.original_audio_count):
            self.cur.execute("SELECT type, entry_date, updated_at FROM shared_entries WHERE id=?",
                             (self.shared_entry_id,))
            old = self.cur.fetchone()
            if old:
                comment = generate_version_comment(
                    self.original_content, content,
                    self.original_title, new_title,
                    self.original_date, new_date,
                    self.original_file_count, cur_files,
                    self.original_participants, cur_parts,
                    self.original_audio_count, cur_audio,
                )
                ts = old[2] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.cur.execute("""INSERT INTO entry_versions
                    (shared_entry_id, type, title, content, entry_date, version_date, version_comment)
                    VALUES (?,?,?,?,?,?,?)""",
                    (self.shared_entry_id, old[0], self.original_title,
                     self.original_content, old[1], ts, comment))

        # Verschlüsseln
        save_title   = new_title
        save_content = content
        encrypted    = 0
        if self.security_level >= 2 and self.encryption.enabled:
            save_title   = self.encryption.encrypt(new_title)
            save_content = self.encryption.encrypt(content)
            encrypted    = 1

        linked_str = ','.join(str(x) for x in self.linked_data) if self.linked_data else None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cur.execute("""UPDATE shared_entries
            SET title=?, content=?, entry_date=?, updated_at=?, tags=?,
                security_level=?, encrypted=?, linked_entries=?, is_draft=?
            WHERE id=?""",
            (save_title, save_content, new_date, now, new_tags,
             self.security_level, encrypted, linked_str, new_draft,
             self.shared_entry_id))

        new_date_iso = normalize_date(new_date) if new_date else None
        # Punkt 2: last_contact darf nie in der Zukunft liegen
        today_iso = datetime.now().strftime("%Y-%m-%d")
        new_date_for_lc = (new_date_iso
                           if new_date_iso and new_date_iso <= today_iso
                           else None)
        if new_date_for_lc and self.original_etype in ("Treffen", "Telefonat"):
            for pid in self.participant_ids:
                self.cur.execute("""UPDATE persons SET last_contact=?
                    WHERE id=? AND (
                        last_contact IS NULL
                        OR last_contact = ''
                        OR (length(last_contact)=10 AND last_contact < ?)
                    )""", (new_date_for_lc, pid, new_date_for_lc))

        self.con.commit()
        self.accept()


# ── Versionshistorie ──────────────────────────────────────────────────────────
class VersionHistoryDialog(QDialog):
    def __init__(self, parent, shared_entry_id, cur, con):
        super().__init__(parent)
        self.setWindowTitle("📜 Versionshistorie")
        self.resize(700, 500)
        self.shared_entry_id = shared_entry_id
        self.cur = cur; self.con = con

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Doppelklick auf eine Version zum Anzeigen:"))
        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._view_version)

        self.cur.execute("SELECT type,title,content,entry_date,updated_at FROM shared_entries WHERE id=?",
                         (shared_entry_id,))
        curr = self.cur.fetchone()
        if curr:
            item = QListWidgetItem(f"🟢 AKTUELL | {curr[4]} | {curr[1] or curr[0]}")
            item.setData(Qt.UserRole, {'type':curr[0],'title':curr[1],'content':curr[2],
                                       'entry_date':curr[3],'date':curr[4],'is_current':True})
            self._list.addItem(item)

        self.cur.execute("""SELECT id,type,title,content,entry_date,version_date,version_comment
            FROM entry_versions WHERE shared_entry_id=? ORDER BY version_date DESC""",
            (shared_entry_id,))
        for vid,vt,vtit,vc,ved,vd,vc_comment in self.cur.fetchall():
            txt = f"📌 {vd} | {vtit or vt}"
            if vc_comment: txt += f" – {vc_comment}"
            item = QListWidgetItem(txt)
            item.setData(Qt.UserRole, {'id':vid,'type':vt,'title':vtit,'content':vc,
                                       'entry_date':ved,'date':vd,'is_current':False})
            self._list.addItem(item)

        lay.addWidget(self._list)
        bl = QHBoxLayout()
        restore = QPushButton("🔄 Wiederherstellen"); restore.clicked.connect(self._restore)
        close   = QPushButton("Schließen");            close.clicked.connect(self.accept)
        bl.addWidget(restore); bl.addWidget(close); lay.addLayout(bl)

    def _view_version(self, item):
        d = item.data(Qt.UserRole)
        dlg = QDialog(self); dlg.setWindowTitle(f"Version – {d['date']}"); dlg.resize(600,400)
        l = QVBoxLayout(dlg)
        if not d.get('is_current'):
            w = QLabel("🔒 Schreibgeschützte alte Version")
            w.setStyleSheet("color:orange;padding:5px;")
            l.addWidget(w)
        ti = QLineEdit(d.get('title','') or ''); ti.setReadOnly(True); l.addWidget(QLabel("Titel:")); l.addWidget(ti)
        ct = QTextEdit(d.get('content','') or ''); ct.setReadOnly(True); l.addWidget(QLabel("Inhalt:")); l.addWidget(ct)
        cl = QPushButton("Schließen"); cl.clicked.connect(dlg.accept); l.addWidget(cl)
        dlg.exec()

    def _restore(self):
        item = self._list.currentItem()
        if not item: return
        d = item.data(Qt.UserRole)
        if d.get('is_current'):
            QMessageBox.information(self, "Info", "Das ist bereits die aktuelle Version"); return
        if QMessageBox.question(self, "Wiederherstellen",
                                f"Version vom {d['date']} wiederherstellen?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.cur.execute("SELECT type,title,content,entry_date FROM shared_entries WHERE id=?",
                             (self.shared_entry_id,))
            curr = self.cur.fetchone()
            if curr:
                self.cur.execute("""INSERT INTO entry_versions
                    (shared_entry_id,type,title,content,entry_date,version_comment)
                    VALUES (?,?,?,?,?,?)""",
                    (self.shared_entry_id, *curr, "Automatisch vor Wiederherstellung"))
            self.cur.execute("""UPDATE shared_entries
                SET type=?,title=?,content=?,entry_date=?,updated_at=? WHERE id=?""",
                (d['type'],d['title'],d['content'],d['entry_date'],
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), self.shared_entry_id))
            self.con.commit()
            QMessageBox.information(self, "Erfolg", "Version wiederhergestellt")
            self.accept()


# ── Dashboard Widget ──────────────────────────────────────────────────────────
class DashboardWidget(QWidget):
    def __init__(self, cur, con, parent=None):
        super().__init__(parent)
        self.cur = cur; self.con = con
        lay = QVBoxLayout(self)

        title = QLabel("📊 Dashboard")
        title.setStyleSheet("font-size:22px;font-weight:bold;padding:10px;")
        lay.addWidget(title)

        # Statistik-Kacheln
        stats_row = QHBoxLayout()
        self.cur.execute("SELECT COUNT(*) FROM persons WHERE is_group=0")
        self._stat_persons = self.cur.fetchone()[0]
        self.cur.execute("SELECT COUNT(*) FROM entries")
        self._stat_entries = self.cur.fetchone()[0]
        self.cur.execute("SELECT COUNT(*) FROM reminders WHERE completed=0 AND reminder_date>=date('now')")
        self._stat_reminders = self.cur.fetchone()[0]
        self.cur.execute("SELECT COUNT(*) FROM shared_entries WHERE is_draft=1")
        self._stat_drafts = self.cur.fetchone()[0]

        for label, val, color in [
            ("👥 Personen",    str(self._stat_persons),   "#3498db"),
            ("📝 Einträge",    str(self._stat_entries),   "#2ecc71"),
            ("📌 Erinnerungen",str(self._stat_reminders), "#e74c3c"),
            ("📋 Drafts",      str(self._stat_drafts),    "#e67e22"),
        ]:
            stats_row.addWidget(self._card(label, val, color))
        lay.addLayout(stats_row)

        # Fällige Kontakte
        lay.addWidget(self._section("⏰ Kontakt überfällig"))
        self.neglected_list = QListWidget()
        self._load_neglected()
        lay.addWidget(self.neglected_list)

        # Geburtstage
        lay.addWidget(self._section("🎂 Bald Geburtstag (nächste 30 Tage)"))
        self.birthday_list = QListWidget()
        self._load_birthdays()
        lay.addWidget(self.birthday_list)

        lay.addStretch()

    @staticmethod
    def _card(title, value, color):
        card = QWidget()
        card.setStyleSheet(f"QWidget{{background:{color};border-radius:10px;padding:16px;}}")
        cl = QVBoxLayout(card)
        tl = QLabel(title); tl.setStyleSheet("color:white;font-size:13px;")
        vl = QLabel(value); vl.setStyleSheet("color:white;font-size:28px;font-weight:bold;")
        cl.addWidget(tl); cl.addWidget(vl)
        return card

    @staticmethod
    def _section(text):
        lbl = QLabel(f"<b>{text}</b>")
        lbl.setStyleSheet("font-size:14px;padding:6px 0 2px 0;")
        return lbl

    def _load_neglected(self):
        self.neglected_list.clear()
        self.cur.execute("""
            SELECT id, name, last_contact, contact_interval_days,
                   CAST(julianday('now') - julianday(last_contact) AS INTEGER) AS days_since
            FROM persons
            WHERE last_contact IS NOT NULL
              AND last_contact != ''
              AND length(last_contact) = 10
              AND contact_interval_days > 0
              AND (julianday('now') - julianday(last_contact)) > contact_interval_days
            ORDER BY days_since DESC LIMIT 15
        """)
        for pid, name, lc, interval, days in self.cur.fetchall():
            over = days - interval
            item = QListWidgetItem(
                f"⚠️ {name}  —  Letzter Kontakt: {format_date_german(lc)}  "
                f"({over} Tage überfällig)")
            item.setData(Qt.UserRole, pid)
            if over > 30:
                item.setBackground(QColor("#ffe0b2"))
            elif over > 14:
                item.setBackground(QColor("#fff9c4"))
            self.neglected_list.addItem(item)
        if not self.neglected_list.count():
            self.neglected_list.addItem("✅ Alle Kontakte aktuell!")

    def _load_birthdays(self):
        self.birthday_list.clear()
        self.cur.execute("SELECT id, name, birthday FROM persons WHERE birthday IS NOT NULL AND is_group=0")
        today    = datetime.now()
        upcoming = []
        for pid, name, bday in self.cur.fetchall():
            try:
                parts = str(bday).strip().split('.')
                if len(parts) < 2: continue
                day, month = int(parts[0]), int(parts[1])
                bday_this  = datetime(today.year, month, day)
                nxt        = bday_this if bday_this >= today else datetime(today.year + 1, month, day)
                days_until = (nxt - today).days
                if days_until <= 30:
                    upcoming.append((pid, name, bday, days_until))
            except: pass
        upcoming.sort(key=lambda x: x[3])
        for pid, name, bday, days in upcoming:
            if   days == 0: txt = f"🎉 {name} – HEUTE! ({bday})"
            elif days == 1: txt = f"🎂 {name} – MORGEN ({bday})"
            else:           txt = f"🎂 {name} – in {days} Tagen ({bday})"
            item = QListWidgetItem(txt)
            item.setData(Qt.UserRole, pid)
            if days <= 3:
                item.setBackground(QColor("#c8e6c9"))
            self.birthday_list.addItem(item)
        if not self.birthday_list.count():
            self.birthday_list.addItem("Keine Geburtstage in den nächsten 30 Tagen")

    def refresh(self):
        self._load_neglected()
        self._load_birthdays()


# ── Papierkorb ────────────────────────────────────────────────────────────────
class TrashDialog(QDialog):
    def __init__(self, parent, cur, con):
        super().__init__(parent)
        self.setWindowTitle("🗑️ Papierkorb")
        self.resize(700, 500)
        self.cur = cur; self.con = con
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<h2>🗑️ Gelöschte Einträge</h2>"))
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.MultiSelection)
        self._data: list[int] = []
        self._load(); lay.addWidget(self._list)
        bl = QHBoxLayout()
        for lbl, fn in [("♻️ Wiederherstellen", self._restore),
                         ("🗑️ Endgültig löschen", self._delete_perm),
                         ("🔥 Papierkorb leeren", self._empty),
                         ("Schließen",             self.accept)]:
            b = QPushButton(lbl); b.clicked.connect(fn); bl.addWidget(b)
        lay.addLayout(bl)

    def _load(self):
        self._list.clear(); self._data.clear()
        self.cur.execute("""SELECT e.id, se.type, se.title, se.entry_date, p.name, se.updated_at
            FROM entries e JOIN shared_entries se ON e.shared_entry_id=se.id
            JOIN persons p ON e.person_id=p.id
            WHERE e.hidden=2 ORDER BY se.updated_at DESC""")
        for eid, et, tit, ed, pn, del_at in self.cur.fetchall():
            icon = {"Notiz":"📝","Treffen":"👥","Telefonat":"📞"}.get(et, "📄")
            self._list.addItem(f"{icon} {pn} | {format_date_german(ed)} | {tit or et}  (gelöscht: {del_at})")
            self._data.append(eid)

    def _restore(self):
        sel = self._list.selectedItems()
        if not sel: return
        for item in sel:
            self.cur.execute("UPDATE entries SET hidden=0 WHERE id=?", (self._data[self._list.row(item)],))
        self.con.commit(); self._load()
        QMessageBox.information(self, "Erfolg", f"{len(sel)} Einträge wiederhergestellt")

    def _delete_perm(self):
        sel = self._list.selectedItems()
        if not sel: return
        if QMessageBox.warning(self, "Löschen", f"{len(sel)} Eintrag/Einträge ENDGÜLTIG löschen?",
                               QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            for item in sel:
                self.cur.execute("DELETE FROM entries WHERE id=?", (self._data[self._list.row(item)],))
            self.con.commit(); self._load()

    def _empty(self):
        if QMessageBox.warning(self, "Papierkorb leeren",
                               "Gesamten Papierkorb leeren?",
                               QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.cur.execute("DELETE FROM entries WHERE hidden=2")
            self.con.commit(); self._load()


# ── Beziehungs-Dialog ─────────────────────────────────────────────────────────
class RelationshipDialog(QDialog):
    def __init__(self, parent, person_id, cur, con):
        super().__init__(parent)
        self.setWindowTitle("👥 Beziehungen")
        self.resize(800, 600)
        self.person_id = person_id; self.cur = cur; self.con = con
        self.cur.execute("SELECT name FROM persons WHERE id=?", (person_id,))
        self.person_name = self.cur.fetchone()[0]

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<h3>Beziehungen von {self.person_name}</h3>"))

        tabs = QTabWidget()

        # Tab 1: Beziehungen
        t1 = QWidget(); l1 = QVBoxLayout(t1)
        self._rel_list = QListWidget(); self._rel_data: list = []; self._load_relationships()
        l1.addWidget(self._rel_list)
        bl1 = QHBoxLayout()
        for lbl, fn in [("➕ Hinzufügen", self._add_rel),
                         ("✏️ Bearbeiten", self._edit_rel),
                         ("🗑️ Löschen",   self._del_rel)]:
            b = QPushButton(lbl); b.clicked.connect(fn); bl1.addWidget(b)
        l1.addLayout(bl1); tabs.addTab(t1, "🔗 Beziehungen")

        # Tab 2: Gemeinsame Events
        t2 = QWidget(); l2 = QVBoxLayout(t2)
        self._ev_list = QListWidget(); self._load_events()
        l2.addWidget(self._ev_list); tabs.addTab(t2, "📅 Gemeinsame Events")

        # Tab 3: Vorschläge
        t3 = QWidget(); l3 = QVBoxLayout(t3)
        self._sug_list = QListWidget(); self._sug_data: list[int] = []; self._load_suggestions()
        l3.addWidget(self._sug_list)
        cb = QPushButton("✅ Beziehung erstellen"); cb.clicked.connect(self._create_suggested)
        l3.addWidget(cb); tabs.addTab(t3, "💡 Vorschläge")

        lay.addWidget(tabs)
        close = QPushButton("Schließen"); close.clicked.connect(self.accept); lay.addWidget(close)

    def _load_relationships(self):
        self._rel_list.clear(); self._rel_data.clear()
        for sql, direction in [
            (("SELECT r.id,p.name,r.relationship_type,r.description,r.created_at,r.auto_created "
              "FROM relationships r JOIN persons p ON r.person_b_id=p.id WHERE r.person_a_id=?"), 'outgoing'),
            (("SELECT r.id,p.name,r.relationship_type,r.description,r.created_at,0 "
              "FROM relationships r JOIN persons p ON r.person_a_id=p.id WHERE r.person_b_id=?"), 'incoming'),
        ]:
            for row in self.cur.execute(sql, (self.person_id,)):
                rid, name, rt, desc, created, auto = row
                arrow = "→" if direction == 'outgoing' else "←"
                txt = f"{arrow} {name} ({rt})"
                if desc: txt += f" – {desc}"
                if auto: txt += " [Auto]"
                self._rel_list.addItem(txt)
                self._rel_data.append((direction, rid))

    def _add_rel(self):
        self.cur.execute("SELECT id,name FROM persons WHERE id!=? ORDER BY name", (self.person_id,))
        persons = self.cur.fetchall()
        if not persons: return
        names = [n for _,n in persons]
        pname, ok = QInputDialog.getItem(self, "Person", "Mit welcher Person?", names, 0, False)
        if not ok: return
        pb_id = persons[names.index(pname)][0]
        types = ["Freund","Familie","Kollege","Partner","Bekannter","Sonstiges"]
        rtype, ok2 = QInputDialog.getItem(self, "Typ", "Beziehungstyp:", types, 0, False)
        if not ok2: return
        desc, _ = QInputDialog.getText(self, "Beschreibung", "Optional:")
        self.cur.execute("""INSERT INTO relationships (person_a_id,person_b_id,relationship_type,description,created_at,auto_created)
            VALUES (?,?,?,?,?,0)""",
            (self.person_id, pb_id, rtype, desc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self.con.commit(); self._load_relationships()

    def _edit_rel(self):
        row = self._rel_list.currentRow()
        if row < 0: return
        direction, rid = self._rel_data[row]
        if direction == 'incoming':
            QMessageBox.information(self, "Info", "Eingehende Beziehungen nicht editierbar"); return
        self.cur.execute("SELECT relationship_type,description FROM relationships WHERE id=?", (rid,))
        rt, desc = self.cur.fetchone()
        types = ["Freund","Familie","Kollege","Partner","Bekannter","Sonstiges"]
        ntype, ok = QInputDialog.getItem(self, "Typ ändern", "Neuer Typ:", types,
                                         types.index(rt) if rt in types else 0, False)
        if ok:
            ndesc, ok2 = QInputDialog.getText(self, "Beschreibung", "Beschreibung:", text=desc or "")
            if ok2:
                self.cur.execute("UPDATE relationships SET relationship_type=?,description=? WHERE id=?",
                                 (ntype, ndesc, rid))
                self.con.commit(); self._load_relationships()

    def _del_rel(self):
        row = self._rel_list.currentRow()
        if row < 0: return
        _, rid = self._rel_data[row]
        if QMessageBox.question(self, "Löschen", "Beziehung löschen?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.cur.execute("DELETE FROM relationships WHERE id=?", (rid,))
            self.con.commit(); self._load_relationships()

    def _load_events(self):
        self._ev_list.clear()
        self.cur.execute("""
            SELECT DISTINCT se.type, se.title, se.entry_date,
                   GROUP_CONCAT(DISTINCT p2.name) as others
            FROM entries e1 JOIN shared_entries se ON e1.shared_entry_id=se.id
            JOIN entries e2 ON se.id=e2.shared_entry_id AND e2.person_id!=?
            JOIN persons p2 ON e2.person_id=p2.id
            WHERE e1.person_id=? AND se.type IN ('Treffen','Telefonat')
            GROUP BY se.id ORDER BY se.entry_date DESC LIMIT 50
        """, (self.person_id, self.person_id))
        for et, tit, ed, others in self.cur.fetchall():
            icon = "👥" if et == "Treffen" else "📞"
            self._ev_list.addItem(f"{icon} {format_date_german(ed)} | {tit or et} | Mit: {others}")

    def _load_suggestions(self):
        self._sug_list.clear(); self._sug_data.clear()
        self.cur.execute("""
            SELECT p.id, p.name, COUNT(DISTINCT se.id) AS cnt
            FROM entries e1 JOIN shared_entries se ON e1.shared_entry_id=se.id
            JOIN entries e2 ON se.id=e2.shared_entry_id AND e2.person_id!=?
            JOIN persons p ON e2.person_id=p.id
            LEFT JOIN relationships r ON (
                (r.person_a_id=? AND r.person_b_id=p.id) OR
                (r.person_b_id=? AND r.person_a_id=p.id))
            WHERE e1.person_id=? AND r.id IS NULL
            GROUP BY p.id HAVING cnt>=2 ORDER BY cnt DESC LIMIT 20
        """, (self.person_id,)*4)
        for pid, name, cnt in self.cur.fetchall():
            self._sug_list.addItem(f"👤 {name} ({cnt} gemeinsame Events)")
            self._sug_data.append(pid)

    def _create_suggested(self):
        row = self._sug_list.currentRow()
        if row < 0: return
        pb_id = self._sug_data[row]
        types = ["Freund","Familie","Kollege","Partner","Bekannter"]
        rtype, ok = QInputDialog.getItem(self, "Typ", "Beziehungstyp:", types, 0, False)
        if ok:
            self.cur.execute("""INSERT INTO relationships
                (person_a_id,person_b_id,relationship_type,created_at,auto_created) VALUES (?,?,?,?,0)""",
                (self.person_id, pb_id, rtype, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            self.con.commit(); self._load_relationships(); self._load_suggestions()


# ── Gruppen ───────────────────────────────────────────────────────────────────
class GroupDialog(QDialog):
    def __init__(self, parent, cur, con):
        super().__init__(parent)
        self.setWindowTitle("📁 Gruppen verwalten")
        self.resize(700, 500)
        self.cur = cur; self.con = con; self.main_window = parent
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<h3>📁 Gruppen</h3>"))
        self._list = QListWidget()
        self._data: list[int] = []
        self._list.itemDoubleClicked.connect(self._edit)
        self._load(); lay.addWidget(self._list)
        bl = QHBoxLayout()
        for lbl, fn in [("➕ Neue Gruppe", self._create),
                         ("✏️ Bearbeiten",  lambda: self._edit(self._list.currentItem())),
                         ("🗑️ Löschen",    self._delete),
                         ("Schließen",      self.accept)]:
            b = QPushButton(lbl); b.clicked.connect(fn); bl.addWidget(b)
        lay.addLayout(bl)

    def _load(self):
        self._list.clear(); self._data.clear()
        self.cur.execute("""SELECT p.id, p.name, p.category,
            COUNT(DISTINCT e.shared_entry_id) AS ec,
            (SELECT COUNT(*) FROM group_members gm WHERE gm.group_id=p.id) AS mc
            FROM persons p LEFT JOIN entries e ON p.id=e.person_id
            WHERE p.is_group=1 GROUP BY p.id ORDER BY p.name""")
        for pid, name, cat, ec, mc in self.cur.fetchall():
            icon = {"group":"📚","project":"💼","team":"👥"}.get(cat, "📁")
            self._list.addItem(f"{icon} {name} | {mc} Mitglieder | {ec} Einträge")
            self._data.append(pid)

    def _create(self):
        name, ok = QInputDialog.getText(self, "Neue Gruppe", "Name:")
        if not ok or not name.strip(): return
        types = ["group","project","team"]
        labels = ["Gruppe (Buchclub, …)","Projekt (Hausbau, …)","Team (Sportgruppe, …)"]
        lbl, ok2 = QInputDialog.getItem(self, "Typ", "Typ:", labels, 0, False)
        if not ok2: return
        gtype = types[labels.index(lbl)]
        self.cur.execute("INSERT INTO persons (name,category,is_group,created_at) VALUES (?,?,1,?)",
                         (name.strip(), gtype, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        self.con.commit()
        gid = self.cur.lastrowid
        self._add_members(gid); self._load(); self.main_window.load_persons()

    def _add_members(self, gid):
        self.cur.execute("SELECT id,name FROM persons WHERE is_group=0 ORDER BY name")
        persons = self.cur.fetchall()
        if not persons: return
        dlg = QDialog(self); dlg.setWindowTitle("Mitglieder"); dlg.resize(400,500)
        l = QVBoxLayout(dlg); l.addWidget(QLabel("Mitglieder auswählen:"))
        scroll = QScrollArea(); sw = QWidget(); sl = QVBoxLayout(sw)
        cbs: dict[int, QCheckBox] = {}
        for pid, pname in persons:
            cb = QCheckBox(f"👤 {pname}"); sl.addWidget(cb); cbs[pid] = cb
        scroll.setWidget(sw); scroll.setWidgetResizable(True); l.addWidget(scroll)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); l.addWidget(bb)
        if dlg.exec():
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for pid, cb in cbs.items():
                if cb.isChecked():
                    self.cur.execute("INSERT INTO group_members (group_id,person_id,joined_at) VALUES (?,?,?)",
                                     (gid, pid, now))
            self.con.commit()

    def _edit(self, item):
        if not item: return
        idx = self._list.row(item)
        if idx < 0 or idx >= len(self._data): return
        gid = self._data[idx]
        # Punkt 6: Vollständiger Mitglieder-/Historie-Manager
        GroupMemberManagerDialog(self, gid, self.cur, self.con).exec()
        self._load(); self.main_window.load_persons()

    def _rename_only(self, gid):
        """Nur Umbenennen — über Kontextmenü erreichbar (separat zur
        Mitglieder-Verwaltung)."""
        self.cur.execute("SELECT name FROM persons WHERE id=?", (gid,))
        row = self.cur.fetchone()
        if not row:
            return
        new_name, ok = QInputDialog.getText(self, "Name ändern",
                                            "Neuer Name:", text=row[0])
        if ok and new_name.strip():
            self.cur.execute("UPDATE persons SET name=? WHERE id=?",
                             (new_name.strip(), gid))
            self.con.commit()
            self._load(); self.main_window.load_persons()

    def _delete(self):
        row = self._list.currentRow()
        if row < 0: return
        gid = self._data[row]
        if QMessageBox.question(self, "Löschen", "Gruppe wirklich löschen?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            for tbl in ["group_members","entries"]:
                self.cur.execute(f"DELETE FROM {tbl} WHERE {'group_id' if tbl=='group_members' else 'person_id'}=?", (gid,))
            self.cur.execute("DELETE FROM persons WHERE id=?", (gid,))
            self.con.commit(); self._load(); self.main_window.load_persons()


# ── Kalender & Erinnerungen ───────────────────────────────────────────────────
class CalendarDialog(QDialog):
    def __init__(self, parent, cur, con):
        super().__init__(parent)
        self.setWindowTitle("📅 Kalender & Erinnerungen")
        self.resize(800, 600)
        self.cur = cur; self.con = con; self.main_window = parent
        lay = QVBoxLayout(self)
        self._calendar = QCalendarWidget()
        self._calendar.clicked.connect(self._date_clicked)
        lay.addWidget(self._calendar)
        self._mark_days()
        lay.addWidget(QLabel("📌 Erinnerungen:"))
        self._rem_list = QListWidget()
        self._rem_list.itemDoubleClicked.connect(self._edit_reminder)
        self._rem_data: list[int] = []
        self._load_reminders(); lay.addWidget(self._rem_list)
        bl = QHBoxLayout()
        new_btn = QPushButton("➕ Neue Erinnerung"); new_btn.clicked.connect(self._new_reminder)
        close   = QPushButton("Schließen");           close.clicked.connect(self.accept)
        bl.addWidget(new_btn); bl.addWidget(close); lay.addLayout(bl)

    def _mark_days(self):
        fmt = QTextCharFormat(); fmt.setBackground(QColor(200,200,200))
        self.cur.execute("SELECT DISTINCT entry_date FROM shared_entries WHERE entry_date IS NOT NULL")
        for (ds,) in self.cur.fetchall():
            try:
                if '-' in str(ds):
                    y,m,d = map(int, ds.split('-'))
                elif '.' in str(ds):
                    d,m,y = map(int, ds.split('.'))
                else: continue
                q = QDate(y,m,d)
                if q.isValid(): self._calendar.setDateTextFormat(q, fmt)
            except: pass

    def _date_clicked(self, date):
        ds = date.toString("yyyy-MM-dd")
        self.cur.execute("""
            SELECT e.id, se.type, se.title, p.name
            FROM entries e JOIN shared_entries se ON e.shared_entry_id=se.id
            JOIN persons p ON e.person_id=p.id
            WHERE se.entry_date=? ORDER BY se.created_at DESC
        """, (ds,))
        rows = self.cur.fetchall()
        if rows:
            msg = f"Einträge am {format_date_german(ds)}:\n\n"
            for _, et, tit, pn in rows:
                msg += f"• {pn}: {tit or et}\n"
            QMessageBox.information(self, "Einträge", msg)
        else:
            QMessageBox.information(self, "Keine Einträge", f"Keine Einträge am {format_date_german(ds)}")

    def _load_reminders(self):
        self._rem_list.clear(); self._rem_data.clear()
        self.cur.execute("""SELECT r.id, r.reminder_date, r.title, p.name, r.completed
            FROM reminders r LEFT JOIN persons p ON r.person_id=p.id
            ORDER BY r.completed ASC, r.reminder_date ASC""")
        for rid, rdate, title, pname, done in self.cur.fetchall():
            mark = "✅ " if done else "📌 "
            txt  = f"{mark}{rdate} | {title}" + (f" ({pname})" if pname else "")
            item = QListWidgetItem(txt)
            if done:
                f = item.font(); f.setStrikeOut(True); item.setFont(f)
                item.setForeground(Qt.gray)
            self._rem_list.addItem(item); self._rem_data.append(rid)

    def _new_reminder(self):
        ds = self._calendar.selectedDate().toString("yyyy-MM-dd")
        title, ok = QInputDialog.getText(self, "Neue Erinnerung", "Titel:")
        if ok and title.strip():
            self.cur.execute("""INSERT INTO reminders (reminder_date,title,created_at,completed)
                VALUES (?,?,?,0)""",
                (ds, title.strip(), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            self.con.commit(); self._load_reminders(); self._mark_days()

    def _edit_reminder(self, item):
        idx = self._rem_list.row(item)
        if idx < len(self._rem_data):
            rid = self._rem_data[idx]
            # Einfacher Toggle erledigt / nicht erledigt
            self.cur.execute("SELECT completed FROM reminders WHERE id=?", (rid,))
            done = self.cur.fetchone()[0]
            self.cur.execute("UPDATE reminders SET completed=? WHERE id=?", (0 if done else 1, rid))
            self.con.commit(); self._load_reminders()


# ── Import / Export ───────────────────────────────────────────────────────────
class ImportExportDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("📁 Import / Export")
        self.resize(480, 380)
        self.mw = parent
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<h2>Export</h2>"))
        for lbl, fn in [
            ("📊 Alle Personen → CSV",      lambda: self.mw.export_csv(self)),
            ("📊 Aktuelle Person → CSV",     lambda: self.mw.export_csv_person(self)),
            ("📄 Aktuelle Person → HTML",    lambda: self.mw.export_pdf(self)),
        ]:
            b = QPushButton(lbl); b.clicked.connect(fn); lay.addWidget(b)
        lay.addWidget(QLabel("<h2>Import</h2>"))
        for lbl, fn in [
            ("📥 Personen aus CSV",          lambda: self.mw.import_csv(self)),
            ("📱 WhatsApp Chat importieren", lambda: self.mw.import_whatsapp(self)),
        ]:
            b = QPushButton(lbl); b.clicked.connect(fn); lay.addWidget(b)
        close = QPushButton("Schließen"); close.clicked.connect(self.accept); lay.addWidget(close)


# ── Suche ─────────────────────────────────────────────────────────────────────
class SearchResultsDialog(QDialog):
    def __init__(self, main_window, query, results):
        super().__init__(main_window)
        self.setWindowTitle(f"🔍 Suchergebnisse: {query}")
        self.resize(700, 600)
        self.mw = main_window
        lay = QVBoxLayout(self)
        total = len(results['persons']) + len(results['entries']) + len(results['files'])
        lay.addWidget(QLabel(f"<b>{total} Treffer für '{query}'</b>"))
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        sc = QWidget(); sl = QVBoxLayout(sc)

        for section, items, builder in [
            ("👤 Personen", results['persons'], self._person_widget),
            ("📝 Einträge", results['entries'], self._entry_widget),
            ("📎 Dateien",  results['files'],   self._file_widget),
        ]:
            if items:
                h = QLabel(f"<b>{section} ({len(items)})</b>")
                h.setStyleSheet("font-size:14px;border-bottom:2px solid #3498db;padding:6px;")
                sl.addWidget(h)
                for it in items: sl.addWidget(builder(it))

        sl.addStretch(); scroll.setWidget(sc); lay.addWidget(scroll)
        close = QPushButton("Schließen"); close.clicked.connect(self.accept); lay.addWidget(close)

    @staticmethod
    def _hl(text):
        if not text: return ""
        return text.replace('<<<', '<span style="background:#ffeb3b;font-weight:bold;">')\
                   .replace('>>>', '</span>')

    def _card_widget(self, on_click):
        w = QWidget(); w.setCursor(Qt.PointingHandCursor)
        w.setStyleSheet("QWidget{background:#f8f9fa;border:1px solid #ddd;border-radius:5px;}"
                        "QWidget:hover{background:#e3f2fd;border:1px solid #2196f3;}")
        w.mousePressEvent = lambda _: on_click()
        return w

    def _person_widget(self, p):
        w = self._card_widget(lambda: (self.mw.select_person_by_id(p['id']), self.accept()))
        l = QVBoxLayout(w); l.setContentsMargins(10,8,10,8)
        hl = QLabel(f"👤 {self._hl(p['snippets']['name'] or p['name'])} ({p['category']})")
        hl.setTextFormat(Qt.RichText); hl.setStyleSheet("font-weight:bold;")
        l.addWidget(hl)
        if p['occupation']:
            l.addWidget(QLabel(f"  💼 {p['occupation']}"))
        return w

    def _entry_widget(self, e):
        w = self._card_widget(lambda: (self.mw.open_entry_by_id(e['entry_id']), self.accept()))
        l = QVBoxLayout(w); l.setContentsMargins(10,8,10,8)
        icon = {"Notiz":"📝","Treffen":"👥","Telefonat":"📞","KI":"🤖"}.get(e['type'], "📄")
        h = QLabel(f"{icon} {format_date_german(e['date'])} | {self._hl(e['snippets']['title'] or e['title'] or e['type'])}")
        h.setTextFormat(Qt.RichText); h.setStyleSheet("font-weight:bold;")
        l.addWidget(h)
        l.addWidget(QLabel(f"  Mit: {e['person']}"))
        ct = QLabel(f"  {self._hl(e['snippets']['content'])}"); ct.setTextFormat(Qt.RichText); ct.setWordWrap(True)
        l.addWidget(ct)
        return w

    def _file_widget(self, f):
        w = self._card_widget(lambda: None)
        l = QVBoxLayout(w); l.setContentsMargins(10,8,10,8)
        l.addWidget(QLabel(f"📎 {f['filename']}"))
        l.addWidget(QLabel(f"  In: {f['entry_title']} ({f['person']})"))
        return w


class AdvancedSearchDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("🔍 Erweiterte Suche")
        self.resize(580, 520)
        self.mw = parent; self.config = Config()
        lay = QVBoxLayout(self)

        sl = QHBoxLayout()
        self._input = QLineEdit(); self._input.setPlaceholderText("Suchbegriff …")
        self._input.returnPressed.connect(self._search)
        sb = QPushButton("🔍 Suchen"); sb.clicked.connect(self._search)
        sl.addWidget(self._input); sl.addWidget(sb); lay.addLayout(sl)

        og = QGroupBox("Suchbereich"); ol = QVBoxLayout()
        self._cb_persons = QCheckBox("👤 Personen"); self._cb_persons.setChecked(True)
        self._cb_entries = QCheckBox("📝 Einträge"); self._cb_entries.setChecked(True)
        self._cb_files   = QCheckBox("📎 Dateien")
        for cb in [self._cb_persons, self._cb_entries, self._cb_files]: ol.addWidget(cb)
        og.setLayout(ol); lay.addWidget(og)

        mg = QGroupBox("Modus"); ml = QVBoxLayout()
        self._mode_all = QRadioButton("Alle Wörter (UND)"); self._mode_all.setChecked(True)
        self._mode_any = QRadioButton("Mindestens ein Wort (ODER)")
        self._mode_exact = QRadioButton("Exakte Phrase")
        for rb in [self._mode_all, self._mode_any, self._mode_exact]: ml.addWidget(rb)
        mg.setLayout(ml); lay.addWidget(mg)

        dg = QGroupBox("Zeitraum"); dl = QFormLayout()
        self._from = QLineEdit(); self._from.setPlaceholderText("YYYY-MM-DD")
        self._to   = QLineEdit(); self._to.setPlaceholderText("YYYY-MM-DD")
        dl.addRow("Von:", self._from); dl.addRow("Bis:", self._to); dg.setLayout(dl); lay.addWidget(dg)

        close = QPushButton("Schließen"); close.clicked.connect(self.accept); lay.addWidget(close)

    def _search(self):
        q = self._input.text().strip()
        if not q: QMessageBox.warning(self, "Fehler", "Suchbegriff eingeben"); return
        if self._mode_exact.isChecked(): fts_q = f'"{q}"'
        elif self._mode_any.isChecked():  fts_q = ' OR '.join(q.split())
        else:                              fts_q = q
        filters = {'search_persons': self._cb_persons.isChecked(),
                   'search_entries': self._cb_entries.isChecked(),
                   'search_files':   self._cb_files.isChecked(),
                   'date_from': self._from.text() or None,
                   'date_to':   self._to.text()   or None}
        results = FullTextSearch(self.mw.con).search(fts_q, filters)
        SearchResultsDialog(self.mw, q, results).exec()


# ── WhatsApp Import ───────────────────────────────────────────────────────────
class WhatsAppImportDialog(QDialog):
    def __init__(self, parent, entries, cur, con):
        super().__init__(parent)
        self.setWindowTitle("📱 WhatsApp Import")
        self.resize(1000, 700)
        self.entries = entries; self.cur = cur; self.con = con
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>{len(entries)} Nachrichten gefunden</b>"))

        sf = QHBoxLayout(); sf.addWidget(QLabel("Absender:"))
        self._sender_cb = QComboBox(); self._sender_cb.addItem("Alle")
        for s in sorted({e['sender'] for e in entries}):
            cnt = sum(1 for e in entries if e['sender'] == s)
            self._sender_cb.addItem(f"{s} ({cnt})")
        self._sender_cb.currentTextChanged.connect(self._filter)
        sf.addWidget(self._sender_cb); sf.addStretch(); lay.addLayout(sf)

        self._table = QTableWidget(); self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(['☑','Datum','Zeit','Absender','Nachricht','Länge'])
        self._fill_table(); lay.addWidget(self._table)

        self._stats = QLabel(); self._update_stats(); lay.addWidget(self._stats)

        bl = QHBoxLayout()
        for lbl, fn in [("✅ Alle", self._all), ("❌ Keine", self._none),
                         ("📝 Nur lang (>100)", self._long),
                         ("⬇️ Importieren", self._do_import),
                         ("Abbrechen", self.reject)]:
            b = QPushButton(lbl); b.clicked.connect(fn)
            if lbl == "⬇️ Importieren":
                b.setStyleSheet("background:#2ecc71;color:white;font-weight:bold;")
            bl.addWidget(b)
        lay.addLayout(bl)

    def _fill_table(self):
        self._table.setRowCount(len(self.entries))
        for i, e in enumerate(self.entries):
            cb = QCheckBox(); cb.setChecked(len(e['message']) > 100)
            cb.stateChanged.connect(self._update_stats)
            self._table.setCellWidget(i, 0, cb)
            for j, val in enumerate([e['date'], e['time'], e['sender'],
                                      (e['message'][:150] + "..." if len(e['message']) > 150
                                       else e['message']).replace('\n',' '),
                                      str(len(e['message']))], 1):
                self._table.setItem(i, j, QTableWidgetItem(val))
        self._table.setColumnWidth(0,50); self._table.setColumnWidth(4,450)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

    def _filter(self):
        txt = self._sender_cb.currentText()
        sender = txt.split(" (")[0] if txt != "Alle" else None
        for i, e in enumerate(self.entries):
            self._table.setRowHidden(i, sender is not None and e['sender'] != sender)

    def _update_stats(self):
        sel = sum(1 for i in range(self._table.rowCount())
                  if not self._table.isRowHidden(i) and
                  (w := self._table.cellWidget(i, 0)) and w.isChecked())
        self._stats.setText(f"<b>{sel}</b> von <b>{len(self.entries)}</b> ausgewählt")

    def _all(self):
        for i in range(self._table.rowCount()):
            if not self._table.isRowHidden(i):
                w = self._table.cellWidget(i, 0)
                if w: w.setChecked(True)

    def _none(self):
        for i in range(self._table.rowCount()):
            w = self._table.cellWidget(i, 0)
            if w: w.setChecked(False)

    def _long(self):
        for i, e in enumerate(self.entries):
            w = self._table.cellWidget(i, 0)
            if w: w.setChecked(len(e['message']) > 100)

    def _do_import(self):
        dlg = QDialog(self); dlg.setWindowTitle("Zielperson"); dlg.resize(400, 180)
        l = QVBoxLayout(dlg); l.addWidget(QLabel("Für welche Person importieren?"))
        combo = QComboBox(); combo.addItem("➕ Neue Person erstellen…", None)
        self.cur.execute("SELECT id,name FROM persons WHERE is_group=0 ORDER BY name")
        for pid, pname in self.cur.fetchall(): combo.addItem(f"👤 {pname}", pid)
        l.addWidget(combo)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept); bb.rejected.connect(dlg.reject); l.addWidget(bb)
        if not dlg.exec(): return
        person_id = combo.currentData()
        if person_id is None:
            name, ok = QInputDialog.getText(self, "Neue Person", "Name:")
            if not ok or not name.strip(): return
            self.cur.execute("INSERT INTO persons (name,category,created_at) VALUES (?,?,?)",
                             (name.strip(),'Bekannte',datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            self.con.commit(); person_id = self.cur.lastrowid
        imported = 0
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i, e in enumerate(self.entries):
            w = self._table.cellWidget(i, 0)
            if w and w.isChecked():
                self.cur.execute("""INSERT INTO shared_entries
                    (type,title,content,entry_date,created_at,updated_at,tags)
                    VALUES (?,?,?,?,?,?,?)""",
                    ('Notiz',f"WhatsApp: {e['sender']}",e['message'],e['date'],now,now,'whatsapp,import'))
                sid = self.cur.lastrowid
                self.cur.execute("INSERT INTO entries (person_id,shared_entry_id,hidden,pinned) VALUES (?,?,0,0)",
                                 (person_id, sid))
                imported += 1
        self.con.commit()
        QMessageBox.information(self, "Erfolg", f"✅ {imported} Einträge importiert!")
        self.accept()


# ===========================================================================
# HAUPTFENSTER
# ===========================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("👥 PersonenKartei v2.0")
        self.resize(1600, 950)
        self.con = sqlite3.connect(DB_NAME)
        self.cur = self.con.cursor()
        self.current_person_id: int | None = None
        self.show_hidden   = False
        self.sort_by_recent = False
        self.entries_data: list[int] = []
        self.persons_data: list[int] = []

        self._setup_shortcuts()

        mw = QWidget(); self.setCentralWidget(mw)
        ml = QVBoxLayout(mw)
        self.tabs = QTabWidget(); ml.addWidget(self.tabs)

        kartei = QWidget()
        self._setup_kartei_ui(kartei)
        self.tabs.addTab(kartei, "📇 Kartei")

        self.dashboard = DashboardWidget(self.cur, self.con)
        self.tabs.addTab(self.dashboard, "📊 Dashboard")

        self.load_persons()

    # ── Shortcuts ─────────────────────────────────────────────────────────────
    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+N"), self).activated.connect(self.add_person)
        QShortcut(QKeySequence("Ctrl+E"), self).activated.connect(self.new_entry)
        QShortcut(QKeySequence("Ctrl+F"), self).activated.connect(lambda: self.search_input.setFocus())
        QShortcut(QKeySequence("Ctrl+D"), self).activated.connect(lambda: self.tabs.setCurrentIndex(1))

    # ── Kartei-UI ─────────────────────────────────────────────────────────────
    def _setup_kartei_ui(self, widget):
        splitter = QSplitter(Qt.Horizontal, widget)
        QVBoxLayout(widget).addWidget(splitter)

        # ── Linke Spalte: Personenliste ──────────────────────────────────────
        left = QWidget(); ll = QVBoxLayout(left)

        sl = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Suche… (Strg+F)")
        self.search_input.textChanged.connect(self.load_persons)
        adv_btn = QPushButton("🔍+"); adv_btn.setToolTip("Erweiterte Suche")
        adv_btn.clicked.connect(self.show_advanced_search)
        sl.addWidget(self.search_input); sl.addWidget(adv_btn); ll.addLayout(sl)

        bl1 = QHBoxLayout()
        add_btn  = QPushButton("➕ Person"); add_btn.clicked.connect(self.add_person)
        grp_btn  = QPushButton("📁 Gruppe"); grp_btn.clicked.connect(self.show_groups)
        sort_btn = QPushButton("⏱️ Letzter Kontakt"); sort_btn.clicked.connect(self.toggle_sort)
        self.sort_btn = sort_btn
        for b in [add_btn, grp_btn, sort_btn]: bl1.addWidget(b)
        ll.addLayout(bl1)

        bl2 = QHBoxLayout()
        cal_btn  = QPushButton("📅 Kalender");     cal_btn.clicked.connect(self.show_calendar)
        ie_btn   = QPushButton("📁 Import/Export"); ie_btn.clicked.connect(self.show_import_export)
        bak_btn  = QPushButton("💾 Backup");        bak_btn.clicked.connect(self.show_backup)
        gcal_btn = QPushButton("📅 Google Kalender")
        gcal_btn.setStyleSheet("background:#4285f4;color:white;font-weight:bold;")
        gcal_btn.setToolTip("Kalendereinträge: 'Personen: Beschreibung'")
        gcal_btn.clicked.connect(self.show_google_sync)
        for b in [cal_btn, ie_btn, bak_btn, gcal_btn]: bl2.addWidget(b)
        ll.addLayout(bl2)

        bl3 = QHBoxLayout()
        alias_btn = QPushButton("🏷️ Aliasse")
        alias_btn.setToolTip("Aliasse / Spitznamen für Kalender-Matching bearbeiten")
        alias_btn.clicked.connect(self.show_alias_editor)
        del_btn = QPushButton("🗑️ Person löschen")
        del_btn.setStyleSheet("color:#c0392b;")
        del_btn.clicked.connect(self.delete_person)
        for b in [alias_btn, del_btn]: bl3.addWidget(b)
        ll.addLayout(bl3)

        ll.addWidget(QLabel("👤 Personen & Gruppen"))
        self.person_list = QListWidget()
        self.person_list.itemSelectionChanged.connect(self.load_person)
        self.person_list.itemDoubleClicked.connect(self.edit_person)
        ll.addWidget(self.person_list)
        splitter.addWidget(left)

        # ── Mittlere Spalte: Neuer Eintrag ───────────────────────────────────
        middle = QWidget(); ml = QVBoxLayout(middle)

        self.entry_type = QComboBox()
        self.entry_type.addItems(["Notiz","Hinweis","Gedanke","Treffen","Telefonat","KI"])
        self.entry_type.currentTextChanged.connect(self.update_entry_fields)

        self.entry_title = QLineEdit(); self.entry_title.setPlaceholderText("Titel (optional)")
        self.entry_text  = QTextEdit();  self.entry_text.setPlaceholderText("Text …")
        self.entry_text.setTabChangesFocus(True)

        self.treffen_persons  = QLineEdit(); self.treffen_persons.setPlaceholderText("Teilnehmer (kommagetrennt)")
        self.treffen_ort      = QLineEdit(); self.treffen_ort.setPlaceholderText("Ort")
        self.treffen_themen   = QTextEdit(); self.treffen_themen.setPlaceholderText("Themen …")
        self.treffen_themen.setTabChangesFocus(True)
        self.treffen_gespraech= QTextEdit(); self.treffen_gespraech.setPlaceholderText("Ergebnis / Gefühl …")
        self.treffen_gespraech.setTabChangesFocus(True)

        self.entry_date = SmartDateEdit()

        # Draft-Checkbox für neue Einträge
        self.new_entry_draft_cb = QCheckBox("📋 Als Draft speichern")
        self.new_entry_draft_cb.setStyleSheet("color:#e67e22;")

        ml.addWidget(QLabel("Typ:")); ml.addWidget(self.entry_type)
        ml.addWidget(QLabel("Titel:")); ml.addWidget(self.entry_title)
        ml.addWidget(self.entry_text)
        ml.addWidget(self.treffen_persons)
        ml.addWidget(self.treffen_ort)
        ml.addWidget(QLabel("Themen:")); ml.addWidget(self.treffen_themen)
        ml.addWidget(QLabel("Ergebnis / Gefühl:")); ml.addWidget(self.treffen_gespraech)
        ml.addWidget(QLabel("Datum:")); ml.addWidget(self.entry_date)
        ml.addWidget(self.new_entry_draft_cb)

        eb = QHBoxLayout()
        save_e_btn  = QPushButton("💾 Speichern"); save_e_btn.clicked.connect(self.save_entry)
        save_e_btn.setStyleSheet("background:#27ae60;color:white;font-weight:bold;")
        new_e_btn   = QPushButton("📝 Eintrag öffnen"); new_e_btn.clicked.connect(self.new_entry)
        draft_btn   = QPushButton("📋 Drafts");         draft_btn.clicked.connect(self.show_drafts)
        trash_btn   = QPushButton("🗑️ Papierkorb");    trash_btn.clicked.connect(self.show_trash)
        rel_btn     = QPushButton("👥 Beziehungen");    rel_btn.clicked.connect(self.show_relationships)
        for b in [save_e_btn, new_e_btn, draft_btn, trash_btn, rel_btn]: eb.addWidget(b)
        ml.addLayout(eb)

        # Ausgeblendet-Schalter
        self.show_hidden_btn = QCheckBox("🙈 Ausgeblendete zeigen")
        self.show_hidden_btn.stateChanged.connect(self.toggle_hidden)
        ml.addWidget(self.show_hidden_btn)

        splitter.addWidget(middle)

        # ── Rechte Spalte: Eintrags-Liste ────────────────────────────────────
        right = QWidget(); rl = QVBoxLayout(right)
        self.person_label = QLabel("Keine Person gewählt")
        self.person_label.setStyleSheet("font-weight:bold;font-size:15px;")
        rl.addWidget(self.person_label)

        self.entries_view = QListWidget()
        self.entries_view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.entries_view.itemDoubleClicked.connect(self.edit_entry)
        self.entries_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.entries_view.customContextMenuRequested.connect(self._entry_context_menu)
        rl.addWidget(QLabel("📂 Kartei")); rl.addWidget(self.entries_view)
        splitter.addWidget(right)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 2)
        self.update_entry_fields(self.entry_type.currentText())

    # ── Personen laden & anzeigen ─────────────────────────────────────────────
    def load_persons(self):
        """
        Bug-Fix: Cursor-Wiederverwendung — Ergebnisse der Hauptquery werden
        zuerst materialisiert (fetchall), und Aliasse/Geburtstage werden
        in einem Dict vor-geladen, damit kein anderer execute() den
        laufenden Iterator stört.

        Punkt 5: Gruppen werden in der Personenliste mit angezeigt
        (📁-Icon, klickbar wie Personen).
        Filtert gelöschte Einträge aus.
        """
        self.person_list.clear()
        self.persons_data.clear()
        s = self.search_input.text().strip().lower()
        today = datetime.now().date()

        # Filter-Klauseln defensiv aufbauen
        self.cur.execute("PRAGMA table_info(persons)")
        cols = {r[1] for r in self.cur.fetchall()}
        where_parts = []
        if 'deleted' in cols: where_parts.append("COALESCE(p.deleted,0) = 0")
        where_sql = " WHERE " + " AND ".join(where_parts) if where_parts else ""

        if self.sort_by_recent:
            order_sql = "ORDER BY COALESCE(p.last_contact,'0000-00-00') DESC, p.name"
        else:
            # Gruppen ans Ende, sonst Favoriten + Name
            order_sql = "ORDER BY COALESCE(p.is_group,0) ASC, p.favorite DESC, p.name"

        query = f"""SELECT p.id, p.name, p.category, p.favorite, p.last_contact,
                          p.contact_interval_days,
                          CAST(julianday('now')-julianday(p.last_contact) AS INTEGER) AS ds,
                          COALESCE(p.aliases, '') AS aliases,
                          p.birthday,
                          COALESCE(p.is_group, 0) AS is_group
                   FROM persons p
                   {where_sql}
                   {order_sql}"""

        rows = self.cur.execute(query).fetchall()

        for (pid, name, cat, fav, lc, interval, ds,
             aliases_raw, bday_raw, is_group) in rows:

            matches_search = (
                (not s)
                or (s in (name or "").lower())
                or (s in (cat or "").lower())
                or (s in (aliases_raw or "").lower())
            )

            # Icon je nach Typ (Punkt 5)
            if is_group:
                icon = {"group": "📚", "project": "💼", "team": "👥"}.get(cat, "📁")
                text = f"{icon} {name}"
            else:
                text = f"👤 {name} ({cat})"

            if fav: text = "⭐ " + text
            if self.sort_by_recent and lc:
                text += f" | {format_date_german(lc)}"

            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, pid)

            if not matches_search:
                item.setForeground(QColor("#c0c0c0"))

            # Fällige Kontakte (nur Personen, nicht Gruppen)
            if (not is_group and matches_search and lc and interval
                    and ds is not None):
                lc_iso = normalize_date(lc)
                if lc_iso:
                    if ds > interval + 7:
                        item.setBackground(QColor("#ffe0b2"))
                        item.setToolTip(
                            f"Kontakt überfällig! Letzter: "
                            f"{format_date_german(lc_iso)} ({ds} Tage)"
                        )
                    elif ds > interval:
                        item.setBackground(QColor("#fff9c4"))
                        item.setToolTip(
                            f"Kontakt leicht überfällig "
                            f"(seit {format_date_german(lc_iso)})"
                        )

            # Geburtstage (nur Personen)
            if not is_group and matches_search and bday_raw:
                bday_str = str(bday_raw).strip()
                try:
                    parts = bday_str.split('.')
                    day, month = int(parts[0]), int(parts[1])
                    bday_this = datetime(today.year, month, day).date()
                    nxt = (bday_this if bday_this >= today
                           else datetime(today.year + 1, month, day).date())
                    days_until = (nxt - today).days
                    if days_until <= 7:
                        item.setBackground(QColor("#c8e6c9"))
                        item.setToolTip(
                            f"🎂 Geburtstag in {days_until} Tagen! ({bday_str})"
                        )
                except Exception:
                    pass

            self.person_list.addItem(item)
            self.persons_data.append(pid)

    def load_person(self):
        item = self.person_list.currentItem()
        if not item: return
        pid = item.data(Qt.UserRole)
        if pid is None:
            # Fallback: Name-Parsing
            name = item.text().replace("⭐","").split("👤")[-1].strip().split("(")[0].strip().split("|")[0].strip()
            self.cur.execute("SELECT id FROM persons WHERE name=?", (name,))
            r = self.cur.fetchone()
            if not r: return
            pid = r[0]
        self.current_person_id = pid
        self.cur.execute("SELECT name, category, last_contact FROM persons WHERE id=?", (pid,))
        r = self.cur.fetchone()
        if r:
            name, cat, lc = r
            txt = f"👤 {name} ({cat})"
            if lc: txt += f"\n📅 Letzter Kontakt: {format_date_german(lc)}"
            self.person_label.setText(txt)
        self.load_entries()

    def load_entries(self):
        self.entries_view.clear(); self.entries_data.clear()
        if not self.current_person_id: return

        hidden_filter = "e.hidden IN (0,1)" if self.show_hidden else "e.hidden = 0"
        self.cur.execute(f"""
            SELECT e.id, se.type, se.title, se.content, se.entry_date,
                   e.pinned, e.hidden, se.tags, se.ai_suggested_tags, se.participants,
                   se.is_draft
            FROM entries e JOIN shared_entries se ON e.shared_entry_id=se.id
            WHERE e.person_id=? AND {hidden_filter}
            ORDER BY e.pinned DESC, COALESCE(se.entry_date,'') DESC, se.created_at DESC
        """, (self.current_person_id,))

        for eid, etype, title, content, edate, pinned, hidden, tags, ai_tags, parts, is_draft \
                in self.cur.fetchall():
            icon = {"Notiz":"📝","Hinweis":"⚠️","Gedanke":"💡",
                    "Treffen":"👥","Telefonat":"📞","KI":"🤖"}.get(etype, "📄")
            pin_mark    = "📌 " if pinned else ""
            hidden_mark = " 👁️" if hidden else ""
            draft_mark  = " [DRAFT]" if is_draft else ""
            date_str    = format_date_german(edate)

            header = f"{pin_mark}{icon} {date_str} | {title or etype}{hidden_mark}{draft_mark}"
            if parts: header += f" | Mit: {parts}"

            tag_parts = []
            if tags:
                for t in tags.split(','):
                    t = t.strip()
                    if t: tag_parts.append(f"🏷️{t}")
            if tag_parts: header += " | " + " ".join(tag_parts)

            if etype in ["Treffen","Telefonat","KI"]:
                p = {"Ort":"","Themen":"","Ergebnis/Gefühl":""}
                for part in (content or "").split("|"):
                    if ":" in part:
                        k, v = part.split(":",1)
                        if k.strip() in p: p[k.strip()] = v.strip()
                lines = [header]
                if etype == "Treffen" and p["Ort"]: lines.append(f"📍 {p['Ort']}")
                if p["Themen"]:        lines.append(f"💬 {self._limit(p['Themen'])}")
                if p["Ergebnis/Gefühl"]: lines.append(f"💭 {self._limit(p['Ergebnis/Gefühl'])}")
                text = "\n".join(l for l in lines if l.strip())
            else:
                text = f"{header}\n{self._limit(content or '')}"

            item = QListWidgetItem(text)

            # Punkt 9: Zukunfts-Einträge in Kursiv
            edate_iso = normalize_date(edate) if edate else None
            is_future = False
            if edate_iso:
                try:
                    is_future = datetime.strptime(edate_iso, "%Y-%m-%d").date() > datetime.now().date()
                except ValueError:
                    pass

            if hidden:
                f = item.font(); f.setItalic(True); item.setFont(f); item.setForeground(Qt.gray)
            elif is_future:
                f = item.font(); f.setItalic(True); item.setFont(f)
                item.setForeground(QColor("#1b6ca8"))
                # Zusätzlicher Tooltip
                item.setToolTip(f"Zukünftiger Termin am {format_date_german(edate_iso)}")
            if is_draft:
                item.setBackground(QColor("#fff3cd"))
            self.entries_view.addItem(item); self.entries_data.append(eid)

    # ── Personen-CRUD ─────────────────────────────────────────────────────────
    def add_person(self):
        name, ok = QInputDialog.getText(self, "Neue Person", "Name:")
        if not ok or not name.strip(): return

        # Dublettenprüfung
        exact, similar = DuplicateChecker.find_duplicates(self.cur, name.strip())
        if exact or similar:
            dlg = DuplicateWarningDialog(self, name.strip(), exact, similar)
            dlg.exec()
            if dlg.get_choice() == 'cancel': return

        cat, ok2 = QInputDialog.getItem(self, "Kategorie", "Kategorie:",
                                        ["Freund","Familie","Bekannte","Arbeitskollege"], 0, False)
        if ok2:
            self.cur.execute("INSERT INTO persons (name,category,created_at) VALUES (?,?,?)",
                             (name.strip(), cat, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            self.con.commit(); self.load_persons()

    def edit_person(self):
        if not self.current_person_id: return
        self.cur.execute("SELECT name, is_group FROM persons WHERE id=?",
                         (self.current_person_id,))
        r = self.cur.fetchone()
        if not r: return
        orig_name, is_group = r

        dlg = QDialog(self)
        dlg.setWindowTitle("📁 Gruppe bearbeiten" if is_group else "👤 Person bearbeiten")
        dlg.resize(500, 600)
        scroll = QScrollArea(dlg); scroll.setWidgetResizable(True)
        sw = QWidget(); lay = QFormLayout(sw); scroll.setWidget(sw)
        outer = QVBoxLayout(dlg); outer.addWidget(scroll)
        fields: dict[str, QLineEdit] = {}

        name_edit = QLineEdit(orig_name); lay.addRow("Name", name_edit)

        cat_cb = QComboBox()
        if is_group:
            cat_cb.addItems(["group","project","team"])
        else:
            cat_cb.addItems(["Freund","Familie","Bekannte","Arbeitskollege",
                             "Mentor","Nachbar","Vereinsmitglied"])
        lay.addRow("Kategorie / Typ", cat_cb)

        if not is_group:
            for fld, label in [
                ("hometown",    "Heimatort"),
                ("residence",   "Wohnort"),
                ("occupation",  "Beruf"),
                ("friend_group","Freundeskreis"),
                ("relationship","Beziehungsstand"),
                ("topics",      "Themen"),
                ("birthday",    "Geburtstag (TT.MM.JJJJ)"),
                ("name_day",    "Namenstag"),
                ("notes",       "Notizen"),
            ]:
                le = QLineEdit(); lay.addRow(label, le); fields[fld] = le

            # Aliasse
            aliases_edit = QLineEdit()
            aliases_edit.setPlaceholderText("Anna, Annie, A. Schmidt …")
            lay.addRow("Aliasse (Kalender-Matching)", aliases_edit)

            fav_cb = QComboBox(); fav_cb.addItems(["Nein","Ja"])
            lay.addRow("Favorit ⭐", fav_cb)

        # ── Kontaktintervall (für Personen UND Gruppen) ─────────────────────
        interval_group = QGroupBox("📅 Kontakthäufigkeit")
        ig_lay = QFormLayout()

        int_spin = QSpinBox()
        int_spin.setRange(0, 9999)
        int_spin.setSuffix(" Tage")
        int_spin.setSpecialValueText("Nie überfällig ∞")
        ig_lay.addRow("Intervall:", int_spin)

        # Preset-Buttons inkl. "Nie"
        preset_row = QHBoxLayout()
        for label, days in [("1 Wo.",7),("2 Wo.",14),("1 Mo.",30),
                             ("3 Mo.",90),("6 Mo.",180),("1 Jahr",365),("Nie",0)]:
            btn = QPushButton(label)
            btn.setFlat(True)
            color = "#e74c3c" if label == "Nie" else "#3498db"
            btn.setStyleSheet(f"QPushButton{{color:{color};text-decoration:underline;border:none;}}"
                              f"QPushButton:hover{{color:#2980b9;}}")
            btn.clicked.connect(lambda _, d=days: int_spin.setValue(d))
            preset_row.addWidget(btn)
        ig_lay.addRow("Schnellwahl:", preset_row)

        # Nächster Kontakt Info – berechnet aus normalisierten Daten
        self.cur.execute("""SELECT last_contact,
            CAST(julianday('now')-julianday(last_contact) AS INTEGER) AS ds
            FROM persons WHERE id=?""", (self.current_person_id,))
        lc_row = self.cur.fetchone()
        lc_lbl = QLabel("Kein letzter Kontakt")

        # last_contact normalisiert anzeigen
        if lc_row and lc_row[0]:
            lc_iso = normalize_date(lc_row[0]) or lc_row[0]
            ig_lay.addRow("Letzter Kontakt:", QLabel(format_date_german(lc_iso)))
            ig_lay.addRow("", lc_lbl)

        def _update_next_label(v):
            if v == 0:
                lc_lbl.setText("∞ Kontakt wird nie als überfällig markiert")
                lc_lbl.setStyleSheet("color:#27ae60;")
            elif lc_row and lc_row[0] and lc_row[1] is not None:
                remaining = v - lc_row[1]
                if remaining > 0:
                    lc_lbl.setText(f"Nächster fällig in {remaining} Tagen")
                    lc_lbl.setStyleSheet("color:#3498db;")
                else:
                    lc_lbl.setText(f"Überfällig seit {abs(remaining)} Tagen!")
                    lc_lbl.setStyleSheet("color:#e74c3c;font-weight:bold;")
            else:
                lc_lbl.setText("")

        int_spin.valueChanged.connect(_update_next_label)

        interval_group.setLayout(ig_lay)
        lay.addRow(interval_group)

        # Werte laden
        self.cur.execute(
            f"SELECT category,{','.join(fields) if fields else 'category'},"
            "favorite,contact_interval_days,aliases FROM persons WHERE id=?",
            (self.current_person_id,))
        db = self.cur.fetchone()
        if db:
            cat_cb.setCurrentText(db[0] or "")
            if not is_group:
                for i, k in enumerate(fields): fields[k].setText(db[i+1] or "")
                fav_cb.setCurrentIndex(db[-3] or 0)
                aliases_edit.setText(db[-1] or "")
            int_spin.setValue(db[-2] or 30)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)

        def _save():
            new_name = name_edit.text().strip()
            if new_name != orig_name:
                if QMessageBox.warning(dlg, "Namensänderung",
                                       f"'{orig_name}' → '{new_name}' ändern?",
                                       QMessageBox.Yes | QMessageBox.No) == QMessageBox.No:
                    return
            updates = [("name", new_name), ("category", cat_cb.currentText())]
            if not is_group:
                updates += [(k, fields[k].text().strip()) for k in fields]
                updates += [("favorite",  fav_cb.currentIndex()),
                             ("aliases",   aliases_edit.text().strip())]
            updates.append(("contact_interval_days", int_spin.value()))
            sc   = ", ".join(f"{k}=?" for k, _ in updates)
            vals = [v for _, v in updates] + [self.current_person_id]
            self.cur.execute(f"UPDATE persons SET {sc} WHERE id=?", vals)
            self.con.commit(); dlg.accept(); self.load_person(); self.load_persons()

        bb.accepted.connect(_save); bb.rejected.connect(dlg.reject)
        outer.addWidget(bb); dlg.exec()

    # ── Eintrags-CRUD ─────────────────────────────────────────────────────────
    def new_entry(self):
        """Erstellt leeren Eintrag und öffnet den vollständigen Edit-Dialog."""
        if not self.current_person_id:
            QMessageBox.warning(self, "Fehler", "Bitte Person auswählen"); return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cur.execute("INSERT INTO shared_entries (type,title,content,created_at,updated_at) VALUES (?,?,?,?,?)",
                         ("Notiz","","",now,now))
        self.con.commit(); sid = self.cur.lastrowid
        self.cur.execute("INSERT INTO entries (person_id,shared_entry_id,hidden,pinned) VALUES (?,?,0,0)",
                         (self.current_person_id, sid))
        self.con.commit(); eid = self.cur.lastrowid
        EditEntryDialog(self, eid, self.cur, self.con).exec()
        self.load_entries(); self.load_persons(); self.load_person()

    def save_entry(self):
        """Speichert einen Eintrag direkt über das Schnelleingabe-Formular."""
        if not self.current_person_id:
            QMessageBox.warning(self, "Fehler", "Person auswählen"); return
        etype = self.entry_type.currentText()
        title = self.entry_title.text().strip()
        edate = self.entry_date.text().strip() or None

        if etype in ["Treffen","Telefonat","KI"]:
            if etype == "Treffen":
                content = (f"Ort: {self.treffen_ort.text()} | "
                           f"Themen: {self.treffen_themen.toPlainText()} | "
                           f"Ergebnis/Gefühl: {self.treffen_gespraech.toPlainText()}")
            else:
                content = (f"Themen: {self.treffen_themen.toPlainText()} | "
                           f"Ergebnis/Gefühl: {self.treffen_gespraech.toPlainText()}")
        else:
            content = self.entry_text.toPlainText().strip()

        if not content:
            QMessageBox.warning(self, "Fehler", "Text eingeben"); return

        is_draft = 1 if self.new_entry_draft_cb.isChecked() else 0

        # KI-Tags
        tags_str = None
        if Config().get('auto_tags', True):
            suggested = AITagSuggestion.suggest_tags(content, title)
            if suggested:
                picks, ok = QInputDialog.getItem(self, "Tag-Vorschlag",
                                                 f"Tags vorgeschlagen: {', '.join(suggested)}\nÜbernehmen?",
                                                 ["Alle übernehmen", "Keine übernehmen"] + suggested, 0, False)
                if ok:
                    if picks == "Alle übernehmen": tags_str = ','.join(suggested)
                    elif picks != "Keine übernehmen": tags_str = picks

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cur.execute("""INSERT INTO shared_entries
            (type,title,content,entry_date,created_at,updated_at,tags,is_draft)
            VALUES (?,?,?,?,?,?,?,?)""",
            (etype, title, content, edate, now, now, tags_str, is_draft))
        self.con.commit(); sid = self.cur.lastrowid

        participant_ids = [self.current_person_id]
        if etype in ["Treffen","Telefonat","KI"]:
            for pname in [p.strip() for p in self.treffen_persons.text().split(",") if p.strip()]:
                self.cur.execute("SELECT id FROM persons WHERE name=? AND is_group=0", (pname,))
                r = self.cur.fetchone()
                if r and r[0] not in participant_ids:
                    participant_ids.append(r[0])

        edate_iso = normalize_date(edate) if edate else None
        # Punkt 2: last_contact darf nie in der Zukunft liegen
        today_iso = datetime.now().strftime("%Y-%m-%d")
        edate_for_lc = edate_iso if (edate_iso and edate_iso <= today_iso) else None

        for pid in participant_ids:
            self.cur.execute("INSERT INTO entries (person_id,shared_entry_id,hidden,pinned) VALUES (?,?,0,0)",
                             (pid, sid))
            if edate_for_lc and etype in ("Treffen", "Telefonat"):
                # last_contact nur aktualisieren wenn neues Datum wirklich neuer ist
                self.cur.execute("""UPDATE persons SET last_contact=?
                    WHERE id=? AND (
                        last_contact IS NULL
                        OR last_contact = ''
                        OR (length(last_contact)=10 AND last_contact < ?)
                    )""", (edate_for_lc, pid, edate_for_lc))

        if len(participant_ids) > 1:
            parts_str = ", ".join(
                self.cur.execute("SELECT name FROM persons WHERE id=?", (pid,)).fetchone()[0]
                for pid in participant_ids)
            self.cur.execute("UPDATE shared_entries SET participants=? WHERE id=?", (parts_str, sid))

        self.con.commit()
        self.entry_text.clear(); self.entry_title.clear()
        self.treffen_themen.clear(); self.treffen_gespraech.clear()
        self.treffen_ort.clear(); self.treffen_persons.clear()
        self.load_entries(); self.load_persons()

    def edit_entry(self, item):
        idx = self.entries_view.row(item)
        if 0 <= idx < len(self.entries_data):
            EditEntryDialog(self, self.entries_data[idx], self.cur, self.con).exec()
            self.load_entries(); self.load_persons(); self.load_person()

    def edit_entry_by_id(self, entry_id):
        if entry_id in self.entries_data:
            idx = self.entries_data.index(entry_id)
            item = self.entries_view.item(idx)
            if item: self.edit_entry(item)

    # ── Navigation ────────────────────────────────────────────────────────────
    def select_person_by_id(self, person_id):
        for i in range(self.person_list.count()):
            item = self.person_list.item(i)
            if item.data(Qt.UserRole) == person_id:
                self.person_list.setCurrentItem(item); return
        # Fallback: Name-Suche
        self.cur.execute("SELECT name FROM persons WHERE id=?", (person_id,))
        r = self.cur.fetchone()
        if r:
            for i in range(self.person_list.count()):
                if r[0] in self.person_list.item(i).text():
                    self.person_list.setCurrentItem(self.person_list.item(i)); return

    def open_entry_by_id(self, entry_id):
        self.cur.execute("SELECT person_id FROM entries WHERE id=?", (entry_id,))
        r = self.cur.fetchone()
        if r:
            self.select_person_by_id(r[0])
            QTimer.singleShot(100, lambda: self.edit_entry_by_id(entry_id))

    # ── Kontext-Menü Einträge ─────────────────────────────────────────────────
    def _entry_context_menu(self, pos):
        menu = QMenu()
        sel = self.entries_view.selectedItems()
        if sel:
            ta = QAction("🗑️ In Papierkorb", self); ta.triggered.connect(self._trash_selected)
            menu.addAction(ta)
            if len(sel) == 1:
                ea = QAction("✏️ Bearbeiten", self)
                ea.triggered.connect(lambda: self.edit_entry(sel[0]))
                menu.addAction(ea)
        menu.exec(self.entries_view.mapToGlobal(pos))

    def _trash_selected(self):
        sel = self.entries_view.selectedItems()
        if not sel: return
        if QMessageBox.question(self, "Papierkorb",
                                f"{len(sel)} Eintrag/Einträge in Papierkorb?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            for item in sel:
                idx = self.entries_view.row(item)
                if idx < len(self.entries_data):
                    self.cur.execute("UPDATE entries SET hidden=2 WHERE id=?", (self.entries_data[idx],))
            self.con.commit(); self.load_entries()

    # ── Schalter ──────────────────────────────────────────────────────────────
    def toggle_sort(self):
        self.sort_by_recent = not self.sort_by_recent
        self.sort_btn.setText("📋 Alphabetisch" if self.sort_by_recent else "⏱️ Letzter Kontakt")
        self.load_persons()

    def toggle_hidden(self):
        self.show_hidden = self.show_hidden_btn.isChecked()
        if self.current_person_id: self.load_entries()

    def update_entry_fields(self, etype):
        is_meeting = etype in ["Treffen","Telefonat","KI"]
        self.entry_text.setVisible(not is_meeting)
        self.treffen_persons.setVisible(is_meeting)
        self.treffen_ort.setVisible(etype == "Treffen")
        self.treffen_themen.setVisible(is_meeting)
        self.treffen_gespraech.setVisible(is_meeting)

    # ── Dialog-Öffner ─────────────────────────────────────────────────────────
    def show_advanced_search(self):
        AdvancedSearchDialog(self).exec()

    def show_groups(self):
        GroupDialog(self, self.cur, self.con).exec(); self.load_persons()

    def show_calendar(self):
        CalendarDialog(self, self.cur, self.con).exec()

    def show_import_export(self):
        ImportExportDialog(self).exec()

    def show_backup(self):
        BackupDialog(self).exec()

    def show_trash(self):
        TrashDialog(self, self.cur, self.con).exec(); self.load_entries()

    def show_relationships(self):
        if not self.current_person_id:
            QMessageBox.warning(self, "Fehler", "Person auswählen"); return
        RelationshipDialog(self, self.current_person_id, self.cur, self.con).exec()

    def show_drafts(self):
        DraftOverviewDialog(self, self.cur, self.con).exec()
        self.load_entries()

    # ── Hilfsmethoden ─────────────────────────────────────────────────────────
    @staticmethod
    def _limit(text, limit=200):
        text = str(text).strip()
        return text[:limit].rstrip() + "..." if len(text) > limit else text

    # ── Export / Import ───────────────────────────────────────────────────────
    def export_csv(self, parent):
        path, _ = QFileDialog.getSaveFileName(parent, "CSV exportieren",
            f"personen_{datetime.now().strftime('%Y%m%d')}.csv", "CSV (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    w = csv.writer(f)
                    w.writerow(['Name','Kategorie','Heimatort','Wohnort','Beruf',
                                'Freundeskreis','Beziehung','Themen','Geburtstag',
                                'Namenstag','Notizen','Favorit','Ist_Gruppe'])
                    self.cur.execute("""SELECT name,category,hometown,residence,occupation,
                        friend_group,relationship,topics,birthday,name_day,notes,favorite,is_group
                        FROM persons ORDER BY name""")
                    for row in self.cur.fetchall(): w.writerow(row)
                QMessageBox.information(parent, "Erfolg", f"Exportiert:\n{path}")
            except Exception as e:
                QMessageBox.critical(parent, "Fehler", str(e))

    def export_csv_person(self, parent):
        if not self.current_person_id:
            QMessageBox.warning(parent, "Fehler", "Person auswählen"); return
        self.cur.execute("SELECT name FROM persons WHERE id=?", (self.current_person_id,))
        name = self.cur.fetchone()[0]
        path, _ = QFileDialog.getSaveFileName(parent, "CSV exportieren",
            f"{name}_{datetime.now().strftime('%Y%m%d')}.csv", "CSV (*.csv)")
        if path:
            try:
                with open(path, 'w', newline='', encoding='utf-8') as f:
                    w = csv.writer(f)
                    w.writerow(['Datum','Typ','Titel','Inhalt','Tags'])
                    self.cur.execute("""SELECT se.entry_date,se.type,se.title,se.content,se.tags
                        FROM entries e JOIN shared_entries se ON e.shared_entry_id=se.id
                        WHERE e.person_id=? AND e.hidden=0 ORDER BY se.entry_date DESC""",
                        (self.current_person_id,))
                    for row in self.cur.fetchall(): w.writerow(row)
                QMessageBox.information(parent, "Erfolg", f"Exportiert:\n{path}")
            except Exception as e:
                QMessageBox.critical(parent, "Fehler", str(e))

    def export_pdf(self, parent):
        if not self.current_person_id:
            QMessageBox.warning(parent, "Fehler", "Person auswählen"); return
        self.cur.execute("SELECT name FROM persons WHERE id=?", (self.current_person_id,))
        pname = self.cur.fetchone()[0]
        path, _ = QFileDialog.getSaveFileName(parent, "HTML exportieren",
            f"{pname}_{datetime.now().strftime('%Y%m%d')}.html", "HTML (*.html)")
        if path:
            try:
                self.cur.execute("""SELECT se.entry_date,se.type,se.title,se.content,se.tags
                    FROM entries e JOIN shared_entries se ON e.shared_entry_id=se.id
                    WHERE e.person_id=? AND e.hidden=0 ORDER BY se.entry_date DESC""",
                    (self.current_person_id,))
                html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
                <title>{pname}</title><style>
                body{{font-family:Arial;margin:20px;}}
                .entry{{margin:20px 0;padding:15px;background:#f8f9fa;border-left:4px solid #3498db;}}
                .date{{color:#7f8c8d;font-weight:bold;}}
                .tags{{color:#27ae60;font-size:12px;}}
                </style></head><body><h1>📇 {pname}</h1>
                <p>Exportiert: {datetime.now().strftime('%d.%m.%Y %H:%M')}</p>"""
                for ed, et, tit, ct, tags in self.cur.fetchall():
                    th = f'<div class="tags">🏷️ {tags}</div>' if tags else ''
                    html += f'<div class="entry"><div class="date">{format_date_german(ed)}</div>'
                    html += f'<span>{et}</span><h3>{tit or et}</h3>'
                    html += f'<p>{(ct or "").replace(chr(10),"<br>")}</p>{th}</div>'
                html += "</body></html>"
                with open(path, 'w', encoding='utf-8') as f: f.write(html)
                QMessageBox.information(parent, "Erfolg", f"Exportiert:\n{path}")
            except Exception as e:
                QMessageBox.critical(parent, "Fehler", str(e))

    def import_csv(self, parent):
        path, _ = QFileDialog.getOpenFileName(parent, "CSV importieren", "", "CSV (*.csv)")
        if path:
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f); imp = 0
                    for row in reader:
                        name = row.get('Name','').strip()
                        if not name: continue
                        self.cur.execute("""INSERT INTO persons
                            (name,category,hometown,residence,occupation,friend_group,
                             relationship,topics,birthday,name_day,notes,favorite,created_at,is_group)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (name, row.get('Kategorie','Bekannte'),
                             row.get('Heimatort',''), row.get('Wohnort',''),
                             row.get('Beruf',''), row.get('Freundeskreis',''),
                             row.get('Beziehung',''), row.get('Themen',''),
                             row.get('Geburtstag',''), row.get('Namenstag',''),
                             row.get('Notizen',''), int(row.get('Favorit',0)),
                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             1 if row.get('Ist_Gruppe','0')=='1' else 0))
                        imp += 1
                self.con.commit(); self.load_persons()
                QMessageBox.information(parent, "Erfolg", f"{imp} Personen importiert")
            except Exception as e:
                QMessageBox.critical(parent, "Fehler", str(e))

    def import_whatsapp(self, parent):
        path, _ = QFileDialog.getOpenFileName(parent, "WhatsApp Chat", "", "Textdateien (*.txt)")
        if path:
            entries = parse_whatsapp_chat(path)
            if entries:
                WhatsAppImportDialog(parent, entries, self.cur, self.con).exec()
                self.load_entries()
            else:
                QMessageBox.warning(parent, "Fehler", "Keine Nachrichten gefunden")


# ── Audio-Aufnahme ────────────────────────────────────────────────────────────
class AudioRecorder:
    """Einfacher Audio-Recorder (benötigt sounddevice + soundfile)."""

    def __init__(self):
        self.recording   = False
        self.frames      = []
        self.sample_rate = 16000
        self.stream      = None

    def start_recording(self):
        try:
            import sounddevice as sd
            self.recording = True
            self.frames    = []

            def _cb(indata, frames, time, status):
                if self.recording:
                    self.frames.append(indata.copy())

            self.stream = sd.InputStream(samplerate=self.sample_rate, channels=1, callback=_cb)
            self.stream.start()
            return True
        except ImportError:
            return False

    def stop_recording(self, output_path):
        if not self.recording:
            return False
        self.recording = False
        if self.stream:
            self.stream.stop(); self.stream.close(); self.stream = None
        try:
            import soundfile as sf
            import numpy as np
            data = np.concatenate(self.frames, axis=0)
            sf.write(output_path, data, self.sample_rate)
            return True
        except ImportError:
            return False

    @staticmethod
    def transcribe_audio(audio_path):
        try:
            import whisper
            model  = whisper.load_model("base")
            result = model.transcribe(audio_path, language="de")
            return result["text"]
        except ImportError:
            return None
        except Exception as e:
            print(f"Whisper error: {e}")
            return None


class AudioRecorderDialog(QDialog):
    """Dialog für Audio-Aufnahme mit optionaler Whisper-Transkription."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("🎤 Sprachaufnahme")
        self.resize(400, 320)
        self.recorder   = AudioRecorder()
        self.audio_file = None
        self.elapsed_seconds = 0

        lay = QVBoxLayout(self)

        self._status = QLabel("Bereit zur Aufnahme")
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setStyleSheet("font-size:16px;padding:16px;")
        lay.addWidget(self._status)

        self._timer_lbl = QLabel("00:00")
        self._timer_lbl.setAlignment(Qt.AlignCenter)
        self._timer_lbl.setStyleSheet("font-size:32px;font-weight:bold;color:#e74c3c;")
        lay.addWidget(self._timer_lbl)

        self._rec_btn = QPushButton("⏺️ Aufnahme starten")
        self._rec_btn.setStyleSheet("padding:12px;font-size:13px;")
        self._rec_btn.clicked.connect(self._toggle)
        lay.addWidget(self._rec_btn)

        self._trans_btn = QPushButton("📝 Transkribieren (Whisper)")
        self._trans_btn.setEnabled(False)
        self._trans_btn.clicked.connect(self._auto_transcribe)
        lay.addWidget(self._trans_btn)

        self._trans_edit = QTextEdit()
        self._trans_edit.setPlaceholderText("Transkription erscheint hier …")
        self._trans_edit.setVisible(False)
        lay.addWidget(self._trans_edit)

        bl = QHBoxLayout()
        self._save_btn = QPushButton("💾 Speichern"); self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self.accept)
        cancel = QPushButton("Abbrechen"); cancel.clicked.connect(self.reject)
        bl.addWidget(self._save_btn); bl.addWidget(cancel); lay.addLayout(bl)

        self._qtimer = QTimer()
        self._qtimer.timeout.connect(self._tick)

    def _toggle(self):
        if not self.recorder.recording:
            self._start()
        else:
            self._stop()

    def _start(self):
        if self.recorder.start_recording():
            self._rec_btn.setText("⏹️ Aufnahme stoppen")
            self._rec_btn.setStyleSheet("padding:12px;font-size:13px;"
                                        "background:#e74c3c;color:white;")
            self._status.setText("🔴 Aufnahme läuft …")
            self.elapsed_seconds = 0
            self._qtimer.start(1000)
        else:
            QMessageBox.warning(self, "Fehler",
                "Audio-Aufnahme nicht verfügbar.\n"
                "Bitte installieren: pip install sounddevice soundfile")

    def _stop(self):
        self._qtimer.stop()
        self._rec_btn.setText("⏺️ Aufnahme starten")
        self._rec_btn.setStyleSheet("padding:12px;font-size:13px;")
        self._status.setText("✅ Aufnahme beendet")
        fname = f"rec_{datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        self.audio_file = os.path.join(AUDIO_DIR, fname)
        if self.recorder.stop_recording(self.audio_file):
            self._trans_btn.setEnabled(True)
            self._save_btn.setEnabled(True)
        else:
            self._status.setText("⚠️ Speichern fehlgeschlagen (soundfile fehlt?)")

    def _tick(self):
        self.elapsed_seconds += 1
        m, s = divmod(self.elapsed_seconds, 60)
        self._timer_lbl.setText(f"{m:02d}:{s:02d}")

    def _auto_transcribe(self):
        if not self.audio_file or not os.path.exists(self.audio_file):
            return
        self._status.setText("⏳ Transkribiere …")
        QApplication.processEvents()
        text = AudioRecorder.transcribe_audio(self.audio_file)
        if text:
            self._trans_edit.setPlainText(text)
            self._trans_edit.setVisible(True)
            self._status.setText("✅ Transkription fertig")
        else:
            self._status.setText("⚠️ Whisper nicht verfügbar")

    def get_audio_file(self):
        return self.audio_file

    def get_transcription(self):
        return self._trans_edit.toPlainText().strip() if self._trans_edit.isVisible() else None


# (Audio-Aufnahme ist direkt in EditEntryDialog.__init__ integriert)


# ── Tag-Auswahl-Dialog ────────────────────────────────────────────────────────
class TagSelectionDialog(QDialog):
    """Zeigt KI-vorgeschlagene Tags zur Auswahl an."""

    def __init__(self, parent, suggested_tags, existing_tags=None):
        super().__init__(parent)
        self.setWindowTitle("🏷️ Tag-Vorschläge")
        self.resize(380, 300)
        self._checks: dict[str, QCheckBox] = {}

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>🤖 KI-Vorschläge:</b>"))
        lay.addWidget(QLabel("Wähle die Tags, die du übernehmen möchtest:"))

        for tag in suggested_tags:
            cb = QCheckBox(tag)
            cb.setChecked(True)
            self._checks[tag] = cb
            lay.addWidget(cb)

        lay.addSpacing(8)
        bl = QHBoxLayout()
        ok  = QPushButton("✅ Übernehmen"); ok.clicked.connect(self.accept)
        skip = QPushButton("❌ Überspringen"); skip.clicked.connect(self.reject)
        ok.setStyleSheet("background:#27ae60;color:white;font-weight:bold;")
        bl.addWidget(ok); bl.addWidget(skip); lay.addLayout(bl)

    def get_selected_tags(self):
        return [tag for tag, cb in self._checks.items() if cb.isChecked()]


# ── AI-Worker (Hintergrund-Analyse) ──────────────────────────────────────────
class AIWorker(QThread):
    """Analysiert Text im Hintergrund und sendet Tag/Typ-Vorschläge."""
    finished = Signal(dict)

    def __init__(self, text):
        super().__init__()
        self.text = text

    def run(self):
        self.finished.emit(self._analyze(self.text))

    @staticmethod
    def _analyze(text):
        tl = text.lower()
        suggestions = {'tags': AITagSuggestion.suggest_tags(text), 'type': 'Notiz', 'sentiment': 'neutral'}

        if any(w in tl for w in ['getroffen','treffen','café','restaurant','besuch']):
            suggestions['type'] = 'Treffen'
        elif any(w in tl for w in ['anruf','telefoniert','gespräch','tel.']):
            suggestions['type'] = 'Telefonat'
        elif any(w in tl for w in ['idee','gedanke','überlegung','frage mich']):
            suggestions['type'] = 'Gedanke'
        elif any(w in tl for w in ['wichtig','achtung','beachten','merken']):
            suggestions['type'] = 'Hinweis'

        pos = sum(1 for w in ['gut','toll','schön','super','freue','glücklich'] if w in tl)
        neg = sum(1 for w in ['schlecht','ärger','problem','sorge','traurig','schwierig'] if w in tl)
        if pos > neg: suggestions['sentiment'] = 'positiv'
        elif neg > pos: suggestions['sentiment'] = 'negativ'

        return suggestions


# ── Erinnerungs-Dialog ────────────────────────────────────────────────────────
class ReminderDialog(QDialog):
    """Dialog zum Anlegen und Bearbeiten von Erinnerungen."""

    def __init__(self, parent, reminder_id, cur, con):
        super().__init__(parent)
        self.setWindowTitle("📌 Erinnerung")
        self.resize(500, 460)
        self.reminder_id = reminder_id
        self.cur = cur; self.con = con

        lay = QVBoxLayout(self)

        self.cur.execute("""SELECT reminder_date, title, description, person_id, completed
            FROM reminders WHERE id=?""", (reminder_id,))
        r = self.cur.fetchone()
        if not r:
            QMessageBox.critical(self, "Fehler", "Erinnerung nicht gefunden")
            self.reject(); return
        rdate, title, desc, person_id, completed = r

        lay.addWidget(QLabel("Person (optional):"))
        self._person_cb = QComboBox()
        self._person_cb.addItem("Keine Person", None)
        self.cur.execute("SELECT id, name FROM persons ORDER BY name")
        for pid, pname in self.cur.fetchall():
            self._person_cb.addItem(pname, pid)
        if person_id:
            idx = self._person_cb.findData(person_id)
            if idx >= 0: self._person_cb.setCurrentIndex(idx)
        lay.addWidget(self._person_cb)

        lay.addWidget(QLabel("Datum (YYYY-MM-DD):"))
        self._date = QLineEdit(rdate or ""); lay.addWidget(self._date)

        lay.addWidget(QLabel("Titel:"))
        self._title = QLineEdit(title or ""); lay.addWidget(self._title)

        lay.addWidget(QLabel("Beschreibung:"))
        self._desc = QTextEdit(desc or ""); self._desc.setTabChangesFocus(True)
        self._desc.setMaximumHeight(100); lay.addWidget(self._desc)

        self._done_cb = QCheckBox("✅ Erledigt")
        self._done_cb.setChecked(completed == 1); lay.addWidget(self._done_cb)

        bl = QHBoxLayout()
        for lbl, fn in [("💾 Speichern", self._save), ("🗑️ Löschen", self._delete),
                         ("Abbrechen", self.reject)]:
            b = QPushButton(lbl); b.clicked.connect(fn); bl.addWidget(b)
        lay.addLayout(bl)

    def _save(self):
        self.cur.execute("""UPDATE reminders
            SET reminder_date=?, title=?, description=?, person_id=?, completed=?
            WHERE id=?""",
            (self._date.text().strip(), self._title.text().strip(),
             self._desc.toPlainText().strip(), self._person_cb.currentData(),
             1 if self._done_cb.isChecked() else 0, self.reminder_id))
        self.con.commit(); self.accept()

    def _delete(self):
        if QMessageBox.question(self, "Löschen", "Erinnerung löschen?",
                                QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.cur.execute("DELETE FROM reminders WHERE id=?", (self.reminder_id,))
            self.con.commit(); self.accept()


# ── Dashboard-Statistiken-Dialog ──────────────────────────────────────────────
class DashboardDialog(QDialog):
    """Detaillierter Statistik-Dialog (wird über Menü geöffnet)."""

    def __init__(self, parent, cur, con):
        super().__init__(parent)
        self.setWindowTitle("📊 Dashboard & Statistiken")
        self.resize(820, 620)
        self.cur = cur; self.con = con

        lay = QVBoxLayout(self)
        tabs = QTabWidget()

        # ── Tab 1: Übersicht ─────────────────────────────────────────────────
        t1 = QWidget(); l1 = QVBoxLayout(t1)
        stats = self._get_stats()
        html = f"""<h2>📈 Übersicht</h2>
        <table width='100%' cellspacing='6'>
        <tr><td><b>Personen gesamt:</b></td><td>{stats['persons']}</td></tr>
        <tr><td><b>Einträge gesamt:</b></td><td>{stats['entries']}</td></tr>
        <tr><td><b>Einträge diese Woche:</b></td><td>{stats['week']}</td></tr>
        <tr><td><b>Einträge diesen Monat:</b></td><td>{stats['month']}</td></tr>
        <tr><td><b>Ø Einträge / Person:</b></td><td>{stats['avg']:.1f}</td></tr>
        <tr><td><b>Draft-Einträge:</b></td><td>{stats['drafts']}</td></tr>
        </table>
        <h3>📝 Eintragstypen</h3><table width='100%'>"""
        for etype, cnt in stats['types'].items():
            html += f"<tr><td>{etype}:</td><td>{cnt}</td></tr>"
        html += "</table>"
        lbl = QLabel(html); lbl.setTextFormat(Qt.RichText); l1.addWidget(lbl)
        tabs.addTab(t1, "📈 Übersicht")

        # ── Tab 2: Fällige Kontakte ───────────────────────────────────────────
        t2 = QWidget(); l2 = QVBoxLayout(t2)
        l2.addWidget(QLabel("<h3>⏰ Fällige Kontakte</h3>"))
        overdue_list = QListWidget()
        self.cur.execute("""SELECT name, last_contact, contact_interval_days,
            CAST(julianday('now')-julianday(last_contact) AS INTEGER) AS ds
            FROM persons WHERE last_contact IS NOT NULL
            AND (julianday('now')-julianday(last_contact)) > contact_interval_days
            ORDER BY ds DESC""")
        for name, lc, intv, ds in self.cur.fetchall():
            item = QListWidgetItem(f"⚠️ {name} – letzter Kontakt: {format_date_german(lc)} ({ds-intv} Tage überfällig)")
            item.setBackground(QColor("#ffe0b2") if ds - intv > 14 else QColor("#fff9c4"))
            overdue_list.addItem(item)
        if not overdue_list.count(): overdue_list.addItem("✅ Alle Kontakte aktuell!")
        l2.addWidget(overdue_list); tabs.addTab(t2, "⏰ Fällige Kontakte")

        # ── Tab 3: Geburtstage ────────────────────────────────────────────────
        t3 = QWidget(); l3 = QVBoxLayout(t3)
        l3.addWidget(QLabel("<h3>🎂 Kommende Geburtstage (60 Tage)</h3>"))
        bday_list = QListWidget()
        today = datetime.now()
        self.cur.execute("SELECT id, name, birthday FROM persons WHERE birthday IS NOT NULL AND is_group=0")
        upcoming = []
        for pid, name, bday in self.cur.fetchall():
            try:
                parts = str(bday).strip().split('.')
                day, month = int(parts[0]), int(parts[1])
                nxt = datetime(today.year, month, day)
                if nxt < today: nxt = datetime(today.year+1, month, day)
                days_until = (nxt - today).days
                if days_until <= 60: upcoming.append((name, bday, days_until))
            except: pass
        upcoming.sort(key=lambda x: x[2])
        for name, bday, days in upcoming:
            if   days == 0: txt = f"🎉 {name} – HEUTE! ({bday})"
            elif days == 1: txt = f"🎂 {name} – MORGEN ({bday})"
            else:           txt = f"🎂 {name} – in {days} Tagen ({bday})"
            item = QListWidgetItem(txt)
            if days <= 3: item.setBackground(QColor("#c8e6c9"))
            bday_list.addItem(item)
        if not bday_list.count(): bday_list.addItem("Keine Geburtstage in den nächsten 60 Tagen")
        l3.addWidget(bday_list); tabs.addTab(t3, "🎂 Geburtstage")

        # ── Tab 4: Tags ───────────────────────────────────────────────────────
        t4 = QWidget(); l4 = QVBoxLayout(t4)
        l4.addWidget(QLabel("<h3>🏷️ Häufigste Tags</h3>"))
        tag_list = QListWidget()
        self.cur.execute("SELECT tags FROM shared_entries WHERE tags IS NOT NULL AND tags!=''")
        counts: dict[str, int] = defaultdict(int)
        for (ts,) in self.cur.fetchall():
            for t in ts.split(','):
                t = t.strip()
                if t: counts[t] += 1
        for tag, cnt in sorted(counts.items(), key=lambda x: -x[1])[:30]:
            tag_list.addItem(f"🏷️ {tag}: {cnt}×")
        l4.addWidget(tag_list); tabs.addTab(t4, "🏷️ Tags")

        lay.addWidget(tabs)
        close = QPushButton("Schließen"); close.clicked.connect(self.accept); lay.addWidget(close)

    def _get_stats(self):
        stats = {}
        self.cur.execute("SELECT COUNT(*) FROM persons WHERE is_group=0")
        stats['persons'] = self.cur.fetchone()[0]
        self.cur.execute("SELECT COUNT(DISTINCT shared_entry_id) FROM entries")
        stats['entries'] = self.cur.fetchone()[0]
        week  = (datetime.now()-timedelta(7)).strftime("%Y-%m-%d")
        month = (datetime.now()-timedelta(30)).strftime("%Y-%m-%d")
        self.cur.execute("SELECT COUNT(*) FROM shared_entries WHERE entry_date>=?", (week,))
        stats['week'] = self.cur.fetchone()[0]
        self.cur.execute("SELECT COUNT(*) FROM shared_entries WHERE entry_date>=?", (month,))
        stats['month'] = self.cur.fetchone()[0]
        self.cur.execute("SELECT COUNT(*) FROM shared_entries WHERE is_draft=1")
        stats['drafts'] = self.cur.fetchone()[0]
        stats['avg'] = stats['entries'] / max(stats['persons'], 1)
        self.cur.execute("SELECT type, COUNT(*) FROM shared_entries GROUP BY type ORDER BY 2 DESC")
        stats['types'] = {r[0]: r[1] for r in self.cur.fetchall()}
        return stats


# ── Dashboard-Schaltfläche in MainWindow ergänzen ────────────────────────────
_orig_mw_init = MainWindow.__init__

def _mw_init_extended(self):
    _orig_mw_init(self)
    # Statistik-Button in Toolbar
    stats_btn = QPushButton("📊 Statistiken")
    stats_btn.clicked.connect(lambda: DashboardDialog(self, self.cur, self.con).exec())
    stats_btn.setToolTip("Detaillierte Statistiken und Übersichten")

    self.statusBar().addPermanentWidget(stats_btn)
    self._refresh_statusbar()

    # Refresh-Timer: Dashboard alle 60s aktualisieren
    timer = QTimer(self)
    timer.timeout.connect(self._refresh_statusbar)
    timer.start(60_000)

    # Punkt 7: Pending-Attendance-Reminder beim Start
    QTimer.singleShot(500, self._check_pending_attendance)


def _check_pending_attendance(self):
    """Prüft, ob es vergangene Gruppen-Treffen mit ausstehender Anwesenheit
    gibt. Falls ja, öffnet PendingAttendanceReminderDialog."""
    try:
        from calendar_sync import list_pending_attendance
        pending = list_pending_attendance(self.con)
        if pending:
            dlg = PendingAttendanceReminderDialog(self, self.cur, self.con, pending)
            dlg.exec()
            self.load_persons()
            self._refresh_statusbar()
    except Exception as e:
        # Beim Start nicht crashen, nur loggen
        print(f"⚠️ pending-attendance Check fehlgeschlagen: {e}")


MainWindow._check_pending_attendance = _check_pending_attendance

def _refresh_statusbar(self):
    self.cur.execute("SELECT COUNT(*) FROM persons WHERE is_group=0")
    p = self.cur.fetchone()[0]
    self.cur.execute("SELECT COUNT(DISTINCT shared_entry_id) FROM entries")
    e = self.cur.fetchone()[0]
    self.cur.execute("""SELECT COUNT(*) FROM persons
        WHERE last_contact IS NOT NULL AND last_contact != ''
          AND length(last_contact) = 10
          AND contact_interval_days > 0
          AND (julianday('now')-julianday(last_contact)) > contact_interval_days""")
    ov = self.cur.fetchone()[0]
    self.cur.execute("SELECT COUNT(*) FROM shared_entries WHERE is_draft=1")
    dr = self.cur.fetchone()[0]

    parts = [f"👤 {p} Personen", f"📝 {e} Einträge"]
    if ov:  parts.append(f"⚠️ {ov} Kontakt(e) fällig")
    if dr:  parts.append(f"📋 {dr} Draft(s)")
    self.statusBar().showMessage("   |   ".join(parts))

MainWindow.__init__           = _mw_init_extended
MainWindow._refresh_statusbar = _refresh_statusbar


# ── Keyboard-Shortcut für Statistiken ────────────────────────────────────────
_orig_shortcuts = MainWindow._setup_shortcuts

def _extended_shortcuts(self):
    _orig_shortcuts(self)
    QShortcut(QKeySequence("Ctrl+S"), self).activated.connect(self.save_entry)
    QShortcut(QKeySequence("Ctrl+B"), self).activated.connect(self.show_backup)
    QShortcut(QKeySequence("Ctrl+T"), self).activated.connect(self.show_trash)
    QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.show_relationships)

MainWindow._setup_shortcuts = _extended_shortcuts


# ── Person löschen (fehlendes Feature) ───────────────────────────────────────
def _delete_person(self):
    """Löscht die aktuell gewählte Person nach Bestätigung."""
    if not self.current_person_id:
        QMessageBox.warning(self, "Fehler", "Bitte Person auswählen")
        return

    self.cur.execute("SELECT name FROM persons WHERE id=?", (self.current_person_id,))
    name = self.cur.fetchone()[0]

    self.cur.execute("SELECT COUNT(*) FROM entries WHERE person_id=?", (self.current_person_id,))
    entry_count = self.cur.fetchone()[0]

    msg = f"Person '{name}' wirklich löschen?"
    if entry_count > 0:
        msg += f"\n\n⚠️ {entry_count} verknüpfte Einträge werden ebenfalls gelöscht!"

    if QMessageBox.question(self, "Person löschen", msg,
                             QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
        # Shared entries aufräumen, die nur dieser Person gehören
        self.cur.execute("""SELECT shared_entry_id FROM entries WHERE person_id=?""",
                         (self.current_person_id,))
        shared_ids = [r[0] for r in self.cur.fetchall()]

        self.cur.execute("DELETE FROM entries WHERE person_id=?", (self.current_person_id,))

        # Shared entries löschen, die jetzt verwaist sind
        for sid in shared_ids:
            self.cur.execute("SELECT COUNT(*) FROM entries WHERE shared_entry_id=?", (sid,))
            if self.cur.fetchone()[0] == 0:
                for tbl in ['entry_files', 'entry_audio', 'entry_versions']:
                    self.cur.execute(f"DELETE FROM {tbl} WHERE shared_entry_id=?", (sid,))
                self.cur.execute("DELETE FROM shared_entries WHERE id=?", (sid,))

        self.cur.execute("DELETE FROM relationships WHERE person_a_id=? OR person_b_id=?",
                         (self.current_person_id, self.current_person_id))
        self.cur.execute("DELETE FROM group_members WHERE person_id=?", (self.current_person_id,))
        self.cur.execute("DELETE FROM reminders WHERE person_id=?", (self.current_person_id,))
        self.cur.execute("DELETE FROM persons WHERE id=?", (self.current_person_id,))
        self.con.commit()

        self.current_person_id = None
        self.person_label.setText("Keine Person gewählt")
        self.entries_view.clear()
        self.entries_data.clear()
        self.load_persons()
        self._refresh_statusbar()

MainWindow.delete_person = _delete_person


# ── Person-Lösch-Button in die UI integrieren ─────────────────────────────────
# (Buttons direkt in _setup_kartei_ui integriert)


# ── Kontakt-Intervall-Anzeige beim Laden einer Person ────────────────────────
# Punkt 3: Vollständig idempotent — der gesamte person_label-Text wird hier
# aus den DB-Daten neu zusammengebaut, nicht angehängt. Mehrfacher Aufruf
# führt nicht mehr zur Doppelanzeige.
def _load_person_extended(self):
    # Wir rufen das Original NICHT auf, weil es seinerseits den Text setzt
    # und wir alles in einem Rutsch sauber neu aufbauen wollen.
    item = self.person_list.currentItem()
    if not item:
        return
    pid = item.data(Qt.UserRole)
    if pid is None:
        # Fallback: Name aus Listentext extrahieren
        raw = item.text()
        # Icons abschneiden
        for prefix in ("⭐ ", "👤 ", "📁 ", "📚 ", "💼 ", "👥 "):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
        name = raw.split("(")[0].strip().split("|")[0].strip()
        self.cur.execute("SELECT id FROM persons WHERE name=?", (name,))
        r = self.cur.fetchone()
        if not r:
            return
        pid = r[0]
    self.current_person_id = pid

    # Alle relevanten Daten in einem Query holen
    self.cur.execute("""
        SELECT name, category, last_contact, contact_interval_days,
               CAST(julianday('now')-julianday(last_contact) AS INTEGER) AS ds,
               birthday, COALESCE(is_group,0) AS is_group
        FROM persons WHERE id=?
    """, (pid,))
    row = self.cur.fetchone()
    if not row:
        return
    name, cat, lc_raw, interval, ds, bday, is_group = row

    # Header je nach Typ
    if is_group:
        icon = {"group": "📚", "project": "💼", "team": "👥"}.get(cat or "", "📁")
        lines = [f"{icon} {name}  (Gruppe)"]
    else:
        lines = [f"👤 {name} ({cat})"]
        if lc_raw:
            lines.append(f"📅 Letzter Kontakt: {format_date_german(lc_raw)}")

    # Extras NUR für Personen
    extras = []
    lc_valid = lc_raw and len(str(lc_raw)) == 10 and '-' in str(lc_raw)
    if not is_group and interval and interval > 0 and ds is not None and lc_valid:
        if ds > interval:
            extras.append(f"⚠️ Kontakt {ds - interval} Tage überfällig!")
        else:
            extras.append(f"📅 Nächster Kontakt in {interval - ds} Tagen fällig")

    if not is_group and bday:
        try:
            today = datetime.now()
            parts = str(bday).strip().split('.')
            day, month = int(parts[0]), int(parts[1])
            nxt = datetime(today.year, month, day)
            if nxt < today:
                nxt = datetime(today.year + 1, month, day)
            days_until = (nxt - today).days
            if days_until == 0:
                extras.append("🎉 HEUTE Geburtstag!")
            elif days_until <= 7:
                extras.append(f"🎂 Geburtstag in {days_until} Tagen!")
        except Exception:
            pass

    if extras:
        lines.append("  ".join(extras))

    self.person_label.setText("\n".join(lines))
    self.load_entries()

MainWindow.load_person = _load_person_extended


# ===========================================================================
# EINSTIEGSPUNKT
# ===========================================================================

# ===========================================================================
# GOOGLE KALENDER SYNCHRONISATION
# ===========================================================================
# Lazy Import: calendar_sync, name_parser, person_matcher werden nur geladen
# wenn der Nutzer den Sync-Button klickt → kein Absturz wenn Pakete fehlen.

# ── Personen-Auflösungs-Dialog ────────────────────────────────────────────────
class PersonResolveDialog(QDialog):
    """
    Wird geöffnet wenn ein Name aus dem Kalender nicht eindeutig zugeordnet
    werden kann.

    Rückgabe via get_choice():
      int   →  person_id (positiv) oder -group_id (negativ, ≠ -1/-2)
      -1    →  neue Person anlegen
      -2    →  Name auf Blacklist setzen
      None  →  ignorieren
    """

    SENTINEL_NEW_PERSON = -1
    SENTINEL_BLACKLIST  = -2

    def __init__(self, parent, match_result, cur, event_title: str = ""):
        super().__init__(parent)
        self.cur          = cur
        self.result       = match_result
        self.event_title  = event_title or ""
        self._choice      = None
        self._all_items: list[tuple[int, str]] = []   # (UserRole-data, lower-text) für Filter
        self.setWindowTitle("🔍 Person zuordnen")
        self.resize(560, 520)

        lay = QVBoxLayout(self)

        # ── Kopfzeile mit vollständigem Eventtitel ──────────────────────────
        name = match_result.query
        if match_result.status == 'not_found':
            header_txt = f'<b>„{name}"</b> wurde nicht gefunden.'
        else:
            header_txt = (
                f'<b>„{name}"</b> ist mehreren Einträgen zugeordnet – '
                'bitte wähle die richtige Person oder Gruppe aus.'
            )
        lbl = QLabel(header_txt)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size:14px;padding:6px 4px 0 4px;")
        lay.addWidget(lbl)

        if self.event_title:
            evt = QLabel(f"<i>Aus Kalendereintrag:</i>  {self.event_title}")
            evt.setWordWrap(True)
            evt.setStyleSheet("color:#34495e;padding:0 4px 8px 4px;")
            lay.addWidget(evt)

        # ── Fuzzy-Hinweise (bei not_found) ──────────────────────────────────
        if match_result.fuzzy_hints:
            lay.addWidget(QLabel("<i>Meintest du vielleicht …?</i>"))
            for p, dist in match_result.fuzzy_hints:
                hint = QLabel(
                    f"   🔸 {p.name} ({p.category})  –  Ähnlichkeit: {3-dist}/3"
                )
                hint.setStyleSheet("color:#7f8c8d;")
                lay.addWidget(hint)
            lay.addSpacing(6)

        # ── Suchfeld ────────────────────────────────────────────────────────
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(
            "🔍 Suche nach Person oder Gruppe …"
        )
        self._search_edit.textChanged.connect(self._apply_filter)
        lay.addWidget(self._search_edit)

        # ── Liste: Personen UND Gruppen ─────────────────────────────────────
        self._person_list = QListWidget()
        lay.addWidget(QLabel("<b>Treffer / alle Einträge:</b>"))
        lay.addWidget(self._person_list)

        # Bei ambiguous: nur Kandidaten zeigen
        # Bei not_found: alle Personen + alle Gruppen zeigen
        if match_result.status == 'ambiguous':
            for p in match_result.candidates:
                kind = getattr(p, 'kind', 'person')
                if kind == 'group':
                    icon = {"group":"📚","project":"💼","team":"👥"}.get(p.category, "📁")
                    role = -p.id
                else:
                    icon = "👤"
                    role = p.id
                aliases_str = ""
                if getattr(p, 'aliases', None):
                    aliases_str = "  [" + ", ".join(p.aliases) + "]"
                self._add_list_item(
                    f"{icon}  {p.name}  ({p.category}){aliases_str}",
                    role,
                    extra_searchable=" ".join(p.aliases) if getattr(p,'aliases',None) else ""
                )
        else:
            cur.execute("PRAGMA table_info(persons)")
            cols = {r[1] for r in cur.fetchall()}
            has_aliases = 'aliases' in cols
            has_is_group = 'is_group' in cols
            has_deleted = 'deleted' in cols

            # ── Personen ────────────────────────────────────────────────────
            wp = []
            if has_is_group: wp.append("COALESCE(is_group,0)=0")
            if has_deleted:  wp.append("COALESCE(deleted,0)=0")
            wp_sql = " WHERE " + " AND ".join(wp) if wp else ""
            sel = "id, name, category"
            if has_aliases: sel += ", COALESCE(aliases,'')"
            cur.execute(f"SELECT {sel} FROM persons{wp_sql} ORDER BY name")
            for row in cur.fetchall():
                if has_aliases:
                    pid, pname, ccat, aliases_raw = row
                else:
                    pid, pname, ccat = row; aliases_raw = ""
                aliases_str = ""
                searchable_extra = ""
                if aliases_raw:
                    aliases_str = "  [" + aliases_raw + "]"
                    searchable_extra = aliases_raw
                self._add_list_item(
                    f"👤  {pname}  ({ccat or ''}){aliases_str}",
                    pid,
                    extra_searchable=searchable_extra
                )

            # ── Gruppen (aus persons WHERE is_group=1) ──────────────────────
            if has_is_group:
                wg = ["COALESCE(is_group,0)=1"]
                if has_deleted: wg.append("COALESCE(deleted,0)=0")
                wg_sql = " WHERE " + " AND ".join(wg)
                sel_g = "id, name, COALESCE(category,'group')"
                if has_aliases: sel_g += ", COALESCE(aliases,'')"
                cur.execute(f"SELECT {sel_g} FROM persons{wg_sql} ORDER BY name")
                for row in cur.fetchall():
                    if has_aliases:
                        gid, gname, gtype, aliases_raw = row
                    else:
                        gid, gname, gtype = row; aliases_raw = ""
                    icon = {"group":"📚","project":"💼","team":"👥"}.get(gtype, "📁")
                    aliases_str = ""
                    searchable_extra = ""
                    if aliases_raw:
                        aliases_str = "  [" + aliases_raw + "]"
                        searchable_extra = aliases_raw
                    self._add_list_item(
                        f"{icon}  {gname}  ({gtype}){aliases_str}",
                        -gid,
                        extra_searchable=searchable_extra
                    )

        # ── Buttons ─────────────────────────────────────────────────────────
        bl1 = QHBoxLayout()

        assign_btn = QPushButton("✅ Zuordnen")
        assign_btn.setStyleSheet(
            "background:#27ae60;color:white;font-weight:bold;padding:6px 12px;"
        )
        assign_btn.clicked.connect(self._assign)

        new_btn = QPushButton("➕ Neue Person anlegen")
        new_btn.clicked.connect(self._new_person)

        skip_btn = QPushButton("⏭️ Ignorieren")
        skip_btn.clicked.connect(self._skip)

        for b in [assign_btn, new_btn, skip_btn]:
            bl1.addWidget(b)
        lay.addLayout(bl1)

        bl2 = QHBoxLayout()
        bl_btn = QPushButton(f"🚫 „{name}“ immer ignorieren (Blacklist)")
        bl_btn.setStyleSheet("color:#c0392b;")
        bl_btn.setToolTip(
            "Diesen Namen auf die Blacklist setzen. Künftige "
            "Kalendereinträge mit diesem Namen werden ignoriert."
        )
        bl_btn.clicked.connect(self._blacklist)
        bl2.addWidget(bl_btn)
        lay.addLayout(bl2)

    def _add_list_item(self, text: str, role_data: int,
                       extra_searchable: str = "") -> None:
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, role_data)
        self._person_list.addItem(item)
        # Auch Aliase im Filter-Text durchsuchbar machen
        searchable = (text + " " + extra_searchable).lower()
        self._all_items.append((role_data, searchable))

    def _apply_filter(self, text: str) -> None:
        s = text.strip().lower()
        for i in range(self._person_list.count()):
            item = self._person_list.item(i)
            visible = (not s) or (s in self._all_items[i][1])
            item.setHidden(not visible)

    def _assign(self):
        item = self._person_list.currentItem()
        if not item or item.isHidden():
            QMessageBox.warning(self, "Fehler", "Bitte Person oder Gruppe auswählen")
            return
        self._choice = item.data(Qt.UserRole)
        self.accept()

    def _new_person(self):
        self._choice = self.SENTINEL_NEW_PERSON
        self.accept()

    def _skip(self):
        self._choice = None
        self.accept()

    def _blacklist(self):
        if QMessageBox.question(
            self, "Auf Blacklist?",
            f"„{self.result.query}“ wird auf die Blacklist gesetzt und in "
            "Zukunft beim Kalender-Sync immer ignoriert.\n\nFortfahren?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self._choice = self.SENTINEL_BLACKLIST
            self.accept()

    def get_choice(self):
        return self._choice


# ── Alias-Editor ──────────────────────────────────────────────────────────────
class AliasEditorDialog(QDialog):
    """Einfacher Dialog zum Bearbeiten der Aliasse einer Person."""

    def __init__(self, parent, person_id, cur, con):
        super().__init__(parent)
        self.person_id = person_id
        self.cur = cur; self.con = con
        self.setWindowTitle("🏷️ Aliasse bearbeiten")
        self.resize(400, 300)

        self.cur.execute("SELECT name, aliases FROM persons WHERE id=?", (person_id,))
        r = self.cur.fetchone()
        name, aliases_raw = r if r else ("", "")

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<b>Person: {name}</b>"))
        lay.addWidget(QLabel(
            "Aliasse werden beim Google-Kalender-Abgleich verwendet.\n"
            "Trage alle bekannten Kurznamen oder Varianten ein, z. B.:\n"
            "  Anna, Annie, A. Schmidt"
        ))

        self._edit = QTextEdit()
        self._edit.setPlaceholderText("Alias1, Alias2, Spitzname …")
        self._edit.setPlainText(aliases_raw or "")
        self._edit.setMaximumHeight(100)
        lay.addWidget(self._edit)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _save(self):
        raw = self._edit.toPlainText().strip()
        self.cur.execute("UPDATE persons SET aliases=? WHERE id=?",
                         (raw, self.person_id))
        self.con.commit()
        self.accept()


# ── Merge-Vorschlag bei bestehendem manuellen Eintrag (Punkt 4) ──────────────
class MergeProposalDialog(QDialog):
    """Wird aufgerufen, wenn beim Kalender-Sync ein bestehender manueller
    Eintrag mit identischem Datum gefunden wurde. Der Nutzer entscheidet, ob
    fehlende Felder (Ort/Themen) ergänzt werden — bestehende Werte werden
    NIE überschrieben.
    """

    def __init__(self, parent, proposal):
        super().__init__(parent)
        self.proposal = proposal
        self._merge   = False

        self.setWindowTitle("🔄 Bestehender Eintrag gefunden")
        self.resize(560, 480)

        lay = QVBoxLayout(self)

        lay.addWidget(QLabel(
            f"<b>Es gibt schon einen Karteieintrag für "
            f"{proposal.person_name} am "
            f"{format_date_german(proposal.existing_date)}.</b>"
        ))
        lay.addWidget(QLabel(
            "Statt ein Duplikat anzulegen kann ich fehlende Felder "
            "(Ort, Themen) aus dem Kalendereintrag ergänzen. "
            "<i>Bestehende Werte werden nicht überschrieben.</i>"
        ))

        ex_box = QGroupBox("Bestehender Karteieintrag")
        exl = QVBoxLayout()
        exl.addWidget(QLabel(f"<b>Titel:</b> {proposal.existing_title or '(ohne)'}"))

        ex_parts = {"Ort": "", "Themen": "", "Ergebnis/Gefühl": ""}
        for piece in (proposal.existing_content or '').split('|'):
            if ':' in piece:
                k, v = piece.split(':', 1)
                k = k.strip()
                if k in ex_parts:
                    ex_parts[k] = v.strip()

        for label_de, val in ex_parts.items():
            shown = val if val else "<i style='color:#c0392b'>(leer)</i>"
            exl.addWidget(QLabel(f"<b>{label_de}:</b> {shown}"))
        ex_box.setLayout(exl)
        lay.addWidget(ex_box)

        new_box = QGroupBox("Aus dem Kalendereintrag")
        nl = QVBoxLayout()
        nl.addWidget(QLabel(f"<b>Eventtitel:</b> {proposal.new_event_title}"))
        nl.addWidget(QLabel(f"<b>Themen (neu):</b> {proposal.new_treffen_titel}"))
        nl.addWidget(QLabel(f"<b>Ort (neu):</b> {proposal.new_ort or '—'}"))
        new_box.setLayout(nl)
        lay.addWidget(new_box)

        lay.addWidget(QLabel(
            "<i>Wenn du auf <b>Ergänzen</b> klickst, werden nur die "
            "leeren Felder aus dem bestehenden Eintrag mit den "
            "Kalender-Werten gefüllt. Es entsteht KEIN neuer Eintrag.</i>"
        ))

        bl = QHBoxLayout()
        merge_btn = QPushButton("🔄 Ergänzen (kein Duplikat)")
        merge_btn.setStyleSheet(
            "background:#27ae60;color:white;font-weight:bold;padding:6px 12px;"
        )
        merge_btn.clicked.connect(self._do_merge)
        skip_btn = QPushButton("➕ Trotzdem als neuen Eintrag")
        skip_btn.clicked.connect(self._skip_merge)
        bl.addWidget(merge_btn); bl.addWidget(skip_btn)
        lay.addLayout(bl)

    def _do_merge(self):
        self._merge = True
        self.accept()

    def _skip_merge(self):
        self._merge = False
        self.accept()

    def get_choice(self) -> bool:
        return self._merge


# ── Anwesenheits-Dialog für Gruppen-Treffen (Punkt 7) ────────────────────────
class GroupAttendanceDialog(QDialog):
    """Häkchen-Dialog: 'Wer von der Gruppe war beim Treffen dabei?'

    Mode 'past'    → Treffen liegen heute oder in der Vergangenheit
    Mode 'pending' → spätere Anwesenheits-Erinnerung
    """

    def __init__(self, parent, request, mode: str = 'past'):
        super().__init__(parent)
        self.request = request
        self._cbs: dict[int, QCheckBox] = {}

        self.setWindowTitle("👥 Wer war beim Treffen dabei?")
        self.resize(500, 540)

        lay = QVBoxLayout(self)

        if mode == 'pending':
            head = (
                f"<h3>📅 Anwesenheit nachtragen</h3>"
                f"<p>Das Gruppentreffen <b>{request.event_title}</b> "
                f"vom <b>{format_date_german(request.event_date)}</b> "
                f"hat noch keine Teilnahme-Information. "
                f"<br>Wer war von <b>{request.group_name}</b> dabei?</p>"
            )
        else:
            head = (
                f"<h3>👥 Gruppentreffen erkannt</h3>"
                f"<p>Eintrag: <b>{request.event_title}</b><br>"
                f"Datum: <b>{format_date_german(request.event_date)}</b><br>"
                f"Gruppe: <b>{request.group_name}</b></p>"
                f"<p>Bitte hake an, wer tatsächlich dabei war. "
                f"Nur die Angehakten erhalten einen Karteieintrag.</p>"
            )
        head_lbl = QLabel(head)
        head_lbl.setWordWrap(True)
        lay.addWidget(head_lbl)

        tb = QHBoxLayout()
        all_btn = QPushButton("✅ Alle"); all_btn.clicked.connect(self._all)
        none_btn = QPushButton("❌ Keine"); none_btn.clicked.connect(self._none)
        tb.addWidget(all_btn); tb.addWidget(none_btn); tb.addStretch()
        lay.addLayout(tb)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        sw = QWidget(); sl = QVBoxLayout(sw)
        for pid, pname in zip(request.member_ids, request.member_names):
            cb = QCheckBox(f"👤 {pname}")
            cb.setChecked(True)
            sl.addWidget(cb)
            self._cbs[pid] = cb
        sl.addStretch()
        scroll.setWidget(sw)
        lay.addWidget(scroll)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("✅ Übernehmen")
        bb.button(QDialogButtonBox.Cancel).setText("⏭️ Ignorieren")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _all(self):
        for cb in self._cbs.values(): cb.setChecked(True)

    def _none(self):
        for cb in self._cbs.values(): cb.setChecked(False)

    def get_attended(self) -> list[int]:
        return [pid for pid, cb in self._cbs.items() if cb.isChecked()]


# ── Reminder für ausstehende Anwesenheits-Eintragungen (Punkt 7) ─────────────
class PendingAttendanceReminderDialog(QDialog):
    """Beim App-Start: Liste aller Gruppen-Treffen in der Vergangenheit,
    deren Anwesenheit noch nicht eingetragen wurde."""

    def __init__(self, parent, cur, con, pending_list):
        super().__init__(parent)
        self.cur = cur; self.con = con
        self.pending = pending_list

        self.setWindowTitle("⏳ Anwesenheit nachtragen")
        self.resize(560, 420)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            f"<h3>⏳ {len(pending_list)} Gruppen-Treffen warten "
            f"auf Anwesenheit</h3>"
        ))
        lay.addWidget(QLabel(
            "Diese Gruppentreffen sind importiert, aber es ist noch nicht "
            "festgehalten, wer tatsächlich dabei war."
        ))

        self._list = QListWidget()
        for p in pending_list:
            txt = (f"📁 {p['group_name']}  |  "
                   f"{format_date_german(p['entry_date'])}  |  "
                   f"{p['title']}")
            item = QListWidgetItem(txt)
            item.setData(Qt.UserRole, p['shared_entry_id'])
            self._list.addItem(item)
        self._list.itemDoubleClicked.connect(self._open_selected)
        lay.addWidget(self._list)

        bl = QHBoxLayout()
        open_btn = QPushButton("✏️ Anwesenheit eintragen …")
        open_btn.setStyleSheet(
            "background:#3498db;color:white;font-weight:bold;padding:6px 12px;"
        )
        open_btn.clicked.connect(lambda: self._open_selected(self._list.currentItem()))
        later_btn = QPushButton("Später")
        later_btn.clicked.connect(self.accept)
        bl.addWidget(open_btn); bl.addWidget(later_btn)
        lay.addLayout(bl)

    def _open_selected(self, item):
        if not item:
            return
        sid = item.data(Qt.UserRole)
        self.cur.execute("""
            SELECT se.title, se.entry_date, se.group_id, p.name
            FROM shared_entries se
            LEFT JOIN persons p ON se.group_id = p.id
            WHERE se.id=?
        """, (sid,))
        r = self.cur.fetchone()
        if not r:
            return
        title, edate, gid, gname = r

        members = _get_active_group_members(self.cur, gid, at_date=edate)
        if not members:
            QMessageBox.information(
                self, "Keine Mitglieder",
                "Diese Gruppe hat (zum Zeitpunkt) keine Mitglieder."
            )
            return

        from calendar_sync import GroupAttendanceRequest, resolve_pending_attendance
        req = GroupAttendanceRequest(
            group_id     = gid,
            group_name   = gname or "?",
            member_ids   = [m[0] for m in members],
            member_names = [m[1] for m in members],
            event_title  = title or "?",
            event_date   = edate,
        )
        dlg = GroupAttendanceDialog(self, req, mode='pending')
        if dlg.exec():
            attended = dlg.get_attended()
            resolve_pending_attendance(self.con, sid, attended)
            row = self._list.row(item)
            self._list.takeItem(row)
            if self._list.count() == 0:
                QMessageBox.information(
                    self, "Fertig",
                    "Alle ausstehenden Anwesenheiten sind eingetragen."
                )
                self.accept()


def _get_active_group_members(cur, group_id, at_date=None) -> list[tuple[int, str]]:
    """Aktive Mitglieder einer Gruppe.
    Wenn at_date (YYYY-MM-DD): zum genannten Datum aktiv.
    Sonst: aktuell aktiv (left_at IS NULL).
    """
    cur.execute("PRAGMA table_info(group_members)")
    cols = {r[1] for r in cur.fetchall()}
    has_left = 'left_at' in cols

    if at_date and has_left:
        cur.execute("""
            SELECT DISTINCT gm.person_id, p.name
            FROM group_members gm
            JOIN persons p ON gm.person_id = p.id
            WHERE gm.group_id = ?
              AND (gm.joined_at IS NULL OR gm.joined_at <= ?)
              AND (gm.left_at IS NULL OR gm.left_at = '' OR gm.left_at > ?)
            ORDER BY p.name
        """, (group_id, at_date, at_date))
    elif has_left:
        cur.execute("""
            SELECT DISTINCT gm.person_id, p.name
            FROM group_members gm
            JOIN persons p ON gm.person_id = p.id
            WHERE gm.group_id = ?
              AND (gm.left_at IS NULL OR gm.left_at = '')
            ORDER BY p.name
        """, (group_id,))
    else:
        cur.execute("""
            SELECT DISTINCT gm.person_id, p.name
            FROM group_members gm
            JOIN persons p ON gm.person_id = p.id
            WHERE gm.group_id = ?
            ORDER BY p.name
        """, (group_id,))
    return [(r[0], r[1]) for r in cur.fetchall()]


# ── Mitglieder-Manager für eine Gruppe (Punkt 6) ─────────────────────────────
class GroupMemberManagerDialog(QDialog):
    """Verwaltet Mitglieder einer Gruppe inkl. Eintritts-/Austrittsdatum
    und Notiz. Re-Eintritte = neuer group_members-Datensatz; alte bleiben
    in der Historie erhalten.
    """

    def __init__(self, parent, group_id, cur, con):
        super().__init__(parent)
        self.group_id = group_id
        self.cur = cur; self.con = con

        cur.execute("SELECT name FROM persons WHERE id=?", (group_id,))
        row = cur.fetchone()
        self.group_name = row[0] if row else "?"

        self.setWindowTitle(f"📁 Mitglieder: {self.group_name}")
        self.resize(720, 540)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"<h3>📁 {self.group_name}</h3>"))

        tabs = QTabWidget()
        lay.addWidget(tabs)

        # Tab 1: aktuelle Mitglieder
        cur_tab = QWidget(); ct = QVBoxLayout(cur_tab)
        ct.addWidget(QLabel("Aktuelle Mitglieder (noch kein Austrittsdatum):"))
        self._current_list = QListWidget()
        self._current_list.itemDoubleClicked.connect(self._edit_membership)
        ct.addWidget(self._current_list)

        cb = QHBoxLayout()
        add_btn = QPushButton("➕ Person hinzufügen …")
        add_btn.clicked.connect(self._add_member)
        leave_btn = QPushButton("🚪 Austritt eintragen …")
        leave_btn.clicked.connect(self._mark_left)
        edit_btn = QPushButton("✏️ Bearbeiten …")
        edit_btn.clicked.connect(lambda: self._edit_membership(self._current_list.currentItem()))
        for b in (add_btn, leave_btn, edit_btn):
            cb.addWidget(b)
        ct.addLayout(cb)

        tabs.addTab(cur_tab, "👥 Aktuell")

        # Tab 2: Historie
        hist_tab = QWidget(); ht = QVBoxLayout(hist_tab)
        ht.addWidget(QLabel("Vollständige Historie (inkl. ausgetretener Mitglieder):"))
        self._hist_list = QListWidget()
        self._hist_list.itemDoubleClicked.connect(self._edit_membership)
        ht.addWidget(self._hist_list)

        hb = QHBoxLayout()
        rejoin_btn = QPushButton("↩️ Wieder eintreten lassen …")
        rejoin_btn.clicked.connect(self._rejoin)
        edit_h_btn = QPushButton("✏️ Bearbeiten …")
        edit_h_btn.clicked.connect(lambda: self._edit_membership(self._hist_list.currentItem()))
        for b in (rejoin_btn, edit_h_btn):
            hb.addWidget(b)
        ht.addLayout(hb)

        tabs.addTab(hist_tab, "🕘 Historie")

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.accept)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)

        self._refresh()

    def _refresh(self):
        self._current_list.clear()
        self._hist_list.clear()

        self.cur.execute("""
            SELECT gm.id, gm.person_id, p.name, gm.joined_at, gm.note
            FROM group_members gm
            JOIN persons p ON gm.person_id = p.id
            WHERE gm.group_id = ?
              AND (gm.left_at IS NULL OR gm.left_at = '')
            ORDER BY p.name
        """, (self.group_id,))
        for gm_id, pid, pname, joined, note in self.cur.fetchall():
            joined_disp = format_date_german(joined) if joined else "?"
            txt = f"👤 {pname}  |  Eintritt: {joined_disp}"
            if note:
                txt += f"  |  📝 {note}"
            item = QListWidgetItem(txt)
            item.setData(Qt.UserRole, gm_id)
            item.setToolTip(f"Mitgliedschafts-ID: {gm_id}")
            self._current_list.addItem(item)

        self.cur.execute("""
            SELECT gm.id, gm.person_id, p.name, gm.joined_at, gm.left_at, gm.note
            FROM group_members gm
            JOIN persons p ON gm.person_id = p.id
            WHERE gm.group_id = ?
            ORDER BY gm.joined_at DESC, p.name
        """, (self.group_id,))
        for gm_id, pid, pname, joined, left, note in self.cur.fetchall():
            joined_disp = format_date_german(joined) if joined else "?"
            if left:
                left_disp = format_date_german(left)
                txt = f"👤 {pname}  |  {joined_disp} → {left_disp}  (ausgetreten)"
                fg = QColor("#7f8c8d")
            else:
                txt = f"👤 {pname}  |  seit {joined_disp}  (aktiv)"
                fg = QColor("#27ae60")
            if note:
                txt += f"  |  📝 {note}"
            item = QListWidgetItem(txt)
            item.setData(Qt.UserRole, gm_id)
            item.setForeground(fg)
            self._hist_list.addItem(item)

    def _add_member(self):
        self.cur.execute("""
            SELECT id, name FROM persons
            WHERE COALESCE(is_group,0)=0
              AND COALESCE(deleted,0)=0
              AND id NOT IN (
                SELECT person_id FROM group_members
                WHERE group_id=?
                  AND (left_at IS NULL OR left_at='')
              )
            ORDER BY name
        """, (self.group_id,))
        cands = self.cur.fetchall()
        if not cands:
            QMessageBox.information(self, "Keine Kandidaten",
                                    "Alle Personen sind bereits Mitglied.")
            return

        names = [c[1] for c in cands]
        sel, ok = QInputDialog.getItem(self, "Person hinzufügen",
                                       "Person:", names, 0, False)
        if not ok:
            return
        pid = next(c[0] for c in cands if c[1] == sel)

        today_de = datetime.now().strftime("%d.%m.%Y")
        date_str, ok = QInputDialog.getText(
            self, "Eintrittsdatum",
            "Eintrittsdatum (TT.MM.JJJJ, leer = heute):", text=today_de
        )
        if not ok:
            return
        joined_iso = normalize_date(date_str.strip()) or datetime.now().strftime("%Y-%m-%d")

        note, ok2 = QInputDialog.getText(self, "Notiz",
                                         "Notiz (optional):", text="")
        if not ok2:
            note = ""

        self.cur.execute("""
            INSERT INTO group_members (group_id, person_id, joined_at, note)
            VALUES (?, ?, ?, ?)
        """, (self.group_id, pid, joined_iso, note.strip() or None))
        self.con.commit()
        self._refresh()

    def _mark_left(self):
        item = self._current_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Auswahl fehlt",
                                "Bitte ein Mitglied wählen.")
            return
        gm_id = item.data(Qt.UserRole)

        today_de = datetime.now().strftime("%d.%m.%Y")
        date_str, ok = QInputDialog.getText(
            self, "Austrittsdatum",
            "Austrittsdatum (TT.MM.JJJJ, leer = heute):", text=today_de
        )
        if not ok:
            return
        left_iso = normalize_date(date_str.strip()) or datetime.now().strftime("%Y-%m-%d")

        note, ok2 = QInputDialog.getText(
            self, "Notiz", "Notiz zum Austritt (optional):", text=""
        )
        cur_note_addition = note.strip()
        self.cur.execute("SELECT note FROM group_members WHERE id=?", (gm_id,))
        old_note = (self.cur.fetchone() or (None,))[0] or ""
        if cur_note_addition:
            new_note = (old_note + " | " if old_note else "") + f"Austritt: {cur_note_addition}"
        else:
            new_note = old_note

        self.cur.execute("""
            UPDATE group_members SET left_at=?, note=? WHERE id=?
        """, (left_iso, new_note or None, gm_id))
        self.con.commit()
        self._refresh()

    def _rejoin(self):
        item = self._hist_list.currentItem()
        if not item:
            QMessageBox.warning(self, "Auswahl fehlt",
                                "Bitte ein historisches Mitglied wählen.")
            return
        gm_id = item.data(Qt.UserRole)
        self.cur.execute(
            "SELECT person_id, left_at FROM group_members WHERE id=?", (gm_id,)
        )
        r = self.cur.fetchone()
        if not r or not r[1]:
            QMessageBox.information(
                self, "Bereits aktiv",
                "Diese Mitgliedschaft ist bereits aktiv."
            )
            return
        pid = r[0]

        today_de = datetime.now().strftime("%d.%m.%Y")
        date_str, ok = QInputDialog.getText(
            self, "Wieder-Eintritt",
            "Wieder-Eintrittsdatum (TT.MM.JJJJ, leer = heute):", text=today_de
        )
        if not ok:
            return
        joined_iso = normalize_date(date_str.strip()) or datetime.now().strftime("%Y-%m-%d")

        note, ok2 = QInputDialog.getText(
            self, "Notiz", "Notiz (optional):", text="Wiedereintritt"
        )
        self.cur.execute("""
            INSERT INTO group_members (group_id, person_id, joined_at, note)
            VALUES (?, ?, ?, ?)
        """, (self.group_id, pid, joined_iso, note.strip() or None))
        self.con.commit()
        self._refresh()

    def _edit_membership(self, item):
        if not item:
            return
        gm_id = item.data(Qt.UserRole)
        self.cur.execute("""
            SELECT joined_at, left_at, note FROM group_members WHERE id=?
        """, (gm_id,))
        r = self.cur.fetchone()
        if not r:
            return
        joined, left, note = r

        joined_de = format_date_german(joined) if joined else ""
        left_de   = format_date_german(left) if left else ""

        new_joined, ok = QInputDialog.getText(
            self, "Eintrittsdatum bearbeiten",
            "Eintrittsdatum (TT.MM.JJJJ):", text=joined_de
        )
        if not ok:
            return
        new_left, ok2 = QInputDialog.getText(
            self, "Austrittsdatum bearbeiten",
            "Austrittsdatum (leer = noch aktiv):", text=left_de
        )
        if not ok2:
            return
        new_note, ok3 = QInputDialog.getText(
            self, "Notiz", "Notiz:", text=note or ""
        )
        if not ok3:
            return

        joined_iso = normalize_date(new_joined.strip()) if new_joined.strip() else None
        left_iso   = normalize_date(new_left.strip())   if new_left.strip()   else None

        self.cur.execute("""
            UPDATE group_members
            SET joined_at=?, left_at=?, note=?
            WHERE id=?
        """, (joined_iso, left_iso, new_note.strip() or None, gm_id))
        self.con.commit()
        self._refresh()


# ── Kalender-Sync-Dialog ──────────────────────────────────────────────────────
# ── Blacklist-Editor ──────────────────────────────────────────────────────────
class BlacklistEditorDialog(QDialog):
    """Editor für die Kalender-Blacklist (Namen, die beim Sync immer ignoriert
    werden, z. B. 'Kuckuck', 'Hautarzt')."""

    def __init__(self, parent, con):
        super().__init__(parent)
        self.con = con
        from person_matcher import Blacklist
        self.bl  = Blacklist(con)

        self.setWindowTitle("🚫 Kalender-Blacklist")
        self.resize(420, 380)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            "<b>Diese Namen werden beim Google-Kalender-Sync ignoriert.</b>"
        ))
        lay.addWidget(QLabel(
            "Z. B. Termine wie „Kuckuck: Termin“, „Hautarzt: Kontrolle“ – "
            "alles, was Du nicht in der Personen-Kartei haben willst."
        ))

        self._list = QListWidget()
        lay.addWidget(self._list)

        # Hinzufügen
        addrow = QHBoxLayout()
        self._add_edit = QLineEdit()
        self._add_edit.setPlaceholderText("Neuer Eintrag (z. B. „Hautarzt“)")
        self._add_edit.returnPressed.connect(self._add)
        add_btn = QPushButton("➕ Hinzufügen")
        add_btn.clicked.connect(self._add)
        addrow.addWidget(self._add_edit); addrow.addWidget(add_btn)
        lay.addLayout(addrow)

        # Entfernen
        rm_btn = QPushButton("🗑️ Markiertes entfernen")
        rm_btn.clicked.connect(self._remove)
        lay.addWidget(rm_btn)

        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.accept)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)

        self._refresh()

    def _refresh(self):
        self._list.clear()
        for name in self.bl.all():
            self._list.addItem(QListWidgetItem(f"🚫 {name}"))

    def _add(self):
        name = self._add_edit.text().strip()
        if not name:
            return
        self.bl.add(name)
        self._add_edit.clear()
        self._refresh()

    def _remove(self):
        item = self._list.currentItem()
        if not item:
            return
        name = item.text().lstrip("🚫 ").strip()
        if QMessageBox.question(
            self, "Entfernen?",
            f"„{name}“ von der Blacklist entfernen?",
            QMessageBox.Yes | QMessageBox.No
        ) == QMessageBox.Yes:
            self.bl.remove(name)
            self._refresh()


# ── Kalender-Sync-Dialog ──────────────────────────────────────────────────────
class GoogleCalendarSyncDialog(QDialog):
    """
    Haupt-Dialog für die Google-Kalender-Synchronisation.

    Zeigt Fortschritt, fragt bei Konflikten nach und gibt eine
    Zusammenfassung aus.
    """

    def __init__(self, parent, cur, con):
        super().__init__(parent)
        self.setWindowTitle("📅 Google Kalender synchronisieren")
        self.resize(680, 620)
        self.cur = cur; self.con = con
        self._syncer = None

        lay = QVBoxLayout(self)

        # ── Kopf ─────────────────────────────────────────────────────────────
        lay.addWidget(QLabel("<h3>📅 Google Kalender → PersonenKartei</h3>"))
        lay.addWidget(QLabel(
            "Importiert Kalendereinträge der Form\n"
            "<i>Anna, Max &amp; Julius: Café am Neckar</i> "
            "als Treffen in die Kartei."
        ))

        # ── Einstellungen ────────────────────────────────────────────────────
        settings_group = QGroupBox("Einstellungen")
        sl = QFormLayout()

        self._months_back_spin = QSpinBox()
        self._months_back_spin.setRange(0, 60)
        self._months_back_spin.setValue(12)
        self._months_back_spin.setSuffix(" Monate")
        sl.addRow("Zeitraum zurück:", self._months_back_spin)

        self._months_fwd_spin = QSpinBox()
        self._months_fwd_spin.setRange(0, 24)
        self._months_fwd_spin.setValue(0)
        self._months_fwd_spin.setSuffix(" Monate")
        self._months_fwd_spin.setToolTip(
            "Zukunfts-Zeitraum: bezieht auch geplante Termine ein."
        )
        sl.addRow("Zeitraum vorwärts:", self._months_fwd_spin)

        self._creds_lbl = QLabel()
        self._update_creds_status()
        sl.addRow("Credentials:", self._creds_lbl)

        creds_btn = QPushButton("📁 credentials.json wählen …")
        creds_btn.clicked.connect(self._choose_credentials)
        sl.addRow("", creds_btn)

        bl_btn = QPushButton("🚫 Blacklist verwalten …")
        bl_btn.setToolTip("Namen wie „Kuckuck“, „Hautarzt“ dauerhaft ignorieren.")
        bl_btn.clicked.connect(self._open_blacklist)
        sl.addRow("", bl_btn)

        settings_group.setLayout(sl)
        lay.addWidget(settings_group)

        # ── Fortschrittsbalken ───────────────────────────────────────────────
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setVisible(False)
        lay.addWidget(self._progress_bar)

        self._progress_lbl = QLabel("")
        self._progress_lbl.setStyleSheet("color:#7f8c8d;font-style:italic;")
        lay.addWidget(self._progress_lbl)

        # ── Log-Ausgabe ──────────────────────────────────────────────────────
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMinimumHeight(200)
        self._log.setStyleSheet("font-family:monospace;font-size:12px;")
        lay.addWidget(self._log)

        # ── Buttons ──────────────────────────────────────────────────────────
        bl = QHBoxLayout()
        self._sync_btn = QPushButton("🔄 Synchronisation starten")
        self._sync_btn.setStyleSheet(
            "background:#3498db;color:white;font-weight:bold;padding:8px 16px;"
        )
        self._sync_btn.clicked.connect(self._start_sync)

        close_btn = QPushButton("Schließen")
        close_btn.clicked.connect(self.accept)

        bl.addWidget(self._sync_btn); bl.addWidget(close_btn)
        lay.addLayout(bl)

    def _update_creds_status(self):
        from calendar_sync import CREDENTIALS_FILE, TOKEN_FILE
        if os.path.exists(TOKEN_FILE):
            self._creds_lbl.setText("✅ Angemeldet (Token vorhanden)")
            self._creds_lbl.setStyleSheet("color:#27ae60;")
        elif os.path.exists(CREDENTIALS_FILE):
            self._creds_lbl.setText("🔑 credentials.json vorhanden – Login noch nötig")
            self._creds_lbl.setStyleSheet("color:#e67e22;")
        else:
            self._creds_lbl.setText("❌ Keine credentials.json")
            self._creds_lbl.setStyleSheet("color:#e74c3c;")

    def _choose_credentials(self):
        from calendar_sync import CREDENTIALS_FILE
        path, _ = QFileDialog.getOpenFileName(
            self, "credentials.json auswählen", "", "JSON (*.json)"
        )
        if path:
            import shutil
            shutil.copy2(path, CREDENTIALS_FILE)
            self._log_line(f"✅ credentials.json kopiert nach {CREDENTIALS_FILE}")
            self._update_creds_status()

    def _open_blacklist(self):
        BlacklistEditorDialog(self, self.con).exec()

    def _log_line(self, text: str):
        self._log.append(text)
        self._log.verticalScrollBar().setValue(
            self._log.verticalScrollBar().maximum()
        )

    def _on_progress(self, current: int, total: int, desc: str):
        if total > 0:
            pct = int(current / total * 100)
            self._progress_bar.setValue(pct)
        # Vollständigen Eventtitel anzeigen, ohne Kürzung
        self._progress_lbl.setText(desc)
        QApplication.processEvents()

    def _make_resolve_callback(self):
        """Erzeugt eine Callback-Funktion die Qt-Dialoge öffnet.
        Signatur: (match_result, event_title) -> int | None"""

        def _resolve(match_result, event_title: str = "") -> int | None:
            # Vollständigen Titel im Log: [Name]: [Titel]
            self._log_line(
                f"❓ „{match_result.query}“ nicht eindeutig in: {event_title}"
            )

            dlg    = PersonResolveDialog(self, match_result, self.cur,
                                         event_title=event_title)
            dlg.exec()
            choice = dlg.get_choice()

            if choice is None:
                self._log_line(f"   ⏭️  Ignoriert: {match_result.query}")
            elif choice == PersonResolveDialog.SENTINEL_NEW_PERSON:
                self._log_line(
                    f"   ➕ Neue Person angelegt: {match_result.query}"
                )
            elif choice == PersonResolveDialog.SENTINEL_BLACKLIST:
                self._log_line(
                    f"   🚫 Blacklisted: {match_result.query}"
                )
            elif choice < 0:
                # Gruppe (-group_id)
                gid = -choice
                self.cur.execute("SELECT name FROM groups WHERE id=?", (gid,))
                r  = self.cur.fetchone()
                gn = r[0] if r else "?"
                self._log_line(
                    f"   📁 Zugeordnet zu Gruppe: {match_result.query} → {gn}"
                )
            else:
                self.cur.execute("SELECT name FROM persons WHERE id=?", (choice,))
                r     = self.cur.fetchone()
                pname = r[0] if r else "?"
                self._log_line(
                    f"   🔗 Zugeordnet: {match_result.query} → {pname}"
                )

            return choice

        return _resolve

    def _make_merge_callback(self):
        """Wird aufgerufen, wenn ein bestehender manueller Eintrag mit
        identischem Datum gefunden wurde (Punkt 4).
        Rückgabe: True = ergänzen, False = trotzdem als neuen Eintrag.
        """
        def _merge_cb(proposal) -> bool:
            self._log_line(
                f"🔄 Bestehender Eintrag für {proposal.person_name} am "
                f"{format_date_german(proposal.existing_date)} gefunden – "
                f"frage Nutzer …"
            )
            dlg = MergeProposalDialog(self, proposal)
            dlg.exec()
            choice = dlg.get_choice()
            if choice:
                self._log_line(
                    f"   ✅ Felder ergänzt im bestehenden Eintrag "
                    f"#{proposal.existing_shared_id}"
                )
            else:
                self._log_line(
                    f"   ➕ Wird trotzdem als neuer Eintrag importiert"
                )
            return choice
        return _merge_cb

    def _make_group_attendance_callback(self):
        """Punkt 7: Wer war beim Gruppen-Treffen dabei?
        Wird nur für Vergangenheits-Treffen aufgerufen.
        Rückgabe: list[int] der angehakten person_ids, oder None.
        """
        def _att_cb(request):
            self._log_line(
                f"👥 Gruppen-Treffen erkannt: {request.group_name} "
                f"am {format_date_german(request.event_date)}"
            )
            dlg = GroupAttendanceDialog(self, request, mode='past')
            if not dlg.exec():
                self._log_line(f"   ⏭️  Ignoriert")
                return None
            attended = dlg.get_attended()
            self._log_line(f"   ✅ {len(attended)} von {len(request.member_ids)} dabei")
            return attended
        return _att_cb

    def _start_sync(self):
        try:
            from calendar_sync import CalendarSyncer, _check_google_libs
            if not _check_google_libs():
                QMessageBox.critical(
                    self, "Fehlende Pakete",
                    "Bitte installiere die Google-API-Pakete:\n\n"
                    "pip install google-api-python-client "
                    "google-auth-httplib2 google-auth-oauthlib"
                )
                return
        except ImportError as e:
            QMessageBox.critical(self, "Import-Fehler", str(e))
            return

        self._sync_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._log.clear()
        self._log_line("🔄 Starte Synchronisation …")

        try:
            syncer = CalendarSyncer(
                con                       = self.con,
                months_back               = self._months_back_spin.value(),
                months_forward            = self._months_fwd_spin.value(),
                resolve_callback          = self._make_resolve_callback(),
                merge_callback            = self._make_merge_callback(),
                group_attendance_callback = self._make_group_attendance_callback(),
                progress_callback         = self._on_progress,
            )
            sync_result = syncer.sync()

        except Exception as e:
            self._log_line(f"❌ Fehler: {e}")
            self._sync_btn.setEnabled(True)
            return

        # Ergebnis anzeigen
        self._log_line("")
        self._log_line("─" * 50)
        self._log_line("📋 Zusammenfassung:")
        for line in sync_result.summary().split("\n"):
            self._log_line(f"   {line}")

        if sync_result.errors:
            self._log_line("")
            self._log_line("⚠️  Fehler:")
            for err in sync_result.errors:
                self._log_line(f"   • {err}")

        self._progress_bar.setValue(100)
        self._progress_lbl.setText("✅ Fertig")
        self._sync_btn.setEnabled(True)

        # Hauptfenster aktualisieren
        if hasattr(self.parent(), 'load_persons'):
            self.parent().load_persons()
        if hasattr(self.parent(), '_refresh_statusbar'):
            self.parent()._refresh_statusbar()


# ── Integration in MainWindow ─────────────────────────────────────────────────

def _show_google_sync(self):
    """Öffnet den Google-Kalender-Sync-Dialog."""
    GoogleCalendarSyncDialog(self, self.cur, self.con).exec()

MainWindow.show_google_sync = _show_google_sync


def _show_alias_editor(self):
    """Öffnet den Alias-Editor für die aktuelle Person."""
    if not self.current_person_id:
        QMessageBox.warning(self, "Fehler", "Bitte zuerst Person auswählen")
        return
    AliasEditorDialog(self, self.current_person_id, self.cur, self.con).exec()

MainWindow.show_alias_editor = _show_alias_editor


# ── Button in die bestehende UI einbauen (Monkey-Patch) ───────────────────────



if __name__ == "__main__":
    init_db()
    migrate_database()
    app = QApplication(sys.argv)
    w   = MainWindow()
    w.show()
    sys.exit(app.exec())
