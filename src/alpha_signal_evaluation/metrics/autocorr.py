"""シグナルの自己相関の metric ``autocorr``。

シグナルのクロスセクション（銘柄の並び）がカレンダー上の数行前とどれだけ似ているか
（シグナルの持続性・回転の目安）を、自己相関と偏自己相関で測る。
"""

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field, field_validator

from ..data.bundle import DataBundle
from ._common import concat
from .base import Metric, MetricParams, MetricResult
from .registry import register_metric


class AutocorrParams(MetricParams):
    """``autocorr`` metric のパラメータ。

    リターンを使わないため ``SignalReturnParams`` ではなく ``MetricParams`` を継承する
    （``signals`` は ``Metric.check`` が存在を自動で検証する）。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        lags: 出力するラグ（カレンダーの行数）のリスト。デフォルト ``[1, 2, 3, 6, 12]``。
            空でない正の整数のリストであること。重複を除いて昇順に並べ替えられる。
        method: 相関の種類。``"spearman"``（順位相関、デフォルト）または ``"pearson"``。
        min_obs: 行 t と t−k の両方にシグナルがある銘柄の最小数（3 以上）。
            デフォルト 10。未満の行の自己相関は欠損。
    """

    signals: list[str] | None = None
    lags: list[int] = Field(default_factory=lambda: [1, 2, 3, 6, 12])
    method: Literal["spearman", "pearson"] = "spearman"
    min_obs: int = Field(default=10, ge=3)

    @field_validator("lags")
    @classmethod
    def _positive(cls, v: list[int]) -> list[int]:
        """``lags`` が空でなく全て 1 以上であることを検証し、重複を除いて昇順にする。

        Args:
            v: 入力されたラグのリスト。

        Returns:
            重複を除いて昇順に並べたラグのリスト。

        Raises:
            ValueError: 空のリスト、または 1 未満のラグを含む場合。
        """
        if not v or any(lag < 1 for lag in v):
            raise ValueError("lags must be a non-empty list of positive integers")
        return sorted(set(v))


def _rowwise_corr(a: pd.DataFrame, b: pd.DataFrame, method: str, min_obs: int) -> pd.DataFrame:
    """同じ行（日付）同士の銘柄方向の相関を求める。

    行ごとに ``a`` と ``b`` の両方が欠損でない銘柄だけを使う。``method="spearman"`` は
    その共通銘柄の中で行ごとに順位（同順位は平均順位）に変換してからピアソン相関を取る。

    Args:
        a: (date x asset) の wide 形式の値。
        b: ``a`` と同じ index・columns の値（通常 ``a`` を行方向にずらしたもの）。
        method: ``"spearman"`` または ``"pearson"``。
        min_obs: 相関を計算する最小銘柄数。未満の行の相関は欠損。

    Returns:
        index=``a`` の index（date）、columns=[autocorr, n] の DataFrame。
        ``n`` は両方が揃っている銘柄数。
    """
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
    """ラグ 1..K の自己相関から偏自己相関を求める（Durbin-Levinson 再帰）。

    途中のラグで自己相関が欠損・非有限になった、または分母が 0 になった場合は
    そこで打ち切り、以降のラグは NaN のまま返す。

    Args:
        acf: ラグ 1..K の自己相関（長さ K、``acf[k - 1]`` がラグ k）。

    Returns:
        ラグ 1..K の偏自己相関（長さ K）。
    """
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

    シグナルごとに、(date x asset) のパネルと k 行ずらしたパネルについて、行ごとに
    両方が揃っている銘柄の相関（spearman は順位に変換してから）を求める。
    銘柄数が ``min_obs`` 未満の行は欠損。ラグ k の自己相関（ACF）はこの時系列の平均
    （欠損を除く）。偏自己相関（PACF）は、ラグ 1..max(lags) の時系列平均 ACF から
    Durbin-Levinson 再帰で求める（そのため ACF は ``lags`` に含まれないラグも内部で計算する）。

    パラメータは :class:`AutocorrParams` を参照。

    出力:
        tables["timeseries"]: ``lags`` の各ラグについての行ごとの自己相関
            （評価対象の全カレンダー行）。columns=[date, autocorr, n, signal, lag]。
            ``n`` は行 t と t−k の両方にシグナルがある銘柄数。
        tables["summary"]: 1行が (signal, lag) の1組。columns=[signal, lag, acf, pacf]。
        summary: ``"{signal}/lag{lag}/acf"`` → ACF。
    """

    name = "autocorr"
    description = "Cross-sectional autocorrelation and partial autocorrelation of signals"
    requires = frozenset({"signals"})
    Params = AutocorrParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全てのシグナルについて自己相関の時系列と ACF・PACF を求める。

        Args:
            data: pipeline が用意したデータ（シグナルだけを使う）。

        Returns:
            ``timeseries`` / ``summary`` の表、ACF の summary、ラグの単位と PACF の
            求め方の注記を持つ MetricResult。
        """
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
