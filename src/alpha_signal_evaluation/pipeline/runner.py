"""metrics の実行。1つの metric が失敗しても他の結果は返し、失敗は記録する。

pipeline のうち「計算の実行」を担う層。データの読み込みと時点合わせ
（``pipeline/preprocess.py``）は済んだ DataBundle を受け取り、各 metric の
``compute()`` を呼ぶだけで、結果の書き出し（``report``）は扱わない。

- エラーの隔離: metric ごとに例外を捕捉し、トレースバック文字列を ``MetricRun.error``
  に記録する。ある metric の失敗で他の metric の実行や結果は失われない。
- 並列実行: ``n_jobs > 1`` かつ metric が2つ以上なら ProcessPoolExecutor で並列に
  実行する。DataBundle は initializer でワーカープロセスごとに1回だけ渡し
  （タスクごとの pickle を避ける）、各タスクには metric のキーとインスタンスだけを渡す。
- 結果は完了順によらず、渡された metrics の順序（config の順序）で返す。
- 完了の通知: ``on_result`` を渡すと、metric が1つ終わるたびに（成功・失敗とも）その
  MetricRun を渡して呼ぶ。途中でプロセスが落ちてもそれまでの結果を残せるよう、呼び出し側
  （``api.evaluate(save=...)``）が逐次書き出すために使う。ここでは書き出しを扱わない。
"""

import logging
import time
import traceback
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from dataclasses import dataclass

from ..data.bundle import DataBundle
from ..metrics.base import Metric, MetricResult

logger = logging.getLogger(__name__)


@dataclass
class MetricRun:
    """1つの metric の実行結果（成功時の結果、または失敗時のトレースバック）。

    Attributes:
        key: metric のキー（config の ``id``、なければ ``name``）。結果の辞書のキーで、
            レポートの出力先ディレクトリ名にもなる。
        name: metric の登録名（``Metric.name``）。
        result: 成功時の MetricResult。失敗時は None。
        error: 失敗時のトレースバック（``traceback.format_exc()`` の文字列）。
            成功時は None。
        elapsed: 実行時間（秒、``time.perf_counter`` で計測）。pickle の失敗や
            ワーカーの異常終了で計測できなかった場合は 0.0。
    """

    key: str
    name: str
    result: MetricResult | None = None
    error: str | None = None  # traceback
    elapsed: float = 0.0

    @property
    def ok(self) -> bool:
        """成功したか（``error`` が None なら True）。"""
        return self.error is None


def _run_one(key: str, metric: Metric, data: DataBundle) -> MetricRun:
    """``metric.compute(data)`` を実行し、成功・失敗を MetricRun に包んで返す。

    ``Exception`` を捕捉してトレースバックを ``error`` に入れる（KeyboardInterrupt など
    ``BaseException`` だけを継承する例外は捕捉しない）。``compute()`` が MetricResult
    以外を返した場合も TypeError として失敗扱いにする。どの場合も経過時間を記録する。
    """
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
    """ProcessPoolExecutor の initializer。

    ワーカープロセスのグローバル変数 ``_WORKER_DATA`` に DataBundle を保持する。
    """
    global _WORKER_DATA
    _WORKER_DATA = data


def _run_in_worker(key: str, metric: Metric) -> MetricRun:
    """ワーカープロセス内で、initializer で受け取った DataBundle に対して metric を実行する。"""
    assert _WORKER_DATA is not None
    return _run_one(key, metric, _WORKER_DATA)


def run_metrics(
    metrics: dict[str, Metric],
    data: DataBundle,
    n_jobs: int = 1,
    on_result: Callable[[MetricRun], None] | None = None,
) -> dict[str, MetricRun]:
    """metrics（キー -> インスタンス）を実行し、config の順序で結果を返す。

    ``n_jobs <= 1`` または metric が1つ以下なら、同じプロセスで順に実行する。
    それ以外は ``min(n_jobs, len(metrics))`` 個のワーカープロセスで並列に実行する。
    並列時は DataBundle を initializer でワーカーごとに1回だけ渡すため、DataBundle と
    metric インスタンスは pickle 可能である必要がある。pickle の失敗やワーカーの
    異常終了もその metric の失敗として記録する（``elapsed`` は 0.0）。

    metric の失敗で例外は送出しない。各 metric の完了時に、成功なら INFO（経過時間）、
    失敗なら ERROR（トレースバック付き）でログを出す。

    ``on_result`` は metric が1つ終わるたびに、このプロセス（並列時も親プロセス）で
    完了順に呼ぶ。``on_result`` が例外を送出しても、ERROR のログを出して実行を続ける
    （書き出しの失敗で計算結果を失わないため）。

    Ctrl-C などで中断された場合（``KeyboardInterrupt`` 等が送出される）、並列実行では
    その時点で終わっている metric を ``on_result`` に渡したうえで、実行中のワーカーを
    止め、残りの metric は実行しない（実行中だった metric の結果は残らない）。

    Args:
        metrics: metric のキー -> Metric インスタンス（config の順序）。
        data: 時点合わせ済みのデータ。
        n_jobs: 並列に使うプロセス数の上限。1 以下なら逐次実行。
        on_result: metric が終わるたびに MetricRun を渡して呼ぶ関数（成功・失敗とも）。

    Returns:
        metric のキー -> MetricRun。順序は ``metrics`` と同じ（完了順ではない）。
    """
    runs: dict[str, MetricRun] = {}
    if n_jobs <= 1 or len(metrics) <= 1:
        for key, metric in metrics.items():
            logger.info("Running metric %s", key)
            runs[key] = _run_one(key, metric, data)
            _done(runs[key], on_result)
    else:
        workers = min(n_jobs, len(metrics))
        pool = ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(data,))
        futures: dict[Future, str] = {}
        try:
            futures = {pool.submit(_run_in_worker, k, m): k for k, m in metrics.items()}
            for future in as_completed(futures):
                key = futures[future]
                try:
                    runs[key] = future.result()
                except Exception:  # pickle の失敗やワーカーの異常終了
                    runs[key] = MetricRun(key, metrics[key].name, error=traceback.format_exc())
                _done(runs[key], on_result)
        except BaseException:
            # Ctrl-C など: 既に終わっている metric は on_result に渡し、残りは実行せずに止める
            for future, key in futures.items():
                if key not in runs and future.done() and not future.cancelled():
                    if future.exception() is None:
                        runs[key] = future.result()
                        _done(runs[key], on_result)
            _stop_pool(pool)
            raise
        pool.shutdown(wait=True)
    return {key: runs[key] for key in metrics}


def _stop_pool(pool: ProcessPoolExecutor) -> None:
    """中断されたプールを、残りの metric を実行させずに止める。

    ``ProcessPoolExecutor`` を ``with`` で抜ける（``shutdown(wait=True)``）と、キューに
    残っている metric をワーカーがすべて実行し終えるまで待ってしまう。ここでは実行中の
    ワーカーを終了させ、キューの metric を取り消して、すぐ呼び出し側に戻る。
    Python 3.14 以降は公開メソッド ``terminate_workers()`` を使い、それより前は内部の
    ワーカー一覧（``_processes``）を使う。どちらも使えなければ、実行中の metric が終わるのを
    待ってから戻る（キューの metric は取り消す）。
    """
    terminate = getattr(pool, "terminate_workers", None)
    if terminate is not None:
        terminate()
        return
    processes = getattr(pool, "_processes", None)
    if processes is None:
        pool.shutdown(wait=True, cancel_futures=True)
        return
    for process in list(processes.values()):
        process.terminate()
    pool.shutdown(wait=False, cancel_futures=True)


def _done(run: MetricRun, on_result: Callable[[MetricRun], None] | None) -> None:
    """完了した metric をログに出し、``on_result`` があれば呼ぶ（その例外は記録して続行する）。"""
    _log(run)
    if on_result is None:
        return
    try:
        on_result(run)
    except Exception:
        logger.exception("on_result failed for metric %s", run.key)


def _log(run: MetricRun) -> None:
    """MetricRun の結果をログに出す（成功: INFO で経過時間、失敗: ERROR でトレースバック）。"""
    if run.ok:
        logger.info("Metric %s finished in %.2fs", run.key, run.elapsed)
    else:
        logger.error("Metric %s failed:\n%s", run.key, run.error)
