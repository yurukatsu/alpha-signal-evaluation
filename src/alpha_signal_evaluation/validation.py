"""config の意味の検証（2段目）。カレンダーと metric レジストリを読み込んだ後に行う。

1段目（``config.py`` の pydantic モデル）が構造だけを検証するのに対し、ここでは
外部の情報と突き合わせる検証を行う。

- カレンダーを読み込み、評価期間に行があるか、パスのプレースホルダや ``match_on`` が
  カレンダーの列か
- ファイルの拡張子が対応しているか、DB の接続先・SQL ファイル・環境変数が揃っているか
- metric 名がレジストリにあるか、``params`` が metric の ``Params`` に合うか、
  metric が要求するデータ（``requires``）やシグナル名・系列名・ホライズンが config にあるか
- 必要なファイルがすべて存在するか（任意）

計算を始める前に設定ミスをすべて集めて一覧で報告する（最初のエラーで止めない）。
データの中身は読まない（カレンダーを除く）。レイヤー上は api から呼ばれ、
data / io / metrics / pipeline の関数を参照するが、DataBundle は組み立てない。
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .config import (
    BarraReturnsSource,
    BarraRiskModelConfig,
    DBSource,
    EvaluationConfig,
    FileSource,
    MetricEntry,
)
from .data.barra import BARRA_CONNECTIONS
from .data.bundle import DataSpec
from .data.calendar import Calendar, check_increasing, load_calendar
from .data.columns import WEIGHT
from .data.loaders import partition_paths
from .io.db import CONNECTIONS, get_connection_config, missing_settings
from .io.file import SUPPORTED_SUFFIXES
from .metrics.base import Metric
from .metrics.registry import get_metric
from .pipeline.preprocess import EvaluationPlan, make_plan

_DATE_OR_MONTH = re.compile(r"^\d{6}(\d{2})?$")
_MAX_EXAMPLES = 3


class ConfigValidationError(ValueError):
    """config の意味の検証で見つかったエラーをまとめて送出する例外。

    メッセージは ``Config validation failed:`` に続けて各エラーを箇条書きにしたもの。
    ``ValueError`` のサブクラスなので、呼び出し側は ``ValueError`` としても捕捉できる。

    Attributes:
        errors: エラーメッセージのリスト（``ValidationReport.errors`` と同じ形式）。
    """

    def __init__(self, errors: list[str]):
        """エラーメッセージのリストから例外を作る。

        Args:
            errors: エラーメッセージのリスト。
        """
        self.errors = errors
        super().__init__("Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass
class ValidationReport:
    """config の検証結果。エラーと警告をメッセージのリストで保持する。

    エラーは評価を始められない設定ミス、警告は評価はできるが結果に影響しうる事項
    （例: カレンダーが最大ホライズン分に足りず、期末のフォワードリターンが欠損する）。

    Attributes:
        errors: エラーメッセージのリスト。各メッセージは ``"<config 上の位置>: <内容>"`` の形式。
        warnings: 警告メッセージのリスト。
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """エラーが1つもなければ ``True``（警告の有無は問わない）。"""
        return not self.errors

    def raise_for_errors(self) -> None:
        """エラーがあれば ``ConfigValidationError`` を送出する。

        Raises:
            ConfigValidationError: ``errors`` が空でない場合。
        """
        if self.errors:
            raise ConfigValidationError(self.errors)


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def spec_from_config(cfg: EvaluationConfig) -> DataSpec:
    """config で定義されたデータから DataSpec を作る（データは読まない）。

    metric の ``requires`` やパラメータ（シグナル名・系列名・ホライズン・リスクモデル名）を
    計算前に検証するため、DataBundle を組み立てた場合に揃うはずのデータを config だけから
    推定する。``provides`` には次を含める。

    - ``signals``: ``data`` セクションがある場合
    - ``classification``: ``data.classification`` がある場合
    - ``forward_returns``: ``returns`` が1つ以上ある場合
    - ``benchmark_weights``: ``benchmark`` がある場合、または ``universe.columns`` に
      ``weight`` role がある場合
    - ``risk_models`` と ``risk_models.<構成要素>``: ``risk_models`` がある場合

    ``portfolio_weights`` は config では未対応のため含めない。

    Args:
        cfg: 構造の検証済みの config。

    Returns:
        データの中身を持たない ``DataSpec``。``signals`` は ``data.signals`` のシグナル名、
        ``horizons`` は config の horizons（昇順）。
    """
    provides: set[str] = set()
    if cfg.data is not None:
        provides.add("signals")
        if cfg.data.classification is not None:
            provides.add("classification")
    if cfg.returns:
        provides.add("forward_returns")
    if cfg.benchmark is not None or (cfg.universe is not None and WEIGHT in cfg.universe.columns):
        provides.add("benchmark_weights")
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
    """pydantic の ValidationError を ``"<位置>: <メッセージ>"`` の文字列リストに変換する。

    位置（``loc``）は ``.`` で連結し、空ならルートを表す ``(root)`` にする。
    """
    return [
        f"{'.'.join(str(p) for p in err['loc']) or '(root)'}: {err['msg']}" for err in e.errors()
    ]


def validate_metrics(entries: list[MetricEntry], spec: DataSpec) -> list[str]:
    """metric の設定をレジストリと ``DataSpec`` に照らして検証する。

    各エントリについて順に、(1) metric 名がレジストリにあるか、(2) ``params`` が
    metric の ``Params`` モデルに合うか、(3) ``Metric.check()``（要求データの有無、
    シグナル名・系列名・ホライズン・リスクモデル名が存在するか等）を確認する。
    (1) または (2) で失敗したエントリはそれ以降の検査をしない。例外は送出せず、
    すべてのエラーをメッセージとして集める。

    config から読み込む場合は ``spec_from_config()``、DataBundle を直接渡す場合は
    ``DataBundle.spec()`` の結果を ``spec`` に渡す。

    Args:
        entries: 検証する metric のエントリ（通常は ``cfg.enabled_metrics``）。
        spec: 利用できるデータの記述。

    Returns:
        エラーメッセージのリスト。各メッセージは ``metrics[<キー>]`` で始まる
        （params のエラーは ``metrics[<キー>].params.<位置>: ...``）。問題がなければ空。
    """
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
    """metric のエントリからインスタンスを作る。

    ``validate_metrics()`` で検証済みであることを前提とする（未検証のエントリでは
    以下の例外がそのまま送出される）。

    Args:
        entries: metric のエントリ（通常は ``cfg.enabled_metrics``）。

    Returns:
        metric のキー（``id`` または ``name``）-> metric インスタンス。順序は ``entries`` のまま。

    Raises:
        ValueError: 未登録の metric 名がある場合。
        pydantic.ValidationError: ``params`` が metric の ``Params`` に合わない場合。
    """
    return {e.key: get_metric(e.name)(e.params) for e in entries}


# ---------------------------------------------------------------------------
# データソース
# ---------------------------------------------------------------------------


def _iter_sources(
    cfg: EvaluationConfig,
) -> Iterator[tuple[str, FileSource | DBSource, bool]]:
    """(ラベル, ソース, 期間データか) を列挙する。期間データはホライズン分延長して読む。

    対象は ``data.signals`` / ``data.classification`` / ``universe`` / ``benchmark`` /
    ``returns`` / ``risk_models.<名前>.<構成要素>`` の順。ラベルはエラーメッセージに使う
    config 上の位置（例: ``returns[0]``）。``returns`` と ``factor_returns`` が期間データ。
    ``source: barra`` のソースとリスクモデルは含めない（``_iter_barra``）。

    Yields:
        ``(ラベル, ソース, 期間データなら True)`` のタプル。
    """
    if cfg.data is not None:
        for i, source in enumerate(cfg.data.signals):
            yield f"data.signals[{i}]", source, False
        if cfg.data.classification is not None:
            yield "data.classification", cfg.data.classification, False
    if cfg.universe is not None:
        yield "universe", cfg.universe, False
    if cfg.benchmark is not None:
        yield "benchmark", cfg.benchmark, False
    for i, source in enumerate(cfg.returns):
        if not isinstance(source, BarraReturnsSource):
            yield f"returns[{i}]", source, True
    for name, model in cfg.risk_models.items():
        if isinstance(model, BarraRiskModelConfig):
            continue
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
    """1つのデータソースの設定をカレンダーと照らして検証する（ファイルの存在は見ない）。

    共通: ``match_on``（省略時は ``calendar.index``）がカレンダーの列であること。
    ファイル: パスのプレースホルダがカレンダーの列であること、拡張子が対応していること。
    DB: 接続先名が既知であること、SQL ファイルが存在すること、``match_on`` 列の値が
    すべて日付（8桁）または月（6桁）であること。期間データで ``plan`` があれば、
    延長行の範囲で ``match_on`` 列が重複なしの昇順であること（期間の境界に使うため）。

    Args:
        label: エラーメッセージに使う config 上の位置。
        source: 検証するソース。
        is_period: 期間データ（returns / factor_returns）か。
        calendar: 読み込み済みのカレンダー。
        plan: 評価対象の行。期間の解決に失敗した場合は ``None``。

    Returns:
        エラーメッセージのリスト。
    """
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

    try:
        get_connection_config(source.connection)
    except ValueError:
        errors.append(
            f"{label}.connection: unknown connection {source.connection!r}; "
            f"available: {sorted(CONNECTIONS)}"
        )
    if not Path(source.query).is_file():
        errors.append(f"{label}.query: file not found: {source.query}")
    if match_on in columns:
        errors += _check_date_column(label, match_on, is_period, calendar, plan)
    return errors


def _check_date_column(
    label: str,
    match_on: str,
    is_period: bool,
    calendar: Calendar,
    plan: EvaluationPlan | None,
) -> list[str]:
    """DB・Barra のソースの ``match_on`` 列（カレンダーにある列）の値を検証する。

    値がすべて日付（8桁）または月（6桁）であること。期間データで ``plan`` があれば、
    延長行の範囲で重複なしの昇順であること（期間の境界に使うため）。

    Returns:
        エラーメッセージのリスト。
    """
    values = calendar.frame[match_on]
    if not values.str.fullmatch(_DATE_OR_MONTH.pattern).fillna(False).all():
        return [
            f"{label}.match_on: DB sources must match on a date (yyyymmdd) or "
            f"month (yyyymm) column; {match_on!r} is not"
        ]
    if is_period and plan is not None:
        try:
            check_increasing(values.iloc[plan.ext_pos], match_on)
        except ValueError as e:
            return [f"{label}.match_on: {e}"]
    return []


def _iter_barra(cfg: EvaluationConfig) -> Iterator[tuple[str, str, bool]]:
    """``source: barra`` の (ラベル, match_on, 期間データか) を列挙する。

    ``returns`` の要素は期間データ。リスクモデルは ``factor_returns`` を読む場合に期間データ
    （延長行の境界に使う）。

    Yields:
        ``(ラベル, match_on, 期間データなら True)`` のタプル。
    """
    for i, source in enumerate(cfg.returns):
        if isinstance(source, BarraReturnsSource):
            yield f"returns[{i}]", source.match_on, True
    for name, model in cfg.risk_models.items():
        if isinstance(model, BarraRiskModelConfig):
            yield f"risk_models.{name}", model.match_on, "factor_returns" in model.components


def _check_barra(
    label: str,
    match_on: str,
    is_period: bool,
    calendar: Calendar,
    plan: EvaluationPlan | None,
) -> list[str]:
    """Barra のソースの ``match_on`` がカレンダーの日付・月の列であることを検証する。

    Returns:
        エラーメッセージのリスト。
    """
    if match_on not in calendar.columns:
        return [f"{label}.match_on: calendar has no column {match_on!r}"]
    return _check_date_column(label, match_on, is_period, calendar, plan)


def _missing_files(
    label: str, source: FileSource, is_period: bool, plan: EvaluationPlan
) -> list[str]:
    """ファイルソースが読むファイルのうち、存在しないものを報告する。

    単一ファイルならそのファイルの存在だけを見る。パーティションされている場合は、
    期間データは延長行のうち評価期間外の行（``plan.optional_keys``、存在しなければ
    読み込み時に欠損扱いになる）を除いた行、それ以外は評価対象の行について、
    カレンダーの値を埋め込んだパスを重複なしで確認する。

    Returns:
        見つからないファイルがあれば、件数と最大 ``_MAX_EXAMPLES`` 件の例を含む
        メッセージ1件のリスト。すべて存在すれば空リスト。
    """
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


def _check_connections(cfg: EvaluationConfig) -> list[str]:
    """DB ソースが使う接続先ごとに、ホスト名・認証情報が環境変数から読めるかを確認する。

    接続先は重複を除いて1回ずつ確認する（実際の接続はしない）。未知の接続先名は
    ``_check_source`` が報告するため、ここでは飛ばす。``source: barra`` があれば
    ``data.barra.BARRA_CONNECTIONS`` の接続先も確認する。

    Returns:
        ``host`` / ``username`` / ``password`` のいずれかが未設定の接続先ごとのエラーメッセージ。
    """
    errors = []
    used = dict.fromkeys(s.connection for _, s, _ in _iter_sources(cfg) if isinstance(s, DBSource))
    if any(True for _ in _iter_barra(cfg)):
        used |= dict.fromkeys(BARRA_CONNECTIONS)
    for connection in used:
        try:
            config = get_connection_config(connection)
        except ValueError:
            continue  # 接続先名の誤りは _check_source で報告する
        missing = missing_settings(config)
        if missing:
            errors.append(
                f"connection {connection!r} ({type(config).__name__}): {', '.join(missing)} "
                "not set; set the environment variables (see .env.example)"
            )
    return errors


def validate_config(cfg: EvaluationConfig, *, check_files: bool = True) -> ValidationReport:
    """config をデータソースまで含めて検証する（計算はしない）。

    構造の検証（pydantic）を通った config に対し、次の順で意味の検証を行い、
    すべてのエラー・警告を集めて返す（最初のエラーで止めない）。

    1. config からデータを読むのに必要な ``calendar`` と ``data`` があるか
    2. カレンダーを読み込み、``period`` に該当する行があるか（ホライズン分の行が
       カレンダーの末尾に足りなければ警告）
    3. 各データソースの設定（プレースホルダ・``match_on``・拡張子・DB の接続先と SQL、
       Barra の ``match_on``）
    4. DB の接続先ごとのホスト名・認証情報の環境変数
    5. 有効な metric の名前・params・要求データ（``spec_from_config()`` と照合）
    6. ``check_files=True`` で、ここまでエラーがなければ、必要なファイルがすべて存在するか

    カレンダーを読めなかった場合は 2・3 を、期間の解決に失敗した場合は 6 を行わない。

    Args:
        cfg: 構造の検証済みの config。
        check_files: ファイルの存在まで確認するか。ファイル数が多い場合や、別の環境で
            設定だけ確認したい場合は ``False`` にする。

    Returns:
        ``ValidationReport``。``report.ok`` が ``False`` なら評価を始められない。
        エラーで例外を送出したい場合は ``report.raise_for_errors()`` を呼ぶ。
    """
    report = ValidationReport()
    if cfg.calendar is None:
        report.errors.append("calendar: required to load data from config")
    if cfg.data is None:
        report.errors.append("data: required to load data from config")

    calendar = plan = None
    if cfg.calendar is not None:
        try:
            calendar = load_calendar(cfg.calendar.path, cfg.calendar.index, cfg.calendar.columns)
        except Exception as e:
            report.errors.append(f"calendar: {e}")
    if calendar is not None:
        try:
            plan = make_plan(calendar, cfg.period.start, cfg.period.end, cfg.horizons)
        except ValueError as e:
            report.errors.append(f"period: {e}")
        if plan is not None and plan.tail_warning:
            report.warnings.append(plan.tail_warning)
        for label, source, is_period in _iter_sources(cfg):
            report.errors += _check_source(label, source, is_period, calendar, plan)
        for label, match_on, is_period in _iter_barra(cfg):
            report.errors += _check_barra(label, match_on, is_period, calendar, plan)

    report.errors += _check_connections(cfg)
    report.errors += validate_metrics(cfg.enabled_metrics, spec_from_config(cfg))

    # パスの解決に問題がなければ、全ファイルの存在を一括でチェックする
    if check_files and plan is not None and not report.errors:
        for label, source, is_period in _iter_sources(cfg):
            if isinstance(source, FileSource):
                report.errors += _missing_files(label, source, is_period, plan)
    return report
