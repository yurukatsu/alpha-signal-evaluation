"""metrics に渡す標準化済みデータ。

すべて ``calendar.index``（通常 Base_day）の値で整列済みであり、同じ行のシグナルと
フォワードリターンは時点合わせ済みであることを pipeline が保証する。
metrics はここにあるデータを結合・集計するだけで、時点をずらしてはいけない。
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace

import pandas as pd

from ..config import ReturnKind
from .columns import ASSET_ID, DATE

RISK_MODEL_COMPONENTS = frozenset(
    {"exposures", "factor_covariance", "specific_risk", "factor_returns"}
)

# Metric.requires に書ける名前
REQUIREMENTS = frozenset(
    {
        "signals",
        "forward_returns",
        "classification",
        "risk_models",
        "benchmark_weights",
        "portfolio_weights",
        *(f"risk_models.{c}" for c in RISK_MODEL_COMPONENTS),
    }
)


@dataclass(frozen=True)
class RiskModelData:
    exposures: pd.DataFrame | None = None  # index=(date, asset_id), columns=ファクター
    factor_covariance: pd.DataFrame | None = None  # index=date。形式はソースのまま
    specific_risk: pd.DataFrame | None = None  # index=(date, asset_id)
    # horizon -> (date x factor)。forward_returns と同じ規約で時点合わせ済み（加算で集約）
    forward_factor_returns: dict[int, pd.DataFrame] = field(default_factory=dict)
    specific_return: str | None = None  # 対応するスペシフィックリターンの系列名
    factor_groups: dict[str, list[str]] | None = None

    @property
    def components(self) -> frozenset[str]:
        present = {
            "exposures": self.exposures is not None,
            "factor_covariance": self.factor_covariance is not None,
            "specific_risk": self.specific_risk is not None,
            "factor_returns": bool(self.forward_factor_returns),
        }
        return frozenset(k for k, v in present.items() if v)

    def factors(self, group: str | None = None) -> list[str]:
        """エクスポージャーのファクター名。``group`` 指定時は factor_groups で絞る。"""
        if self.exposures is None:
            return []
        factors = list(self.exposures.columns)
        if group is None or not self.factor_groups or group not in self.factor_groups:
            return factors
        members = set(self.factor_groups[group])
        return [f for f in factors if f in members]


@dataclass(frozen=True)
class DataSpec:
    """データの中身を持たない「何が揃っているか」の記述。config の意味の検証に使う。"""

    provides: frozenset[str]
    signals: tuple[str, ...] = ()
    return_kinds: Mapping[str, ReturnKind] = field(default_factory=dict)
    horizons: tuple[int, ...] = ()
    risk_models: Mapping[str, frozenset[str]] = field(default_factory=dict)


def _empty_signals() -> pd.DataFrame:
    index = pd.MultiIndex.from_arrays([[], []], names=[DATE, ASSET_ID])
    return pd.DataFrame(index=index)


@dataclass(frozen=True)
class DataBundle:
    signals: pd.DataFrame  # index=(date, asset_id), columns=シグナル名
    forward_returns: dict[str, dict[int, pd.DataFrame]] = field(default_factory=dict)
    return_kinds: dict[str, ReturnKind] = field(default_factory=dict)
    classification: pd.DataFrame | None = None  # index=(date, asset_id), columns=[category, ...]
    risk_models: dict[str, RiskModelData] = field(default_factory=dict)
    benchmark_weights: pd.DataFrame | None = None  # 将来
    portfolio_weights: dict[str, pd.DataFrame] = field(default_factory=dict)  # 将来
    # 評価対象のカレンダー行（昇順）。欠けのない連続した行であることを pipeline が保証する
    dates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if list(self.signals.index.names) != [DATE, ASSET_ID]:
            raise ValueError(
                f"signals index must be ({DATE}, {ASSET_ID}), got {self.signals.index.names}"
            )
        missing = set(self.forward_returns) - set(self.return_kinds)
        if missing:
            raise ValueError(f"return_kinds is missing for series: {sorted(missing)}")
        if not self.dates:
            dates = self.signals.index.get_level_values(DATE).unique().sort_values()
            object.__setattr__(self, "dates", tuple(dates))

    # --- 何が揃っているか -----------------------------------------------------

    @property
    def signal_names(self) -> list[str]:
        return list(self.signals.columns)

    @property
    def return_names(self) -> list[str]:
        return list(self.forward_returns)

    @property
    def horizons(self) -> list[int]:
        return sorted({h for by_h in self.forward_returns.values() for h in by_h})

    def spec(self) -> DataSpec:
        provides: set[str] = set()
        if len(self.signals.columns):
            provides.add("signals")
        if self.forward_returns:
            provides.add("forward_returns")
        if self.classification is not None:
            provides.add("classification")
        if self.benchmark_weights is not None:
            provides.add("benchmark_weights")
        if self.portfolio_weights:
            provides.add("portfolio_weights")
        if self.risk_models:
            provides.add("risk_models")
            for model in self.risk_models.values():
                provides |= {f"risk_models.{c}" for c in model.components}
        return DataSpec(
            provides=frozenset(provides),
            signals=tuple(self.signal_names),
            return_kinds=dict(self.return_kinds),
            horizons=tuple(self.horizons),
            risk_models={n: m.components for n, m in self.risk_models.items()},
        )

    def restrict(self, requires: Iterable[str]) -> "DataBundle":
        """``requires`` に含まれないデータを空にしたコピー（契約テスト用）。"""
        requires = set(requires)
        risk_components = {r.split(".", 1)[1] for r in requires if r.startswith("risk_models.")}
        risk_models: dict[str, RiskModelData] = {}
        if "risk_models" in requires:
            risk_models = self.risk_models
        elif risk_components:
            drop = RISK_MODEL_COMPONENTS - risk_components
            risk_models = {
                name: replace(
                    model,
                    **{c: None for c in drop if c != "factor_returns"},
                    **({"forward_factor_returns": {}} if "factor_returns" in drop else {}),
                )
                for name, model in self.risk_models.items()
            }
        has_returns = "forward_returns" in requires
        return DataBundle(
            signals=self.signals if "signals" in requires else _empty_signals(),
            forward_returns=self.forward_returns if has_returns else {},
            return_kinds=self.return_kinds if has_returns else {},
            classification=self.classification if "classification" in requires else None,
            risk_models=risk_models,
            benchmark_weights=(self.benchmark_weights if "benchmark_weights" in requires else None),
            portfolio_weights=(self.portfolio_weights if "portfolio_weights" in requires else {}),
            dates=self.dates,
        )

    # --- params の省略値の解決 ----------------------------------------------

    def resolve_signals(self, names: Iterable[str] | None) -> list[str]:
        return self.signal_names if names is None else list(names)

    def resolve_returns(self, names: Iterable[str] | None) -> list[str]:
        """省略時は定義済みの全系列（名前固定のデフォルトにはしない）。"""
        return self.return_names if names is None else list(names)

    def resolve_horizons(self, horizons: Iterable[int] | None) -> list[int]:
        return self.horizons if horizons is None else sorted(horizons)

    def risk_model(self, name: str | None = None) -> tuple[str, RiskModelData]:
        """名前を省略した場合、リスクモデルが1つだけならそれを返す。"""
        if name is None:
            if len(self.risk_models) != 1:
                raise ValueError(f"Specify risk_model; available: {sorted(self.risk_models)}")
            name = next(iter(self.risk_models))
        try:
            return name, self.risk_models[name]
        except KeyError:
            raise ValueError(
                f"Unknown risk model {name!r}; available: {sorted(self.risk_models)}"
            ) from None

    # --- metrics 向けのデータ整形（時点のずらしは一切しない） ---------------------

    def aligned(self, signal: str, returns: str, horizon: int) -> pd.DataFrame:
        """同じ (date, asset_id) のシグナルとフォワードリターンを結合する。

        返り値は index=(date, asset_id)、columns=[signal, "forward_return"]。
        どちらかが欠損している行は除く。
        """
        fwd = self.forward_returns[returns][horizon].stack().rename("forward_return")
        fwd.index.names = [DATE, ASSET_ID]
        return self.signals[[signal]].join(fwd, how="inner").dropna()

    def signal_panel(self, signal: str) -> pd.DataFrame:
        """シグナルを (date x asset) の wide 形式にし、評価対象の全カレンダー行で reindex する。"""
        panel = self.signals[signal].unstack(ASSET_ID)
        return panel.reindex(pd.Index(self.dates, name=DATE))
