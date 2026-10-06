"""Python API の本体。CLI はこれを呼ぶだけ。

評価の入口となる ``evaluate()`` と、計算せずに config を検証する ``validate()`` を提供する。
レイヤー上は cli の直下にあり、config の読み込み（``config``）→ 意味の検証
（``validation``）→ データの準備（``pipeline.preprocess.build_bundle``）→ metric の実行
（``pipeline.runner.run_metrics``）→ ``EvaluationReport`` の組み立て、という流れを束ねる。
時点合わせなどのデータ処理はここでは行わない。
"""

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
    """さまざまな形式の config を ``EvaluationConfig`` にそろえる。

    ``EvaluationConfig`` はそのまま返し、dict 等の mapping は ``model_validate`` で、
    パスは ``load_config()`` で読み込む。mapping の場合は基準ディレクトリがないため、
    config 内の相対パスは解決されず、読み込み時の実行ディレクトリ基準になる。

    Raises:
        pydantic.ValidationError: 構造の検証に失敗した場合。
        FileNotFoundError: パスのファイルが存在しない場合。
        ValueError: YAML のトップレベルが mapping でない場合。
    """
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
    """計算せずに config を検証する（構造・意味・ファイルの存在）。

    config を ``EvaluationConfig`` にそろえたうえで ``validation.validate_config()`` を呼ぶ。
    意味の検証のエラーは例外にせず ``ValidationReport`` に集めて返すが、構造の検証
    （pydantic）に失敗した場合は config を作れないため例外になる。

    Args:
        config: config.yaml のパス、dict、または ``EvaluationConfig``。dict の場合、
            config 内の相対パスは実行ディレクトリ基準になる。
        check_files: データファイルの存在まで確認するか（既定 ``True``）。

    Returns:
        エラーと警告を持つ ``ValidationReport``。``report.ok`` で可否を判定する。

    Raises:
        pydantic.ValidationError: 構造の検証に失敗した場合。
        FileNotFoundError: config ファイルが存在しない場合。
        ValueError: YAML のトップレベルが mapping でない場合。

    Examples:
        >>> report = validate("config.yaml", check_files=False)  # doctest: +SKIP
        >>> report.ok, report.errors  # doctest: +SKIP
    """
    return validate_config(_as_config(config), check_files=check_files)


def evaluate(
    config: EvaluationConfig | Mapping[str, Any] | str | Path,
    *,
    data: DataBundle | None = None,
    n_jobs: int | None = None,
) -> EvaluationReport:
    """config に従って metrics を計算する。

    ``data`` を省略した場合は、``validate_config()`` で config をデータソースまで含めて
    検証し（エラーがあれば例外）、``build_bundle()`` で io / loaders 経由でデータを読み込み
    時点合わせ済みの DataBundle を組み立ててから、有効な metric を実行する。
    検証とデータ準備の警告は重複を除いて ``report.warnings`` に入る。

    ``data`` を渡した場合は io / loaders をバイパスし、metric の設定だけを
    ``data.spec()`` と照らして検証する（config の ``calendar`` / ``data`` / ``returns`` /
    ``period`` 等は使わない）。この場合 ``report.warnings`` は空。

    metric の計算中の例外は送出せず、その metric を失敗として記録して他の metric の
    結果を返す。

    Args:
        config: config.yaml のパス、dict、または EvaluationConfig。dict の場合、
            config 内の相対パスは実行ディレクトリ基準になる。
        data: 渡された場合は io / loaders をバイパスしてこのデータで評価する
            （config の calendar / data / returns は不要）。
            手元の DataFrame からは ``make_bundle()`` で作れる。
        n_jobs: 並列数（プロセス数）。省略時（または 0）は config の execution.n_jobs。

    Returns:
        EvaluationReport。失敗した metric があっても他の結果は返す（``report.errors``）。
        ファイルへの書き出しは ``report.save()``。

    Raises:
        ConfigValidationError: config の意味の検証（``data`` を渡した場合は metric の
            検証のみ）でエラーがあった場合。
        pydantic.ValidationError: config の構造の検証に失敗した場合。
        FileNotFoundError: config ファイルや、評価期間内のデータファイルが見つからない場合
            （データ読み込み中のその他の例外もそのまま送出される）。

    Examples:
        >>> report = evaluate("config.yaml", n_jobs=4)  # doctest: +SKIP
        >>> report["ic"].tables["summary"]  # doctest: +SKIP
        >>> report.save()  # doctest: +SKIP
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
