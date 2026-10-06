from abc import ABC, abstractmethod
from typing import Any, Self

import pandas as pd
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from .config import ConnectionConfig


class DBClient(ABC):
    """SQLAlchemy Engine を介した読み取り専用の DB クライアント。

    サブクラスは ``_url()`` と DB 固有の設定（``_connect_args()`` や接続イベント）
    だけを実装する。SQL のプレースホルダは ``:name`` 形式に統一される。
    """

    def __init__(self, config: ConnectionConfig):
        self._config = config
        self._engine: Engine | None = None

    @abstractmethod
    def _url(self) -> URL: ...

    def _connect_args(self) -> dict[str, Any]:
        return {}

    def connect(self) -> None:
        self._engine = create_engine(self._url(), connect_args=self._connect_args())

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
        if self._engine is None:
            raise RuntimeError("DBClient is not connected. Call connect() first.")
        with self._engine.connect() as conn:
            return pd.read_sql(text(sql), conn, params=params)

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
