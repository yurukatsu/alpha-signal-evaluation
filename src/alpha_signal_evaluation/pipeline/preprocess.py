"""データ準備: カレンダー整列、期間リターン集約、フォワードリターン計算、ユニバース適用。

**時点合わせはすべてこのモジュールに閉じ込める。** metrics に届いた時点で、
同じ行（同じ ``date``）のシグナルとフォワードリターンは整列済みであることを保証し、
metrics 側では一切時点をずらさない。loaders（``data/loaders.py``）は「どのファイル /
クエリを読み、どのカレンダー行に対応づけるか」までを担い、期間集約やフォワード
リターンの計算はこのモジュールが行う。

用語:
    カレンダー行: カレンダーファイルの1行 = 1評価期間。すべてのデータは
        ``calendar.index`` 列（通常 Base_day）の値を ``date`` キーとして持つ。
        行は位置（0 始まりの行番号）で扱い、ずらしは常に「行単位」で行う。
    評価行: ``period.start <= index の値 <= period.end`` を満たす行。
        シグナルを評価する行で、DataBundle.dates になる。
    延長行: リターン取得のために評価行を前に1行（anchor）、後ろに最大ホライズン行
        だけ伸ばした行（カレンダーの範囲で切る）。
    規約（convention）: 期間リターンの行 t がどの期間のリターンを持つか。
        ``realized`` は「行 t-1 → 行 t」の実現リターン（デフォルト）、
        ``forward`` は「行 t → 行 t+1」のリターン。
    集約方法: 複数の日次・期間のリターンを束ねる方法。``total`` は複利
        （compound）、``specific`` は加算（sum）で、``PERIOD_AGGREGATION`` により
        kind から固定で決まる。ファクターリターンは常に加算。

処理の流れ（``build_bundle``）:
    1. カレンダーを読み、``make_plan`` で評価行と延長行を決める。
    2. シグナル・ユニバース・ベンチマーク・分類・リスクモデルのスナップショットを
       評価行について読む。
    3. リターンは延長行について読む。DB ソースは日次で取得して
       ``to_period_returns`` でカレンダー期間に集約し、ファイルソースはその行の
       期間リターンとしてそのまま使う。``forward_returns`` でホライズンごとの
       フォワードリターンにして評価行に reindex する。
    4. 銘柄の一致率などの警告を集め、DataBundle にまとめる。

欠損の扱い:
    欠損は 0 埋めしない。期間内の日次リターンが全て欠損の期間、ホライズンの途中に
    欠損を含むフォワードリターン、カレンダー末尾を超えるフォワードリターンは
    すべて NaN になる（期間内の一部の日だけが欠損の場合はその日を除いて集約する）。
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import (
    BarraReturnsSource,
    BarraRiskModelConfig,
    ClassificationDBSource,
    ClassificationFileSource,
    Convention,
    DBSource,
    EvaluationConfig,
    FileSource,
    ReturnKind,
    ReturnsDBSource,
    RiskModelConfig,
)
from ..data import barra
from ..data.bundle import DataBundle, RiskModelData
from ..data.calendar import Calendar, check_increasing, load_calendar
from ..data.columns import ASSET_ID, CATEGORY, DATE, FACTOR, VALUE, WEIGHT
from ..data.loaders import (
    LoadContext,
    describe_source,
    load_daily,
    load_snapshot,
    period_end_day,
    period_start_day,
    shift_day,
)
from ..io.cache import ParquetCache

logger = logging.getLogger(__name__)

# 期間内（日次→期間）の集約。kind から固定で決まり、ユーザーには選ばせない
PERIOD_AGGREGATION: dict[ReturnKind, str] = {"total": "compound", "specific": "sum"}


# ---------------------------------------------------------------------------
# 評価対象の行
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationPlan:
    """評価期間の行と、リターン取得のために前後へ延長した行（位置はカレンダーの行番号）。

    位置はすべてカレンダーの行番号（0 始まり。``calendar.frame`` の RangeIndex）。
    カレンダーの index 列は昇順・重複なしが保証されているため、``make_plan`` で
    作った ``eval_pos`` は連続した行番号になる。

    延長の考え方（``h_max = max(horizons)``、``first`` / ``last`` は最初 / 最後の評価行）:

    - 前に1行（``anchor``）: realized 規約の行 ``first`` の期間リターン
      （行 ``first-1`` → ``first``）の起点。DB の日次取得範囲の開始もこの行で決まる。
    - 後ろに ``h_max`` 行: realized 規約で行 ``last`` のシグナルに対し
      行 ``last+1`` 〜 ``last+h_max`` の期間リターンを束ねるため。

    どちらもカレンダーの範囲で切り、足りない後ろの行数は ``missing_tail`` で分かる。
    延長はホライズンの最大値だけで決まり、規約（realized / forward）にはよらない。

    Attributes:
        calendar: 評価の時間軸となるカレンダー。
        eval_pos: 評価行の位置（昇順の int 配列）。``make_plan`` 経由なら空でない。
        horizons: 評価するホライズン（行数、正の整数、昇順）。1つ以上必要
            （``ext_pos`` などで最大値を使うため）。
    """

    calendar: Calendar
    eval_pos: np.ndarray
    horizons: list[int]

    @property
    def first(self) -> int:
        """最初の評価行の位置（カレンダーの行番号）。"""
        return int(self.eval_pos[0])

    @property
    def last(self) -> int:
        """最後の評価行の位置（カレンダーの行番号）。"""
        return int(self.eval_pos[-1])

    @property
    def anchor(self) -> int | None:
        """最初の評価行の1行前（realized の最初の期間の起点）。

        最初の評価行がカレンダーの先頭（行 0）なら None。None のとき延長行は
        ``first`` から始まり、DB ソースでは行 ``first`` の realized 期間リターンが
        起点なしの欠損になる。なお評価行のフォワードリターンは、realized では
        行 t+1 以降、forward では行 t 以降の期間リターンしか使わない。
        """
        return self.first - 1 if self.first > 0 else None

    @property
    def ext_pos(self) -> np.ndarray:
        """期初の1行前から期末の最大ホライズン行先まで（カレンダーの範囲で切る）。

        ``anchor``（None なら ``first``）から ``min(last + max(horizons), 末尾行)`` まで
        の連続した行番号（両端を含む int 配列）。期間リターンはこの行について読む。
        """
        start = self.first if self.anchor is None else self.anchor
        end = min(self.last + max(self.horizons), len(self.calendar) - 1)
        return np.arange(start, end + 1)

    @property
    def missing_tail(self) -> int:
        """カレンダーが足りずに取得できないホライズン分の行数。

        ``last + max(horizons)`` がカレンダーの最終行を超える行数（0 以上）。
        正のとき、評価期間末尾の行の長いホライズンのフォワードリターンは欠損になる。
        """
        return max(self.last + max(self.horizons) - (len(self.calendar) - 1), 0)

    @property
    def tail_warning(self) -> str | None:
        """カレンダー末尾が最大ホライズンに足りない場合の警告文（英語）。

        ``missing_tail`` が 0 なら None。``build_bundle`` が警告リストの先頭に入れる。
        """
        if not self.missing_tail:
            return None
        return (
            f"Calendar ends {self.missing_tail} row(s) short of the longest horizon; "
            "forward returns at the end of the period are missing"
        )

    @property
    def eval_rows(self) -> pd.DataFrame:
        """評価行のカレンダー行（全列 str、index はカレンダーの行番号）。

        loaders に「どの行のデータを読むか」を渡すのに使う。
        """
        return self.calendar.rows(self.eval_pos)

    @property
    def ext_rows(self) -> pd.DataFrame:
        """延長行のカレンダー行（全列 str、index はカレンダーの行番号）。"""
        return self.calendar.rows(self.ext_pos)

    @property
    def eval_keys(self) -> list[str]:
        """評価行の ``calendar.index`` 列の値（昇順の文字列リスト）。

        DataBundle.dates になり、シグナル等の ``date`` キーと一致する。
        """
        return self.eval_rows[self.calendar.index].tolist()

    @property
    def eval_index(self) -> pd.Index:
        """``eval_keys`` を名前 ``date`` の pd.Index にしたもの。

        フォワードリターンを評価行に reindex する先として使う。
        """
        return pd.Index(self.eval_keys, name=DATE)

    @property
    def ext_keys(self) -> list[str]:
        """延長行の ``calendar.index`` 列の値（昇順の文字列リスト）。"""
        return self.ext_rows[self.calendar.index].tolist()

    @property
    def optional_keys(self) -> set[str]:
        """延長行のうち評価期間外の行（ファイルがなくても欠損として扱う）。

        anchor 行と末尾の延長行のキー。日付パーティションファイルの読み込みで
        これらの行のファイルが存在しなくてもエラーにせず、その行を欠損として扱う
        （評価行のファイルがない場合は FileNotFoundError）。
        """
        return set(self.ext_keys) - set(self.eval_keys)


def make_plan(
    calendar: Calendar, start: str | None, end: str | None, horizons: list[int]
) -> EvaluationPlan:
    """カレンダーと評価期間・ホライズンから EvaluationPlan を作る。

    評価行は ``start <= calendar.index の値 <= end``（文字列比較、両端を含む）を満たす
    行。``start`` / ``end`` が None ならその側は制限しない。``horizons`` は昇順に
    並べ替えて保持する（重複や正の整数であることの検査は config 側で行う）。

    Args:
        calendar: 評価の時間軸となるカレンダー。
        start: 評価期間の開始キー（含む）。None ならカレンダーの先頭から。
        end: 評価期間の終了キー（含む）。None ならカレンダーの末尾まで。
        horizons: ホライズン（行数）。1つ以上必要。

    Returns:
        評価行と延長行を表す EvaluationPlan。

    Raises:
        ValueError: 期間内にカレンダー行が1つもない場合。
    """
    eval_pos = calendar.positions(start, end)
    if len(eval_pos) == 0:
        raise ValueError(
            f"No calendar rows in period start={start}, end={end} "
            f"(calendar {calendar.index}: {calendar.keys.iloc[0]}..{calendar.keys.iloc[-1]})"
        )
    return EvaluationPlan(calendar=calendar, eval_pos=eval_pos, horizons=sorted(horizons))


# ---------------------------------------------------------------------------
# リターンの時点合わせ
# ---------------------------------------------------------------------------


def to_period_returns(
    daily: pd.DataFrame,
    boundaries: pd.Series,
    convention: Convention,
    method: str,
) -> pd.DataFrame:
    """日次リターン（index=yyyymmdd, wide）をカレンダー期間に集約する。

    ``boundaries`` は index=calendar.index の値、values=期間の境界となる日付（例: Trading_day）。

    - realized: 行 i の期間は ``(boundaries[i-1], boundaries[i]]``。最初の行は起点がないため欠損
    - forward:  行 i の期間は ``[boundaries[i], boundaries[i+1])``。最後の行は終点がないため欠損

    ``method`` は ``compound``（複利）または ``sum``（加算）。期間内が全て欠損なら欠損のまま
    （0% にしない）。

    日付の比較は 8 桁文字列の辞書順で行う（``boundaries`` は昇順であること）。
    どの期間にも入らない日（realized では ``boundaries[0]`` 以前と最後の境界より後、
    forward では ``boundaries[0]`` より前と最後の境界以降）は捨てる。realized は
    左開右閉、forward は左閉右開なので、``boundaries[i]`` の日の日次リターンは、
    realized ではそこで終わる行 i の期間、forward ではそこから始まる行 i の期間に入る。

    欠損の扱い:
        - 期間内の一部の日だけが欠損: その日を除いて集約する（実質 0% として扱う）。
        - 期間内の日が全て欠損、または期間内に日次データが1日もない: NaN。
        - compound は ``log1p`` の和を ``expm1`` で戻すため、-1 未満の日次値は NaN、
          -1 ちょうどはその期間を -1 にする。

    Args:
        daily: 日次リターン。index=日付（8 桁の文字列 yyyymmdd、昇順でなくてよい）、
            columns=銘柄（または factor）。
        boundaries: index=カレンダー行のキー（calendar.index の値）、
            values=期間の境界日（8 桁）。延長行の連続した行であること。
        convention: ``realized`` または ``forward``。
        method: ``compound`` または ``sum``。

    Returns:
        期間リターン。index=``boundaries.index``（名前 ``date``、全行を含む）、
        columns=``daily`` の列。行 i が上記の期間 i の集約値。

    Raises:
        ValueError: ``method`` が ``compound`` / ``sum`` 以外の場合。

    Examples:
        境界 ``[0131, 0228, 0331]``（行キー ``[20200131, 20200229, 20200331]``）、
        日次 ``0128, 0131, 0203, 0214, 0228, 0302, 0331`` の場合::

            realized: 20200131 = NaN（0128・0131 は捨てる）
                      20200229 = 0203 + 0214 + 0228
                      20200331 = 0302 + 0331
            forward:  20200131 = 0131 + 0203 + 0214（0128 は捨てる）
                      20200229 = 0228 + 0302
                      20200331 = NaN（0331 は捨てる）
    """
    edges = np.asarray(boundaries, dtype=str)
    days = np.asarray(daily.index, dtype=str)
    if convention == "realized":
        pos = np.searchsorted(edges, days, side="left")
        valid = (pos >= 1) & (pos < len(edges))
    else:
        pos = np.searchsorted(edges, days, side="right") - 1
        valid = (pos >= 0) & (pos < len(edges) - 1)
    x = daily[valid]
    keys = boundaries.index.to_numpy()[pos[valid]]
    if method == "compound":
        out = np.expm1(np.log1p(x).groupby(keys).sum(min_count=1))
    elif method == "sum":
        out = x.groupby(keys).sum(min_count=1)
    else:
        raise ValueError(f"Unknown aggregation method: {method!r}")
    out = out.reindex(boundaries.index)
    out.index.name = DATE
    return out


def forward_returns(
    period_ret: pd.DataFrame,
    horizons: list[int],
    convention: Convention,
    method: str,
) -> dict[int, pd.DataFrame]:
    """期間リターン（行=カレンダー行、欠けのない連続した行）からフォワードリターンを計算する。

    - realized: 行 t のリターンは「行 t-1 → t」の実現リターン。行 t のシグナルに対し
      行 t+1 〜 t+h のリターンを束ねる
    - forward:  行 t のリターンは「行 t → t+1」のリターン。行 t 〜 t+h-1 を束ねる

    途中に欠損がある場合は欠損のまま（min_periods=h）。

    どちらの規約でも、行 t の結果は「行 t の時点から h 行先の時点まで」のリターンで、
    行 t のシグナルと同じ行に置かれる。ずらしは index の値ではなく行の位置で行うため、
    ``period_ret`` は昇順で欠けのない連続したカレンダー行でなければならない
    （index の連続性は検査しない）。束ねる行がフレームの末尾を超える行
    （realized は最後の h 行、forward は最後の h-1 行）は NaN になる。

    束ね方は ``method`` による: ``compound`` は ``log1p`` の和を ``expm1`` で戻す複利、
    ``sum`` は単純な和。0 埋めはしないので、束ねる h 行のうち1つでも欠損なら NaN。

    Args:
        period_ret: 期間リターン。index=date（カレンダー行のキー）、columns=asset_id
            （ファクターリターンなら factor）。
        horizons: ホライズン（行数、正の整数）。
        convention: ``period_ret`` の規約（``realized`` / ``forward``）。
        method: ``compound`` または ``sum``。

    Returns:
        ホライズン -> フォワードリターン。各値は ``period_ret`` と同じ index・columns の
        wide 形式（reindex はしないので、評価行への絞り込みは呼び出し側で行う）。

    Raises:
        ValueError: ``method`` が ``compound`` / ``sum`` 以外の場合。

    Examples:
        期間リターン ``r0 .. r5``（行 0〜5）、``h=2``、``method="sum"`` の場合::

            realized: fwd[0] = r1 + r2, fwd[3] = r4 + r5, fwd[4] = fwd[5] = NaN
            forward:  fwd[0] = r0 + r1, fwd[4] = r4 + r5, fwd[5] = NaN

        realized で r2 が欠損なら fwd[0]・fwd[1] は NaN（r1・r3 があっても 0 埋めしない）。
    """
    if method == "compound":
        x = np.log1p(period_ret)
    elif method == "sum":
        x = period_ret
    else:
        raise ValueError(f"Unknown aggregation method: {method!r}")
    offset = 0 if convention == "forward" else 1
    shifted = x.shift(-offset)
    out = {}
    for h in horizons:
        summed = shifted.rolling(h, min_periods=h).sum().shift(-(h - 1))
        out[h] = np.expm1(summed) if method == "compound" else summed
    return out


def _to_wide(df: pd.DataFrame, columns: str, values: str, index: list[str]) -> pd.DataFrame:
    """long 形式の ``df`` を index=date、columns=``columns`` 列の値の wide 形式にする。

    値は float に変換し、``index`` で reindex する（``index`` にない日付の行は捨て、
    データのない日付は全 NaN の行になる）。columns の名前は ``columns`` にする。

    Raises:
        ValueError: (date, ``columns``) の組が重複している場合、または値を float に
            変換できない場合。
    """
    dup = df.duplicated([DATE, columns])
    if dup.any():
        examples = df.loc[dup, [DATE, columns]].head(5).to_dict("records")
        raise ValueError(f"Duplicated ({DATE}, {columns}) rows: {examples}")
    wide = df.pivot(index=DATE, columns=columns, values=values).astype(float)
    wide = wide.reindex(pd.Index(index, name=DATE))
    wide.columns.name = columns
    return wide


def _db_range(plan: EvaluationPlan, match_on: str) -> tuple[str, str]:
    """DB から取得する日次データの範囲 ``(start, end]``（ホライズン分延長済み）。

    match_on が月（6桁）の場合は、その月の初日〜月末日として扱う。

    - start: anchor 行があれば、その行の ``match_on`` の値が表す期間の最終日
      （8 桁ならその日、6 桁の月なら月末日）。anchor がない（最初の評価行が
      カレンダー先頭）場合は、最初の評価行の期間の初日の前日。
    - end: 延長行の最後の行の ``match_on`` の値が表す期間の最終日。

    ``load_daily`` は ``start < date <= end`` で取得するため start の日自体は含まない。
    例えば月次カレンダーで anchor が ``201912``、延長行の最後が ``202101`` なら
    ``("20191231", "20210131")`` になる。

    Args:
        plan: 評価計画。
        match_on: 境界に使うカレンダーの列名（8 桁の日付または 6 桁の月）。

    Returns:
        ``(start, end)``。いずれも 8 桁の yyyymmdd 文字列。
    """
    values = plan.calendar.frame[match_on]
    if plan.anchor is not None:
        start = period_end_day(values.iloc[plan.anchor])
    else:
        start = shift_day(period_start_day(values.iloc[plan.first]), -1)
    return start, period_end_day(values.iloc[int(plan.ext_pos[-1])])


def _period_boundaries(plan: EvaluationPlan, match_on: str) -> pd.Series:
    """延長行の期間の境界日。index=延長行のキー（名前 ``date``）、values=8桁の日付。

    ``match_on`` 列の値を ``period_end_day`` で日付にする（6 桁の月なら月末日が境界になり、
    realized の期間はちょうどその月の初日〜月末日になる）。

    Raises:
        ValueError: ``match_on`` 列に空値・重複・非昇順がある場合。
    """
    rows = plan.ext_rows
    boundaries = pd.Series(
        rows[match_on].to_numpy(), index=pd.Index(rows[plan.calendar.index], name=DATE)
    )
    check_increasing(boundaries, match_on)
    return boundaries.map(period_end_day)


def _aggregate_daily(
    daily: pd.DataFrame,
    boundaries: pd.Series,
    columns: str,
    values: Mapping[str, tuple[str, str]],
    convention: Convention,
) -> dict[str, pd.DataFrame]:
    """日次の long 形式を ``values`` の列ごとに wide にし、``to_period_returns`` で集約する。"""
    daily_index = sorted(daily[DATE].unique())
    return {
        name: to_period_returns(
            _to_wide(daily, columns, col, daily_index), boundaries, convention, method
        )
        for name, (col, method) in values.items()
    }


def _load_period_frames(
    source: FileSource | DBSource | BarraReturnsSource,
    plan: EvaluationPlan,
    ctx: LoadContext,
    columns: str,
    values: Mapping[str, str],
    required_roles: set[str],
    convention: Convention,
    warnings: list[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """延長行の期間データを読み、values の各列を wide（行=延長行）に変換する。

    DB ソースは日次で取得し、``values`` ごとの集約方法（compound / sum）で期間に集約する。

    - DBSource: ``_db_range`` の範囲の日次データを1クエリで取得し、延長行の
      ``match_on`` 列の値を境界として ``to_period_returns`` で集約する。境界は
      ``period_end_day`` で日付にするため、6 桁の月なら月末日が境界になり、
      realized の期間はちょうどその月の初日〜月末日になる。
    - BarraReturnsSource: DBSource と同じ範囲・境界で、``data.barra.load_returns_daily`` が
      nri_code・小数に変換した日次データを集約する。
    - FileSource: 延長行についてスナップショットとして読み、そのまま wide にする。
      ファイルの値が既にその行の期間リターン（``convention`` に従う）である前提で、
      集約はしない（``values`` の集約方法は使わない）。評価期間外の行
      （``plan.optional_keys``）のファイルがなければ、その行は欠損になる。

    Args:
        source: 読み込むソース（リターン系列またはファクターリターン）。
        plan: 評価計画（延長行を使う）。
        ctx: 読み込みコンテキスト。
        columns: wide の列にする role 名（銘柄なら ``asset_id``、ファクターなら ``factor``）。
        values: 出力名 -> ``(値の role 名, 集約方法)``。集約方法は ``compound`` / ``sum``。
        required_roles: ソースに必須の role 名。
        convention: DB の日次→期間集約で使う規約（``realized`` / ``forward``）。
        warnings: 警告文を追記するリスト（Barra ソースで使う）。

    Returns:
        出力名 -> 期間データ。index=date（延長行のキー、延長行と同じ順序・同じ数）、
        columns=``columns`` の値。データのない行は全 NaN。

    Raises:
        ValueError: DB の ``match_on`` 列に空値・重複・非昇順がある場合、
            (date, ``columns``) が重複する場合。
    """
    keys = plan.ext_keys
    if isinstance(source, DBSource | BarraReturnsSource):
        start, end = _db_range(plan, source.match_on)
        boundaries = _period_boundaries(plan, source.match_on)
        if isinstance(source, BarraReturnsSource):
            daily = barra.load_returns_daily(
                source, start, end, boundaries.tolist(), ctx, [] if warnings is None else warnings
            )
        else:
            daily = load_daily(source, start, end, ctx, required_roles)
        return _aggregate_daily(daily, boundaries, columns, values, convention)
    df = load_snapshot(source, plan.ext_rows, ctx, required_roles, plan.optional_keys)
    return {name: _to_wide(df, columns, col, keys) for name, (col, _) in values.items()}


# ---------------------------------------------------------------------------
# DataBundle の組み立て
# ---------------------------------------------------------------------------


@dataclass
class PreparedData:
    """``build_bundle`` の結果。DataBundle と、それを作った評価計画・警告をまとめる。

    Attributes:
        bundle: metrics に渡す時点合わせ済みのデータ。
        plan: 使用した EvaluationPlan（評価行・延長行の確認用）。
        warnings: データ準備中の警告文（英語）。カレンダー末尾の不足、リスクモデルの
            ``specific_return`` 未設定、シグナルとリターンの銘柄の一致率が低い場合など。
            いずれも ``logger.warning`` にも出力済み。
    """

    bundle: DataBundle
    plan: EvaluationPlan
    warnings: list[str] = field(default_factory=list)


def _load_signals(cfg: EvaluationConfig, plan: EvaluationPlan, ctx: LoadContext) -> pd.DataFrame:
    """config の全シグナルソースを評価行について読み、1つの DataFrame にまとめる。

    ソースごとに ``values``（シグナル名 -> 実カラム名）の列を選んでシグナル名に付け替え、
    数値（float）に変換する。複数ソースは (date, asset_id) で外部結合するため、
    あるソースにしかない銘柄では他のソースのシグナルが NaN になる。評価行の
    ファイルがない場合は loaders から FileNotFoundError が送出される。

    Returns:
        index=(date, asset_id)（ソート済み）、columns=シグナル名（config の順）。

    Raises:
        ValueError: 指定した列がソースにない場合、数値に変換できない値がある場合。
    """
    frames = []
    for source in cfg.data.signals:
        df = load_snapshot(source, plan.eval_rows, ctx, {ASSET_ID})
        value_map = source.value_map
        missing = set(value_map.values()) - set(df.columns)
        if missing:
            raise ValueError(
                f"Signal columns {sorted(missing)} not found in {describe_source(source)}. "
                f"Available: {list(df.columns)}"
            )
        values = df.set_index([DATE, ASSET_ID])[list(value_map.values())]
        values.columns = list(value_map)
        try:
            frames.append(values.apply(pd.to_numeric, errors="raise").astype(float))
        except ValueError as e:
            raise ValueError(f"Non-numeric signal values in {describe_source(source)}: {e}") from e
    signals = pd.concat(frames, axis=1, join="outer") if len(frames) > 1 else frames[0]
    return signals.sort_index()


def _load_classification(
    source: ClassificationFileSource | ClassificationDBSource,
    plan: EvaluationPlan,
    ctx: LoadContext,
) -> pd.DataFrame:
    """index=(date, asset_id)、columns=[category, ...] に揃える。

    one_hot なら 0/1 のダミー列から、1 が立っている列名をカテゴリとして復元する。

    評価行についてスナップショットとして読む。

    - one_hot でない場合: ``asset_id`` と ``category`` の role が必須。それ以外の列も
      そのまま残す。
    - one_hot の場合: (date, asset_id) 以外の全列を数値のダミー変数とみなし、
      欠損を 0、0 以外を「立っている」と扱う。立っている列がちょうど1つならその列名が
      カテゴリ、1つもなければ欠損。返り値の列は ``category`` だけ。

    Returns:
        index=(date, asset_id)（ソート済み）、columns=[category, ...]。

    Raises:
        ValueError: one_hot で1銘柄に複数のカテゴリが立っている場合、またはダミー列に
            数値に変換できない値がある場合。
    """
    if not source.one_hot:
        df = load_snapshot(source, plan.eval_rows, ctx, {ASSET_ID, CATEGORY})
        return df.set_index([DATE, ASSET_ID]).sort_index()
    df = load_snapshot(source, plan.eval_rows, ctx, {ASSET_ID}).set_index([DATE, ASSET_ID])
    dummies = df.apply(pd.to_numeric, errors="raise")
    flags = dummies.fillna(0) != 0
    n_flags = flags.sum(axis=1)
    if (n_flags > 1).any():
        examples = n_flags[n_flags > 1].index[:5].tolist()
        raise ValueError(f"one_hot classification has multiple categories per asset: {examples}")
    category = flags.idxmax(axis=1).where(n_flags == 1)  # どれも立っていなければ欠損
    return category.rename(CATEGORY).to_frame().sort_index()


def _load_barra_risk_model(
    name: str,
    model: BarraRiskModelConfig,
    plan: EvaluationPlan,
    ctx: LoadContext,
    warnings: list[str],
) -> RiskModelData:
    """Barra のリスクモデル（``source: barra``）を読み、RiskModelData にまとめる。

    - exposures / factor_covariance: 評価行ごとに、``match_on`` の日付（6桁の月なら月末日）
      以前で最新の Barra のデータ（``data.barra``）。
    - factor_returns: 延長行の範囲の日次ファクターリターンを ``match_on`` の境界で期間に
      加算で集約し、``model.convention`` に従って加算のフォワードリターンにする
      （``RiskModelConfig`` の DB ソースと同じ規則）。評価行に reindex する。

    ``factor_groups`` は config で指定があればそれを、なければ ``{model}_FAC`` の FGROUP から
    作ったもの（``FactorList.groups``）を使う。``specific_return`` が未設定なら警告する。

    Args:
        name: リスクモデル名（警告文に使う）。
        model: Barra のリスクモデルの config。
        plan: 評価計画。
        ctx: 読み込みコンテキスト。
        warnings: 警告文を追記するリスト。

    Returns:
        読み込んだ RiskModelData（``specific_risk`` は常に None）。
    """
    factors = barra.load_factor_list(model.model, ctx, warnings)
    rows = plan.eval_rows
    targets = dict(
        zip(rows[plan.calendar.index], rows[model.match_on].map(period_end_day), strict=True)
    )
    exposures = factor_covariance = None
    forward_factor = {}
    if "exposures" in model.components:
        exposures = barra.load_exposures(model.model, targets, factors, ctx, warnings)
    if "factor_covariance" in model.components:
        factor_covariance = barra.load_factor_covariance(
            model.model, targets, factors, ctx, warnings
        )
    if "factor_returns" in model.components:
        start, end = _db_range(plan, model.match_on)
        boundaries = _period_boundaries(plan, model.match_on)
        daily = barra.load_factor_returns_daily(model.model, start, end, factors, ctx, warnings)
        (period,) = _aggregate_daily(
            daily, boundaries, FACTOR, {"factor_returns": (VALUE, "sum")}, model.convention
        ).values()
        period = period[[f for f in factors.order if f in period.columns]]
        fwd = forward_returns(period, plan.horizons, model.convention, "sum")
        forward_factor = {h: f.reindex(plan.eval_index) for h, f in fwd.items()}
    if model.specific_return is None:
        warnings.append(f"risk_models.{name}.specific_return is not set")
    return RiskModelData(
        exposures=exposures,
        factor_covariance=factor_covariance,
        forward_factor_returns=forward_factor,
        specific_return=model.specific_return,
        factor_groups=model.factor_groups or factors.groups,
    )


def _load_risk_model(
    name: str,
    model: RiskModelConfig | BarraRiskModelConfig,
    plan: EvaluationPlan,
    ctx: LoadContext,
    warnings: list[str],
) -> RiskModelData:
    """1つのリスクモデルの各コンポーネントを読み、RiskModelData にまとめる。

    ``source: barra`` のモデルは ``_load_barra_risk_model`` に任せる。以下はそれ以外の場合。

    - exposures: 評価行のスナップショット。数値列だけをファクターとして残す
      （銘柄名などの文字列列は除く）。index=(date, asset_id)、columns=ファクター。
    - factor_covariance: 評価行のスナップショット。index=date、列はソースのまま。
    - specific_risk: 評価行のスナップショット。index=(date, asset_id)。
    - factor_returns: 延長行の期間データ（index=date、columns=factor）を読み
      （DB なら日次を加算で期間に集約）、ソースの convention に従って銘柄の
      フォワードリターンと同じ規則で加算のフォワードリターンにする。評価行に
      reindex して ``forward_factor_returns[h]`` に入れる。

    config で未設定のコンポーネントは None（factor_returns は空 dict）のまま。
    ``specific_return`` が未設定なら ``warnings`` に警告文を追記する
    （引数のリストをその場で変更する）。

    Args:
        name: リスクモデル名（警告文に使う）。
        model: リスクモデルの config。
        plan: 評価計画。
        ctx: 読み込みコンテキスト。
        warnings: 警告文を追記するリスト。

    Returns:
        読み込んだ RiskModelData。
    """
    if isinstance(model, BarraRiskModelConfig):
        return _load_barra_risk_model(name, model, plan, ctx, warnings)
    exposures = factor_covariance = specific_risk = None
    forward_factor = {}
    if model.exposures is not None:
        df = load_snapshot(model.exposures, plan.eval_rows, ctx, {ASSET_ID})
        exposures = df.set_index([DATE, ASSET_ID]).select_dtypes("number").sort_index()
    if model.factor_covariance is not None:
        df = load_snapshot(model.factor_covariance, plan.eval_rows, ctx)
        factor_covariance = df.set_index(DATE)
    if model.specific_risk is not None:
        df = load_snapshot(model.specific_risk, plan.eval_rows, ctx, {ASSET_ID})
        specific_risk = df.set_index([DATE, ASSET_ID]).sort_index()
    if model.factor_returns is not None:
        source = model.factor_returns
        # ファクターリターンは「日付×ファクター」。日次→期間の集約は加算
        (period,) = _load_period_frames(
            source,
            plan,
            ctx,
            columns=FACTOR,
            values={"factor_returns": (VALUE, "sum")},
            required_roles={FACTOR, VALUE},
            convention=source.convention,
        ).values()
        fwd = forward_returns(period, plan.horizons, source.convention, "sum")
        forward_factor = {h: f.reindex(plan.eval_index) for h, f in fwd.items()}
    if model.specific_return is None:
        warnings.append(f"risk_models.{name}.specific_return is not set")
    return RiskModelData(
        exposures=exposures,
        factor_covariance=factor_covariance,
        specific_risk=specific_risk,
        forward_factor_returns=forward_factor,
        specific_return=model.specific_return,
        factor_groups=model.factor_groups,
    )


def _coverage_warnings(
    signals: pd.DataFrame, fwd: dict[str, dict[int, pd.DataFrame]], horizon: int
) -> list[str]:
    """シグナルとリターンの銘柄がほとんど一致しない場合に警告する（銘柄コードの書式違い等）。

    リターン系列ごとに、ホライズン ``horizon`` のフォワードリターンが欠損でない
    (date, asset_id) に含まれるシグナル行の割合を計算し、50% 未満なら警告文を作る。
    シグナルが0行なら割合 0 として警告する。末尾のフォワードリターンの欠損も
    「一致しない」側に数える。

    Args:
        signals: index=(date, asset_id) のシグナル。
        fwd: 系列名 -> ホライズン -> フォワードリターン（index=date、columns=asset_id）。
        horizon: 判定に使うホライズン（``build_bundle`` は最短を渡す）。

    Returns:
        警告文（英語）のリスト。問題がなければ空。
    """
    warnings = []
    for name, by_h in fwd.items():
        values = by_h[horizon].stack()
        matched = signals.index.isin(values.index[values.notna()]).mean() if len(signals) else 0.0
        if matched < 0.5:
            warnings.append(
                f"Only {matched:.0%} of signal rows have {name!r} forward returns (h={horizon}); "
                "check that asset_id formats match across sources"
            )
    return warnings


def build_bundle(cfg: EvaluationConfig) -> PreparedData:
    """config の data セクションから DataBundle を組み立てる。

    calendar / period / horizons / data / universe / benchmark / returns / risk_models を
    使う。手順:

    1. カレンダーを読み、``make_plan`` で評価行・延長行を決める。カレンダー末尾が
       最大ホライズンに足りなければ警告する。
    2. ``LoadContext``（DB 接続とキャッシュ。終了時に DB 接続を閉じる）の中で読む。

       - シグナル: 評価行。
       - ユニバース（任意）: 評価行の (date, asset_id) にシグナルを絞る。ユニバースに
         ``weight`` 列があればベンチマークウェイトとして使う。
       - ベンチマーク（任意）: ``weight`` をベンチマークウェイトにする
         （ユニバースのウェイトより優先）。
       - 分類（任意）: 評価行。
       - リターン: ソースごとに延長行の期間リターンを読み、系列ごとに kind に応じた
         集約方法（``PERIOD_AGGREGATION``）とソースの convention でフォワードリターンに
         して、評価行に reindex する。
       - リスクモデル: ``_load_risk_model``。

    3. 最短ホライズンで銘柄の一致率を確認して警告を加え、全警告を
       ``logger.warning`` に出す。

    ユニバースで絞るのはシグナルだけで、分類・フォワードリターン・ベンチマーク
    ウェイトは絞らない。``DataBundle.dates`` は評価行のキー全体（シグナルがない行も含む）。

    Args:
        cfg: 評価の config。

    Returns:
        DataBundle、評価計画、警告をまとめた PreparedData。

    Raises:
        ValueError: ``calendar`` または ``data`` が未設定の場合、評価期間にカレンダー行が
            ない場合、データの重複・型・列不足などの不整合がある場合
            （後者は読み込み処理から送出される）。
    """
    if cfg.calendar is None or cfg.data is None:
        raise ValueError("`calendar` and `data` are required to load data from config")
    calendar = load_calendar(cfg.calendar.path, cfg.calendar.index, cfg.calendar.columns)
    plan = make_plan(calendar, cfg.period.start, cfg.period.end, cfg.horizons)
    warnings = [plan.tail_warning] if plan.tail_warning else []

    cache = ParquetCache(cfg.cache.dir) if cfg.cache.enabled else None
    benchmark_weights = None
    with LoadContext(calendar, cache) as ctx:
        signals = _load_signals(cfg, plan, ctx)

        if cfg.universe is not None:
            members = load_snapshot(cfg.universe, plan.eval_rows, ctx, {ASSET_ID})
            members = members.set_index([DATE, ASSET_ID]).sort_index()
            signals = signals[signals.index.isin(members.index)]
            if WEIGHT in members.columns:
                benchmark_weights = members[[WEIGHT]].astype(float)

        if cfg.benchmark is not None:
            bm = load_snapshot(cfg.benchmark, plan.eval_rows, ctx, {ASSET_ID, WEIGHT})
            benchmark_weights = bm.set_index([DATE, ASSET_ID])[[WEIGHT]].astype(float).sort_index()

        classification = None
        if cfg.data.classification is not None:
            classification = _load_classification(cfg.data.classification, plan, ctx)

        fwd: dict[str, dict[int, pd.DataFrame]] = {}
        for source in cfg.returns:
            required = {ASSET_ID} | ({DATE} if isinstance(source, ReturnsDBSource) else set())
            periods = _load_period_frames(
                source,
                plan,
                ctx,
                columns=ASSET_ID,
                values={
                    name: (s.column, PERIOD_AGGREGATION[s.kind])
                    for name, s in source.series.items()
                },
                required_roles=required,
                convention=source.convention,
                warnings=warnings,
            )
            for name, period in periods.items():
                kind = source.series[name].kind
                by_h = forward_returns(
                    period, plan.horizons, source.convention, PERIOD_AGGREGATION[kind]
                )
                fwd[name] = {h: f.reindex(plan.eval_index) for h, f in by_h.items()}

        risk_models = {
            name: _load_risk_model(name, model, plan, ctx, warnings)
            for name, model in cfg.risk_models.items()
        }

    warnings += _coverage_warnings(signals, fwd, min(plan.horizons))
    for w in warnings:
        logger.warning(w)
    bundle = DataBundle(
        signals=signals,
        forward_returns=fwd,
        return_kinds=cfg.return_kinds,
        classification=classification,
        risk_models=risk_models,
        benchmark_weights=benchmark_weights,
        dates=tuple(plan.eval_keys),
    )
    return PreparedData(bundle=bundle, plan=plan, warnings=warnings)


def make_bundle(
    signals: pd.DataFrame,
    period_returns: Mapping[str, pd.DataFrame],
    *,
    return_kinds: Mapping[str, ReturnKind],
    horizons: list[int],
    convention: Convention = "realized",
    classification: pd.DataFrame | None = None,
    universe: pd.MultiIndex | pd.DataFrame | None = None,
    dates: list[str] | None = None,
) -> DataBundle:
    """手元の DataFrame から DataBundle を作る（io / loaders をバイパスする Python API 用）。

    カレンダーファイルや config の data セクションを使わず、既に期間リターンに
    なっている DataFrame から ``forward_returns`` でフォワードリターンを計算する。
    時点合わせの規則は ``build_bundle`` と同じで、集約方法は ``return_kinds`` から
    ``PERIOD_AGGREGATION`` で決まる（total は複利、specific は加算）。

    ``period_returns`` は index でソートしてから行の位置でずらすため、行は欠けのない
    連続したカレンダー行でなければならない（連続性は検査しない）。評価行の後ろに
    必要な行（realized なら最大ホライズン行、forward なら最大ホライズン - 1 行）が
    なければ、その分だけ末尾のフォワードリターンは NaN になる。計算後のフォワード
    リターンは ``dates`` に reindex し、columns の名前を ``asset_id`` にする。

    date・asset_id の型や書式は正規化しないので、シグナルと期間リターンで揃えて
    おくこと（例: 銘柄コードを両方 str にする）。リスクモデルとベンチマーク
    ウェイトは設定されない。

    Args:
        signals: index=(date, asset_id)、columns=シグナル名。
            2 レベルの MultiIndex であればレベル名は ``date`` / ``asset_id`` に付け替える。
        period_returns: 系列名 -> 期間リターン（index=date, columns=asset_id）。
            行は欠けのない連続したカレンダー行で、評価期間の後ろに最大ホライズン分の行を含むこと。
        return_kinds: 系列名 -> ``total`` / ``specific``。
            ``period_returns`` の全系列について必要（余分な系列は無視する）。
        horizons: ホライズン（行数、正の整数）。
        convention: 期間リターンの規約（realized: 行 t は t-1→t の実現リターン）。
            forward なら行 t は t→t+1 のリターン。
        classification: index=(date, asset_id)、columns=[category, ...]。
            レベル名を付け替える以外はそのまま使う。
        universe: 評価対象の (date, asset_id)。指定時はシグナルをこの範囲に絞る。
            MultiIndex、またはそれを index に持つ DataFrame。
        dates: 評価対象のカレンダー行。省略時はシグナルの日付（昇順）。
            指定してもシグナルは ``dates`` で絞らない。

    Returns:
        DataBundle。``return_kinds`` は ``period_returns`` にある系列だけを持つ。

    Raises:
        ValueError: ``period_returns`` のいずれかに ``dates`` の日付がない場合。
        KeyError: ``return_kinds`` に ``period_returns`` の系列名がない場合。

    Examples:
        realized・h=1 なら、行 t のシグナルには行 t+1 の期間リターンが対応する::

            bundle = make_bundle(
                signals,  # index=(date, asset_id)
                {"total": period_ret},  # index=date（評価行 + 後ろに1行以上）
                return_kinds={"total": "total"},
                horizons=[1],
            )
            fwd = bundle.forward_returns["total"][1]  # index=dates, columns=asset_id
            # fwd の行 t の値 = period_ret の行 t+1 の値（t+1 がなければ NaN）
    """
    signals = signals.copy()
    signals.index = signals.index.set_names([DATE, ASSET_ID])
    if universe is not None:
        members = universe if isinstance(universe, pd.MultiIndex) else universe.index
        signals = signals[signals.index.isin(members)]
    if dates is None:
        dates = sorted(signals.index.get_level_values(DATE).unique())
    fwd = {}
    for name, period in period_returns.items():
        missing = set(dates) - set(period.index)
        if missing:
            raise ValueError(f"period_returns[{name!r}] is missing dates: {sorted(missing)[:5]}")
        by_h = forward_returns(
            period.sort_index(),
            horizons,
            convention,
            PERIOD_AGGREGATION[return_kinds[name]],
        )
        fwd[name] = {}
        for h, f in by_h.items():
            f = f.reindex(pd.Index(dates, name=DATE))
            f.columns.name = ASSET_ID
            fwd[name][h] = f
    if classification is not None:
        classification = classification.copy()
        classification.index = classification.index.set_names([DATE, ASSET_ID])
    return DataBundle(
        signals=signals.sort_index(),
        forward_returns=fwd,
        return_kinds={k: return_kinds[k] for k in period_returns},
        classification=classification,
        dates=tuple(dates),
    )
