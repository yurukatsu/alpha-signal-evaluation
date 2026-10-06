"""metrics の実行。1つの metric が失敗しても他の結果は返し、失敗は記録する。"""

import logging
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass

from ..data.bundle import DataBundle
from ..metrics.base import Metric, MetricResult

logger = logging.getLogger(__name__)


@dataclass
class MetricRun:
    key: str
    name: str
    result: MetricResult | None = None
    error: str | None = None  # traceback
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None


def _run_one(key: str, metric: Metric, data: DataBundle) -> MetricRun:
    start = time.perf_counter()
    try:
        result = metric.compute(data)
        if not isinstance(result, MetricResult):
            raise TypeError(f"compute() must return MetricResult, got {type(result).__name__}")
        return MetricRun(key, metric.name, result=result, elapsed=time.perf_counter() - start)
    except Exception:
        return MetricRun(
            key,
            metric.name,
            error=traceback.format_exc(),
            elapsed=time.perf_counter() - start,
        )


# ProcessPoolExecutor はタスクごとに引数を pickle するため、DataBundle は
# initializer でワーカーごとに1回だけ渡し、タスクには metric（小さい）だけを渡す
_WORKER_DATA: DataBundle | None = None


def _init_worker(data: DataBundle) -> None:
    global _WORKER_DATA
    _WORKER_DATA = data


def _run_in_worker(key: str, metric: Metric) -> MetricRun:
    assert _WORKER_DATA is not None
    return _run_one(key, metric, _WORKER_DATA)


def run_metrics(
    metrics: dict[str, Metric], data: DataBundle, n_jobs: int = 1
) -> dict[str, MetricRun]:
    """metrics（キー -> インスタンス）を実行し、config の順序で結果を返す。"""
    runs: dict[str, MetricRun] = {}
    if n_jobs <= 1 or len(metrics) <= 1:
        for key, metric in metrics.items():
            logger.info("Running metric %s", key)
            runs[key] = _run_one(key, metric, data)
            _log(runs[key])
    else:
        workers = min(n_jobs, len(metrics))
        with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(data,)) as pool:
            futures = {pool.submit(_run_in_worker, k, m): k for k, m in metrics.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    runs[key] = future.result()
                except Exception:  # pickle の失敗やワーカーの異常終了
                    runs[key] = MetricRun(key, metrics[key].name, error=traceback.format_exc())
                _log(runs[key])
    return {key: runs[key] for key in metrics}


def _log(run: MetricRun) -> None:
    if run.ok:
        logger.info("Metric %s finished in %.2fs", run.key, run.elapsed)
    else:
        logger.error("Metric %s failed:\n%s", run.key, run.error)
