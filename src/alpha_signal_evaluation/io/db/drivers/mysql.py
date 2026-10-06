"""MySQL 用の DB クライアント（PyMySQL ドライバ、namdb v0.1.2 由来）。"""

from sqlalchemy import URL, create_engine

from .base import DBClient


class MySQLClient(DBClient):
    """MySQL 用のクライアント。SQLAlchemy の ``mysql+pymysql`` ダイアレクトを使う。"""

    def connect(self) -> None:
        """``mysql+pymysql`` の Engine を作る。

        接続 URL にはホスト・ポート・DB 名・認証情報と、クエリ引数 ``charset``（接続設定の
        文字コード）を含める。Engine の作成時点では接続せず、最初のクエリ実行時に接続する。
        """
        cfg = self._config
        url = URL.create(
            "mysql+pymysql",
            username=cfg.username,
            password=cfg.password,
            host=cfg.host,
            port=cfg.port,
            database=cfg.db,
            query={"charset": cfg.charset},
        )
        self._connection = create_engine(url)

    def get_table_schema(
        self,
        table_name: str,
        schema: str | None = None,
    ):
        """``INFORMATION_SCHEMA.COLUMNS`` からテーブルの列定義を取得する。

        Args:
            table_name: テーブル名。
            schema: スキーマ（データベース）名。``None`` なら接続中のデータベース
                （``DATABASE()``）を対象にする。

        Returns:
            1行 = 1列の DataFrame。columns=[table_schema, table_name, ordinal_position,
            column_name, data_type, character_maximum_length, numeric_precision,
            numeric_scale, is_nullable]。ordinal_position の昇順。
        """
        sql = """
        SELECT
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
        WHERE TABLE_SCHEMA = COALESCE(:schema_name, DATABASE())
          AND TABLE_NAME = :table_name
        ORDER BY ORDINAL_POSITION
        """
        return self.execute(
            sql,
            params={
                "table_name": table_name,
                "schema_name": schema,
            },
        )
