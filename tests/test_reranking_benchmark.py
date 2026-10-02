"""Independent ranking metrics and private artifact handling for local benchmarks."""

import json
import stat

import pytest

from scripts.benchmark_reranking import ndcg, ranking_metrics, validate_labels, write_private_json


def test_ndcg_rewards_early_direct_evidence_and_handles_no_relevant_candidates():
    assert ndcg([4, 2, 0], [0, 2, 4], 3) == 1
    assert ndcg([0, 2, 4], [0, 2, 4], 3) < 1
    assert ndcg([0, 0], [0, 0], 5) == 0


def test_facet_coverage_and_duplicate_slots_measure_different_outcomes():
    labels = {
        "c1": {"grade": 3, "facets": ["a"]},
        "c2": {"grade": 3, "facets": ["a"]},
        "c3": {"grade": 3, "facets": ["b"]},
        "c4": {"grade": 0, "facets": []},
    }
    contents = {"c1": "same evidence", "c2": "same\n evidence", "c3": "other", "c4": "noise"}
    metrics = ranking_metrics(list(labels), labels, contents, ["a", "b"])
    assert metrics["facet_coverage_at_5"] == 1
    assert metrics["shortlist_facet_coverage"] == 1
    assert metrics["unique_content_rate_at_12"] == 0.75
    assert metrics["zero_grade_count_at_12"] == 1
    assert metrics["first_grade_4_rank"] is None


@pytest.mark.parametrize("invalid", ["missing", "grade", "facet", "duplicate"])
def test_labels_are_complete_valid_and_consistent_for_identical_content(invalid):
    case = {
        "candidates": [{"label": "c1", "content": "same"}, {"label": "c2", "content": "same"}],
        "required_facets": ["a"],
    }
    labels = {key: {"grade": 3, "facets": ["a"]} for key in ("c1", "c2")}
    if invalid == "missing":
        labels.pop("c1")
    if invalid == "grade":
        labels["c1"]["grade"] = True
    if invalid == "facet":
        labels["c1"]["facets"] = ["unknown"]
    if invalid == "duplicate":
        labels["c1"]["grade"] = 0
    with pytest.raises(ValueError):
        validate_labels(case, labels)


def test_private_artifacts_cannot_overwrite_existing_files_or_follow_symlinks(tmp_path):
    path = tmp_path / "snapshot.json"
    write_private_json(path, {"private": "source text"})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        write_private_json(path, {})
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        write_private_json(link, {})
    assert json.loads(path.read_text()) == {"private": "source text"}
