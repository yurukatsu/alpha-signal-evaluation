from typing import Literal

from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from .base import Metric, MetricParams, MetricResult
from .registry import register_metric


class FactorCorrelationParams(MetricParams):
    signals: list[str] | None = None
    method: Literal["spearman", "pearson"] = "spearman"
    # リスクモデルがあれば、シグナルとスタイルエクスポージャーの相関も出す
    include_exposures: bool = True
    risk_model: str | None = None
    factor_group: str | None = "style"  # factor_groups にこのグループがあればそのファクターに絞る
    min_obs: int = Field(default=10, ge=3)


@register_metric
class FactorCorrelation(Metric):
    """シグナル間（とシグナル × エクスポージャー）のクロスセクション相関の時系列平均。"""

    name = "factor_correlation"
    description = "Average cross-sectional correlation among signals (and risk-model exposures)"
    requires = frozenset({"signals"})  # risk_models は任意
    Params = FactorCorrelationParams

    def compute(self, data: DataBundle) -> MetricResult:
        p = self.params
        signals = data.resolve_signals(p.signals)
        df = data.signals[signals]
        if (
            p.include_exposures
            and data.risk_models
            and (p.risk_model or len(data.risk_models) == 1)
        ):
            _, model = data.risk_model(p.risk_model)
            if model.exposures is not None:
                factors = model.factors(p.factor_group)
                df = df.join(model.exposures[factors], how="inner")

        dates = df.index.get_level_values(DATE)
        if p.method == "spearman":
            df = df.groupby(dates).rank()
        counts = df.notna().groupby(dates).sum()
        valid_dates = counts.index[(counts[signals] >= p.min_obs).all(axis=1)]
        df = df[dates.isin(valid_dates)]
        corr = df.groupby(level=DATE).corr()  # index=(date, 変数), columns=変数
        corr.index.names = [DATE, "a"]

        pairs = corr[corr.index.get_level_values("a").isin(signals)].stack()
        pairs.index.names = [DATE, "a", "b"]
        pairs = pairs[pairs.index.get_level_values("a") != pairs.index.get_level_values("b")]
        stats = pairs.groupby(level=["a", "b"]).agg(["mean", "std", "count"])
        stats = stats.rename(columns={"mean": "mean_corr", "std": "std_corr", "count": "n_periods"})
        matrix = corr.groupby(level="a").mean()
        matrix = matrix.loc[[c for c in matrix.columns if c in matrix.index], matrix.columns]
        return MetricResult(
            tables={
                "pairs": stats.reset_index(),
                "matrix": matrix.rename_axis(index=None),
            },
            summary={
                f"{a}/{b}/mean_corr": float(v)
                for (a, b), v in stats["mean_corr"].items()
                if a in signals and b in signals and a < b
            },
        )
