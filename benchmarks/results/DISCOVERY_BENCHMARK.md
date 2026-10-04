# Discovery benchmark — agent loop vs one-shot LLM vs decision model (Jev)

2026-10-04 · fictional company Officine Valsorda (30 documents, `benchmarks/fictional_company/`) · real orchestrator, live models: LLM via OpenRouter (`openai/gpt-oss-120b` main, `openai/gpt-oss-20b` summaries), decisions via Venice AI (`jev-latest`). OpenRouter is used for benchmarks only; the product calls Regolo.ai. Same documents and provider for every run. **Caveat:** only the decision-model run used OpenRouter's throughput routing (`provider.sort=throughput`), which removed most of the LLM latency; the two earlier runs did not.

## Headline

| | Agent loop | One-shot LLM | Decision model (Jev) |
|---|---|---|---|
| Completed all 7 steps | yes | yes | yes |
| Model calls | 100 | 47 | 75 |
| Tokens in / out | 467,586 / 92,408 | 87,371 / 60,876 | 162,482 / 58,555 |
| Cost (USD, OpenRouter prices) | $0.0330 | $0.0123 | $0.0139 |
| Wall time | 1810 s | 548 s | 29 s |
| Planted tasks found | 7/9 | 8/9 | 6/9 |
| Hours/week proposed (planted: ~26) | 28.9 | 39.5 | 60.8 |
| Noise opportunities | 0 | 1 | 1 |
| Notes (with evidence) | 25 (25) | 28 (28) | 45 (45) |
| Automations drafted | 5 | 8 | 7 |

**Change from Agent loop to Decision model (Jev):** calls −25%, tokens −61%, cost −58%, time −98%.

For reference, the first real run on Regolo (agent loop, Oct 3): 127 calls, 957,716 in / 93,335 out, stopped at step 7 by Regolo's daily cap (402).

## Signal of the value provided

Assumptions: 46 working weeks/year, €30/hour fully loaded office cost. Hours are the model's own estimates for its opportunities; "matched" counts only opportunities that correspond to a planted task (checked against the ground truth), so it is the conservative figure.

| | Agent loop | One-shot LLM | Decision model (Jev) |
|---|---|---|---|
| Planted hours/week covered by matched opportunities | 21.2 | 24.9 | 18.8 |
| → hours/year | 975 | 1,145 | 865 |
| → €/year at €30/h | €29,256 | €34,362 | €25,944 |
| AI cost of the discovery run | $0.0330 | $0.0123 | $0.0139 |
| Value / AI cost | 952,986× | 2,994,197× | 2,002,640× |

The AI cost of a discovery is negligible next to the work it surfaces in either design. What the new design changes is **whether the run finishes inside a provider's quota and how fast the user sees the result**: the agent loop exhausted Regolo's daily trial cap before drafting the automations; the summary-based run needs a fraction of the tokens.

## Opportunities found

**Agent loop**

- Automazione completa della gestione fatture fornitori — 13.0 h/week · sources: Procedura_ciclo_passivo.docx, Verbale_riunione_direzione_2026-09-21.docx, Scadenzario_clienti_2026.csv
- Digitalizzazione e integrazione dei rapporti turno MOD‑PR‑02 — 5.42 h/week · sources: Istruzioni_rapporto_turno.txt
- Controllo e riordino automatico delle scorte di magazzino — 5.0 h/week · sources: Verbale_riunione_direzione_2026-09-21.docx, Magazzino_Gestio7_export.sqlite, Procedura_riordino_materie_prime.docx
- Gestione automatizzata delle Non Conformità (8D) — 3.0 h/week · sources: PQ-08_Gestione_non_conformita.docx, Verbale_riunione_direzione_2026-09-21.docx, Modelli_email_solleciti.txt
- Automazione dei solleciti di pagamento ai clienti — 2.5 h/week · sources: Modelli_email_solleciti.txt, Verbale_riunione_direzione_2026-09-21.docx, Scadenzario_clienti_2026.csv

**One-shot LLM**

- Automazione Gestione Movimenti di Magazzino e Riordino Settimanale — 9.0 h/week · sources: Movimenti_magazzino_settembre_2026.csv, Magazzino_Gestio7_export.sqlite, Scadenzario_clienti_2026.csv
- Automazione del ciclo passivo fatture fornitori (N4) — 7.0 h/week · sources: Procedura_ciclo_passivo.docx, Fatture_fornitori_2026_Q3.xlsx, Anagrafica_fornitori.csv
- Gestione solleciti e incassi clienti — 6.0 h/week · sources: Modelli_email_solleciti.txt, Email_ritardi_consegna_settembre.txt, Scadenzario_clienti_2026.csv
- Gestione non conformità e 8D — 5.0 h/week · sources: Registro_non_conformita_2026.xlsx, PQ-08_Gestione_non_conformita.docx, Fermi_macchina_2026.csv
- Automazione per la Raccolta, Trascrizione e Aggiornamento dei Rapporti di Turno — 5.0 h/week · sources: Rapporti_turno_settembre_2026.csv, Istruzioni_rapporto_turno.txt, IO-12_Attrezzaggio_plurimandrino.docx
- Pianificazione e monitoraggio produzione settimanale — 3.5 h/week · sources: Piano_produzione_settimana_41.xlsx, Calendario_manutenzioni_preventive.xlsx, Verbale_riunione_direzione_2026-09-21.docx
- Gestione offerte commerciali — 3.0 h/week · sources: Registro_offerte_2026.csv, Listino_prezzi_2026.xlsx, Ordini_acquisto_2026_Q3.xlsx
- Procedura attrezzaggio plurimandrino — 1.0 h/week · sources: IO-12_Attrezzaggio_plurimandrino.docx

**Decision model (Jev)**

- Automazione per ridurre le attività manuali ricorrenti (Back office, Amministrazione, Mag. — 14.7 h/week · sources: Verbale_riunione_direzione_2026-09-21.docx
- Gestione automatizzata della mensa aziendale — 9.9 h/week · sources: Regolamento_mensa.txt
- Automazione digitalizzazione e sintesi rapporti turno — 9.4 h/week · sources: Istruzioni_rapporto_turno.txt, Profilo_aziendale.md, Rapporti_turno_settembre_2026.csv
- Automazione controllo fatture passive – ciclo passivo — 8.6 h/week · sources: Procedura_ciclo_passivo.docx, Fatture_fornitori_2026_Q3.xlsx
- Automazione per analisi movimenti magazzino settembre 2026 — 5.2 h/week · sources: Movimenti_magazzino_settembre_2026.csv, Magazzino_Gestio7_export.sqlite
- Automazione riordino materie prime – ordine settimanale — 5.0 h/week · sources: Procedura_riordino_materie_prime.docx, Ordini_acquisto_2026_Q3.xlsx, Anagrafica_fornitori.csv
- Automazione per il monitoraggio qualità e gestione fornitori (Rete N2) — 4.8 h/week · sources: Manuale_qualita_estratto_rev9.pdf
- Automazione gestione non conformità (PQ‑08) — 3.2 h/week · sources: PQ-08_Gestione_non_conformita.docx, Registro_non_conformita_2026.xlsx

## Per step and per model

## Discovery benchmark — tokens, time, cost, quality

### Agent loop — live, openrouter

Models: main `openai/gpt-oss-120b`, summary `openai/gpt-oss-20b` · documents: 30 · status: **completed** · time: **1810.5 s** · calls: **100** · tokens in/out: **467,586 / 92,408** · cost: **$0.03301**

| Step | Status | Time (s) | Model | Calls | Tokens in | Tokens out |
|---|---|---|---|---|---|---|
| load | done | 0.0 | — | 0 | 0 | 0 |
| knowledge_base | done | 0.0 | — | 0 | 0 | 0 |
| extract | done | 112.2 | openai/gpt-oss-120b | 30 | 53,173 | 21,313 |
| repetitive | done | 1071.6 | openai/gpt-oss-120b | 56 | 367,194 | 39,591 |
| handoffs | done | 137.6 | openai/gpt-oss-120b | 1 | 2,866 | 6,769 |
| impact | done | 73.1 | openai/gpt-oss-120b | 1 | 11,502 | 3,477 |
| map | done | 415.9 | openai/gpt-oss-120b | 12 | 32,851 | 21,258 |

Quality vs ground truth: **7/9 planted tasks found** (recall 0.78) · hours estimated 28.9 vs planted 25.7 (matched: 36.9 vs 21.2) · noise opportunities: 0 · notes with evidence: 25/25 · automations drafted: 5

### One-shot LLM — live, openrouter

Models: main `openai/gpt-oss-120b`, summary `openai/gpt-oss-20b` · documents: 30 · status: **completed** · time: **547.9 s** · calls: **47** · tokens in/out: **87,371 / 60,876** · cost: **$0.01234**

| Step | Status | Time (s) | Model | Calls | Tokens in | Tokens out |
|---|---|---|---|---|---|---|
| load | done | 0.0 | — | 0 | 0 | 0 |
| knowledge_base | done | 0.0 | — | 0 | 0 | 0 |
| extract | done | 52.8 | openai/gpt-oss-20b | 19 | 20,212 | 10,055 |
| repetitive | done | 170.3 | openai/gpt-oss-120b | 1 | 3,187 | 1,610 |
| handoffs | done | 25.3 | openai/gpt-oss-120b | 1 | 1,112 | 1,620 |
| impact | done | 69.7 | openai/gpt-oss-120b | 3 | 6,838 | 5,917 |
| map | done | 229.7 | openai/gpt-oss-120b | 22 | 55,385 | 41,170 |

Slowest documents in step 3: Verbale_audit_interno_2026-09.docx 25.01 s (fallback), Politica_ambientale_2026.pdf 25.01 s (fallback), Verbale_riunione_direzione_2026-09-21.docx 25.01 s (fallback), Profilo_aziendale.md 20.58 s (openai/gpt-oss-20b), Email_ritardi_consegna_settembre.txt 18.28 s (openai/gpt-oss-20b)

Quality vs ground truth: **8/9 planted tasks found** (recall 0.89) · hours estimated 39.5 vs planted 25.7 (matched: 44.5 vs 24.9) · noise opportunities: 1 · notes with evidence: 28/28 · automations drafted: 8

### Decision model (Jev) — live, openrouter

Models: main `openai/gpt-oss-120b`, summary `openai/gpt-oss-20b` · documents: 30 · status: **completed** · time: **28.7 s** · calls: **75** · tokens in/out: **162,482 / 58,555** · cost: **$0.01393**

| Step | Status | Time (s) | Model | Calls | Tokens in | Tokens out |
|---|---|---|---|---|---|---|
| load | done | 0.0 | — | 0 | 0 | 0 |
| knowledge_base | done | 0.0 | — | 0 | 0 | 0 |
| extract | done | 8.5 | openai/gpt-oss-20b | 32 | 38,048 | 10,850 |
|  |  |  | openai/gpt-oss-120b | 3 | 4,711 | 1,815 |
| repetitive | done | 1.6 | jev-latest | 3 | 30,282 | 3,181 |
| handoffs | done | 0.6 | jev-latest | 9 | 10,310 | 628 |
| impact | done | 3.0 | openai/gpt-oss-120b | 8 | 18,174 | 7,440 |
| map | done | 15.0 | openai/gpt-oss-120b | 20 | 60,957 | 34,641 |

Slowest documents in step 3: Istruzioni_rapporto_turno.txt 7.13 s (openai/gpt-oss-120b), Politica_ambientale_2026.pdf 6.51 s (openai/gpt-oss-120b), Modelli_email_solleciti.txt 5.36 s (openai/gpt-oss-120b), Organigramma.md 3.7 s (openai/gpt-oss-20b), Verbale_audit_interno_2026-09.docx 3.13 s (openai/gpt-oss-20b)

Quality vs ground truth: **6/9 planted tasks found** (recall 0.67) · hours estimated 60.8 vs planted 25.7 (matched: 55.8 vs 18.8) · noise opportunities: 1 · notes with evidence: 45/45 · automations drafted: 7

## Method and limits

- One live run per design: model outputs vary between runs; treat differences of ±1 task as noise.
- Quality matching: an opportunity counts for a planted task when it cites at least one of the task's documents and shares a keyword with the task title (or cites two of its documents).
- Cost uses OpenRouter's list prices at run time; Regolo's pricing differs.
- Reproduce: `uv run python -m benchmarks.bench_discovery_tokens --live --label <name>` then this script.
