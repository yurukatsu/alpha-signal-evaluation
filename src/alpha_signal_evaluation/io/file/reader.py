from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

_READERS: dict[str, Callable[..., pd.DataFrame]] = {
    ".csv": pd.read_csv,
    ".dat": pd.read_csv,  # 区切り文字等は read_options で指定する
    ".tsv": lambda path, **kw: pd.read_csv(path, **{"sep": "\t", **kw}),
    ".parquet": pd.read_parquet,
    ".pkl": pd.read_pickle,
}

SUPPORTED_SUFFIXES = frozenset(_READERS)


def read_table(path: str | Path, **options: Any) -> pd.DataFrame:
    """拡張子からリーダーを選んでファイルを読み込む。``options`` はリーダーにそのまま渡す。"""
    path = Path(path)
    try:
        reader = _READERS[path.suffix.lower()]
    except KeyError:
        raise ValueError(
            f"Unsupported file type {path.suffix!r}: {path}. "
            f"Supported: {sorted(SUPPORTED_SUFFIXES)}"
        ) from None
    return reader(path, **options)
