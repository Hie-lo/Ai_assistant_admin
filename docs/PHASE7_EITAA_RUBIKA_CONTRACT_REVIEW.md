# Phase 7 Contract Review — Eitaa & Rubika Bot APIs

- **Status:** DRAFT — pending owner decisions (see §7)
- **Date:** 2026-09-16
- **Scope:** Official API contract review, capability matrix, limits, and certification
  strategy for the Eitaa and Rubika platform adapters — same rigor as the Phase 6
  (Bale) review, per owner directive: *no adapter code until contracts are reviewed
  and ambiguities are resolved with the owner*.

## Sources (accessed 2026-09-16)

| Source | URL | Notes |
|---|---|---|
| Rubika official Bot API intro | https://rubika.ir/botapi | bot creation, base URL, event delivery |
| Rubika official Bot API methods | https://rubika.ir/botapi/methods | full method catalog (7 doc sections) |
| Rubika official Bot API models | https://rubika.ir/botapi/models | model definitions (not fully mined; see open items) |
| Rubka official Python SDK (PyPI) | https://pypi.org/project/Rubka/ | "provided by Rubika"; v8.1.7 |
| Rubka SDK source (GitHub) | https://github.com/Mahdy-Ahmadi/rubka | **MTProto-style session client, NOT a REST bot-API client** — do not conflate with the REST contract |
| Eitaa — official API | *none found* | 2 search passes; eitaa.com has no public bot API page |
| eitaayar.ir API doc (third-party) | https://eitaayar.ir/assets/download/API_eitaayar.ir.pdf | 3-method contract (full text captured in §3.2) |
| eitaayar.ir test endpoint | https://eitaayar.ir/testApi | for API sanity checks |

---

## 1. Method

1. Read the official documentation of each platform end to end.
2. Map every documented method to our `PlatformClient` protocol semantics
   (`get_me`, `get_chat`, `send_message`, `send_photo`, `send_media_group`,
   `edit_message_text`, `edit_message_caption`, `delete_message`, capabilities).
3. Record every **documented limit** and every **unknown** (unknowns become
   certification probes — never guesses).
4. Design a certification run (extension of `scripts/certify_platform.py`) that
   resolves each unknown against the live API before the platform is enabled.

---

## 2. Rubika (OFFICIAL API)

### 2.1 Bot creation and access

- Create the bot in the **Rubika app via BotFather** (https://rubika.ir/BotFather)
  → receive a bot token. (Same owner action as Bale.)
- Base URL: `https://botapi.rubika.ir/v3/{token}/{method}`
- Transport: **POST, `application/json` body** (all doc examples use
  `requests.post(url, json=data)`).
- Event delivery (V2/Phase 11+ concern, noted for completeness): long polling via
  `getUpdates(offset_id, limit)` or webhook via `updateBotEndpoints(url, type)`
  — webhook requires a public HTTPS endpoint.

### 2.2 Documented method catalog → our semantics

| Doc method | Our semantic | Notes |
|---|---|---|
| `getMe` | `get_me` | bot identity |
| `getChat(chat_id)` | `get_chat` | chat info (id, name, type). **No admin-status field documented** — see §2.6 |
| `sendMessage(chat_id, text, disable_notification?, reply_to_message_id?, chat_keypad?, inline_keypad?, metadata?)` | `send_message` | `text` = string. **Metadata model is index-based** (`meta_data_parts`: `{type: Bold|MentionText, from_index, length, mention_text_user_id?}`) — different from Bale markdown / Telegram entities. V1 sends plain text; do NOT use metadata. |
| `editMessageText(chat_id, message_id, text)` | `edit_message_text` | edits a bot-sent message in place |
| *(none)* | `edit_message_caption` | **No dedicated method.** Candidate: `editMessageText` on a media message (its `text` param) — **live probe P3** |
| `requestSendFile(type)` → `upload_url`; then **multipart POST** of raw file → `file_id`; then `sendFile(chat_id, file_id, text?, ...)` | `send_photo` | **2-step upload model** — see §2.3. `text` = caption alongside the file |
| *(none)* | `send_media_group` | **No album/media-group method documented** — live probe P5; fallback = owner decision Q2 |
| `deleteMessage(chat_id, message_id)` | `delete_message` | **No time limit documented** (unlike Bale's 48h) — optional 48h probe |
| `getUpdates` | — (out of V1 scope) | inbound events, Phase 11+ |
| `forwardMessage`, `setCommands`, `editMessageKeypad`, `editChatKeypad`, `getFile`, `banChatMember`, `unbanChatMember`, `updateBotEndpoints` | — (out of V1 scope) | admin/event/keyboard operations |
| `sendPoll`, `sendLocation`, `sendContact` | — (out of V1 scope) | not needed by the publication pipeline |

**Identifier type:** all `chat_id` / `message_id` fields are documented as
**`string`** (example values are numeric, e.g. `85917`). Adapter must accept
string IDs on the wire and convert at our protocol boundary (int where possible;
recorded as probe P0 detail).

### 2.3 Media pipeline — architectural difference from Telegram/Bale

- **Telegram/Bale:** the adapter passes a public URL; the *platform* fetches it
  server-side.
- **Rubika:** the *platform does not fetch URLs* (no URL-input documented for
  `sendFile`). Flow our server must perform:
  1. `requestSendFile(type="Image")` → receive `upload_url`
  2. download the media bytes from our product's media URL (our server → source)
  3. `POST` the raw bytes (multipart/form-data, field `file`) to `upload_url`
     → receive `file_id`
  4. `sendFile(chat_id, file_id, text=caption)` → `message_id`

Implications:

- Extra latency per media publish (2 HTTP round-trips before send); acceptable at
  V1 volume (starter plan: 2 channels, 3 syncs/day).
- Requires outbound HTTPS from our server to the media host — already true in
  production (the AI pipeline calls OpenRouter the same way).
- `FileTypeEnum` values: `Image` confirmed in docs; others (video/audio/document)
  not needed in V1.
- **File size limits: not documented** → probe P4 (use a realistic ~300–500 KB
  JPEG; record any error at larger sizes as optional).
- Direct-URL send is **not ruled out** (a non-documented input may exist) —
  probe P2 is informational only; the 2-step flow is the design baseline either way.

### 2.4 Limits

**Documented:** *none* for text length, caption length, file size, rate limits,
or delete age (checked intro + full methods page).

**Unknown → certification probes** (no guesses):

| # | Unknown | Probe |
|---|---|---|
| P0 | Error envelope shape (undocumented!) | send an intentionally invalid request (e.g. `sendMessage` missing `text`, then a bad-token call); record HTTP status + body; hardcode parsing to the *observed* shape in `_attach_detail` (same approach as Bale) |
| P1 | `text` max length | binary probe 512 → 1024 → 2048 → 4096 → 8192 |
| P2 | direct-URL `sendFile`? | informational: try `file_id=<url>`; expect/reject, record |
| P3 | caption edit = `editMessageText` on a media message? | sendFile → editMessageText on it → owner visually confirms |
| P4 | file size / type limits | upload test image (default size); optional larger size |
| P5 | album / media-group existence | try plausible method names (e.g. `sendMediaGroup`) against the API; record 404/error; **fallback per owner Q2** |
| P6 | delete age limit | optional 48h probe (flag, like Bale) |
| P7 | rate limits | optional burst probe (flag, like Bale) |

### 2.5 Error handling

Undocumented. We must **not** assume eitaayar/Telegram conventions. Certification
step order puts P0 *first*; the adapter ships with error parsing matched to the
observed envelope (status + body fields), and unit tests pin the mapping.

### 2.6 Admin verification (channel)

No `getChatMember` documented. Strategy: **behavioral proof** — in a Rubika
channel, only admins can post, so a successful `sendMessage` to the target
channel *is* the admin check (equivalent signal to Bale's live `getChatMember`
in Phase 6 run #6). `getChat` resolves the chat; failed send = not-admin error is
surfaced verbatim.

### 2.7 Rubika open items (all resolved by certification, none by guess)

P0–P7 above. No adapter constant may be set from assumption; every capability
flag and limit is set from the live run's recorded evidence (same rule as Phase 6).

---

## 3. Eitaa

### 3.1 Official status (finding)

- Eitaa has an in-app **@BotFather** (community-confirmed) → an official bot
  platform exists.
- **No official public Bot API documentation found** as of 2026-09-16
  (eitaa.com has no bot-API page; two search passes; every community guide routes
  through the third-party panel eitaayar.ir).
- **Owner action (≈2 min, decides the route):** open Eitaa → @BotFather → check
  whether creating a bot yields a token + API endpoint, and whether official API
  docs are linked. If yes → build against the official API (preferred). If no →
  the only public route is third-party (§3.2).

### 3.2 Third-party route: eitaayar.ir (آی‌تایار) — contract (from official PDF)

- Base: `https://eitaayar.ir/api/{TOKEN}/{METHOD_NAME}`
  - `GET` and `POST` both accepted; method name **case-insensitive**
  - 4 parameter styles accepted: URL query string, `application/json`,
    `application/x-www-form-urlencoded`, `multipart/form-data`
- **Every response carries an `ok` boolean** (true/false); on failure `ok:false`
  with an error message — Telegram-style envelope (the *only* documented error
  model for this route).
- Token + per-channel `chat_id` are provisioned **in the eitaayar panel**
  (register bot in panel → panel assigns the channel identifier; channel username
  without `@` also accepted as `chat_id`).
- Test endpoint: `https://eitaayar.ir/testApi`
- **Exactly 3 documented methods:**

| Method | Params | Output |
|---|---|---|
| `getMe` | — | `result: {id, is_bot, first_name, last_name, username}` |
| `sendMessage` | `chat_id`* (panel id or username w/o @), `text`*, `title?` (panel-only), `disable_notification?`, `reply_to_message_id?`, **`date?` (unix ts → scheduled send)**, **`pin?` (=1 → pin message)**, **`viewCountForDelete?` (auto-delete after N views)** | full message object: `{message_id, from, chat, date, text}` |
| `sendFile` (a.k.a. `sendDocument`) | `file`* (any file, multipart), `caption?` | full message object with `caption`. **Rename rules: GIF = send `.mp4` renamed `.gif`; sticker = send `.png` renamed `.webp`** |

### 3.3 Capability consequences of the third-party route

| Semantic | eitaayar | Consequence for the app |
|---|---|---|
| `send_text` | ✓ (limit **undocumented** → probe) | OK |
| `send_photo` (single media) | ✓ via `sendFile` upload (our server uploads the file) | same 2-step-style upload flow as Rubika |
| `send_media_group` (album) | **✗ not documented** | single photo only |
| `edit_message_text` | **✗ no method** | manual update = **always REPOST** (new message) |
| `edit_message_caption` | **✗ no method** | same |
| `delete_message` | **✗ no method** | old posts **cannot be removed remotely** after update/delete → owner deletes manually in the Eitaa app (same pattern as Bale's lingering-delete flow). `viewCountForDelete` is auto-delete-after-N-views — NOT a substitute for manual correction |
| `inspect_remote` | ✗ | not needed (V1 model) |
| admin verification | behavioral (successful send to the panel-bound channel) | same strategy as Rubika |

**Eitaa-only bonuses (Phase 8 candidates):** scheduled send (`date`) and pinning
(`pin`) — capabilities *none* of our other platforms offer in the bot API.

### 3.4 Third-party risk assessment

- **Uptime/availability:** publication availability depends on a third party
  (no SLA documented).
- **Data path:** text and channel identifiers transit eitaayar.ir.
- **Commercial terms:** panel pricing / ToS not documented in the PDF — owner to
  confirm on the panel (free tier? volume limits?).
- **Lock-in:** chat_ids come from their panel, not from Eitaa itself.
- **Mitigation:** if §3.1's in-app check finds an official API, prefer it and
  discard this route.

---

## 4. Capability matrix (V1 publication scope)

| Capability | Telegram | Bale | Rubika (official docs) | Eitaa via eitaayar |
|---|---|---|---|---|
| send_text | ✓ 4096 (doc) | ✓ 4096 (doc, live-verified) | ✓ **limit unknown** (P1) | ✓ **limit unknown** (probe) |
| single media (photo) | ✓ URL, ≤5 MB (doc) | ✓ URL, ≤5 MB (doc, live-verified) | ⚠ 2-step upload; size unknown (P4) | ⚠ file upload; size unknown (probe) |
| media group / album | ✓ 2–10 (doc+live) | ✓ 2–10 (**live-certified run #6**) | ✗ undocumented (P5; fallback = Q2) | ✗ |
| edit text | ✓ | ✓ (live-verified) | ✓ `editMessageText` | **✗** |
| edit caption | ✓ | ✓ (live-verified) | ⚠ via `editMessageText`? (P3) | **✗** |
| delete message | ✓ <48 h (doc) | ✓ <48 h (doc, live-verified) | ✓ no limit documented (P6 optional) | **✗** (manual in-app only) |
| remote text inspect | ✓ | ✗ | ✗ | ✗ |
| admin verification | `getChatMember` (doc) | `getChatMember` (doc, live-verified) | behavioral (successful post) §2.6 | behavioral (panel-bound channel) |
| scheduled send (Phase 8) | ✓ | ✓ (planned) | ? (undocumented) | ✓ `date` (bonus) |
| pin (Phase 8 candidate) | — | — | ? (undocumented) | ✓ `pin` (bonus) |

**Net effect:** Rubika ≈ full-parity with the current flow (pending P1–P5);
Eitaa (third-party) = send-only posting, updates always create new posts.

---

## 5. Certification strategy

Extend `scripts/certify_platform.py` with `--platform rubika | eitaa`
(same pattern as Phase 6: wire-format marker line at start, step-by-step PASS/FAIL,
automatic cleanup, owner-visual steps, final RESULT line).

**Rubika run (required order):**

1. wire-format marker + `getMe`
2. **P0 error-envelope probe first** (invalid request; record raw response)
3. `getChat` on the target channel
4. short `sendMessage` → **admin proof** (only admins can post to a channel)
5. P1 text-limit binary probe (cleanup each)
6. `requestSendFile(Image)` → download `--photo-url` on the server → upload → `sendFile` → **owner confirms photo landed**
7. P3 caption-edit probe (`editMessageText` on the media message → owner confirms)
8. P2 direct-URL probe (informational)
9. P5 album method-existence probe
10. `deleteMessage` on all test artifacts (owner confirms channel clean)
11. optional flags: P6 48h delete, P7 rate burst (never on by default)

**Eitaa run (if route approved; requires a dedicated test channel because
there is NO delete method — all test messages persist):**

1. marker + `getMe` (via token)
2. short `sendMessage` to the test channel (admin proof; owner confirms)
3. text-limit binary probe
4. `sendFile` photo + caption (owner confirms; verify gif/webp rename rules with
   sample files if media types expand)
5. document `pin`/`date` behavior (Phase 8 evidence; harmless in a test channel)
6. no cleanup step — channel is disposable/test-only

**Pass bar (same as Phase 6 §13):** a platform is enabled only for the
capabilities proven by its certification run; limits are set from recorded
evidence; anything unproven stays disabled.

---

## 6. Build plan (after owner decisions)

1. `app/infrastructure/platforms/rubika.py` + `fake_rubika.py` + unit &
   integration tests (current suite: 354 passed / 2 skipped)
2. `app/infrastructure/platforms/eitaa.py` + fake + tests (if route approved)
3. `platforms/base.py`: registry entries, capability flags from certified values
4. `publications.py`: per-capability routing (album → platform fallback; single
   media → `send_photo` 2-step flow)
5. `settings.py` / `.env.example`: `RUBIKA_BOT_TOKEN`, `EITAA_BOT_TOKEN`
   (env-only, same handling as BALE_BOT_TOKEN)
6. `certify_platform.py` extensions (§5)
7. `docs/IMPLEMENTATION_ROADMAP_V1.md` + `PROJECT_LOG.md` updates

---

## 7. Decisions needed from the owner

- **Q1 — Eitaa route:** official-check first (recommended) / third-party now / skip Eitaa
- **Q2 — Rubika album fallback** (only used if P5 shows no album support):
  first photo only (recommended) / one message per photo / text only
- **Q3 — Eitaa update semantics** (only relevant if Q1 = third-party now):
  accept permanent old posts (manual in-app delete) / don't enable Eitaa
- **Q4 — bot model & creation timing** for Eitaa/Rubika: shared org bot (as
  Telegram/Bale) — now or later?
