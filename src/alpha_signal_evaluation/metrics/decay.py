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
_PER_PERIOD = {"total": "compound", "residual": "sum"}


class AlphaDecayParams(SignalReturnParams):
    method: Literal["spearman", "pearson"] = "spearman"
    n_quantiles: int = Field(default=5, ge=2)
    min_obs: int = Field(default=10, ge=3)


@register_metric
class AlphaDecay(Metric):
    """ホライズン別の IC と分位スプレッドの減衰。"""

    name = "alpha_decay"
    description = "IC and quantile spread by horizon"
    requires = frozenset({"signals", "forward_returns"})
    Params = AlphaDecayParams

    def compute(self, data: DataBundle) -> MetricResult:
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
                            "icir": ic_stats["ir"],
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
                "per-period rate (total: geometric, residual: arithmetic) before differencing."
            ],
        )
