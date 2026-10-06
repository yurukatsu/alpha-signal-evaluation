from sqlalchemy.engine import URL

from .base import DBClient


class MySQLClient(DBClient):
    def _url(self) -> URL:
        c = self._config
        return URL.create(
            "mysql+pymysql",
            username=c.username,
            password=c.password,
            host=c.host,
            port=c.port,
            database=c.db,
            query={"charset": c.charset},
        )
