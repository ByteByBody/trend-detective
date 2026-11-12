"""Disk persistence for the per-variable cry-wolf analysis body."""

import json

from trend_detective.data import analysis_cache


def _body(variable="surface_temperature"):
    return {"variable": variable, "n_naive": 3, "threshold": 0.0359, "slope": [[1.0, None]]}


def test_load_returns_none_when_nothing_is_cached(tmp_path):
    assert analysis_cache.load("surface_temperature", cache_dir=tmp_path) is None


def test_save_then_load_round_trips(tmp_path):
    body = _body()
    path = analysis_cache.save("surface_temperature", body, cache_dir=tmp_path)
    assert path.exists()
    assert analysis_cache.load("surface_temperature", cache_dir=tmp_path) == body


def test_save_leaves_no_partial_files_behind(tmp_path):
    analysis_cache.save("surface_temperature", _body(), cache_dir=tmp_path)
    assert list(tmp_path.rglob("*.part")) == []


def test_a_corrupt_cache_file_reads_as_absent(tmp_path):
    path = analysis_cache.save("surface_temperature", _body(), cache_dir=tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert analysis_cache.load("surface_temperature", cache_dir=tmp_path) is None


def test_a_json_file_without_a_variable_is_rejected(tmp_path):
    path = analysis_cache.save("surface_temperature", _body(), cache_dir=tmp_path)
    path.write_text(json.dumps({"unrelated": 1}), encoding="utf-8")
    assert analysis_cache.load("surface_temperature", cache_dir=tmp_path) is None


def test_nan_survives_a_round_trip(tmp_path):
    body = {"variable": "surface_temperature", "morans_i": float("nan")}
    analysis_cache.save("surface_temperature", body, cache_dir=tmp_path)
    loaded = analysis_cache.load("surface_temperature", cache_dir=tmp_path)
    assert loaded["morans_i"] != loaded["morans_i"]  # NaN is not equal to itself


def test_signature_tracks_analysis_parameters(monkeypatch):
    before = analysis_cache.signature_of()
    monkeypatch.setattr(
        analysis_cache.config, "MIN_PATCH_SIZE", analysis_cache.config.MIN_PATCH_SIZE + 1
    )
    assert analysis_cache.signature_of() != before


def test_signature_tracks_whether_statsmodels_is_present(monkeypatch):
    monkeypatch.setattr(analysis_cache, "_has_statsmodels", lambda: False)
    without = analysis_cache.signature_of()
    monkeypatch.setattr(analysis_cache, "_has_statsmodels", lambda: True)
    assert analysis_cache.signature_of() != without


def test_signature_distinguishes_the_resolved_source():
    # The key must name the source that actually produced the data, not only the
    # configured name: a test or a second deployment can reuse config.DATA_SOURCE
    # while serving different data, and its body must not be served for the live
    # source.
    assert analysis_cache.signature_of(source="PslSource") != analysis_cache.signature_of(
        source="SyntheticSource"
    )


def test_signature_differs_between_configured_and_resolved_source():
    # A body keyed only by the configured name must not collide with one keyed by
    # the resolved source class.
    assert analysis_cache.signature_of() != analysis_cache.signature_of(source="PslSource")


def test_a_changed_signature_does_not_read_the_old_file(tmp_path):
    analysis_cache.save(
        "surface_temperature", _body(), signature="aaa", cache_dir=tmp_path
    )
    assert analysis_cache.load("surface_temperature", signature="bbb", cache_dir=tmp_path) is None


def test_two_variables_do_not_share_a_file(tmp_path):
    analysis_cache.save("surface_temperature", _body("surface_temperature"), cache_dir=tmp_path)
    analysis_cache.save("precipitation", _body("precipitation"), cache_dir=tmp_path)
    assert analysis_cache.load("precipitation", cache_dir=tmp_path)["variable"] == "precipitation"
