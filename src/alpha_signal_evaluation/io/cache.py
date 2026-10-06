import hashlib
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class ParquetCache:
    """任意のキー文字列に対応する DataFrame をローカルの parquet に保存する。

    キーの意味（クエリ・期間など）は呼び出し側が決める。ここはドメイン知識を持たない。
    """

    def __init__(self, directory: str | Path):
        self._dir = Path(directory)

    @staticmethod
    def make_key(*parts: str) -> str:
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self._dir / f"{key}.parquet"

    def get(self, key: str) -> pd.DataFrame | None:
        path = self._path(key)
        if not path.exists():
            return None
        logger.info("Cache hit: %s", path)
        return pd.read_parquet(path)

    def put(self, key: str, df: pd.DataFrame) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        df.to_parquet(tmp)
        tmp.replace(path)  # 書き込み途中のファイルを読まないように置き換える
