from typing import Literal

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import CATEGORY, DATE
from ._common import concat, cross_sectional_corr, summarize_series
from .base import Metric, MetricResult, SignalReturnParams
from .registry import register_metric


class CategoryParams(SignalReturnParams):
    method: Literal["spearman", "pearson"] = "spearman"
    min_obs: int = Field(default=5, ge=3)
    column: str = CATEGORY  # classification の列（role 名）


@register_metric
class CategoryAnalysis(Metric):
    """カテゴリ（業種等）内の IC と、カテゴリ別の平均シグナル（カテゴリへの偏り）。"""

    name = "category"
    description = "IC within each category (e.g. sector) and average signal by category"
    requires = frozenset({"signals", "forward_returns", "classification"})
    Params = CategoryParams

    def compute(self, data: DataBundle) -> MetricResult:
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
                                "icir": stats["ir"],
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
