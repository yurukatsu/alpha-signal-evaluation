"""社内の入力データ（input.zip）の形式を読めることのテスト。"""

import importlib.util
import shutil
from pathlib import Path

import pandas as pd
import pytest

from alpha_signal_evaluation import evaluate, validate
from alpha_signal_evaluation.config import load_config
from alpha_signal_evaluation.data.calendar import load_calendar
from alpha_signal_evaluation.io.file import read_table
from alpha_signal_evaluation.pipeline import build_bundle

EXAMPLES = Path(__file__).parents[1] / "examples" / "example1"


def test_calendar_with_mixed_whitespace(tmp_path):
    path = tmp_path / "calendar.dat"
    path.write_text(
        "#Base_day\tMonthend_day\tTrading_day\tRskmdl_month\tLabel1\tLabel2\n"
        "20260529\t20260529\t20260529\t202604\t202604\t20260528\n"
        "20260630    20260630    20260630    202605  202605  20260629\n"
        "20260731\u300020260731\u300020260731\u3000202606\u3000202606\u300020260730\n",
        encoding="utf-8",
    )
    cal = load_calendar(path)
    assert cal.frame["Label2"].tolist() == ["20260528", "20260629", "20260730"]


def test_csv_header_hash_is_stripped(tmp_path):
    path = tmp_path / "x.csv"
    path.write_text("#nri_code,score\n01301,0.1\n")
    assert list(read_table(path).columns) == ["nri_code", "score"]


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    """examples/make_sample_data.py で input.zip と同じ構成のデータを作る。"""
    root = tmp_path_factory.mktemp("sample")
    spec = importlib.util.spec_from_file_location(
        "make_sample_data", EXAMPLES / "make_sample_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root / "input"
    module.main()
    shutil.copy(EXAMPLES / "config.example.yaml", root / "config.yaml")
    return root


def test_example_config_is_valid(sample):
    result = validate(sample / "config.yaml")
    assert result.ok, result.errors


def test_sample_bundle(sample):
    bundle = build_bundle(load_config(sample / "config.yaml")).bundle
    assert bundle.signal_names == ["ai", "dss"]
    codes = bundle.signals.index.get_level_values("asset_id")
    assert all(len(c) == 5 for c in codes[:100])  # 先頭ゼロが保たれる
    # {Label2} のファイルでも Base_day が付与される
    assert bundle.dates[0] == "20190329"
    # one-hot の分類は L001〜L010 のいずれか
    assert set(bundle.classification["category"]) <= {f"L{i:03d}" for i in range(1, 11)}
    # ユニバースのウェイトはベンチマークウェイトとして保持され、日付ごとに合計 1
    weights = bundle.benchmark_weights["weight"].groupby(level="date").sum()
    assert weights.to_numpy() == pytest.approx(1.0, abs=1e-4)
    # シグナルはユニバースの銘柄に限られる
    assert bundle.signals.index.isin(bundle.benchmark_weights.index).all()


def test_sample_evaluation(sample, tmp_path):
    report = evaluate(sample / "config.yaml", n_jobs=1)
    assert report.ok, report.errors
    summary = report["ic"].tables["summary"].set_index(["signal", "returns", "horizon"])
    assert summary.loc[("ai", "specific", 1), "mean_ic"] > 0.03


def test_one_hot_with_multiple_flags_is_rejected(sample, tmp_path):
    root = tmp_path / "broken"
    shutil.copytree(sample / "input", root / "input")
    shutil.copy(sample / "config.yaml", root / "config.yaml")
    path = sorted((root / "input" / "labels").glob("*.csv"))[-1]
    df = pd.read_csv(path, dtype={"#nri_code": str})
    df.loc[0, "L001":"L002"] = 1
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="multiple categories"):
        build_bundle(load_config(root / "config.yaml"))


def _run_generator(script: Path, root: Path, *args: str) -> None:
    import sys

    spec = importlib.util.spec_from_file_location("make_sample_data_2", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    argv, sys.argv = sys.argv, [str(script), *args]
    try:
        module.main()
    finally:
        sys.argv = argv


@pytest.fixture(scope="module")
def sample2(tmp_path_factory):
    """examples/example2: 月次（YYYYMM）・スペース区切り・ベンチマーク別ファイルの形式。"""
    example = EXAMPLES.parent / "example2"
    root = tmp_path_factory.mktemp("sample2")
    _run_generator(example / "make_sample_data.py", root / "input")
    shutil.copy(example / "config.example.yaml", root / "config.yaml")
    return root


def test_example2_bundle(sample2):
    result = validate(sample2 / "config.yaml")
    assert result.ok, result.errors
    bundle = build_bundle(load_config(sample2 / "config.yaml")).bundle
    assert bundle.signal_names == ["comp_ew"]
    assert bundle.dates[0] == "200801"  # 月だけのカレンダー
    codes = bundle.signals.index.get_level_values("asset_id")
    assert codes.str.fullmatch(r"IND[A-Z]{3}\d").all()
    # ベンチマークは universe ではなく bm から読む
    sizes = bundle.benchmark_weights.groupby(level="date").size()
    assert sizes.max() <= 166
    weights = bundle.benchmark_weights["weight"].groupby(level="date").sum()
    assert weights.to_numpy() == pytest.approx(1.0)


def test_example2_evaluation(sample2):
    report = evaluate(sample2 / "config.yaml", n_jobs=1)
    assert report.ok, report.errors
    summary = report["ic"].tables["summary"].set_index(["returns", "horizon"])
    assert summary.loc[("specific", 1), "mean_ic"] > 0.03


def test_example2_keeps_existing_data(sample2, tmp_path):
    root = tmp_path / "input"
    for d in ["alpha", "bm", "univ"]:
        shutil.copytree(sample2 / "input" / d, root / d)
    before = (root / "alpha" / "202607.dat").read_text()
    calendar = "#Month\n200301\n200302\n"  # 本番の calendar.dat は上書きしない
    (root / "calendar.dat").write_text(calendar)
    script = EXAMPLES.parent / "example2" / "make_sample_data.py"
    with pytest.raises(SystemExit, match="already has data"):
        _run_generator(script, root)
    _run_generator(script, root, "--keep-data")
    assert (root / "alpha" / "202607.dat").read_text() == before
    assert (root / "calendar.dat").read_text() == calendar
    assert len(list((root / "returns").glob("*.csv"))) == len(list((root / "univ").glob("*.dat")))
