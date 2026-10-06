"""アルファの減衰（ホライズン別の予測力）の metric ``alpha_decay``。

ホライズンごとに IC と分位スプレッドを求め、予測力がホライズンとともにどう減衰するかを
1つの表にまとめる。
"""

from typing import Literal

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from ._common import (
    assign_quantiles,
    cross_sectional_corr,
    per_period_rate,
    summarize_series,
)
from .base import Metric, MetricResult, SignalReturnParams
from .registry import register_metric

# 期間内の集約（kind から固定）。h 期間のリターンを1期間あたりに換算するのに使う
_PER_PERIOD = {"total": "compound", "specific": "sum"}


class AlphaDecayParams(SignalReturnParams):
    """``alpha_decay`` metric のパラメータ。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        returns: 評価するリターン系列名（``total`` / ``specific`` 等）。
            デフォルト ``None``（全系列）。
        horizons: 評価するホライズン。デフォルト ``None``（config の全ホライズン）。
        method: IC の相関の種類。``"spearman"``（順位相関、デフォルト）または
            ``"pearson"``。
        n_quantiles: スプレッドに使う分位数（2 以上）。デフォルト 5。
            spread = Q{n_quantiles} − Q1。
        min_obs: 日付ごとに IC を計算する最小銘柄数（3 以上）。デフォルト 10。
            分位スプレッドには適用されない（分位は銘柄数が ``n_quantiles`` 以上の日付で
            割り当てられる）。
    """

    method: Literal["spearman", "pearson"] = "spearman"
    n_quantiles: int = Field(default=5, ge=2)
    min_obs: int = Field(default=10, ge=3)


@register_metric
class AlphaDecay(Metric):
    """ホライズン別の IC と分位スプレッドの減衰。

    シグナル × リターン系列 × ホライズンの組ごとに、``data.aligned`` のシグナルと
    フォワードリターンから次を求める。

    - IC: 日付ごとのクロスセクション相関（``ic`` metric と同じ計算）の平均と、
      lag = horizon − 1 の Newey-West t 値。
    - 分位スプレッド: 日付ごとにシグナルの順位で 1..``n_quantiles`` の分位を割り当て
      （同順位は出現順で分割）、上位分位と下位分位の等ウェイト平均リターンの差
      spread = Q{n} − Q1 を日付ごとに求め、その平均と Newey-West t 値。
      ``mean_spread`` は h 期間リターンのままの値。
    - ``spread_per_period``: 上位・下位それぞれの h 期間平均リターン（日付平均）を
      1期間あたりに換算してから差を取る。換算方法はリターンの kind で固定
      （``total``: 幾何 ``(1 + r) ** (1 / h) - 1``、``specific``: 算術 ``r / h``）。
      異なるホライズンの値を同じ尺度で比べるための指標。

    パラメータは :class:`AlphaDecayParams` を参照。

    出力:
        tables["decay"]: 1行が (signal, returns, horizon) の1組。
            columns=[signal, returns, horizon, mean_ic, ic_t_stat, mean_spread,
            spread_t_stat, spread_per_period]。
        summary: ``"{signal}/{returns}/h{horizon}/mean_ic"`` と
            ``"{signal}/{returns}/h{horizon}/spread_per_period"``。
    """

    name = "alpha_decay"
    description = "IC and quantile spread by horizon"
    requires = frozenset({"signals", "forward_returns"})
    Params = AlphaDecayParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全ての (signal, returns, horizon) の組について IC とスプレッドを求める。

        Args:
            data: pipeline が用意した時点合わせ済みのデータ。``data.return_kinds`` から
                各リターン系列の kind を取り、1期間あたりへの換算方法を決める。

        Returns:
            ``decay`` の表、平均 IC と ``spread_per_period`` の summary、
            換算方法の注記を持つ MetricResult。
        """
        p = self.params
        n = p.n_quantiles
        dates = pd.Index(data.dates, name=DATE)
        rows = []
        for signal in data.resolve_signals(p.signals):
            for ret in data.resolve_returns(p.returns):
                method = _PER_PERIOD[data.return_kinds[ret]]
                for h in data.resolve_horizons(p.horizons):
                    df = data.aligned(signal, ret, h)
                    ic = cross_sectional_corr(df, signal, "forward_return", p.method, p.min_obs)
                    ic_stats = summarize_series(ic["corr"].reindex(dates), h)

                    q = assign_quantiles(df[signal], n)
                    by_q = df["forward_return"].groupby([q.index.get_level_values(DATE), q]).mean()
                    by_q = by_q.unstack()
                    top = by_q.get(float(n), pd.Series(dtype=float))
                    bottom = by_q.get(1.0, pd.Series(dtype=float))
                    spread_stats = summarize_series(top - bottom, h)
                    rows.append(
                        {
                            "signal": signal,
                            "returns": ret,
                            "horizon": h,
                            "mean_ic": ic_stats["mean"],
                            "ic_t_stat": ic_stats["t_stat"],
                            "mean_spread": spread_stats["mean"],
                            "spread_t_stat": spread_stats["t_stat"],
                            # 上位・下位それぞれを1期間あたりに換算してから差を取る
                            "spread_per_period": float(
                                per_period_rate(top.mean(), h, method)
                                - per_period_rate(bottom.mean(), h, method)
                            ),
                        }
                    )
        table = pd.DataFrame(rows)
        summary = {}
        for row in rows:
            prefix = f"{row['signal']}/{row['returns']}/h{row['horizon']}"
            summary[f"{prefix}/mean_ic"] = row["mean_ic"]
            summary[f"{prefix}/spread_per_period"] = row["spread_per_period"]
        return MetricResult(
            tables={"decay": table},
            summary=summary,
            notes=[
                "spread_per_period converts the average h-period top and bottom returns to a "
                "per-period rate (total: geometric, specific: arithmetic) before differencing."
            ],
        )
