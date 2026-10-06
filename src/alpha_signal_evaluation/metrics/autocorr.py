from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field, field_validator

from ..data.bundle import DataBundle
from ._common import concat
from .base import Metric, MetricParams, MetricResult
from .registry import register_metric


class AutocorrParams(MetricParams):
    signals: list[str] | None = None
    lags: list[int] = Field(default_factory=lambda: [1, 2, 3, 6, 12])
    method: Literal["spearman", "pearson"] = "spearman"
    min_obs: int = Field(default=10, ge=3)

    @field_validator("lags")
    @classmethod
    def _positive(cls, v: list[int]) -> list[int]:
        if not v or any(lag < 1 for lag in v):
            raise ValueError("lags must be a non-empty list of positive integers")
        return sorted(set(v))


def _rowwise_corr(a: pd.DataFrame, b: pd.DataFrame, method: str, min_obs: int) -> pd.DataFrame:
    """同じ行（日付）同士の銘柄方向の相関。両方が揃っている銘柄だけを使う。"""
    mask = a.notna() & b.notna()
    a, b = a.where(mask), b.where(mask)
    if method == "spearman":
        a, b = a.rank(axis=1), b.rank(axis=1)
    ad = a.sub(a.mean(axis=1), axis=0)
    bd = b.sub(b.mean(axis=1), axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = (ad * bd).sum(axis=1) / np.sqrt((ad**2).sum(axis=1) * (bd**2).sum(axis=1))
    n = mask.sum(axis=1)
    return pd.DataFrame({"autocorr": corr.where(n >= min_obs), "n": n})


def _pacf(acf: np.ndarray) -> np.ndarray:
    """ラグ 1..K の自己相関から偏自己相関を求める（Durbin-Levinson）。"""
    k_max = len(acf)
    rho = np.concatenate([[1.0], acf])
    pacf = np.full(k_max, np.nan)
    phi = np.zeros((k_max + 1, k_max + 1))
    for k in range(1, k_max + 1):
        num = rho[k] - sum(phi[k - 1, j] * rho[k - j] for j in range(1, k))
        den = 1 - sum(phi[k - 1, j] * rho[j] for j in range(1, k))
        if not np.isfinite(num) or not np.isfinite(den) or den == 0:
            break
        phi[k, k] = num / den
        for j in range(1, k):
            phi[k, j] = phi[k - 1, j] - phi[k, k] * phi[k - 1, k - j]
        pacf[k - 1] = phi[k, k]
    return pacf


@register_metric
class SignalAutocorrelation(Metric):
    """シグナルのクロスセクション自己相関（行 t と t-k の銘柄方向の相関）と偏自己相関。

    ラグの単位はカレンダーの行数。bundle.signal_panel は評価対象の全カレンダー行で
    reindex されるため、行のずらしがそのままカレンダー上のラグになる。
    （シグナル同士の比較であり、シグナルとリターンの時点合わせではない）
    """

    name = "autocorr"
    description = "Cross-sectional autocorrelation and partial autocorrelation of signals"
    requires = frozenset({"signals"})
    Params = AutocorrParams

    def compute(self, data: DataBundle) -> MetricResult:
        p = self.params
        max_lag = max(p.lags)
        series, summary = [], []
        for signal in data.resolve_signals(p.signals):
            panel = data.signal_panel(signal)
            acf = np.full(max_lag, np.nan)
            for lag in range(1, max_lag + 1):
                corr = _rowwise_corr(panel, panel.shift(lag), p.method, p.min_obs)
                acf[lag - 1] = corr["autocorr"].mean()
                if lag in p.lags:
                    series.append(corr.reset_index().assign(signal=signal, lag=lag))
            pacf = _pacf(acf)
            for lag in p.lags:
                summary.append(
                    {
                        "signal": signal,
                        "lag": lag,
                        "acf": acf[lag - 1],
                        "pacf": pacf[lag - 1],
                    }
                )
        summary = pd.DataFrame(summary)
        return MetricResult(
            tables={"timeseries": concat(series), "summary": summary},
            summary={
                f"{r['signal']}/lag{r['lag']}/acf": float(r["acf"])
                for r in summary.to_dict("records")
            },
            notes=[
                "Lags are in calendar rows. PACF is derived from the time-averaged "
                "cross-sectional ACF via Durbin-Levinson."
            ],
        )
