"""カレンダーファイル（評価の時間軸の唯一の定義）の読み込み。

1行 = 1評価期間。各行は ``Base_day``（基準日）や ``Trading_day``・``Rskmdl_month`` など、
その期間に対応する複数の日付キーを列として持つ。値は文字列のまま保持する
（``{Base_day}`` のようなパステンプレートの展開で、先頭ゼロや桁数などの書式を崩さないため）。

``Calendar.index`` で指定した列（通常 ``Base_day``）の値が、パッケージ内部で全データに
付与される日付キー（role ``date``）になる。この列は空なし・重複なし・昇順であることを
生成時に検証する。期間の切り出し（行位置の計算）はここで行うが、行をまたいだ時点合わせ
（フォワードリターンの計算など）は行わない（pipeline/preprocess.py の責務）。
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Calendar:
    """読み込み済みのカレンダー。評価の時間軸（行の並び）を表す。

    行位置（0 始まりの整数）が「カレンダー上の時点」であり、ホライズンやラグは行数で数える。
    生成時に ``index`` 列が存在し、空なし・重複なし・昇順であることを検証する。

    Attributes:
        frame: カレンダー本体。全列が ``str``、index は ``RangeIndex``（行位置）。
        index: 内部で全データに付与する日付キーの列名（通常 ``Base_day``）。

    Raises:
        ValueError: ``index`` 列が ``frame`` にない場合、または ``index`` 列が
            空値・重複・非昇順を含む場合（生成時）。
    """

    frame: pd.DataFrame  # 全列 str、RangeIndex（行位置）
    index: str  # 内部で全データに付与する日付キーの列名（通常 Base_day）

    def __post_init__(self) -> None:
        """``index`` 列の存在と、空なし・重複なし・昇順であることを検証する。

        Raises:
            ValueError: ``index`` 列がない場合（利用可能な列名をメッセージに含める）、
                または ``check_increasing`` の検証に失敗した場合。
        """
        if self.index not in self.frame.columns:
            raise ValueError(
                f"Calendar index column {self.index!r} not found. "
                f"Available: {list(self.frame.columns)}"
            )
        check_increasing(self.frame[self.index], self.index)

    def __len__(self) -> int:
        """カレンダーの行数（評価期間の数）を返す。"""
        return len(self.frame)

    @property
    def columns(self) -> list[str]:
        """カレンダーの列名のリスト（ファイル上の順序）。"""
        return list(self.frame.columns)

    @property
    def keys(self) -> pd.Series:
        """``index`` 列の値（日付キー）。index は行位置の ``RangeIndex``、値は ``str``。"""
        return self.frame[self.index]

    def positions(self, start: str | None, end: str | None) -> np.ndarray:
        """``start <= index <= end`` を満たす行の位置を返す。

        比較は日付キーの文字列比較で行う（キーは昇順が保証されているので、結果は連続した
        行位置になる）。``start`` / ``end`` が ``None`` の側は制限しない。
        桁数の異なるキー同士（例: ``200301`` と ``20030101``）は辞書順で比較される点に注意。

        Args:
            start: 期間の開始キー（両端を含む）。``None`` なら先頭から。
            end: 期間の終了キー（両端を含む）。``None`` なら末尾まで。

        Returns:
            条件を満たす行位置（0 始まり）の昇順の整数配列。該当がなければ空配列。
        """
        keys = self.keys
        mask = pd.Series(True, index=keys.index)
        if start is not None:
            mask &= keys >= start
        if end is not None:
            mask &= keys <= end
        return np.flatnonzero(mask.to_numpy())

    def rows(self, positions: np.ndarray | range) -> pd.DataFrame:
        """指定した行位置のカレンダー行を返す。

        Args:
            positions: 行位置（0 始まり）の配列または range。

        Returns:
            ``frame.iloc[positions]``。列は ``frame`` と同じ全列 ``str``、
            index は元の行位置のまま（振り直さない）。
        """
        return self.frame.iloc[np.asarray(positions)]


def check_increasing(values: pd.Series, name: str) -> None:
    """カレンダー列が空なし・重複なし・昇順であることを検証する。

    ``Calendar.index`` 列のほか、pipeline や validation がリターンの期間境界として使う
    ``match_on`` 列（例: ``Trading_day``）の検証にも使う。昇順は値の比較（文字列なら辞書順）で
    判定し、重複は昇順違反とは別のエラーとして報告する。

    Args:
        values: 検証するカレンダー列の値。
        name: エラーメッセージに表示する列名。

    Raises:
        ValueError: 空値（NaN / None）を含む場合、重複値を含む場合（最大5件の例を表示）、
            または昇順でない場合。
    """
    if values.isna().any():
        raise ValueError(f"Calendar column {name!r} contains empty values")
    if not values.is_unique:
        dup = values[values.duplicated()].unique().tolist()[:5]
        raise ValueError(f"Calendar column {name!r} has duplicated values: {dup}")
    if not values.is_monotonic_increasing:
        raise ValueError(f"Calendar column {name!r} is not sorted in ascending order")


def load_calendar(
    path: str | Path, index: str = "Base_day", columns: list[str] | None = None
) -> Calendar:
    """空白区切りのカレンダーファイルを読み込む。

    - ファイルは UTF-8 として読み、空行は無視する
    - タブ・スペース・全角スペースが混在していても読めるように、連続する空白文字を区切りとみなす
    - 最初のデータ行（先頭が数字の行）の直前の行をヘッダーとし、先頭の ``#`` は除去する。
      それより前の行（``# Rebalancing and trading monthly.`` 等）はコメントとして読み飛ばす
    - データ行の途中にある ``#`` で始まる行はコメントとして無視する
    - 月だけ（例: ``200301``）の1列でもよい
    - ヘッダー行がないファイルは ``columns`` で列名を指定する（指定時はファイルのヘッダーより優先）
    - 値はすべて文字列のまま保持する

    Args:
        path: カレンダーファイルのパス。
        index: 日付キーとして使う列名（通常 ``Base_day``）。
        columns: 列名のリスト。指定するとファイルのヘッダー行より優先する。

    Returns:
        読み込んだ ``Calendar``。``frame`` は全列 ``str``、``RangeIndex``。

    Raises:
        ValueError: データ行が1行もない場合、ヘッダー行がなく ``columns`` も未指定の場合、
            列名の数と各データ行の列数が一致しない場合、または ``Calendar`` の検証
            （``index`` 列の存在・空なし・重複なし・昇順）に失敗した場合。
        OSError: ファイルが読めない場合（``FileNotFoundError`` など）。
    """
    text = Path(path).read_text(encoding="utf-8").replace("　", " ")
    lines = [line.split() for line in text.splitlines() if line.strip()]
    first = next((i for i, tokens in enumerate(lines) if tokens[0][0].isdigit()), None)
    if first is None:
        raise ValueError(f"Calendar file has no data rows: {path}")
    header = lines[first - 1] if first > 0 else None
    # データ行の途中にあるコメント行は無視する
    rows = [tokens for tokens in lines[first:] if not tokens[0].startswith("#")]

    if columns is not None:
        names = list(columns)
    elif header is not None:
        names = " ".join(header).lstrip("#").split()
    else:
        raise ValueError(
            f"Calendar file has no header row: {path}. Set calendar.columns (e.g. [Month])"
        )
    widths = {len(r) for r in rows}
    if widths != {len(names)}:
        source = "calendar.columns" if columns is not None else f"header {names}"
        raise ValueError(
            f"Calendar {path}: {source} has {len(names)} columns "
            f"but data rows have {sorted(widths)} columns"
        )
    frame = pd.DataFrame(rows, columns=names, dtype=str)
    return Calendar(frame=frame, index=index)
