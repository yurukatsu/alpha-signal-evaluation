"""Python API の本体。CLI はこれを呼ぶだけ。"""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import EvaluationConfig, load_config
from .data.bundle import DataBundle
from .pipeline.preprocess import build_bundle
from .pipeline.runner import run_metrics
from .report import EvaluationReport
from .validation import (
    ConfigValidationError,
    ValidationReport,
    instantiate_metrics,
    validate_config,
    validate_metrics,
)

logger = logging.getLogger(__name__)


def _as_config(
    config: EvaluationConfig | Mapping[str, Any] | str | Path,
) -> EvaluationConfig:
    if isinstance(config, EvaluationConfig):
        return config
    if isinstance(config, Mapping):
        return EvaluationConfig.model_validate(config)
    return load_config(config)


def validate(
    config: EvaluationConfig | Mapping[str, Any] | str | Path,
    *,
    check_files: bool = True,
) -> ValidationReport:
    """計算せずに config を検証する（構造・意味・ファイルの存在）。"""
    return validate_config(_as_config(config), check_files=check_files)


def evaluate(
    config: EvaluationConfig | Mapping[str, Any] | str | Path,
    *,
    data: DataBundle | None = None,
    n_jobs: int | None = None,
) -> EvaluationReport:
    """config に従って metrics を計算する。

    Args:
        config: config.yaml のパス、dict、または EvaluationConfig。
        data: 渡された場合は io / loaders をバイパスしてこのデータで評価する
            （config の calendar / data / returns は不要）。
            手元の DataFrame からは ``make_bundle()`` で作れる。
        n_jobs: 並列数。省略時は config の execution.n_jobs。

    Returns:
        EvaluationReport。失敗した metric があっても他の結果は返す（``report.errors``）。
        ファイルへの書き出しは ``report.save()``。
    """
    cfg = _as_config(config)
    warnings: list[str] = []
    if data is None:
        validation = validate_config(cfg)
        validation.raise_for_errors()
        prepared = build_bundle(cfg)
        data = prepared.bundle
        warnings = list(dict.fromkeys(validation.warnings + prepared.warnings))
    else:
        errors = validate_metrics(cfg.enabled_metrics, data.spec())
        if errors:
            raise ConfigValidationError(errors)

    metrics = instantiate_metrics(cfg.enabled_metrics)
    runs = run_metrics(metrics, data, n_jobs or cfg.execution.n_jobs)
    return EvaluationReport(runs=runs, config=cfg, warnings=warnings)
