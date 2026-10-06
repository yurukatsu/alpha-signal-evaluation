"""カレンダーファイル（評価の時間軸の唯一の定義）の読み込み。

1行 = 1評価期間。値は文字列のまま保持する（パス生成で書式を崩さないため）。
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Calendar:
    frame: pd.DataFrame  # 全列 str、RangeIndex（行位置）
    index: str  # 内部で全データに付与する日付キーの列名（通常 Base_day）

    def __post_init__(self) -> None:
        if self.index not in self.frame.columns:
            raise ValueError(
                f"Calendar index column {self.index!r} not found. "
                f"Available: {list(self.frame.columns)}"
            )
        check_increasing(self.frame[self.index], self.index)

    def __len__(self) -> int:
        return len(self.frame)

    @property
    def columns(self) -> list[str]:
        return list(self.frame.columns)

    @property
    def keys(self) -> pd.Series:
        return self.frame[self.index]

    def positions(self, start: str | None, end: str | None) -> np.ndarray:
        """``start <= index <= end`` を満たす行の位置。"""
        keys = self.keys
        mask = pd.Series(True, index=keys.index)
        if start is not None:
            mask &= keys >= start
        if end is not None:
            mask &= keys <= end
        return np.flatnonzero(mask.to_numpy())

    def rows(self, positions: np.ndarray | range) -> pd.DataFrame:
        return self.frame.iloc[np.asarray(positions)]


def check_increasing(values: pd.Series, name: str) -> None:
    if values.isna().any():
        raise ValueError(f"Calendar column {name!r} contains empty values")
    if not values.is_unique:
        dup = values[values.duplicated()].unique().tolist()[:5]
        raise ValueError(f"Calendar column {name!r} has duplicated values: {dup}")
    if not values.is_monotonic_increasing:
        raise ValueError(f"Calendar column {name!r} is not sorted in ascending order")


def load_calendar(path: str | Path, index: str = "Base_day") -> Calendar:
    """タブ区切りのカレンダーを読み込む。ヘッダー先頭の ``#`` は除去する。"""
    frame = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    frame.columns = [str(c).lstrip("#").strip() for c in frame.columns]
    frame = frame.apply(lambda s: s.str.strip()).replace("", pd.NA)
    return Calendar(frame=frame.reset_index(drop=True), index=index)
