from hypotrack.models import Experiment, Hypothesis, Run


def test_hypothesis_yaml_roundtrip_uses_block_scalars():
    h = Hypothesis(id="H1.1", title="dropout helps", parent="H1", statement="line one\nline two", rationale="why")
    text = h.to_yaml()
    assert "statement: |" in text
    back = Hypothesis.from_yaml(text)
    assert back == h


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
