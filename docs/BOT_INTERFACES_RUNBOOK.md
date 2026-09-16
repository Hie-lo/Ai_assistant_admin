# Bot Interfaces Runbook — Telegram & Bale

Status: Phase 9 — Operational
Date: 2026-09-16

Per USER_BUSINESS_MEMBERSHIP_RBAC_SPEC_V1 section 16 and IMPLEMENTATION_ROADMAP_V1 Phase 9.

## 1. Architecture

- Unified permission/use-case layer: bots call same application services as Web panel
- No business logic inside bot handlers (per TECHNOLOGY_AND_REPO_SPECIFICATION_V1 §14)
- Durable state in PostgreSQL, not in bot memory
- Account linking via one-time codes (LinkCode model, 10min expiry, single-use, SHA-256 at rest)
- Never trust username/name matching — explicit proof via code

## 2. Bot Models

Both Telegram and Bale use shared organization bot model (owner decision 2026-09-15):

- ONE platform-level bot per platform (not per business)
- Token from env (TELEGRAM_BOT_TOKEN, BALE_BOT_TOKEN), never stored per business, never logged
- Business adds bot as admin to its own channel/group and connects target
- Verification: getMe -> getChat -> getChatMember admin (Telegram/Bale) or behavioral proof (Rubika/Eitaa)

## 3. Telegram Bot

### Setup

1. Create bot via @BotFather in Telegram
2. Get token, set TELEGRAM_BOT_TOKEN in .env
3. Set webhook: `https://api.telegram.org/bot<token>/setWebhook?url=https://yourdomain.com/api/telegram/webhook`
   Or use polling worker (future)
4. Verify: GET /api/telegram/health should show configured=true

### Webhook

- POST /api/telegram/webhook
- Accepts Telegram Update JSON (update_id, message, edited_message)
- Extracts from_user.id, chat.id, text
- Calls handle_message (app/interfaces/telegram/bot.py)
- Sends reply via Telegram API (best-effort, failure doesn't affect webhook ack)
- Never logs token or full payload with secrets

### Commands

- /start — welcome + connection status + business list if linked
- /link — instructions for linking
- /help — help text
- /businesses — list user businesses
- /products [query] — list products (first business, 10 items, total count)
- /sync [source_id] — trigger manual sync for source (first active or specified)
  - Excel boundary: scheduled/automatic only for Google Sheets, so bot sync for Excel returns instruction to use web panel
  - Entitlement + mapping checks
  - Coalescing: if active job exists, merges request
  - Dispatches to Celery (best-effort, recovery handles if broker down)
- /notifications — recent 5 notifications
- /status — system status (linked, platform, platform_user_id)

### Linking Flow

1. User in Web panel: creates link code for Telegram (POST /api/v1/links, platform=TELEGRAM)
2. Web shows 6-digit code, expires in 10min
3. User sends code in Telegram bot chat
4. Bot verifies via linking.verify_link_code (checks hash, expiry, not consumed)
5. Creates AccountIdentity (platform, platform_user_id) linked to user_id
6. Returns success + business list + commands

Security:
- Code single-use (consumed_at set)
- Cross-account identity conflict -> 409 (one platform user id cannot link to two users)
- Timing equalized, no enumeration

## 4. Bale Bot

Same as Telegram, with differences:

- Base URL: https://tapi.bale.ai (vs https://api.telegram.org)
- Token: BALE_BOT_TOKEN
- Webhook: POST /api/bale/webhook
- Markdown escaping: Bale parses every message as markdown, so adapter escapes \ _ * [ ] ( ) on wire and unescapes for comparison
- Bot replies also escaped via escape_markdown
- Single photo caption 4096 (vs Telegram caption 1024) — handled in publication service, not bot
- 48h delete limit: handled in publication state machine (DELETING->FAILED_FINAL with lingering state)
- Health: GET /api/bale/health

Commands identical to Telegram (shared handler factory).

## 5. Shared Handler Logic

Location: app/interfaces/telegram/bot.py (Bale reuses via app/interfaces/bale/bot.py)

- BotMessage dataclass: platform, platform_user_id, text, chat_id, username
- BotReply dataclass: text, parse_mode
- handle_message: main entry, routes to _handle_* functions
- _find_linked_user: looks up AccountIdentity
- _list_user_businesses: via business service
- Each handler opens its own DB session (never request-scoped), commits, closes

No global mutable state for job/business state (per tech spec §14).

## 6. Failure Scenarios

- User not linked: prompt linking instructions, don't leak business info
- Invalid/expired code: clear error, ask to create new code
- No businesses: prompt to create via web panel
- No sources: prompt to create via web panel
- Source has no active mapping: error with source name
- No active subscription: entitlement error
- Excel sync via bot: explain manual only for Excel, use web panel
- Active sync exists: inform coalesced, show status
- Worker down: job stays QUEUED, recovery dispatcher reclaims after heartbeat expiry
- Platform API down: publish shows FAILED_RETRYABLE, retry bounded (3 attempts)

## 7. Testing

- Unit: bot handlers with mocked DB (not yet, future)
- Integration: link code flow tested in tests/integration/test_linking.py
- Manual: owner must test on real bots (Telegram + Bale) with real accounts
  - Create link code via web, send in bot, verify linked
  - /businesses, /products, /sync, /notifications
  - Cross-tenant: user1 cannot see user2's businesses

## 8. Future

- Polling worker: celery beat task that calls getUpdates and dispatches
- Active business selection: store per-user active business_id (currently first business)
- Inline keyboards: for business selection, product actions
- Webhook secret verification: Telegram supports X-Telegram-Bot-Api-Secret-Token header, enforce in prod
- Rate limiting: per platform_user_id, not just IP
- Rich media: send product preview as photo with caption
