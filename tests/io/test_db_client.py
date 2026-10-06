"""namdb v0.1.2 のテストを取り込んだもの（io/db）。"""

from urllib.parse import parse_qs, unquote_plus, urlparse

import pytest

from alpha_signal_evaluation.io.db.client import get_client, read_db
from alpha_signal_evaluation.io.db.config import (
    AIModelConfig,
    CommonConfig,
    DSSDATA1Config,
    DSSDataConfig,
    EquityConfig,
    FactorGLBConfig,
    FactorJPConfig,
    FFDLConfig,
    FsTrfacGlbConfig,
    GlobalConfig,
    IDSQEConfig,
    JETFVConfig,
    JPMarketConfig,
    KGXConfig,
    NMT202Config,
    RiskModelsConfig,
    TGXConfig,
    TGXLConfig,
    TRCConfig,
    TRSConfig,
    TVLConfig,
    XebralConfig,
)
from alpha_signal_evaluation.io.db.drivers import (
    DBClient,
    MySQLClient,
    PostgresClient,
    SQLServerClient,
)

CONFIG_CASES = [
    (TRCConfig, "TRC"),
    (TRSConfig, "TRS"),
    (NMT202Config, "NMT202"),
    (KGXConfig, "K_GX"),
    (TGXConfig, "T_GX"),
    (TGXLConfig, "T_GXL"),
    (IDSQEConfig, "IDSQE"),
    (GlobalConfig, "GLOBAL"),
    (RiskModelsConfig, "RISK_MODELS"),
    (JPMarketConfig, "JP_MARKET"),
    (DSSDATA1Config, "DSS_DATA1"),
    (JETFVConfig, "JETFV"),
    (FactorGLBConfig, "factor_glb"),
    (FactorJPConfig, "factor_jp"),
    (EquityConfig, "equity"),
    (XebralConfig, "xebral"),
    (CommonConfig, "common"),
    (TVLConfig, "tvl"),
    (AIModelConfig, "ai_model"),
    (DSSDataConfig, "dss_data"),
    (FsTrfacGlbConfig, "fs_trfac_glb"),
    (FFDLConfig, "ffdl"),
]


@pytest.mark.parametrize(("config_cls", "expected_db"), CONFIG_CASES)
def test_db_name_is_configured(config_cls, expected_db):
    config = config_cls(username="user", password="password")

    assert config.db == expected_db


def test_sqlserver_client_connect_uses_configured_db(monkeypatch):
    created_urls = []

    def fake_create_engine(url):
        created_urls.append(url)
        return object()

    monkeypatch.setattr(
        "alpha_signal_evaluation.io.db.drivers.sqlserver.create_engine", fake_create_engine
    )

    config = KGXConfig(username="user", password="password")
    client = SQLServerClient(config)

    client.connect()

    assert len(created_urls) == 1
    url = created_urls[0]
    assert url.startswith("mssql+pyodbc:///?odbc_connect=")

    query = parse_qs(urlparse(url).query)
    odbc_conn_str = unquote_plus(query["odbc_connect"][0])

    assert f"DATABASE={config.db};" in odbc_conn_str
    assert f"SERVER={config.host},{config.port};" in odbc_conn_str
    assert f"UID={config.username};" in odbc_conn_str
    assert f"PWD={config.password};" in odbc_conn_str
    assert f"charset={config.charset};" in odbc_conn_str


@pytest.mark.integration
@pytest.mark.parametrize(("config_cls", "expected_db"), CONFIG_CASES)
def test_client_execute_works_for_all_databases(config_cls, expected_db):
    config = config_cls()
    if not config.username or not config.password:
        pytest.skip(f"{config_cls.__name__} credentials are not configured")

    if config.server_type == "sqlserver":
        client_cls = SQLServerClient
        sql = "SELECT DB_NAME() AS db_name"
    elif config.server_type == "mysql":
        client_cls = MySQLClient
        sql = "SELECT DATABASE() AS db_name"
    elif config.server_type == "postgres":
        client_cls = PostgresClient
        sql = "SELECT current_database() AS db_name"
    else:
        pytest.fail(f"Unsupported server_type: {config.server_type}")

    with client_cls(config) as client:
        result = client.execute(sql)

    assert not result.empty
    assert result.loc[0, "db_name"] == expected_db


class _ExecuteTestClient(DBClient):
    def connect(self) -> None:
        self._connection = object()

    def get_table_schema(self, table_name: str, schema: str | None = None):
        return self.execute("SELECT 1")


@pytest.mark.parametrize(
    ("config", "expected_client_cls"),
    [
        (TRCConfig(username="user", password="password"), SQLServerClient),
        (FactorGLBConfig(username="user", password="password"), MySQLClient),
        (FsTrfacGlbConfig(username="user", password="password"), PostgresClient),
    ],
)
def test_get_client_returns_appropriate_client(config, expected_client_cls):
    client = get_client(config)

    assert isinstance(client, expected_client_cls)


def test_read_db_returns_dataframe(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, config):
            self.config = config

        def __enter__(self):
            calls.append(("enter", self.config))
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            calls.append(("exit", exc_type, exc_val, exc_tb))

        def execute(self, sql, params=None):
            calls.append(("execute", sql, params))
            return "dataframe"

    monkeypatch.setattr(
        "alpha_signal_evaluation.io.db.client.get_client", lambda config: FakeClient(config)
    )

    config = TRCConfig(username="user", password="password")
    params = {"id": 1}

    result = read_db(config, "SELECT * FROM table WHERE id = :id", params=params)

    assert result == "dataframe"
    assert calls == [
        ("enter", config),
        ("execute", "SELECT * FROM table WHERE id = :id", params),
        ("exit", None, None, None),
    ]


@pytest.mark.parametrize(
    ("client", "table_name", "schema", "expected_params"),
    [
        (
            SQLServerClient(KGXConfig(username="user", password="password")),
            "JPE4_D_PRC",
            "dbo",
            {"table_name": "JPE4_D_PRC", "schema_name": "dbo"},
        ),
        (
            MySQLClient(FactorGLBConfig(username="user", password="password")),
            "some_table",
            None,
            {"table_name": "some_table", "schema_name": None},
        ),
        (
            PostgresClient(FsTrfacGlbConfig(username="user", password="password")),
            "some_table",
            "public",
            {"table_name": "some_table", "schema_name": "public"},
        ),
    ],
)
def test_get_table_schema_delegates_to_execute(
    monkeypatch, client, table_name, schema, expected_params
):
    calls = []

    def fake_execute(sql, params=None):
        calls.append((sql, params))
        return "schema"

    monkeypatch.setattr(client, "execute", fake_execute)

    result = client.get_table_schema(table_name, schema=schema)

    assert result == "schema"
    assert len(calls) == 1
    assert calls[0][1] == expected_params
    assert "INFORMATION_SCHEMA.COLUMNS" in calls[0][0].upper()


def test_dbclient_execute_delegates_to_pandas_read_sql(monkeypatch):
    calls = []

    def fake_read_sql(sql, connection, params=None):
        calls.append((sql, connection, params))
        return "result"

    monkeypatch.setattr("alpha_signal_evaluation.io.db.drivers.base.pd.read_sql", fake_read_sql)

    config = KGXConfig(username="user", password="password")
    client = _ExecuteTestClient(config)
    client.connect()

    result = client.execute("SELECT 1")

    assert result == "result"
    assert calls == [("SELECT 1", client._connection, None)]
