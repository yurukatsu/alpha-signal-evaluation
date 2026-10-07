"""Barra ソース（``source: barra``）の読み込み。DB は SQL を解釈する偽物で置き換える。

確認すること:
- 単位（DRTN / SRTN の % → 小数、共分散の %² → 小数²）と期間集約（total は複利、specific は加算）
- BID → nri_code の変換（末尾の空白、日付ごとの対応表、1対1でない組の除外）
- USD 換算（_IDTY の有効期間で通貨を引き、_D_RATE の変化率を掛ける）
- スナップショットの日付（目標日に Barra のデータがなければ、その前の営業日）
- ファクター名（MDL の接頭辞を除く）とグループ、共分散の対称化
- config の検証（モデル名、match_on、接続先の環境変数）
"""

import datetime
import re

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from alpha_signal_evaluation import evaluate, validate
from alpha_signal_evaluation.config import EvaluationConfig
from alpha_signal_evaluation.data import barra
from alpha_signal_evaluation.data.loaders import LoadContext
from alpha_signal_evaluation.pipeline.preprocess import build_bundle

MODEL = "JPE4"
NRI = ["0001", "0002", "0003", "A004"]
BIDS = ["JPNA001", "JPNA002", "JPNA003", "JPNA004"]
FACTORS = {"101": "BETA", "102": "SIZE", "201": "BANKS"}


class FakeDB:
    """テーブルを DataFrame で持ち、data/barra.py が発行する形の SELECT だけを解釈する。"""

    def __init__(self, tables: dict[str, pd.DataFrame]):
        self.tables = tables
        self.queries: list[tuple[str, dict]] = []

    def execute(self, sql: str, params: dict) -> pd.DataFrame:
        self.queries.append((sql, params))
        table = re.search(r"FROM\s+(?:RISK_MODELS\.dbo\.|public\.)(\w+)", sql).group(1)
        df = self.tables[table].reset_index(drop=True)
        dates = [v for k, v in params.items() if k.startswith("dates_")]
        bids = [v for k, v in params.items() if k.startswith("bids_")]
        if table == "barraid_jp":
            values = df["date"].map(lambda d: d.isoformat())
        else:
            values = df.get("DATE")
        mask = pd.Series(True, index=df.index)
        if "start" in params:
            mask &= (values > params["start"]) & (values <= params["end"])
        if dates:
            mask &= values.isin(dates)
        if bids:
            # SQL Server の比較は末尾の空白を無視する
            mask &= df["BID"].str.rstrip().isin(bids)
        df = df[mask]
        select = re.search(r"SELECT\s+(.*?)\s+FROM", sql, re.S).group(1)
        aliases = dict(re.findall(r"(\w+)\s+AS\s+(\w+)", select))
        out = df[list(aliases)].rename(columns=aliases)
        if "DISTINCT" in select:
            out = out.drop_duplicates()
        return out.reset_index(drop=True)


class World:
    """月次カレンダー（Base_day = 月末、Trading_day = 最終営業日）と日次の Barra データ。"""

    def __init__(self, root, rng_seed=0):
        rng = np.random.default_rng(rng_seed)
        self.root = root
        months = pd.date_range("2019-12-31", periods=9, freq="ME")
        self.base = months.strftime("%Y%m%d").tolist()
        self.trading = [pd.offsets.BMonthEnd().rollback(m).strftime("%Y%m%d") for m in months]
        days = pd.bdate_range("2019-11-01", "2020-08-31")
        self.days = days.strftime("%Y%m%d").tolist()
        # 3月の最終営業日は Barra のデータがない（スナップショットは前の営業日を使う）
        self.holiday = self.trading[3]
        barra_days = [d for d in self.days if d != self.holiday]

        n = len(barra_days)
        self.drtn = pd.DataFrame(rng.normal(0, 2, (n, 4)), index=barra_days, columns=BIDS)
        self.srtn = pd.DataFrame(rng.normal(0, 1, (n, 4)), index=barra_days, columns=BIDS)
        self.rate = pd.DataFrame(
            {"JPY": 0.9 + np.cumsum(rng.normal(0, 0.005, n)), "USD": 100.0}, index=barra_days
        )
        self.frtn = pd.DataFrame(rng.normal(0, 0.01, (n, 3)), index=barra_days, columns=[*FACTORS])
        self.exp = {
            d: pd.DataFrame(rng.normal(0, 1, (4, 3)), index=BIDS, columns=[*FACTORS])
            for d in barra_days
        }
        self.cov = {d: (lambda a: a @ a.T)(rng.normal(0, 3, (3, 3))) for d in barra_days}

    def tables(self) -> dict[str, pd.DataFrame]:
        def long(frame, name):
            s = frame.stack().rename(name).rename_axis(["DATE", "BID"]).reset_index()
            s["DATE"] = s["DATE"].astype(int)
            s["BID"] = s["BID"] + " "  # 末尾の空白
            return s

        fac = pd.DataFrame(
            {
                "FCD": [201, 101, 102],
                "FGROUP": ["2-Industries", "1-Risk_Indices", "1-Risk_Indices"],
                "FAC": ["JPE4_BANKS", "JPE4_BETA", "SIZE"],
                "MDL": ["JPE4 ", "JPE4 ", "JPE4 "],
            }
        )
        exp = pd.concat(
            [
                f.stack().rename("EXP").rename_axis(["BID", "FCD"]).reset_index().assign(DATE=d)
                for d, f in self.exp.items()
            ]
        )
        exp["DATE"] = exp["DATE"].astype(int)
        exp["FCD"] = exp["FCD"].astype(int)
        # 共分散は上三角だけを持つ
        cov = pd.DataFrame(
            [
                {"DATE": int(d), "FCD1": int(a), "FCD2": int(b), "COV": m[i, j]}
                for d, m in self.cov.items()
                for i, a in enumerate(FACTORS)
                for j, b in enumerate(FACTORS)
                if i <= j
            ]
        )
        frtn = self.frtn.stack().rename("RTN").rename_axis(["DATE", "FCD"]).reset_index()
        frtn["DATE"] = frtn["DATE"].astype(int)
        frtn["FCD"] = frtn["FCD"].astype(int)
        rate = self.rate.stack().rename("RATE").rename_axis(["DATE", "CUR"]).reset_index()
        rate["DATE"] = rate["DATE"].astype(int)
        # JPNA003 は 2020-04 から USD 建て。JPNA004 は常に USD
        idty = pd.DataFrame(
            {
                "BID": ["JPNA001 ", "JPNA002 ", "JPNA003 ", "JPNA003 ", "JPNA004 "],
                "CUR": ["JPY", "JPY", "JPY", "USD", "USD"],
                "FDATE": [19900101, 19900101, 19900101, 20200401, 19900101],
                "TDATE": [None, None, 20200331, None, None],
            }
        )
        id_map = pd.DataFrame(
            [
                {
                    "date": datetime.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}"),
                    "nri_code": n,
                    "bid": b + " ",
                }
                for d in self.days
                for n, b in zip(NRI, BIDS, strict=True)
            ]
        )
        return {
            "barraid_jp": id_map,
            f"{MODEL}_FAC": fac,
            f"{MODEL}_D_PRC": long(self.drtn, "DRTN"),
            f"{MODEL}_D_SRTN": long(self.srtn, "SRTN"),
            f"{MODEL}_D_EXP": exp,
            f"{MODEL}_D_COV": cov,
            f"{MODEL}_D_FCTRTN": frtn,
            f"{MODEL}_D_RATE": rate,
            f"{MODEL}_IDTY": idty,
        }

    def write(self) -> None:
        header = "#Base_day\tTrading_day\n"
        rows = "".join(f"{b}\t{t}\n" for b, t in zip(self.base, self.trading, strict=True))
        (self.root / "calendar.tsv").write_text(header + rows)
        (self.root / "signals").mkdir()
        rng = np.random.default_rng(1)
        for b in self.base:
            pd.DataFrame({"code": NRI, "alpha": rng.normal(size=4)}).to_parquet(
                self.root / "signals" / f"{b}.parquet"
            )

    def config(self, returns=None, risk_models=None, metrics=None) -> dict:
        return {
            "version": 1,
            "calendar": {"path": str(self.root / "calendar.tsv"), "index": "Base_day"},
            "period": {"start": self.base[1], "end": self.base[6]},
            "horizons": [1, 2],
            "data": {
                "signals": {
                    "source": "file",
                    "path": str(self.root / "signals" / "{Base_day}.parquet"),
                    "columns": {"asset_id": "code"},
                    "values": ["alpha"],
                }
            },
            "returns": returns or [],
            "risk_models": risk_models or {},
            "metrics": metrics or [],
            "cache": {"enabled": False},
        }

    def period_days(self, row: int) -> list[str]:
        """realized で行 row の期間 (Trading_day[row-1], Trading_day[row]] の Barra の営業日。"""
        return [d for d in self.drtn.index if self.trading[row - 1] < d <= self.trading[row]]


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.write()
    w.fakes = {"risk_models": FakeDB(w.tables()), "fs_trfac_glb": FakeDB(w.tables())}
    monkeypatch.setattr(LoadContext, "db", lambda self, connection: w.fakes[connection])
    return w


@pytest.fixture
def barra_env(monkeypatch):
    for name in [
        "IRDDB_HOST",
        "RISK_MODELS_USERNAME",
        "RISK_MODELS_PASSWORD",
        "RDS_APP_002_HOST",
        "RDS_APP_002_USERNAME",
        "RDS_APP_002_PASSWORD",
    ]:
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("USER_FRIENDLY_NAME", raising=False)


def returns_source(**kw):
    return {"source": "barra", "model": MODEL, "match_on": "Trading_day", **kw}


def test_returns_local_units_mapping_and_aggregation(world):
    cfg = EvaluationConfig.model_validate(world.config(returns=[returns_source()]))
    bundle = build_bundle(cfg).bundle

    total = bundle.forward_returns["total"][1]
    specific = bundle.forward_returns["specific"][2]
    assert list(total.columns) == NRI  # nri_code に変換済み（末尾の空白なし）
    for key in bundle.dates:
        row = world.base.index(key)
        days = world.period_days(row + 1)
        expected = (1 + world.drtn.loc[days] / 100).prod() - 1  # 複利
        np.testing.assert_allclose(total.loc[key].to_numpy(), expected.to_numpy())
        if row + 2 < len(world.base):
            days2 = days + world.period_days(row + 2)
            expected2 = (world.srtn.loc[days2] / 100).sum()  # 加算
            np.testing.assert_allclose(specific.loc[key].to_numpy(), expected2.to_numpy())
        else:  # カレンダーの末尾を超える
            assert specific.loc[key].isna().all()


def test_returns_in_usd(world):
    cfg = EvaluationConfig.model_validate(
        world.config(returns=[returns_source(currency="USD", series={"usd": "total"})])
    )
    total = build_bundle(cfg).bundle.forward_returns["usd"][1]

    fx = world.rate / world.rate.shift(1) - 1
    for key in total.index:
        days = world.period_days(world.base.index(key) + 1)
        for nri, bid in zip(NRI, BIDS, strict=True):
            # JPNA003 は 4月の期間から USD（期間の境界は3月末の営業日）
            usd = bid == "JPNA004" or (bid == "JPNA003" and days[0] >= "20200401")
            f = 0.0 if usd else fx.loc[days, "JPY"]
            expected = np.prod((1 + world.drtn.loc[days, bid] / 100) * (1 + f)) - 1
            assert total.loc[key, nri] == pytest.approx(expected)


def test_risk_model(world):
    cfg = EvaluationConfig.model_validate(
        world.config(
            returns=[returns_source()],
            risk_models={
                "jpe4": {
                    "source": "barra",
                    "model": MODEL,
                    "match_on": "Trading_day",
                    "specific_return": "specific",
                }
            },
        )
    )
    prepared = build_bundle(cfg)
    model = prepared.bundle.risk_models["jpe4"]

    assert model.factor_groups == {
        "risk_indices": ["BETA", "SIZE"],
        "industries": ["BANKS"],
        "style": ["BETA", "SIZE"],
    }
    assert list(model.exposures.columns) == ["BETA", "SIZE", "BANKS"]

    # 3月の最終営業日は Barra のデータがないので、その前の営業日を使う
    march = world.base[3]
    prev = max(d for d in world.exp if d < world.holiday)
    got = model.exposures.loc[march].loc[NRI].to_numpy()
    np.testing.assert_allclose(got, world.exp[prev].loc[BIDS, ["101", "102", "201"]].to_numpy())
    april = world.base[4]
    np.testing.assert_allclose(
        model.exposures.loc[april].loc[NRI].to_numpy(),
        world.exp[world.trading[4]].loc[BIDS, ["101", "102", "201"]].to_numpy(),
    )

    # 共分散: 対称、%² → 小数²
    cov = model.factor_covariance.loc[april]
    assert list(cov.index) == list(cov.columns) == ["BETA", "SIZE", "BANKS"]
    expected = world.cov[world.trading[4]][np.ix_([0, 1, 2], [0, 1, 2])] * 1e-4
    np.testing.assert_allclose(cov.to_numpy(), expected)

    # ファクターリターン: 日次の小数を加算
    fwd = model.forward_factor_returns[1]
    days = world.period_days(5)
    expected = world.frtn.loc[days, ["101", "102", "201"]].sum().to_numpy()
    np.testing.assert_allclose(fwd.loc[april, ["BETA", "SIZE", "BANKS"]].to_numpy(), expected)
    assert not any("specific_return" in w for w in prepared.warnings)


def test_evaluate_with_barra(world, barra_env):
    cfg = world.config(
        returns=[returns_source(series={"total": "total"})],
        risk_models={
            "jpe4": {
                "source": "barra",
                "model": MODEL,
                "match_on": "Trading_day",
                "components": ["exposures"],
            }
        },
        metrics=[
            {"name": "ic", "params": {"min_obs": 3}},
            {"name": "factor_correlation", "params": {"min_obs": 3}},
        ],
    )
    assert validate(cfg).ok
    report = evaluate(cfg)
    assert report.ok, report.errors
    # style グループ（risk_indices）のエクスポージャーとの相関が入る
    matrix = report["factor_correlation"].tables["matrix"]
    assert {"BETA", "SIZE"} <= set(matrix.columns)
    assert "BANKS" not in matrix.columns


def test_id_map_drops_ambiguous_pairs(world):
    table = world.fakes["fs_trfac_glb"].tables["barraid_jp"]
    day = datetime.date(2020, 3, 2)
    extra = pd.DataFrame([{"date": day, "nri_code": "9999", "bid": "JPNA001"}])
    world.fakes["fs_trfac_glb"].tables["barraid_jp"] = pd.concat([table, extra])

    warnings = []
    with LoadContext(calendar=None) as ctx:
        got = barra.load_id_map(["20200302", "20200303"], ctx, warnings)
    on_day = got[got["_target"] == "20200302"]
    assert set(on_day["asset_id"]) == {"0002", "0003", "A004"}  # 0001 と 9999 は捨てる
    assert len(got[got["_target"] == "20200303"]) == 4
    assert any("one-to-one" in w for w in warnings)


def test_resolve_asof():
    warnings = []
    got = barra.resolve_asof(
        ["20200103", "20200110"], ["20200105", "20200110", "20200101", "20200301"], "x", warnings
    )
    assert got == {"20200105": "20200103", "20200110": "20200110"}
    assert "2 date(s)" in warnings[0]  # 20200101 は前がない、20200301 は古すぎる


def test_config_rejects_unknown_model_and_column_names(world):
    with pytest.raises(ValidationError, match="JPE4"):
        EvaluationConfig.model_validate(world.config(returns=[returns_source(model="JPE5")]))
    with pytest.raises(ValidationError, match="kind only"):
        EvaluationConfig.model_validate(
            world.config(returns=[returns_source(series={"t": {"column": "x", "kind": "total"}})])
        )
    with pytest.raises(ValidationError, match="components"):
        EvaluationConfig.model_validate(
            world.config(
                risk_models={
                    "m": {
                        "source": "barra",
                        "model": MODEL,
                        "match_on": "Trading_day",
                        "components": ["specific_risk"],
                    }
                }
            )
        )


def test_validation_reports_match_on_and_connections(world, monkeypatch, barra_env):
    monkeypatch.delenv("RDS_APP_002_HOST")
    cfg = world.config(
        returns=[returns_source(match_on="Trade_day")],
        risk_models={"m": {"source": "barra", "model": MODEL, "match_on": "Base_day"}},
    )
    report = validate(cfg)
    assert any(
        "returns[0].match_on: calendar has no column 'Trade_day'" in e for e in report.errors
    )
    assert any("fs_trfac_glb" in e and "host" in e for e in report.errors)
    assert not any("risk_models.m" in e for e in report.errors)
