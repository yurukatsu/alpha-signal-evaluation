"""PostgreSQL 用の DB クライアント（psycopg2 ドライバ、namdb v0.1.2 由来）。"""

from sqlalchemy import URL, create_engine

from .base import DBClient


class PostgresClient(DBClient):
    """PostgreSQL 用のクライアント。SQLAlchemy の ``postgresql+psycopg2`` ダイアレクトを使う。"""

    def connect(self) -> None:
        """``postgresql+psycopg2`` の Engine を作る。

        接続 URL にはホスト・ポート・DB 名・認証情報を含める（接続設定の ``charset`` は
        使わない）。Engine の作成時点では接続せず、最初のクエリ実行時に接続する。
        """
        cfg = self._config
        url = URL.create(
            "postgresql+psycopg2",
            username=cfg.username,
            password=cfg.password,
            host=cfg.host,
            port=cfg.port,
            database=cfg.db,
        )
        self._connection = create_engine(url)

    def get_table_schema(
        self,
        table_name: str,
        schema: str | None = None,
    ):
        """``information_schema.columns`` からテーブルの列定義を取得する。

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
            table_catalog,
            table_schema,
            table_name,
            ordinal_position,
            column_name,
            data_type,
            character_maximum_length,
            numeric_precision,
            numeric_scale,
            is_nullable
        FROM information_schema.columns
        WHERE table_name = :table_name
          AND (:schema_name IS NULL OR table_schema = :schema_name)
        ORDER BY ordinal_position
        """
        return self.execute(
            sql,
            params={
                "table_name": table_name,
                "schema_name": schema,
            },
        )
