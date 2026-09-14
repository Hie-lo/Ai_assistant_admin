#!/usr/bin/env python3
"""Live certification of the BALE platform adapter (Phase 6, spec section 13).

The owner-approved activation gate (2026-09-15): the adapter ships with
fake-client tests (no network), and a human operator runs THIS script on
the production server against the real shared org bot + a real channel to
certify the live contract BEFORE Bale publication is enabled.

Checks (PASS / FAIL / SKIP):
  1. getMe              — the shared bot token works (bot id reported).
  2. getChat            — the target chat exists and is addressable.
  3. getChatMember      — the bot is creator/administrator of the chat.
  4. sendMessage        — a test message is published (markdown-escaped).
  5. editMessageText    — the test message is edited in place.
  6. sendPhoto          — a photo-by-URL message is published (optional:
                          BALE_CERT_PHOTO_URL; skipped when not provided).
  7. sendMediaGroup     — STAGED live limit probe: albums of 2, 5, 10
                          (and --album-size if larger) are published until
                          one fails, measuring Bale's true live item limit
                          (Telegram-documented minimum is 2 items; single
                          media is covered by sendPhoto above).
                          NOTE: Bale downloads the media URL itself — if
                          the URL is unreachable from Bale's network the
                          probe fails; pass --photo-url with a direct
                          image URL reachable from there.
  8. editMessageCaption — the album caption is edited in place.
  9. deleteMessage      — all test messages (younger than 48h) are deleted.
 10. 48h-delete-limit   — (optional: BALE_CERT_OLD_MESSAGE_ID) a message
                          known to be OLDER than 48h is passed to
                          deleteMessage and the API is expected to REJECT
                          it. WARNING: if the message is younger than 48h
                          it WILL be deleted — use a sacrificial one.
 11. rate-limit probe   — (optional: BALE_CERT_RATE_PROBE=<count>) fire
                          <count> messages back to back and report whether
                          429 + retry_after appears.
 12. wire diagnostic    — automatic when the album check fails: probes
                          all three documented wire encodings of the
                          media param (native array / JSON-string in a
                          JSON body / JSON-string in a form body)
                          directly against the live API and reports
                          which one Bale accepts.

The token is read from the environment (BALE_BOT_TOKEN) and is never
printed; anything that could contain it is redacted. Exit code 0 = all
non-optional checks PASS (Bale may be enabled); 1 = at least one FAIL.

Usage (on the server, from the repo root):
    BALE_BOT_TOKEN=<token> BALE_CERT_CHAT=@your_channel \
    python scripts/certify_platform.py --platform bale \
        [--photo-url URL] [--album-size N] [--rate-probe N] \
        [--old-message-id ID]
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time

# Make the repo importable when run from any directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.domain import enums  # noqa: E402
from app.infrastructure.platforms.bale import (  # noqa: E402
    BaleError,
    HttpBaleClient,
)

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

_TOKEN: str = ""


def _redact(value: object) -> str:
    """Never leak the token into output (rule 14)."""
    s = str(value)
    if _TOKEN:
        s = s.replace(_TOKEN, "****")
    return s


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, status: str, detail: str) -> None:
        self.rows.append((name, status, detail))
        print(f"  [{status}] {name}: {_redact(detail)}")

    def ok(self) -> bool:
        return not any(s == FAIL for _, s, _ in self.rows)


def _diagnose_media_group_wire(base_url: str, token: str, chat_id: str, photo: str) -> None:
    """Live wire-format diagnostic (raw httpx, image-adapter independent).

    Tries a 2-item album in the three documented/SDK-backed encodings and
    reports which one the LIVE Bale API accepts:
      A) JSON body, media = native JSON array (Telegram-style)
      B) JSON body, media = JSON-serialized string (Bale docs wording +
         both community SDKs)
      C) form-encoded body, media = JSON-serialized string (exactly how
         the Go SDK posts: url.Values + json.Marshal)
    Only a winning variant creates messages; it deletes them at once.
    The token never appears in any output.
    """
    import httpx

    api = f"{base_url}/bot"
    items = [{"type": "photo", "media": photo}] * 2
    media_str = json.dumps(items)
    variants = [
        ("A: JSON body, native array", {"chat_id": chat_id, "media": items}, None),
        ("B: JSON body, JSON-serialized string", {"chat_id": chat_id, "media": media_str}, None),
        ("C: form body, JSON-serialized string", None, {"chat_id": chat_id, "media": media_str}),
    ]
    print()
    print("  == media-group wire-format diagnostic (2 items each) ==")
    for name, json_body, form_body in variants:
        try:
            if json_body is not None:
                resp = httpx.post(f"{api}{token}/sendMediaGroup", json=json_body, timeout=30)
            else:
                resp = httpx.post(f"{api}{token}/sendMediaGroup", data=form_body, timeout=30)
            data = resp.json()
        except Exception as exc:  # noqa: BLE001 - diagnostic prints, never crashes
            print(f"  [DIAG] {name}: request error -> {type(exc).__name__}")
            continue
        if data.get("ok"):
            mids = [m.get("message_id") for m in data.get("result", []) if isinstance(m, dict)]
            print(f"  [DIAG] {name}: ACCEPTED (message ids: {mids})")
            for mid in mids:
                with contextlib.suppress(Exception):
                    httpx.post(
                        f"{api}{token}/deleteMessage",
                        json={"chat_id": chat_id, "message_id": mid},
                        timeout=30,
                    )
            print("  [DIAG] test messages cleaned up")
            return
        desc = str(data.get("description") or "")[:120]
        print(f"  [DIAG] {name}: rejected -> {data.get('error_code')} ({desc})")
    print("  [DIAG] no encoding accepted — next step: contact Bale support with the above")


def main() -> int:
    global _TOKEN
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", default="bale", choices=["bale"])
    parser.add_argument("--chat", default=os.environ.get("BALE_CERT_CHAT", ""))
    parser.add_argument("--photo-url", default=os.environ.get("BALE_CERT_PHOTO_URL", ""))
    parser.add_argument(
        "--album-size",
        type=int,
        default=int(os.environ.get("BALE_CERT_ALBUM_SIZE", "10")),
    )
    parser.add_argument(
        "--rate-probe", type=int, default=int(os.environ.get("BALE_CERT_RATE_PROBE", "0"))
    )
    parser.add_argument(
        "--old-message-id", default=os.environ.get("BALE_CERT_OLD_MESSAGE_ID", "")
    )
    args = parser.parse_args()

    _TOKEN = os.environ.get("BALE_BOT_TOKEN", "")
    base_url = os.environ.get("BALE_API_BASE_URL", "https://tapi.bale.ai")

    print("== BALE live certification (Phase 6, spec section 13) ==")
    if not _TOKEN:
        print(f"  [{FAIL}] setup: BALE_BOT_TOKEN is not set in the environment")
        return 1
    if not args.chat:
        print(f"  [{FAIL}] setup: --chat / BALE_CERT_CHAT is not set")
        return 1
    print(f"  target chat: {args.chat!r}  base: {base_url}")
    from app.infrastructure.platforms import bale as _balemod

    print(
        "  adapter media wire format: "
        f"{getattr(_balemod, 'MEDIA_GROUP_WIRE_FORMAT', 'UNKNOWN - stale image, rebuild!')}"
    )

    report = Report()
    client = HttpBaleClient(base_url=base_url, token=_TOKEN, timeout=30.0)
    created: list[int] = []  # message ids we created (for cleanup)

    # 1. getMe -------------------------------------------------------------
    try:
        me = client.get_me()
        report.add("getMe", PASS, f"bot id={me.get('id')} username={me.get('username')}")
        bot_id = int(me.get("id") or 0)
    except BaleError as exc:
        report.add("getMe", FAIL, f"{exc.code.value}: {exc.detail}")
        return _finish(report, created)

    # 2. getChat -----------------------------------------------------------
    try:
        chat = client.get_chat(args.chat)
        chat_id = str(chat.get("id") or args.chat)
        report.add("getChat", PASS, f"chat id={chat_id} type={chat.get('type')}")
    except BaleError as exc:
        report.add("getChat", FAIL, f"{exc.code.value}: {exc.detail}")
        return _finish(report, created)

    # 3. getChatMember -----------------------------------------------------
    try:
        member = client.get_chat_member(chat_id, bot_id)
        status = member.get("status")
        if status in ("creator", "administrator"):
            report.add("getChatMember", PASS, f"bot status={status}")
        else:
            report.add(
                "getChatMember", FAIL, f"bot status={status!r} — add the bot as admin"
            )
    except BaleError as exc:
        report.add("getChatMember", FAIL, f"{exc.code.value}: {exc.detail}")
        return _finish(report, created)

    # 4. sendMessage -------------------------------------------------------
    marker_text = f"certification {int(time.time())} *special* _chars_ [x](y) \\ done"
    test_mid: int | None = None
    try:
        test_mid = client.send_message(chat_id, marker_text)
        created.append(test_mid)
        report.add("sendMessage", PASS, f"message_id={test_mid} (markdown escaped)")
    except BaleError as exc:
        report.add("sendMessage", FAIL, f"{exc.code.value}: {exc.detail}")

    # 5. editMessageText ---------------------------------------------------
    if test_mid is not None:
        try:
            client.edit_message_text(chat_id, test_mid, marker_text + " (edited)")
            report.add("editMessageText", PASS, f"message_id={test_mid}")
        except BaleError as exc:
            report.add("editMessageText", FAIL, f"{exc.code.value}: {exc.detail}")
    else:
        report.add("editMessageText", SKIP, "no test message")

    # 6. sendPhoto (optional) ----------------------------------------------
    if args.photo_url:
        try:
            photo_mid = client.send_photo(
                chat_id, args.photo_url, caption="certification photo"
            )
            created.append(photo_mid)
            report.add("sendPhoto", PASS, f"message_id={photo_mid} (via URL)")
        except BaleError as exc:
            report.add("sendPhoto", FAIL, f"{exc.code.value}: {exc.detail}")
    else:
        report.add("sendPhoto", SKIP, "no --photo-url provided")

    # 7. sendMediaGroup (staged live limit probe) ------------------------------
    # IMPORTANT: Bale's servers download the media URL THEMSELVES. If the
    # URL is unreachable from Bale's network (e.g. imgur is blocked in
    # Iran), the API answers 500 even for a 1-item album. Pass
    # --photo-url with a direct image URL reachable from Bale's network;
    # the probe then measures the true live item-count limit.
    photo = args.photo_url or "https://i.imgur.com/1V10c1P.jpg"
    album_mids: list[int] = []
    # Albums start at 2 items: Telegram (which Bale mirrors) documents a
    # 2-10 item minimum for sendMediaGroup, and single media is covered
    # by the sendPhoto check above (production routes 1 photo there too).
    sizes = sorted({2, 5, 10, max(args.album_size, 2)})
    measured_max: int | None = None
    last_err = ""
    for size in sizes:
        try:
            album_mids = client.send_media_group(
                chat_id, [photo] * size, caption=f"certification album ({size})"
            )
            created.extend(album_mids)
            measured_max = size
            print(f"  album probe: {size} item(s) OK")
        except BaleError as exc:
            last_err = f"{exc.code.value}: {exc.detail}"
            print(f"  album probe: {size} item(s) FAILED -> {last_err}")
            break
    if album_mids:
        report.add(
            "sendMediaGroup",
            PASS,
            f"album of {measured_max} accepted; live item limit is >= "
            f"{measured_max}"
            + ("" if measured_max == sizes[-1] else " (raise --album-size to measure higher)"),
        )
    else:
        report.add(
            "sendMediaGroup",
            FAIL,
            f"even the minimum 2-item album failed -> {last_err}; if the "
            "error is a URL fetch problem, use --photo-url with a direct "
            "image URL reachable from Bale's network",
        )
        # The adapter's encoding did not work live. Probe ALL documented
        # wire encodings directly (independent of the image's adapter
        # version) to learn exactly what Bale accepts:
        _diagnose_media_group_wire(base_url, _TOKEN, chat_id, photo)

    # 8. editMessageCaption --------------------------------------------------
    if album_mids:
        try:
            client.edit_message_caption(chat_id, album_mids[0], "certification album (edited)")
            report.add("editMessageCaption", PASS, f"message_id={album_mids[0]}")
        except BaleError as exc:
            report.add("editMessageCaption", FAIL, f"{exc.code.value}: {exc.detail}")
    else:
        report.add("editMessageCaption", SKIP, "no album")

    # 10. 48h delete limit (optional, BEFORE the young-message cleanup) -----
    if args.old_message_id:
        try:
            client.delete_message(chat_id, int(args.old_message_id))
            report.add(
                "48h-delete-limit",
                FAIL,
                "deleteMessage SUCCEEDED on a message that should be older "
                "than 48h — check the message (it was deleted)",
            )
        except BaleError as exc:
            report.add(
                "48h-delete-limit",
                PASS,
                f"API rejected the old delete as expected ({exc.code.value}) — "
                "lingering policy is the correct handling",
            )
    else:
        report.add("48h-delete-limit", SKIP, "no --old-message-id provided")

    # 11. rate-limit probe (optional) ---------------------------------------
    if args.rate_probe > 1:
        probe_ids: list[int] = []
        rate_limited = 0
        max_retry = 0.0
        try:
            for i in range(args.rate_probe):
                try:
                    probe_ids.append(client.send_message(chat_id, f"rate probe {i}"))
                except BaleError as exc:
                    if exc.code is enums.PublicationErrorCode.RATE_LIMITED:
                        rate_limited += 1
                        max_retry = max(max_retry, exc.retry_after)
                    else:
                        raise
            created.extend(probe_ids)
            report.add(
                "rate-limit-probe",
                PASS,
                f"{len(probe_ids)}/{args.rate_probe} sent, 429s seen: "
                f"{rate_limited}, max retry_after={max_retry:.0f}s",
            )
        except BaleError as exc:
            report.add("rate-limit-probe", FAIL, f"{exc.code.value}: {exc.detail}")
    else:
        report.add("rate-limit-probe", SKIP, "no --rate-probe provided")

    # 9. deleteMessage cleanup (young messages must delete fine) ------------
    if created:
        deleted = 0
        for mid in created:
            try:
                client.delete_message(chat_id, mid)
                deleted += 1
            except BaleError as exc:
                report.add(
                    "deleteMessage",
                    FAIL,
                    f"could not delete our own fresh message {mid}: "
                    f"{exc.code.value}: {exc.detail}",
                )
                break
        else:
            report.add(
                "deleteMessage",
                PASS,
                f"all {deleted} fresh test message(s) deleted (48h window OK)",
            )
        print(f"  cleanup: {deleted}/{len(created)} test messages deleted")
    else:
        report.add("deleteMessage", SKIP, "no test messages were created")

    return _finish(report, created)


def _finish(report: Report, created: list[int]) -> int:
    print()
    print("== summary ==")
    for name, status, _ in report.rows:
        print(f"  [{status}] {name}")
    if report.ok():
        print("\nRESULT: CERTIFIED — Bale publication may be enabled.")
        return 0
    print("\nRESULT: NOT CERTIFIED — fix the FAIL items and re-run.")
    if created:
        print(
            "WARNING: "
            f"{len(created)} test message(s) may remain in the chat; "
            "delete them manually."
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
