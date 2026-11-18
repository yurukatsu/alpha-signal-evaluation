"""Python API の本体。CLI はこれを呼ぶだけ。

評価の入口となる ``evaluate()`` と、計算せずに config を検証する ``validate()`` を提供する。
レイヤー上は cli の直下にあり、config の読み込み（``config``）→ 意味の検証
（``validation``）→ データの準備（``pipeline.preprocess.build_bundle``）→ metric の実行
（``pipeline.runner.run_metrics``）→ ``EvaluationReport`` の組み立て、という流れを束ねる。
時点合わせなどのデータ処理はここでは行わない。``evaluate(save=...)`` のときは、完了した
metric から順にファイルへ書き出す（``report.IncrementalWriter``）。
"""

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import EvaluationConfig, load_config
from .data.bundle import DataBundle
from .pipeline.preprocess import build_bundle
from .pipeline.runner import run_metrics
from .report import EvaluationReport, IncrementalWriter
from .validation import (
    ConfigValidationError,
    ValidationReport,
    instantiate_metrics,
    validate_by_date,
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
    save: bool | str | Path = False,
) -> EvaluationReport:
    """config に従って metrics を計算する。

    ``data`` を省略した場合は、``validate_config()`` で config をデータソースまで含めて
    検証し（エラーがあれば例外）、``build_bundle()`` で io / loaders 経由でデータを読み込み
    時点合わせ済みの DataBundle を組み立ててから、有効な metric を実行する。
    検証とデータ準備の警告は重複を除いて ``report.warnings`` に入る。

    ``data`` を渡した場合は io / loaders をバイパスし、metric の設定（と ``output.by_date``
    の日付）だけを ``data`` と照らして検証する（config の ``calendar`` / ``data`` /
    ``returns`` / ``period`` 等は使わない）。この場合 ``report.warnings`` は空。

    metric の計算中の例外は送出せず、その metric を失敗として記録して他の metric の
    結果を返す。

    ``save`` を指定すると、完了した metric から順に config の ``output`` 設定（形式・
    ``by_date``）で書き出す。最後まで走れば、出力は ``report.save()`` と同じになる。途中で
    プロセスが落ちても（強制終了・Ctrl-C・データ読み込み中の例外）、それまでに終わった
    metric の parquet / csv と、どこまで終わったかを記録した run_info.json
    （``state`` / ``pending``）が残る。xlsx は最後（例外や Ctrl-C で中断した場合はその時点）に
    まとめて書く。並列実行中に Ctrl-C で中断した場合、実行中だった metric の結果は残らない。
    詳細は ``report.incremental`` を参照。config の検証でエラーになった場合は何も書かない。

    出力先に以前の実行の結果がある場合、run_info.json は開始時に、summary は metric が
    終わるたびに今回の内容で置き換わる（中断した場合は report.xlsx も途中までの内容になる）。
    metric の表は、書く前にその metric の以前の表を消すので、今回失敗した metric の前回の
    結果は残らない。まだ終わっていない metric のディレクトリには以前の実行のファイルが
    残るので、以前の結果を残したい場合は出力先を分ける。

    Args:
        config: config.yaml のパス、dict、または EvaluationConfig。dict の場合、
            config 内の相対パスは実行ディレクトリ基準になる。
        data: 渡された場合は io / loaders をバイパスしてこのデータで評価する
            （config の calendar / data / returns は不要）。
            手元の DataFrame からは ``make_bundle()`` で作れる。
        n_jobs: 並列数（プロセス数）。省略時（または 0）は config の execution.n_jobs。
        save: ``True`` なら config の ``output.dir`` に、パスならそのディレクトリに、
            完了した metric から順に書き出す。``False``（既定）なら何も書かない。

    Returns:
        EvaluationReport。失敗した metric があっても他の結果は返す（``report.errors``）。
        ``save`` を指定しなかった場合、ファイルへの書き出しは ``report.save()``。

    Raises:
        ConfigValidationError: config の意味の検証（``data`` を渡した場合は metric と
            ``output.by_date`` の検証のみ）でエラーがあった場合。
        pydantic.ValidationError: config の構造の検証に失敗した場合。
        FileNotFoundError: config ファイルや、評価期間内のデータファイルが見つからない場合
            （データ読み込み中のその他の例外もそのまま送出される）。

    Examples:
        >>> report = evaluate("config.yaml", n_jobs=4)  # doctest: +SKIP
        >>> report["ic"].tables["summary"]  # doctest: +SKIP
        >>> report.save()  # doctest: +SKIP
        >>> evaluate("config.yaml", save=True)  # doctest: +SKIP
    """
    cfg = _as_config(config)
    warnings: list[str] = []
    if data is None:
        validation = validate_config(cfg)
        validation.raise_for_errors()
        warnings += validation.warnings
    else:
        errors = validate_metrics(cfg.enabled_metrics, data.spec())
        errors += validate_by_date(cfg, data.dates)
        if errors:
            raise ConfigValidationError(errors)

    metrics = instantiate_metrics(cfg.enabled_metrics)
    writer = None
    if save:
        directory = cfg.output.dir if save is True else save
        writer = IncrementalWriter(
            directory, cfg.output.formats, cfg.output.by_date, metrics, cfg, warnings
        )
    try:
        if writer is not None:
            writer.start()
        if data is None:
            prepared = build_bundle(cfg)
            data = prepared.bundle
            # writer が同じリストを参照しているので、作り直さずに中身を置き換える
            warnings[:] = dict.fromkeys(warnings + prepared.warnings)
        runs = run_metrics(
            metrics,
            data,
            n_jobs or cfg.execution.n_jobs,
            on_result=writer.add if writer is not None else None,
        )
        report = EvaluationReport(runs=runs, config=cfg, warnings=warnings)
        if writer is not None:
            writer.finish(report)
    except BaseException:
        # 途中で落ちても（最後の書き出し中も含む）、そこまでに終わった metric の結果は残す
        if writer is not None:
            writer.abort()
        raise
    return report
