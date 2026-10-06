"""A document's language (English or Italian, English when unsure) decides the language of its summary."""

from __future__ import annotations

import pytest

from app.core.agents.explorer.summarizer import Summarizer
from app.core.knowledge.text_analysis import detect_language
from app.data.models.knowledge import CARD_VERSION, CardKind
from app.gateways.sqlite.repositories import CardRepo
from test.conftest import ORG, PROCEDURE_MD, USER, login, production_csv
from test.fakes import FakeProvider, default_handler

IT = ("Ogni mattina il magazzino controlla i documenti di trasporto del fornitore e ricopia a mano i dati nel gestionale. "
      "La qualità verifica i certificati dei lotti e, se manca il certificato, il lotto viene bloccato.")
EN = ("Every morning the warehouse checks the supplier delivery notes and copies the data by hand into the ERP. "
      "Quality verifies the batch certificates and, if a certificate is missing, the batch is blocked.")


@pytest.mark.parametrize(("text", "expected"), [
    (IT, "it"), (EN, "en"),
    (PROCEDURE_MD, "it"),                       # the Italian fixture the whole suite uses
    ("", "en"), ("Report 2026", "en"),          # too short to say
    ("Data,Turno,Pezzi,Scarti,Fermo\n" + "2026-09-01,A,401,1,7\n" * 30, "en"),  # a spreadsheet of numbers
    ("Il report è pronto. " + EN, "en"),        # mostly English with one Italian sentence
    ("The shift is done. " + IT, "it"),         # mostly Italian with one English sentence
])
def test_detect_language(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_only_english_and_italian_and_unsure_means_english() -> None:
    french = "Chaque matin, le magasin vérifie les bons de livraison du fournisseur et recopie les données à la main."
    assert detect_language(french * 3) in ("en", "it")  # never anything else
    assert detect_language("x " * 40) == "en"


def test_ingest_stores_the_language(services) -> None:  # type: ignore[no-untyped-def]
    ks = services.knowledge(ORG)
    assert ks.add("Procedura.md", IT.encode(), USER, True).language == "it"
    assert ks.add("Procedure.md", EN.encode(), USER, True).language == "en"
    assert ks.add("Report.csv", production_csv(), USER, True).language == "en"  # numbers: English by default


async def test_old_documents_without_a_language_are_detected_when_summarised(services) -> None:  # type: ignore[no-untyped-def]
    login(services)
    seen: list[str] = []

    def recording(messages, tools):  # type: ignore[no-untyped-def]
        seen.append(str(messages[0].content))
        return default_handler(messages, tools)

    services.llm = FakeProvider(recording)
    doc = services.knowledge(ORG).add("Procedura.md", IT.encode(), USER, True)
    with services.db.tx() as c:  # as if it had been ingested before the language column existed
        c.execute("UPDATE documents SET language = NULL WHERE id = ?", (doc.id,))
    doc = services.knowledge(ORG).docs.get(doc.id)
    assert doc.language is None
    await Summarizer(services.agent_context(ORG, USER, language="en")).text_card(doc)
    assert "`sentence` and `summary` in Italian" in seen[-1]


async def test_spreadsheet_cards_use_the_documents_language(services) -> None:  # type: ignore[no-untyped-def]
    login(services)
    rows = ["Attività,Descrizione"] + [f"Controllo {i},Ogni giorno il capoturno verifica che i dati siano corretti e li registra nel gestionale" for i in range(25)]
    it_sheet = services.knowledge(ORG).add("Controlli.csv", "\n".join(rows).encode(), USER, True)
    assert it_sheet.language == "it"
    card, _ = await Summarizer(services.agent_context(ORG, USER, language="en")).card_for(it_sheet)
    assert card.kind == CardKind.table and "righe" in card.sentence  # Italian labels for an Italian sheet, English UI
    num_sheet = services.knowledge(ORG).add("Report.csv", production_csv(), USER, True)
    card, _ = await Summarizer(services.agent_context(ORG, USER, language="it")).card_for(num_sheet)
    assert "rows" in card.sentence  # numbers only: English by default, even with an Italian UI


async def test_cards_built_before_the_language_rule_are_rebuilt(services) -> None:  # type: ignore[no-untyped-def]
    login(services)
    doc = services.knowledge(ORG).add("Procedura.md", IT.encode(), USER, True)
    s = Summarizer(services.agent_context(ORG, USER, language="en"))
    card, built = await s.card_for(doc)
    assert built and card.version == CARD_VERSION == 3
    repo = CardRepo(services.db, ORG)
    assert repo.fresh(doc.id, doc.sha256, CARD_VERSION) is not None
    assert repo.fresh(doc.id, doc.sha256, 2) is None  # a version-2 card (summary in the UI language) is not reused
