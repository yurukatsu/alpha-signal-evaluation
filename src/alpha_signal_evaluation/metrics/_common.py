"""複数の metric で使う計算ヘルパー（``_`` 始まりなので自動探索の対象外）。

ここにあるのはクロスセクション・時系列の集計だけで、時点のずらしは行わない。
入力は ``DataBundle.aligned`` 等で時点合わせ済みのデータを前提とする。
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
    """日付（と ``by`` の列）ごとに x と y のクロスセクション相関を求める。

    ``method="pearson"`` は値そのもののピアソン相関、``method="spearman"`` は
    グループ（日付 × ``by``）内で x・y をそれぞれ順位（同順位は平均順位）に変換してから
    ピアソン相関を取る。相関が定義できない（分散 0 等）場合と、観測数が ``min_obs`` 未満の
    グループは欠損になる。

    ``df`` は index に date レベルを持ち、x・y が欠損なく揃っている前提
    （``DataBundle.aligned`` の出力など）。

    Args:
        df: index に ``date`` レベルを持つ DataFrame（通常 index=(date, asset_id)）。
            ``x``・``y`` と ``by`` の列を含む。
        x: 1つ目の変数の列名（通常シグナル名）。
        y: 2つ目の変数の列名（通常 ``"forward_return"``）。
        method: ``"spearman"`` または ``"pearson"``。
        min_obs: 相関を計算する最小観測数。未満のグループの ``corr`` は欠損。
        by: 日付に加えてグループ分けに使う列名のリスト（例: カテゴリ列）。省略時は日付のみ。

    Returns:
        index=(date, *by)、columns=[corr, n] の DataFrame。``corr`` は相関係数、
        ``n`` はグループの観測数。データのあるグループだけが行になる。
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
    """平均がゼロかどうかの t 値を Newey-West 標準誤差で求める。

    ホライズン h > 1 のフォワードリターンは隣接する日付で期間が重複するため、IC や
    スプレッドの系列に系列相関が生じる。これを Bartlett カーネル
    （重み ``1 - lag / (lags + 1)``）の Newey-West 分散で補正する。分散は ``n`` で割る
    （ddof=0）。``lags=0`` なら通常の t 値（ddof=0 の標準偏差を使用）と一致する。

    欠損は除いてから計算するため、ラグは欠損を詰めた後の観測順で数えられる。
    実際に使うラグは ``min(lags, n - 1)`` まで。

    Args:
        x: 時系列（IC やスプレッド等）。
        lags: 自己共分散を考慮する最大ラグ。通常 ``horizon - 1``。

    Returns:
        t 値。有効な観測数が 2 未満、または標準誤差が 0 以下なら NaN。
    """
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
    """IC やスプレッドの時系列の要約統計を求める。

    欠損は除いて計算する。t 値はホライズンの重複を補正するため
    ``lag = horizon - 1`` の Newey-West（:func:`newey_west_tstat`）。

    Args:
        x: 時系列（index=date）。
        horizon: フォワードリターンのホライズン（カレンダーの行数）。デフォルト 1。

    Returns:
        次のキーを持つ辞書。
            - ``mean``: 平均（有効な値がなければ NaN）
            - ``std``: 標準偏差（ddof=1。有効な値が 2 未満なら NaN）
            - ``t_stat``: Newey-West t 値
            - ``hit_rate``: 値が正の期間の割合（有効な値がなければ NaN）
            - ``n_periods``: 有効な期間数（float）
    """
    v = x.dropna()
    std = v.std()
    return {
        "mean": float(v.mean()) if len(v) else np.nan,
        "std": float(std) if len(v) > 1 else np.nan,
        "t_stat": newey_west_tstat(v, horizon - 1),
        "hit_rate": float((v > 0).mean()) if len(v) else np.nan,
        "n_periods": float(len(v)),
    }


def assign_quantiles(values: pd.Series, n_quantiles: int) -> pd.Series:
    """日付ごとに 1（最小）〜 n_quantiles（最大）の分位を割り当てる。

    日付内の順位 ``rank``（``method="first"``: 同順位は出現順で分割）と有効な観測数
    ``count`` から ``floor((rank - 1) * n_quantiles / count) + 1`` で分位を決める。
    そのため各分位の銘柄数はほぼ均等になる（割り切れない場合は最大 1 銘柄の差）。

    Args:
        values: index に ``date`` レベルを持つ Series（通常 index=(date, asset_id) の
            シグナル値）。
        n_quantiles: 分位数。

    Returns:
        ``values`` と同じ index の float の Series（1.0 〜 n_quantiles）。値が欠損の行と、
        有効な観測数が ``n_quantiles`` に満たない日付の行は欠損。
    """
    g = values.groupby(level=DATE)
    rank = g.rank(method="first")
    count = g.transform("count")
    q = np.floor((rank - 1) * n_quantiles / count) + 1
    return q.where(count >= n_quantiles)


def per_period_rate(r: float | pd.Series, horizon: int, method: str):
    """h 期間のリターンを1期間あたりのリターンに換算する。

    ``method="compound"`` なら幾何的に ``(1 + r) ** (1 / horizon) - 1``、それ以外
    （``"sum"``）なら算術的に ``r / horizon``。

    Args:
        r: h 期間のリターン（スカラーまたは Series）。
        horizon: ホライズン（カレンダーの行数）。
        method: ``"compound"`` または ``"sum"``。

    Returns:
        ``r`` と同じ型の1期間あたりのリターン。
    """
    if method == "compound":
        return (1 + r) ** (1 / horizon) - 1
    return r / horizon


def flatten_summary(frame: pd.DataFrame, keys: list[str], stats: list[str]) -> dict[str, float]:
    """summary テーブルを ``"{key1}/{key2}/.../{stat}"`` の辞書にする。

    ``keys`` の列の値を ``/`` でつないだものを接頭辞とし、``stats`` の各列の値を float で
    格納する。``horizon`` 列だけは ``h{値}`` と表記する（例: ``"alpha/total/h1/mean_ic"``）。

    Args:
        frame: 1行が1組（signal, returns, horizon 等）に対応する summary テーブル。
        keys: キーにする列名（順序どおりに連結する）。
        stats: 値として取り出す統計量の列名。

    Returns:
        キー → float の辞書。``frame`` が空なら空の辞書。
    """
    out = {}
    for row in frame.to_dict("records"):
        prefix = "/".join(f"h{row[k]}" if k == "horizon" else str(row[k]) for k in keys)
        for s in stats:
            out[f"{prefix}/{s}"] = float(row[s])
    return out


def concat(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """DataFrame のリストを縦に連結する（index は振り直す）。

    Args:
        frames: 連結する DataFrame のリスト。

    Returns:
        連結した DataFrame。リストが空なら空の DataFrame。
    """
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
