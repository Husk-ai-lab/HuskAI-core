"""Synthetic, fictional manufacturing company for benchmarks and demos.

"Officine Esempio S.r.l." — pumps and valves, ~60 employees. Every document is generated
from a seed, in Italian, with PLANTED repetitive tasks whose true weekly cost is known
(ground_truth.json). No real company data, no personal data (GDPR guide §5).

    uv run python -m benchmarks.datasets.generate_synthetic --out benchmarks/datasets/generated --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

DEPARTMENTS = ["Ufficio tecnico", "Acquisti", "Qualità", "Produzione", "Magazzino", "Stampaggio", "Commerciale"]
SUPPLIERS = ["Fonderia Nord", "Viteria Sud", "Gomme Tecniche", "Acciai Speciali", "Elettromeccanica Est", None]
PRODUCTS = ["PX200", "PX300", "VL50", "VL80", "PM12"]


@dataclass
class PlantedTask:
    id: str
    title: str
    keywords: list[str]
    documents: list[str]
    occurrences_per_week: float
    minutes_per_occurrence: float
    departments: list[str]
    automatable: bool = True

    @property
    def hours_per_week(self) -> float:
        return round(self.occurrences_per_week * self.minutes_per_occurrence / 60, 2)


@dataclass
class GroundTruth:
    company: str
    seed: int
    tasks: list[PlantedTask] = field(default_factory=list)
    automatable_documents: list[str] = field(default_factory=list)
    noise_documents: list[str] = field(default_factory=list)
    queries: list[dict] = field(default_factory=list)  # {query, relevant: [filenames]}

    def to_json(self) -> str:
        d = asdict(self)
        for t, src in zip(d["tasks"], self.tasks, strict=True):
            t["hours_per_week"] = src.hours_per_week
        d["total_hours_per_week"] = round(sum(t.hours_per_week for t in self.tasks if t.automatable), 2)
        return json.dumps(d, ensure_ascii=False, indent=2)


def bom(rng: random.Random, product: str, rev: str, n: int, missing_rate: float) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "Codice": f"{product}-{i:03d}",
            "Descrizione": rng.choice(["Corpo", "Tenuta meccanica", "Vite M8", "Supporto motore", "Girante", "O-ring", "Flangia"]) + f" {i}",
            "Quantità": rng.choice([1, 1, 2, 4, 8, 12, 16]),
            "Fornitore preferenziale": None if rng.random() < missing_rate else rng.choice(SUPPLIERS[:-1]),
            "Peso unitario kg": None if rng.random() < missing_rate / 2 else round(rng.uniform(0.01, 15), 2),
            "Revisione": rev,
        })
    return pd.DataFrame(rows)


def production(rng: random.Random, start: date, days: int) -> pd.DataFrame:
    rows = []
    for d in range(days):
        day = start + timedelta(days=d)
        if day.weekday() >= 5:
            continue
        for turno in ("A", "B"):
            pezzi = rng.randint(350, 520)
            rows.append({"Data": day.isoformat(), "Turno": turno, "Linea": rng.choice(["L1", "L2"]), "Pezzi": pezzi,
                         "Scarti": rng.randint(0, 25), "Fermo minuti": rng.choice([0, 0, 5, 12, 30, 45]),
                         "Causa fermo": rng.choice(["", "", "Cambio stampo", "Mancanza materiale", "Guasto"])})
    return pd.DataFrame(rows)


def orders(rng: random.Random, n: int) -> pd.DataFrame:
    start = date(2026, 8, 3)
    return pd.DataFrame([{
        "Numero ordine": f"OV-2026-{1000 + i}", "Data": (start + timedelta(days=i // 3)).isoformat(),
        "Cliente": f"Cliente {rng.randint(1, 40):02d}", "Prodotto": rng.choice(PRODUCTS), "Quantità": rng.randint(5, 400),
        "Data consegna richiesta": (start + timedelta(days=i // 3 + rng.randint(10, 40))).isoformat(),
        "Inserito nel gestionale": rng.choice(["sì", "sì", "no"]),
    } for i in range(n)])


PROCEDURE = """# {title}

Revisione {rev} · Reparto {dept}

## Scopo
{scope}

## Attività
{steps}

## Responsabilità
{resp}
"""

NOISE = """# {title}

## Premessa
{body}

## Note
Il presente documento descrive principi generali e non contiene istruzioni operative ricorrenti.
"""


def generate(out: Path, seed: int = 7, scale: int = 1) -> GroundTruth:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    gt = GroundTruth(company="Officine Esempio S.r.l. (fittizia)", seed=seed)

    # 1. BOM revisions copied by hand into the ERP
    for p in PRODUCTS[: 2 + scale]:
        for rev in ("B", "C"):
            f = f"Distinta_Base_{p}_Rev{rev}.xlsx"
            bom(rng, p, rev, 40 * scale, 0.18 if rev == "C" else 0.05).to_excel(out / f, index=False)
            gt.automatable_documents.append(f)
    bom_files = [f for f in gt.automatable_documents if f.startswith("Distinta")]
    gt.tasks.append(PlantedTask("bom_copy", "Ricopiatura delle revisioni di distinta base nel gestionale",
                                ["distinta", "revisione", "gestionale", "ricopi"], bom_files + ["Procedura_Gestione_Distinte_Base.md"],
                                occurrences_per_week=3 * scale, minutes_per_occurrence=75, departments=["Ufficio tecnico"]))
    (out / "Procedura_Gestione_Distinte_Base.md").write_text(PROCEDURE.format(
        title="Procedura di gestione delle distinte base", rev="04", dept="Ufficio tecnico",
        scope="Definire come una nuova revisione di distinta base viene verificata e registrata.",
        steps="- A ogni nuova revisione l'ufficio tecnico confronta a mano la distinta con la revisione precedente.\n"
              "- Le righe modificate vengono ricopiate a mano nel gestionale, articolo per articolo.\n"
              "- Se mancano il fornitore preferenziale o il peso unitario, si invia una email all'ufficio acquisti.\n"
              "- Il controllo richiede in media più di un'ora per revisione.",
        resp="Ufficio tecnico (esecuzione), Acquisti (completamento dati fornitore)."), "utf-8")
    gt.automatable_documents.append("Procedura_Gestione_Distinte_Base.md")

    # 2. Daily production report pasted every evening
    f = "Report_Produzione_Settembre.csv"
    production(rng, date(2026, 9, 1), 28 * scale).to_csv(out / f, index=False)
    (out / "Istruzione_Report_Fine_Turno.md").write_text(PROCEDURE.format(
        title="Istruzione operativa report di fine turno", rev="02", dept="Produzione",
        scope="Raccogliere ogni sera i dati di turno e calcolare l'OEE.",
        steps="- Ogni sera il capoturno copia pezzi, scarti e fermi dal MES nel foglio Excel.\n"
              "- Calcola l'OEE a mano e, se è sotto il 75%, avvisa il responsabile di produzione.\n"
              "- Il foglio viene stampato e archiviato in PDF.",
        resp="Capoturno (compilazione), Responsabile di produzione (verifica)."), "utf-8")
    gt.automatable_documents += [f, "Istruzione_Report_Fine_Turno.md"]
    gt.tasks.append(PlantedTask("shift_report", "Compilazione serale del report di produzione e calcolo OEE",
                                ["report", "turno", "oee", "scarti", "fermi"], [f, "Istruzione_Report_Fine_Turno.md"],
                                occurrences_per_week=10, minutes_per_occurrence=25, departments=["Produzione"]))

    # 3. Orders re-typed into the ERP
    f = "Ordini_Clienti_Agosto_Settembre.csv"
    orders(rng, 120 * scale).to_csv(out / f, index=False)
    (out / "Procedura_Inserimento_Ordini.md").write_text(PROCEDURE.format(
        title="Procedura inserimento ordini clienti", rev="01", dept="Commerciale",
        scope="Inserire gli ordini ricevuti via email nel gestionale.",
        steps="- Ogni mattina il commerciale legge le email degli ordini e reinserisce i dati nel gestionale.\n"
              "- Controlla che la data di consegna sia compatibile con il piano di produzione.\n"
              "- Gli ordini non ancora inseriti restano segnati con 'no' nel file di appoggio.",
        resp="Ufficio commerciale."), "utf-8")
    gt.automatable_documents += [f, "Procedura_Inserimento_Ordini.md"]
    gt.tasks.append(PlantedTask("order_entry", "Reinserimento manuale degli ordini clienti nel gestionale",
                                ["ordini", "gestionale", "reinser", "email"], [f, "Procedura_Inserimento_Ordini.md"],
                                occurrences_per_week=40 * scale, minutes_per_occurrence=6, departments=["Commerciale"]))

    # 4. Incoming material checks
    (out / "Procedura_Accettazione_Materiali.md").write_text(PROCEDURE.format(
        title="Procedura di accettazione materiali", rev="04", dept="Qualità",
        scope="Verificare i materiali in ingresso.",
        steps="- Ogni mattina il magazzino controlla i DDT del fornitore e ricopia i dati nel registro.\n"
              "- La qualità verifica i certificati dei lotti; se il certificato manca il lotto viene bloccato.\n"
              "- Ogni settimana si compila a mano il registro delle non conformità.",
        resp="Magazzino, Qualità."), "utf-8")
    gt.automatable_documents.append("Procedura_Accettazione_Materiali.md")
    gt.tasks.append(PlantedTask("incoming_check", "Registrazione DDT e controllo certificati in accettazione",
                                ["ddt", "certificat", "accettazione", "non conformità"], ["Procedura_Accettazione_Materiali.md"],
                                occurrences_per_week=5, minutes_per_occurrence=40, departments=["Magazzino", "Qualità"]))

    # 5. Noise: documents with no repetitive work
    for i, (t, b) in enumerate([
        ("Politica per la qualità", "L'azienda si impegna al miglioramento continuo e alla soddisfazione del cliente."),
        ("Codice etico", "I principi di correttezza e trasparenza guidano i rapporti con clienti e fornitori."),
        ("Storia aziendale", "Fondata come officina meccanica, l'azienda produce oggi pompe e valvole industriali."),
    ]):
        f = f"Documento_Generale_{i + 1}.md"
        (out / f).write_text(NOISE.format(title=t, body=b), "utf-8")
        gt.noise_documents.append(f)

    gt.queries = [
        {"query": "fornitore preferenziale mancante distinta", "relevant": ["Procedura_Gestione_Distinte_Base.md"] + bom_files},
        {"query": "calcolo OEE fine turno", "relevant": ["Istruzione_Report_Fine_Turno.md"]},
        {"query": "certificato lotto bloccato", "relevant": ["Procedura_Accettazione_Materiali.md"]},
        {"query": "ordini email gestionale", "relevant": ["Procedura_Inserimento_Ordini.md", "Ordini_Clienti_Agosto_Settembre.csv"]},
        {"query": "causa fermo cambio stampo", "relevant": ["Report_Produzione_Settembre.csv"]},
        {"query": "registro non conformità settimanale", "relevant": ["Procedura_Accettazione_Materiali.md"]},
        {"query": "principi di correttezza e trasparenza", "relevant": ["Documento_Generale_2.md"]},
        {"query": "revisione C girante", "relevant": [f for f in bom_files if "RevC" in f]},
    ]
    (out / "ground_truth.json").write_text(gt.to_json(), "utf-8")
    return gt


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).parent / "generated"))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--scale", type=int, default=1)
    a = ap.parse_args()
    g = generate(Path(a.out), a.seed, a.scale)
    print(f"generated {len(list(Path(a.out).glob('*'))) - 1} documents; planted {sum(t.hours_per_week for t in g.tasks):.1f} h/week")
