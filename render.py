"""Render a JSON Resume (jsonresume.org schema) into a polished LaTeX PDF.

The agent produces the *content* as `resumes/<name>.json`; this module turns it
into a PDF deterministically: normalize -> fill a LaTeX template (with proper
escaping) -> compile with Tectonic. No shell or LaTeX knowledge needed from the
model, and every field traces back to stored memory.
"""
import shutil
import subprocess
import tempfile
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

import config

# --- LaTeX escaping --------------------------------------------------------
_LATEX = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$",
    "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def tex_escape(value) -> str:
    if value is None:
        return ""
    out = []
    for ch in str(value):
        out.append(_LATEX.get(ch, ch))
    return "".join(out)


_env = Environment(
    loader=FileSystemLoader(str(config.BASE_DIR / "templates")),
    block_start_string="<%", block_end_string="%>",
    variable_start_string="<<", variable_end_string=">>",
    comment_start_string="<#", comment_end_string="#>",
    trim_blocks=True, lstrip_blocks=True, autoescape=False,
    undefined=ChainableUndefined,
)
_env.filters["tex"] = tex_escape


# --- Normalize JSON Resume into a flat, always-present structure -----------
def _dates(start, end) -> str:
    start, end = (start or "").strip(), (end or "").strip()
    if start and end:
        return f"{start} -- {end}"   # LaTeX renders "--" as an en-dash
    if start:
        return f"{start} -- Present"
    return end


def _normalize(data: dict) -> dict:
    b = data.get("basics") or {}
    loc = b.get("location") or {}

    contact = []
    if b.get("email"):
        contact.append(b["email"])
    if b.get("phone"):
        contact.append(b["phone"])
    where = ", ".join(x for x in (loc.get("city"), loc.get("region")) if x)
    if where:
        contact.append(where)
    if b.get("url"):
        contact.append(b["url"])
    for p in b.get("profiles") or []:
        if p.get("url"):
            net = p.get("network", "")
            contact.append(f"{net}: {p['url']}" if net else p["url"])

    work = [{
        "name": w.get("name") or w.get("company") or "",
        "position": w.get("position") or "",
        "location": w.get("location") or "",
        "summary": w.get("summary") or "",
        "dates": _dates(w.get("startDate"), w.get("endDate")),
        "highlights": [h for h in (w.get("highlights") or []) if h],
    } for w in (data.get("work") or [])]

    projects = [{
        "name": p.get("name") or "",
        "url": p.get("url") or "",
        "description": p.get("description") or "",
        "highlights": [h for h in (p.get("highlights") or []) if h],
    } for p in (data.get("projects") or [])]

    education = [{
        "institution": e.get("institution") or "",
        "studyArea": " ".join(x for x in (e.get("studyType"), e.get("area")) if x),
        "score": e.get("score") or "",
        "dates": _dates(e.get("startDate"), e.get("endDate")),
    } for e in (data.get("education") or [])]

    skills = [{
        "name": s.get("name") or "",
        "keywords": ", ".join(s.get("keywords") or []),
    } for s in (data.get("skills") or [])]

    return {
        "name": b.get("name") or "Resume",
        "label": b.get("label") or "",
        "contact": " | ".join(contact),
        "summary": b.get("summary") or "",
        "work": work, "projects": projects,
        "education": education, "skills": skills,
    }


def render_json_to_pdf(json_path) -> Path:
    """Render a resume.json to a PDF in RESUMES_DIR; return the PDF path."""
    import json
    json_path = Path(json_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    tex = _env.get_template("resume.tex.j2").render(**_normalize(data))

    stem = json_path.stem
    with tempfile.TemporaryDirectory() as td:
        tex_path = Path(td) / f"{stem}.tex"
        tex_path.write_text(tex, encoding="utf-8")
        subprocess.run(
            ["tectonic", "--chatter", "minimal", str(tex_path)],
            cwd=td, check=True, capture_output=True, timeout=180)
        out = config.RESUMES_DIR / f"{stem}.pdf"
        shutil.copy(Path(td) / f"{stem}.pdf", out)
    return out


def _selftest() -> None:
    """Compile a sample resume (also warms the Tectonic package cache at build)."""
    sample = {
        "basics": {
            "name": "Jane Doe", "label": "Senior Data Engineer",
            "email": "jane@example.com", "phone": "012-345 6789",
            "location": {"city": "Kuala Lumpur", "region": "MY"},
            "url": "https://jane.dev",
            "summary": "Data engineer with 6 years building large-scale pipelines.",
            "profiles": [{"network": "GitHub", "url": "https://github.com/jane"}],
        },
        "work": [{
            "name": "Acme Corp & Sons", "position": "Senior Data Engineer",
            "location": "Remote", "startDate": "2021", "endDate": "2024",
            "summary": "Owned the batch & streaming platform (100% uptime target).",
            "highlights": ["Cut batch time 6h -> 90min on 2TB/day via Spark tuning.",
                           "Led Redshift -> Snowflake migration for a team of 5."],
        }],
        "projects": [{"name": "DAG Linter", "url": "https://github.com/jane/dl",
                      "description": "Static analysis for Airflow DAGs.",
                      "highlights": ["Caught 30+ misconfigurations pre-deploy."]}],
        "education": [{"institution": "Example University", "studyType": "BSc",
                       "area": "Computer Science", "startDate": "2014",
                       "endDate": "2018"}],
        "skills": [{"name": "Languages", "keywords": ["Python", "SQL", "Scala"]},
                   {"name": "Cloud", "keywords": ["AWS", "Snowflake"]}],
    }
    import json
    with tempfile.TemporaryDirectory() as td:
        jp = Path(td) / "selftest.json"
        jp.write_text(json.dumps(sample), encoding="utf-8")
        tex = _env.get_template("resume.tex.j2").render(**_normalize(sample))
        tp = Path(td) / "selftest.tex"
        tp.write_text(tex, encoding="utf-8")
        subprocess.run(["tectonic", "--chatter", "minimal", str(tp)],
                       cwd=td, check=True, capture_output=True, timeout=300)
    print("render selftest OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
