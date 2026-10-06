from typing import Literal

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from ._common import concat, cross_sectional_corr, flatten_summary, summarize_series
from .base import Metric, MetricResult, SignalReturnParams
from .registry import register_metric


class ICParams(SignalReturnParams):
    method: Literal["spearman", "pearson"] = "spearman"
    min_obs: int = Field(default=10, ge=3)


@register_metric
class InformationCoefficient(Metric):
    """IC（シグナルとフォワードリターンのクロスセクション相関）と ICIR。"""

    name = "ic"
    description = "IC / ICIR (spearman or pearson)"
    requires = frozenset({"signals", "forward_returns"})
    Params = ICParams

    def compute(self, data: DataBundle) -> MetricResult:
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

        summary = pd.DataFrame(summary).rename(
            columns={"mean": "mean_ic", "std": "std_ic", "ir": "icir"}
        )
        return MetricResult(
            tables={"timeseries": concat(series), "summary": summary},
            summary=flatten_summary(
                summary, ["signal", "returns", "horizon"], ["mean_ic", "icir", "t_stat"]
            ),
            notes=["t_stat is Newey-West with lag = horizon - 1 (overlapping horizons)."],
        )
