"""日付ごとの断面（``EvaluationReport.by_date`` / ``output.by_date``）の境界ケース。

通常の経路（config から評価して書き出す）は ``test_end_to_end.py`` で確認する。
ここでは手で組み立てたレポートで、metric が返しうる表の形の違いを確認する。
"""

import logging

import pandas as pd
import pytest

from alpha_signal_evaluation.config import EvaluationConfig
from alpha_signal_evaluation.metrics.base import MetricResult
from alpha_signal_evaluation.pipeline.runner import MetricRun
from alpha_signal_evaluation.report import EvaluationReport
from alpha_signal_evaluation.validation import validate_by_date


def _report(**metrics: dict[str, pd.DataFrame]) -> EvaluationReport:
    """metric のキー -> {テーブル名: DataFrame} からレポートを作る。"""
    runs = {key: MetricRun(key, key, result=MetricResult(tables=t)) for key, t in metrics.items()}
    return EvaluationReport(runs=runs)


def _timeseries(dates, signals=("a", "b")) -> pd.DataFrame:
    rows = [
        {"date": d, "signal": s, "ic": i + j / 10}
        for i, d in enumerate(dates)
        for j, s in enumerate(signals)
    ]
    return pd.DataFrame(rows)


def test_dates_that_are_not_strings_are_matched_as_strings(tmp_path):
    """int の日付を持つ表でも、dates() の値（文字列）で at() と by_date の指定が引ける。"""
    report = _report(m={"t": _timeseries([20240131, 20240229])})
    assert report.dates() == ["20240131", "20240229"]
    assert list(report.at("20240131")["m"]["t"]["signal"]) == ["a", "b"]
    assert list(report.at(20240229)["m"]["t"]["ic"]) == [1.0, 1.1]

    out = report.save(tmp_path, ["csv"], by_date=["20240131"])
    assert [p.name for p in (out / "by_date").iterdir()] == ["20240131"]


def test_validation_compares_bundle_dates_as_strings():
    cfg = EvaluationConfig.model_validate(
        {"version": 1, "output": {"by_date": [20240229, "20240331"]}}
    )
    errors = validate_by_date(cfg, [20240131, 20240229])
    assert len(errors) == 1 and "['20240331']" in errors[0]


def test_date_in_index_or_in_both_index_and_columns():
    by_column = _timeseries(["20240131", "20240229"])
    in_index = by_column.set_index(["date", "signal"])
    in_both = by_column.set_index("date", drop=False)
    section = _report(m={"index": in_index, "both": in_both}).at("20240229")["m"]

    assert list(section["index"].index) == ["a", "b"]  # date 以外の index は残る
    assert list(section["index"]["ic"]) == [1.0, 1.1]
    assert list(section["both"].columns) == ["signal", "ic"]
    assert list(section["both"]["ic"]) == [1.0, 1.1]


def test_rows_without_a_date_are_in_no_section():
    table = _timeseries(["20240131", None])
    report = _report(m={"t": table})
    assert report.dates() == ["20240131"]
    assert len(report.at("20240131")["m"]["t"]) == 2


def test_nothing_to_write_is_warned_and_run_info_is_kept(tmp_path, caplog):
    """日付を持つ表がなければ by_date/ は作らず警告する。run_info.json は書かれる。"""
    report = _report(m={"summary": pd.DataFrame({"signal": ["a"], "mean_ic": [0.1]})})
    with caplog.at_level(logging.WARNING):
        out = report.save(tmp_path, ["csv"], by_date="all")
    assert "by_date" in caplog.text
    assert not (out / "by_date").exists()
    assert (out / "run_info.json").exists()


def test_no_formats_writes_no_empty_directories(tmp_path):
    report = _report(m={"t": _timeseries(["20240131"])})
    out = report.save(tmp_path, [], by_date="all")
    assert not (out / "by_date").exists()


def test_xlsx_sheet_names_are_the_same_on_every_date(tmp_path):
    """31文字で切ると衝突する名前でも、同じ表は日付によらず同じシート名になる。"""
    key = "quantile_q10_specific_sector_neutral"
    returns = _timeseries(["20240131"]).rename(columns={"ic": "mean_return"})
    cumulative = _timeseries(["20240131", "20240229"]).rename(columns={"ic": "cumulative_return"})
    report = _report(**{key: {"returns": returns, "cumulative": cumulative}})
    out = report.save(tmp_path, ["xlsx"], by_date="all")

    def sheets(path):
        return {name: list(df.columns) for name, df in pd.read_excel(path, sheet_name=None).items()}

    top = sheets(out / "report.xlsx")
    first = sheets(out / "by_date" / "20240131" / "report.xlsx")
    second = sheets(out / "by_date" / "20240229" / "report.xlsx")  # returns の行がない日付
    assert len(first) == 2 and len(second) == 1
    (name,) = second
    assert second[name] == first[name] == ["signal", "cumulative_return"]
    assert top[name] == ["date", "signal", "cumulative_return"]  # 全体の report.xlsx とも同じ名前


@pytest.mark.parametrize(
    ("date", "dirname"), [("2024/01/31", "2024_01_31"), ("../escape", "___escape")]
)
def test_date_keys_cannot_escape_the_output_directory(tmp_path, date, dirname):
    report = _report(m={"t": _timeseries([date])})
    out = report.save(tmp_path / "out", ["csv"], by_date="all")
    assert [p.name for p in (out / "by_date").iterdir()] == [dirname]
    assert (out / "by_date" / dirname / "m" / "t.csv").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["out"]
