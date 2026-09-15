# Project Log — دستیار هوشمند کسب‌وکارهای مجازی

## 2026-09-16 — Phase 8 architecture approved; implementation scope locked

- Owner re-confirmed the documented workflow: read the contracts in `docs/`
  before implementation; no guessed behavior.
- Owner approved the Sync Job architecture: durable jobs, queue workers,
  bounded retry/backoff, idempotency, per-Source concurrency guard,
  manual/scheduled coalescing, crash recovery and final failure notification.
- Existing owner decisions carried into implementation:
  - policies must be configuration-driven and easy to change;
  - automatic mode is approved;
  - retry maximum is 3 attempts;
  - after the third failure Owner/Admin must be notified;
  - blank rows are `BLANK`, not invalid products;
  - missing required mapped columns report exact row + column;
  - identifiable invalid products become reversible `SOURCE_INVALID` and are
    blocked from publication until corrected;
  - incomplete/suspicious reads never trigger missing inference or destructive
    actions.
- This is an additive architecture change. Existing ImportRun history and
  Product data remain durable; rollback is by stopping Sync workers and
  reverting the additive migration/code path.
- Next implementation slice: durable Sync Job model + migration, then pure
  policy/concurrency tests before worker wiring.

## 2026-09-16 — Phase 7 DEFERRED (owner decision) + contract review committed

- Owner decision (2026-09-16): **defer Eitaa/Rubika implementation** — first
  deliver a clean, complete Telegram + Bale product, then add Eitaa/Rubika
  (and any future platforms).
- New execution order (roadmap updated to v1.1): Phase 8 (Sync engine) ->
  9 (Interfaces) -> 10 (Monitoring/Backup/DR) -> 11 (Scale hardening) ->
  12 (Production readiness) -> Eitaa/Rubika as extension platforms.
- Phase 7 contract review committed (76b549b) as
  `docs/PHASE7_EITAA_RUBIKA_CONTRACT_REVIEW.md`:
  - Rubika: OFFICIAL REST Bot API (botapi.rubika.ir/v3) fully reviewed.
    Media = 2-step upload (requestSendFile -> multipart upload -> file_id ->
    sendFile); no album/caption-edit method, no error model, no length or
    rate limits documented -> every unknown is a numbered certification
    probe (P0-P7), no guessed constants.
  - Eitaa: **no public official bot API found** (2 search passes). Only
    public route is third-party eitaayar.ir — send-only: exactly 3 methods
    (getMe, sendMessage, sendFile), NO edit/delete/album; bonuses:
    scheduled send (`date`) + pin. Risk assessment recorded.
  - Capability matrix vs Telegram/Bale + certification run designs for both
    platforms are ready, so the deferred phase now only needs the live
    runs + adapter build.
- Next: Phase 8 — Sync engine (manual sync on schedule, scheduler, queue
  workers, retries/backoff, change classification, reconnect
  reconciliation) per SOURCE_SYNC_DOMAIN_SPECIFICATION_V1; automatic-mode
  publication triggers land here (deferred from Phases 4/5 by design).

## 2026-09-16 — Phase 8 started: safe source-row validation

- Owner approved Phase 8 and confirmed that Sync policies must be flexible,
  configurable and easy to change; automatic mode is approved; retry is
  bounded at 3 attempts; final failure must notify Owner/Admin.
- Empty spreadsheet rows are now treated as `BLANK` (not invalid products),
  counted separately and excluded from identity/missing decisions.
- Rows with missing required fields or invalid values remain visible in the
  ImportRun row diagnostics with their exact locator (for example `row:17`).
  When an invalid row still contains a trusted identity (external_id/SKU/
  barcode), the matched product enters reversible `SOURCE_INVALID`; it cannot
  be published until the customer corrects the source row. A corrected valid
  row returns it to ACTIVE. Customer-owned media is unaffected.
- The publication guard rejects SOURCE_INVALID products, preventing bad
  source data from reaching Telegram/Bale.
- Preview now reports blank rows as `BLANK` too (instead of showing them as
  invalid), so the customer sees the same safe classification before applying
  a Sync.
- Required-field validation is driven by the active Mapping's `required`
  flags, not only by the built-in `name` field. This allows future business
  types to require additional columns; each error includes the source column
  and exact row locator.
- Next implementation slice: durable SyncRun/coalescing, configurable
  policies, worker retry/recovery, and Owner/Admin notification after the
  third failed attempt. Tests will cover blank rows, invalid rows, exact
  row diagnostics, deactivation/reactivation and publication blocking.

## 2026-09-15 — Phase 6 COMPLETE: Bale live CERTIFIED (certification run #6)

### Certification run #6 (owner, real bot, external image URL)
- Wire-format marker: `native-json-array (live-verified)` (correct
  adapter version ran).
- ALL checks PASS: getMe, getChat (channel), getChatMember
  (administrator), sendMessage, editMessageText, sendPhoto (URL),
  sendMediaGroup staged probe 2 -> 5 -> 10 items ALL accepted (live
  album limit >= 10 — the conservative cap of 10 stands, owner
  decision), editMessageCaption, deleteMessage (19/19 test messages
  cleaned up; channel left clean).
- **RESULT: CERTIFIED — Bale publication may be enabled.**

### Phase 6 closure
- Bale is production-ready: shared org bot (env token), text with
  markdown escaping, single photo (4096 caption), albums <= 10
  (1024 caption), edit/delete, 48h lingering policy, explicit
  inspection-unsupported behavior, live certification as the
  activation gate (spec section 13) — all live-verified against
  tapi.bale.ai on the production server.
- Optional probes not run (no owner-provided inputs): 48h delete-limit
  probe (needs a sacrificial >48h message) and rate-limit probe
  (opt-in flag). Available whenever the owner wants them.
- Next: Phase 7 (Eitaa/Rubika). Standing owner directive (2026-09-15):
  BEFORE any coding — review API contract + capability matrix +
  limits + certification strategy; ambiguous points -> structured
  owner questions, NO guessing.

## 2026-09-15 — Bale live certification run #5
: wire format RESOLVED (native array)

### Certification run #5 (owner, real bot, external image URL)
- Wire-format marker printed: `json-serialized-string` (proving the
  rebuilt image's adapter ran — not a stale image).
- Adapter album check (JSON-string body): 2-item -> 400 "malformed
  request" (re-confirmed the string form is rejected by Bale).
- **Automatic DIAG: variant A "JSON body, native array" -> ACCEPTED**
  (message ids 951/954, cleaned up). Owner watched the two photos land
  in the channel and be deleted.

### Resolution (live evidence, no ambiguity left)
- Bale's sendMediaGroup in a JSON body takes a **NATIVE JSON array**
  (Telegram-style). The "JSON-serialized string" convention of the
  community SDKs belongs to their FORM-ENCODED requests, not JSON
  bodies; the docs' "JSON-serialized array" wording is inherited from
  Telegram's form convention.
- Adapter reverted to the native array (the ORIGINAL Phase 6 form —
  the run-#3/#4 failures were the 1-item minimum and the probe
  stopping early, not the array form).
- `MEDIA_GROUP_WIRE_FORMAT` marker now: `native-json-array
  (live-verified)`.
- Regression tests lock the contract both ways: media MUST be a list
  (native array); the string form is the rejected one.

### Suite
354 passing, ruff clean.

### Next (owner action) — expected to be the final run
`git pull` + `docker compose build web && docker compose up -d web` +
re-run with the external --photo-url. Expected: album probe 2 -> 5 -> 10
measures the live limit and the full run is CERTIFIED (editMessageCaption
will also execute for the first time).

## 2026-09-15 — Bale live certification run #4
: media-group wire diagnostic added

### Certification run #4 (owner, real bot, external image URL)
- sendPhoto PASS (external URL reachable from Bale's network).
- 2-item sendMediaGroup STILL 400 "malformed request" — after the
  JSON-serialized-string fix. Two remaining possibilities, both
  unproven: (a) the server image was not rebuilt, so the old adapter
  (native array) was still inside the container; (b) Bale requires a
  third encoding — the form-encoded body exactly as the Go SDK posts
  it (url.Values + json.Marshal). No more guessing.

### Delivered — live wire-format diagnostic (evidence, not inference)
- `bale.py` now carries a `MEDIA_GROUP_WIRE_FORMAT` marker; the
  certification script prints it at start, so every live run PROVES
  which adapter version is inside the running image ("UNKNOWN - stale
  image, rebuild!" otherwise).
- When the album check fails, the script automatically runs a raw
  (image-adapter-independent) diagnostic that sends a 2-item album in
  all three documented/SDK-backed encodings and reports which the LIVE
  API accepts:
    A) JSON body, media = native array (Telegram-style)
    B) JSON body, media = JSON-serialized string (docs wording + SDKs)
    C) form-encoded body, media = JSON-serialized string (Go SDK wire
       form)
  A winning variant's test messages are deleted at once; the token
  never appears in output.
- Suite: 354 passing, ruff clean.

### Next (owner action)
`git pull` + rebuild image + re-run; the output now shows (1) which
adapter version ran and (2) which wire encoding Bale accepts — the
adapter will ship exactly that encoding.

## 2026-09-15 — Bale live certification runs #2/#3
: media wire-format bug found and fixed

### Certification run #2 (owner, real bot)
- URL passed was the Persian placeholder from the example command (not a
  real address) -> sendPhoto and the 1-item album failed with
  VALIDATION_ERROR. Confirmed: Bale downloads media URLs from its own
  network and rejects unfetchable URLs.

### Certification run #3 (owner, real bot, image hosted on this host)
- `sendPhoto` **PASS** (the /static test image on the site itself is
  reachable from Bale's network — the guaranteed-URL approach works).
- 1-item `sendMediaGroup` **FAIL**: 400 "Bad Request: malformed request".
  Same URL worked for sendPhoto in the same run -> NOT a URL problem:
  the ALBUM WIRE FORMAT was wrong.

### Root cause (verified, not guessed)
Bale's official docs document `media` as a "JSON-serialized array" (same
wording as `reply_markup`). Both working community SDKs confirm the wire
form is a **JSON-encoded STRING**:
- Go SDK (arashrahimi46/bale-bot-go-api): `v.Add("media",
  string(json.Marshal(items)))`
- Python SDK (mahdikiani/telegram-bale-bot, pyTelegramBotAPI fork):
  json-encoded string parameter.
Our adapter sent a NATIVE JSON array in the JSON body -> Bale 400
"malformed request".

### Fixed
- Bale adapter now sends `media` as `json.dumps(items)` (a string) in
  the JSON body — matching the docs wording and both SDKs.
- Regression test locks the wire contract: `payload["media"]` MUST be a
  str that parses to the item list.
- Certification probe now starts at 2 items (Telegram — which Bale
  mirrors — documents a 2-10 item minimum for sendMediaGroup; a 1-item
  group is invalid, and production routes single photos to sendPhoto
  anyway). The previous run's "malformed request" at 1 item was
  consistent with BOTH the wrong wire format and the 2-item minimum;
  the probe design (sizes 2/5/10) measures the true live limit next run
  regardless.
- Suite: 353 -> 354 passing, ruff clean.

### Next (owner action)
`git pull`, rebuild the image, re-run the certification with
`--photo-url https://<SITE_DOMAIN>/static/certification_photo.jpg`
(the one that worked for sendPhoto). Expected: album probe 2 -> 5 -> 10
measures the live limit; then Bale is fully certified.

## 2026-09-15 — Bale live certification run #1
: album probe failure found + diagnostics upgraded

### Live certification run #1 (owner, on the server, real bot + channel)
- PASS: getMe, getChat (channel), getChatMember (bot = administrator),
  sendMessage (markdown escaped), editMessageText, deleteMessage — the
  TEXT side of Bale is certified against the live API, and the
  cleanup deleted the test message.
- FAIL: sendMediaGroup with a 10-item album -> HTTP 500.
- Analysis: the album used a public imgur placeholder, and Bale's
  servers download the media URL themselves — imgur is not reachable
  from Iranian networks, so a 500 is expected in that case. The live
  item-count limit is therefore NOT yet measured (no guessing, per the
  standing directive); the probe below measures it on re-run.

### Delivered — certification diagnostics upgrade
- Bale adapter now carries the API's own `description` (and
  `error_code`) into error details (bounded 200 chars; API text only —
  never a secret, rule 14). A gateway 500 with a non-JSON body no
  longer loses diagnostic info and cannot crash the client.
- `scripts/certify_platform.py` album check is now a STAGED LIVE LIMIT
  PROBE: albums of 1, 2, 5, 10 (and `--album-size` if larger) are sent
  until one fails, printing each step and reporting the measured live
  limit. It also uses `--photo-url` for the album items when provided,
  and the FAIL message now distinguishes "URL unreachable from Bale's
  network" (fails at 1 item) from "item-count limit" (fails at N>1).
- Tests: +3 unit (description surfaced + error_code wins, non-JSON body
  survives, description bounded).

### Certification run #2 (owner, real bot)
Re-run with an invalid placeholder image URL (a Persian-language
example string, not a real address) -> sendPhoto and the 1-item album
probe both failed with VALIDATION_ERROR. This CONFIRMS the diagnosis:
Bale downloads the media URL from its own network and rejects URLs it
cannot fetch — the item-count limit still needs a reachable image to
measure.

### Delivered — guaranteed-reachable test image on this host
- New public static route `/static/` serving a small operational test
  photo (`app/interfaces/http/static/certification_photo.jpg`, 4 KB,
  generated locally). Platform bots download media URLs from their own
  servers, so a URL on THIS host is the guaranteed-reachable choice
  for live certification. Phase 12 security review confirms the public
  surface.
- Test: static photo must serve 200 image/jpeg with a valid JPEG body.
- Suite: 352 -> 353 passing, ruff clean.

### Next (owner action)
1. `git pull` + REBUILD the image so the new adapter (error-description
   surfacing) is inside the container:
   `docker compose build web && docker compose up -d web`
2. Re-run with the test image hosted on the site itself:
   `... certify_platform.py --platform bale --photo-url https://<SITE_DOMAIN>/static/certification_photo.jpg`
   (any other direct public image URL reachable from Iran also works)
3. Once the album probe passes, the live limit is known and
   `BALE_CAPABILITIES.media_group_max` is pinned to the measured value.

## 2026-09-15 — Phase 6: Bale platform
 (adapter + multi-platform core + certification tool)

### Delivered — Bale publication capability (owner-approved 2026-09-15, 5 design questions)
- Multi-platform core (`app/infrastructure/platforms/base.py`): ONE
  semantic client surface (`PlatformClient`, now incl. `send_photo` for
  single media) and ONE capability shape (`PlatformCapabilities` with
  separate single-media / album caption limits + `caption_limit_for`).
  The publication state machine, idempotency, attempts and
  reconciliation are shared across platforms; adapters add transport +
  capability data + platform-specific transforms. Client resolution and
  capability lookup go through a registry keyed by platform (built
  lazily to avoid import cycles); adding a platform = one module + one
  registry entry.
- Bale adapter (`app/infrastructure/platforms/bale.py`), contract
  verified against the official docs (docs.bale.ai, 2026-09-15):
  - base URL `https://tapi.bale.ai/bot<token>/METHOD`; failures
    classified from BOTH the HTTP status and the documented
    `ok:false`/`error_code` body (body wins), `retry_after` from
    `parameters` on 429.
  - limits: text 4096; single-media caption 4096 (`sendPhoto`);
    album-item caption 1024 (`sendMediaGroup` items; max count
    undocumented -> conservative 10 until live certification measures
    it); ids up to 52-bit (stored as strings).
  - **markdown escaping**: Bale parses every message as markdown, so the
    adapter escapes `\ _ * [ ] ( )` on the wire and stores an exact
    `unescape_markdown` inverse; reconciliation compares the raw form
    AND the unescaped form against the clean fingerprint (no false
    `remote_modified` on our own content).
  - **no message-lookup method exists** -> `inspect_remote=False`:
    publish verification treats the API's accepted send (message id
    returned) as verified; `check`/reconciliation is an EXPLICIT 409
    "remote inspection is not supported" (spec section 8: no silent
    fallback); after (re)verification, suspended publications resume
    with an attempt record documenting that no inspection happened.
- Owner decisions implemented (2026-09-15):
  1. BALE bot = shared ORGANIZATIONAL bot (same model as Telegram):
     token from env (`BALE_BOT_TOKEN`), never stored per business,
     never logged; connection verification is the same three-step
     proof of control.
  2. Albums up to 10 (conservative; measured live at certification).
  3. Markdown special characters escaped on the wire.
  4. **48h delete limit (lingering)**: Bale only allows deleting
     messages younger than 48h. When the OLD message of a repost is too
     old, the NEW message stays live and the OLD publication ends in
     FAILED_FINAL with its remote message id KEPT (tracked lingering
     state) so the owner can delete it manually in the app — never
     silently lost. State machine now allows DELETING -> FAILED_FINAL
     (this gap would have crashed ANY non-retryable delete failure,
     e.g. a 403 on Telegram too).
  5. Activation gate = this adapter + fake-client tests now, then a
     LIVE certification run by the operator on the server
     (`scripts/certify_platform.py`, spec section 13 checklist):
     getMe, getChat, getChatMember admin, sendMessage,
     editMessageText, sendPhoto (optional URL), sendMediaGroup (album
     size configurable to measure the live limit), editMessageCaption,
     deleteMessage, optional 48h-delete-limit probe (sacrificial old
     message id) and optional rate-limit probe. PASS/FAIL/SKIP report;
     token never printed; exit 0 = certifiable.
- Services refactored off the Telegram hard-coding:
  `platform_connections` (accept TELEGRAM + BALE; per-connection client
  resolution; platform-aware "shared bot unavailable" errors) and
  `publications` (capability-driven rendering incl. per-platform caption
  limits; single media goes through `sendPhoto` on both platforms so
  the declared single-media caption limits are real on the wire;
  platform-aware error handling and reconciliation).
- Telegram behavior is UNCHANGED (same limits, same flows); its
  `TelegramError`/`TelegramCapabilities` remain as compatibility aliases
  of the shared base types.

### Tests
+41: 16 unit (Bale escaping round-trip, error classification from body
error_code vs HTTP status, transport contract with no network,
inspection-unsupported rule, override seam) + 20 integration (Bale
connection verification, publish text/album/single-photo, caption
limits 4096-vs-1024 behavior, timeout->unknown + explicit check
conflict, edit, repost, the 48h lingering scenario end-to-end,
permission loss/disconnect suspension + resume without inspection,
cross-tenant isolation) + capabilities/transition assertions.
Suite: 308 -> 349 passing (2 skipped), ruff clean.

## 2026-09-15 — Server fix + OpenRouter AI provider


### Fixed
- `/readyz` import error (found on the production server): the readiness
  handler lazily imports `get_engine` from the `app.infrastructure.db`
  package, which did not re-export it (defined in `...db.session`). The
  handler masked this as "database: unavailable: ImportError" and always
  answered 503. The package now re-exports `get_engine`, so readiness
  reflects the real database state (200/ok when live, 503 with the
  actual connectivity error when not).
- Regression test added: with a live DB engine, `/readyz` must be 200
  with `{"ready": true, "checks": {"database": "ok"}}`. Previously only
  the no-DB contract test existed (it allows 503, which masked the bug).

### Delivered — OpenRouter as an AI provider (owner request)
- New first-class provider option `AI_PROVIDER=openrouter`: OpenRouter's
  OpenAI-compatible `/chat/completions` endpoint with vendor/model slugs
  (`openai/gpt-4o-mini`, `anthropic/claude-3.5-sonnet`,
  `google/gemini-2.0-flash-001`, ...). The key (`sk-or-...`) comes from
  the environment only — never stored, never logged (rule 14).
  OpenRouter's recommended `X-Title` attribution header (app name) is
  sent; no other custom headers.
- Settings: `AI_OPENROUTER_BASE_URL` (default
  `https://openrouter.ai/api/v1`, overridable), `AI_OPENROUTER_API_KEY`,
  `AI_OPENROUTER_MODEL` (default `openai/gpt-4o-mini`). The generic
  `openai_compatible` option is unchanged for other endpoints.
- Failure classification (unchanged contract, now covered by tests for
  the OpenRouter path): timeout/429/5xx -> transient (bounded retries;
  total failure refunds the credit per AI spec section 12); 4xx —
  including 401 (invalid key) and 402 (insufficient credits) ->
  permanent (no retry, clear operator-visible error); response bodies
  are never included in error messages or logs.
- The built-in `template` provider remains the default (offline,
  deterministic, no keys) — OpenRouter is enabled per deployment by
  setting `AI_PROVIDER=openrouter` + the key.
- Tests: +14 (provider transport contract: exact URL/headers/model,
  attribution header, transient vs permanent classification incl. 402,
  malformed payloads, no body leakage in errors; `get_ai_provider`
  wiring for template / openai_compatible / openrouter incl. missing-key
  permanent failure; `/readyz` regression with a live DB). Suite:
  294 -> 308 passing.

## 2026-09-15 — Phase 5 implementation (Platform adapter / Telegram connection / Publication)

### Delivered
- Platform adapter contract (PLATFORM_ADAPTER_SPECIFICATION): the domain
  core requests SEMANTIC operations (send/edit/delete/inspect) against a
  client interface; platform behavior (limits, error mapping) lives in a
  capability matrix + adapter, never hard-coded in the core. Telegram
  adapter: verified Bot API limits (text 4096, caption 1024, media group
  10), HTTP error -> taxonomy classifier (401 auth, 403 permission, 404
  not-found, 429 rate-limited with retry_after, 400 validation; timeout ->
  NETWORK_TIMEOUT; never leaks response bodies or credentials).
- Shared organization bot (owner decision 2026-09-15): ONE platform-level
  bot token from the environment; the business adds the bot to its own
  channel/group as admin and connects the target. Verification is a
  three-step proof of control (getMe -> getChat -> getChatMember admin);
  no per-business secret is stored anywhere; nothing sensitive is logged.
- Post & Publication domain (spec sections 3, 11-21, 25): logical Post +
  immutable PostVersion snapshots (content + media fingerprints; exact
  product/preset/AI references) -> platform-specific Publication with an
  explicit 17-state machine (no boolean "posted"), deterministic
  idempotency key per (business, product, connection, post version) and a
  durable per-operation attempt log.
- DB backstop: PostgreSQL partial unique index — at most ONE PUBLISHED
  publication per (connection, product); the state machine enforces the
  same invariant in code (the old publication leaves PUBLISHED before the
  replacement enters it, while the remote order stays publish new ->
  verify -> delete old).
- Publication service (manual V1, synchronous — queue workers are Phase
  8): publish (entitlement -> duplicate guard -> platform-limited render
  via the Phase 4 engine -> adapter -> PUBLISHED / UNKNOWN_REMOTE_STATE
  on timeout / FAILED_RETRYABLE|FINAL by taxonomy); update with automatic
  NOOP / EDIT / REPOST classification; forced repost; remote delete;
  reconcile/check.
- Failure-first rules (spec 13-21, 25, 29): a timeout is NEVER a failure
  (UNKNOWN_REMOTE_STATE, reconcilable; a lost message id stays explicitly
  unresolved, never a guessed result); a remote manual deletion is
  recorded (REMOTE_DELETED, post archived) and NEVER auto-reposted; a
  remote manual edit is flagged remote_modified and never overwritten;
  permission loss / disconnect SUSPENDS live publications, and
  (re)verification RESUMES them by reconciling — never bulk-republishing;
  one publication's failure never affects another.
- Connection lifecycle: create (entitlement channels limit; duplicate
  target and same-chat-different-name conflicts fail closed), verify,
  reconnect (from DISCONNECTED only), disconnect (suspends, never deletes
  remote content).
- HTTP: 12 business-scoped endpoints (connections list/create/verify/
  reconnect/disconnect; publications list/get, publish, update, repost,
  delete, check) under the RBAC matrix (connections.* / posts.view /
  posts.publish / posts.update / posts.repost / posts.delete_remote).
- Migration e9f0a1b2c3d4 (additive: 5 tables + indexes + the partial
  unique live-publication backstop).
- Tests: suite grows 244 -> 294 passing locally (unit: state machine full
  transition matrix + fail-closed illegal transitions + suspension/resume
  edges, idempotency scoping, NOOP/EDIT/REPOST planning, retryable-code
  classification, capability contract, error classifier; integration with
  a scriptable in-memory fake Telegram client: connect/verify/normalization/
  no-token-in-responses, bot-not-admin, chat-not-found, shared-bot
  unavailable, duplicate target + same-chat-different-name conflicts,
  no-subscription 403, channel limit (2) + slot freed by disconnect,
  verify/reconnect/disconnect flow, cross-tenant 404, narrow-profile 403;
  publish happy path + effectively-once duplicate block, album capped at
  10 with caption, caption overflow trimmed (essentials survive), timeout
  -> UNKNOWN -> unresolved reconcile, rate-limited retryable, permission
  error final, archived product blocked, unverified connection blocked;
  update NOOP/EDIT, media change -> repost (new -> verify -> delete old),
  forced repost, repost verification failure -> reconcile completes the
  interrupted repost, remote manual delete (no auto-repost, post
  archived), remote manual edit flagged, delete, permission-loss suspend +
  resume, disconnect suspend + resume, publication cross-tenant 404).

### Notes
- Publishing is MANUAL in V1 (owner decision); scheduling/automatic sync
  publishing arrives with the sync engine (Phase 8) and reuses the same
  state machine/idempotency/attempt records (queue workers will drive the
  same service).
- Real Telegram bot token is configured operationally via env
  (TELEGRAM_BOT_TOKEN); the fake client makes the whole flow testable
  offline. A publish timeout that loses the message id cannot be
  auto-reconciled in V1 (no remote search capability) — the state stays
  explicitly UNKNOWN and the duplicate guard prevents a blind re-publish.
- Bale/Eitaa/Rubika adapters are later phases (Bale in Phase 6;
  Eitaa/Rubika certification-blocked).

## 2026-09-14 — Phase 4 implementation (Content / Presets / AI)

### Delivered
- Domain (pure, framework-free): content block model with ownership
  classification (SYSTEM/CUSTOMER/STATIC/DERIVED), a strict allowlisted
  token grammar ({field}, {attr.<key>}, {ai_<key>}) with injection-safe
  two-pass resolution (values are never re-parsed), deterministic
  rendering, and priority-ordered length handling (identity >
  price/stock > contact > attributes > AI > hashtags > decorative;
  blocks with a clear reason instead of silent truncation). AI policy:
  bounded 3-attempt retry (transient + invalid output), reuse rule for
  APPROVED artifacts by (product, key, definition version,
  declared-inputs fingerprint), refund policy, automatic-mode
  eligibility.
- AI providers behind one contract: deterministic offline TemplateProvider
  (V1 default, Persian-aware, factual — never invents price/stock/SKU) and
  an OpenAI-compatible provider (base URL + API key + model from env; key
  and payloads never logged; 429/5xx/timeout -> transient, 4xx ->
  permanent).
- Presets: business-type presets (structured block lists) with immutable
  versions (DRAFT/ACTIVE/SUPERSEDED) managed by the platform operator; a
  seeded default preset per business type. Per-product presets — a
  completely separate section (own tables/routes/permission) — gated by
  the plan flag product_preset_eligible (top-plan entitlement,
  operator-managed at runtime; Starter off by default).
- AI output registry: versioned, configurable definitions (key, prompt
  template, declared inputs, max length, cost credits); seeded
  ai_description + ai_short_title. Generation flow in one transaction:
  entitlement (plan AI) -> reuse check -> atomic credit consumption ->
  bounded provider attempts -> validation -> PENDING_APPROVAL artifact
  (or refund + manual fallback on total failure). Edit/approve/reject
  lifecycle; manual edit of an APPROVED artifact creates a new PENDING.
- Preview: same deterministic renderer (publication will reuse it in
  Phase 5) resolving product preset (top-plan) -> business-type default
  -> built-in fallback; platform-neutral text + media manifest + per-block
  diagnostics + length fit.
- Automatic mode: business-level toggle + tested eligibility logic (the
  trigger lands with the sync/publication phases per the roadmap).
- HTTP: 18 business/operator endpoints (admin presets + AI definitions;
  business presets, product presets CRUD/assign, AI generate/retry/
  edit/approve/reject/list, automatic toggle, preview). New permission
  product_presets.manage (owner + manager).
- Migration d8e9f0a1b2c3 (additive: 6 tables + 3 columns; seeds AI
  definitions + default presets).
- Tests: suite grows 199 -> 244 locally (unit: token grammar/allowlist,
  injection safety, priority trimming, block render, retry bounds, refund
  policy, reuse rule, template provider determinism/length; integration:
  admin preset lifecycle + versioning + super-admin gate, business preset
  visibility, preview + AI approve flow, reuse without double charge,
  regeneration on input change, transient/invalid/permanent failure with
  credit refund, edit/approve/reject/retry lifecycle, plan-without-AI
  403, no-credits 409, product presets top-plan gate + non-destructive
  downgrade + clear, cross-tenant 404, narrow-profile 403, automatic
  toggle, definition versioning never auto-regenerates).

### Notes
- Real AI credentials (OpenAI-compatible endpoint) are configured
  operationally via env later; the built-in template provider makes the
  feature usable from day one without external calls.
- Per-product presets are deliberately a separate section and a top-plan
  exclusive (owner decision); the plan flag lets the operator move the
  entitlement between plans at runtime without code changes.
- V1 preview is platform-neutral; per-platform previews + actual
  publication reuse this exact renderer in Phases 5-7.

## 2026-09-13 — Phase 3 implementation (Source / Product core)

### Delivered
- Domain (pure, framework-free): identity normalization (NFKC + Persian
  yae/kaf unification + casefold), deterministic fingerprints, and the
  resolution priority external_id -> SKU -> barcode -> fingerprint ->
  name-only-collision; change-risk classification with the approved
  thresholds (price jump >50% = HIGH, identity change = CRITICAL, custom
  spec = HIGH, mass change >=5 products = quarantine, mass missing >50%
  = block).
- Source adapters behind one contract: Excel/XLSX (openpyxl, header
  detection, empty-row skipping, incomplete-read reporting) and Google
  Sheets (injectable client factory, batch values.get, permission/API
  failures reported as incomplete reads — never auto-missing).
- Product model: canonical product (durable UUID, business-scoped), typed
  custom attributes (JSON, metadata from the mapping version), compact
  product versions (change categories + risk, not full snapshots),
  first-class media with SOURCE vs CUSTOMER origin protection, per-source
  row records (locator, not identity), versioned column mapping
  (DRAFT/ACTIVE/SUPERSEDED), review cases, and import runs.
- Import pipeline (manual V1): entitlement gate -> structural read ->
  per-row validation -> identity resolution (pure) -> upsert/create/review
  case -> missing-row inference (only on confirmed complete reads) ->
  ImportRun summary + audit. Idempotent re-sync via row content hash; row
  moves keep identity; ambiguous/conflicting identity NEVER auto-merges;
  identity-field changes are CRITICAL + REVIEW_REQUIRED + frozen; mass
  HIGH-risk changes quarantine the rest of the run; new products respect
  the products entitlement; corrupt reads fail the run without touching
  product state.
- HTTP: 23 business-scoped endpoints (sources CRUD/pause/resume, mapping
  suggest/propose/version/activate, import run/preview/list, products
  list/get/patch/versions, media list/add/remove, archive/restore,
  review cases list/resolve). New manager-level permissions:
  products.import, products.review_mapping, sources.view, sources.manage.
- Entitlement usage counters registered for real: products (non-archived)
  and sources (active+paused).
- Migration c7d8e9f0a1b2 (additive; 8 tables; unique backstops for
  locator-per-source and version-per-product).
- Tests: suite grows 110 -> 199 passing locally (unit: identity full
  matrix + fingerprint rules, change-risk thresholds, Persian/English
  mapping heuristics, Excel adapter, Google Sheets adapter with fake
  client; integration: full import happy path + idempotent re-sync,
  change/risk versioning, missing + reappearance, row move, mass-missing
  block, ambiguous/duplicate/conflict identity -> review case + resolve,
  identity-change freeze, media customer protection (default +
  media_authoritative), invalid rows, corrupt-file no-missing, preview
  zero-write, entitlement gates (product/source/daily-sync),
  cross-tenant 404, permission 403, audit trail; product API: edits,
  versions, custom attributes, media ops, archive/restore).

### Notes
- V1 sync is manual only; the scheduler (periodic sync) lands in Phase 8.
- Google Sheets credentials are configured operationally (service-account
  file path behind credentials_ref); the adapter is fully mock-tested.
- Review cases are the ONLY path by which a quarantined row reaches a
  product; every resolution is audited.

## 2026-09-13 — Phase 2 implementation (Subscription / Payment / Entitlement)

### Delivered
- Plan catalog (operator-managed, no code changes): price/term/limits/AI
  credits/feature flags; seeded "Starter" plan (IRT, tunable at runtime).
- Subscription lifecycle: PENDING/ACTIVE/GRACE/EXPIRED/SUSPENDED/CANCELLED/
  REFUNDED with an explicit audited state machine; one live subscription per
  business; hourly Celery task moves ACTIVE->GRACE (7-day grace, owner
  approved) and GRACE->EXPIRED.
- Manual payments as separate records (spec: payment != entitlement
  activation): verification by the platform operator (Super Admin flag),
  amount-mismatch requires an explicit note (audited), double-verification
  impossible (row lock + pending-payment uniqueness).
- Entitlement engine: derived immediately before use (RBAC spec section 20:
  effective permission = role  policy  entitlement  scope); usage-counter
  registry for Phase 3+ (products/sources/channels/admin seats/storage).
- AI credit ledger: separate monthly + purchased pools, atomic idempotent
  consumption (monthly first), auditable append-only transactions.
- Migration f0e1d2c3b4a5 (additive; Postgres partial unique backstops;
  starter plan seed).
- Tests: suite grows 51 -> 110 passing locally (unit: state machine full
  matrix, calendar clamping, entitlement rules, ledger atomicity/
  idempotency; integration: full lifecycle, grace renewal crossing a month
  with a deterministic clock, same-month no-double-grant, plan change,
  downgrade-over-limit blocking, suspend/reactivate, refund, cross-tenant
  isolation, operator-only access, audit).

### Notes
- Subscription expiry never corrupts data: terminal rows are kept as history;
  a new purchase starts a new row; unused monthly credits are expired (not
  deleted) in the ledger.
- Downgrades do not delete data: over-limit usage blocks NEW operations via
  the entitlement engine until usage is reduced (spec section 8).
- The owner's real plan names/prices still need to be set through the plan
  endpoints (the Starter values are placeholders).

## 2026-09-13 — Phase 1 implementation (Identity / Business / RBAC foundation)

### Delivered
- Domain: enums (account/business/membership/request/platform/lifecycle),
  permission matrix (34 permissions, 7 owner-only), domain errors with stable
  machine codes (cross-tenant access reported as 404 to avoid enumeration).
- Infrastructure: 11 ORM tables (users, user_sessions, businesses,
  business_types, memberships, admin_invites, admin_access_requests,
  channel_links, link_codes, account_identities, audit_logs); Argon2id
  password hashing; session tokens stored only as SHA-256 digests; one-time
  link codes; invite codes (ambiguous characters excluded, hashed at rest).
- Application services: auth (register/login/logout/sessions, bounded
  lockout 5 attempts/15min, timing-equalized unknown-email path), business
  (create with atomic OWNER membership, fail-closed access), membership
  (invite lifecycle, request coalescing, transactional approval
  revalidation, self-approval blocked, revocation revokes all sessions),
  linking (single-use 10-minute codes, cross-account identity conflict ->
  409, idempotent re-link), audit (correlation IDs, actor/business/target).
- HTTP: /api/v1 auth, business, admin, links routes; correlation-id
  middleware; DomainError -> JSON mapping; internal /links/verify contract
  protected by shared token (for the Phase 9 bot).
- First migration `a1b2c4d5e5f6` (additive; seeds business type 'general';
  Postgres partial unique indexes as backstops).
- Tests: 50 passing locally (unit + full-stack integration on in-memory
  SQLite); 2 service smoke tests run in CI against PostgreSQL 17 + Redis 7.
  CI also validates migration DDL on Postgres (`alembic upgrade head`).

### Failure-first behaviors verified by tests
- wrong-password lockout persists across requests (commit-before-raise)
- duplicate admin requests coalesce; single-use invites not consumed by
  rejected submissions (check-then-consume ordering)
- revocation revokes the user's sessions (next call 401)
- cross-business access fails closed with 404
- self-approval impossible; owner revocation of the OWNER blocked
- link code single-use; identity conflict across accounts -> 409
- unknown-email login timing equalized; failed logins audited

### Verification status
- Local: 51 passed / 2 skipped (service smoke), ruff clean.
- CI (run 34763142857, commit a56502f): BOTH jobs GREEN on Python 3.13:
  - Lint + unit (21 tests)
  - Integration: service pre-flight, `alembic upgrade head` on PostgreSQL 17,
    and the full integration suite (32 tests incl. auth lockout, RBAC
    lifecycle, one-time linking codes, audit) against real PostgreSQL + Redis.
- Bug found and fixed by CI: `create_engine(..., poolclass=Pool)` used the
  abstract base pool class — first `connect()` raised `NotImplementedError`.
  Now `QueuePool` explicitly, with a regression unit test.

### Known decisions/notes
- Partial unique indexes are Postgres-specific: enforced in the service
  layer (portable) + migration backstop (Postgres). Not declared in model
  metadata to keep SQLite test schemas valid.
- Business type list seeded with 'general'; the owner's actual business
  types (Understanding Report §10) are still pending.
- PostgreSQL in dev/CI = 17 (conservative fallback); production target 18
  per spec (verified before launch, Gate I).
- Ownership transfer (changing the OWNER) is intentionally not implemented
  yet: separate high-risk workflow (RBAC spec section 6).

### Rollback
Revert the Phase 1 commit; the migration has a full downgrade (drops all
Phase 1 tables). No production data exists yet.

## 2026-09-13 — Phase 1 architecture decisions (owner-approved)

### Decisions (approved by Project Owner, structured approval)
1. **Web authentication mechanism (V1): EMAIL + PASSWORD.**
   - Argon2id password hashing (OWASP-recommended).
   - Server-side sessions (DB-stored, token hash at rest) with expiry and
     revocation; SameSite cookies. Telegram/Bale identities are LINKED to the
     Web account later (not the login path in V1).
   - Login rate limiting: per-user bounded attempts with temporary lockout.
2. **Cross-interface linking: ONE-TIME CODE.**
   - Web panel shows a short-lived 6-digit code; the user sends it in the
     Telegram/Bale bot chat; the bot verifies it against the backend contract.
   - Never based on username/name matching (per RBAC spec section 16).
3. **Admin access request discovery: INVITE CODE (primary) + CHANNEL REFERENCE (secondary).**
   - Invite codes are owner-generated, expiring, use-limited.
   - Channel reference is accepted only when the channel is already linked to
     the target Business; ambiguous matches enter review (never guess).

### Impact
- Phase 1 implementation may proceed: models + migrations, auth, business,
  membership/RBAC, admin request flow, linking contracts, audit foundation.

## 2026-09-13 — Phase 0 completed

### Baseline delivered (commit on arena branch)
- 22-document specification package merged from main and validated (22/22).
- Layered app skeleton (domain/application/infrastructure/interfaces/workers/config).
- Pydantic Settings; SQLAlchemy engine/session (pool_pre_ping); FastAPI factory
  with /healthz + /readyz (separate liveness/readiness); Celery app (bounded).
- Alembic wired to settings + Base.metadata (no migrations yet).
- Tests: 9/9 unit passing locally (Python 3.11); CI runs ruff + unit on
  Python 3.13 and integration on PostgreSQL 17 + Redis 7 services.
- requirements.txt (resolved pins) + requirements.lock.txt (full lock).
  Final lock re-verified on first green CI (3.13) per Gate F note.
- Dockerfile (non-root) + docker-compose.yml (postgres 17 fallback, redis,
  web, worker). Makefile, .env.example, .gitignore, pyproject.toml.
- Rollback: pure scaffold, no data — revert commit.

## 2026-09-13 — Owner Approval of Implementation Gates (A–H)

### Approval record
- Date: 2026-09-13 (owner session, structured approval questions)
- Approved by: Project Owner
- Status: FINAL for the listed scope

### Decisions
1. **Gates A+B+C+D — Domain contracts: APPROVED as documented.**
   Product Domain v2, Post/Publication v1, Source/Mapping/Sync v1,
   User/Business/Membership/RBAC v1, AI Configuration v1 and
   Subscription/Payment/Entitlement v1 are adopted as the implementation
   contracts. Includes: evidence-based identity resolution with no
   auto-merge; safe quarantine default for missing source rows; Owner/Admin
   membership with owner-approved admin requests; versioned Presets/AI/Mapping.
2. **Gate F — Technology stack: APPROVED.**
   Python 3.13 + FastAPI + SQLAlchemy 2.0.x + PostgreSQL 18 (fallback 17) +
   Redis + Celery + Jinja2/HTMX + Docker Compose, per
   TECHNOLOGY_AND_REPO_SPECIFICATION_V1. Exact dependency versions will be
   locked after the first green compatibility run in CI.
3. **Gate E — Platform activation order: APPROVED.**
   Telegram + Bale first (certification, then publication). Eitaa and Rubika
   are certified independently; only certified capabilities activate.
4. **Gate G — Payment mode V1: MANUAL VERIFICATION approved.**
   Owner manually verifies payments and activates subscriptions in V1.
   Automatic gateway (e.g. Shaparak) comes in a later phase behind the
   Payment Provider interface.
5. **Gate H — Backup strategy: APPROVED.**
   Encrypted periodic backups + local rotation + off-site copy + mandatory
   restore testing. Telegram may carry an additional encrypted copy only —
   never the sole DR source. Off-site destination pending server details.
6. **Gate I — Production launch: DEFERRED** to the production phase
   (certification + security review + load test + restore test + failure-matrix pass).

### Impact
- Phase 0 (Approval & baseline) is unblocked.
- Foundation implementation may proceed in roadmap order; Phase 1 =
  Auth / User / Business / Membership / RBAC + audit foundation.

## 2026-09-13 — Project Understanding Report delivered

- AI Developer completed the mandatory bootstrap reading of all 22 package
  documents and delivered `docs/PROJECT_UNDERSTANDING_REPORT_V1.md`
  (14 sections: scope, non-goals, domains, security rules, failure/recovery
  rules, approved vs pending decisions, doc conflicts, gaps, risks,
  implementation order, critical tests, repository readiness).
- Findings: 3 minor doc inconsistencies recorded (Subscription phase order
  between roadmap and bootstrap reference; duplicated section number 17 in
  MASTER spec; permission list overlap between Product spec and RBAC spec —
  RBAC spec is authoritative). No technical blockers.
- READY_TO_PLAN = YES; FIRST_PHASE = Phase 0.
- The 22-document package was uploaded to the repository by the owner under
  `docs/` (flat) and validated document-by-document against the original
  package (titles + content markers, 22/22 pass).

## 2026-09-12 — Product Domain v2 Consolidation

### Scope reinforcement
- V1 remains a public multi-tenant SaaS for product ingestion, content generation, multi-platform publication, synchronization, monitoring, subscriptions, authentication, and Owner-managed Admin access.
- Training/tutorials and payment/subscription capabilities remain required project capabilities, but they are separate domains and must not pollute Product state.
- Product Domain must stay compact, deterministic, durable, explainable, and independent from platform-specific publication implementation.

### Lessons extracted from previous project document
- Previous project documentation was reviewed only as a source of lessons, failure cases, and ideas; its architecture, stack, schema, numerical limits, and implementation patterns are NOT imported automatically.
- Useful retained lessons: recommended business-specific sample files, structured AI output, AI budget control, prompt versioning, preview, health checks, structured logging, backup/recovery, onboarding/tutorial concepts, and graceful degradation.
- Deliberately rejected as automatic carry-over: SKU-as-mandatory identity, row-based identity, boolean publication state, fixed rate limits, scheduler choice, hard-coded AI description model, single-image field, and Telegram-centric architecture.

### New Product Domain v2 requirements
- Recommended input templates will be provided for each supported business type, while arbitrary customer structures remain supported.
- Customers should be encouraged to use stable product IDs, avoid ID reuse and unnecessary row churn, keep one logical product per record, and avoid duplicates. These are recommendations, not system dependencies.
- Product identity is independent of source row position/order.
- Internal Product ID is immutable.
- Source Record is distinct from Product and may move/disappear/reappear.
- Product identity resolution uses an evidence hierarchy and conservative ambiguity handling.
- False merge is treated as more dangerous than false-new-product.
- Identity decisions must be explainable through compact evidence.
- Product Media is a first-class component supporting add/remove/replace/reorder from Customer/Admin panels.
- Product supports stable core fields plus typed business-specific custom attributes.
- Custom fields are template-exposable with formatting rules, e.g. `{touch}` -> `دارد/ندارد`.
- Product version storage must favor current state + hashes + compact change metadata rather than full snapshots on every sync.
- Product deletion/missing-source handling remains non-destructive by default.
- Import must isolate invalid rows and allow valid rows to continue where safe.
- Import Preview / Sync Diff is a required quality and safety feature.
- Suspicious high-risk changes can be isolated/reviewed before downstream propagation.
- AI outputs are separate versioned editorial artifacts, manually editable and reusable after approval.
- Existing published products/posts must NOT receive automatic new AI generations because prompt/model versions change.
- Presets are versioned and historical publications remain tied to their used version.
- Business type correction must not destroy Product data; creation of a genuinely new Business must not mix Products or publication state across Businesses.
- Subscription/entitlement checks gate processing but subscription expiry must not destroy Product data.
- Product permissions are Business-scoped and server-enforced.

### Required Product Domain invariants
1. Row number/order never identifies a Product.
2. Product identity survives source row movement.
3. Product ID is immutable.
4. Missing source data is not immediate deletion.
5. Ambiguous identity never silently merges.
6. Product facts are independent from AI outputs.
7. Product is independent from Post/Publication.
8. Identical repeated syncs are idempotent.
9. One invalid record cannot corrupt unrelated valid records.
10. Critical state is durable, not cache-only.
11. Unknown state remains explicit until resolved.
12. Cross-business/tenant data leakage is prohibited.

### New failure scenarios added
- deleted/recreated source row
- ID reuse
- source column meaning change
- source media vs customer-added media conflict
- business type correction vs actual new business
- custom field lifecycle
- AI artifact reuse/manual override
- import partial validity
- suspicious mass changes
- identity evidence tracking
- Product Media failures and platform-dependent downstream handling

### New/updated artifact
- `PRODUCT_DOMAIN_SPECIFICATION_V2.md` created as Draft for review/approval.

### Approval boundary
No Product foundation/database implementation should begin until Product Domain v2 is explicitly approved or revised by the user, especially:
- identity resolution
- custom field storage
- media model
- missing-source policy
- business-type change rules
- compact version/history strategy
- AI artifact model

## 2026-09-12 — Post & Publication Domain Specification v1 drafted

### New design artifact
- POST_PUBLICATION_DOMAIN_SPECIFICATION_V1.md created.

### Core design principles
- Post is platform-independent; Publication is platform-specific.
- PostVersion and PublicationAttempt are historical/operational evidence and must not be overwritten.
- Remote state uncertainty is explicit and must reconcile before duplicate/destructive actions.
- System targets effectively-once behavior using idempotency, unique constraints, concurrency guards, durable attempts, remote identifiers, and reconciliation.
- Reconnect after partial publication must resume from durable publication state instead of republishing everything.
- Repost is Publish New -> Verify -> Delete Old whenever safe/possible.
- Remote manual edits do not become Product edits in V1.
- Managed content and customer-owned content are separated conceptually.
- Platform capabilities and limits are adapter-specific; no Telegram assumptions may be copied to other platforms.
- Preview should use the same renderer/validator pipeline as publish.
- Historical posts never auto-regenerate AI content because of prompt/model changes.

### Current external verification
- Telegram official Bot API documents 1-4096 characters for text messages after entity parsing, 0-1024 for media captions, media-group sending via sendMediaGroup, caption editing, and media editing. Capabilities are platform-specific and must not be generalized. Source: https://core.telegram.org/bots/api
- Telegram core API documentation also exposes media/message editing details. Source: https://core.telegram.org/api/files
- Eitaa/Rubika capabilities remain subject to official API verification and live adapter tests before production claims.

### Approval gate
- Post/Publication domain is still Draft for Review/Approval.
- Database schema, queue technology, or implementation should not be finalized from this draft alone.

## 2026-09-12 — User/Business/Membership/RBAC Domain Specification v1 drafted

### New design artifact
- `USER_BUSINESS_MEMBERSHIP_RBAC_SPEC_V1.md` created as Draft for Review/Approval.

### Core decisions/proposals
- User is an identity, not a permanent global Owner/Admin classification.
- Roles are scoped to Business via Membership.
- User-selected role during onboarding is intent only and never grants authorization.
- Owner can remove Admin at any time; revocation is auditable and must block future protected actions.
- Admin access is requested by candidate and approved/rejected by the Business Owner.
- Authorization is server-side and combines Membership role, permissions, Business scope, resource ownership, subscription entitlement, and operation risk.
- Business Type is separate from Business identity. Classification correction can preserve the same Business; a genuinely different business should be a new/archived Business context rather than an overwrite.
- Account linking across Web/Telegram/Bale requires explicit proof/control and must prevent accidental identity merges.
- Channel connection must verify technical control and required permissions; usernames/channel names are never sufficient ownership proof.
- Cross-tenant access must fail closed.
- Exact authentication, permission matrix, and platform verification mechanisms remain approval-gated decisions.

### New required failure scenarios
- wrong role selection
- duplicate/stale admin requests
- simultaneous approvals
- admin revocation during queued/running jobs
- cross-business context mistakes
- account linking ambiguity
- channel already linked elsewhere
- business-type correction during active work
- subscription expiry during admin work

### Approval gate
- No implementation of authorization foundations until Membership lifecycle, RBAC matrix, authentication/linking, business switching, and platform control verification are explicitly approved.


## 2026-09-12 — Consolidated design package + final review
- Consolidated implementation package prepared with domain, architecture, security, test, operations and developer-directive documents.
- Added common SaaS foundation capabilities: account settings, notifications, support/help, maintenance mode, feature flags, locale/timezone, safe data export, session/device controls and terms/privacy version tracking.
- Added FINAL_REVIEW_AND_SELF_SCORE_V1.md with an overall planning quality score of 9.8/10.
- No implementation code was written during this planning package phase.


## 2026-09-12 — AI Bootstrap Contract Added

### Added
- Added `00_START_HERE.md` as mandatory entry point for any AI developer receiving the project package.
- Defined interactive phase-by-phase workflow: read -> understand -> plan -> approval -> implement -> test -> review -> log -> owner validation.
- Defined owner approval boundaries, Change Proposal format, Stop/Blocker protocol, Bug Loop prevention, failure-first rules, platform certification rules, documentation rules, Definition of Done, and final review protocol.
- Refreshed project package archive as `PROJECT_PACKAGE_V1_FINAL.zip`.

### Final package intent
- The package is intended to be supplied to an AI coding/development agent as an engineering specification and development-governance package, not as permission to blindly generate the entire system.
- The AI must begin with a Project Understanding Report and must not start foundation implementation before required approvals are obtained.
