# Platform Adapter & Capability Specification v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Draft for final cross-domain review
Date: 2026-09-12

## 1. Purpose
Isolate platform-specific behavior from the domain core. The core requests semantic operations; adapters translate them to each platform's verified API behavior.

## 2. Adapter contract
Conceptual operations:
- verify_connection
- get_target_info
- get_capabilities
- validate_content
- publish
- edit_text_or_caption when supported
- edit_media when supported
- delete
- inspect_remote_publication when supported
- reconcile_publication when supported
- normalize_platform_error
- get_rate_limit_hint

The exact implementation is a design decision after technology selection.

## 3. Capability matrix
Every platform declares capabilities rather than inheriting assumptions from another platform:
- send text
- send single media
- send media group/album
- edit text/caption
- replace media
- delete
- inspect remote state
- identify administrators/permissions
- webhook/polling
- remote message lookup
- message length
- caption length
- media count
- file size/type constraints
- rate limits
- formatting rules

## 4. Telegram
Official Telegram Bot API supports message editing, deletion, media groups and administrator/chat methods. Text length for sendMessage is 1–4096 characters after entities parsing. citeturn288425search0
Telegram capability data must still be represented in the adapter instead of hard-coded throughout the core.

## 5. Bale
Official Bale documentation states its Bot API is based on Telegram Bot API with changes, and documents methods including editMessageText, editMessageCaption and deleteMessage. Bale IDs may exceed 32-bit; the documentation recommends signed 64-bit storage for IDs that can be large. citeturn288425search1
Exact limits/capabilities must be validated in adapter integration tests before activation.

## 6. Eitaa
Eitaa connection is different from Telegram: publicly available EitaaYar materials describe token-based API access and channel/group management through the EitaaYar service. Public evidence is not sufficient to assume Telegram feature parity. citeturn613283search0turn613283search2
Therefore V1 Eitaa support must be capability-tested. If current verified integration lacks edit or multi-media support, the adapter must advertise those capabilities as false and the publication state machine must route media/content changes to safe republish behavior.
The previously stated rule “Eitaa must only send the first image” is a product policy candidate, not an implementation fact, until current API behavior is verified by test.

## 7. Rubika
Current public official web evidence located in this review confirms the Rubika service but did not provide a sufficiently detailed official bot API contract. citeturn288425search2
Rubika must therefore remain integration-blocked until an authoritative API specification and live compatibility tests are available.

## 8. No silent capability fallback
If a platform lacks edit, the system must never pretend to edit. It returns an explicit UNSUPPORTED capability and the domain policy selects another action such as repost.

## 9. Content rendering
Generic Post -> platform renderer -> platform validator -> final payload.
No blind truncation. Content sections have priorities and a platform renderer may compact/remove optional sections according to policy.

## 10. Publication identity
Each remote publication stores platform name, remote target identifier, remote message identifier(s), platform version/capability snapshot reference, and publication state.

## 11. Connection verification
The adapter must prove technical control and required permissions. Legal ownership is a separate concern.
Connection checks must be re-runnable after reconnect and must detect loss of permission.

## 12. Rate limits
No hard-coded global delay is assumed. Each adapter reports verified rate constraints and retry/backoff behavior. Queue workers enforce these limits.

## 13. Integration certification
Before enabling a platform for production:
- API contract reviewed
- credentials stored securely
- send tested
- edit tested if claimed
- delete tested if claimed
- media tested
- limits measured/confirmed
- timeout/duplicate recovery tested
- permission loss tested
- reconnect tested
- remote manual edit/delete tested
- rate limiting tested
- live or staging smoke tests documented

## 14. Platform activation gate
A platform can only move to ACTIVE after the certification checklist passes. Unsupported features remain explicit rather than emulated unsafely.
