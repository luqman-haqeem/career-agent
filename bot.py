"""Telegram front-end for the Career Agent (runs on your Claude subscription)."""
import asyncio
import datetime as dt
import html
import json
import logging
import re
import shutil
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import config
import jobs_store
import render
import scan
import telegram_format
from agent import run_turn

# Files Claude Code's Read tool handles natively (no pre-extraction needed).
NATIVE_READ_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp",
                   ".txt", ".md", ".markdown", ".csv"}

# Only these get sent back to the user as generated documents.
SEND_BACK_EXT = {".md", ".pdf", ".docx", ".txt"}

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
log = logging.getLogger("career-agent")


# --- Per-chat session id (Claude conversation continuity) ------------------
def _session_path(chat_id: int) -> Path:
    return config.SESSIONS_DIR / f"{chat_id}.json"


def load_session_id(chat_id: int):
    p = _session_path(chat_id)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8")).get("session_id")
        except Exception:  # noqa: BLE001
            return None
    return None


def save_session_id(chat_id: int, session_id) -> None:
    if session_id:
        _session_path(chat_id).write_text(
            json.dumps({"session_id": session_id}), encoding="utf-8")


def _transcript_path(session_id: str):
    """Locate Claude Code's JSONL transcript for a session, if it exists.

    The CLI stores it under ~/.claude/projects/<escaped-cwd>/<session_id>.jsonl;
    we just search by name so we don't depend on the exact folder encoding.
    """
    if not session_id:
        return None
    root = Path.home() / ".claude" / "projects"
    if not root.exists():
        return None
    for p in root.rglob(f"{session_id}.jsonl"):
        return p
    return None


def _context_stats(chat_id: int) -> dict:
    """Return how big the current conversation is (entries + size).

    Sizing relies on the Claude CLI transcript file. The OpenCode backend stores
    sessions in its own location, so there we only report whether a session is
    active (sized=False) rather than guessing a byte count.
    """
    session_id = load_session_id(chat_id)
    if config.AI_BACKEND == "opencode":
        return {"active": bool(session_id), "sized": False}
    path = _transcript_path(session_id) if session_id else None
    if not path:
        return {"active": False, "sized": True, "entries": 0, "kb": 0}
    try:
        kb = round(path.stat().st_size / 1024)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            entries = sum(1 for _ in fh)
    except OSError:
        return {"active": bool(session_id), "sized": True, "entries": 0, "kb": 0}
    return {"active": True, "sized": True, "entries": entries, "kb": kb}


def _status_text(chat_id: int) -> str:
    s = _context_stats(chat_id)
    if not s["active"]:
        return ("📊 <b>Conversation status</b>\n\n"
                "🟢 Fresh — no active conversation yet. The next message starts one.\n\n"
                "Your long-term memory (profile, goals, experiences) is separate and "
                "always kept.")
    if not s.get("sized", True):
        return ("📊 <b>Conversation status</b>\n\n"
                f"🟢 Active conversation (backend: <b>{config.AI_BACKEND}</b>).\n"
                "Size isn't tracked on this backend, but a long chat can still slow "
                "replies — reset anytime to start fresh.\n\n"
                "Your long-term memory (profile, goals, experiences) is <b>separate</b> "
                "and untouched by a reset.\n\n"
                "Tap below to clear the conversation and start fresh.")
    entries, kb = s["entries"], s["kb"]
    if entries < 40:
        state = "🟢 Fresh — fast replies."
    elif entries < 100:
        state = "🟡 Getting long — replies may start to slow down."
    else:
        state = "🔴 Large — consider resetting to speed things up."
    return ("📊 <b>Conversation status</b>\n\n"
            "This is the chat history that reloads <i>every</i> time you message me.\n"
            f"• Entries: <b>{entries}</b>\n"
            f"• Size: <b>~{kb} KB</b>\n"
            f"• State: {state}\n\n"
            "Your long-term memory (profile, goals, experiences) is <b>separate</b> "
            "and untouched by a reset.\n\n"
            "Tap below to clear the conversation and start fresh.")


def _status_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🧹 Reset context", callback_data="reset_context")]])


def _clear_session(chat_id: int) -> None:
    p = _session_path(chat_id)
    if p.exists():
        p.unlink()


def _allowed(update: Update) -> bool:
    if not config.ALLOWED_USER_IDS:
        return True
    return bool(update.effective_user
                and update.effective_user.id in config.ALLOWED_USER_IDS)


async def _send_chat(bot, chat_id: int, text: str) -> None:
    """Send text rendered as Telegram HTML, with a plain-text fallback."""
    for piece in telegram_format.chunk(text):
        try:
            await bot.send_message(
                chat_id, telegram_format.to_telegram_html(piece), parse_mode="HTML")
        except Exception as e:  # noqa: BLE001 - bad markup etc.: degrade gracefully
            log.warning("HTML send failed (%s); falling back to plain text", e)
            await bot.send_message(chat_id, telegram_format.to_plain(piece))


async def _send(update: Update, text: str) -> None:
    await _send_chat(update.get_bot(), update.effective_chat.id, text)


def _resume_snapshot() -> dict:
    """Map filename -> mtime, so we catch both new AND updated files."""
    snap = {}
    for p in config.RESUMES_DIR.glob("*"):
        try:
            snap[p.name] = p.stat().st_mtime
        except OSError:
            pass
    return snap


# --- Handlers --------------------------------------------------------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else "unknown"
    await update.message.reply_text(
        "👋 I'm your Career Agent.\n\n"
        f"Your Telegram user ID is: {uid}\n"
        "(Put this in ALLOWED_USER_IDS in .env to keep the bot private.)\n\n"
        "Send me:\n"
        "• your existing resume as a file (PDF / DOCX) or a photo → I read it and pull your real experience into memory\n"
        "• an experience or project as text → I structure it into a CV point and remember it\n"
        "• a job description link or pasted text → I assess your fit and draft a tailored resume\n"
        "• your goals, vision, or long-term plans → I store them\n\n"
        "I will never invent experience you don't have.\n\n"
        "Commands: /help  /reset")


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "How to use me:\n\n"
        "📎 Send your resume: upload a PDF/DOCX file or a photo of it — I read it and store your real experience.\n"
        "📌 Build your memory: tell me about your role, goals, and what you've worked on.\n"
        "🧱 Add experience: describe something you did — I'll structure it (Situation/Task/Action/Result/Metrics) and save it.\n"
        "🎯 Check a job: paste a JD or a link. I'll rate the fit and name the gaps.\n"
        "📄 Tailored resume: ask for a resume for a JD — built only from what I actually know about you.\n\n"
        "/status shows how big the current conversation is, with a one-tap Reset button.\n"
        "/reset starts a fresh conversation (your saved profile, goals, experiences and projects are kept).")


async def reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    _clear_session(update.effective_chat.id)
    await update.message.reply_text("🧹 Started a fresh conversation. Your long-term memory is untouched.")


async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("Sorry — this is a private bot.")
        return
    await update.message.reply_text(
        _status_text(update.effective_chat.id),
        parse_mode="HTML", reply_markup=_status_keyboard())


async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not _allowed(update):
        await query.answer("Private bot.", show_alert=True)
        return
    data = query.data or ""
    if data.startswith("job:"):
        await _on_job_action(update, ctx, data)
        return
    if query.data == "reset_context":
        _clear_session(update.effective_chat.id)
        await query.answer("Conversation cleared ✅")
        try:
            await query.edit_message_text(
                "🧹 <b>Conversation cleared.</b> Started fresh — your long-term "
                "memory (profile, goals, experiences) is untouched.",
                parse_mode="HTML")
        except Exception:  # noqa: BLE001 - message may be unchanged/too old to edit
            await query.message.reply_text("🧹 Conversation cleared. Memory kept.")


async def _keep_typing(bot, chat_id: int) -> None:
    """Re-send the 'typing…' action every few seconds during a long turn."""
    try:
        while True:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def _run_and_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                         prompt: str, files=None) -> None:
    """Run one agent turn for this chat and send back text + any new resume.

    `files` are upload paths attached for backends that need them (OpenCode uses
    --file; the Claude path reads via the path in the prompt and ignores them).
    """
    chat_id = update.effective_chat.id
    session_id = load_session_id(chat_id)
    before = _resume_snapshot()

    typing = asyncio.create_task(_keep_typing(ctx.bot, chat_id))
    try:
        text, session_id = await run_turn(prompt, session_id, files=files)
    except Exception as e:  # noqa: BLE001
        log.exception("turn failed")
        await update.message.reply_text(f"⚠️ Something went wrong: {e}")
        return
    finally:
        typing.cancel()

    save_session_id(chat_id, session_id)
    await _send(update, text)
    await _deliver_new_files(update, before)


async def _send_doc_chat(bot, chat_id: int, path: Path) -> None:
    try:
        with open(path, "rb") as fh:
            await bot.send_document(chat_id, document=fh, filename=path.name)
    except Exception as e:  # noqa: BLE001
        log.warning("could not send %s: %s", path, e)


async def _send_doc(update: Update, path: Path) -> None:
    await _send_doc_chat(update.get_bot(), update.effective_chat.id, path)


async def _deliver_changed_resumes(bot, chat_id: int, before: dict) -> None:
    """Render any new/updated resume JSON to PDF and send it; send other docs.

    Compares mtimes so an *updated* resume (same filename) is re-rendered and
    re-sent. Skips scratch/helper files (e.g. _make_pdf.py).
    """
    after = _resume_snapshot()
    changed = sorted(name for name, mtime in after.items()
                     if not name.startswith((".", "_"))
                     and mtime != before.get(name))
    for name in changed:
        path = config.RESUMES_DIR / name
        ext = path.suffix.lower()
        if ext == ".json":
            try:
                pdf = await asyncio.to_thread(render.render_json_to_pdf, path)
                await _send_doc_chat(bot, chat_id, pdf)
            except Exception as e:  # noqa: BLE001
                log.warning("PDF render failed for %s: %s", name, e)
                await bot.send_message(
                    chat_id,
                    "⚠️ I built your resume but couldn't render the PDF. "
                    "Sending the data file instead.")
            await _send_doc_chat(bot, chat_id, path)  # JSON Resume file (portable)
        elif ext in SEND_BACK_EXT:
            await _send_doc_chat(bot, chat_id, path)


async def _deliver_new_files(update: Update, before: dict) -> None:
    await _deliver_changed_resumes(
        update.get_bot(), update.effective_chat.id, before)


# --- Job discovery ---------------------------------------------------------
_SCAN_TZ = ZoneInfo(config.SCAN_TZ)  # validated at import; reused by scheduler + scan gate
_in_flight_applies: set = set()  # job ids currently generating a resume (double-tap guard)


def _job_card_html(job: dict) -> str:
    title = html.escape(job.get("title", "Role"))
    company = html.escape(job.get("company", ""))
    location = html.escape(job.get("location", "") or "—")
    score = job.get("fit_score")
    score_line = f"Fit <b>{score}/10</b>" if score is not None else "Fit: n/a"
    why_fit = html.escape(job.get("why_fit", "") or "")
    why_aligns = html.escape(job.get("why_aligns", "") or "")
    url = html.escape(job.get("url", "") or "")
    if url and not url.lower().startswith(("https://", "http://")):
        url = ""  # drop non-http schemes (e.g. javascript:) before putting in href
    lines = [f"<b>{title}</b> @ {company} · {location}", score_line]
    if why_fit:
        lines.append(f"💪 {why_fit}")
    if why_aligns:
        lines.append(f"🎯 {why_aligns}")
    if url:
        lines.append(f'🔗 <a href="{url}">View posting</a>')
    return "\n".join(lines)


async def _send_job_card(bot, chat_id: int, job: dict) -> None:
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📄 Apply", callback_data=f"job:apply:{job['id']}"),
        InlineKeyboardButton("⏭ Skip", callback_data=f"job:skip:{job['id']}"),
    ]])
    await bot.send_message(
        chat_id, _job_card_html(job), parse_mode="HTML", reply_markup=kb)


async def _on_job_action(update: Update, ctx: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    query = update.callback_query
    try:
        _, action, jid = data.split(":", 2)
    except ValueError:
        await query.answer()
        return
    job = jobs_store.get(jid)
    if not job:
        await query.answer("That job is no longer available.", show_alert=True)
        return

    if action == "skip":
        jobs_store.set_state(jid, "skipped")
        await query.answer("Skipped ⏭")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001 - message too old / unchanged
            pass
        return

    if action == "apply":
        if jid in _in_flight_applies:
            await query.answer("Already generating — please wait.", show_alert=True)
            return
        await query.answer("Tailoring your resume…")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # noqa: BLE001
            pass
        _in_flight_applies.add(jid)
        try:
            await _generate_resume_for(ctx, update.effective_chat.id, job, jid)
        finally:
            _in_flight_applies.discard(jid)
        return

    await query.answer()  # unknown action — dismiss the spinner


async def _generate_resume_for(ctx: ContextTypes.DEFAULT_TYPE, chat_id: int, job: dict, jid: str) -> None:
    session_id = load_session_id(chat_id)
    before = _resume_snapshot()
    prompt = (
        "The user chose to apply to this job from a discovery scan. Build a tailored "
        "resume for it, following ALL your resume rules (never fabricate).\n\n"
        f"Job title: {job.get('title')}\n"
        f"Company: {job.get('company')}\n"
        f"Location: {job.get('location')}\n"
        f"Link: {job.get('url')}\n\n"
        "If the link is reachable, WebFetch it to read the full JD; if it's blocked, "
        "tailor from the details above and the user's memory. Save the resume JSON to "
        "resumes/ as usual so the PDF is generated, then briefly tell me what you "
        "emphasized and any real gaps."
    )
    await ctx.bot.send_message(
        chat_id,
        f"📄 Tailoring your resume for {job.get('title')} @ {job.get('company')} — about a minute…")
    typing = asyncio.create_task(_keep_typing(ctx.bot, chat_id))
    try:
        text, session_id = await run_turn(prompt, session_id)
    except Exception as e:  # noqa: BLE001
        log.exception("resume generation failed")
        await ctx.bot.send_message(chat_id, f"⚠️ Couldn't build the resume: {e}")
        return
    finally:
        typing.cancel()

    save_session_id(chat_id, session_id)
    jobs_store.set_state(jid, "applied")
    await _send_chat(ctx.bot, chat_id, text)
    await _deliver_changed_resumes(ctx.bot, chat_id, before)


async def _do_scan(bot, chat_id: int, manual: bool) -> None:
    try:
        matches = await scan.run_scan()
    except scan.ScanError as e:
        log.warning("scan failed: %s", e)
        if manual:
            await bot.send_message(chat_id, f"⚠️ Scan failed — {e}. Try again later.")
        return
    if not matches:
        if manual or not config.SILENT_WHEN_EMPTY:
            await bot.send_message(chat_id, "🔍 No new strong matches this time.")
        return
    await bot.send_message(chat_id, f"🔍 Found {len(matches)} new match(es):")
    for job in matches:
        await _send_job_card(bot, chat_id, job)


def _owner_chat_id() -> int:
    if config.OWNER_CHAT_ID:
        return config.OWNER_CHAT_ID
    if len(config.ALLOWED_USER_IDS) == 1:
        return next(iter(config.ALLOWED_USER_IDS))
    return 0


async def _scheduled_scan(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Daily timer; only actually scans on configured weekdays (Monday=0)."""
    today = dt.datetime.now(_SCAN_TZ).weekday()
    if today not in config.SCAN_WEEKDAYS:
        return
    chat_id = _owner_chat_id()
    if not chat_id:
        log.warning("Scheduled scan skipped: set OWNER_CHAT_ID in .env.")
        return
    await _do_scan(ctx.bot, chat_id, manual=False)


async def scan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("Sorry — this is a private bot.")
        return
    await update.message.reply_text("🔍 Scanning for jobs… this can take a minute.")
    try:
        await _do_scan(ctx.bot, update.effective_chat.id, manual=True)
    except Exception as e:  # noqa: BLE001
        log.exception("scan_cmd failed")
        await update.message.reply_text(f"⚠️ Scan error — {e}. Try again later.")


def _safe_name(name: str) -> str:
    base = Path(name or "").name or "upload"
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:80]


def _docx_to_text(path: Path):
    try:
        import docx  # python-docx
    except ImportError:
        return None
    try:
        doc = docx.Document(str(path))
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        return "\n".join(parts).strip() or None
    except Exception:  # noqa: BLE001
        return None


async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("Sorry — this is a private bot.")
        return
    await _run_and_reply(update, ctx, update.message.text)


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("Sorry — this is a private bot.")
        return
    doc = update.message.document
    caption = (update.message.caption or "").strip()
    dest = config.UPLOADS_DIR / _safe_name(doc.file_name or f"{doc.file_unique_id}")
    try:
        tg_file = await ctx.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(dest))
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(
            f"⚠️ Couldn't download that file ({e}). Telegram caps bot downloads "
            "at ~20 MB — try a smaller file or paste the text.")
        return

    ext = dest.suffix.lower()
    note = f" The user added this note: {caption!r}." if caption else ""
    attach = None  # files attached for backends that need them (e.g. OpenCode --file)

    if ext == ".docx":
        extracted = _docx_to_text(dest)
        if not extracted:
            await update.message.reply_text(
                "I couldn't read that .docx. Please export it to PDF and resend, "
                "or paste the text.")
            return
        # Text is inlined below, so no need to attach the binary .docx.
        prompt = (f"The user uploaded their existing resume/CV ('{dest.name}'). "
                  f"Here is its full extracted text:{note}\n\n----\n{extracted}\n----\n\n"
                  "Extract their REAL experiences, skills, and profile from it and "
                  "save them into memory following your rules. Never invent anything "
                  "not present in the document. Then briefly summarize what you saved.")
    elif ext in NATIVE_READ_EXT:
        attach = [dest]
        prompt = (f"The user uploaded a document, saved at `uploads/{dest.name}` "
                  f"(most likely their existing resume/CV).{note} Read that file, then "
                  "extract their REAL experiences, skills, and profile and save them "
                  "into memory following your rules. Never invent anything not present "
                  "in the document. Then briefly summarize what you saved and any gaps.")
    else:
        await update.message.reply_text(
            f"I can read PDF, images, .txt/.md, and .docx. '{dest.name}' isn't one of "
            "those — please export it to PDF and resend.")
        return

    await _run_and_reply(update, ctx, prompt, files=attach)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update):
        await update.message.reply_text("Sorry — this is a private bot.")
        return
    photo = update.message.photo[-1]  # largest size
    caption = (update.message.caption or "").strip()
    dest = config.UPLOADS_DIR / f"photo-{photo.file_unique_id}.jpg"
    try:
        tg_file = await ctx.bot.get_file(photo.file_id)
        await tg_file.download_to_drive(str(dest))
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"⚠️ Couldn't download that image ({e}).")
        return

    note = f" The user added this note: {caption!r}." if caption else ""
    prompt = (f"The user sent an image, saved at `uploads/{dest.name}` (likely a "
              f"resume, certificate, or document photo).{note} Read the image, then "
              "extract any REAL experiences, skills, or profile details and save them "
              "into memory following your rules. Never invent anything not visible in "
              "the image. Then briefly summarize what you saved.")
    await _run_and_reply(update, ctx, prompt, files=[dest])


def main() -> None:
    if not config.TELEGRAM_BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env (get it from @BotFather).")
    if config.AI_BACKEND == "opencode" and not (
            shutil.which(config.OPENCODE_BIN) or Path(config.OPENCODE_BIN).exists()):
        raise SystemExit(
            f"AI_BACKEND=opencode but the OpenCode CLI ('{config.OPENCODE_BIN}') was "
            "not found. Install it and/or set OPENCODE_BIN. See docs/opencode-setup.md.")
    if not config.ALLOWED_USER_IDS:
        log.warning("ALLOWED_USER_IDS is empty — the bot is open to anyone who "
                    "finds it. Send /start to learn your ID, then lock it down.")

    async def _set_commands(application: Application) -> None:
        await application.bot.set_my_commands([
            ("scan", "Search for new job matches now"),
            ("status", "Show conversation size + reset button"),
            ("reset", "Start a fresh conversation (memory kept)"),
            ("help", "How to use me"),
            ("start", "Intro & your Telegram ID"),
        ])

    app = (Application.builder()
           .token(config.TELEGRAM_BOT_TOKEN)
           .post_init(_set_commands)
           .build())
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.add_handler(CommandHandler("scan", scan_cmd))

    if config.JOB_DISCOVERY_ENABLED:
        if app.job_queue is None:
            log.warning("JobQueue unavailable — install python-telegram-bot[job-queue]. "
                        "Scheduled scans disabled; /scan still works.")
        else:
            app.job_queue.run_daily(
                _scheduled_scan,
                time=dt.time(hour=config.SCAN_HOUR, tzinfo=_SCAN_TZ),
                name="job-discovery-scan",
            )
            log.info("Job discovery scheduled daily at %02d:00 %s on weekdays %s.",
                     config.SCAN_HOUR, config.SCAN_TZ, sorted(config.SCAN_WEEKDAYS))

    backend_desc = ("Claude subscription (claude_cli)" if config.AI_BACKEND != "opencode"
                    else f"OpenCode (model: {config.OPENCODE_MODEL or 'default'})")
    log.info("Career Agent is running — backend: %s. Ctrl+C to stop.", backend_desc)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
