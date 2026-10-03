# The harness — how the agents think and remember

Oct 3, 2026 · @Marco

The harness is the set of rules around the LLM that makes discovery and editing reliable: what the agents read, in which order, what they may write, and what is enforced in code rather than hoped for in a prompt.

## Agents

| Agent | Role (plan name) | Code |
| --- | --- | --- |
| **Husk Op** | Coordinates a discovery run: 7 checkpointed steps, resume, self-heal | `core/agents/orchestrator.py` |
| **Explorer** | Discovery research (Scout): signal ranking, deep research, handoffs, impact | `core/agents/explorer/` |
| **Builder** | Drafts automations and edits them via chat (Automator / "Assistente Husk") | `core/agents/builder/` |

Agents are **internal**. Users don't build agents in this phase. Both agents use LangChain chat models with an explicit tool loop (`harness/loop.py`): hard iteration caps, tool errors fed back to the model, events streamed to the UI.

## Three markdown artefacts (all persisted in SQLite, versioned)

1. **Company profile** — at most **30 facts**, each with importance, category and evidence.
   * Near-duplicate → the existing fact is reinforced (references +1, evidence merged).
   * Free slot → added.
   * Profile full → the candidate is a **side fact**: it replaces the weakest fact (lowest `strength()` = importance + references + evidence) **only if the judge says it is more important or relevant**. The replaced fact goes to `fact_history`. The cap is enforced in `CompanyProfileManager`, not in the prompt.
2. **Network notes, one network per automation** — atomic notes (`NetworkNote`): title, complete claim, frequency, actors (departments, never names), evidence (document + chunk/sheet + quote), links to other notes, estimated hours.
3. **Network summary, one per automation** — a short markdown map of the documents and tasks the automation touches, citing notes as `[[note_id]]`.

### Note integrity invariant

A note is **always complete**. `NoteValidator` rejects truncated claims, placeholders (`TODO`, `…`, `{{`), missing evidence, evidence on unknown documents and missing frequency for tasks. Notes are **rewritten whole, never appended**. A rejected note is not stored, and the agent gets the exact errors back as a tool error and must rewrite it. Dangling links are dropped, so a network is never "broken".

## Discovery: the 7 steps

| # | Step (UI label) | What happens | Checkpoint granularity |
| --- | --- | --- | --- |
| 1 | Caricamento sicuro dei documenti autorizzati | Only documents with explicit authorisation | step |
| 2 | Costruzione della base di conoscenza | BM25s index rebuilt | step |
| 3 | Estrazione di procedure, ordini e distinte base | Per document: heuristic signal + LLM analysis (type, department, signal, ≤6 facts → profile) | **per document** |
| 4 | Ricerca di attività ripetitive | For the top-ranked documents: one **candidate network** each; Explorer researches with tools (BM25, data tools, notes) and writes notes | **per document** |
| 5 | Mappatura dei passaggi di dati tra reparti | Cross-network links between notes of different documents/departments | step |
| 6 | Stima dell'impatto | Opportunities proposed from all notes; for each, a **new kept network** is created by cloning its notes; exploratory networks are discarded | step |
| 7 | Generazione della mappa | Builder drafts each automation graph (validated, retried once with the validation error), writes the network summary | **per automation** |

Signal ranking = `0.4 × heuristic + 0.6 × LLM`. The heuristic layer (`explorer/Rag/signal.py`) counts frequency words, manual work, handoffs and explicit rules; for tables it adds periodicity and missing-data signals.

Failures: network errors → status `waiting_network`, retry every 30 s; other errors → Studio self-heal (if alive), then `failed` with resume available. Maintenance → `paused`.

## Retrieval order for the Builder (after discovery)

1. Company profile (always)
2. This automation's network summary (always)
3. This automation's notes — **only when the request touches other documents** (`builder/Rag/context.needs_notes`)
4. BM25 search and data tools on demand

## Data-scientist tools (both agents)

`list_sheets`, `describe_table`, `filter_rows`, `aggregate` (with group by), `value_counts`, `detect_periodicity`, `compare_versions` (e.g. BOM rev B vs C), `missing_values`, `read_cells`. CSV, Excel and SQLite files; numbers read from the file, never from flattened text; outputs size-capped.

## Suggestions after documents change

Discovery is a full pass. After it, documents keep arriving: `core/suggestions/engine.py` keeps the harness current **without changing anything by itself**.

* **Upload** → `SuggestionQueue.enqueue`. One job per organisation runs `HUSK_SUGGESTIONS_DEBOUNCE_S` (15 s) after the *last* upload, so a folder sent in several requests is analysed once. The job waits while a discovery runs and skips documents the latest discovery already analysed.
  1. Explorer `analyze` on each new document → company profile updated through the ≤30-fact rule. Any added/replaced fact becomes one informational `memory_update` suggestion.
  2. Documents with signal ≥ 0.15 → BM25 shortlist of automations (shared vocabulary with their documents, or title words), at most 6 → Explorer `suggest_changes` (structured) proposes `modify_automation` / `new_automation`.
  3. **Code-side checks**: evidence must cite real chunks of *that* document (a quote not found verbatim is replaced by the passage), confidence ≥ 0.6, known automation id, no duplicate open suggestion.
  * No completed discovery yet → a single `run_discovery` suggestion, no LLM call. No AI key or a blocking maintenance window → no job.
* **Delete** → `source_deleted` for every automation whose documents or notes cite the file (fixed localized prompt, no LLM); open suggestions based on that file are dismissed.
* **Applying**: `new_automation` → research into a new network + Builder draft (like discovery step 7, `run_id = None` so "Analyze again" never retires it). `modify_automation` / `source_deleted` → only through the Builder chat (below). An accepted proposal marks the suggestion applied and, for `modify_automation`, researches the new document into the automation's network (notes rewritten whole) and refreshes its summary.

### Suggestion messages vs user messages

A suggestion's pre-made prompt is sent with `suggestion_id`. The server uses it only if the text is **unchanged**; edited text is the user's own request.
For a suggestion message the Builder gets `SUGGESTION_RULES` (it was generated from a document, not written by the user; verify the cited passages first; change only what the evidence supports; otherwise change nothing and say why; cite the passage for each change) and the message is tagged `[Husk suggestion — … NOT written by the user]`, also when replayed from history.
**The result is always a proposal, whatever the personality**: nothing changes until the user clicks Apply.

## Personalities

A selector (chat header + Settings) that changes one prompt fragment: **Bilanciato**, **Ultra-prudente** (every change becomes a proposal the user must apply), **Esplicativo**, **Conciso**. Add one in `harness/personalities.py`.

## Editing rules

The Builder changes automations **only** through typed operations (`add_node`, `update_node`, `remove_node`, `connect`, `disconnect`, `set_trigger`, see `builder/graph_ops.py`). Each operation is validated by the `AutomationGraph` model (one trigger, no loops, labelled branches) and saved as a new version. "Annulla" restores the previous version.

## Research pointers (for the next iteration)

* **Contextual chunk headers / contextual retrieval** (Anthropic, 2024): prepend a one-line document context to each chunk before BM25 indexing. Cheap and known to improve keyword recall on short chunks.
* **Reciprocal-rank fusion of BM25 + Serenity navigation**: once Serenity serves "which chunk answers this", fuse its ranking with BM25 instead of filtering.
* **Self-RAG-style "enough evidence?" gating**: the plan's Serenity yes/no on evidence maps directly to this. Wire it before the LLM reads chunks.
