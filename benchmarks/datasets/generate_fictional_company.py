"""Hand-written fictional company for demos, manual QA and end-to-end benchmarks.

"Officine Valsorda S.r.l.": precision-turned brass fittings and ball valves, 86 employees,
fictional town Borgo Valsorda (BS). Unlike `generate_synthetic.py` (procedural, many seeds),
this is ONE coherent company: a full profile, cross-referencing documents in every format the
ingest accepts (docx, xlsx, csv, pptx, pdf, md, txt, sqlite), noise documents, and PLANTED
repetitive tasks and data defects with known answers (ground_truth.json).

Everything is invented. People, companies, addresses, VAT numbers and `.example` domains are
fictional (GDPR guide §5). Output is git-ignored.

    uv run python -m benchmarks.datasets.generate_fictional_company
    uv run python -m benchmarks.datasets.generate_fictional_company --out /tmp/valsorda --seed 3
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import textwrap
from datetime import date, timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from docx import Document  # noqa: E402
from matplotlib.backends.backend_pdf import PdfPages  # noqa: E402
from pptx import Presentation  # noqa: E402
from pptx.util import Pt  # noqa: E402

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "fictional_company"
TODAY = date(2026, 10, 2)  # "now" inside the fiction: Friday of week 40

COMPANY = {
    "name": "Officine Valsorda S.r.l.",
    "short": "Valsorda",
    "address": "Via dell'Artigianato 14, 25099 Borgo Valsorda (BS), Italia (indirizzo fittizio)",
    "vat": "IT00000000000 (fittizia)",
    "founded": 1987,
    "employees": 86,
    "revenue_2025_eur": 14_600_000,
    "domain": "valsorda.example",
    "erp": "gestionale 'Gestio 7' (on-premise, export solo CSV/Excel)",
}

PEOPLE = {  # fictional; role -> (name, initials used in documents)
    "ceo": ("Giovanna Rinaldi", "GR"),
    "cfo": ("Paolo Ferretti", "PF"),
    "plant": ("Marco Belotti", "MB"),
    "quality": ("Elisa Cattaneo", "EC"),
    "purchasing": ("Stefano Guerini", "SG"),
    "sales": ("Laura Moretti", "LM"),
    "admin": ("Chiara Zanetti", "CZ"),
    "warehouse": ("Davide Lancini", "DL"),
    "maintenance": ("Roberto Pasini", "RP"),
    "planning": ("Francesca Bonomi", "FB"),
    "hr": ("Anna Gatti", "AG"),
    "it": ("Luca Tedoldi", "LT"),
}


def person(role: str) -> str:
    return PEOPLE[role][0]


def mail(role: str) -> str:
    first, last = PEOPLE[role][0].lower().split(" ", 1)
    return f"{first[0]}.{last.replace(' ', '')}@{COMPANY['domain']}"


SUPPLIERS = [
    ("F001", "Ottoni Pedemontani S.p.A.", "barre ottone CW617N", 21, "ordini@ottonipedemontani.example"),
    ("F002", "Acciai Trafilati Serio S.r.l.", "barre inox AISI 303", 28, "vendite@acciaiserio.example"),
    ("F003", "Guarnizioni Tecniche Adda S.r.l.", "O-ring NBR/EPDM, sedi PTFE", 14, "info@gtadda.example"),
    ("F004", "Trattamenti Galvanici Sebino S.n.c.", "nichelatura conto lavoro", 7, "lab@galvsebino.example"),
    ("F005", "Utensileria Benaco S.r.l.", "inserti e utensili CNC", 5, "ordini@utbenaco.example"),
    ("F006", "Imballaggi Franciacorta S.r.l.", "scatole, sacchetti, pallet", 10, "commerciale@imbfc.example"),
    ("F007", "Lubrificanti Industriali Mella S.r.l.", "emulsioni e oli da taglio", 7, "ordini@lubmella.example"),
    ("F008", "Trasporti Camuni S.r.l.", "trasporti in uscita", 1, "traffico@trcamuni.example"),
]

CUSTOMERS = [
    ("C001", "Idrotecnica Lario S.p.A.", "IT", 60, "acquisti@idrolario.example"),
    ("C002", "Caldaie Orobiche S.r.l.", "IT", 90, "supply@caldaieorobiche.example"),
    ("C003", "Impianti Termici Veneti S.r.l.", "IT", 60, "ordini@itveneti.example"),
    ("C004", "AquaSysteme Nordwerk GmbH", "DE", 30, "einkauf@aquanordwerk.example"),
    ("C005", "Pneumatica Tirrena S.r.l.", "IT", 90, "acquisti@pntirrena.example"),
    ("C006", "Fluidotecnica Adriatica S.p.A.", "IT", 60, "procurement@fluidoadriatica.example"),
    ("C007", "Irrigazione Padana Soc. Coop.", "IT", 120, "amministrazione@irrpadana.example"),
]

# code, description, material code, grams of bar per piece, list price (EUR, 1-499 pcs)
PRODUCTS = [
    ("RF-12", "Raccordo filettato M-F 1/2\"", "MP-OT20", 85, 1.42),
    ("RF-22", "Raccordo filettato M-M 3/4\"", "MP-OT30", 140, 2.05),
    ("RF-34", "Raccordo filettato M-F 3/4\" con O-ring", "MP-OT30", 155, 2.48),
    ("NP-08", "Nipplo ridotto 3/8\"-1/4\"", "MP-OT20", 40, 0.71),
    ("MN-05", "Manicotto F-F 1/2\"", "MP-OT20", 70, 1.15),
    ("VS-10", "Valvola a sfera 1/2\" PN30 leva", "MP-OT40", 260, 6.90),
    ("VS-20", "Valvola a sfera 1\" PN25 farfalla", "MP-OT40", 520, 11.80),
    ("VX-15", "Valvola a sfera inox 1/2\" per alimentare", "MP-IX30", 300, 18.40),
]

MATERIALS = [  # code, description, unit, min stock, reorder lot, supplier, unit cost
    ("MP-OT20", "Barra ottone CW617N esagonale 20 mm", "kg", 2500, 4000, "F001", 6.85),
    ("MP-OT30", "Barra ottone CW617N esagonale 30 mm", "kg", 3500, 6000, "F001", 6.80),
    ("MP-OT40", "Barra ottone CW617N tonda 40 mm", "kg", 3000, 5000, "F001", 6.78),
    ("MP-IX30", "Barra inox AISI 303 tonda 30 mm", "kg", 800, 1500, "F002", 4.95),
    ("CO-OR12", "O-ring NBR 70 Sh 12x2", "pz", 20000, 50000, "F003", 0.018),
    ("CO-OR20", "O-ring EPDM 20x2,5", "pz", 15000, 40000, "F003", 0.026),
    ("CO-SF10", "Sede sfera PTFE 1/2\"", "pz", 8000, 20000, "F003", 0.11),
    ("CO-SF20", "Sede sfera PTFE 1\"", "pz", 4000, 10000, "F003", 0.19),
    ("CO-LV10", "Leva acciaio verniciata rossa 1/2\"", "pz", 6000, 15000, "F003", 0.32),
    ("UT-INS1", "Inserto tornitura VCGT 160404", "pz", 200, 500, "F005", 7.40),
    ("UT-PUN6", "Punta HSS-Co 6,8 mm", "pz", 50, 100, "F005", 5.10),
    ("IM-SC01", "Scatola cartone 300x200x150", "pz", 1500, 3000, "F006", 0.62),
    ("IM-SAC1", "Sacchetto PE 150x250", "pz", 10000, 25000, "F006", 0.021),
    ("LU-EM05", "Emulsione da taglio semisintetica", "l", 400, 1000, "F007", 3.20),
]

MACHINES = [
    ("TP-1", "Plurimandrino 6 mandrini (2009)", "Reparto tornitura"),
    ("TP-2", "Plurimandrino 8 mandrini (2018)", "Reparto tornitura"),
    ("CL-1", "Centro di lavoro 4 assi (2015)", "Reparto fresatura"),
    ("TR-1", "Transfer rotativo 12 stazioni (2021)", "Reparto valvole"),
    ("LV-1", "Lavatrice a solvente modificato", "Reparto finitura"),
    ("BC-1", "Banco collaudo tenuta ad aria", "Reparto valvole"),
]

OPERATORS = [f"OP{n:02d}" for n in range(1, 25)]  # badge codes only, never names in production data


# ---------------------------------------------------------------- helpers (writers)
def working_days(start: date, end: date) -> list[date]:
    d, out = start, []
    while d <= end:
        if d.weekday() < 5 and d not in (date(2026, 8, 14), date(2026, 8, 17)):
            out.append(d)
        d += timedelta(days=1)
    return out


def write_docx(path: Path, title: str, blocks: list[tuple[str, object]]) -> None:
    """blocks: ("h", text) heading · ("p", text) paragraph · ("ul", [items]) · ("table", [[row]...])."""
    doc = Document()
    doc.add_heading(title, level=0)
    for kind, content in blocks:
        if kind == "h":
            doc.add_heading(str(content), level=1)
        elif kind == "h2":
            doc.add_heading(str(content), level=2)
        elif kind == "p":
            doc.add_paragraph(str(content))
        elif kind == "ul":
            for item in content:  # type: ignore[union-attr]
                doc.add_paragraph(str(item), style="List Bullet")
        elif kind == "ol":
            for item in content:  # type: ignore[union-attr]
                doc.add_paragraph(str(item), style="List Number")
        elif kind == "table":
            rows = list(content)  # type: ignore[arg-type]
            table = doc.add_table(rows=len(rows), cols=len(rows[0]))
            table.style = "Light Grid Accent 1"
            for r, row in enumerate(rows):
                for c, cell in enumerate(row):
                    table.cell(r, c).text = str(cell)
    doc.save(path)


def write_pdf(path: Path, title: str, sections: list[tuple[str, str]]) -> None:
    """Text-only A4 PDF via matplotlib (no extra dependency). Text is extractable by MarkItDown."""
    lines: list[tuple[str, str]] = [("title", title), ("blank", "")]
    for heading, body in sections:
        lines.append(("h", heading))
        for para in body.strip().split("\n\n"):
            for ln in textwrap.wrap(" ".join(para.split()), 92):
                lines.append(("p", ln))
            lines.append(("blank", ""))
    per_page = 52
    with PdfPages(path) as pdf:
        for page_no, start in enumerate(range(0, len(lines), per_page), start=1):
            fig = plt.figure(figsize=(8.27, 11.69))
            y = 0.95
            for kind, text in lines[start:start + per_page]:
                size, weight = {"title": (15, "bold"), "h": (11.5, "bold")}.get(kind, (9.5, "normal"))
                fig.text(0.08, y, text, fontsize=size, fontweight=weight, family="DejaVu Sans", va="top")
                y -= 0.017 if kind != "title" else 0.03
            fig.text(0.08, 0.03, f"{COMPANY['name']} - documento fittizio - pag. {page_no}", fontsize=7.5,
                     color="#777777")
            pdf.savefig(fig)
            plt.close(fig)


def write_pptx(path: Path, slides: list[tuple[str, list[str]]]) -> None:
    prs = Presentation()
    for i, (title, bullets) in enumerate(slides):
        slide = prs.slides.add_slide(prs.slide_layouts[0 if i == 0 else 1])
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.text = bullets[0] if bullets else ""
        for b in bullets[1:]:
            p = body.add_paragraph()
            p.text = b
            p.font.size = Pt(18)
    prs.save(path)


def write_text(path: Path, text: str) -> None:
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------- generator
class Gen:
    def __init__(self, out: Path, seed: int) -> None:
        self.out = out
        self.docs = out / "documents"
        self.rng = random.Random(seed)
        self.seed = seed
        self.truth: dict = {"company": COMPANY["name"], "seed": seed, "as_of": TODAY.isoformat(),
                            "tasks": [], "data_defects": {}, "noise_documents": [], "queries": []}

    def path(self, rel: str) -> Path:
        p = self.docs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def task(self, tid: str, title: str, per_week: float, minutes: float, docs: list[str], departments: list[str],
             automatable: bool = True, notes: str = "") -> None:
        self.truth["tasks"].append({
            "id": tid, "title": title, "occurrences_per_week": per_week, "minutes_per_occurrence": minutes,
            "hours_per_week": round(per_week * minutes / 60, 2), "documents": docs, "departments": departments,
            "automatable": automatable, "notes": notes,
        })

    # ------------------------------------------------------------ direzione
    def profile(self) -> None:
        c = COMPANY
        write_text(self.path("direzione/Profilo_aziendale.md"), f"""
        # {c['name']} - Profilo aziendale

        > Documento interno, aggiornato a settembre 2026. Tutti i dati sono fittizi.

        ## Chi siamo
        {c['name']} e' un'azienda familiare fondata nel {c['founded']} a Borgo Valsorda, in provincia di
        Brescia. Produciamo raccordi filettati e valvole a sfera in ottone e acciaio inox, torniti da barra,
        per l'idrotermosanitario, il riscaldamento, l'irrigazione e (dal 2024) l'industria alimentare.

        | Dato | Valore |
        |---|---|
        | Ragione sociale | {c['name']} |
        | Sede | {c['address']} |
        | Partita IVA | {c['vat']} |
        | Dipendenti | {c['employees']} (62 produzione, 9 ufficio tecnico e qualita', 15 uffici) |
        | Fatturato 2025 | 14,6 milioni di euro (+6% sul 2024), export 31% |
        | Turni | 3 turni su 5 giorni in tornitura, 2 turni negli altri reparti |
        | Certificazioni | ISO 9001:2015, ISO 14001:2015, WRAS per VX-15 in corso |
        | Gestionale | {c['erp']} |
        | Superficie | 9.800 m2 coperti |

        ## Prodotti
        - **Raccordi filettati** RF-12, RF-22, RF-34, nippli NP-08, manicotti MN-05 (circa 70% dei volumi).
        - **Valvole a sfera** VS-10 e VS-20 in ottone nichelato, VX-15 in inox per alimentare.
        - Lavorazioni conto terzi su disegno (circa 8% del fatturato).

        ## Clienti principali
        Idrotecnica Lario (24% del fatturato), Caldaie Orobiche (17%), AquaSysteme Nordwerk (15%, Germania),
        Impianti Termici Veneti, Fluidotecnica Adriatica, Pneumatica Tirrena, Irrigazione Padana.

        ## Fornitori chiave
        Ottoni Pedemontani (barre ottone, circa 60% degli acquisti), Acciai Trafilati Serio (inox),
        Guarnizioni Tecniche Adda (O-ring e sedi PTFE), Trattamenti Galvanici Sebino (nichelatura).

        ## Reparti
        Tornitura (TP-1, TP-2), fresatura (CL-1), valvole (TR-1, BC-1), finitura (LV-1), magazzino,
        qualita', ufficio tecnico, acquisti, commerciale, amministrazione, risorse umane.

        ## Obiettivi 2026-2027
        1. Ridurre i ritardi di consegna sotto il 5% delle righe ordine (oggi circa 14%).
        2. Ridurre gli scarti di tornitura dal 3,1% al 2%.
        3. Liberare tempo negli uffici: amministrazione e acquisti lavorano in media 6 ore di straordinario
           a settimana per attivita' ripetitive (controlli fatture, riordini, solleciti).
        4. Ottenere la certificazione WRAS per la linea inox entro giugno 2027.

        ## Vincoli
        - Il gestionale non ha API: si esporta in CSV/Excel e si reimporta a mano.
        - I dati di produzione restano in azienda; nessun dato su cloud senza approvazione della direzione.
        - Il personale di reparto compila i rapporti di turno su carta, poi trascritti in Excel.
        """)
        write_text(self.path("direzione/Organigramma.md"), f"""
        # Organigramma {COMPANY['name']} (settembre 2026)

        - **Amministratrice delegata**: {person('ceo')}
          - **Direttore amministrativo e finanziario**: {person('cfo')}
            - Contabilita' e ciclo passivo: {person('admin')} + 2 impiegate
            - Risorse umane e paghe: {person('hr')}
            - IT (part-time, anche sistemi di stabilimento): {person('it')}
          - **Direttore di stabilimento**: {person('plant')}
            - Pianificazione produzione: {person('planning')}
            - Capi turno tornitura (3), capo reparto valvole, capo reparto finitura
            - Manutenzione: {person('maintenance')} + 2 manutentori
            - Magazzino e spedizioni: {person('warehouse')} + 3 magazzinieri
          - **Responsabile qualita' e ambiente**: {person('quality')} + 2 tecnici di laboratorio
          - **Responsabile acquisti**: {person('purchasing')}
          - **Responsabile commerciale**: {person('sales')} + 2 back office (Italia, estero)

        Contatti interni nel formato iniziale.cognome@{COMPANY['domain']}.
        """)

    def board_minutes(self) -> None:
        rel = "direzione/Verbale_riunione_direzione_2026-09-21.docx"
        write_docx(self.path(rel), "Verbale riunione di direzione - 21 settembre 2026", [
            ("p", f"Presenti: {', '.join(person(r) for r in ('ceo', 'cfo', 'plant', 'quality', 'purchasing', 'sales'))}. "
                  f"Verbalizza: {person('hr')}."),
            ("h", "1. Andamento agosto-settembre"),
            ("p", "Fatturato di settembre in linea col budget (1,31 milioni di euro). Portafoglio ordini a 7,2 "
                  "settimane. Righe consegnate in ritardo: 14,2% (obiettivo 5%). Scarti tornitura 3,4% a "
                  "settembre, peggiorati per i problemi del TP-1."),
            ("h", "2. Ritardi di consegna"),
            ("p", f"{person('sales')} segnala tre reclami formali (Idrotecnica Lario, AquaSysteme Nordwerk, Caldaie "
                  "Orobiche) perche' i clienti vengono avvisati dei ritardi solo quando chiamano loro. Oggi il back "
                  "office confronta ogni mattina il piano di produzione con il portafoglio ordini e scrive a mano "
                  "le email di avviso: circa 25 minuti al giorno, ma spesso salta."),
            ("p", "Decisione: entro ottobre definire un avviso automatico al cliente quando la data prevista "
                  "supera la data promessa."),
            ("h", "3. Carico degli uffici"),
            ("p", f"{person('cfo')} riporta che l'amministrazione fa 6 ore di straordinario a settimana. Le voci "
                  "principali: controllo fatture fornitori contro ordini e DDT (circa 6,5 ore a settimana), "
                  "solleciti di pagamento ai clienti (circa 2,5 ore), riconciliazioni varie."),
            ("p", f"{person('purchasing')} dedica ogni lunedi' mattina circa 3 ore al controllo scorte e ai "
                  "riordini delle materie prime, perche' il gestionale non segnala il sottoscorta."),
            ("h", "4. Qualita'"),
            ("p", f"{person('quality')}: 41 non conformita' aperte nel 2026, 9 ancora aperte da oltre 30 giorni. "
                  "La compilazione del rapporto 8D e l'invio ai fornitori richiedono circa 45 minuti per NC. "
                  "Audit interno di settembre: 2 rilievi maggiori (vedi verbale)."),
            ("h", "5. Manutenzione"),
            ("p", f"{person('maintenance')}: due interventi preventivi sul TP-1 sono slittati perche' nessuno "
                  "ha ricordato la scadenza. Il calendario e' un Excel che viene aperto 'quando ci si ricorda'."),
            ("h", "6. Azioni"),
            ("table", [["Azione", "Responsabile", "Scadenza"],
                       ["Avviso automatico ritardi ai clienti", person("sales"), "31/10/2026"],
                       ["Valutare strumenti per il controllo fatture", person("cfo"), "15/11/2026"],
                       ["Allarme sottoscorta materie prime", person("purchasing"), "31/10/2026"],
                       ["Revisione piano manutenzione TP-1", person("maintenance"), "10/10/2026"],
                       ["Chiusura NC aperte da oltre 30 giorni", person("quality"), "31/10/2026"]]),
        ])

    # ------------------------------------------------------------ acquisti + amministrazione
    def purchasing_and_invoices(self) -> None:
        rng = self.rng
        days = working_days(date(2026, 7, 1), date(2026, 9, 30))
        mat = {m[0]: m for m in MATERIALS}
        pos, ddts, invoices = [], [], []
        defects: dict[str, list[str]] = {"price_mismatch": [], "quantity_mismatch": [], "missing_ddt": [],
                                         "duplicate_invoice": []}
        for n in range(1, 73):
            code = rng.choice(list(mat))
            m = mat[code]
            supplier = next(s for s in SUPPLIERS if s[0] == m[5])
            qty = m[4] if rng.random() < 0.7 else round(m[4] * rng.choice([0.5, 1.5]))
            d = rng.choice(days[:-10])
            po = f"OA26-{n:04d}"
            pos.append({"N. ordine": po, "Data ordine": d, "Fornitore": supplier[1], "Cod. fornitore": supplier[0],
                        "Articolo": code, "Descrizione": m[1], "UM": m[2], "Quantita'": qty,
                        "Prezzo unitario": m[6], "Importo": round(qty * m[6], 2),
                        "Consegna richiesta": d + timedelta(days=supplier[3]), "Buyer": PEOPLE["purchasing"][1]})
            delivered = d + timedelta(days=supplier[3] + rng.randint(-2, 6))
            if delivered > TODAY:
                continue
            ddt_qty = qty
            inv_qty, inv_price = qty, m[6]
            fate = rng.random()
            inv_no = f"{supplier[0][-1]}{rng.randint(1000, 9999)}/26"
            if fate < 0.08:
                inv_price = round(m[6] * rng.choice([1.04, 1.06, 1.12]), 3)
                defects["price_mismatch"].append(inv_no)
            elif fate < 0.15:
                ddt_qty = round(qty * rng.choice([0.9, 0.95]))
                defects["quantity_mismatch"].append(inv_no)  # invoiced full qty, delivered less
            if fate >= 0.15 and rng.random() < 0.05:
                defects["missing_ddt"].append(inv_no)
            else:
                ddts.append({"N. DDT": f"DDT-{supplier[0]}-{len(ddts) + 1:03d}", "Data DDT": delivered,
                             "Fornitore": supplier[1], "Rif. ordine": po, "Articolo": code, "Quantita' ricevuta": ddt_qty,
                             "Ricevuto da": PEOPLE["warehouse"][1]})
            row = {"N. fattura": inv_no, "Data fattura": delivered + timedelta(days=rng.randint(0, 5)),
                   "Fornitore": supplier[1], "Rif. ordine (da fattura)": po if rng.random() > 0.12 else "",
                   "Articolo": code, "Quantita' fatturata": inv_qty, "Prezzo unitario": inv_price,
                   "Imponibile": round(inv_qty * inv_price, 2), "IVA %": 22,
                   "Scadenza": (delivered + timedelta(days=60)), "Stato controllo": ""}
            invoices.append(row)
            if rng.random() < 0.03:
                invoices.append(dict(row))
                defects["duplicate_invoice"].append(inv_no)
        for r in invoices:  # first ones already checked by hand, the rest still pending
            r["Stato controllo"] = "OK" if r["Data fattura"] < date(2026, 8, 31) and r["N. fattura"] not in sum(
                defects.values(), []) else "da controllare"
        oa = "acquisti/Ordini_acquisto_2026_Q3.xlsx"
        with pd.ExcelWriter(self.path(oa)) as xw:
            pd.DataFrame(pos).to_excel(xw, sheet_name="Ordini", index=False)
            pd.DataFrame(ddts).to_excel(xw, sheet_name="DDT ricevuti", index=False)
        inv = "amministrazione/Fatture_fornitori_2026_Q3.xlsx"
        pd.DataFrame(invoices).to_excel(self.path(inv), sheet_name="Fatture passive", index=False)
        self.truth["data_defects"]["supplier_invoices"] = {k: sorted(set(v)) for k, v in defects.items()}

        write_docx(self.path("amministrazione/Procedura_ciclo_passivo.docx"), "PA-03 Ciclo passivo: "
                   "registrazione e controllo fatture fornitori", [
            ("p", f"Revisione 4 del 12/01/2026. Redatta da {person('admin')}, approvata da {person('cfo')}."),
            ("h", "Scopo"),
            ("p", "Garantire che ogni fattura passiva corrisponda a un ordine d'acquisto e a una merce "
                  "effettivamente ricevuta (controllo a tre vie) prima del pagamento."),
            ("h", "Modalita' operative"),
            ("ol", ["Le fatture arrivano via SDI e vengono scaricate ogni mattina dal portale in PDF/XML.",
                    "L'impiegata apre il file Fatture_fornitori e inserisce a mano numero, fornitore, articolo, "
                    "quantita' e prezzo di ogni riga.",
                    "Per ogni riga cerca l'ordine nel file Ordini_acquisto (foglio Ordini) e il DDT nel foglio "
                    "DDT ricevuti; se in fattura manca il riferimento all'ordine, lo cerca per fornitore e "
                    "articolo.",
                    "Confronta quantita' e prezzo. Tolleranza: 0% sul prezzo, 2% sulla quantita'.",
                    "Se tutto corrisponde scrive OK nella colonna Stato controllo; altrimenti scrive una email al "
                    "fornitore e all'ufficio acquisti con le differenze e mette la fattura in 'sospesa'.",
                    "Ogni venerdi' prepara l'elenco delle fatture da pagare per la tesoreria."]),
            ("h", "Tempi e criticita'"),
            ("p", "Arrivano circa 26 fatture a settimana; il controllo richiede in media 15 minuti per fattura "
                  "(circa 6,5 ore a settimana), di piu' quando manca il riferimento all'ordine. Nel 2025 sono "
                  "stati pagati due volte 3 documenti duplicati e accettati aumenti di prezzo non concordati per "
                  "circa 4.100 euro."),
            ("h", "Documenti collegati"),
            ("ul", ["Ordini_acquisto_2026_Q3.xlsx", "Fatture_fornitori_2026_Q3.xlsx", "Anagrafica_fornitori.csv"]),
        ])
        self.task("T1", "Controllo a tre vie fatture fornitori (ordine, DDT, fattura)", 26, 15,
                  [oa, inv, "amministrazione/Procedura_ciclo_passivo.docx"], ["Amministrazione", "Acquisti"],
                  notes="Answers in data_defects.supplier_invoices.")

        pd.DataFrame([{"Codice": s[0], "Ragione sociale": s[1], "Fornitura": s[2], "Lead time (gg)": s[3],
                       "Email ordini": s[4], "Pagamento": rng.choice(["RB 60 gg fm", "BB 90 gg fm", "BB 60 gg df"]),
                       "Valutazione 2025": rng.choice(["A", "A", "B", "B", "C"])} for s in SUPPLIERS]
                     ).to_csv(self.path("acquisti/Anagrafica_fornitori.csv"), index=False, sep=";")

    def inventory(self) -> None:
        rng = self.rng
        rel = "magazzino/Magazzino_Gestio7_export.sqlite"
        p = self.path(rel)
        p.unlink(missing_ok=True)
        con = sqlite3.connect(p)
        con.executescript("""
            CREATE TABLE articoli (codice TEXT PRIMARY KEY, descrizione TEXT, um TEXT, giacenza REAL,
                                   scorta_minima REAL, lotto_riordino REAL, fornitore TEXT, costo_unitario REAL,
                                   ubicazione TEXT);
            CREATE TABLE movimenti (id INTEGER PRIMARY KEY, data TEXT, codice TEXT REFERENCES articoli(codice),
                                    causale TEXT, quantita REAL, riferimento TEXT);
        """)
        below = []
        for code, desc, um, smin, lot, sup, cost in MATERIALS:
            low = rng.random() < 0.3
            stock = round(smin * rng.uniform(0.35, 0.95)) if low else round(smin * rng.uniform(1.2, 2.6))
            if low:
                below.append(code)
            con.execute("INSERT INTO articoli VALUES (?,?,?,?,?,?,?,?,?)",
                        (code, desc, um, stock, smin, lot, sup, cost, f"{rng.choice('ABCD')}-{rng.randint(1, 30):02d}"))
        mid = 0
        for d in working_days(date(2026, 9, 1), date(2026, 9, 30)):
            for code, _desc, _um, smin, lot, *_ in MATERIALS:
                if rng.random() < 0.55:
                    mid += 1
                    con.execute("INSERT INTO movimenti VALUES (?,?,?,?,?,?)",
                                (mid, d.isoformat(), code, "scarico produzione", -round(smin * rng.uniform(0.03, 0.12)),
                                 f"ODP-26-{rng.randint(400, 620)}"))
                if rng.random() < 0.05:
                    mid += 1
                    con.execute("INSERT INTO movimenti VALUES (?,?,?,?,?,?)",
                                (mid, d.isoformat(), code, "carico da fornitore", lot, "DDT"))
        con.commit()
        mov = pd.read_sql("SELECT m.data AS Data, m.codice AS Articolo, a.descrizione AS Descrizione, m.causale AS "
                          "Causale, m.quantita AS Quantita, m.riferimento AS Riferimento FROM movimenti m JOIN "
                          "articoli a USING(codice) ORDER BY m.id", con)
        con.close()
        mov.to_csv(self.path("magazzino/Movimenti_magazzino_settembre_2026.csv"), index=False, sep=";", decimal=",")
        self.truth["data_defects"]["below_min_stock"] = sorted(below)

        write_docx(self.path("acquisti/Procedura_riordino_materie_prime.docx"),
                   "PA-07 Riordino materie prime e componenti", [
            ("p", f"Revisione 2 del 03/03/2025. Responsabile: {person('purchasing')}."),
            ("h", "Come si fa oggi"),
            ("ol", ["Il lunedi' mattina il buyer esporta dal gestionale la tabella articoli (giacenza, scorta "
                    "minima, lotto di riordino) nel file Magazzino_Gestio7_export.",
                    "Confronta articolo per articolo la giacenza con la scorta minima, tenendo conto degli ordini "
                    "gia' aperti (file Ordini_acquisto) e dei consumi previsti dal piano di produzione.",
                    "Per ogni articolo sottoscorta prepara una richiesta d'offerta o un ordine al fornitore "
                    "abituale (vedi Anagrafica_fornitori) con il lotto di riordino standard.",
                    "Invia gli ordini via email e li registra nel gestionale."]),
            ("p", "Tempo richiesto: circa 3 ore ogni lunedi', piu' 2-3 richieste urgenti a settimana da 20 minuti "
                  "l'una quando il reparto segnala che una barra sta finendo."),
            ("h", "Problemi noti"),
            ("ul", ["Ad agosto il TP-2 e' rimasto fermo 9 ore per mancanza di barra esagonale 30 mm.",
                    "Gli O-ring vengono spesso ordinati due volte perche' non si vede l'ordine gia' aperto.",
                    "Il gestionale non manda avvisi di sottoscorta."]),
        ])
        self.task("T2", "Controllo settimanale scorte e riordino materie prime", 1, 180,
                  [rel, "acquisti/Procedura_riordino_materie_prime.docx", "acquisti/Ordini_acquisto_2026_Q3.xlsx"],
                  ["Acquisti", "Magazzino"], notes="Plus ~2.5 urgent requests/week x 20 min, see T2b.")
        self.task("T2b", "Riordini urgenti segnalati dal reparto", 2.5, 20,
                  ["acquisti/Procedura_riordino_materie_prime.docx"], ["Acquisti", "Produzione"])

    def receivables(self) -> None:
        rng = self.rng
        rows, overdue = [], []
        for n in range(1, 131):
            cust = rng.choices(CUSTOMERS, weights=[24, 17, 9, 15, 7, 9, 5])[0]
            d = date(2026, 4, 1) + timedelta(days=rng.randint(0, 175))
            due = d + timedelta(days=cust[3])
            paid = due < TODAY and rng.random() < 0.82
            inv = f"FV26-{n:04d}"
            if due < TODAY and not paid:
                overdue.append(inv)
            rows.append({"Fattura": inv, "Data": d, "Cliente": cust[1], "Cod. cliente": cust[0],
                         "Importo": round(rng.uniform(1800, 46000), 2), "Scadenza": due,
                         "Pagata": "SI" if paid else "NO",
                         "Data incasso": (due + timedelta(days=rng.randint(-3, 12))) if paid else "",
                         "Solleciti inviati": rng.randint(1, 3) if (due < TODAY and not paid and rng.random() < 0.5) else 0})
        rel = "amministrazione/Scadenzario_clienti_2026.csv"
        pd.DataFrame(rows).to_csv(self.path(rel), index=False, sep=";", decimal=",")
        self.truth["data_defects"]["overdue_receivables"] = overdue
        write_text(self.path("amministrazione/Modelli_email_solleciti.txt"), f"""
        MODELLI EMAIL SOLLECITO PAGAMENTO - uso interno amministrazione
        (copiare nel client di posta, sostituire i campi tra parentesi quadre)

        Procedura: ogni martedi' e venerdi' {person('admin')} filtra lo Scadenzario_clienti per Pagata = NO e
        Scadenza passata, controlla in banca gli incassi del giorno, poi scrive un'email per ogni fattura
        scaduta usando il livello giusto. Tempo: circa 75 minuti per volta.

        ---------------------------------------------------------------------
        LIVELLO 1 (da 1 a 15 giorni di ritardo)
        Oggetto: Promemoria fattura [NUMERO] del [DATA]

        Gentili [CLIENTE],
        da un controllo amministrativo non ci risulta ancora il pagamento della fattura [NUMERO] di euro
        [IMPORTO], scaduta il [SCADENZA]. Se il pagamento e' gia' stato disposto, vi preghiamo di non
        considerare questa comunicazione.
        Cordiali saluti, Ufficio amministrazione {COMPANY['name']}

        ---------------------------------------------------------------------
        LIVELLO 2 (da 16 a 45 giorni)
        Oggetto: Secondo sollecito fattura [NUMERO]
        ... vi chiediamo di provvedere entro 7 giorni e di inviarci copia della contabile ...

        ---------------------------------------------------------------------
        LIVELLO 3 (oltre 45 giorni) - mettere in copia {mail('cfo')} e {mail('sales')}
        Oggetto: Ultimo sollecito prima del blocco forniture - fattura [NUMERO]
        ... in assenza di riscontro entro 5 giorni saremo costretti a sospendere le spedizioni ...
        """)
        self.task("T3", "Solleciti di pagamento ai clienti", 2, 75,
                  [rel, "amministrazione/Modelli_email_solleciti.txt"], ["Amministrazione"],
                  notes="Answers in data_defects.overdue_receivables.")

    # ------------------------------------------------------------ produzione
    def production(self) -> None:
        rng = self.rng
        rows = []
        causes = ["", "", "", "", "cambio barra", "rottura inserto", "attesa materiale", "regolazione quota",
                  "allarme mandrino", "manca operatore"]
        notes = ["", "", "", "tutto ok", "quota 12,4 al limite, ricontrollato", "inserto cambiato x2",
                 "scarti x bava, segnalato al capoturno", "fermo x barra finita, chiamato magazzino",
                 "TP1 rumore mandrino 3 !!", "pezzi messi in quarantena (vedi NC)"]
        for d in working_days(date(2026, 9, 1), date(2026, 9, 30)):
            for turno in ("1 (6-14)", "2 (14-22)", "3 (22-6)"):
                for mach in ("TP-1", "TP-2", "CL-1", "TR-1"):
                    if turno.startswith("3") and mach in ("CL-1", "TR-1"):
                        continue
                    prod = rng.choice(PRODUCTS[:5] if mach.startswith("TP") else PRODUCTS[5:] if mach == "TR-1"
                                      else PRODUCTS)
                    base = {"TP-1": 2600, "TP-2": 3900, "CL-1": 900, "TR-1": 1400}[mach]
                    cause = rng.choice(causes + (["allarme mandrino"] * 2 if mach == "TP-1" else []))
                    stop = rng.randint(15, 140) if cause else 0
                    good = int(base * (1 - stop / 480) * rng.uniform(0.85, 1.0))
                    scrap_rate = rng.uniform(0.012, 0.03) * (1.8 if mach == "TP-1" and d.day > 14 else 1)
                    rows.append({"Data": d.strftime("%d/%m/%Y"), "Turno": turno, "Macchina": mach,
                                 "Operatore": rng.choice(OPERATORS), "Articolo": prod[0], "ODP": f"ODP-26-{rng.randint(480, 640)}",
                                 "Pezzi buoni": good, "Scarti": int(good * scrap_rate), "Fermo (min)": stop,
                                 "Causa fermo": cause, "Note capoturno": rng.choice(notes)})
        rep = "produzione/Rapporti_turno_settembre_2026.csv"
        df = pd.DataFrame(rows)
        df.to_csv(self.path(rep), index=False, sep=";")
        tp1 = df[df.Macchina == "TP-1"]
        self.truth["data_defects"]["tp1_scrap_rate_sept"] = {
            "first_half": round(tp1[tp1.Data.str[:2].astype(int) <= 14].Scarti.sum()
                                / tp1[tp1.Data.str[:2].astype(int) <= 14]["Pezzi buoni"].sum(), 4),
            "second_half": round(tp1[tp1.Data.str[:2].astype(int) > 14].Scarti.sum()
                                 / tp1[tp1.Data.str[:2].astype(int) > 14]["Pezzi buoni"].sum(), 4),
            "note": "TP-1 scrap roughly doubles after 14 Sept (spindle 3 issue, see notes and maintenance).",
        }
        write_text(self.path("produzione/Istruzioni_rapporto_turno.txt"), f"""
        RAPPORTO DI TURNO - COME SI COMPILA (affisso in reparto, rev. 2025)

        1. A fine turno il capoturno compila il modulo cartaceo MOD-PR-02 per ogni macchina: articolo, ODP,
           pezzi buoni, scarti, minuti di fermo e causa, note.
        2. I moduli vanno nella cassetta all'ingresso dell'ufficio produzione.
        3. Ogni mattina {person('planning')} trascrive i moduli del giorno prima nel file
           Rapporti_turno (circa 10 schede al giorno, 25-30 minuti), poi aggiorna l'avanzamento degli ODP
           nel gestionale e il grafico OEE settimanale (altri 20 minuti).
        4. Il venerdi' prepara il riepilogo scarti e fermi per la riunione di produzione del lunedi' (1 ora).

        Note: grafia illeggibile e moduli mancanti sono la causa principale dei buchi nei dati.
        """)
        self.task("T4", "Trascrizione rapporti di turno, avanzamento ODP e riepilogo settimanale", 5, 50,
                  [rep, "produzione/Istruzioni_rapporto_turno.txt"], ["Produzione"],
                  notes="5 x 50 min daily + weekly summary; data-capture step needs a tablet/form, rest automatable.")

        week = [TODAY + timedelta(days=3 + i) for i in range(5)]  # week 41 plan
        plan = []
        for mach in ("TP-1", "TP-2", "CL-1", "TR-1"):
            for d in week:
                prod = rng.choice(PRODUCTS[:5] if mach.startswith("TP") else PRODUCTS[5:])
                cust = rng.choice(CUSTOMERS)
                plan.append({"Settimana": 41, "Giorno": d.strftime("%a %d/%m"), "Macchina": mach, "Articolo": prod[0],
                             "ODP": f"ODP-26-{rng.randint(640, 700)}", "Quantita'": rng.randint(4, 30) * 500,
                             "Cliente": cust[1], "Fine prevista": d, "Data promessa": d + timedelta(days=rng.randint(-4, 8)),
                             "Note": rng.choice(["", "", "materiale da confermare", "attrezzaggio lungo"])})
        pl = pd.DataFrame(plan)
        late = pl[pl["Data promessa"] < pl["Fine prevista"]]
        pl.to_excel(self.path("produzione/Piano_produzione_settimana_41.xlsx"), index=False, sheet_name="Piano W41")
        self.truth["data_defects"]["plan_w41_late_vs_promise"] = sorted(late.ODP.tolist())

        write_docx(self.path("produzione/IO-12_Attrezzaggio_plurimandrino.docx"),
                   "IO-12 Istruzione operativa: attrezzaggio plurimandrino TP-1 / TP-2", [
            ("p", "Revisione 6. Documento tecnico, nessuna attivita' ripetitiva d'ufficio."),
            ("h", "Sicurezza"),
            ("ul", ["Macchina in modalita' attrezzaggio, ripari chiusi durante la rotazione del tamburo.",
                    "Guanti antitaglio per la manipolazione degli inserti.", "Lockout sul quadro durante il cambio barra."]),
            ("h", "Sequenza"),
            ("ol", ["Scaricare la barra residua e pulire il caricatore.", "Montare le pinze del diametro corretto "
                    "(20, 30 o 40 mm) secondo la scheda articolo.", "Montare gli utensili per stazione come da "
                    "tabella, azzerare con presetting.", "Caricare il programma dal server CN (cartella ARTICOLI/"
                    "<codice>).", "Primo pezzo: controllo dimensionale completo in sala metrologica, registrare su "
                    "MOD-QA-05.", "Avvio serie solo dopo firma del capoturno."]),
            ("table", [["Articolo", "Pinza", "Tempo attrezzaggio std (min)", "Ciclo (s/pz)"],
                       ["RF-12", "20 esag.", "75", "6,5"], ["RF-22", "30 esag.", "90", "8,0"],
                       ["RF-34", "30 esag.", "95", "8,6"], ["NP-08", "20 esag.", "60", "4,2"],
                       ["MN-05", "20 esag.", "70", "5,5"]]),
        ])

    def downtime_and_maintenance(self) -> None:
        rng = self.rng
        plan = [("TP-1", "Lubrificazione centralizzata", 7), ("TP-1", "Controllo giochi mandrini", 30),
                ("TP-1", "Sostituzione filtri emulsione", 30), ("TP-2", "Lubrificazione centralizzata", 7),
                ("TP-2", "Controllo giochi mandrini", 30), ("CL-1", "Verifica geometrie e backlash", 90),
                ("CL-1", "Pulizia filtri quadro elettrico", 30), ("TR-1", "Verifica tavola rotante", 60),
                ("LV-1", "Rigenerazione solvente", 14), ("LV-1", "Controllo sensori livello", 30),
                ("BC-1", "Taratura trasduttori di pressione", 180), ("Compressore", "Manutenzione 2000 ore", 90)]
        rows, overdue = [], []
        for mach, job, every in plan:
            last = TODAY - timedelta(days=rng.randint(0, every - 1))
            late = {("TP-1", "Controllo giochi mandrini"): 17, ("TP-1", "Sostituzione filtri emulsione"): 9,
                    ("CL-1", "Pulizia filtri quadro elettrico"): 4}.get((mach, job))  # matches the audit (3 jobs)
            if late:
                last = TODAY - timedelta(days=every + late)
            nxt = last + timedelta(days=every)
            if nxt < TODAY:
                overdue.append(f"{mach}: {job}")
            rows.append({"Macchina": mach, "Intervento": job, "Frequenza (gg)": every, "Ultima esecuzione": last,
                         "Prossima scadenza": nxt, "Esecutore": rng.choice(["interno", "interno", "ditta esterna"]),
                         "Durata stimata (h)": rng.choice([0.5, 1, 2, 4])})
        rel = "produzione/Calendario_manutenzioni_preventive.xlsx"
        pd.DataFrame(rows).to_excel(self.path(rel), index=False, sheet_name="Preventiva")
        self.truth["data_defects"]["overdue_maintenance"] = overdue
        self.task("T5", "Promemoria scadenze manutenzione preventiva", 1, 45,
                  [rel, "direzione/Verbale_riunione_direzione_2026-09-21.docx"], ["Manutenzione"],
                  notes="Today done 'when someone remembers'; cost is the weekly check, the real value is avoided stops.")

        stops = []
        for d in working_days(date(2026, 1, 7), date(2026, 9, 30)):
            for mach, _, _ in MACHINES[:4]:
                if rng.random() < (0.22 if mach == "TP-1" else 0.12):
                    cause = rng.choice(["guasto meccanico", "guasto elettrico", "mancanza materiale",
                                        "attrezzaggio oltre standard", "attesa manutentore", "qualita' (fermo per NC)"])
                    stops.append({"Data": d.isoformat(), "Macchina": mach, "Causa": cause,
                                  "Durata (min)": rng.randint(20, 300), "Intervento": rng.choice(["RP", "manut. 2", "ditta esterna"])})
        pd.DataFrame(stops).to_csv(self.path("produzione/Fermi_macchina_2026.csv"), index=False)

    # ------------------------------------------------------------ qualita'
    def quality(self) -> None:
        rng = self.rng
        defects = ["filetto fuori tolleranza", "bava interna", "porosita' ottone", "nichelatura non uniforme",
                   "perdita al collaudo", "quota 12,4 fuori tolleranza", "graffi superficiali", "O-ring mancante"]
        rows, stale = [], []
        opened = [date(2026, 1, 8) + timedelta(days=int(n * 6.4) + rng.randint(-2, 2)) for n in range(1, 42)]
        old_ones = [i for i, d in enumerate(opened) if (TODAY - d).days > 30]
        left_open = set(rng.sample(old_ones, 9))  # board minutes and audit both say 9
        for n, d in enumerate(opened, start=1):
            origin = rng.choice(["interna", "interna", "fornitore", "cliente"])
            closed = (n - 1) not in left_open and (TODAY - d).days > 20
            nc = f"NC-2026-{n:03d}"
            if not closed and (TODAY - d).days > 30:
                stale.append(nc)
            prod = rng.choice(PRODUCTS)
            rows.append({"N. NC": nc, "Data apertura": d, "Origine": origin, "Articolo": prod[0],
                         "Fornitore/Cliente": rng.choice(SUPPLIERS[:4])[1] if origin == "fornitore"
                         else rng.choice(CUSTOMERS)[1] if origin == "cliente" else "",
                         "Difetto": rng.choice(defects), "Pezzi coinvolti": rng.randint(50, 4000),
                         "Causa (5 perche')": rng.choice(["", "usura inserto", "barra fuori specifica", "operatore nuovo",
                                                          "programma CN vecchia revisione"]),
                         "Azione correttiva": rng.choice(["", "selezione 100%", "rilavorazione", "reso a fornitore",
                                                          "modifica piano di controllo"]),
                         "8D inviato": "SI" if origin != "interna" and closed else "NO" if origin != "interna" else "n/a",
                         "Stato": "chiusa" if closed else "aperta", "Responsabile": PEOPLE["quality"][1],
                         "Data chiusura": d + timedelta(days=rng.randint(5, 25)) if closed else ""})
        rel = "qualita/Registro_non_conformita_2026.xlsx"
        pd.DataFrame(rows).to_excel(self.path(rel), index=False, sheet_name="NC 2026")
        self.truth["data_defects"]["nc_open_over_30_days"] = stale

        write_docx(self.path("qualita/PQ-08_Gestione_non_conformita.docx"), "PQ-08 Gestione delle non conformita'", [
            ("p", f"Revisione 5 del 20/02/2026. Responsabile: {person('quality')}."),
            ("h", "Flusso"),
            ("ol", ["Chi rileva il difetto compila il cartellino rosso e isola il materiale.",
                    "La qualita' registra la NC nel Registro_non_conformita (numero progressivo).",
                    "Per NC di fornitore o cliente si compila il rapporto 8D in Word partendo dal modello, "
                    "copiando a mano i dati dal registro, le foto e le misure del laboratorio (circa 30 minuti).",
                    "Si invia l'8D via email al fornitore (o al cliente) e si archivia il PDF (circa 15 minuti).",
                    "Ogni lunedi' si controllano le NC aperte e si sollecitano i responsabili delle azioni."]),
            ("p", "Volumi: circa 4 NC a settimana di cui 3 con 8D. Il controllo delle NC aperte richiede circa 30 "
                  "minuti a settimana ma spesso viene saltato: 9 NC sono aperte da piu' di 30 giorni."),
        ])
        self.task("T6", "Compilazione e invio rapporti 8D + follow-up NC aperte", 3, 45,
                  [rel, "qualita/PQ-08_Gestione_non_conformita.docx"], ["Qualita'"],
                  notes="Plus 30 min/week follow-up. Answers in data_defects.nc_open_over_30_days.")

        write_docx(self.path("qualita/Verbale_audit_interno_2026-09.docx"), "Verbale audit interno ISO 9001 - "
                   "settembre 2026", [
            ("p", "Auditor: consulente esterno (fittizio). Aree: produzione, qualita', acquisti, manutenzione."),
            ("h", "Rilievi maggiori"),
            ("ol", ["Manutenzione preventiva: 3 interventi scaduti senza registrazione di slittamento motivato "
                    "(TP-1 controllo giochi mandrini scaduto da oltre 2 settimane).",
                    "Non conformita': 9 NC aperte da oltre 30 giorni senza azione correttiva definita."]),
            ("h", "Rilievi minori"),
            ("ul", ["Rapporti di turno incompleti: 6% dei turni di agosto senza modulo.",
                    "Valutazione fornitori 2025 non aggiornata per due fornitori.",
                    "Piano di controllo RF-34 non riporta la frequenza di misura della quota 12,4."]),
            ("h", "Punti di forza"),
            ("ul", ["Sala metrologica ben gestita, tarature in regola.", "Personale formato e disponibile."]),
        ])
        write_pdf(self.path("qualita/Manuale_qualita_estratto_rev9.pdf"), f"Manuale della qualita' - {COMPANY['name']}", [
            ("1. Campo di applicazione", "Progettazione, produzione e vendita di raccordi filettati e valvole a sfera "
             "in ottone e acciaio inossidabile torniti da barra. Esclusa la progettazione per lavorazioni conto "
             "terzi su disegno del cliente."),
            ("2. Politica per la qualita'", "La direzione si impegna a consegnare prodotti conformi nei tempi "
             "promessi, a migliorare continuamente i processi e a coinvolgere il personale. Gli indicatori chiave "
             "sono: puntualita' di consegna (obiettivo 95%), scarti interni (obiettivo 2%), reclami cliente "
             "(obiettivo meno di 12 l'anno), tempo medio di chiusura NC (obiettivo 20 giorni)."),
            ("3. Processi", "Commerciale (offerta, conferma d'ordine), pianificazione, approvvigionamento, "
             "produzione, controllo, magazzino e spedizione, manutenzione, gestione NC e azioni correttive, "
             "audit interni, riesame della direzione. Ogni processo ha un responsabile e indicatori mensili."),
            ("4. Controllo dei documenti", "Le procedure (PA, PQ, IO) sono archiviate sul server aziendale in "
             "sola lettura. Le revisioni sono approvate dal responsabile qualita'. Le copie stampate in reparto "
             "riportano il timbro 'copia controllata'."),
            ("5. Tracciabilita'", "Ogni lotto e' identificato dal numero ODP e dal numero di colata della barra. "
             "Il certificato 3.1 del fornitore viene associato al lotto all'accettazione."),
            ("6. Gestione dei fornitori", "I fornitori sono valutati ogni anno su qualita', puntualita' e "
             "reattivita' (classi A, B, C). I fornitori in classe C hanno un piano di miglioramento."),
        ])
        pd.DataFrame([
            {"Caratteristica": "Diametro filetto 3/4\" gas", "Strumento": "calibro filettato P/NP", "Frequenza": "1/500 pz",
             "Tolleranza": "classe A", "Registrazione": "MOD-QA-05"},
            {"Caratteristica": "Quota 12,4", "Strumento": "micrometro", "Frequenza": "", "Tolleranza": "+/-0,05",
             "Registrazione": "MOD-QA-05"},
            {"Caratteristica": "Sede O-ring", "Strumento": "proiettore di profili", "Frequenza": "1/1000 pz",
             "Tolleranza": "+0,1/0", "Registrazione": "MOD-QA-05"},
            {"Caratteristica": "Tenuta 6 bar aria", "Strumento": "banco BC-1", "Frequenza": "100%", "Tolleranza": "0 perdite",
             "Registrazione": "automatica BC-1"},
            {"Caratteristica": "Aspetto", "Strumento": "visivo", "Frequenza": "1/200 pz", "Tolleranza": "campione",
             "Registrazione": "MOD-QA-05"},
        ]).to_csv(self.path("qualita/Piano_controllo_RF-34.csv"), index=False, sep=";")

    # ------------------------------------------------------------ commerciale
    def sales(self) -> None:
        rng = self.rng
        price = []
        for code, desc, mcode, grams, p in PRODUCTS:
            price.append({"Articolo": code, "Descrizione": desc, "Materiale": mcode, "Peso (g)": grams,
                          "1-499 pz": p, "500-1999 pz": round(p * 0.93, 3), "2000-9999 pz": round(p * 0.86, 3),
                          ">=10000 pz": round(p * 0.80, 3), "Imballo std (pz)": rng.choice([50, 100, 200, 500])})
        lp = "commerciale/Listino_prezzi_2026.xlsx"
        with pd.ExcelWriter(self.path(lp)) as xw:
            pd.DataFrame(price).to_excel(xw, sheet_name="Listino", index=False)
            pd.DataFrame([{"Voce": "Supplemento ottone (EUR/kg oltre 6,50)", "Valore": 0.0011},
                          {"Voce": "Nichelatura (EUR/pz) RF", "Valore": 0.09},
                          {"Voce": "Nichelatura (EUR/pz) VS", "Valore": 0.21},
                          {"Voce": "Trasporto Italia franco destino (EUR/collo)", "Valore": 14.5},
                          {"Voce": "Lotto minimo fatturabile (EUR)", "Valore": 250}]
                         ).to_excel(xw, sheet_name="Maggiorazioni", index=False)
        offers = []
        for n in range(1, 97):
            cust = rng.choice(CUSTOMERS)
            d = date(2026, 1, 5) + timedelta(days=int(n * 2.8))
            lines = rng.randint(1, 9)
            offers.append({"Offerta": f"OF26-{n:03d}", "Data richiesta": d, "Cliente": cust[1],
                           "Righe": lines, "Valore (EUR)": round(lines * rng.uniform(900, 7000), 0),
                           "Tempo preparazione (min)": 20 + lines * rng.randint(6, 12),
                           "Giorni per risposta": rng.choice([0, 1, 1, 2, 3, 5]),
                           "Esito": rng.choice(["vinta", "vinta", "persa", "persa (prezzo)", "in attesa"]),
                           "Note": rng.choice(["", "", "cliente chiede consegna 3 settimane", "richiesto certificato 3.1",
                                               "prezzo ottone da aggiornare", "concorrente piu' rapido a rispondere"])})
        of = "commerciale/Registro_offerte_2026.csv"
        dfo = pd.DataFrame(offers)
        dfo.to_csv(self.path(of), index=False, sep=";")
        self.task("T7", "Preparazione offerte da listino (righe, maggiorazioni, PDF, email)", 3.4,
                  round(float(dfo["Tempo preparazione (min)"].mean()), 1),
                  [lp, of], ["Commerciale"], notes="~96 offers in 9 months; 25% of losses cite response time.")

        delay_rel = "commerciale/Email_ritardi_consegna_settembre.txt"
        write_text(self.path(delay_rel), f"""
        Da: acquisti@idrolario.example
        A: {mail('sales')}
        Data: 09/09/2026 08:12
        Oggetto: R: Ordine 4500123 - RF-34 x 20.000

        Buongiorno, la merce doveva arrivare venerdi' e non abbiamo ricevuto nessun avviso. La nostra linea di
        assemblaggio si ferma mercoledi'. Ci date una data certa?

        ---
        Da: {mail('sales')}
        A: acquisti@idrolario.example
        Data: 09/09/2026 10:40
        Oggetto: R: R: Ordine 4500123 - RF-34 x 20.000

        Buongiorno, ci scusiamo. Il lotto e' stato rallentato da un fermo del TP-1. Spediamo 12.000 pezzi
        domani e il saldo martedi' 15/09. In futuro vi avviseremo appena il piano cambia.

        ---
        Da: einkauf@aquanordwerk.example
        A: {mail('sales')}
        Data: 17/09/2026 14:03
        Oggetto: Liefertermin VS-20 / delivery date VS-20

        Dear Ms Moretti, this is the second time this quarter that we learn about a delay only from our
        forwarder. Please send us proactive notice of any change to confirmed dates. Regards, Purchasing.

        ---
        Da: {mail('sales')}
        A: {mail('planning')}
        Data: 17/09/2026 14:30
        Oggetto: piano vs portafoglio

        Francesca, puoi mandarmi ogni mattina l'elenco degli ODP che finiscono dopo la data promessa? Lo
        confronto io col portafoglio e scrivo ai clienti. Lo facciamo a mano finche' non troviamo di meglio.

        ---
        Da: supply@caldaieorobiche.example
        A: {mail('sales')}
        Data: 24/09/2026 09:55
        Oggetto: Reclamo formale ritardi

        Con la presente formalizziamo il reclamo per i ritardi di consegna di agosto e settembre (3 ordini).
        Chiediamo un piano di rientro e avvisi tempestivi.
        """)
        self.task("T8", "Confronto piano produzione vs portafoglio e avviso ritardi ai clienti", 5, 25,
                  [delay_rel, "produzione/Piano_produzione_settimana_41.xlsx",
                   "direzione/Verbale_riunione_direzione_2026-09-21.docx"], ["Commerciale", "Produzione"],
                  notes="Answers in data_defects.plan_w41_late_vs_promise.")

        write_pptx(self.path("commerciale/Presentazione_aziendale_2026.pptx"), [
            (COMPANY["name"], ["Raccordi e valvole in ottone e inox dal 1987", "Borgo Valsorda (BS) - documento fittizio"]),
            ("Numeri", ["86 persone, 9.800 m2", "14,6 milioni di euro nel 2025, export 31%",
                        "38 milioni di pezzi torniti l'anno", "ISO 9001 e ISO 14001"]),
            ("Prodotti", ["Raccordi filettati RF, nippli NP, manicotti MN", "Valvole a sfera VS-10, VS-20",
                          "Linea inox VX-15 per alimentare (WRAS in corso)", "Lavorazioni su disegno"]),
            ("Reparti e macchine", ["2 plurimandrini (6 e 8 mandrini)", "Centro di lavoro 4 assi",
                                    "Transfer rotativo 12 stazioni", "Lavaggio e collaudo tenuta 100%"]),
            ("Perche' sceglierci", ["Lotti da 500 a 200.000 pezzi", "Tracciabilita' per colata",
                                    "Ufficio tecnico interno", "Consegna media 3-4 settimane"]),
        ])

    # ------------------------------------------------------------ noise
    def noise(self) -> None:
        mensa = "risorse_umane/Regolamento_mensa.txt"
        write_text(self.path(mensa), """
        REGOLAMENTO MENSA AZIENDALE

        La mensa e' aperta dalle 12:00 alle 14:00 e dalle 19:30 alle 20:30 per il secondo turno.
        Il buono pasto si prenota entro le 10:00 sul foglio in portineria. Il menu settimanale e' esposto il
        lunedi'. E' possibile richiedere menu senza glutine o vegetariano avvisando la portineria.
        Si raccomanda di lasciare il vassoio negli appositi carrelli e di rispettare gli orari per reparto.
        """)
        env = "qualita/Politica_ambientale_2026.pdf"
        write_pdf(self.path(env), "Politica ambientale 2026", [
            ("Impegni", "Ridurre i consumi elettrici del 10% entro il 2027 con l'impianto fotovoltaico da 400 kWp, "
             "recuperare il 100% dei trucioli di ottone tramite fonderia convenzionata, sostituire le emulsioni con "
             "prodotti esenti da boro, formare tutto il personale sulla gestione dei rifiuti."),
            ("Indicatori", "kWh per milione di pezzi, kg di rifiuti pericolosi per tonnellata di ottone lavorato, "
             "litri di emulsione consumati per mese."),
        ])
        welcome = "risorse_umane/Benvenuto_nuovi_assunti.docx"
        write_docx(self.path(welcome), "Benvenuto in Officine Valsorda", [
            ("p", "Questa guida aiuta i nuovi colleghi nella prima settimana."),
            ("ul", ["Badge e DPI in portineria il primo giorno.", "Corso sicurezza generale (4 ore) e specifico.",
                    "Affiancamento di due settimane con un collega esperto.", "Parcheggio interno lato nord."]),
        ])
        self.truth["noise_documents"] = [mensa, env, welcome, "produzione/IO-12_Attrezzaggio_plurimandrino.docx"]

    # ------------------------------------------------------------ meta
    def queries(self) -> None:
        self.truth["queries"] = [
            {"query": "come si controllano le fatture dei fornitori contro ordini e DDT",
             "relevant": ["amministrazione/Procedura_ciclo_passivo.docx", "amministrazione/Fatture_fornitori_2026_Q3.xlsx"]},
            {"query": "scorta minima barra ottone riordino", "relevant": [
                "acquisti/Procedura_riordino_materie_prime.docx", "magazzino/Magazzino_Gestio7_export.sqlite"]},
            {"query": "solleciti pagamento clienti scaduti", "relevant": [
                "amministrazione/Modelli_email_solleciti.txt", "amministrazione/Scadenzario_clienti_2026.csv"]},
            {"query": "scarti TP-1 mandrino settembre", "relevant": [
                "produzione/Rapporti_turno_settembre_2026.csv", "produzione/Calendario_manutenzioni_preventive.xlsx"]},
            {"query": "rapporto 8D non conformita' fornitore", "relevant": [
                "qualita/PQ-08_Gestione_non_conformita.docx", "qualita/Registro_non_conformita_2026.xlsx"]},
            {"query": "reclamo ritardo consegna cliente", "relevant": [
                "commerciale/Email_ritardi_consegna_settembre.txt", "direzione/Verbale_riunione_direzione_2026-09-21.docx"]},
            {"query": "quota 12,4 piano di controllo RF-34", "relevant": [
                "qualita/Piano_controllo_RF-34.csv", "qualita/Verbale_audit_interno_2026-09.docx"]},
            {"query": "tempo preparazione offerte listino", "relevant": [
                "commerciale/Registro_offerte_2026.csv", "commerciale/Listino_prezzi_2026.xlsx"]},
        ]

    def readme(self) -> None:
        tasks = self.truth["tasks"]
        total = round(sum(t["hours_per_week"] for t in tasks if t["automatable"]), 1)
        rows = "\n".join(f"| {t['id']} | {t['title']} | {t['hours_per_week']} | {', '.join(t['departments'])} |"
                         for t in tasks)
        files = sorted(str(p.relative_to(self.docs)) for p in self.docs.rglob("*") if p.is_file())
        write_text(self.out / "README.md", "\n".join([
            f"# {COMPANY['name']} (fictional test company)", "",
            "Generated by `benchmarks/datasets/generate_fictional_company.py`. **Git-ignored. Everything is invented**: "
            "people, companies, addresses, VAT numbers and `.example` e-mail domains.", "",
            "Upload the contents of `documents/` into Husk (Knowledge), then run Discovery. Do **not** upload "
            "this README or `ground_truth.json`: they contain the answers.", "",
            "## Company", "",
            f"Brass and stainless fittings and ball valves, {COMPANY['employees']} employees, EUR 14.6M revenue (2025), "
            f"31% export, 3 shifts in turning. ERP without API (CSV/Excel exports). In-fiction date: {TODAY.isoformat()}. "
            "Full profile: `documents/direzione/Profilo_aziendale.md`. Documents are in Italian like a real customer's.", "",
            "## Planted repetitive tasks (expected Discovery findings)", "",
            "| ID | Task | h/week | Departments |", "|---|---|---|---|", rows, "",
            f"Total automatable: **~{total} h/week**. Document/defect answers are in `ground_truth.json`.", "",
            "## Planted data defects (for the data-science tools)", "",
            "- Supplier invoices: price and quantity mismatches against orders/DDT, missing DDT, duplicates.",
            "- Warehouse SQLite: items below minimum stock.",
            "- Receivables: overdue unpaid invoices.",
            "- Shift reports: TP-1 scrap rate roughly doubles after 14 September (spindle issue).",
            "- Maintenance: overdue preventive jobs (TP-1 spindle check overdue by 17 days).",
            "- Quality: NCs open for more than 30 days.",
            "- Week 41 plan: work orders finishing after the promised date.", "",
            "## Noise documents (should produce no opportunity)", "",
            *[f"- `{n}`" for n in self.truth["noise_documents"]], "",
            f"## Files ({len(files)})", "", *[f"- `{f}`" for f in files], "",
            "Regenerate: `uv run python -m benchmarks.datasets.generate_fictional_company [--seed N]`.",
        ]))
        (self.out / "ground_truth.json").write_text(
            json.dumps({**self.truth, "total_hours_per_week": total}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")

    def run(self) -> dict:
        self.docs.mkdir(parents=True, exist_ok=True)
        self.profile()
        self.board_minutes()
        self.purchasing_and_invoices()
        self.inventory()
        self.receivables()
        self.production()
        self.downtime_and_maintenance()
        self.quality()
        self.sales()
        self.noise()
        self.queries()
        self.readme()
        return self.truth


def generate(out: Path = DEFAULT_OUT, seed: int = 11) -> dict:
    return Gen(out, seed).run()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    truth = generate(a.out, a.seed)
    n = sum(1 for p in (a.out / "documents").rglob("*") if p.is_file())
    print(f"{COMPANY['name']}: {n} documents, {len(truth['tasks'])} planted tasks -> {a.out}")


if __name__ == "__main__":
    main()
