from hypotrack import slurm
from hypotrack.overrides import config_name_from, extract_overrides, override_diff, varying_and_fixed


def test_parse_job_ids_all_and_dedup():
    text = "some output\nSubmitted batch job 123\nnoise\nSubmitted batch job 124\nSubmitted batch job 123\n"
    assert slurm.parse_job_ids(text) == ["123", "124"]
    assert slurm.parse_job_ids("nothing here") == []


def test_parse_sacct_ignores_steps_and_normalizes():
    text = (
        "1001|COMPLETED|0:0|00:10:00\n"
        "1001.batch|COMPLETED|0:0|00:10:00\n"
        "1001.extern|COMPLETED|0:0|00:10:00\n"
        "1002|FAILED|1:0|00:02:00\n"
        "1003|CANCELLED by 500|0:0|00:00:10\n"
        "1004|RUNNING||00:01:00\n"
    )
    infos = slurm.parse_sacct(text)
    assert set(infos) == {"1001", "1002", "1003", "1004"}
    assert infos["1002"].exit_code == "1:0"
    assert infos["1003"].state == "CANCELLED"
    assert infos["1004"].exit_code is None


def test_lookup_aggregates_array_tasks():
    infos = slurm.parse_sacct("55_0|COMPLETED|0:0|1\n55_1|FAILED|1:0|1\n55_2|COMPLETED|0:0|1\n")
    agg = slurm.lookup(infos, "55")
    assert agg is not None and agg.state == "FAILED" and agg.exit_code == "1:0"
    infos = slurm.parse_sacct("55_0|COMPLETED|0:0|1\n55_1|RUNNING||1\n")
    assert slurm.lookup(infos, "55").state == "RUNNING"
    assert slurm.lookup(infos, "77") is None


def test_extract_overrides_from_submit_command():
    argv = ["bash", "scripts/submit.sh", "-m", "model.dropout=0.1,0.2", "+trainer.fast=true", "~optim.sched", "--config-name", "train", "-cn", "x"]
    assert extract_overrides(argv) == ["model.dropout=0.1,0.2", "+trainer.fast=true", "~optim.sched"]
    assert config_name_from(argv) == "train"
    assert extract_overrides(["python", "train.py", "--lr=0.1"]) == []


def test_varying_and_fixed_and_diff():
    runs = [["model.dropout=0.1", "trainer.max_epochs=50"], ["model.dropout=0.2", "trainer.max_epochs=50"]]
    varying, fixed = varying_and_fixed(runs)
    assert varying == ["model.dropout"]
    assert fixed == {"trainer.max_epochs": "50"}
    assert override_diff(["a=1", "b=2"], ["a=1", "b=3", "c=4"]) == [("b", "2", "3"), ("c", None, "4")]
    assert varying_and_fixed([["a=1"]]) == ([], {"a": "1"})
