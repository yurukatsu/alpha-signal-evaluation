"""カテゴリ（業種等）別分析の metric ``category``。

カテゴリ内の IC（カテゴリ内でのみ銘柄を比較した予測力）と、カテゴリ別の平均シグナル
（シグナルの特定カテゴリへの偏り）を求める。
"""

from typing import Literal

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import CATEGORY, DATE
from ._common import concat, cross_sectional_corr, summarize_series
from .base import Metric, MetricResult, SignalReturnParams
from .registry import register_metric


class CategoryParams(SignalReturnParams):
    """``category`` metric のパラメータ。

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

    method: Literal["spearman", "pearson"] = "spearman"
    min_obs: int = Field(default=5, ge=3)
    column: str = CATEGORY  # classification の列（role 名）


@register_metric
class CategoryAnalysis(Metric):
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

    パラメータは :class:`CategoryParams` を参照。以下 ``<column>`` は ``params.column``
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

    name = "category"
    description = "IC within each category (e.g. sector) and average signal by category"
    requires = frozenset({"signals", "forward_returns", "classification"})
    Params = CategoryParams

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
