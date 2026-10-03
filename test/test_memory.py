"""Harness memory: 30-fact cap with side-fact replacement, note integrity, networks."""

from __future__ import annotations

import pytest

from app.core.knowledge.memory.notes import NoteInvalid
from app.data.models.knowledge import EvidenceRef, FactCandidate, FactDecision, NoteKind
from test.conftest import ORG, login


@pytest.fixture
def memory(services):  # type: ignore[no-untyped-def]
    login(services)
    ks = services.knowledge(ORG)
    ks.add("doc.md", b"# Titolo\n\nOgni sera il capoturno ricopia i dati.", "user_test", True)
    return services.memory(ORG)


async def test_fact_cap_and_side_fact(memory) -> None:  # type: ignore[no-untyped-def]
    for i in range(30):
        out = await memory.profile.add(FactCandidate(text=f"Fatto numero {i} su reparto{i} e macchina{i}", importance=0.3))
        assert out.decision == FactDecision.added
    assert len(memory.repo.facts()) == 30
    weak = await memory.profile.add(FactCandidate(text="Dettaglio poco rilevante su colore pareti", importance=0.2))
    assert weak.decision == FactDecision.rejected
    strong = await memory.profile.add(FactCandidate(text="L'azienda produce pompe centrifughe per l'industria", importance=0.95))
    assert strong.decision == FactDecision.replaced and strong.replaced is not None
    assert len(memory.repo.facts()) == 30  # never above the cap
    assert memory.repo.fact_history()[0]["replaced_by"] == strong.fact.id
    md = memory.profile.markdown()
    assert "30/30" in md and "pompe centrifughe" in md


async def test_duplicate_fact_reinforces(memory) -> None:  # type: ignore[no-untyped-def]
    await memory.profile.add(FactCandidate(text="Il gestionale aziendale è usato per gli ordini", importance=0.5))
    out = await memory.profile.add(FactCandidate(text="Il gestionale aziendale viene usato per gli ordini", importance=0.7))
    assert out.decision == FactDecision.duplicate
    facts = memory.repo.facts()
    assert len(facts) == 1 and facts[0].references == 1 and facts[0].importance == 0.7


async def test_custom_llm_judge_is_used(memory) -> None:  # type: ignore[no-untyped-def]
    for i in range(30):
        await memory.profile.add(FactCandidate(text=f"Fatto {i} tema{i} oggetto{i}", importance=0.9))

    async def judge(candidate, weakest):  # type: ignore[no-untyped-def]
        return True, "più rilevante per l'automazione"

    out = await memory.profile.add(FactCandidate(text="Nuovo fatto chiave sul MES", importance=0.1), judge=judge)
    assert out.decision == FactDecision.replaced


def test_notes_must_be_complete(memory) -> None:  # type: ignore[no-untyped-def]
    doc_id = memory.repo.db and next(iter(memory.networks.validator.known_documents))
    net = memory.networks.create("run_1", "Rete test", [doc_id])
    with pytest.raises(NoteInvalid) as e:
        memory.networks.write_note(net.id, title="Ricopiatura", claim="Il capoturno ricopia i dati della", frequency="ogni sera",
                                   evidence=[EvidenceRef(document_id=doc_id)])
    assert any("truncated" in err for err in e.value.errors)
    with pytest.raises(NoteInvalid):
        memory.networks.write_note(net.id, title="Senza prove", claim="Il capoturno ricopia i dati ogni sera nel file.",
                                   frequency="ogni sera")
    with pytest.raises(NoteInvalid):
        memory.networks.write_note(net.id, title="Doc ignoto", claim="Il capoturno ricopia i dati ogni sera nel file.",
                                   frequency="ogni sera", evidence=[EvidenceRef(document_id="doc_inventato")])
    a = memory.networks.write_note(net.id, title="Ricopiatura serale", claim="Il capoturno ricopia ogni sera i dati di turno nel file.",
                                   frequency="ogni sera", actors=["Produzione"], evidence=[EvidenceRef(document_id=doc_id)],
                                   links=["note_che_non_esiste"])
    assert a.links == []  # dangling links are never stored
    b = memory.networks.write_note(net.id, kind=NoteKind.entity, title="File di turno",
                                   claim="Il file di turno raccoglie pezzi, scarti e fermi.", evidence=[EvidenceRef(document_id=doc_id)])
    memory.networks.link(a.id, b.id)
    rewritten = memory.networks.write_note(net.id, note_id=a.id, title="Ricopiatura serale dati",
                                           claim="Il capoturno ricopia ogni sera pezzi e scarti nel file di turno.",
                                           frequency="ogni sera", evidence=[EvidenceRef(document_id=doc_id)], links=[b.id])
    assert rewritten.version == 2 and rewritten.id == a.id


def test_keep_one_network_per_automation(memory) -> None:  # type: ignore[no-untyped-def]
    doc_id = next(iter(memory.networks.validator.known_documents))
    n1 = memory.networks.create("run_1", "A", [doc_id])
    n2 = memory.networks.create("run_1", "B", [doc_id])
    note = memory.networks.write_note(n1.id, title="Attività", claim="Ogni sera i dati vengono ricopiati a mano.",
                                      frequency="ogni sera", evidence=[EvidenceRef(document_id=doc_id)])
    kept = memory.networks.create("run_1", "Automazione X")
    clones = memory.networks.clone_notes([note.id], kept.id)
    assert clones[0].id != note.id and clones[0].network_id == kept.id
    memory.networks.keep(kept.id, "auto_1")
    assert memory.networks.discard_others("run_1", {kept.id}) == 2
    memory.networks.write_summary("auto_1", kept.id, "# Riepilogo\n\nCollega i dati di turno.")
    ctx = memory.context_for_automation("auto_1", include_notes=True)
    assert "Company profile" in ctx and "Riepilogo" in ctx and "Knowledge network" in ctx
    assert memory.networks.get(n2.id).status.value == "discarded"
