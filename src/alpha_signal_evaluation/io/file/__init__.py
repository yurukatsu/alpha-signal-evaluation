"""拡張子に応じたファイルの読み込み（io 層）。

``read_table`` と、対応する拡張子の集合 ``SUPPORTED_SUFFIXES`` を公開する。
"""

from .reader import SUPPORTED_SUFFIXES, read_table

__all__ = ["SUPPORTED_SUFFIXES", "read_table"]
