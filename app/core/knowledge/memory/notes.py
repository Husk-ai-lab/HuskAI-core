"""Knowledge networks: atomic markdown notes linked across documents.

Integrity invariant (the harness rule): a note is ALWAYS complete. It is validated before
every save and always rewritten whole, never appended to. A note that fails validation is
not stored; the agent gets the errors back and must rewrite it.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from app.data.models.knowledge import (
    EvidenceRef,
    MemoryKind,
    Network,
    NetworkNote,
    NetworkStatus,
    NetworkSummary,
    NoteKind,
)
from app.gateways.sqlite.repositories import MemoryRepo
from app.helpers.ids import new_id
from app.helpers.text import ends_cleanly
from app.helpers.time import utcnow

_PLACEHOLDER = re.compile(r"(\bTODO\b|\bTBD\b|\.\.\.\s*$|…\s*$|\[\s*…?\s*\]|<[^>]+>|\{\{)", re.I)


class NoteInvalid(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class NoteValidator:
    def __init__(self, known_documents: set[str]):
        self.known_documents = known_documents

    def validate(self, note: NetworkNote) -> list[str]:
        errors: list[str] = []
        claim = note.claim.strip()
        if len(note.title.strip()) < 3:
            errors.append("missing title")
        if len(claim) < 20:
            errors.append("claim too short: write a complete piece of information")
        if not ends_cleanly(claim):
            errors.append("the claim is truncated: it must end with a complete sentence")
        if _PLACEHOLDER.search(claim) or _PLACEHOLDER.search(note.title):
            errors.append("contains placeholders or incomplete text")
        if not note.evidence:
            errors.append("at least one piece of evidence is required (document + location)")
        for e in note.evidence:
            if e.document_id not in self.known_documents:
                errors.append(f"evidence on an unknown document: {e.document_id}")
        if note.kind in (NoteKind.task, NoteKind.handoff, NoteKind.check) and not note.frequency:
            errors.append("state how often the activity happens (e.g. 'every evening', 'at every revision')")
        return errors


class NetworkManager:
    def __init__(self, repo: MemoryRepo, known_documents: set[str]):
        self.repo = repo
        self.validator = NoteValidator(known_documents)

    # networks
    def create(self, run_id: str, label: str, document_ids: list[str] | None = None) -> Network:
        net = Network(
            id=new_id("net"), org_id=self.repo.org_id, run_id=run_id, label=label, document_ids=document_ids or []
        )
        self.repo.save_network(net)
        return net

    def get(self, network_id: str) -> Network | None:
        return self.repo.network(network_id)

    def notes(self, network_id: str) -> list[NetworkNote]:
        return self.repo.notes(network_id)

    # notes
    def write_note(
        self,
        network_id: str,
        *,
        title: str,
        claim: str,
        kind: NoteKind = NoteKind.task,
        frequency: str | None = None,
        actors: list[str] | None = None,
        evidence: list[EvidenceRef] | None = None,
        links: list[str] | None = None,
        hours_per_week: float | None = None,
        note_id: str | None = None,
    ) -> NetworkNote:
        """Create or fully rewrite a note. Raises NoteInvalid with actionable errors."""
        existing = self.repo.note(note_id) if note_id else None
        net = self.repo.network(network_id)
        if net is None:
            raise NoteInvalid([f"network {network_id} does not exist"])
        known_notes = {n.id for n in self.repo.notes(network_id)}
        try:
            note = NetworkNote(
                id=existing.id if existing else new_id("note"),
                org_id=self.repo.org_id,
                network_id=network_id,
                kind=kind,
                title=title.strip(),
                claim=claim.strip(),
                frequency=frequency,
                actors=actors or [],
                evidence=evidence or [],
                # dangling links would make the network "broken": keep only resolvable ones
                links=[lnk for lnk in (links or []) if lnk in known_notes and lnk != (note_id or "")],
                hours_per_week=hours_per_week,
                version=(existing.version + 1) if existing else 1,
                updated_at=utcnow(),
            )
        except ValidationError as exc:  # model constraints (lengths, ranges) are note errors too, never a crash
            raise NoteInvalid([e["msg"] for e in exc.errors()]) from None
        errors = self.validator.validate(note)
        if errors:
            raise NoteInvalid(errors)
        self.repo.save_note(note)
        for e in note.evidence:
            if e.document_id not in net.document_ids:
                net.document_ids.append(e.document_id)
        self.repo.save_network(net)
        return note

    def link(self, a: str, b: str) -> None:
        na, nb = self.repo.note(a), self.repo.note(b)
        if not na or not nb:
            return
        if b not in na.links:
            na.links.append(b)
            self.repo.save_note(na)
        if a not in nb.links:
            nb.links.append(a)
            self.repo.save_note(nb)

    def clone_notes(self, note_ids: list[str], target_network_id: str) -> list[NetworkNote]:
        """Copy already-valid notes into another network, remapping links among them."""
        originals = [n for n in (self.repo.note(i) for i in note_ids) if n]
        id_map = {n.id: new_id("note") for n in originals}
        net = self.repo.network(target_network_id)
        out = []
        for n in originals:
            clone = n.model_copy(deep=True)
            clone.id = id_map[n.id]
            clone.network_id = target_network_id
            clone.links = [id_map[lnk] for lnk in n.links if lnk in id_map]
            clone.version = 1
            self.repo.save_note(clone)
            out.append(clone)
            if net:
                for e in clone.evidence:
                    if e.document_id not in net.document_ids:
                        net.document_ids.append(e.document_id)
        if net:
            self.repo.save_network(net)
        return out

    # lifecycle
    def keep(self, network_id: str, automation_id: str) -> Network:
        net = self.repo.network(network_id)
        if net is None:
            raise KeyError(network_id)
        net.status = NetworkStatus.kept
        net.automation_id = automation_id
        self.repo.save_network(net)
        self.repo.put_markdown(MemoryKind.network_notes, automation_id, self.render(net))
        return net

    def discard_others(self, run_id: str, keep_ids: set[str]) -> int:
        n = 0
        for net in self.repo.networks(run_id=run_id):
            if net.id not in keep_ids and net.status == NetworkStatus.candidate:
                net.status = NetworkStatus.discarded
                self.repo.save_network(net)
                n += 1
        return n

    def render(self, net: Network, lang: str = "en") -> str:
        from app.i18n import t

        notes = self.repo.notes(net.id)
        head = [f"# {t('memory.network_heading', lang, label=net.label)}", "",
                f"_{t('memory.network_meta', lang, count=len(notes), documents=', '.join(net.document_ids))}_", ""]
        return "\n".join(head) + "\n".join(n.render_markdown(lang) for n in notes)

    def write_summary(self, automation_id: str, network_id: str, markdown: str) -> NetworkSummary:
        doc = self.repo.put_markdown(MemoryKind.network_summary, automation_id, markdown)
        return NetworkSummary(
            org_id=self.repo.org_id,
            automation_id=automation_id,
            network_id=network_id,
            markdown=markdown,
            version=doc.version,
        )

    def summary(self, automation_id: str) -> str | None:
        doc = self.repo.get_markdown(MemoryKind.network_summary, automation_id)
        return doc.markdown if doc else None
