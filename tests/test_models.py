from hypotrack.models import Experiment, Hypothesis, Run


def test_hypothesis_yaml_holds_metadata_only():
    h = Hypothesis(id="H1.1", title="dropout helps", parent="H1", statement="line one\nline two", rationale="why")
    text = h.to_yaml()
    assert "statement" not in text and "rationale" not in text and "conclusion" not in text
    back = Hypothesis.from_yaml(text)
    assert back.id == "H1.1" and back.parent == "H1" and back.statement == ""


def test_hypothesis_legacy_yaml_with_body_still_loads():
    legacy = "id: H1\nparent: null\ntitle: t\nstatus: open\ncreated: 2026-11-05\nstatement: |\n  old body\nconclusion: ''\n"
    h = Hypothesis.from_yaml(legacy)
    assert h.statement == "old body"


def test_hypothesis_markdown_roundtrip_and_preserves_extra_sections():
    h = Hypothesis(id="H1.1", title="dropout helps", parent="H1", statement="line one\nline two", rationale="why")
    md = h.to_markdown()
    assert md.startswith("# H1.1: dropout helps\n")
    assert "## Statement\n\nline one\nline two\n" in md
    assert "## Rationale\n\nwhy\n" in md
    assert md.rstrip().endswith("## Conclusion")  # present but empty
    back = Hypothesis(id="H1.1", title="dropout helps", parent="H1")
    back.apply_markdown(md)
    assert back == h

    # a human adds a section and edits the statement by hand; a later save keeps both
    edited = md.replace("line one\nline two", "edited by hand") + "\n## Open questions\n\n- is 0.3 too much?\n"
    back.apply_markdown(edited)
    assert back.statement == "edited by hand"
    back.conclusion = "settled"
    out = back.to_markdown(edited)
    assert "## Open questions\n\n- is 0.3 too much?" in out
    assert "## Conclusion\n\nsettled" in out
    assert out.count("## Statement") == 1


def test_hypothesis_markdown_keeps_intro_and_ignores_fenced_headings():
    h = Hypothesis(id="H1", title="t", statement="s")
    existing = "# H1: old title\n\nfree-form intro\n\n## Statement\n\n```\n## not a heading\n```\n\n## Conclusion\n"
    h.apply_markdown(existing)
    assert h.statement == "```\n## not a heading\n```"
    out = h.to_markdown(existing)
    assert out.startswith("# H1: t\n\nfree-form intro\n\n## Statement\n\n```\n## not a heading\n```\n")
    assert "\n\n\n" not in out  # empty sections do not leave blank runs
    assert out.rstrip().endswith("## Conclusion\n\n## Rationale")  # missing section appended, both empty


def test_hypothesis_markdown_duplicates_and_indentation_survive():
    text = "# H1: t\n\n## statement\n\n    indented first line\nplain\n\n## Statement\n\nsecond copy\n\n## Conclusion\n"
    h = Hypothesis(id="H1", title="t")
    h.apply_markdown(text)
    assert h.statement == "    indented first line\nplain"  # first occurrence wins, indentation kept
    out = h.to_markdown(text)
    assert "## statement\n\n    indented first line\nplain\n" in out  # heading spelling kept
    assert "## Statement\n\nsecond copy" in out  # duplicate kept verbatim, nothing lost


def test_experiment_yaml_roundtrip_drops_empty_run_fields():
    e = Experiment(id="exp-001", hypothesis="H1.1", title="t", runs=[Run(job_id="1"), Run(subdir="a/0", state="COMPLETED")])
    text = e.to_yaml()
    assert "overrides" not in text  # empty lists are not written
    back = Experiment.from_yaml(text)
    assert back.runs[0].job_id == "1"
    assert back.runs[1].state == "COMPLETED"
    assert back.status == "draft"


def _exp(*states, status="submitted"):
    return Experiment(id="exp-001", hypothesis="H1", title="t", status=status, runs=[Run(job_id=str(i), state=s) for i, s in enumerate(states)])


def test_derive_status():
    assert _exp("COMPLETED", "COMPLETED").derive_status() == "completed"
    assert _exp("COMPLETED", "FAILED").derive_status() == "partial"
    assert _exp("FAILED", "TIMEOUT").derive_status() == "failed"
    assert _exp("RUNNING", "COMPLETED").derive_status() == "running"
    assert _exp("PENDING", "COMPLETED").derive_status() == "submitted"
    assert _exp(None, "COMPLETED").derive_status() == "submitted"
    assert _exp("CANCELLED", "CANCELLED").derive_status() == "cancelled"
    assert _exp("COMPLETED", status="cancelled").derive_status() == "cancelled"
    assert _exp(status="draft").derive_status() == "draft"


def test_run_label_and_failed():
    assert Run(subdir="2026/1").label == "1"
    assert Run(job_id="77").label == "77"
    assert Run(state="FAILED").is_failed
    assert not Run(state="COMPLETED").is_failed
    assert not Run(state="RUNNING").is_failed
