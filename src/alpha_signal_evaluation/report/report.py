from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from ..config import EvaluationConfig, OutputFormat
from ..pipeline.runner import MetricRun


@dataclass
class EvaluationReport:
    """評価結果。metric のキー（config の id または name）ごとに MetricRun を持つ。"""

    runs: dict[str, MetricRun]
    config: EvaluationConfig | None = None
    warnings: list[str] = field(default_factory=list)

    def __getitem__(self, key: str):
        run = self.runs[key]
        if not run.ok:
            raise RuntimeError(f"Metric {key!r} failed:\n{run.error}")
        return run.result

    @property
    def ok(self) -> bool:
        return all(run.ok for run in self.runs.values())

    @property
    def errors(self) -> dict[str, str]:
        return {key: run.error for key, run in self.runs.items() if not run.ok}

    def summary(self) -> pd.DataFrame:
        """全 metric の summary を long 形式（metric, stat, value）にまとめる。"""
        rows = [
            {"metric": key, "stat": stat, "value": value}
            for key, run in self.runs.items()
            if run.ok
            for stat, value in run.result.summary.items()
        ]
        return pd.DataFrame(rows, columns=["metric", "stat", "value"])

    def status(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "metric": key,
                    "name": run.name,
                    "status": "ok" if run.ok else "failed",
                    "elapsed_sec": round(run.elapsed, 3),
                    "error": _last_line(run.error),
                }
                for key, run in self.runs.items()
            ]
        )

    def save(
        self,
        directory: str | Path | None = None,
        formats: list[OutputFormat] | None = None,
    ) -> Path:
        """結果を書き出す。省略時は config の output 設定を使う。"""
        from .writers import write_report

        if directory is None or formats is None:
            if self.config is None:
                raise ValueError("directory and formats are required when config is not set")
            directory = directory or self.config.output.dir
            formats = formats or self.config.output.formats
        return write_report(self, Path(directory), formats)


def _last_line(text: str | None) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else ""
