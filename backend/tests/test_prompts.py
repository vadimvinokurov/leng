from app import prompts


def test_load_returns_raw_template_with_text_placeholder():
    template = prompts.load("translate_and_classify")
    assert isinstance(template, str)
    # The loader does not format: the {text} placeholder is returned raw and
    # the caller applies .format(text=...).
    assert "{text}" in template


def test_template_formats_with_text():
    template = prompts.load("translate_and_classify")
    rendered = template.format(text="went")
    assert "went" in rendered
    # Doubled literal braces around the JSON example collapse to a single pair.
    assert '{"kind"' in rendered


def test_load_is_cwd_independent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert "{text}" in prompts.load("translate_and_classify")
