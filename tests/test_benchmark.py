"""ベンチマークウェイトは ``benchmark`` からだけ用意し、ユニバースのウェイトでは代わりにしない。"""

import pandas as pd
import pytest

from alpha_signal_evaluation import ConfigValidationError, evaluate, validate
from alpha_signal_evaluation.config import load_config
from alpha_signal_evaluation.metrics.base import Metric, MetricResult
from alpha_signal_evaluation.metrics.registry import _REGISTRY
from alpha_signal_evaluation.pipeline.preprocess import build_bundle

UNUSED = "universe.columns: `weight` is ignored"


class BenchmarkCount(Metric):
    """ベンチマークの銘柄数を数えるだけの、テスト用の metric。"""

    name = "test_benchmark_count"
    description = "number of benchmark rows (tests only)"
    requires = frozenset({"signals", "benchmark_weights"})

    def compute(self, data) -> MetricResult:
        """ベンチマークウェイトの行数を返す。"""
        return MetricResult(summary={"n": float(len(data.benchmark_weights))})


@pytest.fixture
def benchmark_metric(monkeypatch):
    monkeypatch.setitem(_REGISTRY, BenchmarkCount.name, BenchmarkCount)
    return [{"name": BenchmarkCount.name}]


def _write_weights(dataset, name: str) -> dict:
    """ユニバースと同じ (日付, 銘柄) に weight 列を付けた単一ファイルを書き、そのソースを返す。"""
    universe = pd.read_csv(dataset.root / "universe.csv", dtype=str)
    universe["wgt"] = 1.0 / universe.groupby("dt")["code"].transform("size")
    universe.to_csv(dataset.root / name, index=False)
    return {
        **dataset.config()["universe"],
        "path": name,
        "columns": {"asset_id": "code", "date": "dt", "weight": "wgt"},
    }


def test_universe_weight_is_accepted_but_not_used_as_benchmark(dataset):
    universe = _write_weights(dataset, "universe_w.csv")
    path = dataset.write_config(universe=universe)
    result = validate(path)
    assert result.ok, result.errors
    assert any(w.startswith(UNUSED) for w in result.warnings)

    prepared = build_bundle(load_config(path))
    assert prepared.bundle.benchmark_weights is None
    assert "benchmark_weights" not in prepared.bundle.spec().provides
    # ユニバースで絞る動作は weight 列があっても変わらない
    plain = build_bundle(load_config(dataset.write_config())).bundle
    pd.testing.assert_frame_equal(prepared.bundle.signals, plain.signals)


def test_universe_without_weight_has_no_warning(dataset):
    result = validate(dataset.write_config())
    assert result.ok, result.errors
    assert not any(w.startswith(UNUSED) for w in result.warnings)


def test_metric_needing_benchmark_fails_without_benchmark(dataset, benchmark_metric):
    universe = _write_weights(dataset, "universe_w.csv")
    path = dataset.write_config(universe=universe, metrics=benchmark_metric)
    errors = validate(path).errors
    assert errors == [
        "metrics[test_benchmark_count]: requires data that is not configured: "
        "['benchmark_weights'] (set `benchmark`)"
    ]
    with pytest.raises(ConfigValidationError, match="set `benchmark`"):
        evaluate(path)


def test_metric_needing_benchmark_runs_with_benchmark(dataset, benchmark_metric, tmp_path):
    universe = _write_weights(dataset, "universe_w.csv")
    # 互換: 以前ユニバースに書いていたウェイトのファイルを、そのまま benchmark に指定できる
    path = dataset.write_config(
        universe=universe,
        benchmark=universe,
        metrics=benchmark_metric,
        output={"dir": str(tmp_path / "out")},
    )
    report = evaluate(path)
    assert report.ok, report.errors
    bundle = build_bundle(load_config(path)).bundle
    sums = bundle.benchmark_weights["weight"].groupby(level="date").sum()
    assert sums.to_numpy() == pytest.approx(1.0)
    assert report[BenchmarkCount.name].summary["n"] == len(bundle.benchmark_weights)
