"""月だけのカレンダー（例: 200301, 200302, ...）での評価。"""

import numpy as np
import pandas as pd
import pytest
import yaml

from alpha_signal_evaluation import evaluate, validate
from alpha_signal_evaluation.data.calendar import load_calendar
from alpha_signal_evaluation.data.loaders import LoadContext

MONTHS = pd.period_range("2019-12", periods=14, freq="M").strftime("%Y%m").tolist()


def test_month_only_calendar_with_header(tmp_path):
    path = tmp_path / "calendar.dat"
    path.write_text("#Month\n" + "\n".join(MONTHS) + "\n")
    cal = load_calendar(path, index="Month")
    assert cal.columns == ["Month"]
    assert cal.keys.tolist() == MONTHS


def test_comment_lines_before_header_are_skipped(tmp_path):
    path = tmp_path / "calendar.dat"
    path.write_text(
        "# Rebalancing and trading monthly.\n"
        "#Base_month\tMonthend\tTrading_month\tRskmdl_month\tLabel1\tLabel2\n"
        "198712\t198712\t198712\t198801\t198712\t87c\n"
        "198801\t198801\t198801\t198801\t198801\t881\n"
    )
    cal = load_calendar(path, index="Base_month")
    assert cal.columns == [
        "Base_month",
        "Monthend",
        "Trading_month",
        "Rskmdl_month",
        "Label1",
        "Label2",
    ]
    assert cal.frame["Label2"].tolist() == ["87c", "881"]


def test_header_and_data_width_mismatch(tmp_path):
    path = tmp_path / "calendar.dat"
    path.write_text("#Month\tLabel\n200301\n")
    with pytest.raises(ValueError, match="header"):
        load_calendar(path, index="Month")


def test_headerless_calendar_requires_columns(tmp_path):
    path = tmp_path / "calendar.dat"
    path.write_text("\n".join(MONTHS) + "\n")
    with pytest.raises(ValueError, match="calendar.columns"):
        load_calendar(path, index="Month")
    cal = load_calendar(path, index="Month", columns=["Month"])
    assert cal.keys.tolist() == MONTHS


def test_columns_override_existing_header(tmp_path):
    path = tmp_path / "calendar.dat"
    path.write_text("#YYYYMM\n" + "\n".join(MONTHS) + "\n")
    assert load_calendar(path, index="Month", columns=["Month"]).keys.tolist() == MONTHS
    with pytest.raises(ValueError, match="calendar.columns has 2 columns"):
        load_calendar(path, index="Month", columns=["Month", "Extra"])


class FakeDB:
    def __init__(self, daily):
        self.daily = daily
        self.calls = []

    def execute(self, sql, params):
        self.calls.append(params)
        start, end = pd.Timestamp(params["start"]), pd.Timestamp(params["end"])
        return self.daily[(self.daily["dt"] > start) & (self.daily["dt"] <= end)].copy()


def test_db_returns_are_aggregated_by_calendar_month(tmp_path, monkeypatch, db_env):
    rng = np.random.default_rng(0)
    codes = [f"IND{c}1" for c in ["AAA", "AAB", "AAC", "AAD", "AAE", "AAF", "AAG", "AAH"]]
    days = pd.bdate_range("2019-11-01", "2021-03-31")
    daily = pd.DataFrame(
        [(d, c, rng.normal(0, 0.01)) for d in days for c in codes], columns=["dt", "bid", "ret"]
    )
    wide = daily.pivot(index="dt", columns="bid", values="ret")
    monthly = (1 + wide).groupby(wide.index.strftime("%Y%m")).prod() - 1

    (tmp_path / "calendar.dat").write_text("#Month\n" + "\n".join(MONTHS) + "\n")
    (tmp_path / "alpha").mkdir()
    for i, m in enumerate(MONTHS[:-1]):
        # 月 m のシグナル = 翌月の実現リターンそのもの → realized で h=1 の IC は 1
        nxt = MONTHS[i + 1]
        lines = ["#bid comp_ew"] + [f"{c} {float(monthly.loc[nxt, c])!r}" for c in codes]
        (tmp_path / "alpha" / f"{m}.dat").write_text("\n".join(lines) + "\n")
    (tmp_path / "returns.sql").write_text(
        "SELECT dt, bid, ret FROM r WHERE dt > :start AND dt <= :end"
    )
    config = {
        "version": 1,
        "calendar": {"path": "calendar.dat", "index": "Month"},
        "period": {"start": MONTHS[1], "end": MONTHS[10]},
        "horizons": [1, 3],
        "data": {
            "signals": {
                "source": "file",
                "path": "alpha/{Month}.dat",
                "read_options": {"sep": r"\s+"},
                "columns": {"asset_id": "bid"},
                "values": ["comp_ew"],
            }
        },
        "returns": [
            {
                "source": "db",
                "connection": "risk_models",
                "query": "returns.sql",
                "match_on": "Month",
                "columns": {"date": "dt", "asset_id": "bid"},
                "series": {"total": {"column": "ret", "kind": "total"}},
            }
        ],
        "metrics": [{"name": "ic", "params": {"min_obs": 5}}],
        "cache": {"enabled": False},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config))
    assert validate(path).ok

    fake = FakeDB(daily)
    monkeypatch.setattr(LoadContext, "db", lambda self, connection: fake)
    report = evaluate(path)
    assert report.ok, report.errors
    # 期初の1行前の月末日（含まない）〜 期末から最大ホライズン先の月末日
    assert fake.calls == [{"start": "20191231", "end": "20210131"}]
    ts = report["ic"].tables["timeseries"]
    h1 = ts[ts["horizon"] == 1]
    assert h1["date"].tolist() == MONTHS[1:11]
    assert h1["ic"].to_numpy() == pytest.approx(1.0)
