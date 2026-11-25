"""社内の入力データ（input.zip）の形式を読めることのテスト。"""

import importlib.util
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml

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
    shutil.copy(EXAMPLES / "config.yaml", root / "config.yaml")
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
    # 縦持ちの分類は L001〜L010 のいずれか
    assert set(bundle.classification["category"]) <= {f"L{i:03d}" for i in range(1, 11)}
    # ベンチマークは universe ではなく bm から読み、日付ごとに合計 1
    sizes = bundle.benchmark_weights.groupby(level="date").size()
    assert sizes.max() <= 300
    weights = bundle.benchmark_weights["weight"].groupby(level="date").sum()
    assert weights.to_numpy() == pytest.approx(1.0, abs=1e-4)


def test_sample_evaluation(sample, tmp_path):
    report = evaluate(sample / "config.yaml", n_jobs=1)
    assert report.ok, report.errors
    summary = report["ic"].tables["summary"].set_index(["signal", "returns", "horizon"])
    assert summary.loc[("ai", "specific", 1), "mean_ic"] > 0.03


def _copy_sample(sample: Path, root: Path) -> dict:
    """サンプルを root に複製し、その config を dict で返す。"""
    shutil.copytree(sample / "input", root / "input")
    return yaml.safe_load((sample / "config.yaml").read_text())


def _bundle(root: Path, cfg: dict):
    (root / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))
    return build_bundle(load_config(root / "config.yaml")).bundle


def _to_one_hot(root: Path) -> None:
    """縦持ちの分類ファイルを、L001〜L010 の 0/1 の列を持つ one-hot の形式に書き換える。"""
    for path in (root / "input" / "labels").glob("*.csv"):
        df = pd.read_csv(path, dtype=str)
        one_hot = pd.get_dummies(df["label"]).astype(int)
        one_hot.insert(0, "#nri_code", df["#nri_code"])
        one_hot.to_csv(path, index=False)


def test_one_hot_classification_matches_long_format(sample, tmp_path):
    root = tmp_path / "one_hot"
    cfg = _copy_sample(sample, root)
    expected = _bundle(root, cfg).classification
    _to_one_hot(root)
    cfg["data"]["classification"]["columns"] = {"asset_id": "nri_code"}
    cfg["data"]["classification"]["one_hot"] = True
    pd.testing.assert_frame_equal(_bundle(root, cfg).classification, expected)

    path = sorted((root / "input" / "labels").glob("*.csv"))[-1]
    df = pd.read_csv(path, dtype={"#nri_code": str})
    df.loc[0, "L001":"L002"] = 1
    df.to_csv(path, index=False)
    with pytest.raises(ValueError, match="multiple categories"):
        _bundle(root, cfg)


def test_universe_file_with_weight_column_is_read(sample, tmp_path):
    """2列目にウェイトがある旧形式のユニバースも、usecols で銘柄だけを読める。"""
    root = tmp_path / "legacy"
    cfg = _copy_sample(sample, root)
    expected = _bundle(root, cfg).signals
    for path in (root / "input" / "univ").glob("*.dat"):
        lines = path.read_text().splitlines()
        path.write_text(
            "\n".join(line if line.startswith("#") else f"{line}\t0.001" for line in lines) + "\n"
        )
    pd.testing.assert_frame_equal(_bundle(root, cfg).signals, expected)


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
    shutil.copy(example / "config.yaml", root / "config.yaml")
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
