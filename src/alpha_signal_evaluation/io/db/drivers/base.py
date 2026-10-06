"""DB クライアントの共通基底クラス（namdb v0.1.2 由来）。"""

from abc import ABC, abstractmethod

import pandas as pd
from sqlalchemy import text

from ..config import ConnectionConfig


class DBClient(ABC):
    """SQLAlchemy ベースの DB クライアントの抽象基底クラス。

    サブクラスは ``connect``（Engine の作成）と ``get_table_schema``（テーブル定義の取得）を
    実装する。SQL の実行（``execute``）と切断（``close``）は共通。with 文で使うと、
    入るときに ``connect``、抜けるときに ``close`` を呼ぶ。
    """

    def __init__(self, config: ConnectionConfig):
        """クライアントを作る。この時点では接続しない。

        Args:
            config: 接続設定（ホスト・ポート・DB 名・認証情報・文字コード）。
        """
        self._config = config
        self._connection = None

    @abstractmethod
    def connect(self) -> None:
        """接続設定から SQLAlchemy の Engine を作り、クライアントに保持する。"""
        ...

    @abstractmethod
    def get_table_schema(
        self,
        table_name: str,
        schema: str | None = None,
    ) -> pd.DataFrame:
        """``INFORMATION_SCHEMA.COLUMNS`` からテーブルの列定義を取得する。

        Args:
            table_name: テーブル名。
            schema: スキーマ名。``None`` の場合の扱いは DB 種別ごとに異なる。

        Returns:
            1行 = 1列の DataFrame（列名・データ型・NULL 可否など、列の順序で並ぶ）。
        """
        ...

    def close(self) -> None:
        """接続を閉じる。

        保持しているのが Engine（``dispose`` を持つ）なら接続プールを破棄し、そうでなければ
        ``close`` を呼ぶ。未接続なら何もしない。何度呼んでもよい。
        """
        if self._connection is not None:
            if hasattr(self._connection, "dispose"):
                self._connection.dispose()
            else:
                self._connection.close()
            self._connection = None

    def execute(self, sql: str, params: dict | None = None) -> pd.DataFrame:
        """SQL を実行し、結果を DataFrame で返す。

        ``params`` を渡した場合は SQL を ``sqlalchemy.text`` で包み、``:name`` 形式の
        バインドパラメータとして値を渡す。``None`` の場合は SQL 文字列をそのまま
        ``pandas.read_sql`` に渡す。事前に ``connect`` しておく必要がある。

        Args:
            sql: 実行する SQL。
            params: バインドパラメータ（パラメータ名 -> 値）。

        Returns:
            クエリ結果の DataFrame（列名は DB が返したまま）。
        """
        statement = text(sql) if params is not None else sql
        return pd.read_sql(statement, self._connection, params=params)

    def __enter__(self) -> "DBClient":
        """``connect`` を呼んで自身を返す。"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """``close`` を呼ぶ。例外は抑止しない。"""
        self.close()
