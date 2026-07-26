from app.clients.vllm import parse_json_block_robust, salvage_json_fields


def test_parse_json_invalid_escape():
    raw = '{"reference_answer": "\\\\frac{1}{2}", "solver_confidence": 0.9}'
    data = parse_json_block_robust(raw, salvage_fields=("reference_answer",))
    assert "reference_answer" in data


def test_parse_latex_frac_not_formfeed():
    """Raw \\frac must not be accepted as JSON form-feed + 'rac'."""
    raw = r'{"page_no":1,"blocks":[{"id":"a","text":"\frac{1}{2}"}]}'
    data = parse_json_block_robust(raw)
    assert data["blocks"][0]["text"] == r"\frac{1}{2}"


def test_parse_latex_end_invalid_escape():
    raw = r'{"blocks":[{"id":"a","text":"\end{aligned}"}]}'
    data = parse_json_block_robust(raw)
    assert data["blocks"][0]["text"] == r"\end{aligned}"


def test_parse_latex_brace_set_literal():
    """Raw \\{1,2\\} in model output must parse (common set notation)."""
    raw = r'{"page_no":1,"blocks":[{"id":"a","text":"集合可表示为 $\{1,2\}$"}]}'
    data = parse_json_block_robust(raw)
    assert data["blocks"][0]["text"] == r"集合可表示为 $\{1,2\}$"


def test_parse_already_escaped_pi_not_tripled():
    """Correct JSON \\\\pi must stay \\pi after repair (no triple-backslash)."""
    raw = '{"page_no":1,"blocks":[{"id":"a","text":"（4）$\\\\pi$____ R."}]}'
    data = parse_json_block_robust(raw)
    assert data["blocks"][0]["text"] == r"（4）$\pi$____ R."


def test_parse_mixed_escaped_and_raw_latex():
    raw = (
        '{"blocks":[{"id":"a","text":"$\\\\pi$ and $\\{1,2\\}$ and \\frac{1}{2}"}]}'
    )
    data = parse_json_block_robust(raw)
    t = data["blocks"][0]["text"]
    assert r"$\pi$" in t
    assert r"$\{1,2\}$" in t
    assert r"\frac{1}{2}" in t


def test_salvage_truncated_reference():
    raw = """{"reference_answer": "(-∞,-1]", "solution_steps": ["step1", "step2"""
    data = parse_json_block_robust(raw, salvage_fields=("reference_answer", "solution_steps"))
    assert data.get("reference_answer") == "(-∞,-1]"


def test_salvage_fields_is_wrong():
    raw = '{"is_wrong": true, "eval_confidence": 0.88, "match_notes": "符号错误'
    data = salvage_json_fields(raw, ("is_wrong", "eval_confidence", "match_notes"))
    assert data["is_wrong"] is True
    assert data["eval_confidence"] == 0.88
