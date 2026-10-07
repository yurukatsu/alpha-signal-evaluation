"""MSCI Barra リスクモデル（JPE4 / GEMLT / GEM3）のデータを社内 DB から読み込む。

config の ``source: barra``（``returns`` の要素と ``risk_models.<名前>``）の読み込みを担う。
SQL・単位の換算・銘柄 ID の変換・通貨換算をここに閉じ込め、pipeline には他のソースと
同じ形（role 名の long 形式・wide 形式）で渡す。期間への集約とフォワードリターンの
計算（時点合わせ）は pipeline/preprocess.py が行い、ここではデータを日付ごとに
取り出すだけで時点はずらさない。

データの所在（接続先名で引く。ホスト名・認証情報は環境変数から読む）:

- ``risk_models``（SQL Server）: ``RISK_MODELS.dbo.{model}_*``
    - ``_FAC``: ファクターの一覧（FCD, FGROUP, FAC, MDL）
    - ``_D_PRC`` / ``_D_SRTN``: 日次のトータル（DRTN、取引通貨建て）・スペシフィック（SRTN）
    - ``_D_EXP`` / ``_D_COV`` / ``_D_FCTRTN``: 日次のエクスポージャー・ファクター共分散・
      ファクターリターン（いずれも FCD をキーにした long 形式）
    - ``_D_RATE``: 日次の為替（CUR, RATE）、``_IDTY``: BID の通貨（FDATE〜TDATE で有効）
- ``fs_trfac_glb``（PostgreSQL）: ``public.barraid_jp``（日付ごとの nri_code と BID の対応）

単位（DB の値 → このパッケージの値）:

- DRTN / SRTN: 1日あたりの % → 小数（× 0.01）
- ファクターリターン: 1日あたりの小数（そのまま）
- ファクター共分散: 年率の %² → 年率の小数²（× 0.0001）
- 為替 RATE: USD / 現地通貨 × 100。日次の変化率だけを使うので × 100 は打ち消し合う

銘柄 ID: Barra の BID を ``barraid_jp`` で nri_code（``asset_id``）に変換する。対応表は
必要な日付（カレンダー行の ``match_on`` の日付）ごとに、その日以前で最新のものを使う。
1つの日付で BID と nri_code が1対1にならない組は、どちらとも決められないため捨てる
（警告を出す）。取得する BID は対応表にあるものに限る（GEM3 等で全銘柄を読まないため）。

日付: スナップショット（エクスポージャー・共分散）は、目標日（``match_on`` の日付、
6桁の月なら月末日）以前で最新の Barra の営業日（``_D_FCTRTN`` にある日付）のデータを使う。
目標日から ``ASOF_LOOKBACK_DAYS`` 日より前のデータしかない場合は欠損として警告する。
"""

import itertools
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import get_args

import numpy as np
import pandas as pd

from ..config import BarraModel, BarraReturnsSource
from .columns import ASSET_ID, DATE, FACTOR, VALUE
from .loaders import LoadContext, execute_cached, shift_day, to_asset_id, to_date_key

logger = logging.getLogger(__name__)

BARRA_DB = "risk_models"
ID_MAP_DB = "fs_trfac_glb"
# Barra のソースが使う接続先（validation で環境変数の有無を確認する）
BARRA_CONNECTIONS = (BARRA_DB, ID_MAP_DB)

PERCENT = 0.01  # DRTN / SRTN（%）→ 小数
PERCENT_SQUARED = 1e-4  # 共分散（%²）→ 小数²
ASOF_LOOKBACK_DAYS = 31  # 目標日からこの日数より前のデータは使わない
_BIDS_PER_QUERY = 1000  # IN 句に並べる BID の数（SQL Server は1クエリ 2100 パラメータまで）
_DATES_PER_QUERY = 20  # IN 句に並べる日付の数
_OPEN_END = "99999999"  # _IDTY の TDATE が空のとき（有効期間の終わりがない）

_BID = "bid"
_TARGET = "_target"  # 目標日（match_on の日付）
_RESOLVED = "_resolved"  # 目標日に対して実際に使う Barra / 対応表の日付

_FACTORS_SQL = """
SELECT FCD AS fcd, FGROUP AS fgroup, FAC AS fac, MDL AS mdl
FROM RISK_MODELS.dbo.{model}_FAC
"""
_TRADING_DATES_SQL = """
SELECT DISTINCT DATE AS dateymd
FROM RISK_MODELS.dbo.{model}_D_FCTRTN
WHERE DATE > :start AND DATE <= :end
"""
_DAILY_VALUES_SQL = """
SELECT DATE AS dateymd, BID AS bid, {column} AS value
FROM RISK_MODELS.dbo.{model}_{table}
WHERE DATE > :start AND DATE <= :end AND BID IN ({bids})
"""
_EXPOSURES_SQL = """
SELECT DATE AS dateymd, BID AS bid, FCD AS fcd, EXP AS value
FROM RISK_MODELS.dbo.{model}_D_EXP
WHERE DATE IN ({dates}) AND BID IN ({bids})
"""
_COVARIANCE_SQL = """
SELECT DATE AS dateymd, FCD1 AS fcd1, FCD2 AS fcd2, COV AS value
FROM RISK_MODELS.dbo.{model}_D_COV
WHERE DATE IN ({dates})
"""
_FACTOR_RETURNS_SQL = """
SELECT DATE AS dateymd, FCD AS fcd, RTN AS value
FROM RISK_MODELS.dbo.{model}_D_FCTRTN
WHERE DATE > :start AND DATE <= :end
"""
_RATES_SQL = """
SELECT DATE AS dateymd, CUR AS cur, RATE AS rate
FROM RISK_MODELS.dbo.{model}_D_RATE
WHERE DATE > :start AND DATE <= :end
"""
_CURRENCIES_SQL = """
SELECT BID AS bid, CUR AS cur, FDATE AS fdate, TDATE AS tdate
FROM RISK_MODELS.dbo.{model}_IDTY
WHERE BID IN ({bids})
"""
_ID_MAP_DATES_SQL = """
SELECT DISTINCT date AS date
FROM public.barraid_jp
WHERE date > :start AND date <= :end
"""
_ID_MAP_SQL = """
SELECT date AS date, nri_code AS nri_code, bid AS bid
FROM public.barraid_jp
WHERE date IN ({dates})
"""


@dataclass(frozen=True)
class FactorList:
    """Barra のモデルのファクター一覧（``{model}_FAC`` テーブル）。

    Attributes:
        names: FCD（文字列）-> ファクター名。ファクター名は FAC からモデル名の接頭辞
            （``JPE4_`` 等）を除いたもの。並びは FGROUP・FCD の順。
        groups: グループ名 -> ファクター名のリスト。グループ名は FGROUP から先頭の番号を
            除いて小文字にしたもの（``1-Risk_Indices`` → ``risk_indices``）。
            ``risk_indices`` があれば、同じリストを ``style`` としても持つ
            （``factor_correlation`` の ``factor_group`` の既定値に合わせるため）。
    """

    names: dict[str, str]
    groups: dict[str, list[str]]

    @property
    def order(self) -> list[str]:
        """ファクター名を一覧の順に並べたリスト。"""
        return list(self.names.values())


# ---------------------------------------------------------------------------
# SQL の実行
# ---------------------------------------------------------------------------


def _sql(template: str, model: str, **parts: str) -> str:
    """SQL のテンプレートにモデル名と IN 句等を埋め込む。

    テーブル名はバインドパラメータにできないため文字列で埋め込む。SQL インジェクションを
    防ぐため、モデル名は ``BarraModel`` のいずれかでなければならない。

    Raises:
        ValueError: ``model`` が ``BarraModel`` にない場合。
    """
    if model not in get_args(BarraModel):
        raise ValueError(f"Unknown Barra model {model!r}; available: {get_args(BarraModel)}")
    sql = template.replace("{model}", model)
    for name, text in parts.items():
        sql = sql.replace("{" + name + "}", text)
    return sql


def _run(ctx: LoadContext, connection: str, sql: str, params: dict, label: str) -> pd.DataFrame:
    """SQL を実行し（結果はキャッシュする）、列名を小文字にして返す。"""
    df = execute_cached(connection, sql, params, ctx, label=label)
    return df.rename(columns=lambda c: str(c).lower())


def _run_in(
    ctx: LoadContext,
    connection: str,
    sql: str,
    label: str,
    lists: Mapping[str, tuple[Sequence, int]],
    columns: list[str],
    params: Mapping | None = None,
) -> pd.DataFrame:
    """IN 句に値のリストを並べる SQL を、リストを分割して実行し結果を縦に連結する。

    ``sql`` の ``{名前}`` を ``:名前_0, :名前_1, ...`` に置き換え、値をバインドパラメータで
    渡す。複数のリストがあれば、それぞれの分割の全組み合わせについて実行する。

    Args:
        ctx: 読み込みコンテキスト。
        connection: 接続先名。
        sql: ``{名前}`` の IN 句を含む SQL（モデル名は埋め込み済み）。
        label: ログに出すクエリの名前。
        lists: 名前 -> (値のリスト, 1回のクエリに並べる最大数)。
        columns: 結果が0行のときに返す DataFrame の列（小文字）。
        params: IN 句以外のバインドパラメータ。

    Returns:
        クエリ結果を連結したもの（列名は小文字）。いずれかのリストが空なら ``columns`` を
        持つ空の DataFrame。
    """
    chunked = {
        name: [list(values[i : i + size]) for i in range(0, len(values), size)]
        for name, (values, size) in lists.items()
    }
    frames = []
    for combo in itertools.product(*chunked.values()):
        query, bind = sql, dict(params or {})
        for name, chunk in zip(chunked, combo, strict=True):
            names = [f"{name}_{i}" for i in range(len(chunk))]
            query = query.replace("{" + name + "}", ", ".join(f":{n}" for n in names))
            bind.update(zip(names, chunk, strict=True))
        frames.append(_run(ctx, connection, query, bind, label))
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)


def _strip(values: pd.Series) -> pd.Series:
    """コード（BID・nri_code・FCD・通貨）を前後の空白を除いた文字列にする（欠損は欠損のまま）。"""
    values = to_asset_id(values)
    return values.str.strip().where(values.notna())


def _iso(key: str) -> str:
    """8桁の日付キーを ``yyyy-mm-dd`` にする（PostgreSQL の date 列と比較するため）。"""
    return f"{key[:4]}-{key[4:6]}-{key[6:]}"


# ---------------------------------------------------------------------------
# 日付の解決（目標日以前で最新のデータ）
# ---------------------------------------------------------------------------


def resolve_asof(
    available: Iterable[str], targets: Iterable[str], what: str, warnings: list[str]
) -> dict[str, str]:
    """各目標日について、その日以前で最新の利用可能な日付を選ぶ。

    目標日から ``ASOF_LOOKBACK_DAYS`` 日より前の日付しかない目標日は対応させず、
    ``warnings`` に警告文を追記する（引数のリストをその場で変更する）。

    Args:
        available: データのある日付（8桁）。
        targets: 目標日（8桁）。
        what: 警告文に入れるデータの名前。
        warnings: 警告文を追記するリスト。

    Returns:
        目標日 -> 使う日付。対応する日付がない目標日は含まない。
    """
    days = np.array(sorted(set(available)), dtype=str)
    out, missing = {}, []
    for target in sorted(set(targets)):
        i = int(np.searchsorted(days, target, side="right")) - 1
        if i >= 0 and days[i] >= shift_day(target, -ASOF_LOOKBACK_DAYS):
            out[target] = str(days[i])
        else:
            missing.append(target)
    if missing:
        warnings.append(
            f"No {what} within {ASOF_LOOKBACK_DAYS} days on or before {len(missing)} date(s) "
            f"(e.g. {missing[:3]}); treated as missing"
        )
    return out


def _trading_dates(model: str, targets: Iterable[str], ctx: LoadContext) -> list[str]:
    """最初の目標日の ``ASOF_LOOKBACK_DAYS`` 日前〜最後の目標日の Barra の営業日（8桁）。"""
    targets = sorted(set(targets))
    params = {
        "start": int(shift_day(targets[0], -ASOF_LOOKBACK_DAYS - 1)),
        "end": int(targets[-1]),
    }
    df = _run(ctx, BARRA_DB, _sql(_TRADING_DATES_SQL, model), params, f"{model}_D_FCTRTN dates")
    return to_date_key(df["dateymd"], 8).tolist()


# ---------------------------------------------------------------------------
# ファクター一覧・銘柄 ID の対応表
# ---------------------------------------------------------------------------


def _group_key(fgroup: str) -> str:
    """FGROUP をグループ名にする（``1-Risk_Indices`` → ``risk_indices``）。"""
    return re.sub(r"^\s*\d+\s*-\s*", "", fgroup).strip().lower()


def load_factor_list(model: str, ctx: LoadContext, warnings: list[str]) -> FactorList:
    """``{model}_FAC`` からファクター一覧を読む。

    ファクター名は FAC から ``MDL + "_"`` の接頭辞を除いたもの（接頭辞がなければそのまま）。
    名前が重複した場合は ``{名前}_{FCD}`` にして区別し、``warnings`` に警告文を追記する。

    Args:
        model: Barra のモデル。
        ctx: 読み込みコンテキスト。
        warnings: 警告文を追記するリスト。

    Returns:
        ``FactorList``。
    """
    df = _run(ctx, BARRA_DB, _sql(_FACTORS_SQL, model), {}, f"{model}_FAC")
    df = pd.DataFrame(
        {
            "fcd": _strip(df["fcd"]),
            "fgroup": df["fgroup"].astype(str).str.strip(),
            "group": df["fgroup"].astype(str).map(_group_key),
            "fac": df["fac"].astype(str).str.strip(),
            "mdl": df["mdl"].astype(str).str.strip(),
        }
    )
    prefix = df["mdl"] + "_"
    has_prefix = [f.startswith(p) for f, p in zip(df["fac"], prefix, strict=True)]
    df["name"] = [
        f[len(p) :] if ok else f for f, p, ok in zip(df["fac"], prefix, has_prefix, strict=True)
    ]
    dup = df["name"].duplicated(keep=False)
    if dup.any():
        warnings.append(
            f"{model}_FAC has duplicated factor names {sorted(df.loc[dup, 'name'].unique())}; "
            "renamed to '<name>_<FCD>'"
        )
        df.loc[dup, "name"] = df.loc[dup, "name"] + "_" + df.loc[dup, "fcd"]
    df = df.assign(_fcd=pd.to_numeric(df["fcd"], errors="coerce")).sort_values(["fgroup", "_fcd"])

    groups = {g: list(names) for g, names in df.groupby("group", sort=False)["name"]}
    if "risk_indices" in groups and "style" not in groups:
        groups["style"] = groups["risk_indices"]
    return FactorList(names=dict(zip(df["fcd"], df["name"], strict=True)), groups=groups)


def load_id_map(targets: Iterable[str], ctx: LoadContext, warnings: list[str]) -> pd.DataFrame:
    """目標日ごとの BID → nri_code の対応表を読む。

    各目標日について ``barraid_jp`` にある日付のうち目標日以前で最新のもの
    （``resolve_asof``）の対応を使う。1つの日付の中で BID または nri_code が重複する組は
    捨て、``warnings`` に警告文を追記する。

    Args:
        targets: 目標日（8桁）。
        ctx: 読み込みコンテキスト。
        warnings: 警告文を追記するリスト。

    Returns:
        columns=[_target, bid, asset_id] の DataFrame（RangeIndex）。
    """
    targets = sorted(set(targets))
    params = {
        "start": _iso(shift_day(targets[0], -ASOF_LOOKBACK_DAYS - 1)),
        "end": _iso(targets[-1]),
    }
    dates = _run(ctx, ID_MAP_DB, _ID_MAP_DATES_SQL, params, "barraid_jp dates")
    resolved = resolve_asof(to_date_key(dates["date"], 8), targets, "barraid_jp", warnings)
    used = sorted(set(resolved.values()))
    rows = _run_in(
        ctx,
        ID_MAP_DB,
        _ID_MAP_SQL,
        "barraid_jp",
        {"dates": ([_iso(d) for d in used], _DATES_PER_QUERY)},
        columns=["date", "nri_code", "bid"],
    )
    rows = pd.DataFrame(
        {
            _RESOLVED: to_date_key(rows["date"], 8),
            _BID: _strip(rows["bid"]),
            ASSET_ID: _strip(rows["nri_code"]),
        }
    ).dropna()
    rows = rows.drop_duplicates()
    ambiguous = rows.duplicated([_RESOLVED, _BID], keep=False) | rows.duplicated(
        [_RESOLVED, ASSET_ID], keep=False
    )
    if ambiguous.any():
        examples = rows.loc[ambiguous, [_RESOLVED, _BID, ASSET_ID]].head(3).to_dict("records")
        warnings.append(
            f"barraid_jp: dropped {int(ambiguous.sum())} rows whose bid / nri_code is not "
            f"one-to-one on the date (e.g. {examples})"
        )
        rows = rows[~ambiguous]
    keys = pd.DataFrame({_TARGET: list(resolved), _RESOLVED: list(resolved.values())}, dtype=str)
    return keys.merge(rows, on=_RESOLVED)[[_TARGET, _BID, ASSET_ID]]


def _attach_targets(df: pd.DataFrame, resolved: Mapping[str, str], targets: Mapping[str, str]):
    """Barra の日付（``date`` 列）のデータを、それを使うカレンダー行に対応づける。

    Args:
        df: ``date`` 列（Barra の日付）を持つ DataFrame。
        resolved: 目標日 -> 使う Barra の日付。
        targets: カレンダー行のキー -> 目標日。

    Returns:
        ``date`` 列をカレンダー行のキーに置き換え、``_target`` 列（目標日）を足した
        DataFrame。1つの Barra の日付を複数の行が使う場合は行ごとに複製する。
    """
    keys = pd.DataFrame({DATE: list(targets), _TARGET: list(targets.values())}, dtype=str)
    keys[_RESOLVED] = keys[_TARGET].map(resolved)
    keys = keys.dropna().astype(str)  # 空でも列の型を str に保つ（merge のため）
    return keys.merge(df.rename(columns={DATE: _RESOLVED}), on=_RESOLVED).drop(columns=_RESOLVED)


# ---------------------------------------------------------------------------
# リターン
# ---------------------------------------------------------------------------


def _daily_values(
    model: str, table: str, column: str, start: str, end: str, bids: list[str], ctx: LoadContext
) -> pd.DataFrame:
    """``{model}_{table}`` の ``column`` を ``start < DATE <= end`` について BID ごとに読む。

    Returns:
        columns=[date, bid, value] の DataFrame。date は8桁の文字列、value は float。
    """
    df = _run_in(
        ctx,
        BARRA_DB,
        _sql(_DAILY_VALUES_SQL, model, table=table, column=column),
        f"{model}_{table}",
        {"bids": (bids, _BIDS_PER_QUERY)},
        columns=["dateymd", "bid", "value"],
        params={"start": int(start), "end": int(end)},
    )
    return pd.DataFrame(
        {
            DATE: to_date_key(df["dateymd"], 8),
            _BID: _strip(df["bid"]),
            VALUE: pd.to_numeric(df["value"]).astype(float),
        }
    )


def _fx_returns(model: str, start: str, end: str, ctx: LoadContext) -> pd.DataFrame:
    """通貨ごとの日次の為替変化率（現地通貨 → USD）を求める。

    RATE（USD / 現地通貨 × 100）の、同じ通貨の前の日付からの変化率
    ``RATE_t / RATE_{t-1} - 1``。最初の日の変化率を求めるため、``start`` より
    ``ASOF_LOOKBACK_DAYS`` 日前から読む。

    Returns:
        columns=[date, cur, fx] の DataFrame（前の日付がない行は含まない）。

    Raises:
        ValueError: (DATE, CUR) が重複している場合。
    """
    params = {"start": int(shift_day(start, -ASOF_LOOKBACK_DAYS)), "end": int(end)}
    df = _run(ctx, BARRA_DB, _sql(_RATES_SQL, model), params, f"{model}_D_RATE")
    df = pd.DataFrame(
        {
            DATE: to_date_key(df["dateymd"], 8),
            "cur": _strip(df["cur"]),
            "rate": pd.to_numeric(df["rate"]).astype(float),
        }
    ).dropna(subset=["cur"])
    if df.duplicated([DATE, "cur"]).any():
        raise ValueError(f"{model}_D_RATE has duplicated (DATE, CUR) rows")
    df = df.dropna(subset=["rate"]).sort_values(DATE)
    df["fx"] = df["rate"] / df.groupby("cur")["rate"].shift(1) - 1
    return df.dropna(subset=["fx"])[[DATE, "cur", "fx"]]


def _currencies(model: str, keys: pd.DataFrame, bids: list[str], ctx: LoadContext) -> pd.DataFrame:
    """各 (date, bid) の通貨を ``{model}_IDTY`` の有効期間（FDATE〜TDATE、両端を含む）から引く。

    TDATE が空なら終わりのない期間とみなす。有効期間が重なる場合は FDATE が新しい方を使う。

    Args:
        model: Barra のモデル。
        keys: columns=[date, bid] の DataFrame（重複なし）。
        bids: 対象の BID。
        ctx: 読み込みコンテキスト。

    Returns:
        columns=[date, bid, cur] の DataFrame。通貨が決まらない組は含まない。
    """
    df = _run_in(
        ctx,
        BARRA_DB,
        _sql(_CURRENCIES_SQL, model),
        f"{model}_IDTY",
        {"bids": (bids, _BIDS_PER_QUERY)},
        columns=["bid", "cur", "fdate", "tdate"],
    )
    idty = pd.DataFrame(
        {
            _BID: _strip(df["bid"]),
            "cur": _strip(df["cur"]),
            "fdate": to_date_key(df["fdate"], 8).where(df["fdate"].notna()),
            "tdate": to_date_key(df["tdate"], 8).where(df["tdate"].notna(), _OPEN_END),
        }
    ).dropna(subset=[_BID, "cur", "fdate"])
    m = keys.merge(idty, on=_BID)
    m = m[(m["fdate"] <= m[DATE]) & (m[DATE] <= m["tdate"])]
    m = m.sort_values("fdate").drop_duplicates([DATE, _BID], keep="last")
    return m[[DATE, _BID, "cur"]]


def _to_usd(
    model: str,
    total: pd.DataFrame,
    start: str,
    end: str,
    bids: list[str],
    ctx: LoadContext,
    warnings: list[str],
) -> pd.DataFrame:
    """取引通貨建ての日次トータルリターンを USD 建てにする。

    ``(1 + r_local) * (1 + fx) - 1``。fx は BID の通貨（``_IDTY``）の、その日の為替変化率
    （``_D_RATE``）。通貨または為替が見つからない日は欠損にし（0 にはしない）、
    ``warnings`` に件数を追記する。

    Args:
        model: Barra のモデル。
        total: columns=[date, bid, value] の日次トータルリターン（小数）。
        start: 取得範囲の開始日（8桁、この日は含まない）。
        end: 取得範囲の終了日（8桁）。
        bids: 対象の BID。
        ctx: 読み込みコンテキスト。
        warnings: 警告文を追記するリスト。

    Returns:
        ``total`` と同じ列の DataFrame（value が USD 建て）。
    """
    keys = total[[DATE, _BID]].drop_duplicates()
    currencies = _currencies(model, keys, bids, ctx)
    fx = _fx_returns(model, start, end, ctx)
    out = total.merge(currencies, on=[DATE, _BID], how="left").merge(
        fx, on=[DATE, "cur"], how="left"
    )
    lost = int((out[VALUE].notna() & out["fx"].isna()).sum())
    if lost:
        warnings.append(
            f"{model}: {lost} daily total returns have no currency or FX rate; "
            "treated as missing in USD"
        )
    out[VALUE] = (1 + out[VALUE]) * (1 + out["fx"]) - 1
    return out[[DATE, _BID, VALUE]]


def load_returns_daily(
    source: BarraReturnsSource,
    start: str,
    end: str,
    boundaries: Sequence[str],
    ctx: LoadContext,
    warnings: list[str],
) -> pd.DataFrame:
    """``start < date <= end`` の日次リターンを、nri_code の long 形式で読む。

    ``source.series`` の kind が ``total`` なら DRTN、``specific`` なら SRTN を読み、% を小数に
    する。``currency: USD`` ならトータルリターンを USD 建てにする。BID は、その日を含む
    期間の境界日（``boundaries`` のうちその日以後で最初のもの、なければ最後のもの）の
    対応表で nri_code に変換し、対応のない BID の行は捨てる。

    Args:
        source: Barra のリターンソース。
        start: 取得範囲の開始日（8桁、この日は含まない）。
        end: 取得範囲の終了日（8桁）。
        boundaries: 期間の境界日（8桁、延長行の ``match_on`` の日付）。
        ctx: 読み込みコンテキスト。
        warnings: 警告文を追記するリスト。

    Returns:
        columns=[date, asset_id, <kind>...] の DataFrame。kind の列（``total`` /
        ``specific``）は ``source.series`` にあるものだけで、値は小数。date は8桁の文字列。
    """
    model = source.model
    boundaries = np.array(sorted(set(boundaries)), dtype=str)
    id_map = load_id_map(boundaries, ctx, warnings)
    bids = sorted(id_map[_BID].unique())
    kinds = sorted({s.kind for s in source.series.values()})
    tables = {"total": ("D_PRC", "DRTN"), "specific": ("D_SRTN", "SRTN")}

    daily = None
    for kind in kinds:
        table, column = tables[kind]
        df = _daily_values(model, table, column, start, end, bids, ctx)
        df[VALUE] = df[VALUE] * PERCENT
        if kind == "total" and source.currency == "USD":
            df = _to_usd(model, df, start, end, bids, ctx, warnings)
        df = df.rename(columns={VALUE: kind})
        daily = df if daily is None else daily.merge(df, on=[DATE, _BID], how="outer")

    # その日を含む期間の境界日の対応表で nri_code にする
    pos = np.searchsorted(boundaries, daily[DATE].to_numpy(dtype=str), side="left")
    daily[_TARGET] = boundaries[np.minimum(pos, len(boundaries) - 1)]
    daily = daily.merge(id_map, on=[_TARGET, _BID])
    return daily[[DATE, ASSET_ID, *kinds]].reset_index(drop=True)


# ---------------------------------------------------------------------------
# リスクモデル
# ---------------------------------------------------------------------------


def _factor_names(codes: pd.Series, factors: FactorList, what: str, warnings: list[str]):
    """FCD をファクター名にする。一覧にない FCD は欠損にし、``warnings`` に追記する。"""
    names = _strip(codes).map(factors.names)
    unknown = sorted(_strip(codes)[names.isna()].dropna().unique())
    if unknown:
        warnings.append(f"{what}: FCD {unknown[:5]} not in the factor list; ignored")
    return names


def load_exposures(
    model: str,
    targets: Mapping[str, str],
    factors: FactorList,
    ctx: LoadContext,
    warnings: list[str],
) -> pd.DataFrame:
    """カレンダー行ごとのファクターエクスポージャーを読む（wide 形式）。

    各行の目標日以前で最新の Barra の営業日のデータを使い、BID は目標日の対応表で
    nri_code にする。DB に行のない (銘柄, ファクター) は欠損のまま（0 で埋めない）。

    Args:
        model: Barra のモデル。
        targets: カレンダー行のキー -> 目標日（8桁）。
        factors: ファクター一覧。
        ctx: 読み込みコンテキスト。
        warnings: 警告文を追記するリスト。

    Returns:
        index=(date, asset_id)（ソート済み）、columns=ファクター名（一覧の順）の DataFrame。
    """
    resolved = resolve_asof(
        _trading_dates(model, targets.values(), ctx), targets.values(), f"{model} data", warnings
    )
    id_map = load_id_map(targets.values(), ctx, warnings)
    df = _run_in(
        ctx,
        BARRA_DB,
        _sql(_EXPOSURES_SQL, model),
        f"{model}_D_EXP",
        {
            "dates": ([int(d) for d in sorted(set(resolved.values()))], _DATES_PER_QUERY),
            "bids": (sorted(id_map[_BID].unique()), _BIDS_PER_QUERY),
        },
        columns=["dateymd", "bid", "fcd", "value"],
    )
    df = pd.DataFrame(
        {
            DATE: to_date_key(df["dateymd"], 8),
            _BID: _strip(df["bid"]),
            FACTOR: _factor_names(df["fcd"], factors, f"{model}_D_EXP", warnings),
            VALUE: pd.to_numeric(df["value"]).astype(float),
        }
    ).dropna(subset=[FACTOR])
    df = _attach_targets(df, resolved, targets).merge(id_map, on=[_TARGET, _BID])
    if df.empty:
        warnings.append(f"{model}_D_EXP: no exposures for the evaluation dates")
        return pd.DataFrame(index=pd.MultiIndex.from_arrays([[], []], names=[DATE, ASSET_ID]))
    if df.duplicated([DATE, ASSET_ID, FACTOR]).any():
        raise ValueError(f"{model}_D_EXP has duplicated (date, asset_id, factor) rows")
    wide = df.pivot(index=[DATE, ASSET_ID], columns=FACTOR, values=VALUE)
    wide = wide[[f for f in factors.order if f in wide.columns]]
    wide.columns.name = None
    return wide.sort_index()


def load_factor_covariance(
    model: str,
    targets: Mapping[str, str],
    factors: FactorList,
    ctx: LoadContext,
    warnings: list[str],
) -> pd.DataFrame:
    """カレンダー行ごとのファクター共分散行列を読む（年率、小数の2乗）。

    各行の目標日以前で最新の Barra の営業日のデータを使う。DB の値（年率の %²）を
    × 0.0001 する。(i, j) しかない組は (j, i) にも同じ値を入れて対称にする。

    Args:
        model: Barra のモデル。
        targets: カレンダー行のキー -> 目標日（8桁）。
        factors: ファクター一覧。
        ctx: 読み込みコンテキスト。
        warnings: 警告文を追記するリスト。

    Returns:
        index=(date, factor)、columns=ファクター名の DataFrame（日付ごとに正方行列を
        縦に積んだもの）。行・列ともファクター一覧の順。
    """
    resolved = resolve_asof(
        _trading_dates(model, targets.values(), ctx), targets.values(), f"{model} data", warnings
    )
    df = _run_in(
        ctx,
        BARRA_DB,
        _sql(_COVARIANCE_SQL, model),
        f"{model}_D_COV",
        {"dates": ([int(d) for d in sorted(set(resolved.values()))], _DATES_PER_QUERY)},
        columns=["dateymd", "fcd1", "fcd2", "value"],
    )
    what = f"{model}_D_COV"
    df = pd.DataFrame(
        {
            DATE: to_date_key(df["dateymd"], 8),
            "f1": _factor_names(df["fcd1"], factors, what, warnings),
            "f2": _factor_names(df["fcd2"], factors, what, warnings),
            VALUE: pd.to_numeric(df["value"]).astype(float) * PERCENT_SQUARED,
        }
    ).dropna(subset=["f1", "f2"])
    swapped = df.rename(columns={"f1": "f2", "f2": "f1"})
    df = pd.concat([df, swapped], ignore_index=True).drop_duplicates([DATE, "f1", "f2"])
    df = _attach_targets(df, resolved, targets)
    if df.empty:
        warnings.append(f"{model}_D_COV: no covariance for the evaluation dates")
        return pd.DataFrame(index=pd.MultiIndex.from_arrays([[], []], names=[DATE, FACTOR]))
    matrix = df.pivot(index=[DATE, "f1"], columns="f2", values=VALUE)
    order = [f for f in factors.order if f in matrix.columns]
    dates = matrix.index.get_level_values(DATE).unique().sort_values()
    matrix = matrix.reindex(pd.MultiIndex.from_product([dates, order], names=[DATE, FACTOR]))
    matrix = matrix[order]
    matrix.columns.name = FACTOR
    return matrix


def load_factor_returns_daily(
    model: str, start: str, end: str, factors: FactorList, ctx: LoadContext, warnings: list[str]
) -> pd.DataFrame:
    """``start < date <= end`` の日次ファクターリターンを読む（小数、long 形式）。

    Returns:
        columns=[date, factor, value] の DataFrame。date は8桁の文字列。
    """
    params = {"start": int(start), "end": int(end)}
    df = _run(ctx, BARRA_DB, _sql(_FACTOR_RETURNS_SQL, model), params, f"{model}_D_FCTRTN")
    out = pd.DataFrame(
        {
            DATE: to_date_key(df["dateymd"], 8),
            FACTOR: _factor_names(df["fcd"], factors, f"{model}_D_FCTRTN", warnings),
            VALUE: pd.to_numeric(df["value"]).astype(float),
        }
    )
    return out.dropna(subset=[FACTOR]).reset_index(drop=True)
