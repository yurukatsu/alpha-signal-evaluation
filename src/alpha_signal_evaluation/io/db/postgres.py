from typing import Any

from sqlalchemy.engine import URL

from .base import DBClient


class PostgresClient(DBClient):
    def _url(self) -> URL:
        c = self._config
        return URL.create(
            "postgresql+psycopg2",
            username=c.username,
            password=c.password,
            host=c.host,
            port=c.port,
            database=c.db,
        )

    def _connect_args(self) -> dict[str, Any]:
        # Postgres のエンコーディング名（SJIS 等）を指定する。cp932 は通らない可能性がある
        return {"client_encoding": self._config.charset}
