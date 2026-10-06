"""DataFrame をローカルの parquet ファイルにキャッシュする（io 層）。

主に DB クエリ結果の再取得を避けるために ``data.loaders.run_query`` から使われる。
キーの意味（どのクエリ・期間か）は呼び出し側が決め、ここはドメイン知識を持たない。
"""

import hashlib
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class ParquetCache:
    """任意のキー文字列に対応する DataFrame をローカルの parquet に保存する。

    キーの意味（クエリ・期間など）は呼び出し側が決める。ここはドメイン知識を持たない。

    キャッシュはキーごとに ``<directory>/<key>.parquet`` の1ファイル。有効期限や
    無効化の仕組みはないので、元データが変わった場合はファイルを削除する必要がある。
    """

    def __init__(self, directory: str | Path):
        """キャッシュを作る。ディレクトリは最初の ``put`` で作成する。

        Args:
            directory: parquet ファイルを置くディレクトリ。
        """
        self._dir = Path(directory)

    @staticmethod
    def make_key(*parts: str) -> str:
        """複数の文字列からキャッシュキーを作る。

        各要素を区切り文字（``\\x1f``）で連結した UTF-8 バイト列の SHA-256 を返す。
        同じ要素の並びからは常に同じキーになる。

        Args:
            *parts: キーを構成する文字列（例: 接続先名・SQL 本文・パラメータの JSON）。

        Returns:
            64文字の16進文字列。ファイル名としてそのまま使える。
        """
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        """キーに対応する parquet ファイルのパス（``<directory>/<key>.parquet``）を返す。"""
        return self._dir / f"{key}.parquet"

    def get(self, key: str) -> pd.DataFrame | None:
        """キーに対応するキャッシュを読み込む。

        Args:
            key: キャッシュキー（通常 ``make_key`` で作ったもの）。

        Returns:
            キャッシュされた DataFrame。ファイルがなければ ``None``。
        """
        path = self._path(key)
        if not path.exists():
            return None
        logger.info("Cache hit: %s", path)
        return pd.read_parquet(path)

    def put(self, key: str, df: pd.DataFrame) -> None:
        """DataFrame をキーに対応する parquet ファイルに保存する。

        ディレクトリがなければ作成する。一時ファイル（拡張子 ``.tmp``）に書いてから
        置き換えるので、書き込み途中のファイルを ``get`` が読むことはない。
        既存のキャッシュは上書きする。

        Args:
            key: キャッシュキー。
            df: 保存する DataFrame（parquet に書ける列型であること）。書き込みに失敗した
                場合は pandas / pyarrow の例外をそのまま送出する。
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        df.to_parquet(tmp)
        tmp.replace(path)  # 書き込み途中のファイルを読まないように置き換える
