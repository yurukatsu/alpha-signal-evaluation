"""IC（Information Coefficient）の metric ``ic``。

シグナルと同じ行のフォワードリターンとのクロスセクション相関を日付ごとに求め、
その時系列を要約する。
"""

from typing import Literal

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from ._common import concat, cross_sectional_corr, flatten_summary, summarize_series
from .base import Metric, MetricResult, SignalReturnParams
from .registry import register_metric


class ICParams(SignalReturnParams):
    """``ic`` metric のパラメータ。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        returns: 評価するリターン系列名（``total`` / ``specific`` 等）。
            デフォルト ``None``（全系列）。
        horizons: 評価するホライズン。デフォルト ``None``（全ホライズン）。
        method: 相関の種類。``"spearman"``（順位相関、デフォルト）または ``"pearson"``。
        min_obs: 日付ごとに IC を計算する最小銘柄数（3 以上）。デフォルト 10。
            未満の日付の IC は欠損。
    """

    method: Literal["spearman", "pearson"] = "spearman"
    min_obs: int = Field(default=10, ge=3)


@register_metric
class InformationCoefficient(Metric):
    """IC（シグナルとフォワードリターンのクロスセクション相関）。

    シグナル × リターン系列 × ホライズンの組ごとに、``data.aligned`` で取り出した
    同じ (date, asset_id) のシグナルとフォワードリターンについて、日付ごとの
    クロスセクション相関を求める。``method="spearman"`` は日付内で両者を順位
    （同順位は平均順位）に変換してからピアソン相関を取り、``"pearson"`` は値そのものの
    ピアソン相関。銘柄数が ``min_obs`` 未満の日付は欠損になる。

    IC の時系列から平均・標準偏差・t 値・正の割合・期間数を求める。t 値は
    ホライズンの重複による系列相関を補正するため lag = horizon − 1 の Newey-West。

    パラメータは :class:`ICParams` を参照。

    出力:
        tables["timeseries"]: 日付ごとの IC。評価対象の全カレンダー行
            （``data.dates``）で reindex するため、データのない日付は ``ic``・``n`` が欠損。
            columns=[date, ic, n, signal, returns, horizon]。``n`` はその日付の銘柄数。
        tables["summary"]: 1行が (signal, returns, horizon) の1組。
            columns=[signal, returns, horizon, mean_ic, std_ic, t_stat, hit_rate,
            n_periods]。``hit_rate`` は IC が正の期間の割合、``n_periods`` は IC が
            欠損でない期間数。
        summary: ``"{signal}/{returns}/h{horizon}/mean_ic"`` と
            ``"{signal}/{returns}/h{horizon}/t_stat"``。
    """

    name = "ic"
    description = "IC (spearman or pearson)"
    requires = frozenset({"signals", "forward_returns"})
    Params = ICParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全ての (signal, returns, horizon) の組について IC の時系列と要約を求める。

        Args:
            data: pipeline が用意した時点合わせ済みのデータ。

        Returns:
            ``timeseries`` / ``summary`` の表、平均 IC と t 値の summary、
            t 値の計算方法の注記を持つ MetricResult。
        """
        p = self.params
        series, summary = [], []
        for signal in data.resolve_signals(p.signals):
            for ret in data.resolve_returns(p.returns):
                for h in data.resolve_horizons(p.horizons):
                    df = data.aligned(signal, ret, h)
                    ic = cross_sectional_corr(df, signal, "forward_return", p.method, p.min_obs)
                    ic = ic.reindex(pd.Index(data.dates, name=DATE))
                    key = {"signal": signal, "returns": ret, "horizon": h}
                    series.append(ic.rename(columns={"corr": "ic"}).assign(**key).reset_index())
                    summary.append({**key, **summarize_series(ic["corr"], h)})

        summary = pd.DataFrame(summary).rename(columns={"mean": "mean_ic", "std": "std_ic"})
        return MetricResult(
            tables={"timeseries": concat(series), "summary": summary},
            summary=flatten_summary(
                summary, ["signal", "returns", "horizon"], ["mean_ic", "t_stat"]
            ),
            notes=["t_stat is Newey-West with lag = horizon - 1 (overlapping horizons)."],
        )
