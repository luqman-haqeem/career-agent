"""Convert the agent's standard Markdown into Telegram-safe HTML.

Telegram's HTML mode supports a small tag set (<b> <i> <u> <s> <code> <pre>
<a>) and only requires escaping & < >. That's far less error-prone than
MarkdownV2, which needs ~18 characters escaped and 400s on the slightest slip.
"""
import re

_PLACEHOLDER = "\x00{}\x00"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_telegram_html(text: str) -> str:
    stash: list[str] = []

    def keep(html: str) -> str:
        stash.append(html)
        return _PLACEHOLDER.format(len(stash) - 1)

    # 1) Fenced code blocks -> <pre> (stashed so nothing inside gets mangled).
    text = re.sub(
        r"```[a-zA-Z0-9_+-]*\n?(.*?)```",
        lambda m: keep("<pre>" + _esc(m.group(1).rstrip("\n")) + "</pre>"),
        text, flags=re.S)
    # 2) Inline code -> <code>.
    text = re.sub(r"`([^`\n]+)`",
                  lambda m: keep("<code>" + _esc(m.group(1)) + "</code>"), text)
    # 3) Links [text](url) -> <a>.
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^\s)]+)\)",
        lambda m: keep(f'<a href="{_esc(m.group(2))}">{_esc(m.group(1))}</a>'), text)

    # 4) Escape the remaining literal text (placeholders contain no & < >).
    text = _esc(text)

    # 5) Bold then italic (bold first so ** isn't eaten by the * rule).
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"<b>\1</b>", text, flags=re.S)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", text)
    text = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<i>\1</i>", text)

    # 6) Headings -> bold lines; bullets -> "• "; per line.
    out = []
    for ln in text.split("\n"):
        h = re.match(r"^\s*#{1,6}\s+(.*)$", ln)
        if h:
            out.append("<b>" + h.group(1).rstrip() + "</b>")
            continue
        b = re.match(r"^(\s*)[-*]\s+(.*)$", ln)
        if b:
            out.append(b.group(1) + "• " + b.group(2))
            continue
        out.append(ln)
    text = "\n".join(out)

    # 7) Restore stashed code/links.
    for i, html in enumerate(stash):
        text = text.replace(_PLACEHOLDER.format(i), html)
    return text


def to_plain(md: str) -> str:
    """Strip Markdown to clean plain text (fallback if HTML send fails)."""
    md = re.sub(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", r"\1", md, flags=re.S)
    md = re.sub(r"`([^`\n]+)`", r"\1", md)
    md = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1 (\2)", md)
    md = re.sub(r"\*\*(.+?)\*\*", r"\1", md, flags=re.S)
    md = re.sub(r"(?<!\w)__(.+?)__(?!\w)", r"\1", md, flags=re.S)
    md = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", md)
    md = re.sub(r"^\s*#{1,6}\s+", "", md, flags=re.M)
    md = re.sub(r"^(\s*)[-*]\s+", r"\1• ", md, flags=re.M)
    return md


def chunk(text: str, size: int = 4000):
    """Split on line boundaries, keeping chunks under Telegram's 4096 limit."""
    buf = ""
    for ln in text.split("\n"):
        while len(ln) > size:
            if buf:
                yield buf
                buf = ""
            yield ln[:size]
            ln = ln[size:]
        if buf and len(buf) + len(ln) + 1 > size:
            yield buf
            buf = ""
        buf = ln if not buf else buf + "\n" + ln
    if buf:
        yield buf
