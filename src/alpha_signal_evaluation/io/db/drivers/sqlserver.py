"""SQL Server 用の DB クライアント（pyodbc ドライバ、namdb v0.1.2 由来）。"""

from urllib.parse import quote_plus

from sqlalchemy import create_engine

from .base import DBClient


class SQLServerClient(DBClient):
    """SQL Server 用のクライアント。SQLAlchemy の ``mssql+pyodbc`` ダイアレクトを使う。

    実行環境に ``ODBC Driver 17 for SQL Server`` がインストールされている必要がある。
    """

    def connect(self) -> None:
        """``mssql+pyodbc`` の Engine を作る。

        ODBC 接続文字列（ドライバ・``ホスト,ポート``・DB 名・認証情報・文字コード）を
        URL エンコードして ``odbc_connect`` に渡す。Engine の作成時点では接続せず、
        最初のクエリ実行時に接続する。
        """
        cfg = self._config
        odbc_conn_str = (
            "DRIVER={ODBC Driver 17 for SQL Server};"
            f"SERVER={cfg.host},{cfg.port};DATABASE={cfg.db};"
            f"UID={cfg.username};PWD={cfg.password};"
            f"charset={cfg.charset};"
        )
        self._connection = create_engine(
            f"mssql+pyodbc:///?odbc_connect={quote_plus(odbc_conn_str)}"
        )

    def get_table_schema(
        self,
        table_name: str,
        schema: str | None = None,
    ):
        """``INFORMATION_SCHEMA.COLUMNS`` からテーブルの列定義を取得する。

        Args:
            table_name: テーブル名。
            schema: スキーマ名。``None`` ならスキーマで絞らない（同名のテーブルが複数の
                スキーマにあれば、すべての列が返る）。

        Returns:
            1行 = 1列の DataFrame。columns=[table_catalog, table_schema, table_name,
            ordinal_position, column_name, data_type, character_maximum_length,
            numeric_precision, numeric_scale, is_nullable]。ordinal_position の昇順。
        """
        sql = """
        SELECT
            TABLE_CATALOG AS table_catalog,
            TABLE_SCHEMA AS table_schema,
            TABLE_NAME AS table_name,
            ORDINAL_POSITION AS ordinal_position,
            COLUMN_NAME AS column_name,
            DATA_TYPE AS data_type,
            CHARACTER_MAXIMUM_LENGTH AS character_maximum_length,
            NUMERIC_PRECISION AS numeric_precision,
            NUMERIC_SCALE AS numeric_scale,
            IS_NULLABLE AS is_nullable
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_NAME = :table_name
          AND (:schema_name IS NULL OR TABLE_SCHEMA = :schema_name)
        ORDER BY ORDINAL_POSITION
        """
        return self.execute(
            sql,
            params={
                "table_name": table_name,
                "schema_name": schema,
            },
        )
