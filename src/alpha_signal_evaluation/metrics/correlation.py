"""シグナル間・シグナルとファクターエクスポージャーの相関の metric ``factor_correlation``。

シグナル同士の重複や、既知のリスクファクター（スタイル等）との類似度を見るため、
日付ごとのクロスセクション相関の時系列平均を求める。
"""

from typing import Literal

from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from .base import Metric, MetricParams, MetricResult
from .registry import register_metric


class FactorCorrelationParams(MetricParams):
    """``factor_correlation`` metric のパラメータ。

    リターンを使わないため ``SignalReturnParams`` ではなく ``MetricParams`` を継承する
    （``signals`` と ``risk_model`` は ``Metric.check`` が存在を自動で検証する）。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        method: 相関の種類。``"spearman"``（日付内の順位の相関、デフォルト）または
            ``"pearson"``。
        include_exposures: ``True``（デフォルト）なら、リスクモデルのエクスポージャーも
            相関の対象に加える。リスクモデルがない、エクスポージャーがない、または
            リスクモデルが複数あって ``risk_model`` を指定していない場合は加えない。
        risk_model: エクスポージャーを使うリスクモデルの名前。デフォルト ``None``
            （リスクモデルが1つだけならそれを使う）。
        factor_group: エクスポージャーのファクターを絞り込むグループ名。デフォルト
            ``"style"``。リスクモデルの ``factor_groups`` にこのグループがあれば
            そのメンバーのファクターだけを使い、なければ（または ``None`` なら）
            全ファクターを使う。
        min_obs: 日付ごとに必要な、各シグナルの欠損でない銘柄数（3 以上）。
            デフォルト 10。いずれかのシグナルが未満の日付は除く。
    """

    signals: list[str] | None = None
    method: Literal["spearman", "pearson"] = "spearman"
    # リスクモデルがあれば、シグナルとスタイルエクスポージャーの相関も出す
    include_exposures: bool = True
    risk_model: str | None = None
    factor_group: str | None = "style"  # factor_groups にこのグループがあればそのファクターに絞る
    min_obs: int = Field(default=10, ge=3)


@register_metric
class FactorCorrelation(Metric):
    """シグナル間（とシグナル × エクスポージャー）のクロスセクション相関の時系列平均。

    計算の流れ:

    1. 対象シグナルの列を取り出す。エクスポージャーを含める条件
       （:class:`FactorCorrelationParams` の ``include_exposures`` を参照）を満たせば、
       ``factor_group`` で絞ったファクターのエクスポージャーを (date, asset_id) で
       内部結合する（このときシグナル同士の相関もエクスポージャーのある銘柄に限られる）。
    2. ``method="spearman"`` なら各変数を日付内で順位（同順位は平均順位）に変換する。
    3. 全シグナルの欠損でない銘柄数が ``min_obs`` 以上の日付だけを残す。
    4. 日付ごとに全変数（シグナル + ファクター）間の相関行列を求める
       （pandas の ``corr``: 組ごとに両方が欠損でない銘柄を使うピアソン相関）。
    5. a がシグナルである組 (a, b) について、相関の時系列の平均・標準偏差・期間数を求める。
       また全変数の相関行列を日付で平均する。

    パラメータは :class:`FactorCorrelationParams` を参照。

    出力:
        tables["pairs"]: 1行が変数の組 (a, b)。a はシグナル、b はシグナルまたはファクター
            （a ≠ b。シグナル同士は (a, b) と (b, a) の両方が出る）。
            columns=[a, b, mean_corr, std_corr, n_periods]。``n_periods`` は相関が
            欠損でない日付数。
        tables["matrix"]: 日付で平均した相関行列（正方行列）。index・columns ともに
            全変数（シグナル + 使ったファクター）、index 名なし。対角は通常 1。
        summary: シグナル同士の組（名前の辞書順で a < b のものだけ）について
            ``"{a}/{b}/mean_corr"`` → 平均相関。
    """

    name = "factor_correlation"
    description = "Average cross-sectional correlation among signals (and risk-model exposures)"
    requires = frozenset({"signals"})  # risk_models は任意
    Params = FactorCorrelationParams

    def compute(self, data: DataBundle) -> MetricResult:
        """シグナル（と必要ならエクスポージャー）の相関を日付ごとに求めて平均する。

        ``risk_models`` は ``requires`` に含めない任意のデータで、bundle にあるときだけ使う。

        Args:
            data: pipeline が用意したデータ（シグナルと、あればリスクモデル）。

        Returns:
            ``pairs`` / ``matrix`` の表とシグナル同士の平均相関の summary を持つ
            MetricResult。

        Raises:
            ValueError: ``risk_model`` に bundle にない名前を指定した場合
                （エクスポージャーを含める条件を満たすとき）。
        """
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
