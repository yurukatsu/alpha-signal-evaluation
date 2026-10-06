"""config の意味の検証（2段目）。カレンダーと metric レジストリを読み込んだ後に行う。

計算を始める前に設定ミスをすべて集めて一覧で報告する。
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .config import DBSource, EvaluationConfig, FileSource, MetricEntry
from .data.bundle import DataSpec
from .data.calendar import Calendar, check_increasing, load_calendar
from .data.loaders import partition_paths
from .io.db import CONNECTIONS
from .io.file import SUPPORTED_SUFFIXES
from .metrics.base import Metric
from .metrics.registry import get_metric
from .pipeline.preprocess import EvaluationPlan, make_plan

_EIGHT_DIGITS = re.compile(r"^\d{8}$")
_MAX_EXAMPLES = 3


class ConfigValidationError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_for_errors(self) -> None:
        if self.errors:
            raise ConfigValidationError(self.errors)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def spec_from_config(cfg: EvaluationConfig) -> DataSpec:
    """config で定義されたデータから DataSpec を作る（データは読まない）。"""
    provides: set[str] = set()
    if cfg.data is not None:
        provides.add("signals")
        if cfg.data.classification is not None:
            provides.add("classification")
    if cfg.returns:
        provides.add("forward_returns")
    if cfg.risk_models:
        provides.add("risk_models")
        for model in cfg.risk_models.values():
            provides |= {f"risk_models.{c}" for c in model.components}
    return DataSpec(
        provides=frozenset(provides),
        signals=tuple(cfg.data.signal_names) if cfg.data else (),
        return_kinds=cfg.return_kinds,
        horizons=tuple(cfg.horizons),
        risk_models={name: frozenset(m.components) for name, m in cfg.risk_models.items()},
    )


def _format_validation_error(e: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in e.errors()
    ]


def validate_metrics(entries: list[MetricEntry], spec: DataSpec) -> list[str]:
    errors = []
    for entry in entries:
        prefix = f"metrics[{entry.key}]"
        try:
            metric_cls = get_metric(entry.name)
        except ValueError as e:
            errors.append(f"{prefix}: {e}")
            continue
        try:
            params = metric_cls.Params.model_validate(entry.params)
        except ValidationError as e:
            errors += [f"{prefix}.params.{msg}" for msg in _format_validation_error(e)]
            continue
        errors += [f"{prefix}: {msg}" for msg in metric_cls.check(params, spec)]
    return errors


def instantiate_metrics(entries: list[MetricEntry]) -> dict[str, Metric]:
    return {e.key: get_metric(e.name)(e.params) for e in entries}


# ---------------------------------------------------------------------------
# データソース
# ---------------------------------------------------------------------------


def _iter_sources(
    cfg: EvaluationConfig,
) -> Iterator[tuple[str, FileSource | DBSource, bool]]:
    """(ラベル, ソース, 期間データか) を列挙する。期間データはホライズン分延長して読む。"""
    if cfg.data is not None:
        for i, source in enumerate(cfg.data.signals):
            yield f"data.signals[{i}]", source, False
        if cfg.data.classification is not None:
            yield "data.classification", cfg.data.classification, False
    if cfg.universe is not None:
        yield "universe", cfg.universe, False
    for i, source in enumerate(cfg.returns):
        yield f"returns[{i}]", source, True
    for name, model in cfg.risk_models.items():
        for component in sorted(model.components):
            yield (
                f"risk_models.{name}.{component}",
                getattr(model, component),
                (component == "factor_returns"),
            )


def _check_source(
    label: str,
    source: FileSource | DBSource,
    is_period: bool,
    calendar: Calendar,
    plan: EvaluationPlan | None,
) -> list[str]:
    errors = []
    columns = set(calendar.columns)
    match_on = source.match_on or calendar.index
    if match_on not in columns:
        errors.append(f"{label}.match_on: calendar has no column {match_on!r}")

    if isinstance(source, FileSource):
        missing = source.placeholders - columns
        if missing:
            errors.append(
                f"{label}.path: placeholders {sorted(missing)} are not calendar columns "
                f"{calendar.columns}"
            )
        if Path(source.path).suffix.lower() not in SUPPORTED_SUFFIXES:
            errors.append(
                f"{label}.path: unsupported file type; supported: {sorted(SUPPORTED_SUFFIXES)}"
            )
        return errors

    if source.connection not in CONNECTIONS:
        errors.append(
            f"{label}.connection: unknown connection {source.connection!r}; "
            f"available: {sorted(CONNECTIONS)}"
        )
    if not Path(source.query).is_file():
        errors.append(f"{label}.query: file not found: {source.query}")
    if match_on in columns:
        values = calendar.frame[match_on]
        if not values.str.fullmatch(_EIGHT_DIGITS.pattern).fillna(False).all():
            errors.append(
                f"{label}.match_on: DB sources must match on an 8-digit date column; "
                f"{match_on!r} is not"
            )
        elif is_period and plan is not None:
            try:
                check_increasing(values.iloc[plan.ext_pos], match_on)
            except ValueError as e:
                errors.append(f"{label}.match_on: {e}")
    return errors


def _missing_files(
    label: str, source: FileSource, is_period: bool, plan: EvaluationPlan
) -> list[str]:
    if not source.is_partitioned:
        return [] if Path(source.path).is_file() else [f"{label}: file not found: {source.path}"]
    if is_period:
        rows = plan.ext_rows[~plan.ext_rows[plan.calendar.index].isin(plan.optional_keys)]
    else:
        rows = plan.eval_rows
    paths = dict.fromkeys(partition_paths(source, rows, plan.calendar.index).values())
    missing = [str(p) for p in paths if not p.is_file()]
    if not missing:
        return []
    examples = ", ".join(missing[:_MAX_EXAMPLES])
    more = f" (and {len(missing) - _MAX_EXAMPLES} more)" if len(missing) > _MAX_EXAMPLES else ""
    return [f"{label}: {len(missing)} of {len(paths)} files not found: {examples}{more}"]


def validate_config(cfg: EvaluationConfig, *, check_files: bool = True) -> ValidationReport:
    """config をデータソースまで含めて検証する（計算はしない）。"""
    report = ValidationReport()
    if cfg.calendar is None:
        report.errors.append("calendar: required to load data from config")
    if cfg.data is None:
        report.errors.append("data: required to load data from config")

    calendar = plan = None
    if cfg.calendar is not None:
        try:
            calendar = load_calendar(cfg.calendar.path, cfg.calendar.index)
        except Exception as e:
            report.errors.append(f"calendar: {e}")
    if calendar is not None:
        try:
            plan = make_plan(calendar, cfg.period.start, cfg.period.end, cfg.horizons)
        except ValueError as e:
            report.errors.append(f"period: {e}")
        if plan is not None and plan.missing_tail:
            report.warnings.append(
                f"Calendar ends {plan.missing_tail} row(s) short of the longest horizon; "
                "forward returns at the end of the period will be missing"
            )
        for label, source, is_period in _iter_sources(cfg):
            report.errors += _check_source(label, source, is_period, calendar, plan)

    report.errors += validate_metrics(cfg.enabled_metrics, spec_from_config(cfg))

    # パスの解決に問題がなければ、全ファイルの存在を一括でチェックする
    if check_files and plan is not None and not report.errors:
        for label, source, is_period in _iter_sources(cfg):
            if isinstance(source, FileSource):
                report.errors += _missing_files(label, source, is_period, plan)
    return report
