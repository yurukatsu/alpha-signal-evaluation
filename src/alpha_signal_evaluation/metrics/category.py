"""カテゴリ（業種等）別分析の metric。

- ``category_ic``: カテゴリ内の IC（カテゴリ内でのみ銘柄を比較した予測力）と、カテゴリ別の
  平均シグナル（シグナルの特定カテゴリへの偏り）。
- ``category_quantile``: カテゴリ内で割り当てた分位のポートフォリオ。カテゴリごとの結果と、
  全カテゴリを合わせたカテゴリ中立の結果。
- ``category_returns``: カテゴリ別の平均フォワードリターンと累積リターン（シグナルに
  依らない、評価対象の銘柄のカテゴリ別の値動き）。
"""

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import ASSET_ID, CATEGORY, DATE
from ._common import (
    CorrMethod,
    assign_quantiles,
    concat,
    cross_sectional_corr,
    cumulative_portfolios,
    flatten_summary,
    quantile_portfolios,
    summarize_portfolios,
    summarize_series,
)
from .base import Metric, MetricParams, MetricResult, ReturnAggregation, SignalReturnParams
from .registry import register_metric


class CategoryICParams(SignalReturnParams):
    """``category_ic`` metric のパラメータ。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        returns: 評価するリターン系列名（``total`` / ``specific`` 等）。
            デフォルト ``None``（全系列）。
        horizons: 評価するホライズン。デフォルト ``None``（全ホライズン）。
        method: 相関の種類。``"spearman"``（順位相関、デフォルト）または ``"pearson"``。
        min_obs: 日付 × カテゴリごとに IC を計算する最小銘柄数（3 以上）。デフォルト 5。
            未満の組の IC は欠損。
        column: カテゴリとして使う classification の列（role 名）。
            デフォルト ``"category"``。
    """

    method: CorrMethod = "spearman"
    min_obs: int = Field(default=5, ge=3)
    column: str = CATEGORY  # classification の列（role 名）


@register_metric
class CategoryIC(Metric):
    """カテゴリ（業種等）内の IC と、カテゴリ別の平均シグナル（カテゴリへの偏り）。

    カテゴリ内 IC: シグナル × リターン系列 × ホライズンの組ごとに、``data.aligned`` の
    シグナルとフォワードリターンに同じ (date, asset_id) の classification を内部結合し、
    日付 × カテゴリごとのクロスセクション相関を求める（``method="spearman"`` は
    日付 × カテゴリ内で順位に変換してからピアソン相関）。銘柄数が ``min_obs`` 未満の
    日付 × カテゴリは欠損。各カテゴリの IC 時系列について平均と
    lag = horizon − 1 の Newey-West t 値を求める。

    カテゴリ別の平均シグナル: シグナルごとに（リターンやホライズンに依らず）、
    classification と内部結合したシグナルの日付 × カテゴリごとの平均と銘柄数を求める。
    シグナルの生の値の平均であり、ユニバース平均との差ではない。

    パラメータは :class:`CategoryICParams` を参照。以下 ``<column>`` は ``params.column``
    の値（デフォルト ``category``）の列名を表す。

    出力:
        tables["ic_timeseries"]: 日付 × カテゴリごとの IC（データのある組だけ）。
            columns=[date, <column>, ic, n, signal, returns, horizon]。
            ``n`` はその日付 × カテゴリの銘柄数。
        tables["summary"]: 1行が (signal, returns, horizon, カテゴリ) の1組。
            columns=[signal, returns, horizon, <column>, mean_ic, t_stat, n_periods,
            avg_n_assets]。``n_periods`` は IC が欠損でない期間数、``avg_n_assets`` は
            そのカテゴリが現れた日付（IC が欠損の日付も含む）の平均銘柄数。
        tables["mean_signal"]: 日付 × カテゴリごとの平均シグナル。
            columns=[date, <column>, mean_signal, n, signal]。
        summary: なし（空の辞書。結果は表だけで返す）。
    """

    name = "category_ic"
    description = "IC within each category (e.g. sector) and average signal by category"
    requires = frozenset({"signals", "forward_returns", "classification"})
    Params = CategoryICParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全てのシグナルについてカテゴリ別の平均シグナルとカテゴリ内 IC を求める。

        Args:
            data: pipeline が用意した時点合わせ済みのデータ。``data.classification`` に
                ``params.column`` の列が必要。

        Returns:
            ``ic_timeseries`` / ``summary`` / ``mean_signal`` の表と注記を持つ
            MetricResult（summary 辞書は空）。

        Raises:
            KeyError: ``data.classification`` に ``params.column`` の列がない場合。
        """
        p = self.params
        classification = data.classification[[p.column]]
        series, summary, tilts = [], [], []
        for signal in data.resolve_signals(p.signals):
            labelled = data.signals[[signal]].join(classification, how="inner").dropna()
            tilt = labelled.groupby([labelled.index.get_level_values(DATE), p.column])[signal]
            tilts.append(
                tilt.agg(["mean", "count"])
                .rename(columns={"mean": "mean_signal", "count": "n"})
                .reset_index()
                .assign(signal=signal)
            )
            for ret in data.resolve_returns(p.returns):
                for h in data.resolve_horizons(p.horizons):
                    df = data.aligned(signal, ret, h).join(classification, how="inner")
                    df = df.dropna(subset=[p.column])
                    ic = cross_sectional_corr(
                        df, signal, "forward_return", p.method, p.min_obs, by=[p.column]
                    )
                    key = {"signal": signal, "returns": ret, "horizon": h}
                    series.append(ic.rename(columns={"corr": "ic"}).reset_index().assign(**key))
                    for category, s in ic["corr"].groupby(level=p.column):
                        stats = summarize_series(s, h)
                        summary.append(
                            {
                                **key,
                                p.column: category,
                                "mean_ic": stats["mean"],
                                "t_stat": stats["t_stat"],
                                "n_periods": stats["n_periods"],
                                "avg_n_assets": float(ic.loc[s.index, "n"].mean()),
                            }
                        )
        return MetricResult(
            tables={
                "ic_timeseries": concat(series),
                "summary": pd.DataFrame(summary),
                "mean_signal": concat(tilts),
            },
            notes=[
                "IC is computed within each category; "
                "categories with fewer assets than min_obs are missing."
            ],
        )


class CategoryQuantileParams(SignalReturnParams):
    """``category_quantile`` metric のパラメータ。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        returns: 評価するリターン系列名。デフォルト ``None``（全系列）。
        horizons: 評価するホライズン。デフォルト ``None``（全ホライズン）。
        n_quantiles: カテゴリ内の分位数（2 以上）。デフォルト 5。銘柄数が分位数に満たない
            日付 × カテゴリは除く。
        column: カテゴリとして使う classification の列（role 名）。
            デフォルト ``"category"``。
        aggregation: 期間をまたぐ集計の規約（:class:`~.base.ReturnAggregation`）。
            累積リターンの方法を kind ごとに指定できる。
    """

    n_quantiles: int = Field(default=5, ge=2)
    column: str = CATEGORY
    aggregation: ReturnAggregation = Field(default_factory=ReturnAggregation)


@register_metric
class CategoryQuantile(Metric):
    """カテゴリ内の分位ポートフォリオ（等ウェイト）と、カテゴリ中立のスプレッド。

    シグナル × リターン系列 × ホライズンの組ごとに、``data.aligned`` のシグナルと
    フォワードリターンに同じ (date, asset_id) の classification を内部結合し、日付 ×
    カテゴリの中でシグナルの順位から 1（最小）〜 ``n_quantiles``（最大）の分位を割り当てる
    （同順位は出現順で分割）。分位ごとの集計は ``quantile`` metric と同じ
    （等ウェイト平均、spread = Q{n} − Q1、算術平均・幾何平均、spread の Newey-West t 値、
    ホライズン 1 だけ累積リターン）で、次の2通りに行う。

    - カテゴリ別: カテゴリごとに、そのカテゴリの銘柄だけで集計する。
    - カテゴリ中立: カテゴリ内で割り当てた分位のまま全カテゴリの銘柄を合わせて集計する
      （銘柄ごとの等ウェイトなので、銘柄数の多いカテゴリほど重みが大きい）。カテゴリの
      偏りを除いたシグナルの予測力を表す。

    パラメータは :class:`CategoryQuantileParams` を参照。以下 ``<column>`` は
    ``params.column`` の値（デフォルト ``category``）の列名を表す。

    出力:
        tables["returns"]: カテゴリ別の日付 × 分位ごとの平均リターン（データのある組だけ）。
            columns=[date, quantile, mean_return, n, signal, returns, horizon, <column>]。
        tables["cumulative"]: カテゴリ別のホライズン 1 の累積リターン（評価対象の全
            カレンダー行）。columns=[date, portfolio, cumulative_return, signal, returns,
            <column>]。``portfolio`` は ``"Q1"``..``"Q{n}"`` と ``"spread"``。
        tables["summary"]: 1行が (signal, returns, horizon, <column>, portfolio) の1組。
            columns=[signal, returns, horizon, <column>, portfolio, arith_mean, geo_mean,
            t_stat, hit_rate]。``t_stat``・``hit_rate`` は spread の行だけ値を持つ。
        tables["neutral_returns"] / tables["neutral_cumulative"] /
        tables["neutral_summary"]: カテゴリ中立の結果。列は上の3つから ``<column>`` を除いたもの。
        summary: カテゴリ中立の spread についての ``"{signal}/{returns}/h{horizon}/arith_mean"``、
            ``".../geo_mean"``、``".../t_stat"``。
    """

    name = "category_quantile"
    description = "Quantile portfolios within each category and the category-neutral spread"
    requires = frozenset({"signals", "forward_returns", "classification"})
    Params = CategoryQuantileParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全ての (signal, returns, horizon) の組についてカテゴリ内の分位ポートフォリオを評価する。

        Args:
            data: pipeline が用意した時点合わせ済みのデータ。``data.classification`` に
                ``params.column`` の列が必要。

        Returns:
            カテゴリ別とカテゴリ中立の ``returns`` / ``cumulative`` / ``summary`` の表、
            カテゴリ中立の spread の要約の summary、注記を持つ MetricResult。

        Raises:
            KeyError: ``data.classification`` に ``params.column`` の列がない場合。
        """
        p = self.params
        n = p.n_quantiles
        dates = pd.Index(data.dates, name=DATE)
        classification = data.classification[[p.column]]
        tables: dict[str, list] = {
            name: []
            for name in (
                "returns",
                "cumulative",
                "summary",
                "neutral_returns",
                "neutral_cumulative",
                "neutral_summary",
            )
        }

        def collect(prefix: str, df: pd.DataFrame, key: dict, kind: str, h: int) -> None:
            """``df``（``quantile`` 列付き）の分位ポートフォリオを集計して ``tables`` に足す。"""
            long, wide = quantile_portfolios(df, df["quantile"], n, dates)
            tables[f"{prefix}returns"].append(long.assign(**key))
            rows = summarize_portfolios(wide, h, p.aggregation)
            tables[f"{prefix}summary"] += [{**key, **row} for row in rows]
            if h == 1:  # 重複のない1期間ホライズンだけ累積リターンを出す
                cum = cumulative_portfolios(wide, kind, p.aggregation)
                tables[f"{prefix}cumulative"].append(
                    cum.assign(**{k: v for k, v in key.items() if k != "horizon"})
                )

        for signal in data.resolve_signals(p.signals):
            for ret in data.resolve_returns(p.returns):
                kind = data.return_kinds[ret]
                for h in data.resolve_horizons(p.horizons):
                    df = data.aligned(signal, ret, h).join(classification, how="inner")
                    df = df.dropna(subset=[p.column])
                    df["quantile"] = assign_quantiles(df[signal], n, by=df[p.column])
                    key = {"signal": signal, "returns": ret, "horizon": h}
                    collect("neutral_", df, key, kind, h)
                    for category, sub in df.groupby(p.column):
                        collect("", sub, {**key, p.column: category}, kind, h)

        frames = {
            name: concat(rows) if name.endswith(("returns", "cumulative")) else pd.DataFrame(rows)
            for name, rows in tables.items()
        }
        neutral = frames["neutral_summary"]
        spread = neutral[neutral["portfolio"] == "spread"] if len(neutral) else neutral
        return MetricResult(
            tables=frames,
            summary=flatten_summary(
                spread, ["signal", "returns", "horizon"], ["arith_mean", "geo_mean", "t_stat"]
            ),
            notes=[
                f"Quantiles are assigned within each category; Q1 = lowest signal, Q{n} = highest; "
                f"spread = Q{n} - Q1 (equal-weighted).",
                "neutral_* tables pool all categories with the within-category quantiles "
                "(equal weight per asset).",
                "Cumulative returns are shown for horizon 1 only (longer horizons overlap).",
            ],
        )


class CategoryReturnsParams(MetricParams):
    """``category_returns`` metric のパラメータ。

    シグナルごとには計算しないため ``SignalReturnParams`` ではなく ``MetricParams`` を
    継承する（``returns`` / ``horizons`` は ``Metric.check`` が存在を自動で検証する）。

    Attributes:
        returns: 評価するリターン系列名。デフォルト ``None``（全系列）。
        horizons: 評価するホライズン。デフォルト ``None``（全ホライズン）。
        column: カテゴリとして使う classification の列（role 名）。
            デフォルト ``"category"``。
        aggregation: 期間をまたぐ集計の規約（:class:`~.base.ReturnAggregation`）。
            累積リターンの方法を kind ごとに指定できる。
    """

    returns: list[str] | None = None
    horizons: list[int] | None = None
    column: str = CATEGORY
    aggregation: ReturnAggregation = Field(default_factory=ReturnAggregation)


@register_metric
class CategoryReturns(Metric):
    """カテゴリ別の平均フォワードリターン（等ウェイト）と累積リターン。

    評価対象の銘柄（いずれかのシグナルがある (date, asset_id)。ユニバースで絞った後の
    銘柄）について、リターン系列 × ホライズンの組ごとに、日付 × カテゴリの等ウェイト
    平均フォワードリターンを求める。シグナルの値は使わず、評価対象の銘柄がカテゴリごとに
    どう動いたかを見るためのもの（シグナルの業種への偏りの影響を読むときの比較対象）。

    各カテゴリの時系列について算術平均と幾何平均を求め、ホライズン 1 のときだけ
    累積リターンを作る（累積方法はリターンの kind ごとに ``aggregation`` で決まる）。

    パラメータは :class:`CategoryReturnsParams` を参照。以下 ``<column>`` は
    ``params.column`` の値（デフォルト ``category``）の列名を表す。

    出力:
        tables["returns"]: 日付 × カテゴリごとの平均リターン（データのある組だけ）。
            columns=[date, <column>, mean_return, n, returns, horizon]。
        tables["cumulative"]: ホライズン 1 の累積リターン（評価対象の全カレンダー行）。
            columns=[date, <column>, cumulative_return, returns]。
        tables["summary"]: 1行が (returns, horizon, カテゴリ) の1組。
            columns=[returns, horizon, <column>, arith_mean, geo_mean, avg_n_assets]。
            ``avg_n_assets`` はそのカテゴリが現れた日付の平均銘柄数。
        summary: なし（空の辞書。結果は表だけで返す）。
    """

    name = "category_returns"
    description = "Average and cumulative forward returns by category (assets with signals)"
    requires = frozenset({"signals", "forward_returns", "classification"})
    Params = CategoryReturnsParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全ての (returns, horizon) の組についてカテゴリ別のリターンを求める。

        Args:
            data: pipeline が用意した時点合わせ済みのデータ。シグナルは評価対象の銘柄を
                決めるためだけに使う。``data.classification`` に ``params.column`` の列が必要。

        Returns:
            ``returns`` / ``cumulative`` / ``summary`` の表と注記を持つ MetricResult
            （summary 辞書は空）。

        Raises:
            KeyError: ``data.classification`` に ``params.column`` の列がない場合。
        """
        p = self.params
        dates = pd.Index(data.dates, name=DATE)
        universe = data.signals.index[data.signals.notna().any(axis=1)]
        classification = data.classification[[p.column]].dropna()
        returns_rows, cumulative_rows, summary = [], [], []
        for ret in data.resolve_returns(p.returns):
            kind = data.return_kinds[ret]
            for h in data.resolve_horizons(p.horizons):
                fwd = data.forward_returns[ret][h].stack().dropna().rename("forward_return")
                fwd.index.names = [DATE, ASSET_ID]
                df = fwd[fwd.index.isin(universe)].to_frame().join(classification, how="inner")
                grouped = df.groupby([df.index.get_level_values(DATE), p.column])
                stats = grouped["forward_return"].agg(["mean", "count"])
                long = stats.reset_index().rename(columns={"mean": "mean_return", "count": "n"})
                returns_rows.append(long.assign(returns=ret, horizon=h))

                wide = stats["mean"].unstack(p.column).reindex(index=dates)
                counts = stats["count"].groupby(level=p.column).mean()
                for category in wide.columns:
                    summary.append(
                        {
                            "returns": ret,
                            "horizon": h,
                            p.column: category,
                            **p.aggregation.summarize(wide[category]),
                            "avg_n_assets": float(counts[category]),
                        }
                    )
                # 重複のない1期間ホライズンだけ累積リターンを出す
                if h == 1:
                    cum = p.aggregation.cumulate(wide, kind).reset_index()
                    cumulative_rows.append(
                        cum.melt(
                            id_vars=DATE, var_name=p.column, value_name="cumulative_return"
                        ).assign(returns=ret)
                    )
        return MetricResult(
            tables={
                "returns": concat(returns_rows),
                "cumulative": concat(cumulative_rows),
                "summary": pd.DataFrame(summary),
            },
            notes=[
                "Equal-weighted average over assets with at least one signal "
                "(after the universe filter).",
                "Cumulative returns are shown for horizon 1 only (longer horizons overlap).",
            ],
        )
