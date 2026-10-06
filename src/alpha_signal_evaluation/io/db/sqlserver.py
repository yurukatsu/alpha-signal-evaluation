from sqlalchemy import event
from sqlalchemy.engine import URL

from .base import DBClient


class SQLServerClient(DBClient):
    def _url(self) -> URL:
        c = self._config
        return URL.create(
            "mssql+pyodbc",
            username=c.username,
            password=c.password,
            host=c.host,
            port=c.port,
            database=c.db,
            query={"driver": "ODBC Driver 17 for SQL Server"},
        )

    def connect(self) -> None:
        super().connect()
        import pyodbc

        charset = self._config.charset

        # プールから払い出される全ての DBAPI 接続に文字コードを設定する
        @event.listens_for(self._engine, "connect")
        def _set_encoding(dbapi_conn, _record):
            dbapi_conn.setdecoding(pyodbc.SQL_CHAR, encoding=charset)
            dbapi_conn.setencoding(encoding=charset)
