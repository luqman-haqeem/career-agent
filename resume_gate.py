"""Score a freshly written resume before the user ever sees it.

The bar the user set (2026-09-12): the FIRST resume they receive for a job
should already score 90 or more on the standard critique rubric in CLAUDE.md,
and when it cannot, the reply has to say so instead of quietly shipping an 82.

Two things make that happen. CLAUDE.md tells the agent to aim at the bar while
it drafts; this module is the check that the draft actually cleared it. The
agent grades its own work here, so the number is a self-assessment and not an
objective measure. The prompts below lean on that hard (grade strictly, defend
the number, never inflate to clear the bar), and the never-fabricate rules mean
a real gap has to come back as a low score rather than as an invented bullet.

Flow, all inside the thread whose session holds the JD:
    score -> clears the bar? ship it.
          -> under it? revise once, re-score, ship the best honest version and
             tell the user where it landed and what is capping it.
"""
import re

# The scoring turns answer with this marker so the number never has to be
# fished out of prose: `[[SCORE:86|thin AWS keyword coverage]]`. The reason
# side is optional, and everything after the first pipe is one short line.
SCORE_MARKER_RE = re.compile(r"\[\[SCORE:\s*(\d{1,3})\s*(?:\|([^\]]*))?\]\]")

# Fallback for a turn that ignored the marker and wrote a normal scorecard
# headline ("📄 Acme Backend — 86/100").
SCORE_PROSE_RE = re.compile(r"\b(\d{1,3})\s*/\s*100\b")


def parse_score(text: str):
    """Pull (score, reason) out of a scoring reply. (None, None) if absent.

    A score outside 0-100 is treated as no score at all: a model that answers
    "[[SCORE:950]]" has misunderstood the format, and passing that through
    would clear the bar on nonsense.
    """
    text = text or ""
    m = SCORE_MARKER_RE.search(text)
    reason = None
    if m:
        score = int(m.group(1))
        reason = (m.group(2) or "").strip() or None
    else:
        m = SCORE_PROSE_RE.search(text)
        if not m:
            return None, None
        score = int(m.group(1))
    if not 0 <= score <= 100:
        return None, None
    return score, reason


def strip_marker(text: str) -> str:
    """The reply text without the score marker (for logs)."""
    return SCORE_MARKER_RE.sub("", text or "").strip()


def score_prompt(name: str, target: int) -> str:
    return (
        f"Before this resume goes to the user, score it. Read resumes/{name}, "
        "memory/corrections.md, and the JD from this conversation, then apply "
        "your normal 8-dimension critique rubric and compute the weighted total.\n\n"
        "Grade it the way a hiring manager who has never seen this draft would: "
        "strictly, only on what is actually in the file, and with the accuracy "
        f"pass first. Do not round up and do not grade generously to clear {target}. "
        "An inflated score is worse than a low one, because the user sends the "
        "resume on the strength of it.\n\n"
        "Reply with EXACTLY these two lines and nothing else:\n"
        "[[SCORE:NN|one short line on what is capping the score]]\n"
        "<the single highest-impact fix, or 'none' if it clears the bar>"
    )


def revise_prompt(name: str, target: int, score: int, reason: str = None) -> str:
    why = f" ({reason})" if reason else ""
    return (
        f"That draft scored {score}/100, under the user's {target}/100 bar{why}. "
        f"Rewrite resumes/{name} in place with the changes that genuinely raise "
        "the score, following ALL your resume rules: never fabricate, no em "
        "dashes, en dashes or tildes, verbs that match what the user really did, "
        "nothing that contradicts memory/corrections.md.\n\n"
        "Wording, ordering, emphasis and truthful keyword coverage are yours to "
        "change. Real missing experience is NOT: if that is what caps the score, "
        "leave it missing and let the score stay low. Being told is exactly what "
        "the user asked for.\n\n"
        "Then re-score the rewritten file just as strictly and reply with EXACTLY "
        "these two lines and nothing else:\n"
        "[[SCORE:NN|what is capping it now, or 'clears the bar']]\n"
        "<one line on what you changed>"
    )


def verdict(score, target: int, reason: str = None, revised: bool = False) -> str:
    """The line appended to the user's reply. Always states where it landed."""
    if score is None:
        return ("⚠️ I couldn't confirm a score for this draft before sending it. "
                "Tap 📝 Critique it to score it properly.")
    if score >= target:
        lead = "✅ Revised before sending, now scores" if revised else "✅ Scored"
        return f"{lead} **{score}/100** against this JD (your bar: {target})."
    lines = [f"⚠️ This one tops out at **{score}/100**, under your {target} bar."]
    if reason:
        lines.append(f"What's capping it: {reason}")
    lines.append("I didn't pad it to clear the bar. Tap 📝 Critique it for the "
                 "full breakdown, or tell me what to change.")
    return "\n".join(lines)
