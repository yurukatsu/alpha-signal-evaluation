"""metric の拡張契約。

新しい metric の作者が書くのは「``Metric`` を継承し、``name`` / ``requires`` / ``Params`` /
``compute`` を定義して ``@register_metric`` を付ける」だけ（``_template.py`` を参照）。
"""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal, TypeAlias

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..config import ReturnKind
from ..data.bundle import REQUIREMENTS, DataBundle, DataSpec

CumulativeMethod: TypeAlias = Literal["compound", "sum"]

# 期間をまたぐ累積のデフォルト。params で変更できる
CUMULATIVE_DEFAULT: dict[ReturnKind, CumulativeMethod] = {
    "total": "compound",
    "residual": "sum",
}


class MetricParams(BaseModel):
    """metric のパラメータ。未知のキーはエラーにする（設定ミスを計算前に検出するため）。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SignalReturnParams(MetricParams):
    """シグナル × リターン系列 × ホライズンを対象とする metric の共通パラメータ。

    いずれも省略時は bundle にある全て（名前固定のデフォルトにはしない）。
    """

    signals: list[str] | None = None
    returns: list[str] | None = None
    horizons: list[int] | None = None


class ReturnAggregation(BaseModel):
    """期間をまたぐリターン集計の規約（quantile / category / alpha_decay 等で共通）。

    - サマリー統計は算術平均と幾何平均の両方を出す（選択させない）
    - 累積リターン系列は kind ごとに ``compound`` / ``sum`` を選べる。
      指定しなかった kind はデフォルト（total → compound、residual → sum）を維持する
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    cumulative: dict[ReturnKind, CumulativeMethod] = Field(
        default_factory=lambda: dict(CUMULATIVE_DEFAULT)
    )

    @field_validator("cumulative", mode="before")
    @classmethod
    def _merge_defaults(cls, v: Any) -> Any:
        if isinstance(v, Mapping):
            return {**CUMULATIVE_DEFAULT, **v}
        return v

    def cumulate(self, returns: pd.Series | pd.DataFrame, kind: ReturnKind):
        """累積リターン系列。欠損の期間は欠損のまま（前後はつなげる）。"""
        if self.cumulative[kind] == "compound":
            return (1 + returns).cumprod() - 1
        return returns.cumsum()

    @staticmethod
    def summarize(returns: pd.Series) -> dict[str, float]:
        """算術平均（予測力・t 値の評価用）と幾何平均（実運用の成長率）。"""
        r = returns.dropna()
        if r.empty:
            return {"arith_mean": np.nan, "geo_mean": np.nan}
        geo = np.expm1(np.log1p(r).mean()) if (r > -1).all() else np.nan
        return {"arith_mean": float(r.mean()), "geo_mean": float(geo)}


@dataclass
class MetricResult:
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)  # 時系列IC、分位別リターン等
    summary: dict[str, float] = field(default_factory=dict)  # 平均IC、ICIR等
    notes: list[str] = field(default_factory=list)  # レポートに載せる注記


class Metric(ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    requires: ClassVar[frozenset[str]] = frozenset()  # 必要データを宣言（data.bundle.REQUIREMENTS）
    Params: ClassVar[type[MetricParams]] = MetricParams

    def __init__(self, params: MetricParams | Mapping[str, Any] | None = None, **kwargs: Any):
        """``Metric(Params(...))``、``Metric({"k": v})``、``Metric(k=v)`` のいずれでも作れる。"""
        if params is None:
            params = self.Params(**kwargs)
        elif isinstance(params, Mapping):
            params = self.Params.model_validate({**params, **kwargs})
        elif kwargs:
            raise TypeError("Pass either a Params instance or keyword arguments, not both")
        if not isinstance(params, self.Params):
            raise TypeError(
                f"{type(self).__name__} expects {self.Params.__name__}, got {type(params).__name__}"
            )
        self.params = params

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        unknown = set(cls.requires) - REQUIREMENTS
        if unknown:
            raise TypeError(
                f"{cls.__name__}.requires has unknown names {sorted(unknown)}. "
                f"Available: {sorted(REQUIREMENTS)}"
            )

    @abstractmethod
    def compute(self, data: DataBundle) -> MetricResult: ...

    @classmethod
    def check(cls, params: MetricParams, spec: DataSpec) -> list[str]:
        """計算前の意味の検証。問題をメッセージのリストで返す。

        共通パラメータ（signals / returns / horizons / risk_model）を持つ metric は
        ここで自動的に検証される。独自の検証を足す場合は ``super().check()`` の結果に追加する。
        """
        errors = []
        missing = cls.requires - spec.provides
        if missing:
            errors.append(f"requires data that is not configured: {sorted(missing)}")
        signals = getattr(params, "signals", None)
        if signals is not None and (unknown := set(signals) - set(spec.signals)):
            errors.append(f"unknown signals {sorted(unknown)}; available: {list(spec.signals)}")
        returns = getattr(params, "returns", None)
        if returns is not None and (unknown := set(returns) - set(spec.return_kinds)):
            errors.append(
                f"unknown return series {sorted(unknown)}; available: {list(spec.return_kinds)}"
            )
        horizons = getattr(params, "horizons", None)
        if horizons is not None and (unknown := set(horizons) - set(spec.horizons)):
            errors.append(
                f"horizons {sorted(unknown)} are not in the configured horizons "
                f"{list(spec.horizons)}"
            )
        risk_model = getattr(params, "risk_model", None)
        if risk_model is not None and risk_model not in spec.risk_models:
            errors.append(
                f"unknown risk_model {risk_model!r}; available: {sorted(spec.risk_models)}"
            )
        return errors
