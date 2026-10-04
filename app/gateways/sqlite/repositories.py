"""Repositories over the local SQLite database.

Rule: every query on product data filters on `org_id`. Repositories are constructed with
the caller's org, so forgetting the filter is not possible from endpoint code.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from app.data.models.agent import AgentCheckpoint, Chat, ChatMessage, Proposal, ProposalStatus
from app.data.models.automation import Automation, AutomationRun, AutomationVersion
from app.data.models.discovery import (
    DiscoveryRun,
    DiscoveryStep,
    DiscoveryStepId,
    Opportunity,
    StepStatus,
)
from app.data.models.document import Chunk, Document, DocumentStatus, TabularProfile
from app.data.models.knowledge import (
    DocumentCard,
    DocumentSignal,
    Fact,
    MemoryDoc,
    MemoryKind,
    Network,
    NetworkNote,
    NetworkStatus,
)
from app.data.models.suggestion import OPEN_STATUSES, Suggestion, SuggestionKind, SuggestionStatus
from app.data.models.user import User, UserSettings
from app.gateways.sqlite.database import Database
from app.helpers.ids import new_id
from app.helpers.time import iso, parse_iso, utcnow


def _j(v: Any) -> str:
    return json.dumps(v, default=str, ensure_ascii=False)


# --- identity -------------------------------------------------------------------------
class UserRepo:
    def __init__(self, db: Database):
        self.db = db

    def upsert(self, user: User) -> User:
        now = iso()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO orgs(id, name, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET name = COALESCE(excluded.name, orgs.name)",
                (user.org_id, user.org_name, now),
            )
            c.execute(
                "INSERT INTO users(id, org_id, email, display_name, roles, created_at, last_login_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET org_id = excluded.org_id, "
                "email = excluded.email, display_name = excluded.display_name, roles = excluded.roles, "
                "last_login_at = excluded.last_login_at",
                (user.id, user.org_id, user.email, user.display_name, _j(user.roles), iso(user.created_at), now),
            )
        return user

    def get(self, user_id: str) -> User | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT u.*, o.name AS org_name FROM users u JOIN orgs o ON o.id = u.org_id WHERE u.id = ?",
                (user_id,),
            ).fetchone()
        if not r:
            return None
        return User(
            id=r["id"],
            org_id=r["org_id"],
            email=r["email"],
            display_name=r["display_name"],
            org_name=r["org_name"],
            roles=json.loads(r["roles"]),
            created_at=parse_iso(r["created_at"]),
            last_login_at=parse_iso(r["last_login_at"]),
        )

    def get_settings(self, user_id: str) -> UserSettings:
        with self.db.read() as c:
            r = c.execute("SELECT data FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        return UserSettings.model_validate_json(r["data"]) if r else UserSettings()

    def save_settings(self, user_id: str, settings: UserSettings) -> UserSettings:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO user_settings(user_id, data) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET data = excluded.data",
                (user_id, settings.model_dump_json()),
            )
        return settings


class SessionRepo:
    """Local UI sessions. Only a hash of the cookie value is stored."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _hash(session_id: str) -> str:
        return hashlib.sha256(session_id.encode()).hexdigest()

    def create(self, session_id: str, user: User, expires_at: datetime) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO sessions(id_hash, user_id, org_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
                (self._hash(session_id), user.id, user.org_id, iso(), iso(expires_at)),
            )

    def resolve(self, session_id: str) -> str | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT user_id, expires_at FROM sessions WHERE id_hash = ?", (self._hash(session_id),)
            ).fetchone()
        if not r or parse_iso(r["expires_at"]) < utcnow():
            return None
        return r["user_id"]

    def delete(self, session_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE id_hash = ?", (self._hash(session_id),))

    def purge_expired(self) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (iso(),))


# --- documents --------------------------------------------------------------------------
class DocumentRepo:
    def __init__(self, db: Database, org_id: str):
        self.db, self.org_id = db, org_id

    def _row(self, r: Any) -> Document:
        d = dict(r)
        d.pop("markdown", None)
        return Document.model_validate(d)

    def insert(self, doc: Document) -> Document:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO documents(id, org_id, filename, title, kind, department, size_bytes, rows, sha256, "
                "status, excerpt, error, authorized_at, uploaded_by, created_at, source_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc.id, self.org_id, doc.filename, doc.title, doc.kind.value, doc.department,
                    doc.size_bytes, doc.rows, doc.sha256, doc.status.value, doc.excerpt, doc.error,
                    iso(doc.authorized_at), doc.uploaded_by, iso(doc.created_at), doc.source_path,
                ),
            )
        return doc

    def by_sha(self, sha256: str) -> Document | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT * FROM documents WHERE org_id = ? AND sha256 = ?", (self.org_id, sha256)
            ).fetchone()
        return self._row(r) if r else None

    def get(self, doc_id: str) -> Document | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT * FROM documents WHERE org_id = ? AND id = ?", (self.org_id, doc_id)
            ).fetchone()
        return self._row(r) if r else None

    def list(self) -> list[Document]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT * FROM documents WHERE org_id = ? ORDER BY created_at DESC", (self.org_id,)
            ).fetchall()
        return [self._row(r) for r in rows]

    def markdown(self, doc_id: str) -> str:
        with self.db.read() as c:
            r = c.execute(
                "SELECT markdown FROM documents WHERE org_id = ? AND id = ?", (self.org_id, doc_id)
            ).fetchone()
        return (r["markdown"] or "") if r else ""

    def mark_ingested(
        self, doc_id: str, markdown: str, excerpt: str, rows: int | None, department: str | None
    ) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE documents SET status = ?, markdown = ?, excerpt = ?, rows = ?, "
                "department = COALESCE(?, department), error = NULL WHERE org_id = ? AND id = ?",
                (DocumentStatus.ingested.value, markdown, excerpt, rows, department, self.org_id, doc_id),
            )

    def set_department(self, doc_id: str, department: str | None) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE documents SET department = ? WHERE org_id = ? AND id = ?",
                (department, self.org_id, doc_id),
            )

    def mark_failed(self, doc_id: str, error: str) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE documents SET status = ?, error = ? WHERE org_id = ? AND id = ?",
                (DocumentStatus.failed.value, error[:500], self.org_id, doc_id),
            )

    def delete(self, doc_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM documents WHERE org_id = ? AND id = ?", (self.org_id, doc_id))

    # chunks
    def replace_chunks(self, doc_id: str, chunks: list[Chunk]) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM chunks WHERE org_id = ? AND document_id = ?", (self.org_id, doc_id))
            c.executemany(
                'INSERT INTO chunks(id, org_id, document_id, ordinal, heading, text, start, "end") '
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(ch.id, self.org_id, doc_id, ch.ordinal, ch.heading, ch.text, ch.start, ch.end) for ch in chunks],
            )

    def all_chunks(self) -> list[Chunk]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT ch.* FROM chunks ch JOIN documents d ON d.id = ch.document_id "
                "WHERE ch.org_id = ? AND d.status = 'ingested' ORDER BY ch.document_id, ch.ordinal",
                (self.org_id,),
            ).fetchall()
        return [Chunk.model_validate(dict(r)) for r in rows]

    def chunks_for(self, doc_id: str) -> list[Chunk]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT * FROM chunks WHERE org_id = ? AND document_id = ? ORDER BY ordinal",
                (self.org_id, doc_id),
            ).fetchall()
        return [Chunk.model_validate(dict(r)) for r in rows]

    def chunk(self, chunk_id: str) -> Chunk | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT * FROM chunks WHERE org_id = ? AND id = ?", (self.org_id, chunk_id)
            ).fetchone()
        return Chunk.model_validate(dict(r)) if r else None

    # tabular profiles
    def save_profiles(self, doc_id: str, profiles: list[TabularProfile]) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM tabular_profiles WHERE document_id = ?", (doc_id,))
            c.executemany(
                "INSERT INTO tabular_profiles(org_id, document_id, sheet, data) VALUES (?, ?, ?, ?)",
                [(self.org_id, doc_id, p.sheet, p.model_dump_json()) for p in profiles],
            )

    def profiles(self, doc_id: str | None = None) -> list[TabularProfile]:
        q, args = "SELECT data FROM tabular_profiles WHERE org_id = ?", [self.org_id]
        if doc_id:
            q += " AND document_id = ?"
            args.append(doc_id)
        with self.db.read() as c:
            rows = c.execute(q, args).fetchall()
        return [TabularProfile.model_validate_json(r["data"]) for r in rows]


# --- discovery ----------------------------------------------------------------------------
class DiscoveryRepo:
    def __init__(self, db: Database, org_id: str):
        self.db, self.org_id = db, org_id

    def _run(self, r: Any) -> DiscoveryRun:
        d = dict(r)
        d["document_ids"] = json.loads(d["document_ids"])
        d["error_params"] = json.loads(d.get("error_params") or "{}")
        return DiscoveryRun.model_validate(d)

    def create(self, run: DiscoveryRun) -> DiscoveryRun:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO discovery_runs(id, org_id, status, current_step, progress, started_by, document_ids, "
                "error, trace_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run.id, self.org_id, run.status.value, None, 0, run.started_by, _j(run.document_ids),
                    None, run.trace_id, iso(run.created_at),
                ),
            )
            c.executemany(
                "INSERT INTO discovery_steps(run_id, step, status) VALUES (?, ?, ?)",
                [(run.id, s.value, StepStatus.pending.value) for s in DiscoveryStepId],
            )
        return run

    def update(self, run: DiscoveryRun) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE discovery_runs SET status = ?, current_step = ?, progress = ?, error = ?, error_key = ?, "
                "error_params = ?, trace_id = ?, traced = ?, finished_at = ? WHERE org_id = ? AND id = ?",
                (
                    run.status.value, run.current_step.value if run.current_step else None, run.progress,
                    run.error, run.error_key, _j(run.error_params), run.trace_id, int(run.traced),
                    iso(run.finished_at) if run.finished_at else None, self.org_id, run.id,
                ),
            )

    def get(self, run_id: str) -> DiscoveryRun | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT * FROM discovery_runs WHERE org_id = ? AND id = ?", (self.org_id, run_id)
            ).fetchone()
        return self._run(r) if r else None

    def latest(self) -> DiscoveryRun | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT * FROM discovery_runs WHERE org_id = ? ORDER BY created_at DESC LIMIT 1", (self.org_id,)
            ).fetchone()
        return self._run(r) if r else None

    def latest_completed(self) -> DiscoveryRun | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT * FROM discovery_runs WHERE org_id = ? AND status = 'completed' "
                "ORDER BY created_at DESC LIMIT 1",
                (self.org_id,),
            ).fetchone()
        return self._run(r) if r else None

    def steps(self, run_id: str) -> list[DiscoveryStep]:
        with self.db.read() as c:
            rows = c.execute("SELECT * FROM discovery_steps WHERE run_id = ?", (run_id,)).fetchall()
        steps = []
        for r in rows:
            d = dict(r)
            d["output"] = json.loads(d["output"])
            steps.append(DiscoveryStep.model_validate(d))
        order = list(DiscoveryStepId)
        return sorted(steps, key=lambda s: order.index(s.step))

    def save_step(self, step: DiscoveryStep) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE discovery_steps SET status = ?, output = ?, error = ?, attempts = ?, started_at = ?, "
                "finished_at = ? WHERE run_id = ? AND step = ?",
                (
                    step.status.value, _j(step.output), step.error, step.attempts,
                    iso(step.started_at) if step.started_at else None,
                    iso(step.finished_at) if step.finished_at else None, step.run_id, step.step.value,
                ),
            )

    def save_signals(self, signals: list[DocumentSignal]) -> None:
        with self.db.tx() as c:
            c.executemany(
                "INSERT INTO doc_signals(org_id, run_id, document_id, data) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(run_id, document_id) DO UPDATE SET data = excluded.data",
                [(self.org_id, s.run_id, s.document_id, s.model_dump_json()) for s in signals],
            )

    def signals(self, run_id: str) -> list[DocumentSignal]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT data FROM doc_signals WHERE org_id = ? AND run_id = ?", (self.org_id, run_id)
            ).fetchall()
        return sorted(
            (DocumentSignal.model_validate_json(r["data"]) for r in rows), key=lambda s: -s.score
        )

    def save_opportunities(self, run_id: str, opps: list[Opportunity]) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM opportunities WHERE org_id = ? AND run_id = ?", (self.org_id, run_id))
            c.executemany(
                "INSERT INTO opportunities(id, org_id, run_id, data) VALUES (?, ?, ?, ?)",
                [(o.id, self.org_id, run_id, o.model_dump_json()) for o in opps],
            )

    def opportunities(self, run_id: str) -> list[Opportunity]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT data FROM opportunities WHERE org_id = ? AND run_id = ?", (self.org_id, run_id)
            ).fetchall()
        return sorted(
            (Opportunity.model_validate_json(r["data"]) for r in rows), key=lambda o: -o.hours_per_week
        )


# --- harness memory --------------------------------------------------------------------------
class MemoryRepo:
    def __init__(self, db: Database, org_id: str):
        self.db, self.org_id = db, org_id

    # facts
    def facts(self) -> list[Fact]:
        with self.db.read() as c:
            rows = c.execute("SELECT data FROM facts WHERE org_id = ?", (self.org_id,)).fetchall()
        return [Fact.model_validate_json(r["data"]) for r in rows]

    def save_fact(self, fact: Fact) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO facts(id, org_id, data) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
                (fact.id, self.org_id, fact.model_dump_json()),
            )

    def replace_fact(self, old: Fact, new: Fact, reason: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM facts WHERE org_id = ? AND id = ?", (self.org_id, old.id))
            c.execute(
                "INSERT INTO facts(id, org_id, data) VALUES (?, ?, ?)", (new.id, self.org_id, new.model_dump_json())
            )
            c.execute(
                "INSERT INTO fact_history(org_id, fact_id, replaced_by, reason, data, at) VALUES (?, ?, ?, ?, ?, ?)",
                (self.org_id, old.id, new.id, reason, old.model_dump_json(), iso()),
            )

    def fact_history(self) -> list[dict[str, Any]]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT fact_id, replaced_by, reason, data, at FROM fact_history WHERE org_id = ? ORDER BY id DESC",
                (self.org_id,),
            ).fetchall()
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    # networks + notes
    def save_network(self, net: Network) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO networks(id, org_id, run_id, status, automation_id, data) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, automation_id = excluded.automation_id, "
                "data = excluded.data",
                (net.id, self.org_id, net.run_id, net.status.value, net.automation_id, net.model_dump_json()),
            )

    def network(self, network_id: str) -> Network | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT data FROM networks WHERE org_id = ? AND id = ?", (self.org_id, network_id)
            ).fetchone()
        return Network.model_validate_json(r["data"]) if r else None

    def networks(self, run_id: str | None = None, status: NetworkStatus | None = None) -> list[Network]:
        q, args = "SELECT data FROM networks WHERE org_id = ?", [self.org_id]
        if run_id:
            q += " AND run_id = ?"
            args.append(run_id)
        if status:
            q += " AND status = ?"
            args.append(status.value)
        with self.db.read() as c:
            rows = c.execute(q, args).fetchall()
        return [Network.model_validate_json(r["data"]) for r in rows]

    def network_for_automation(self, automation_id: str) -> Network | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT data FROM networks WHERE org_id = ? AND automation_id = ? AND status = 'kept'",
                (self.org_id, automation_id),
            ).fetchone()
        return Network.model_validate_json(r["data"]) if r else None

    def delete_network(self, network_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM networks WHERE org_id = ? AND id = ?", (self.org_id, network_id))

    def delete_note(self, note_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM network_notes WHERE org_id = ? AND id = ?", (self.org_id, note_id))

    def save_note(self, note: NetworkNote) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO network_notes(id, org_id, network_id, version, data) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET version = excluded.version, data = excluded.data, "
                "network_id = excluded.network_id",
                (note.id, self.org_id, note.network_id, note.version, note.model_dump_json()),
            )

    def note(self, note_id: str) -> NetworkNote | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT data FROM network_notes WHERE org_id = ? AND id = ?", (self.org_id, note_id)
            ).fetchone()
        return NetworkNote.model_validate_json(r["data"]) if r else None

    def notes(self, network_id: str) -> list[NetworkNote]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT data FROM network_notes WHERE org_id = ? AND network_id = ?", (self.org_id, network_id)
            ).fetchall()
        return [NetworkNote.model_validate_json(r["data"]) for r in rows]

    def move_notes(self, note_ids: list[str], network_id: str) -> None:
        for nid in note_ids:
            note = self.note(nid)
            if note:
                note.network_id = network_id
                self.save_note(note)

    # versioned markdown documents
    def put_markdown(self, kind: MemoryKind, scope: str, markdown: str) -> MemoryDoc:
        with self.db.tx() as c:
            r = c.execute(
                "SELECT MAX(version) AS v FROM memory_docs WHERE org_id = ? AND kind = ? AND scope = ?",
                (self.org_id, kind.value, scope),
            ).fetchone()
            latest = c.execute(
                "SELECT markdown FROM memory_docs WHERE org_id = ? AND kind = ? AND scope = ? "
                "ORDER BY version DESC LIMIT 1",
                (self.org_id, kind.value, scope),
            ).fetchone()
            version = (r["v"] or 0) + 1
            if latest and latest["markdown"] == markdown:
                version -= 1  # unchanged, keep current version
            else:
                c.execute(
                    "INSERT INTO memory_docs(id, org_id, kind, scope, version, markdown, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id("mem"), self.org_id, kind.value, scope, version, markdown, iso()),
                )
        return MemoryDoc(
            id="", org_id=self.org_id, kind=kind, scope=scope, version=version, markdown=markdown
        )

    def get_markdown(self, kind: MemoryKind, scope: str) -> MemoryDoc | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT * FROM memory_docs WHERE org_id = ? AND kind = ? AND scope = ? ORDER BY version DESC LIMIT 1",
                (self.org_id, kind.value, scope),
            ).fetchone()
        return MemoryDoc.model_validate(dict(r)) if r else None


# --- automations ------------------------------------------------------------------------------
class AutomationRepo:
    def __init__(self, db: Database, org_id: str):
        self.db, self.org_id = db, org_id

    def save(self, a: Automation, change_note: str = "", author: str = "builder", new_version: bool = True) -> Automation:
        a.updated_at = utcnow()
        with self.db.tx() as c:
            if new_version:
                r = c.execute(
                    "SELECT MAX(version) AS v FROM automation_versions WHERE automation_id = ?", (a.id,)
                ).fetchone()
                a.version = (r["v"] or 0) + 1
            c.execute(
                "INSERT INTO automations(id, org_id, status, data, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data, "
                "updated_at = excluded.updated_at",
                (a.id, self.org_id, a.status.value, a.model_dump_json(), iso(a.updated_at)),
            )
            if new_version:
                v = AutomationVersion(
                    automation_id=a.id, version=a.version, graph=a.graph, change_note=change_note, author=author
                )
                c.execute(
                    "INSERT INTO automation_versions(automation_id, version, data) VALUES (?, ?, ?)",
                    (a.id, a.version, v.model_dump_json()),
                )
        return a

    def get(self, automation_id: str) -> Automation | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT data FROM automations WHERE org_id = ? AND id = ?", (self.org_id, automation_id)
            ).fetchone()
        return Automation.model_validate_json(r["data"]) if r else None

    def list(self) -> list[Automation]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT data FROM automations WHERE org_id = ? ORDER BY updated_at DESC", (self.org_id,)
            ).fetchall()
        items = [Automation.model_validate_json(r["data"]) for r in rows]
        return sorted(items, key=lambda a: -a.hours_per_week)

    def all_active(self) -> list[Automation]:
        """Across orgs: used by the local scheduler at startup."""
        with self.db.read() as c:
            rows = c.execute("SELECT data FROM automations WHERE status = 'attiva'").fetchall()
        return [Automation.model_validate_json(r["data"]) for r in rows]

    def versions(self, automation_id: str) -> list[AutomationVersion]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT v.data FROM automation_versions v JOIN automations a ON a.id = v.automation_id "
                "WHERE a.org_id = ? AND v.automation_id = ? ORDER BY v.version DESC",
                (self.org_id, automation_id),
            ).fetchall()
        return [AutomationVersion.model_validate_json(r["data"]) for r in rows]

    def delete(self, automation_id: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM automations WHERE org_id = ? AND id = ?", (self.org_id, automation_id))

    def save_run(self, run: AutomationRun) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO automation_runs(id, org_id, automation_id, status, data, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status = excluded.status, "
                "data = excluded.data",
                (run.id, self.org_id, run.automation_id, run.status.value, run.model_dump_json(), iso(run.started_at)),
            )

    def runs(self, automation_id: str, limit: int = 30) -> list[AutomationRun]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT data FROM automation_runs WHERE org_id = ? AND automation_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (self.org_id, automation_id, limit),
            ).fetchall()
        return [AutomationRun.model_validate_json(r["data"]) for r in rows]


# --- chats --------------------------------------------------------------------------------------
class ChatRepo:
    def __init__(self, db: Database, org_id: str):
        self.db, self.org_id = db, org_id

    def create(self, chat: Chat) -> Chat:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO chats(id, org_id, user_id, automation_id, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (chat.id, self.org_id, chat.user_id, chat.automation_id, chat.model_dump_json(), iso(chat.created_at)),
            )
        return chat

    def update(self, chat: Chat) -> None:
        with self.db.tx() as c:
            c.execute(
                "UPDATE chats SET data = ? WHERE org_id = ? AND id = ?", (chat.model_dump_json(), self.org_id, chat.id)
            )

    def get(self, chat_id: str) -> Chat | None:
        with self.db.read() as c:
            r = c.execute("SELECT data FROM chats WHERE org_id = ? AND id = ?", (self.org_id, chat_id)).fetchone()
        return Chat.model_validate_json(r["data"]) if r else None

    def for_automation(self, automation_id: str, user_id: str) -> Chat | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT data FROM chats WHERE org_id = ? AND automation_id = ? AND user_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (self.org_id, automation_id, user_id),
            ).fetchone()
        return Chat.model_validate_json(r["data"]) if r else None

    def add_message(self, msg: ChatMessage) -> ChatMessage:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO chat_messages(id, chat_id, data, created_at) VALUES (?, ?, ?, ?)",
                (msg.id, msg.chat_id, msg.model_dump_json(), iso(msg.created_at)),
            )
        return msg

    def messages(self, chat_id: str, limit: int = 200) -> list[ChatMessage]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT m.data FROM chat_messages m JOIN chats ch ON ch.id = m.chat_id "
                "WHERE ch.org_id = ? AND m.chat_id = ? ORDER BY m.created_at ASC LIMIT ?",
                (self.org_id, chat_id, limit),
            ).fetchall()
        return [ChatMessage.model_validate_json(r["data"]) for r in rows]

    def save_proposal(self, p: Proposal) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO proposals(id, org_id, chat_id, status, data) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET status = excluded.status, data = excluded.data",
                (p.id, self.org_id, p.chat_id, p.status.value, p.model_dump_json()),
            )

    def proposal(self, proposal_id: str) -> Proposal | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT data FROM proposals WHERE org_id = ? AND id = ?", (self.org_id, proposal_id)
            ).fetchone()
        return Proposal.model_validate_json(r["data"]) if r else None

    def pending_proposals(self, chat_id: str) -> list[Proposal]:
        with self.db.read() as c:
            rows = c.execute(
                "SELECT data FROM proposals WHERE org_id = ? AND chat_id = ? AND status = ?",
                (self.org_id, chat_id, ProposalStatus.pending.value),
            ).fetchall()
        return [Proposal.model_validate_json(r["data"]) for r in rows]


# --- document cards -------------------------------------------------------------------------
class CardRepo:
    def __init__(self, db: Database, org_id: str):
        self.db, self.org_id = db, org_id

    def save(self, card: DocumentCard) -> DocumentCard:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO document_cards(document_id, org_id, sha256, version, data, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(document_id) DO UPDATE SET sha256 = excluded.sha256, version = excluded.version, "
                "data = excluded.data, created_at = excluded.created_at",
                (card.document_id, self.org_id, card.sha256, card.version, card.model_dump_json(), iso(card.created_at)),
            )
        return card

    def get(self, document_id: str) -> DocumentCard | None:
        with self.db.read() as c:
            r = c.execute("SELECT data FROM document_cards WHERE org_id = ? AND document_id = ?",
                          (self.org_id, document_id)).fetchone()
        return DocumentCard.model_validate_json(r["data"]) if r else None

    def fresh(self, document_id: str, sha256: str, version: int) -> DocumentCard | None:
        """The cached card if the file and the card format are unchanged."""
        card = self.get(document_id)
        return card if card and card.sha256 == sha256 and card.version == version else None

    def list(self, document_ids: list[str] | None = None) -> list[DocumentCard]:
        with self.db.read() as c:
            rows = c.execute("SELECT data FROM document_cards WHERE org_id = ?", (self.org_id,)).fetchall()
        cards = [DocumentCard.model_validate_json(r["data"]) for r in rows]
        if document_ids is not None:
            order = {d: i for i, d in enumerate(document_ids)}
            cards = sorted((c for c in cards if c.document_id in order), key=lambda c: order[c.document_id])
        return cards


# --- suggestions ------------------------------------------------------------------------------
class SuggestionRepo:
    def __init__(self, db: Database, org_id: str):
        self.db, self.org_id = db, org_id

    def save(self, s: Suggestion) -> Suggestion:
        s.updated_at = utcnow()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO suggestions(id, org_id, kind, status, automation_id, document_ids, data, created_at, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status = excluded.status, "
                "automation_id = excluded.automation_id, document_ids = excluded.document_ids, data = excluded.data, "
                "updated_at = excluded.updated_at",
                (s.id, self.org_id, s.kind.value, s.status.value, s.automation_id, _j(s.document_ids),
                 s.model_dump_json(), iso(s.created_at), iso(s.updated_at)),
            )
        return s

    def get(self, suggestion_id: str) -> Suggestion | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT data FROM suggestions WHERE org_id = ? AND id = ?", (self.org_id, suggestion_id)
            ).fetchone()
        return Suggestion.model_validate_json(r["data"]) if r else None

    def list(self, statuses: tuple[SuggestionStatus, ...] | None = None, limit: int = 200) -> list[Suggestion]:
        q, args = "SELECT data FROM suggestions WHERE org_id = ?", [self.org_id]
        if statuses:
            q += f" AND status IN ({', '.join('?' * len(statuses))})"
            args += [s.value for s in statuses]
        q += " ORDER BY created_at DESC LIMIT ?"
        with self.db.read() as c:
            rows = c.execute(q, (*args, limit)).fetchall()
        return [Suggestion.model_validate_json(r["data"]) for r in rows]

    def count(self, status: SuggestionStatus) -> int:
        with self.db.read() as c:
            r = c.execute(
                "SELECT COUNT(*) AS n FROM suggestions WHERE org_id = ? AND status = ?", (self.org_id, status.value)
            ).fetchone()
        return int(r["n"])

    def set_status(self, suggestion_id: str, status: SuggestionStatus) -> Suggestion | None:
        s = self.get(suggestion_id)
        if s is None:
            return None
        s.status = status
        return self.save(s)

    def find_open(self, kind: SuggestionKind, automation_id: str | None, document_id: str | None) -> Suggestion | None:
        """Dedupe: an open suggestion of the same kind on the same automation and document."""
        for s in self.list(OPEN_STATUSES):
            if s.kind == kind and s.automation_id == automation_id and (
                document_id is None or document_id in s.document_ids
            ):
                return s
        return None


# --- checkpoints + usage ---------------------------------------------------------------------------
class CheckpointRepo:
    def __init__(self, db: Database, org_id: str):
        self.db, self.org_id = db, org_id

    def save(self, ck: AgentCheckpoint) -> None:
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO agent_checkpoints(id, org_id, run_id, agent, step, state, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ck.id, self.org_id, ck.run_id, ck.agent, ck.step, _j(ck.state), iso(ck.created_at)),
            )

    def latest(self, run_id: str, agent: str, step: str) -> AgentCheckpoint | None:
        with self.db.read() as c:
            r = c.execute(
                "SELECT * FROM agent_checkpoints WHERE org_id = ? AND run_id = ? AND agent = ? AND step = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (self.org_id, run_id, agent, step),
            ).fetchone()
        if not r:
            return None
        d = dict(r)
        d["state"] = json.loads(d["state"])
        return AgentCheckpoint.model_validate(d)


class UsageRepo:
    def __init__(self, db: Database):
        self.db = db

    def add(self, org_id: str, user_id: str, model: str, tokens_in: int, tokens_out: int) -> None:
        day = utcnow().date().isoformat()
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO token_usage(org_id, user_id, day, model, tokens_in, tokens_out, calls) "
                "VALUES (?, ?, ?, ?, ?, ?, 1) ON CONFLICT(org_id, user_id, day, model) DO UPDATE SET "
                "tokens_in = token_usage.tokens_in + excluded.tokens_in, "
                "tokens_out = token_usage.tokens_out + excluded.tokens_out, calls = token_usage.calls + 1",
                (org_id, user_id, day, model, tokens_in, tokens_out),
            )

    def summary(self, org_id: str, user_id: str | None = None) -> list[dict[str, Any]]:
        q, args = "SELECT day, model, tokens_in, tokens_out, calls, user_id FROM token_usage WHERE org_id = ?", [org_id]
        if user_id:
            q += " AND user_id = ?"
            args.append(user_id)
        with self.db.read() as c:
            rows = c.execute(q + " ORDER BY day DESC LIMIT 400", args).fetchall()
        return [dict(r) for r in rows]
