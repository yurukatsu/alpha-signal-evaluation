from dataclasses import replace

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from alpha_signal_evaluation.data.bundle import DataSpec
from alpha_signal_evaluation.metrics import get_metric
from alpha_signal_evaluation.metrics._common import assign_quantiles, newey_west_tstat


def _summary(result, **where):
    table = result.tables["summary"]
    for k, v in where.items():
        table = table[table[k] == v]
    assert len(table) == 1, table
    return table.iloc[0]


class TestIC:
    def test_informative_signal(self, bundle):
        result = get_metric("ic")().compute(bundle)
        alpha = _summary(result, signal="alpha", returns="specific", horizon=1)
        noise = _summary(result, signal="noise", returns="specific", horizon=1)
        assert alpha["mean_ic"] > 0.2 and alpha["t_stat"] > 5
        assert abs(noise["mean_ic"]) < 0.05
        assert result.summary["alpha/specific/h1/mean_ic"] == pytest.approx(alpha["mean_ic"])

    @pytest.mark.parametrize("method", ["spearman", "pearson"])
    def test_matches_scipy_on_one_date(self, bundle, method):
        result = get_metric("ic")(
            method=method, signals=["mixed"], returns=["total"], horizons=[1]
        ).compute(bundle)
        date = bundle.dates[5]
        df = bundle.aligned("mixed", "total", 1).xs(date, level="date")
        func = stats.spearmanr if method == "spearman" else stats.pearsonr
        expected = func(df["mixed"], df["forward_return"])[0]
        ts = result.tables["timeseries"].set_index("date")
        assert ts.loc[date, "ic"] == pytest.approx(expected)
        assert ts.loc[date, "n"] == len(df)

    def test_min_obs_gives_missing(self, bundle):
        result = get_metric("ic")(min_obs=10_000).compute(bundle)
        assert result.tables["timeseries"]["ic"].isna().all()


class TestQuantile:
    def test_spread_is_positive_for_informative_signal(self, bundle):
        result = get_metric("quantile")(signals=["alpha"], returns=["specific"]).compute(bundle)
        spread = _summary(result, horizon=1, portfolio="spread")
        assert spread["arith_mean"] > 0 and spread["t_stat"] > 3
        means = [_summary(result, horizon=1, portfolio=f"Q{q}")["arith_mean"] for q in range(1, 6)]
        assert means == sorted(means)

    def test_both_arith_and_geo_means_are_reported(self, bundle):
        result = get_metric("quantile")().compute(bundle)
        assert {"arith_mean", "geo_mean"} <= set(result.tables["summary"].columns)

    def test_cumulative_follows_kind(self, bundle):
        result = get_metric("quantile")(signals=["alpha"]).compute(bundle)
        cum = result.tables["cumulative"]
        returns = result.tables["returns"]
        q1 = returns[
            (returns["returns"] == "specific")
            & (returns["horizon"] == 1)
            & (returns["quantile"] == 1)
        ]
        spec = cum[(cum["returns"] == "specific") & (cum["portfolio"] == "Q1")]
        # specific は加算で累積
        assert spec["cumulative_return"].iloc[-1] == pytest.approx(q1["mean_return"].sum())
        q1_total = returns[
            (returns["returns"] == "total") & (returns["horizon"] == 1) & (returns["quantile"] == 1)
        ]
        total = cum[(cum["returns"] == "total") & (cum["portfolio"] == "Q1")]
        # total は複利で累積
        assert total["cumulative_return"].iloc[-1] == pytest.approx(
            (1 + q1_total["mean_return"]).prod() - 1
        )

    def test_cumulative_can_be_overridden(self, bundle):
        params = {
            "signals": ["alpha"],
            "returns": ["specific"],
            "aggregation": {"cumulative": {"specific": "compound"}},
        }
        result = get_metric("quantile")(params).compute(bundle)
        returns = result.tables["returns"]
        q1 = returns[(returns["horizon"] == 1) & (returns["quantile"] == 1)]
        cum = result.tables["cumulative"]
        last = cum[cum["portfolio"] == "Q1"]["cumulative_return"].iloc[-1]
        assert last == pytest.approx((1 + q1["mean_return"]).prod() - 1)


def test_category_ic_is_positive_in_every_sector(bundle):
    result = get_metric("category")(signals=["alpha"], returns=["specific"], horizons=[1]).compute(
        bundle
    )
    table = result.tables["summary"]
    assert len(table) == 5
    assert (table["mean_ic"] > 0).all()


class TestCategoryQuantile:
    def test_neutral_spread_is_positive_for_informative_signal(self, bundle):
        result = get_metric("category_quantile")(signals=["alpha", "noise"]).compute(bundle)
        neutral = result.tables["neutral_summary"].set_index(["signal", "returns", "horizon"])
        spread = neutral[neutral["portfolio"] == "spread"]
        assert spread.loc[("alpha", "specific", 1), "t_stat"] > 3
        assert abs(spread.loc[("noise", "specific", 1), "t_stat"]) < 2
        per_category = result.tables["summary"]
        alpha = per_category[
            (per_category["signal"] == "alpha")
            & (per_category["horizon"] == 1)
            & (per_category["portfolio"] == "spread")
        ]
        assert len(alpha) == 2 * 5  # returns 2系列 × 5業種
        assert (alpha["arith_mean"] > 0).all()

    def test_quantiles_are_assigned_within_category(self, bundle):
        result = get_metric("category_quantile")(
            signals=["alpha"], returns=["total"], horizons=[1], n_quantiles=4
        ).compute(bundle)
        date = bundle.dates[3]
        returns = result.tables["returns"]
        per_category = returns[returns["date"] == date]
        # 各業種の中で分位の銘柄数がほぼ均等（16銘柄 → 4 × 4）
        counts = per_category.groupby("category")["n"]
        assert (counts.max() - counts.min() <= 1).all()
        # カテゴリ中立の分位は、各業種の同じ分位の銘柄を合わせたもの（銘柄の等ウェイト）
        neutral = result.tables["neutral_returns"]
        neutral = neutral[neutral["date"] == date].set_index("quantile")
        pooled = per_category.assign(total=lambda d: d["mean_return"] * d["n"]).groupby("quantile")
        assert (neutral["n"] == pooled["n"].sum()).all()
        expected = pooled["total"].sum() / pooled["n"].sum()
        pd.testing.assert_series_equal(neutral["mean_return"], expected, check_names=False)


class TestCategoryReturns:
    def test_matches_manual_average_over_assets_with_signals(self, bundle):
        # 半分の銘柄のシグナルを落とす → その銘柄はカテゴリ別リターンに含まれない
        assets = bundle.signals.index.get_level_values("asset_id")
        kept = replace(bundle, signals=bundle.signals[assets < "A0040"])
        result = get_metric("category_returns")(returns=["total"], horizons=[1]).compute(kept)
        date = bundle.dates[4]
        returns = result.tables["returns"]
        row = returns[(returns["date"] == date) & (returns["category"] == "S0")].iloc[0]
        members = kept.signals.xs(date, level="date").dropna(how="all").index
        sector = bundle.classification.xs(date, level="date")["category"]
        members = [a for a in members if sector[a] == "S0"]
        fwd = bundle.forward_returns["total"][1].loc[date, members]
        assert row["n"] == len(members) == 8
        assert row["mean_return"] == pytest.approx(fwd.mean())

    def test_cumulative_follows_kind(self, bundle):
        result = get_metric("category_returns")(horizons=[1]).compute(bundle)
        returns = result.tables["returns"].query("category == 'S1'")
        cum = result.tables["cumulative"].query("category == 'S1'")
        spec = returns[returns["returns"] == "specific"]["mean_return"]
        total = returns[returns["returns"] == "total"]["mean_return"]
        last = cum.groupby("returns")["cumulative_return"].last()
        assert last["specific"] == pytest.approx(spec.sum())  # specific は加算
        assert last["total"] == pytest.approx((1 + total).prod() - 1)  # total は複利


def test_alpha_decay_covers_all_horizons(bundle):
    result = get_metric("alpha_decay")(signals=["alpha"]).compute(bundle)
    decay = result.tables["decay"]
    assert sorted(decay["horizon"].unique()) == bundle.horizons
    spec = decay[decay["returns"] == "specific"].set_index("horizon")
    # specific は算術で1期間あたりに換算
    assert spec.loc[3, "spread_per_period"] < spec.loc[1, "spread_per_period"]


def test_autocorr_detects_persistent_signal():
    rng = np.random.default_rng(1)
    dates = [f"2020{m:02d}28" for m in range(1, 13)]
    values = np.zeros((12, 50))
    values[0] = rng.standard_normal(50)
    for t in range(1, 12):
        values[t] = 0.8 * values[t - 1] + 0.6 * rng.standard_normal(50)
    signals = (
        pd.DataFrame(
            values,
            index=pd.Index(dates, name="date"),
            columns=pd.Index([f"A{i}" for i in range(50)], name="asset_id"),
        )
        .stack()
        .to_frame("persistent")
    )
    from alpha_signal_evaluation.data.bundle import DataBundle

    result = get_metric("autocorr")(lags=[1, 2], method="pearson").compute(
        DataBundle(signals=signals)
    )
    summary = result.tables["summary"].set_index("lag")
    assert summary.loc[1, "acf"] > 0.6
    # AR(1) なので偏自己相関はラグ2でほぼ消える
    assert abs(summary.loc[2, "pacf"]) < 0.2


def test_factor_correlation_includes_style_exposures_only(bundle):
    result = get_metric("factor_correlation")().compute(bundle)
    matrix = result.tables["matrix"]
    assert {"size", "momentum"} <= set(matrix.columns)
    assert "ind_a" not in matrix.columns
    assert matrix.loc["alpha", "momentum"] > 0.3


def test_factor_correlation_without_risk_model(bundle):
    result = get_metric("factor_correlation")(include_exposures=False).compute(bundle)
    assert list(result.tables["matrix"].columns) == ["alpha", "noise", "mixed"]


def test_check_reports_unknown_references():
    spec = DataSpec(
        provides=frozenset({"signals", "forward_returns"}),
        signals=("value",),
        return_kinds={"total": "total"},
        horizons=(1, 3),
    )
    ic = get_metric("ic")
    errors = ic.check(ic.Params(signals=["nope"], returns=["specific"], horizons=[12]), spec)
    assert len(errors) == 3
    category = get_metric("category")
    assert "classification" in category.check(category.Params(), spec)[0]


def test_assign_quantiles_equal_counts():
    s = pd.Series(
        np.arange(10, dtype=float),
        index=pd.MultiIndex.from_product([["d1"], list("abcdefghij")], names=["date", "asset_id"]),
    )
    q = assign_quantiles(s, 5)
    assert q.value_counts().tolist() == [2] * 5
    assert q.iloc[0] == 1 and q.iloc[-1] == 5
    assert assign_quantiles(s.iloc[:3], 5).isna().all()


def test_newey_west_equals_classic_t_without_lags():
    x = pd.Series(np.random.default_rng(0).standard_normal(200) + 0.2)
    classic = x.mean() / (x.std(ddof=0) / np.sqrt(len(x)))
    assert newey_west_tstat(x, 0) == pytest.approx(classic)
