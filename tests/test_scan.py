import scan


def test_parse_matches_clean_array():
    text = '[{"title":"SRE","company":"Acme","url":"https://x.io/1","fit_score":8,' \
           '"why_fit":"aws","why_aligns":"sre pivot","location":"KL"}]'
    out = scan.parse_matches(text)
    assert len(out) == 1
    assert out[0]["company"] == "Acme"


def test_parse_matches_strips_fences_and_prose():
    text = ("Here are the matches:\n```json\n"
            '[{"title":"DevOps","company":"Beta","url":"https://x.io/2","fit_score":7,'
            '"why_fit":"docker","why_aligns":"hybrid","location":"Cheras"}]'
            "\n```\nHope that helps!")
    out = scan.parse_matches(text)
    assert len(out) == 1
    assert out[0]["title"] == "DevOps"


def test_parse_matches_empty_array():
    assert scan.parse_matches("[]") == []


def test_parse_matches_garbage_returns_empty():
    assert scan.parse_matches("sorry, I could not find anything") == []


def test_valid_match_requires_core_fields():
    good = {"title": "SRE", "company": "Acme", "url": "https://x.io/1", "fit_score": 8}
    assert scan.valid_match(good)
    assert not scan.valid_match({"title": "", "company": "Acme", "url": "https://x.io/1"})
    assert not scan.valid_match({"title": "SRE", "company": "Acme", "url": ""})


def test_coerce_fit_score_from_string():
    out = scan.parse_matches('[{"title":"SRE","company":"Acme","url":"https://x.io/1",'
                             '"fit_score":"9"}]')
    assert out[0]["fit_score"] == 9
