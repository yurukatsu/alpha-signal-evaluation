"""コマンドラインインターフェース（エントリポイント ``alpha-eval``）。

typer アプリケーション ``app`` を公開する。実装は ``cli.main`` にあり、
評価のロジックはすべて ``api`` に委ねる。
"""

from .main import app

__all__ = ["app"]
