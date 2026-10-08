# Connectors: Telegram and Gmail

Connectors are how an automation sends something outside Husk: a message, an email, a PDF report. In an automation everything that tells someone something is a **notification node** (kind `notification`) with one channel: `desktop` (a notification on this computer, no connector), `telegram` or `gmail`; the PDF report is still made by a `pdf_report` action step, and the notification has an "attach the report" switch. Automations still run only on this computer; only what they send leaves it, **straight from this computer to Telegram or Google, not through Husk's servers**.

| | Telegram | Gmail |
| --- | --- | --- |
| Channel of a **notification node** (one channel per node) | `telegram` | `gmail` |
| What it uses | The customer's own bot (created with @BotFather): bot token + the chat that receives messages | SMTP (`smtp.gmail.com:465`) with a Google **App Password** |
| Set up in Husk | Token → **Detect my chat** (reads `getUpdates` after the user pressed Start) → verify and save | Address + App Password → verify (SMTP login) and save |
| Sends | Text (split at 4096 characters, one message per second) and PDF reports (`sendDocument`) | Text emails to one or more addresses, optional PDF attachment |
| Limits worth knowing | A group takes 20 messages/minute; messages are not end-to-end encrypted | Personal Gmail: about 500 emails/day; App Passwords need 2-Step Verification and are usually **not available on Google Workspace** accounts |

OAuth for Gmail (scope `gmail.send`) is the planned second method for Workspace customers; it needs Google's verification of Husk's app first.

## Where things live

* **Credentials**: the OS keychain only (`gateways/connectors/secrets.py`, service `ai.husk.desktop.connectors`, one entry per organisation and connector). They survive sign-out (they are the customer's own bot / account), can be removed in Settings, and are never in SQLite, API responses, logs or backups. Without a keychain (headless Linux) they last for the process, and the connector then reads as "not set up" after a restart.
* **Status**: table `connectors` (`state`: `not_configured` | `ready` | `error`, a hint such as `@husk_bot` or the Gmail address, a short `last_error` code). Emptied in exports; after a restore the status is derived from the credential.
* **Code**: `core/connectors/service.py` (configure, test, send, `kinds_used`, `changes_sends`), `gateways/connectors/{telegram,gmail}.py`, API `endpoints/v1/connectors/router.py`.

## Rules (in code, tested)

1. A connector is saved **only after the provider accepts it** (`getMe`, SMTP login).
2. **No secret, address, chat id, message or exception text** in a log, a run log or an API response. The Telegram token sits in every Bot API URL, so errors become short codes (`invalid_token`, `auth_failed`, `chat_not_found`, …) and the log scrubber also redacts `bot<id>:<token>`, OAuth query parameters and bearer tokens. httpx's own request logging is kept at WARNING.
3. A run log keeps `{connector, chars, recipients}` for a send, never the content.
4. **Prova never reaches third parties**: Telegram goes to the configured chat (the user's own), email only to the connector's own address with a `[Prova]` subject. Real runs send to the step's recipients: scheduled and file-triggered runs, and **Run now** in the editor (`POST /automations/{id}/run`, `RunTrigger.manual`), which asks for confirmation and lists the channels and addresses first. If you "tested" a Gmail step and the mail reached you instead of the address on the node, that is this rule, not a bug.
5. **A notification that reaches people outside this computer is always a proposal when the Builder adds or changes it** (`changes_sends`, Telegram and Gmail only; a desktop notification reaches nobody else): whatever the personality, the change is shown for acceptance first. The user's own edit in the node popup (below) applies at once.
6. A connector that is **not set up** at run time skips the step (`skipped`) and the run goes on; a send that **fails** fails the run and turns the connector to "needs attention".
7. Email recipients must be real addresses (a role such as "the purchasing manager" is skipped with a clear message). The popup validates them (at most 10) and the server checks them again.

## The notification node and the popup

On the canvas the notification node is drawn in the full accent colour; its chip shows the channel (and, for Gmail, the recipients). Clicking the chip opens the popup: choose **This computer / Telegram / Gmail** (with each connector's live state and a "Set up" button), the recipients and subject for Gmail, the message, and whether to attach the report. Save calls `PUT /api/v1/automations/{id}/nodes/{node}/notification`, which applies one `update_node` through `graph_ops` (only those five fields), saves a new version with the user as author, syncs the scheduler and raises the connector suggestion if the chosen connector is not set up. Old `notify` / `email` / `telegram` action nodes no longer exist (saved automations from before are ignored, not converted).

## The Builder and the suggestions

* The Builder is told, in every chat and every draft, which connectors exist and whether each is `ready`, `needs attention` or `NOT set up` (`AgentContext.connectors_block`). It may add a step for a connector that is not set up (and says so); it must not invent recipients.
* A connector that is not alive becomes **one** suggestion of kind `configure_connector` (per connector, not per automation), created when an automation that uses it is saved, activated, drafted by discovery, or when a send fails. "Set up" / "Reconnect" opens Settings → Connectors for that connector; the suggestion closes itself when the connector works.

## Where the user meets them

* **First login**: the popup "Configure your connectors!" (until closed once; stored per user as `connectors_onboarded`), unless everything already works.
* **Settings → Connectors**: status, **Guide** (a popup with the numbered steps and links), **Set up / Change**, **Send test**, **Remove**.
* The guides are in `app/i18n/locales` (`connectors.<kind>.step<n>_title/_body`, `link<n>`, `tip<n>`/`note<n>`), English and Italian; the URLs they open are in `app/frontend/src/lib/connectors.ts`.

## Privacy notes (for `compliance/gdpr_guide.md`)

Telegram and Google become channels through which customer data can leave the computer, **only** as the customer's own automations send it and only to accounts the customer set up. The privacy text in Settings says so. The company plan's "customer data stays local" rule and the GDPR data map should list this case (it is customer-controlled, not a Husk sub-processor). Telegram messages are not end-to-end encrypted: the in-app guide advises against sensitive personal data.
