import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from ._common import (
    assign_quantiles,
    concat,
    flatten_summary,
    newey_west_tstat,
)
from .base import Metric, MetricResult, ReturnAggregation, SignalReturnParams
from .registry import register_metric


class QuantileParams(SignalReturnParams):
    n_quantiles: int = Field(default=5, ge=2)
    # True なら日付ごとにユニバース平均を差し引いた超過リターンで集計する
    demean: bool = False
    aggregation: ReturnAggregation = Field(default_factory=ReturnAggregation)


@register_metric
class QuantileAnalysis(Metric):
    """分位ポートフォリオ（等ウェイト）のフォワードリターンと、上位 − 下位のスプレッド。"""

    name = "quantile"
    description = "Quantile portfolio returns and top-minus-bottom spread"
    requires = frozenset({"signals", "forward_returns"})
    Params = QuantileParams

    def compute(self, data: DataBundle) -> MetricResult:
        p = self.params
        n = p.n_quantiles
        dates = pd.Index(data.dates, name=DATE)
        returns_rows, cumulative_rows, summary = [], [], []
        for signal in data.resolve_signals(p.signals):
            for ret in data.resolve_returns(p.returns):
                kind = data.return_kinds[ret]
                for h in data.resolve_horizons(p.horizons):
                    df = data.aligned(signal, ret, h)
                    if p.demean:
                        mean = df.groupby(level=DATE)["forward_return"].transform("mean")
                        df = df.assign(forward_return=df["forward_return"] - mean)
                    df = df.assign(quantile=assign_quantiles(df[signal], n)).dropna()
                    grouped = df.groupby([df.index.get_level_values(DATE), "quantile"])
                    stats = grouped["forward_return"].agg(["mean", "count"])
                    wide = stats["mean"].unstack("quantile").reindex(index=dates)
                    wide = wide.reindex(columns=[float(q) for q in range(1, n + 1)])
                    wide.columns = [f"Q{int(q)}" for q in wide.columns]
                    wide["spread"] = wide[f"Q{n}"] - wide["Q1"]

                    key = {"signal": signal, "returns": ret, "horizon": h}
                    long = stats.reset_index().rename(
                        columns={"level_0": DATE, "mean": "mean_return", "count": "n"}
                    )
                    long["quantile"] = long["quantile"].astype(int)
                    returns_rows.append(long.assign(**key))

                    for col in wide.columns:
                        row = {
                            **key,
                            "portfolio": col,
                            **p.aggregation.summarize(wide[col]),
                        }
                        if col == "spread":
                            row["t_stat"] = newey_west_tstat(wide[col], h - 1)
                            row["hit_rate"] = float((wide[col].dropna() > 0).mean())
                        summary.append(row)

                    # 重複のない1期間ホライズンだけ累積リターンを出す
                    if h == 1:
                        cum = p.aggregation.cumulate(wide, kind)
                        cumulative_rows.append(
                            cum.reset_index()
                            .melt(
                                id_vars=DATE,
                                var_name="portfolio",
                                value_name="cumulative_return",
                            )
                            .assign(signal=signal, returns=ret)
                        )

        summary = pd.DataFrame(summary)
        spread = summary[summary["portfolio"] == "spread"] if len(summary) else summary
        return MetricResult(
            tables={
                "returns": concat(returns_rows),
                "cumulative": concat(cumulative_rows),
                "summary": summary,
            },
            summary=flatten_summary(
                spread,
                ["signal", "returns", "horizon"],
                ["arith_mean", "geo_mean", "t_stat"],
            ),
            notes=[
                f"Q1 = lowest signal, Q{n} = highest; spread = Q{n} - Q1 (equal-weighted).",
                "Cumulative returns are shown for horizon 1 only (longer horizons overlap).",
                "Differences between total (compounded) and specific (summed) cumulative returns "
                "do not exactly equal the factor contribution.",
            ],
        )
