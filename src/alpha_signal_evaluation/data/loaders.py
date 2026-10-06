"""config の各データソースを読み込み、role 名にリネームした long 形式に揃える。

ここでは「どのファイル / クエリを読み、どのカレンダー行に対応づけるか」までを扱い、
期間集約やフォワードリターンなどの時点合わせは行わない（pipeline/preprocess.py の責務）。

data 層のうち、物理的な読み込み（io 層: ``io.file`` / ``io.db`` / ``io.cache``）を呼び出す
唯一のモジュール。依存は data -> io の一方向で、io 層はここでのキーの意味（カレンダー・
期間など）を知らない。主な入口は次の2つで、どちらも pipeline/preprocess.py から呼ばれる。

- ``load_snapshot``: カレンダーの各行に1時点のデータ（シグナル・ユニバース・分類・
  リスクモデルなど）を対応づける。ファイル（日付パーティション / 単一ファイル）と DB に対応。
- ``load_daily``: DB から日次データを期間指定で取得する（期間への集約は pipeline が行う）。

日付キーは ``yyyymmdd``（8桁）または ``yyyymm``（6桁）の文字列、銘柄コード（``asset_id``）は
文字列に正規化し、ソース間で型や書式が違っても結合できるようにする。
"""

import json
import logging
from collections.abc import Collection, Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Self

import pandas as pd

from ..config import DBSource, FileSource
from ..io.cache import ParquetCache
from ..io.db import DBClient, get_client, get_connection_config
from ..io.file import read_table
from .calendar import Calendar
from .columns import ASSET_ID, DATE, ColumnMap, normalize_columns

logger = logging.getLogger(__name__)

_CSV_SUFFIXES = {".csv", ".dat", ".tsv"}


class LoadContext:
    """読み込み中に共有する状態（カレンダー、DB 接続、キャッシュ）。

    DB クライアントは接続先名ごとに最初の利用時に作成・接続し、以降は使い回す。
    with 文で使うと、抜けるときに作成したすべてのクライアントを閉じる。

    Attributes:
        calendar: 評価に使うカレンダー。``calendar.index`` が付与する日付キーの列名になる。
        cache: DB クエリ結果のキャッシュ。``None`` ならキャッシュしない。
    """

    def __init__(self, calendar: Calendar, cache: ParquetCache | None = None):
        """コンテキストを作る。この時点では DB に接続しない。

        Args:
            calendar: 評価に使うカレンダー。
            cache: DB クエリ結果のキャッシュ。``None`` ならキャッシュしない。
        """
        self.calendar = calendar
        self.cache = cache
        self._clients: dict[str, DBClient] = {}

    def db(self, connection: str) -> DBClient:
        """接続先名に対応する接続済みの DB クライアントを返す。

        初回は ``io.db.get_connection_config`` で接続設定（ホスト名・認証情報は環境変数から）を
        作り、``get_client`` で得たクライアントの ``connect()`` を呼んで保持する。
        2回目以降は同じクライアントを返す。

        Args:
            connection: config の ``connection`` キー（例: ``risk_models``）。

        Returns:
            接続済みの ``DBClient``。

        Raises:
            ValueError: 接続先名が未知の場合、または接続設定の server_type が未対応の場合。
        """
        if connection not in self._clients:
            client = get_client(get_connection_config(connection))
            client.connect()
            self._clients[connection] = client
        return self._clients[connection]

    def close(self) -> None:
        """作成済みのすべての DB クライアントを閉じ、保持をやめる。何度呼んでもよい。"""
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def __enter__(self) -> Self:
        """自身を返す（with 文用。DB への接続は ``db`` を呼んだときに行う）。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """``close`` を呼んで DB クライアントを閉じる。例外は抑止しない。"""
        self.close()


# ---------------------------------------------------------------------------
# キーの正規化
# ---------------------------------------------------------------------------


def to_date_key(values: pd.Series, ndigits: int) -> pd.Series:
    """日付を表す列を ``yyyymmdd``（8桁）または ``yyyymm``（6桁）の文字列に揃える。

    入力の型ごとに次のように変換する。欠損は欠損のまま残す。

    - datetime 型: ``strftime`` で書式化する
    - 数値型（例: ``20030131``）: 整数の文字列にして先頭 ``ndigits`` 文字を取る
      （8桁の日付から6桁の月を作れる）。整数でない値があると型変換で失敗する
    - ``date`` / ``datetime`` オブジェクトの列: ``pd.to_datetime`` を経て書式化する
    - 文字列: 前後の空白を除き、最初の空白より前（時刻部分を捨てる）から ``-`` と ``/`` を
      除去して先頭 ``ndigits`` 文字を取る。月・日がゼロ埋めされていることを前提とする

    Args:
        values: 日付を表す列。
        ndigits: 8 なら ``yyyymmdd``、それ以外なら ``yyyymm`` の書式にする。

    Returns:
        日付キーの文字列の Series（index は ``values`` と同じ）。
    """
    fmt = "%Y%m%d" if ndigits == 8 else "%Y%m"
    if pd.api.types.is_datetime64_any_dtype(values):
        return values.dt.strftime(fmt)
    if pd.api.types.is_numeric_dtype(values):
        return values.astype("Int64").astype(str).str[:ndigits]
    non_null = values.dropna()
    if len(non_null) and not isinstance(non_null.iloc[0], str):  # date / datetime オブジェクト
        return pd.to_datetime(values).dt.strftime(fmt)
    keys = values.astype(str).str.strip().str.split(" ").str[0].str.replace(r"[-/]", "", regex=True)
    return keys.str[:ndigits]


def to_asset_id(values: pd.Series) -> pd.Series:
    """銘柄コードを文字列に揃える（ソース間で int / str が混在しても結合できるように）。

    float 型（欠損を含む整数列が float になったもの）は一度 ``Int64`` にして ``.0`` が
    付かないようにする。文字列はそのまま（先頭ゼロも保持）。欠損は欠損のまま残す。

    Args:
        values: 銘柄コードの列。

    Returns:
        文字列（欠損は NaN）の Series。index は ``values`` と同じ。
    """
    if pd.api.types.is_float_dtype(values):
        values = values.astype("Int64")
    return values.astype(str).where(values.notna())


def shift_day(key: str, days: int) -> str:
    """``yyyymmdd`` の日付キーを暦日で ``days`` 日ずらしたキーを返す。

    Args:
        key: 8桁の日付キー。
        days: ずらす日数（負なら過去へ）。

    Returns:
        8桁の日付キー。

    Raises:
        ValueError: ``key`` が ``yyyymmdd`` として解釈できない場合。
    """
    return (datetime.strptime(key, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


def period_end_day(key: str) -> str:
    """日付キーが表す期間の最終日（8桁はそのまま、6桁の月はその月の月末日）を返す。

    DB から日次データを取得する範囲の終端を決めるのに使う。

    Args:
        key: 8桁の日付キー、または6桁の月キー。

    Returns:
        8桁の日付キー。
    """
    if len(key) == 8:
        return key
    return pd.Period(f"{key[:4]}-{key[4:6]}", freq="M").end_time.strftime("%Y%m%d")


def period_start_day(key: str) -> str:
    """日付キーが表す期間の初日（8桁はそのまま、6桁の月はその月の1日）を返す。

    Args:
        key: 8桁の日付キー、または6桁の月キー。

    Returns:
        8桁の日付キー。
    """
    return key if len(key) == 8 else f"{key}01"


# ---------------------------------------------------------------------------
# ファイル
# ---------------------------------------------------------------------------


def partition_paths(source: FileSource, rows: pd.DataFrame, index: str) -> dict[str, Path]:
    """カレンダーの各行（index の値）に対応するファイルパスを返す。

    ``source.path`` のプレースホルダ（例: ``{Base_day}``・``{Rskmdl_month}``）を、
    各行のカレンダー列の値で ``str.format`` により埋める。複数の行が同じパスに
    対応してもよい（例: 月次で更新されるファイルを日次の行から参照する場合）。

    Args:
        source: 日付パーティションされたファイルソース。
        rows: 対象のカレンダー行（全列 ``str``）。
        index: カレンダーの日付キー列名（``calendar.index``）。

    Returns:
        日付キー -> ファイルパスの辞書（``rows`` の順）。

    Raises:
        KeyError: プレースホルダがカレンダーの列にない場合。
    """
    return {row[index]: Path(source.path.format(**row)) for row in rows.to_dict("records")}


def _read_options(source: FileSource, path: Path) -> dict:
    """ファイルを読むときにリーダーへ渡すオプションを作る。

    ``source.read_options`` をコピーし、CSV 系（``.csv`` / ``.dat`` / ``.tsv``）で ``dtype`` が
    未指定なら、銘柄コード列（ヘッダー先頭に ``#`` が付いた名前も含む）を文字列として
    読むよう ``dtype`` を追加する。
    """
    options = dict(source.read_options)
    if path.suffix.lower() in _CSV_SUFFIXES and "dtype" not in options:
        # 銘柄コードの先頭ゼロ落ち等を防ぐ（ヘッダーが "#nri_code" の場合も含む）
        name = source.columns.get(ASSET_ID, ASSET_ID)
        options["dtype"] = {name: str, f"#{name}": str}
    return options


def _read_file(source: FileSource, path: Path) -> pd.DataFrame:
    """``io.file.read_table`` でファイルを読む。

    失敗した場合は、同じ型の例外をファイルパス入りのメッセージで作り直して送出する
    （元の例外は ``__cause__`` に残る）。
    """
    try:
        return read_table(path, **_read_options(source, path))
    except Exception as e:
        raise type(e)(f"Failed to read {path}: {e}") from e


def _load_partitioned(
    source: FileSource,
    rows: pd.DataFrame,
    ctx: LoadContext,
    column_map: ColumnMap,
    optional_keys: Collection[str],
) -> pd.DataFrame:
    """日付パーティションされたファイルを行ごとに読み、``date`` 列に日付キーを付けて結合する。

    同じファイルが複数の行に対応する場合は1回だけ読む。ファイル内に ``date`` role の列が
    あっても捨て、カレンダー行の日付キーで置き換える。ファイルがない行は、``optional_keys``
    に含まれれば欠損として読み飛ばして警告をログに出し、そうでなければ
    ``FileNotFoundError`` を送出する。

    Returns:
        ``date`` 列と role 名にリネーム済みの列を持つ long 形式（RangeIndex）。
        1件も読めなければ、列だけを持つ空の DataFrame。
    """
    index = ctx.calendar.index
    cache: dict[Path, pd.DataFrame] = {}  # 同じファイルが複数行に対応する場合は1回だけ読む
    frames: dict[str, pd.DataFrame] = {}
    skipped: list[Path] = []
    for key, path in partition_paths(source, rows, index).items():
        if path not in cache:
            if not path.exists():
                if key in optional_keys:
                    skipped.append(path)
                    continue
                raise FileNotFoundError(path)
            cache[path] = normalize_columns(_read_file(source, path), column_map)
        frames[key] = cache[path].drop(columns=[DATE], errors="ignore")
    if skipped:
        logger.warning(
            "%d optional file(s) outside the period not found, treated as missing: %s%s",
            len(skipped),
            skipped[0],
            f" ... {skipped[-1]}" if len(skipped) > 1 else "",
        )
    if not frames:
        return pd.DataFrame(columns=[DATE, *column_map.mapping])
    df = pd.concat(frames, names=[DATE, "_row"]).reset_index(level=DATE)
    return df.reset_index(drop=True)


def _attach_calendar_keys(
    df: pd.DataFrame, rows: pd.DataFrame, ctx: LoadContext, match_on: str
) -> pd.DataFrame:
    """データ内の date 列を ``match_on`` 列と突き合わせ、calendar.index の値に置き換える。

    データの date を ``match_on`` 列の値と同じ桁数（8桁 / 6桁）の日付キーに揃えてから
    内部結合する。どの行にも対応しないデータは捨て、同じ ``match_on`` の値を持つ行が
    複数あればデータを行ごとに複製する。``date`` 列は結果の末尾の列になる。

    Raises:
        ValueError: ``df`` に ``date`` 列がない場合。
    """
    if DATE not in df.columns:
        raise ValueError(f"Column for role {DATE!r} is required to match rows on {match_on!r}")
    index = ctx.calendar.index
    ndigits = len(rows[match_on].iloc[0])
    df = df.assign(**{DATE: to_date_key(df[DATE], ndigits)})
    keys = pd.DataFrame({"_key": rows[index].to_numpy(), DATE: rows[match_on].to_numpy()})
    # 同じ match_on の値が複数行に対応する場合（同じ Rskmdl_month が続く等）は行ごとに複製する
    merged = df.merge(keys, on=DATE, how="inner")
    return merged.drop(columns=DATE).rename(columns={"_key": DATE})


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def run_query(source: DBSource, params: dict[str, str], ctx: LoadContext) -> pd.DataFrame:
    """SQL ファイルを実行して結果の DataFrame を返す。

    結果は「接続先名・SQL 本文・パラメータ」のハッシュをキーにキャッシュする
    （``ctx.cache`` が ``None`` ならキャッシュしない）。キャッシュがあれば DB に問い合わせない。
    キャッシュへの保存に失敗しても警告をログに出すだけで、評価は止めない。

    Args:
        source: DB ソース。``query`` は SQL ファイル（UTF-8）のパス。
        params: SQL のバインドパラメータ（例: ``{"start": ..., "end": ...}``。
            SQL 側では ``:start`` / ``:end`` で参照する）。
        ctx: 読み込みコンテキスト（DB 接続とキャッシュ）。

    Returns:
        クエリ結果（列名は DB が返したまま。role 名へのリネームは呼び出し側で行う）。

    Raises:
        OSError: SQL ファイルが読めない場合。
        ValueError: 接続先名が未知の場合。
    """
    sql = Path(source.query).read_text(encoding="utf-8")
    key = ParquetCache.make_key(source.connection, sql, json.dumps(params, sort_keys=True))
    if ctx.cache is not None and (cached := ctx.cache.get(key)) is not None:
        return cached
    logger.info("Querying %s (%s): %s", source.connection, source.query, params)
    df = ctx.db(source.connection).execute(sql, params)
    if ctx.cache is not None:
        try:
            ctx.cache.put(key, df)
        except Exception as e:  # キャッシュの失敗で評価を止めない
            logger.warning("Failed to cache query result: %s", e)
    return df


def load_daily(
    source: DBSource,
    start: str,
    end: str,
    ctx: LoadContext,
    required_roles: Iterable[str] = (),
) -> pd.DataFrame:
    """``start < date <= end`` の日次データを取得する。date 列は8桁の文字列。

    SQL には ``start`` / ``end`` をパラメータとして渡し（``run_query``）、結果を role 名に
    リネームしたうえで、日付キーと銘柄コードを正規化し、念のため ``start < date <= end``
    の範囲に絞る（SQL が範囲を広めに返しても良い）。期間への集約は行わない
    （pipeline/preprocess.py の責務）。

    Args:
        source: DB ソース。``columns`` には ``date`` と ``required_roles`` のマッピングが必要。
        start: 取得範囲の開始日（8桁、この日は含まない）。
        end: 取得範囲の終了日（8桁、この日を含む）。
        ctx: 読み込みコンテキスト。
        required_roles: ``date`` 以外に必須の role 名。

    Returns:
        role 名にリネーム済みの long 形式。``date`` 列は8桁の文字列、``asset_id`` 列が
        あれば文字列に正規化済み。index は振り直さない。

    Raises:
        ValueError: 必須 role のマッピングがない場合、またはマッピングした列がクエリ結果に
            ない場合。
    """
    column_map = ColumnMap(source.columns, frozenset({DATE, *required_roles}))
    df = normalize_columns(run_query(source, {"start": start, "end": end}, ctx), column_map)
    df[DATE] = to_date_key(df[DATE], 8)
    if ASSET_ID in df.columns:
        df[ASSET_ID] = to_asset_id(df[ASSET_ID])
    return df[(df[DATE] > start) & (df[DATE] <= end)]


# ---------------------------------------------------------------------------
# スナップショット（カレンダーの行ごとに1時点のデータ）
# ---------------------------------------------------------------------------


def load_snapshot(
    source: FileSource | DBSource,
    rows: pd.DataFrame,
    ctx: LoadContext,
    required_roles: Iterable[str] = (),
    optional_keys: Collection[str] = frozenset(),
) -> pd.DataFrame:
    """``rows``（カレンダーの行）に対応するデータを読み、``date`` 列に calendar.index を付与する。

    ソースの種類ごとに、データとカレンダー行を次のように対応づける。

    - 日付パーティションされたファイル: 行ごとにパステンプレートを展開して読む。
      ファイル内の date 列は使わない（``optional_keys`` の行はファイルがなくてもよい）
    - 単一ファイル: ファイル全体を読み、date 列を ``match_on`` 列（省略時は
      ``calendar.index``）の値と突き合わせる
    - DB: ``match_on`` 列の最小値の期間初日から最大値の期間末日までの日次データを取得し
      （``load_daily``）、date 列を ``match_on`` 列の値と突き合わせる。``match_on`` が月
      （6桁）なら月内の全日がその月の行に対応するため、銘柄ごとのデータでは SQL が
      (月, 銘柄) ごとに1行だけ返す必要がある（重複は下記の検証でエラーになる）

    ``asset_id`` 列があれば文字列に正規化し、欠損の行を除いたうえで、
    (date, asset_id) の重複がないことを検証する。時点はずらさない。

    Args:
        source: 読み込むデータソース。
        rows: 対象のカレンダー行（全列 ``str``）。
        ctx: 読み込みコンテキスト。
        required_roles: マッピングが必須の role 名（例: ``{"asset_id"}``）。
        optional_keys: ファイルがなくても欠損として扱う行の日付キー
            （日付パーティションされたファイルのときだけ使う）。

    Returns:
        long 形式の DataFrame（RangeIndex）。``date`` 列（calendar.index の値）と、
        role 名にリネーム済みの列（マッピングにない列は元の名前のまま）を持つ。

    Raises:
        ValueError: 必須 role のマッピングや列がない場合、単一ファイル / DB のデータに
            ``date`` 列がない場合、または (date, asset_id) が重複している場合。
        FileNotFoundError: 必須の行のパーティションファイルがない場合。
    """
    required_roles = frozenset(required_roles)
    match_on = source.match_on or ctx.calendar.index

    if isinstance(source, FileSource) and source.is_partitioned:
        column_map = ColumnMap(source.columns, required_roles - {DATE})
        df = _load_partitioned(source, rows, ctx, column_map, optional_keys)
    elif isinstance(source, FileSource):
        column_map = ColumnMap(source.columns, required_roles | {DATE})
        df = normalize_columns(_read_file(source, Path(source.path)), column_map)
        df = _attach_calendar_keys(df, rows, ctx, match_on)
    else:
        values = rows[match_on]
        start = shift_day(period_start_day(values.min()), -1)
        df = load_daily(source, start, period_end_day(values.max()), ctx, required_roles)
        df = _attach_calendar_keys(df, rows, ctx, match_on)

    if ASSET_ID in df.columns:
        df[ASSET_ID] = to_asset_id(df[ASSET_ID])
        df = df[df[ASSET_ID].notna()]
        dup = df.duplicated([DATE, ASSET_ID])
        if dup.any():
            examples = df.loc[dup, [DATE, ASSET_ID]].head(5).to_dict("records")
            raise ValueError(
                f"Duplicated (date, asset_id) rows in {describe_source(source)}: {examples}"
            )
    return df.reset_index(drop=True)


def describe_source(source: FileSource | DBSource) -> str:
    """エラーメッセージ用にデータソースを表す文字列を返す。

    Args:
        source: データソース。

    Returns:
        ファイルソースならパス（テンプレートのまま）、DB ソースなら SQL ファイルのパス。
    """
    return source.path if isinstance(source, FileSource) else str(source.query)
