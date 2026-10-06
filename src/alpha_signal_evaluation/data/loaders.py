"""config の各データソースを読み込み、role 名にリネームした long 形式に揃える。

ここでは「どのファイル / クエリを読み、どのカレンダー行に対応づけるか」までを扱い、
期間集約やフォワードリターンなどの時点合わせは行わない（pipeline/preprocess.py の責務）。
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
from ..io.db import DBClient, create_db_client, get_connection_config
from ..io.file import read_table
from .calendar import Calendar
from .columns import ASSET_ID, DATE, ColumnMap, normalize_columns

logger = logging.getLogger(__name__)

_CSV_SUFFIXES = {".csv", ".dat", ".tsv"}


class LoadContext:
    """読み込み中に共有する状態（カレンダー、DB 接続、キャッシュ）。"""

    def __init__(self, calendar: Calendar, cache: ParquetCache | None = None):
        self.calendar = calendar
        self.cache = cache
        self._clients: dict[str, DBClient] = {}

    def db(self, connection: str) -> DBClient:
        if connection not in self._clients:
            client = create_db_client(get_connection_config(connection))
            client.connect()
            self._clients[connection] = client
        return self._clients[connection]

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ---------------------------------------------------------------------------
# キーの正規化
# ---------------------------------------------------------------------------


def to_date_key(values: pd.Series, ndigits: int) -> pd.Series:
    """日付を表す列を ``yyyymmdd``（8桁）または ``yyyymm``（6桁）の文字列に揃える。"""
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
    """銘柄コードを文字列に揃える（ソース間で int / str が混在しても結合できるように）。"""
    if pd.api.types.is_float_dtype(values):
        values = values.astype("Int64")
    return values.astype(str).where(values.notna())


def shift_day(key: str, days: int) -> str:
    return (datetime.strptime(key, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# ファイル
# ---------------------------------------------------------------------------


def partition_paths(source: FileSource, rows: pd.DataFrame, index: str) -> dict[str, Path]:
    """カレンダーの各行（index の値）に対応するファイルパス。"""
    return {row[index]: Path(source.path.format(**row)) for row in rows.to_dict("records")}


def _read_options(source: FileSource, path: Path) -> dict:
    options = dict(source.read_options)
    if path.suffix.lower() in _CSV_SUFFIXES and "dtype" not in options:
        # 銘柄コードの先頭ゼロ落ち等を防ぐ
        options["dtype"] = {source.columns.get(ASSET_ID, ASSET_ID): str}
    return options


def _read_file(source: FileSource, path: Path) -> pd.DataFrame:
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
    index = ctx.calendar.index
    cache: dict[Path, pd.DataFrame] = {}  # 同じファイルが複数行に対応する場合は1回だけ読む
    frames: dict[str, pd.DataFrame] = {}
    for key, path in partition_paths(source, rows, index).items():
        if path not in cache:
            if not path.exists():
                if key in optional_keys:
                    logger.warning("Optional file not found, treated as missing: %s", path)
                    continue
                raise FileNotFoundError(path)
            cache[path] = normalize_columns(_read_file(source, path), column_map)
        frames[key] = cache[path].drop(columns=[DATE], errors="ignore")
    if not frames:
        return pd.DataFrame(columns=[DATE, *column_map.mapping])
    df = pd.concat(frames, names=[DATE, "_row"]).reset_index(level=DATE)
    return df.reset_index(drop=True)


def _attach_calendar_keys(
    df: pd.DataFrame, rows: pd.DataFrame, ctx: LoadContext, match_on: str
) -> pd.DataFrame:
    """データ内の date 列を ``match_on`` 列と突き合わせ、calendar.index の値に置き換える。"""
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
    """SQL ファイルを実行する。結果はクエリと期間のハッシュをキーにキャッシュする。"""
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
    """``start < date <= end`` の日次データを取得する。date 列は8桁の文字列。"""
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

    返り値は long 形式で、role 名にリネーム済みの列と ``date`` 列を持つ。
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
        df = load_daily(source, shift_day(values.min(), -1), values.max(), ctx, required_roles)
        df = _attach_calendar_keys(df, rows, ctx, match_on)

    if ASSET_ID in df.columns:
        df[ASSET_ID] = to_asset_id(df[ASSET_ID])
        df = df[df[ASSET_ID].notna()]
        dup = df.duplicated([DATE, ASSET_ID])
        if dup.any():
            examples = df.loc[dup, [DATE, ASSET_ID]].head(5).to_dict("records")
            raise ValueError(f"Duplicated (date, asset_id) rows in {_describe(source)}: {examples}")
    return df.reset_index(drop=True)


def _describe(source: FileSource | DBSource) -> str:
    return source.path if isinstance(source, FileSource) else str(source.query)
