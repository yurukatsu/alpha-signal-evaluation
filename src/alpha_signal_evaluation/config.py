"""config.yaml のスキーマ定義と読み込み（1段目＝構造の検証）。

このモジュールは config.yaml の構造を pydantic モデルとして定義し、型・必須項目・未知のキー・
値の範囲・config 内の相互参照（metric のキーや return 系列名の重複など）といった、
外部のファイルや実装に依存しない検証だけを行う。

検証は2段階に分かれている。

1. 構造の検証（このモジュール）: YAML をモデルに読み込む時点で pydantic が行う。
   未知のキーは ``extra="forbid"`` によりエラーになる（typo を計算前に検出するため）。
2. 意味の検証（``validation.py``）: カレンダーファイルや metric レジストリを読み込んだ後に、
   プレースホルダがカレンダーの列か、metric が要求するデータが揃っているか、
   ファイルが存在するか等を確認する。

レイヤー上は cli → api → validation / pipeline → data → io の各層から参照される
「設定の型」であり、このモジュール自身はデータを読まない。
相対パス（``PathLike``）は ``load_config()`` が渡す ``base_dir`` を基準に解決されるため、
実行ディレクトリではなく config ファイルの場所が基準になる。
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
    Discriminator,
    Field,
    Tag,
    ValidationInfo,
    field_validator,
    model_validator,
)

ReturnKind: TypeAlias = Literal["total", "specific"]
Convention: TypeAlias = Literal["realized", "forward"]
OutputFormat: TypeAlias = Literal["parquet", "xlsx", "csv"]


def _resolve_path(value: str, info: ValidationInfo) -> str:
    """相対パスを config ファイルの場所を基準に解決する（実行ディレクトリ基準ではない）。

    ``~`` は常に展開する。検証コンテキストに ``base_dir`` がない場合（``load_config()`` を
    経由せず dict から ``model_validate`` した場合など）や絶対パスの場合は、そのまま返す。
    その場合の相対パスは、実際に読み込む時点の実行ディレクトリ基準になる。

    Args:
        value: config に書かれたパス文字列。``{Base_day}`` のようなプレースホルダを含んでよい。
        info: pydantic の検証情報。``info.context["base_dir"]`` を基準ディレクトリとして使う。

    Returns:
        解決後のパス文字列。
    """
    base_dir = (info.context or {}).get("base_dir")
    path = Path(value).expanduser()
    if base_dir is None or path.is_absolute():
        return str(path)
    return str(Path(base_dir) / path)


def _to_str(value: Any) -> Any:
    """int を文字列に変換する（日付キー用の前処理）。

    YAML でクォートせずに ``20100131`` と書くと int として読まれるため、
    文字列パターンの検証の前に str へ戻す。int 以外はそのまま返す。
    """
    # YAML では 20100131 のようにクォートなしで書くと int になる
    return str(value) if isinstance(value, int) else value


def _to_list(value: Any) -> Any:
    """単一のソース（dict）をリストに包む。

    ``data.signals`` を1ソースだけならリストにせず書けるようにするための前処理。
    dict 以外（リスト等）はそのまま返す。
    """
    return [value] if isinstance(value, dict) else value


PathLike = Annotated[str, AfterValidator(_resolve_path)]
DateKey = Annotated[str, BeforeValidator(_to_str), Field(pattern=r"^\d{6}(\d{2})?$")]


def template_fields(template: str) -> set[str]:
    """パステンプレート中のプレースホルダ名を返す（例: ``{Base_day}`` → ``Base_day``）。

    ``string.Formatter`` の構文で解析するため、``{Base_day:...}`` のような書式指定が
    付いていてもフィールド名だけを返す。プレースホルダがなければ空集合を返す。
    ファイルソースが日付パーティションされているか（``FileSource.is_partitioned``）の判定や、
    プレースホルダがカレンダーの列名かどうかの検証に使う。

    Args:
        template: ``./input/{Label2}.csv`` のようなパステンプレート。

    Returns:
        プレースホルダ名の集合。

    Examples:
        >>> sorted(template_fields("./alpha/{Rskmdl_month}/{Base_day}.csv"))
        ['Base_day', 'Rskmdl_month']
    """
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


class _Base(BaseModel):
    """config モデル共通の基底クラス。

    ``extra="forbid"`` で未知のキーをエラーにし（typo を計算前に検出する）、
    ``frozen=True`` で読み込み後の変更を禁止する。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------------------
# カレンダー・期間
# ---------------------------------------------------------------------------


class CalendarConfig(_Base):
    """``calendar`` セクション。評価の時間軸の唯一の定義となるカレンダーファイル。

    カレンダーは空白区切りのテキストで、1行 = 1評価期間。値は文字列のまま扱う
    （8桁 = 日付 yyyymmdd、6桁 = 月 yyyymm）。読み込みは ``data.calendar.load_calendar()``。

    Attributes:
        path: カレンダーファイルのパス（YAML ``calendar.path``、必須）。
            相対パスは config ファイルの場所を基準に解決する。
        index: 内部で全データに付与する日付キーの列名（YAML ``calendar.index``、
            既定 ``"Base_day"``）。値は重複なしの昇順である必要がある。
        columns: ヘッダー行のないカレンダーの列名（YAML ``calendar.columns``、既定 ``None``）。
            指定するとファイルのヘッダーより優先される（例: 月だけのファイルなら ``[Month]``）。
    """

    path: PathLike
    index: str = "Base_day"
    # ヘッダー行のないカレンダーの列名（例: 月だけのファイルなら [Month]）
    columns: list[str] | None = None


class PeriodConfig(_Base):
    """``period`` セクション。評価期間を ``calendar.index`` の値で絞る。

    ``start <= calendar.index <= end`` を満たすカレンダー行が評価対象になる
    （比較は文字列として行う）。両方省略するとカレンダーの全行が対象。

    Attributes:
        start: 評価期間の開始（YAML ``period.start``、既定 ``None``）。6桁（yyyymm）
            または8桁（yyyymmdd）の数字。YAML でクォートせず int として書いても文字列に変換する。
        end: 評価期間の終了（YAML ``period.end``、既定 ``None``）。書式は ``start`` と同じ。

    Examples:
        >>> PeriodConfig(start=20190329, end="20260731").start
        '20190329'
    """

    start: DateKey | None = None
    end: DateKey | None = None

    @model_validator(mode="after")
    def _check_order(self) -> "PeriodConfig":
        """``start`` が ``end`` より後になっていないかを確認する。

        Raises:
            ValueError: 両方指定されていて ``start > end``（文字列比較）の場合。
        """
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError(f"period.start ({self.start}) is after period.end ({self.end})")
        return self


# ---------------------------------------------------------------------------
# データソース（source: file | db の discriminated union）
# ---------------------------------------------------------------------------


class FileSource(_Base):
    """日付パーティションされたファイル、またはプレースホルダなしの単一ファイル。

    ``path`` に ``{列名}`` 形式のプレースホルダがあれば、カレンダーの各行の値を埋め込んだ
    ファイルを行ごとに読む（パーティション）。どの列でファイルを選んでも、データには
    ``calendar.index`` の値が付与される。プレースホルダがなければ1ファイルとして読み、
    ファイル内の ``date`` 列を ``match_on`` の列と突き合わせて calendar.index を付与する。
    読み込みは ``data.loaders.load_snapshot()`` が行う。

    Attributes:
        source: ソース種別の判別子。常に ``"file"``（YAML ``source: file``）。
        path: ファイルパスまたはパステンプレート（YAML ``path``、必須）。相対パスは
            config ファイルの場所を基準に解決する。拡張子でリーダーを選ぶ
            （``.csv`` / ``.dat`` / ``.tsv`` / ``.parquet`` / ``.pkl``）。
        read_options: リーダー（``pandas.read_csv`` 等）にそのまま渡す引数
            （YAML ``read_options``、既定 ``{}``）。
        columns: ``{role: 実カラム名}`` の対応（YAML ``columns``、既定 ``{}``）。role は
            ``asset_id`` / ``date`` / ``category`` / ``weight`` / ``factor`` / ``value``。
            読み込み時にこの対応で列名を role 名へリネームする。用途ごとに必要な role
            （例: シグナルなら ``asset_id``、単一ファイルなら ``date`` も）は、実カラム名が
            role 名と同じでも明示する必要がある。
        match_on: 単一ファイルのとき、ファイル内の ``date`` 列と突き合わせるカレンダーの列
            （YAML ``match_on``、既定 ``None`` = ``calendar.index``）。
    """

    source: Literal["file"]
    path: PathLike
    read_options: dict[str, Any] = Field(default_factory=dict)
    columns: dict[str, str] = Field(default_factory=dict)
    # 単一ファイルのとき、ファイル内の date 列をどのカレンダー列と突き合わせるか
    match_on: str | None = None

    @property
    def placeholders(self) -> set[str]:
        """``path`` に含まれるプレースホルダ名（カレンダーの列名であるべきもの）の集合。"""
        return template_fields(self.path)

    @property
    def is_partitioned(self) -> bool:
        """``path`` にプレースホルダがあり、カレンダーの行ごとにファイルが分かれているか。"""
        return bool(self.placeholders)


class DBSource(_Base):
    """期間分を1クエリで取得する DB ソース。SQL は ``:start`` / ``:end`` を参照する。

    パイプラインは ``start < date <= end`` の範囲（8桁の日付文字列）を ``:start`` /
    ``:end`` として SQL に渡し、取得した日次データの ``date`` 列を ``match_on`` の列と
    突き合わせてカレンダー行に対応付ける。クエリ結果は ``cache`` 設定に従って
    ローカルにキャッシュされる。

    Attributes:
        source: ソース種別の判別子。常に ``"db"``（YAML ``source: db``）。
        connection: 接続先 DB 名（YAML ``connection``、必須）。``io.db.CONNECTIONS`` の
            キー（小文字の DB 名、例: ``risk_models``）または Config クラス名。
            ホスト名・認証情報は環境変数から読む。
        query: SQL ファイルのパス（YAML ``query``、必須）。相対パスは config ファイルの
            場所を基準に解決する。
        columns: ``{role: 実カラム名}`` の対応（YAML ``columns``、既定 ``{}``）。
            ``date`` role は常に必要で、用途ごとに必要な role（例: ``asset_id``）とともに
            実カラム名が role 名と同じでも明示する必要がある。
        match_on: DB の日付と突き合わせるカレンダーの列（YAML ``match_on``、既定 ``None`` =
            ``calendar.index``）。列の値は日付（yyyymmdd）または月（yyyymm）である必要がある。
    """

    source: Literal["db"]
    connection: str
    query: PathLike
    columns: dict[str, str] = Field(default_factory=dict)
    match_on: str | None = None


Source = Annotated[FileSource | DBSource, Field(discriminator="source")]


def _check_values(v: list[str] | dict[str, str]) -> list[str] | dict[str, str]:
    """シグナルの ``values`` が空でないことを確認する。

    Raises:
        ValueError: 空のリストまたは空の dict の場合。
    """
    if not v:
        raise ValueError("values must not be empty")
    return v


# シグナル名のリスト、または {シグナル名: 実カラム名}（ソース間で列名が同じ場合に名前を付け替える）
SignalValues = Annotated[list[str] | dict[str, str], AfterValidator(_check_values)]


class _SignalsMixin:
    """シグナルソースに ``values`` からシグナル名と実カラム名の対応を引く機能を足す mixin。"""

    @property
    def value_map(self) -> dict[str, str]:
        """シグナル名 -> 実カラム名。

        ``values`` がリストなら各要素をシグナル名かつ列名とみなし ``{v: v}`` を返す。
        dict ならそのコピーを返す（ソース間で列名が重なる場合に名前を付け替えるため）。
        """
        if isinstance(self.values, dict):
            return dict(self.values)
        return {v: v for v in self.values}


class SignalsFileSource(_SignalsMixin, FileSource):
    """ファイルから読むシグナルソース（``data.signals`` の要素、``source: file``）。

    ``FileSource`` の項目に加えて ``values`` を持つ。``asset_id`` role の列が必要。

    Attributes:
        values: 読み込むシグナル列（YAML ``values``、必須・空不可）。シグナル名のリスト、
            または ``{シグナル名: 実カラム名}``。
    """

    values: SignalValues


class SignalsDBSource(_SignalsMixin, DBSource):
    """DB から読むシグナルソース（``data.signals`` の要素、``source: db``）。

    ``DBSource`` の項目に加えて ``values`` を持つ。``asset_id`` と ``date`` role の列が必要。

    Attributes:
        values: 読み込むシグナル列（YAML ``values``、必須・空不可）。シグナル名のリスト、
            または ``{シグナル名: 実カラム名}``。
    """

    values: SignalValues


SignalsSource = Annotated[SignalsFileSource | SignalsDBSource, Field(discriminator="source")]


class ReturnSeries(_Base):
    """リターンソース内の1系列の定義（``returns[].series.<系列名>``）。

    Attributes:
        column: ソース内の実カラム名（YAML ``column``、必須）。
        kind: リターンの種類 ``total`` / ``specific``（YAML ``kind``、必須）。
            期間内の集約方法は kind から固定で決まる（total は複利、specific は加算）。
    """

    column: str
    kind: ReturnKind


class ReturnsFileSource(FileSource):
    """ファイルから読むリターンソース（``returns`` の要素、``source: file``）。

    カレンダーの行ごとに1ファイル（または単一ファイル）で、値はすでに期間リターンとみなし、
    日次からの集約はしない。``asset_id`` role の列が必要。

    Attributes:
        convention: 期間リターンの時点の規約（YAML ``convention``、既定 ``"realized"``）。
            ``realized`` は行 t のリターン = 行 t-1 → t の実現リターン、``forward`` は
            行 t → t+1 のリターン。
        series: ``{系列名: ReturnSeries}``（YAML ``series``、必須・1件以上）。
            系列名は全リターンソースで一意である必要がある。
    """

    convention: Convention = "realized"
    series: dict[str, ReturnSeries] = Field(min_length=1)


class ReturnsDBSource(DBSource):
    """DB から読むリターンソース（``returns`` の要素、``source: db``）。

    期間全体の日次リターンを1クエリで取得し、``match_on`` の列を境界として
    カレンダー期間に集約する（total は複利、specific は加算）。

    Attributes:
        convention: 期間リターンの時点の規約（YAML ``convention``、既定 ``"realized"``）。
        series: ``{系列名: ReturnSeries}``（YAML ``series``、必須・1件以上）。
        match_on: 期間の境界となるカレンダーの列（YAML ``match_on``、必須）。日付列なら
            ``(前行の日付, 当該行の日付]``（realized）を、月列ならその月を1期間とする。
    """

    convention: Convention = "realized"
    series: dict[str, ReturnSeries] = Field(min_length=1)
    match_on: str  # DB は日次で取得してカレンダー期間に集約するため必須


BarraModel: TypeAlias = Literal["JPE4", "GEMLT", "GEM3"]
BarraComponent: TypeAlias = Literal["exposures", "factor_covariance", "factor_returns"]
Currency: TypeAlias = Literal["local", "USD"]


def _barra_series(value: Any) -> Any:
    """Barra の ``series`` の ``{系列名: kind}`` を ``{系列名: ReturnSeries}`` の形に展開する。

    値が文字列（``total`` / ``specific``）なら ``{"column": v, "kind": v}`` にする
    （Barra ソースの読み込み結果は kind と同じ名前の列を持つ）。それ以外はそのまま返し、
    pydantic の型検証に任せる。
    """
    if isinstance(value, dict):
        return {k: {"column": v, "kind": v} if isinstance(v, str) else v for k, v in value.items()}
    return value


def _default_barra_series() -> dict[str, ReturnSeries]:
    """Barra の ``series`` の既定値（``total`` と ``specific`` の両方）。"""
    return {k: ReturnSeries(column=k, kind=k) for k in ("total", "specific")}


class BarraReturnsSource(_Base):
    """Barra のトータル・スペシフィックリターン（``returns`` の要素、``source: barra``）。

    社内 DB（接続先 ``risk_models``）から日次の DRTN（トータル）・SRTN（スペシフィック）を
    取得し、``match_on`` の列を境界としてカレンダー期間に集約する（total は複利、
    specific は加算）。単位の換算（% → 小数）と銘柄 ID の変換（BID → nri_code）は
    読み込み時に行う。詳細は ``data/barra.py``。

    Attributes:
        source: ソース種別の判別子。常に ``"barra"``（YAML ``source: barra``）。
        model: Barra のモデル（YAML ``model``、必須）。``JPE4`` / ``GEMLT`` / ``GEM3``。
        match_on: 期間の境界となるカレンダーの列（YAML ``match_on``、必須）。
            日付（yyyymmdd）または月（yyyymm）の列。
        convention: 期間リターンの時点の規約（YAML ``convention``、既定 ``"realized"``）。
        currency: トータルリターンの通貨（YAML ``currency``、既定 ``"local"``）。
            ``local`` は取引通貨建て（DB の値のまま）、``USD`` は日次で為替を掛けて
            USD 建てにする。スペシフィックリターンは通貨によらずそのまま。
        series: ``{系列名: kind}``（YAML ``series``、既定 ``{total: total, specific: specific}``）。
            kind は ``total``（DRTN）または ``specific``（SRTN）。系列名は全リターンソースで
            一意である必要がある。
    """

    source: Literal["barra"]
    model: BarraModel
    match_on: str
    convention: Convention = "realized"
    currency: Currency = "local"
    series: Annotated[dict[str, ReturnSeries], BeforeValidator(_barra_series)] = Field(
        default_factory=_default_barra_series, min_length=1
    )

    @model_validator(mode="after")
    def _column_is_kind(self) -> "BarraReturnsSource":
        """各系列の ``column`` が ``kind`` と同じであることを確認する（列名は選べない）。

        Raises:
            ValueError: ``{column: ..., kind: ...}`` の形で異なる列名を指定した場合。
        """
        bad = sorted(name for name, s in self.series.items() if s.column != s.kind)
        if bad:
            raise ValueError(
                f"series {bad}: write the kind only (e.g. `total: total`); "
                "Barra sources have no column names to choose"
            )
        return self


ReturnsSource = Annotated[
    ReturnsFileSource | ReturnsDBSource | BarraReturnsSource, Field(discriminator="source")
]


class FactorReturnsFileSource(FileSource):
    """ファイルから読むファクターリターン（``risk_models.<名前>.factor_returns``）。

    long 形式（``factor`` / ``value`` role の列）で、値はすでに期間リターンとみなす。

    Attributes:
        convention: 時点の規約（YAML ``convention``、既定 ``"realized"``）。
    """

    convention: Convention = "realized"


class FactorReturnsDBSource(DBSource):
    """DB から読むファクターリターン（``risk_models.<名前>.factor_returns``）。

    long 形式（``date`` / ``factor`` / ``value`` role の列）の日次データを取得し、
    カレンダー期間に加算で集約する。

    Attributes:
        convention: 時点の規約（YAML ``convention``、既定 ``"realized"``）。
        match_on: 期間の境界となるカレンダーの列（YAML ``match_on``、必須）。
    """

    convention: Convention = "realized"
    match_on: str


FactorReturnsSource = Annotated[
    FactorReturnsFileSource | FactorReturnsDBSource, Field(discriminator="source")
]


class ClassificationFileSource(FileSource):
    """ファイルから読む分類（業種等）（``data.classification``、``source: file``）。

    Attributes:
        one_hot: ``True`` なら ``category`` 列の代わりに、``asset_id`` 以外の列を 0/1 の
            ダミー変数として扱い、1 が立っている列名をカテゴリにする（YAML ``one_hot``、
            既定 ``False``）。``False`` のときは ``category`` role の列が必要。
    """

    # True なら category 列の代わりに、asset_id 以外の列を 0/1 のダミー変数として扱い、
    # 1 が立っている列名をカテゴリにする（例: L001〜L010）
    one_hot: bool = False


class ClassificationDBSource(DBSource):
    """DB から読む分類（業種等）（``data.classification``、``source: db``）。

    Attributes:
        one_hot: ``ClassificationFileSource.one_hot`` と同じ（YAML ``one_hot``、既定 ``False``）。
    """

    one_hot: bool = False


ClassificationSource = Annotated[
    ClassificationFileSource | ClassificationDBSource, Field(discriminator="source")
]


# ---------------------------------------------------------------------------
# セクション
# ---------------------------------------------------------------------------


class DataConfig(_Base):
    """``data`` セクション。シグナルと分類のソース。

    Attributes:
        signals: シグナルソースのリスト（YAML ``data.signals``、必須・1件以上）。
            1ソースならリストにせず dict で書いてもよい（内部では常にリスト）。
            シグナル名は全ソースで一意である必要がある。
        classification: 業種等の分類ソース（YAML ``data.classification``、既定 ``None``）。
            ``category`` metric 等で使う。
    """

    # 単一ソースでもリストでも書ける。内部では常にリスト
    signals: Annotated[list[SignalsSource], BeforeValidator(_to_list)] = Field(min_length=1)
    classification: ClassificationSource | None = None

    @model_validator(mode="after")
    def _unique_signal_names(self) -> "DataConfig":
        """シグナル名がソース間で重複していないかを確認する。

        Raises:
            ValueError: 同じシグナル名が複数回定義されている場合。
        """
        names = self.signal_names
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"Duplicate signal names across sources: {dup}")
        return self

    @property
    def signal_names(self) -> list[str]:
        """全ソースのシグナル名を定義順に並べたリスト（``value_map`` のキー）。"""
        return [name for s in self.signals for name in s.value_map]


class RiskModelConfig(_Base):
    """``risk_models.<名前>`` セクション。1つのリスクモデルのデータを束ねる。

    別モデルのデータとの混在を防ぐため、モデルごとにまとめて定義する。
    構成要素はすべて任意で、指定したものだけが読み込まれる。

    Attributes:
        exposures: エクスポージャー（YAML ``exposures``、既定 ``None``）。``asset_id`` 以外の
            数値列がファクターになる。
        factor_covariance: ファクター共分散（YAML ``factor_covariance``、既定 ``None``）。
            現状はソースの形式のまま読み込む。
        specific_risk: 固有リスク（YAML ``specific_risk``、既定 ``None``）。
        factor_returns: ファクターリターン（YAML ``factor_returns``、既定 ``None``）。
            期間への集約・フォワードリターンの計算はいずれも加算。
        specific_return: このモデルに対応するスペシフィックリターンの系列名
            （YAML ``specific_return``、既定 ``None``）。``returns`` に定義された系列名で
            なければならない。未設定だとデータ構築時に警告が出る。
        factor_groups: ``{グループ名: [ファクター名, ...]}``（YAML ``factor_groups``、
            既定 ``None``）。``factor_correlation`` の ``factor_group`` 等で使う。
    """

    exposures: Source | None = None
    factor_covariance: Source | None = None
    specific_risk: Source | None = None
    factor_returns: FactorReturnsSource | None = None
    specific_return: str | None = None  # returns の系列名。同一モデルであることを明示
    factor_groups: dict[str, list[str]] | None = None

    @property
    def components(self) -> set[str]:
        """設定されている構成要素名の集合。

        ``exposures`` / ``factor_covariance`` / ``specific_risk`` / ``factor_returns`` の
        うち ``None`` でないもの。metric の ``requires``（``risk_models.<構成要素>``）の
        充足判定に使う。
        """
        names = ("exposures", "factor_covariance", "specific_risk", "factor_returns")
        return {n for n in names if getattr(self, n) is not None}


class BarraRiskModelConfig(_Base):
    """Barra のリスクモデル（``risk_models.<名前>`` に ``source: barra`` と書いた場合）。

    社内 DB（接続先 ``risk_models``）から、``components`` に指定した構成要素を読む。
    ファクター名は ``{model}_FAC`` テーブルの FAC（モデル名の接頭辞を除いたもの）、
    ``factor_groups`` の既定値は同じテーブルの FGROUP から作る。詳細は ``data/barra.py``。

    - exposures / factor_covariance: 評価行の ``match_on`` の日付（6桁の月なら月末日）
      以前で最新の Barra のデータ（スナップショット）
    - factor_returns: 日次のファクターリターンを ``match_on`` の列を境界として期間に
      加算で集約する

    Attributes:
        source: 判別子。常に ``"barra"``（YAML ``source: barra``）。
        model: Barra のモデル（YAML ``model``、必須）。``JPE4`` / ``GEMLT`` / ``GEM3``。
        match_on: スナップショットの日付と期間の境界に使うカレンダーの列
            （YAML ``match_on``、必須）。日付（yyyymmdd）または月（yyyymm）の列。
        convention: ファクターリターンの時点の規約（YAML ``convention``、既定 ``"realized"``）。
        components: 読み込む構成要素（YAML ``components``、既定は3つすべて）。
            ``exposures`` / ``factor_covariance`` / ``factor_returns`` から選ぶ。
        specific_return: このモデルに対応するスペシフィックリターンの系列名
            （YAML ``specific_return``、既定 ``None``）。``RiskModelConfig`` と同じ。
        factor_groups: ``{グループ名: [ファクター名, ...]}``（YAML ``factor_groups``、
            既定 ``None`` = FGROUP から自動で作る）。
    """

    source: Literal["barra"]
    model: BarraModel
    match_on: str
    convention: Convention = "realized"
    components: list[BarraComponent] = Field(
        default_factory=lambda: ["exposures", "factor_covariance", "factor_returns"],
        min_length=1,
    )
    specific_return: str | None = None
    factor_groups: dict[str, list[str]] | None = None

    @field_validator("components")
    @classmethod
    def _unique_components(cls, v: list[str]) -> list[str]:
        """``components`` の重複を除く（順序は保つ）。"""
        return list(dict.fromkeys(v))


def _risk_model_tag(value: Any) -> str:
    """``risk_models.<名前>`` の判別子。``source: barra`` なら ``barra``、それ以外は ``custom``。"""
    source = value.get("source") if isinstance(value, dict) else getattr(value, "source", None)
    return "barra" if source == "barra" else "custom"


RiskModelSource = Annotated[
    Annotated[RiskModelConfig, Tag("custom")] | Annotated[BarraRiskModelConfig, Tag("barra")],
    Discriminator(_risk_model_tag),
]


class MetricEntry(_Base):
    """``metrics`` の1要素。実行する metric とそのパラメータ。

    ``params`` はここでは検証せず、``validation.validate_metrics()`` が metric の
    ``Params`` モデルで検証する。

    Attributes:
        name: レジストリに登録された metric 名（YAML ``name``、必須）。
        id: 結果のキー（YAML ``id``、既定 ``None`` = ``name``）。同じ metric を複数回
            使う場合は一意な id が必要。
        enabled: ``False`` なら実行しない（YAML ``enabled``、既定 ``True``）。
        params: metric ごとのパラメータ（YAML ``params``、既定 ``{}`` = すべて既定値）。

    Examples:
        >>> MetricEntry(name="quantile", id="q10", params={"n_quantiles": 10}).key
        'q10'
    """

    name: str
    id: str | None = None
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    @property
    def key(self) -> str:
        """結果のキー。``id`` があれば ``id``、なければ ``name``。"""
        return self.id or self.name


class ExecutionConfig(_Base):
    """``execution`` セクション。実行方法の設定。

    Attributes:
        n_jobs: metric を並列に実行するプロセス数（YAML ``execution.n_jobs``、既定 ``1``、
            1以上）。1 以下または metric が1つなら逐次実行する。
    """

    n_jobs: int = Field(default=1, ge=1)


class OutputConfig(_Base):
    """``output`` セクション。結果の書き出し先と形式。

    Attributes:
        dir: 出力ディレクトリ（YAML ``output.dir``、既定 ``"out"``）。既定値も含め、
            相対パスは config ファイルの場所を基準に解決する。
        formats: 出力形式のリスト（YAML ``output.formats``、既定 ``["csv"]``）。
            ``parquet`` / ``xlsx`` / ``csv`` から選ぶ。
    """

    dir: PathLike = Field(default="out", validate_default=True)
    formats: list[OutputFormat] = Field(default_factory=lambda: ["csv"])


class CacheConfig(_Base):
    """DB 取得結果のローカルキャッシュ（``cache`` セクション）。

    キャッシュのキーは接続先・SQL 本文・期間パラメータのハッシュ。ファイルソースは
    キャッシュしない。

    Attributes:
        enabled: キャッシュを使うか（YAML ``cache.enabled``、既定 ``True``）。
        dir: キャッシュ（parquet）の保存先（YAML ``cache.dir``、既定 ``".cache"``）。
            既定値も含め、相対パスは config ファイルの場所を基準に解決する。
    """

    enabled: bool = True
    dir: PathLike = Field(default=".cache", validate_default=True)


class EvaluationConfig(_Base):
    """config.yaml 全体（トップレベル）のモデル。

    構造の検証に加え、metric キー・リターン系列名の重複や
    ``risk_models.*.specific_return`` の参照先といった config 内の相互参照も検証する。
    カレンダーやファイル、metric レジストリが必要な検証は ``validation.validate_config()``
    が行う。

    Attributes:
        version: config の書式バージョン（YAML ``version``、必須）。現状は ``1`` のみ。
        calendar: カレンダー（YAML ``calendar``、既定 ``None``）。config からデータを
            読み込む場合は必須（``validation.py`` で確認する）。DataBundle を直接渡す
            Python API では省略できる。
        period: 評価期間（YAML ``period``、既定はカレンダーの全行）。
        horizons: フォワードリターンのホライズン（単位 = カレンダーの行数）
            （YAML ``horizons``、既定 ``[1]``）。正の整数で重複不可。昇順に並べ替えて保持する。
        data: シグナルと分類（YAML ``data``、既定 ``None``）。config からデータを読み込む
            場合は必須。
        universe: 評価対象のユニバース（YAML ``universe``、既定 ``None``）。シグナルを
            この (日付, 銘柄) に絞る。``columns`` に ``weight`` を指定すると、
            ``benchmark`` がない場合にユニバースのウェイトをベンチマークウェイトとして使う。
        benchmark: ベンチマークウェイト（YAML ``benchmark``、既定 ``None``）。
            ``asset_id`` と ``weight`` role の列が必要。
        returns: リターンソースのリスト（YAML ``returns``、既定 ``[]``）。系列名は全ソースで一意。
        risk_models: ``{モデル名: RiskModelConfig | BarraRiskModelConfig}``（YAML ``risk_models``、
            既定 ``{}``）。``source: barra`` と書いたものが ``BarraRiskModelConfig``。
        portfolios: 将来のポートフォリオ（Brinson・リスク分解の入力）のための予約枠
            （YAML ``portfolios``、既定 ``[]``）。現状は空でないとエラーになる。
        metrics: 実行する metric のリスト（YAML ``metrics``、既定 ``[]``）。
        execution: 実行設定（YAML ``execution``、既定 ``n_jobs=1``）。
        output: 出力設定（YAML ``output``、既定 ``dir="out"``、``formats=["csv"]``）。
        cache: DB キャッシュの設定（YAML ``cache``、既定 ``enabled=True``、
            ``dir=".cache"``）。

    Examples:
        >>> cfg = EvaluationConfig.model_validate(
        ...     {"version": 1, "horizons": [3, 1], "metrics": [{"name": "ic"}]}
        ... )
        >>> cfg.horizons
        [1, 3]
    """

    version: Literal[1]
    # calendar / data / returns は DataBundle を直接渡す Python API では省略できる。
    # io 経由で読み込む場合に必須であることは意味の検証（validation.py）で確認する
    calendar: CalendarConfig | None = None
    period: PeriodConfig = Field(default_factory=PeriodConfig)
    horizons: list[int] = Field(default_factory=lambda: [1], min_length=1)
    data: DataConfig | None = None
    # columns に weight を指定すると、benchmark がない場合にユニバースのウェイトを
    # ベンチマークウェイトとして使う
    universe: Source | None = None
    # ベンチマークウェイト（columns に asset_id と weight が必要）
    benchmark: Source | None = None
    returns: list[ReturnsSource] = Field(default_factory=list)
    risk_models: dict[str, RiskModelSource] = Field(default_factory=dict)
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
        """ホライズンが正の整数で重複がないことを確認し、昇順に並べ替える。

        Raises:
            ValueError: 1 未満の値または重複がある場合。
        """
        if any(h < 1 for h in v):
            raise ValueError(f"horizons must be positive integers: {v}")
        if len(set(v)) != len(v):
            raise ValueError(f"horizons must be unique: {v}")
        return sorted(v)

    @field_validator("portfolios")
    @classmethod
    def _reserved_portfolios(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """予約枠の ``portfolios`` が空であることを確認する。

        Raises:
            ValueError: ``portfolios`` に要素がある場合（未対応）。
        """
        if v:
            raise ValueError("portfolios is reserved for a future version and not supported yet")
        return v

    @model_validator(mode="after")
    def _check_cross_references(self) -> "EvaluationConfig":
        """config 内の相互参照を検証する。

        - metric のキー（``id`` または ``name``）が重複していないこと
          （無効化された metric も含めて検査する）
        - リターン系列名が全リターンソースで重複していないこと
        - ``risk_models.*.specific_return`` が定義済みのリターン系列名を指していること

        Raises:
            ValueError: 上記のいずれかに違反した場合。
        """
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
        """リターン系列名 -> kind（``total`` / ``specific``）。全リターンソースを通した対応。"""
        return {name: s.kind for r in self.returns for name, s in r.series.items()}

    @property
    def enabled_metrics(self) -> list[MetricEntry]:
        """``enabled`` が ``True`` の metric を config の順序のまま返す。"""
        return [m for m in self.metrics if m.enabled]


def load_config(path: str | Path) -> EvaluationConfig:
    """YAML を読み込み、相対パスを config ファイルの場所を基準に解決して検証する。

    ``path`` は ``~`` を展開して絶対パスにし、その親ディレクトリを ``base_dir`` として
    pydantic の検証コンテキストに渡す。これにより config 内の相対パス（``PathLike``）は
    実行ディレクトリではなく config ファイルの場所を基準に解決される。
    ここで行うのは構造の検証（1段目）だけで、意味の検証は ``validation.validate_config()``。

    Args:
        path: config.yaml のパス。

    Returns:
        検証済みの ``EvaluationConfig``。

    Raises:
        FileNotFoundError: ファイルが存在しない場合。
        yaml.YAMLError: YAML として解析できない場合。
        ValueError: YAML のトップレベルが mapping でない場合。
        pydantic.ValidationError: スキーマに合わない場合（未知のキー、型の不一致、
            相互参照の誤り等）。

    Examples:
        >>> cfg = load_config("examples/example1/config.example.yaml")  # doctest: +SKIP
        >>> [m.key for m in cfg.enabled_metrics]  # doctest: +SKIP
    """
    path = Path(path).expanduser().resolve()
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file must be a YAML mapping: {path}")
    return EvaluationConfig.model_validate(raw, context={"base_dir": path.parent})
