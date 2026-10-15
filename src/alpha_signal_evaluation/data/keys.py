"""日付キーと銘柄コードの正規化・日付キーの計算。

日付キーは ``yyyymmdd``（8桁）または ``yyyymm``（6桁）の文字列、銘柄コード（``asset_id``）は
文字列に揃え、ソース間で型や書式が違っても結合できるようにする。loaders・barra・
pipeline/preprocess.py から共通に使う。時点はずらさない（暦日の計算だけを行う）。
"""

from datetime import datetime, timedelta

import pandas as pd


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

    DB から日次データを取得する範囲の終端や、期間の境界を決めるのに使う。

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
