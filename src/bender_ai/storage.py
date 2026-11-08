"""SQLite owns durable state; worker threads use serialized short transactions."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import uuid
from .models import Plan, ValidationError


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if self.db.execute('PRAGMA user_version').fetchone()[0] > 1:
            self.db.close()
            raise ValidationError('База создана более новой версией приложения')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS skills(id TEXT PRIMARY KEY, name TEXT, current_version INTEGER);
        CREATE TABLE IF NOT EXISTS skill_versions(skill_id TEXT REFERENCES skills ON DELETE CASCADE,
          version INTEGER, plan TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, validation TEXT DEFAULT 'unverified',
          PRIMARY KEY(skill_id, version));
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, name TEXT, status TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS run_steps(run_id TEXT REFERENCES runs ON DELETE CASCADE,
          position INTEGER, action TEXT, status TEXT, detail TEXT, PRIMARY KEY(run_id,position));
        CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY, event TEXT, action TEXT,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS chat_sessions(id TEXT PRIMARY KEY, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        PRAGMA user_version=1;
        ''')
        self.db.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        self.db.execute("UPDATE run_steps SET status='skipped' WHERE status='pending'")
        self.db.execute("UPDATE run_steps SET status='interrupted' WHERE status='running'")
        self.db.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            with self.db:
                yield self.db

    def close(self):
        with self.lock:
            self.db.close()

    def setting(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key, value):
        with self.transaction() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', (key, json.dumps(value)))

    def save_skill(self, plan, skill_id=None):
        plan = Plan.from_dict(plan.to_dict())
        skill_id = skill_id or uuid.uuid4().hex
        with self.transaction() as db:
            row = db.execute('SELECT MAX(version) FROM skill_versions WHERE skill_id=?', (skill_id,)).fetchone()
            version = (row[0] or 0) + 1
            db.execute('INSERT INTO skills VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,current_version=excluded.current_version',
                       (skill_id, plan.name, version))
            db.execute('INSERT INTO skill_versions(skill_id,version,plan) VALUES(?,?,?)', (skill_id, version, plan.to_json()))
        return skill_id

    def skills(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM skills ORDER BY name')]

    def versions(self, skill_id):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT version,created_at,validation FROM skill_versions WHERE skill_id=? ORDER BY version DESC', (skill_id,))]

    def load_skill(self, skill_id, version=None):
        with self.lock:
            row = self.db.execute('SELECT plan FROM skill_versions WHERE skill_id=? AND version=COALESCE(?,(SELECT current_version FROM skills WHERE id=?))',
                                  (skill_id, version, skill_id)).fetchone()
            if not row:
                raise ValidationError('Навык или версия не найдены')
            return Plan.from_json(row[0])

    def restore_version(self, skill_id, version):
        return self.save_skill(self.load_skill(skill_id, version), skill_id)

    def mark_validation(self, skill_id, version, status):
        with self.transaction() as db:
            db.execute('UPDATE skill_versions SET validation=? WHERE skill_id=? AND version=?', (status, skill_id, version))

    def delete_skill(self, skill_id):
        with self.transaction() as db:
            db.execute('DELETE FROM skills WHERE id=?', (skill_id,))

    def export_skill(self, skill_id):
        return json.dumps({'schema': 1, 'plan': self.load_skill(skill_id).to_dict()}, ensure_ascii=False, indent=2)

    def import_skill(self, raw):
        try:
            data = json.loads(raw)
            if set(data) != {'schema', 'plan'} or data['schema'] != 1:
                raise ValidationError('Неподдерживаемая версия экспорта')
            return self.save_skill(Plan.from_dict(data['plan']))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValidationError('Некорректный экспорт') from exc

    def start_run(self, plan):
        run = uuid.uuid4().hex
        with self.transaction() as db:
            db.execute('INSERT INTO runs(id,name,status) VALUES(?,?,?)', (run, f'План из {len(plan.steps)} шагов', 'running'))
            db.execute('DELETE FROM runs WHERE rowid NOT IN (SELECT rowid FROM runs ORDER BY rowid DESC LIMIT 2000)')
            db.executemany('INSERT INTO run_steps VALUES(?,?,?,?,?)', [(run, i, s.action, 'pending', '') for i,s in enumerate(plan.steps)])
        return run

    def step_status(self, run, i, status, detail=''):
        with self.transaction() as db:
            db.execute('UPDATE run_steps SET status=?,detail=? WHERE run_id=? AND position=?', (status, detail, run, i))

    def finish_run(self, run, status):
        with self.transaction() as db:
            db.execute('UPDATE runs SET status=? WHERE id=?', (status, run))

    def runs(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM runs ORDER BY created_at DESC,rowid DESC LIMIT 200')]

    def run_steps(self, run):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM run_steps WHERE run_id=? ORDER BY position', (run,))]

    def log(self, event, action):
        # Arguments, UI text, titles, prompts and provider errors are deliberately excluded.
        with self.transaction() as db:
            db.execute('INSERT INTO audit_events(event,action) VALUES(?,?)', (event, action))
            db.execute('DELETE FROM audit_events WHERE id <= (SELECT MAX(id)-10000 FROM audit_events)')

    def audit(self):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM audit_events ORDER BY id DESC LIMIT 500')]

    def clear_history(self):
        with self.transaction() as db:
            db.execute('DELETE FROM runs')
            db.execute('DELETE FROM audit_events')
