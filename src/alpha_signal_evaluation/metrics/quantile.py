"""分位ポートフォリオ分析の metric ``quantile``。

日付ごとにシグナルで銘柄を分位に分け、等ウェイトの分位ポートフォリオのフォワードリターンと
上位 − 下位のスプレッドを求める。
"""

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
    """``quantile`` metric のパラメータ。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        returns: 評価するリターン系列名（``total`` / ``specific`` 等）。
            デフォルト ``None``（全系列）。
        horizons: 評価するホライズン。デフォルト ``None``（全ホライズン）。
        n_quantiles: 分位数（2 以上）。デフォルト 5。Q1 がシグナル最小、
            Q{n_quantiles} が最大。
        demean: ``True`` なら日付ごとにユニバース（その日付にシグナルとリターンが
            揃っている銘柄）の等ウェイト平均を差し引いた超過リターンで集計する。
            デフォルト ``False``。
        aggregation: 期間をまたぐ集計の規約（:class:`~.base.ReturnAggregation`）。
            累積リターンの方法を kind ごとに指定できる
            （デフォルト: total → compound、specific → sum）。
    """

    n_quantiles: int = Field(default=5, ge=2)
    # True なら日付ごとにユニバース平均を差し引いた超過リターンで集計する
    demean: bool = False
    aggregation: ReturnAggregation = Field(default_factory=ReturnAggregation)


@register_metric
class QuantileAnalysis(Metric):
    """分位ポートフォリオ（等ウェイト）のフォワードリターンと、上位 − 下位のスプレッド。

    シグナル × リターン系列 × ホライズンの組ごとに、``data.aligned`` で取り出した
    シグナルとフォワードリターンについて次を行う。

    1. （``demean=True`` なら）日付ごとのクロスセクション平均をリターンから差し引く。
    2. 日付ごとにシグナルの順位で 1（最小）〜 ``n_quantiles``（最大）の分位を割り当てる。
       同順位は出現順で分割し、各分位の銘柄数をほぼ均等にする。銘柄数が分位数に
       満たない日付は除く。
    3. 日付 × 分位ごとにフォワードリターンの等ウェイト平均を取る。
    4. spread = Q{n} − Q1（上位 − 下位）を日付ごとに求める。
    5. 各ポートフォリオ（Q1..Qn と spread）の時系列について算術平均と幾何平均を求める
       （:meth:`~.base.ReturnAggregation.summarize`）。spread には lag = horizon − 1 の
       Newey-West t 値と、spread が正の期間の割合も求める。
    6. ホライズン 1 のときだけ（h > 1 は期間が重複するため）累積リターン系列を作る。
       累積方法はリターンの kind（``total`` / ``specific``）ごとに ``aggregation`` で決まる
       （デフォルト: total は複利、specific は単純加算）。spread 列も同じ方法で累積する。

    パラメータは :class:`QuantileParams` を参照。

    出力:
        tables["returns"]: 日付 × 分位ごとの平均リターン（long 形式。データのある
            組だけ）。columns=[date, quantile, mean_return, n, signal, returns, horizon]。
            ``quantile`` は int（1..n）、``n`` はその分位の銘柄数。
        tables["cumulative"]: ホライズン 1 の累積リターン（long 形式。評価対象の全
            カレンダー行）。columns=[date, portfolio, cumulative_return, signal, returns]。
            ``portfolio`` は ``"Q1"``..``"Q{n}"`` と ``"spread"``。
        tables["summary"]: 1行が (signal, returns, horizon, portfolio) の1組。
            columns=[signal, returns, horizon, portfolio, arith_mean, geo_mean, t_stat,
            hit_rate]。``t_stat``・``hit_rate`` は ``portfolio == "spread"`` の行だけ値を持つ
            （他は欠損）。平均は h 期間リターンのまま（1期間あたりに換算しない）。
        summary: spread についての ``"{signal}/{returns}/h{horizon}/arith_mean"``、
            ``".../geo_mean"``、``".../t_stat"``。
    """

    name = "quantile"
    description = "Quantile portfolio returns and top-minus-bottom spread"
    requires = frozenset({"signals", "forward_returns"})
    Params = QuantileParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全ての (signal, returns, horizon) の組について分位ポートフォリオを評価する。

        Args:
            data: pipeline が用意した時点合わせ済みのデータ。``data.return_kinds`` から
                各リターン系列の kind を取り、累積方法の選択に使う。

        Returns:
            ``returns`` / ``cumulative`` / ``summary`` の表、spread の要約の summary、
            分位の定義と累積についての注記を持つ MetricResult。
        """
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
