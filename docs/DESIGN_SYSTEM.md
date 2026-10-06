# Design system

Oct 3, 2026 · @Marco · reference: `Company_memory/HuskAI/pdf/Husk_AI_Prototipo_Schermate.pdf` (19 screens × light/dark)

Designed from scratch for Husk. The flow editor sits on **React Flow (`@xyflow/react`, MIT)**. No code or assets from n8n (Sustainable Use License) were used.

## Tokens (`app/frontend/src/styles/tokens.css`, sampled from the prototype)

| Token | Light | Dark |
| --- | --- | --- |
| `--bg` (canvas/page) | `#f6f5fa` | `#111016` |
| `--surface` (sidebar, cards) | `#ffffff` | `#1a1921` |
| `--text` | `#15141a` | `#f2f1f9` |
| `--accent` | `#7456f6` | `#8c71ff` |
| `--accent-soft` (active nav, tiles) | `#efebfb` | `#26203b` |
| `--primary` (buttons) | `#15141a` | `#f2f1f9` |
| `--success` (FATTA) | `#2f8f6a` | `#5cc09a` |

Type: **Barlow Condensed** (display), **Hanken Grotesk** (body), **JetBrains Mono** (paths, chips, meta). Fonts are bundled with `@fontsource` (no Google Fonts call, so it works offline and avoids sending IPs to Google).

## Brand mark

Official Husk AI logo (3D purple mark), exported from the source artwork into `app/frontend/public/brand/husk-logo-{32,64,128,256,512}.png`. Each export is cropped to the mark, keeping only a thin margin of its glow, so it stays legible at 20–30 px. `favicon.png` is the 64 px export. The `Logo` component picks the size, with a 2× variant for retina screens. To update it, re-export from the source PNG at the same sizes.

## Patterns

* Sidebar: Automazioni / Conoscenza with the 3 latest items, Discovery with state badge (DA FARE · IN CORSO · FATTA), Impostazioni, user card.
* Top bar: mono breadcrumb path (`automazioni/…`), then the **light bulb** (Suggestions: accent dot when unread, soft pulse while Husk analyses) and the theme toggle.
* Snackbars (`lib/notifications.tsx`): dark pill at the bottom centre, optional action ("View"), close button, 6 s, at most 3 stacked.
* Suggestion cards: kind chip, title, body, evidence as an accent left-bordered quote with the file name, mono meta, actions on the right (primary first, "Ignora" ghost).
* Husk Studio: `StudioAction` next to every blocking error ("View in Husk Studio" outlined accent; "Install Husk Studio" smaller, dashed, muted); a collapsible sidebar card above Settings with a status dot (green = connected); a pinned advice card on Suggestions; "Diagnose with Husk Studio" button only when Studio's MCP is up; self-heal messages are dashed bubbles labelled "Husk Studio diagnosis".
* Chat: a Husk suggestion is a dashed-accent bubble labelled "Suggerimento di Husk" with document chips, never the user's filled bubble; the composer shows the same dashed frame while a suggestion is pre-filled; proposals from a suggestion carry a small tag.
* Page header: eyebrow, 52 px condensed title ending with a full stop, lead paragraph.
* Row cards: index number, icon tile, title + mono chip, description, mono meta line, status dot.
* Editor: dotted canvas, square icon tiles for trigger/condition/action, wide card for AI agent nodes with satellite attachments (modello, memoria, strumenti), dashed purple edges with `vero/falso` labels, zoom pill bottom-left, chat on the right with suggestion chips and a selected-node context line.

## Differences from the prototype (intentional, MVP rules)

| Prototype | MVP |
| --- | --- |
| Email + password form, "Continua come ospite" | One "Continue with your Husk account" button: the system browser opens Clerk, a "you can close this tab" page (`/auth/done`, same design) confirms, and the app window continues by itself. No guest |
| "Primo accesso" profile choice, Tecnico/Non tecnico | Removed (no distinction in this phase) |
| Floating "+" (new agent / manual automation) | Removed (no manual automations). The one manual control is the notification node's chip → popup (channel, recipients) |
| AI agent items (Assistente Qualità) | Not generated in this phase |
| Carica = local server, Attiva = production | Carica = saved locally, Attiva = scheduled on this computer |
| Settings "Area di lavoro del prototipo" | Replaced by **Backup completo** (Esporta / Importa) |
| "CONSERVAZIONE ZERO" | "DOCUMENTI IN LOCALE" plus the precise privacy sentence (GDPR G3) |
| — | Personality selector (chat header + Settings) |
| Italian-only copy | English (default) + Italian from `app/i18n`; language picker in Settings and in the login corner |
