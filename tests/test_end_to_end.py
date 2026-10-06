import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from alpha_signal_evaluation import ConfigValidationError, evaluate, validate
from alpha_signal_evaluation.cli import app
from alpha_signal_evaluation.config import load_config
from alpha_signal_evaluation.data.loaders import LoadContext
from alpha_signal_evaluation.pipeline import build_bundle


def test_file_pipeline_is_time_aligned(dataset):
    report = evaluate(dataset.write_config())
    assert report.ok, report.errors
    ts = report["ic"].tables["timeseries"]
    perfect = ts[(ts["signal"] == "perfect") & (ts["horizon"] == 1) & (ts["returns"] == "total")]
    # 評価期間は行 1〜10。h=1 の IC は全日付で 1（1期ずれがあれば崩れる）
    assert perfect["date"].tolist() == dataset.base[1:11]
    assert perfect["ic"].to_numpy() == pytest.approx(1.0)
    # 29 銘柄（ユニバース外の 0001 を除く）
    assert (perfect["n"] == dataset.N_ASSETS - 1).all()


def test_bundle_contents(dataset):
    prepared = build_bundle(load_config(dataset.write_config()))
    bundle = prepared.bundle
    assert bundle.dates == tuple(dataset.base[1:11])
    assert "0001" not in bundle.signals.index.get_level_values("asset_id")
    assert "0002" in bundle.signals.index.get_level_values("asset_id")  # 先頭ゼロが保たれる
    # 同じ Rskmdl_month の行は同じファイルから（行ごとに複製されて）付与される
    cls = bundle.classification
    assert len(cls.xs(dataset.base[3], level="date")) == dataset.N_ASSETS
    # h=3 は行 t+1〜t+3 の複利
    fwd = bundle.forward_returns["total"][3]
    t = 2
    expected = np.prod(1 + dataset.total[t + 1 : t + 4, 0]) - 1
    assert fwd.loc[dataset.base[t], "0001"] == pytest.approx(expected)
    # specific は加算
    fwd_s = bundle.forward_returns["specific"][3]
    assert fwd_s.loc[dataset.base[t], "0001"] == pytest.approx(
        0.5 * dataset.total[t + 1 : t + 4, 0].sum()
    )


def test_tail_beyond_calendar_is_missing_with_warning(dataset):
    cfg = dataset.config(period={"start": dataset.base[1], "end": dataset.base[12]})
    report = evaluate(
        cfg | {"calendar": {"path": str(dataset.root / "calendar.tsv")}} | _abs(dataset, cfg)
    )
    assert any("short of the longest horizon" in w for w in report.warnings)
    ts = report["ic"].tables["timeseries"]
    h3 = ts[(ts["signal"] == "perfect") & (ts["horizon"] == 3) & (ts["returns"] == "total")]
    h3 = h3.set_index("date")
    assert np.isnan(h3.loc[dataset.base[12], "ic"])


def _abs(dataset, cfg):
    """dict で渡す config の相対パスを dataset の場所に解決する。"""
    import copy

    cfg = copy.deepcopy(cfg)
    root = dataset.root
    cfg["data"]["signals"]["path"] = str(root / cfg["data"]["signals"]["path"])
    cfg["data"]["classification"]["path"] = str(root / cfg["data"]["classification"]["path"])
    cfg["universe"]["path"] = str(root / cfg["universe"]["path"])
    cfg["returns"][0]["path"] = str(root / cfg["returns"][0]["path"])
    cfg["output"]["dir"] = str(root / "out")
    cfg["cache"]["dir"] = str(root / ".cache")
    return {k: cfg[k] for k in ["data", "universe", "returns", "output", "cache"]}


def test_report_is_saved(dataset):
    report = evaluate(dataset.write_config())
    out = report.save()
    assert (out / "summary.parquet").exists()
    assert (out / "ic" / "timeseries.parquet").exists()
    assert (out / "report.xlsx").exists()
    assert (out / "run_info.json").exists()
    summary = pd.read_parquet(out / "summary.parquet")
    assert set(summary["metric"]) == {
        "ic",
        "quantile",
    }  # category は summary を持たない


def test_report_is_saved_as_csv(dataset, tmp_path):
    report = evaluate(dataset.write_config())
    out = report.save(tmp_path / "csv_out", ["csv"])
    assert (out / "summary.csv").read_bytes().startswith(b"\xef\xbb\xbf")  # Excel 向けの BOM
    ic = pd.read_csv(out / "ic" / "summary.csv", encoding="utf-8-sig")
    pd.testing.assert_frame_equal(ic, report["ic"].tables["summary"], check_dtype=False)
    assert not list(out.glob("**/*.parquet"))


class FakeDB:
    def __init__(self, daily: pd.DataFrame):
        self.daily = daily
        self.calls = []

    def execute(self, sql, params):
        self.calls.append(params)
        start = pd.Timestamp(params["start"])
        end = pd.Timestamp(params["end"])
        return self.daily[(self.daily["dt"] > start) & (self.daily["dt"] <= end)].copy()

    def close(self):
        pass


def test_db_pipeline_aggregates_daily_returns(dataset, monkeypatch, db_env):
    rng = np.random.default_rng(5)
    days = pd.bdate_range("2019-12-01", "2021-03-31")
    daily = pd.DataFrame(
        [(d, c, rng.normal(0, 0.01)) for d in days for c in dataset.codes],
        columns=["dt", "code", "ret"],
    )
    daily["sret"] = daily["ret"] / 2
    # 期待値: 行 i の期間 (Trading_day[i-1], Trading_day[i]] を複利で束ねる
    wide = daily.pivot(index="dt", columns="code", values="ret")
    td = pd.to_datetime(dataset.trading)
    period = np.full((dataset.N_ROWS, dataset.N_ASSETS), np.nan)
    for i in range(1, dataset.N_ROWS):
        window = wide[(wide.index > td[i - 1]) & (wide.index <= td[i])]
        period[i] = (1 + window).prod().to_numpy() - 1
    dataset.write_signals(period)

    fake = FakeDB(daily)
    monkeypatch.setattr(LoadContext, "db", lambda self, connection: fake)
    (dataset.root / "returns.sql").write_text(
        "SELECT dt, code, ret, sret FROM r WHERE dt > :start AND dt <= :end"
    )
    returns = [
        {
            "source": "db",
            "connection": "risk_models",
            "query": "returns.sql",
            "match_on": "Trading_day",
            "columns": {"date": "dt", "asset_id": "code"},
            "series": {
                "total": {"column": "ret", "kind": "total"},
                "specific": {"column": "sret", "kind": "specific"},
            },
        }
    ]
    report = evaluate(dataset.write_config(returns=returns))
    assert report.ok, report.errors
    # 期初の1行前から期末の最大ホライズン行先までを1クエリで取得
    assert fake.calls == [{"start": dataset.trading[0], "end": dataset.trading[13]}]
    ts = report["ic"].tables["timeseries"]
    perfect = ts[(ts["signal"] == "perfect") & (ts["horizon"] == 1) & (ts["returns"] == "total")]
    assert perfect["ic"].to_numpy() == pytest.approx(1.0)

    # 2回目はキャッシュから読む
    evaluate(dataset.write_config(returns=returns))
    assert len(fake.calls) == 1


def test_validation_reports_all_problems_at_once(dataset):
    (dataset.root / "signals" / f"{dataset.base[4]}.parquet").unlink()
    (dataset.root / "signals" / f"{dataset.base[5]}.parquet").unlink()
    result = validate(dataset.write_config())
    assert not result.ok
    assert any("2 of 10 files not found" in e for e in result.errors)

    metrics = [
        {"name": "ic", "params": {"horizons": [12]}},
        {"name": "quantile", "params": {"n_quantile": 5}},
        {"name": "nope"},
    ]
    bad_path = dataset.config()["data"]
    bad_path["classification"]["path"] = "sector/{Rskmdl}.csv"
    result = validate(dataset.write_config(metrics=metrics, data=bad_path))
    text = "\n".join(result.errors)
    assert "horizons [12]" in text
    assert "n_quantile" in text
    assert "Unknown metric 'nope'" in text
    assert "placeholders ['Rskmdl']" in text


def test_db_source_accepts_month_match_on(dataset):
    (dataset.root / "q.sql").write_text("SELECT 1")
    universe = {
        "source": "db",
        "connection": "unknown_db",
        "query": "q.sql",
        "match_on": "Rskmdl_month",
        "columns": {"asset_id": "code", "date": "dt"},
    }
    result = validate(dataset.write_config(universe=universe))
    # 月（6桁）の match_on は許可される。接続先の誤りだけが報告される
    assert len(result.errors) == 1
    assert result.errors[0].startswith("universe.connection: unknown connection 'unknown_db'")


def test_db_connection_settings_are_checked(dataset, monkeypatch):
    for name in ["IRDDB_HOST", "RISK_MODELS_USERNAME", "RISK_MODELS_PASSWORD"]:
        monkeypatch.delenv(name, raising=False)
    (dataset.root / "q.sql").write_text("SELECT 1")
    returns = [
        {
            "source": "db",
            "connection": "risk_models",
            "query": "q.sql",
            "match_on": "Trading_day",
            "columns": {"date": "dt", "asset_id": "code"},
            "series": {"total": {"column": "ret", "kind": "total"}},
        }
    ]
    result = validate(dataset.write_config(returns=returns))
    assert result.errors == [
        "connection 'risk_models' (RiskModelsConfig): host, username, password not set; "
        "set the environment variables (see .env.example)"
    ]


def test_evaluate_raises_before_computing(dataset):
    with pytest.raises(ConfigValidationError, match="Unknown metric"):
        evaluate(dataset.write_config(metrics=[{"name": "nope"}]))


def test_failed_metric_is_isolated(bundle):
    report = evaluate(
        {
            "version": 1,
            "metrics": [
                {"name": "ic"},
                {"name": "quantile", "params": {"n_quantiles": 1000}},
            ],
        },
        data=bundle,
    )
    assert report.runs["ic"].ok
    # n_quantiles が観測数より多い → 全て欠損になるが例外にはならない
    assert report.runs["quantile"].ok


def test_parallel_matches_serial(bundle):
    cfg = {
        "version": 1,
        "metrics": [{"name": "ic"}, {"name": "autocorr"}, {"name": "alpha_decay"}],
    }
    serial = evaluate(cfg, data=bundle)
    parallel = evaluate(cfg, data=bundle, n_jobs=2)
    assert parallel.ok, parallel.errors
    pd.testing.assert_frame_equal(serial.summary(), parallel.summary())


def test_api_with_data_rejects_missing_requirements(bundle):
    with pytest.raises(ConfigValidationError, match="classification"):
        evaluate(
            {"version": 1, "metrics": [{"name": "category"}]},
            data=bundle.restrict({"signals", "forward_returns"}),
        )


class TestCLI:
    runner = CliRunner()

    def test_validate(self, dataset):
        result = self.runner.invoke(app, ["validate", str(dataset.write_config())])
        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_validate_fails(self, dataset):
        result = self.runner.invoke(
            app, ["validate", str(dataset.write_config(metrics=[{"name": "nope"}]))]
        )
        assert result.exit_code == 1

    def test_invalid_structure(self, dataset):
        result = self.runner.invoke(app, ["validate", str(dataset.write_config(horizon=[1]))])
        assert result.exit_code == 1

    def test_run(self, dataset, tmp_path):
        out = tmp_path / "cli_out"
        result = self.runner.invoke(
            app, ["run", str(dataset.write_config()), "--output-dir", str(out)]
        )
        assert result.exit_code == 0, result.output
        assert (out / "report.xlsx").exists()

    def test_metrics_list(self):
        result = self.runner.invoke(app, ["metrics", "list"])
        assert result.exit_code == 0
        assert "ic" in result.output and "n_quantiles" in result.output


def test_risk_model_files_are_aligned(dataset):
    rng = np.random.default_rng(3)
    (dataset.root / "barra").mkdir()
    for r in dict.fromkeys(dataset.rskmdl):
        pd.DataFrame(
            {"code": dataset.codes, "SIZE": rng.standard_normal(dataset.N_ASSETS), "name": "x"}
        ).to_csv(dataset.root / "barra" / f"{r}.dat", sep="\t", index=False)
    factor = rng.normal(0, 0.01, (dataset.N_ROWS, 2))
    for i, b in enumerate(dataset.base):
        pd.DataFrame({"f": ["SIZE", "VALUE"], "r": factor[i]}).to_csv(
            dataset.root / "barra" / f"fret_{b}.csv", index=False
        )
    risk_models = {
        "barra": {
            "exposures": {
                "source": "file",
                "path": "barra/{Rskmdl_month}.dat",
                "read_options": {"sep": "\t"},
                "columns": {"asset_id": "code"},
            },
            "factor_returns": {
                "source": "file",
                "path": "barra/fret_{Base_day}.csv",
                "columns": {"factor": "f", "value": "r"},
            },
            "specific_return": "specific",
        }
    }
    cfg = dataset.write_config(risk_models=risk_models, metrics=[{"name": "factor_correlation"}])
    assert validate(cfg).ok
    bundle = build_bundle(load_config(cfg)).bundle
    model = bundle.risk_models["barra"]
    assert list(model.exposures.columns) == ["SIZE"]  # 数値列だけがファクター
    # realized: 行 t の h=1 ファクターリターンは行 t+1、h=3 は行 t+1〜t+3 の加算
    fwd1 = model.forward_factor_returns[1]
    fwd3 = model.forward_factor_returns[3]
    assert fwd1.loc[dataset.base[2], "SIZE"] == pytest.approx(factor[3, 0])
    assert fwd3.loc[dataset.base[2], "VALUE"] == pytest.approx(factor[3:6, 1].sum())
    assert evaluate(cfg).ok


class MultiQueryDB(FakeDB):
    """SQL の中身で返すデータを切り替える。"""

    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))
        self.daily = self.frames[sql]
        return super().execute(sql, params)


def test_db_snapshot_source(dataset, monkeypatch, db_env):
    rows = [
        {"dt": pd.Timestamp(t), "code": c, "sector": f"S{i % 2}"}
        for t in dataset.trading
        for i, c in enumerate(dataset.codes)
    ]
    fake = MultiQueryDB({"SELECT sector": pd.DataFrame(rows)})
    monkeypatch.setattr(LoadContext, "db", lambda self, connection: fake)
    (dataset.root / "sector.sql").write_text("SELECT sector")
    data = dataset.config()["data"]
    data["classification"] = {
        "source": "db",
        "connection": "risk_models",
        "query": "sector.sql",
        "match_on": "Trading_day",
        "columns": {"date": "dt", "asset_id": "code", "category": "sector"},
    }
    cfg = dataset.write_config(data=data, cache={"enabled": False})
    bundle = build_bundle(load_config(cfg)).bundle
    # 評価期間（行 1〜10）の Trading_day を (start, end] で取得し、Base_day に付け替える
    assert fake.calls[0][1] == {
        "start": (pd.Timestamp(dataset.trading[1]) - pd.Timedelta(days=1)).strftime("%Y%m%d"),
        "end": dataset.trading[10],
    }
    cls = bundle.classification
    assert sorted(cls.index.get_level_values("date").unique()) == dataset.base[1:11]
    assert cls.loc[(dataset.base[1], "0002"), "category"] == "S1"
