"""config.yaml のスキーマ（1段目＝構造の検証）。

ここではカレンダーや metric の実装に依存しない検証だけを行う。
カレンダー・レジストリを読み込んだ後の意味の検証は ``validation.py`` が担う。
"""

import string
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

ReturnKind: TypeAlias = Literal["total", "residual"]
Convention: TypeAlias = Literal["realized", "forward"]
OutputFormat: TypeAlias = Literal["parquet", "xlsx", "csv"]


def _resolve_path(value: str, info: ValidationInfo) -> str:
    """相対パスを config ファイルの場所を基準に解決する（実行ディレクトリ基準ではない）。"""
    base_dir = (info.context or {}).get("base_dir")
    path = Path(value).expanduser()
    if base_dir is None or path.is_absolute():
        return str(path)
    return str(Path(base_dir) / path)


def _to_str(value: Any) -> Any:
    # YAML では 20100131 のようにクォートなしで書くと int になる
    return str(value) if isinstance(value, int) else value


def _to_list(value: Any) -> Any:
    return [value] if isinstance(value, dict) else value


PathLike = Annotated[str, AfterValidator(_resolve_path)]
DateKey = Annotated[str, BeforeValidator(_to_str), Field(pattern=r"^\d{6}(\d{2})?$")]


def template_fields(template: str) -> set[str]:
    """パステンプレート中のプレースホルダ名を返す（例: ``{Base_day}`` → ``Base_day``）。"""
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# カレンダー・期間
# ---------------------------------------------------------------------------


class CalendarConfig(_Base):
    path: PathLike
    index: str = "Base_day"


class PeriodConfig(_Base):
    start: DateKey | None = None
    end: DateKey | None = None

    @model_validator(mode="after")
    def _check_order(self) -> "PeriodConfig":
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError(f"period.start ({self.start}) is after period.end ({self.end})")
        return self


# ---------------------------------------------------------------------------
# データソース（source: file | db の discriminated union）
# ---------------------------------------------------------------------------


class FileSource(_Base):
    """日付パーティションされたファイル、またはプレースホルダなしの単一ファイル。"""

    source: Literal["file"]
    path: PathLike
    read_options: dict[str, Any] = Field(default_factory=dict)
    columns: dict[str, str] = Field(default_factory=dict)
    # 単一ファイルのとき、ファイル内の date 列をどのカレンダー列と突き合わせるか
    match_on: str | None = None

    @property
    def placeholders(self) -> set[str]:
        return template_fields(self.path)

    @property
    def is_partitioned(self) -> bool:
        return bool(self.placeholders)


class DBSource(_Base):
    """期間分を1クエリで取得する DB ソース。SQL は ``:start`` / ``:end`` を参照する。"""

    source: Literal["db"]
    connection: str
    query: PathLike
    columns: dict[str, str] = Field(default_factory=dict)
    match_on: str | None = None


Source = Annotated[FileSource | DBSource, Field(discriminator="source")]


class SignalsFileSource(FileSource):
    values: list[str] = Field(min_length=1)


class SignalsDBSource(DBSource):
    values: list[str] = Field(min_length=1)


SignalsSource = Annotated[SignalsFileSource | SignalsDBSource, Field(discriminator="source")]


class ReturnSeries(_Base):
    column: str
    kind: ReturnKind


class ReturnsFileSource(FileSource):
    convention: Convention = "realized"
    series: dict[str, ReturnSeries] = Field(min_length=1)


class ReturnsDBSource(DBSource):
    convention: Convention = "realized"
    series: dict[str, ReturnSeries] = Field(min_length=1)
    match_on: str  # DB は日次で取得してカレンダー期間に集約するため必須


ReturnsSource = Annotated[ReturnsFileSource | ReturnsDBSource, Field(discriminator="source")]


class FactorReturnsFileSource(FileSource):
    convention: Convention = "realized"


class FactorReturnsDBSource(DBSource):
    convention: Convention = "realized"
    match_on: str


FactorReturnsSource = Annotated[
    FactorReturnsFileSource | FactorReturnsDBSource, Field(discriminator="source")
]


# ---------------------------------------------------------------------------
# セクション
# ---------------------------------------------------------------------------


class DataConfig(_Base):
    # 単一ソースでもリストでも書ける。内部では常にリスト
    signals: Annotated[list[SignalsSource], BeforeValidator(_to_list)] = Field(min_length=1)
    classification: Source | None = None

    @model_validator(mode="after")
    def _unique_signal_names(self) -> "DataConfig":
        names = [v for s in self.signals for v in s.values]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"Duplicate signal names across sources: {dup}")
        return self

    @property
    def signal_names(self) -> list[str]:
        return [v for s in self.signals for v in s.values]


class RiskModelConfig(_Base):
    exposures: Source | None = None
    factor_covariance: Source | None = None
    specific_risk: Source | None = None
    factor_returns: FactorReturnsSource | None = None
    specific_return: str | None = None  # returns の系列名。同一モデルであることを明示
    factor_groups: dict[str, list[str]] | None = None

    @property
    def components(self) -> set[str]:
        names = ("exposures", "factor_covariance", "specific_risk", "factor_returns")
        return {n for n in names if getattr(self, n) is not None}


class MetricEntry(_Base):
    name: str
    id: str | None = None
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        return self.id or self.name


class ExecutionConfig(_Base):
    n_jobs: int = Field(default=1, ge=1)


class OutputConfig(_Base):
    dir: PathLike = Field(default="out", validate_default=True)
    formats: list[OutputFormat] = Field(default_factory=lambda: ["parquet"])


class CacheConfig(_Base):
    """DB 取得結果のローカルキャッシュ。"""

    enabled: bool = True
    dir: PathLike = Field(default=".cache", validate_default=True)


class EvaluationConfig(_Base):
    version: Literal[1]
    # calendar / data / returns は DataBundle を直接渡す Python API では省略できる。
    # io 経由で読み込む場合に必須であることは意味の検証（validation.py）で確認する
    calendar: CalendarConfig | None = None
    period: PeriodConfig = Field(default_factory=PeriodConfig)
    horizons: list[int] = Field(default_factory=lambda: [1], min_length=1)
    data: DataConfig | None = None
    universe: Source | None = None
    returns: list[ReturnsSource] = Field(default_factory=list)
    risk_models: dict[str, RiskModelConfig] = Field(default_factory=dict)
    # 将来のポートフォリオ（Brinson・リスク分解の入力）のために枠だけ予約している
    portfolios: list[dict[str, Any]] = Field(default_factory=list)
    metrics: list[MetricEntry] = Field(default_factory=list)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    # 省略時も相対パスを config ファイル基準で解決するため、デフォルトも検証を通す
    output: OutputConfig = Field(default_factory=dict, validate_default=True)
    cache: CacheConfig = Field(default_factory=dict, validate_default=True)

    @field_validator("horizons")
    @classmethod
    def _check_horizons(cls, v: list[int]) -> list[int]:
        if any(h < 1 for h in v):
            raise ValueError(f"horizons must be positive integers: {v}")
        if len(set(v)) != len(v):
            raise ValueError(f"horizons must be unique: {v}")
        return sorted(v)

    @field_validator("portfolios")
    @classmethod
    def _reserved_portfolios(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if v:
            raise ValueError("portfolios is reserved for a future version and not supported yet")
        return v

    @model_validator(mode="after")
    def _check_cross_references(self) -> "EvaluationConfig":
        keys = [m.key for m in self.metrics]
        dup = sorted({k for k in keys if keys.count(k) > 1})
        if dup:
            raise ValueError(
                f"Duplicate metric keys: {dup}. Set a unique `id` when using the same metric twice"
            )

        names = [n for r in self.returns for n in r.series]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"Duplicate return series names across sources: {dup}")

        for name, model in self.risk_models.items():
            if model.specific_return is not None and model.specific_return not in names:
                raise ValueError(
                    f"risk_models.{name}.specific_return refers to unknown return series "
                    f"{model.specific_return!r}. Defined series: {names}"
                )
        return self

    @property
    def return_kinds(self) -> dict[str, ReturnKind]:
        return {name: s.kind for r in self.returns for name, s in r.series.items()}

    @property
    def enabled_metrics(self) -> list[MetricEntry]:
        return [m for m in self.metrics if m.enabled]


def load_config(path: str | Path) -> EvaluationConfig:
    """YAML を読み込み、相対パスを config ファイルの場所を基準に解決して検証する。"""
    path = Path(path).expanduser().resolve()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping: {path}")
    return EvaluationConfig.model_validate(raw, context={"base_dir": path.parent})
