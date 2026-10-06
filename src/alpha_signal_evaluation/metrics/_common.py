"""複数の metric で使う計算ヘルパー（``_`` 始まりなので自動探索の対象外）。

ここにあるのはクロスセクション・時系列の集計だけで、時点のずらしは行わない。
"""

from typing import Literal

import numpy as np
import pandas as pd

from ..data.columns import DATE

CorrMethod = Literal["spearman", "pearson"]


def cross_sectional_corr(
    df: pd.DataFrame,
    x: str,
    y: str,
    method: CorrMethod,
    min_obs: int,
    by: list[str] | None = None,
) -> pd.DataFrame:
    """日付（と ``by`` の列）ごとのクロスセクション相関。

    ``df`` は index に date を持ち、x・y が欠損なく揃っている前提。
    返り値は index=(date, *by)、columns=[corr, n]。観測数が ``min_obs`` 未満なら欠損。
    """
    keys = [df.index.get_level_values(DATE), *(df[c] for c in by or [])]
    values = df[[x, y]]
    if method == "spearman":
        values = values.groupby(keys).rank()
    g = values.groupby(keys)
    xd = values[x] - g[x].transform("mean")
    yd = values[y] - g[y].transform("mean")
    stats = pd.DataFrame({"xy": xd * yd, "xx": xd**2, "yy": yd**2}).groupby(keys).sum()
    n = g[x].count()
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = stats["xy"] / np.sqrt(stats["xx"] * stats["yy"])
    corr = corr.replace([np.inf, -np.inf], np.nan).where(n >= min_obs)
    out = pd.DataFrame({"corr": corr, "n": n})
    out.index.names = [DATE, *(by or [])]
    return out


def newey_west_tstat(x: pd.Series, lags: int) -> float:
    """平均の t 値（Newey-West）。重複するホライズン（h > 1）の系列相関を補正する。"""
    v = x.dropna().to_numpy(dtype=float)
    n = len(v)
    if n < 2:
        return np.nan
    e = v - v.mean()
    s = e @ e / n
    for lag in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - lag / (lags + 1)) * (e[lag:] @ e[:-lag]) / n
    se = np.sqrt(s / n)
    return float(v.mean() / se) if se > 0 else np.nan


def summarize_series(x: pd.Series, horizon: int = 1) -> dict[str, float]:
    """IC やスプレッドの時系列の要約統計。t 値は lag = horizon - 1 の Newey-West。"""
    v = x.dropna()
    std = v.std()
    return {
        "mean": float(v.mean()) if len(v) else np.nan,
        "std": float(std) if len(v) > 1 else np.nan,
        "ir": float(v.mean() / std) if len(v) > 1 and std > 0 else np.nan,
        "t_stat": newey_west_tstat(v, horizon - 1),
        "hit_rate": float((v > 0).mean()) if len(v) else np.nan,
        "n_periods": float(len(v)),
    }


def assign_quantiles(values: pd.Series, n_quantiles: int) -> pd.Series:
    """日付ごとに 1（最小）〜 n_quantiles（最大）の分位を割り当てる（同順位は出現順で分割）。

    観測数が分位数に満たない日付は欠損。
    """
    g = values.groupby(level=DATE)
    rank = g.rank(method="first")
    count = g.transform("count")
    q = np.floor((rank - 1) * n_quantiles / count) + 1
    return q.where(count >= n_quantiles)


def per_period_rate(r: float | pd.Series, horizon: int, method: str):
    """h 期間のリターンを1期間あたりに換算する（compound: 幾何、sum: 算術）。"""
    if method == "compound":
        return (1 + r) ** (1 / horizon) - 1
    return r / horizon


def flatten_summary(frame: pd.DataFrame, keys: list[str], stats: list[str]) -> dict[str, float]:
    """summary テーブルを ``"{key1}/{key2}/.../{stat}"`` の辞書にする。"""
    out = {}
    for row in frame.to_dict("records"):
        prefix = "/".join(f"h{row[k]}" if k == "horizon" else str(row[k]) for k in keys)
        for s in stats:
            out[f"{prefix}/{s}"] = float(row[s])
    return out


def concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
