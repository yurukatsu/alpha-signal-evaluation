"""拡張子からリーダーを選んで表形式のファイルを DataFrame として読み込む（io 層）。

対応する拡張子は ``.csv`` / ``.dat`` / ``.tsv``（pandas.read_csv）、``.parquet``、``.pkl``。
区切り文字・コメント行・ヘッダー有無などの指定は、呼び出し側（config の ``read_options``）が
オプションとして渡す。読んだ内容の意味（どの列が銘柄コードか等）はここでは扱わない。
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd


def _read_csv(path: Path, **options: Any) -> pd.DataFrame:
    """``pandas.read_csv`` で読み、文字列の列名の先頭の ``#`` と前後の空白を除去する。

    ヘッダー行がコメント記号で始まるファイル（例: ``#nri_code date ...``）でも、
    列名を ``nri_code`` のように扱えるようにする。``options`` は ``read_csv`` にそのまま渡す。
    """
    df = pd.read_csv(path, **options)
    # ヘッダー先頭の "#"（例: "#nri_code"）は除去する
    df.columns = [c.lstrip("#").strip() if isinstance(c, str) else c for c in df.columns]
    return df


_READERS: dict[str, Callable[..., pd.DataFrame]] = {
    ".csv": _read_csv,
    ".dat": _read_csv,  # 区切り文字・コメント行・ヘッダー有無は read_options で指定する
    ".tsv": lambda path, **kw: _read_csv(path, **{"sep": "\t", **kw}),
    ".parquet": pd.read_parquet,
    ".pkl": pd.read_pickle,
}

SUPPORTED_SUFFIXES = frozenset(_READERS)


def read_table(path: str | Path, **options: Any) -> pd.DataFrame:
    """拡張子からリーダーを選んでファイルを読み込む。``options`` はリーダーにそのまま渡す。

    拡張子は大文字・小文字を区別しない。``.tsv`` は区切り文字のデフォルトをタブにする
    （``options`` の ``sep`` が優先）。CSV 系では列名の先頭の ``#`` を除去する。

    Args:
        path: 読み込むファイルのパス。
        **options: リーダー（``pandas.read_csv`` / ``read_parquet`` / ``read_pickle``）に
            そのまま渡すキーワード引数。

    Returns:
        読み込んだ DataFrame（列名・型はファイルのまま。CSV 系の ``#`` の除去を除く）。

    Raises:
        ValueError: 対応していない拡張子の場合。メッセージに対応する拡張子の一覧を含める。
    """
    path = Path(path)
    try:
        reader = _READERS[path.suffix.lower()]
    except KeyError:
        raise ValueError(
            f"Unsupported file type {path.suffix!r}: {path}. "
            f"Supported: {sorted(SUPPORTED_SUFFIXES)}"
        ) from None
    return reader(path, **options)
