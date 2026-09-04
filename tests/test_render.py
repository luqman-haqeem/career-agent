"""Rendering a JSON Resume into LaTeX.

These stop at the .tex string — compiling with Tectonic is slow and needs the
binary, and every bug worth catching here (a missing section, a dropped field,
an unescaped character) is visible in the source.
"""
import render


def _tex(data: dict) -> str:
    return render._env.get_template("resume.tex.j2").render(**render._normalize(data))


BASE = {"basics": {"name": "Luqman Haqeem"}}


def test_certificates_render_with_issuer_and_date():
    tex = _tex({**BASE, "certificates": [
        {"name": "Professional Scrum Master I (PSM I)",
         "issuer": "Scrum.org", "date": "2024"},
    ]})
    assert "Certifications" in tex
    assert "Professional Scrum Master I (PSM I)" in tex
    assert "Scrum.org" in tex
    assert "2024" in tex


def test_certificate_url_is_included_when_given():
    tex = _tex({**BASE, "certificates": [
        {"name": "AWS Certified Developer", "issuer": "AWS", "date": "2025",
         "url": "https://example.com/verify/abc"},
    ]})
    assert "https://example.com/verify/abc" in tex


def test_certificate_survives_missing_issuer_and_date():
    tex = _tex({**BASE, "certificates": [{"name": "PSM I"}]})
    assert "Certifications" in tex
    assert "PSM I" in tex


def test_no_certificates_section_when_absent():
    assert "Certifications" not in _tex(BASE)
    assert "Certifications" not in _tex({**BASE, "certificates": []})


def test_certificate_text_is_latex_escaped():
    tex = _tex({**BASE, "certificates": [
        {"name": "Scrum & Agile 100%", "issuer": "Scrum.org"},
    ]})
    assert r"Scrum \& Agile 100\%" in tex


def test_certificates_keep_their_given_order():
    tex = _tex({**BASE, "certificates": [
        {"name": "First Cert"}, {"name": "Second Cert"},
    ]})
    assert tex.index("First Cert") < tex.index("Second Cert")


def test_existing_sections_still_render():
    tex = _tex({
        **BASE,
        "work": [{"name": "Inmagine", "position": "Web Application Developer",
                  "startDate": "2025-04", "highlights": ["Shipped a thing."]}],
        "education": [{"institution": "Selangor Islamic University",
                       "studyType": "Diploma", "area": "Computer Science",
                       "startDate": "2018", "endDate": "2020"}],
        "skills": [{"name": "Backend", "keywords": ["Node.js", "Python"]}],
    })
    for expected in ("Experience", "Inmagine", "Education", "Skills", "Node.js"):
        assert expected in tex
