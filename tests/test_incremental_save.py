"""逐次書き出し（``evaluate(save=...)``）: 途中で落ちても、それまでの結果が残ること。"""

import json
import logging
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest
from slow_metric import SlowMetric
from typer.testing import CliRunner

from alpha_signal_evaluation import ConfigValidationError, api, evaluate
from alpha_signal_evaluation.cli import app
from alpha_signal_evaluation.metrics.category import CategoryIC
from alpha_signal_evaluation.metrics.ic import InformationCoefficient
from alpha_signal_evaluation.metrics.quantile import QuantilePerformance
from alpha_signal_evaluation.pipeline.runner import run_metrics
from alpha_signal_evaluation.report import IncrementalWriter, incremental, writers
from alpha_signal_evaluation.report.writers import _atomic

OUTPUT = {"dir": "out", "formats": ["csv", "parquet", "xlsx"], "by_date": "all"}


def _run_info(directory) -> dict:
    return json.loads((directory / "run_info.json").read_text(encoding="utf-8"))


def _files(directory) -> list[str]:
    return sorted(str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file())


@pytest.mark.parametrize("n_jobs", [1, 2])
def test_runner_reports_each_finished_metric(bundle, n_jobs, caplog):
    """on_result は metric ごとに1回呼ばれ、そこで例外が出ても実行は続く。"""
    metrics = {
        "ic": InformationCoefficient(),
        "broken": QuantilePerformance(signals=["no_such_signal"]),
        "quantile": QuantilePerformance(),
    }
    seen = []

    def on_result(run):
        seen.append((run.key, run.ok))
        if run.key == "ic":
            raise OSError("disk full")

    with caplog.at_level(logging.ERROR):
        runs = run_metrics(metrics, bundle, n_jobs=n_jobs, on_result=on_result)
    assert sorted(seen) == [("broken", False), ("ic", True), ("quantile", True)]
    assert list(runs) == ["ic", "broken", "quantile"] and runs["quantile"].ok
    assert "on_result failed for metric ic" in caplog.text


@pytest.mark.parametrize("n_jobs", [1, 2])
def test_final_output_is_the_same_as_report_save(dataset, tmp_path, n_jobs):
    """最後まで走れば、逐次書き出しの結果は report.save() と同じになる。"""
    report = evaluate(dataset.write_config(output=OUTPUT), n_jobs=n_jobs, save=True)
    saved = dataset.root / "out"
    expected = report.save(tmp_path / "expected")

    assert _files(saved) == _files(expected)  # 一時ファイル（.tmp-*）も残っていない
    assert any(f.startswith("by_date/") and f.endswith(".csv") for f in _files(saved))
    for name in _files(saved):
        if name.endswith(".csv"):
            assert (saved / name).read_bytes() == (expected / name).read_bytes(), name
        elif name.endswith(".parquet"):
            pd.testing.assert_frame_equal(
                pd.read_parquet(saved / name), pd.read_parquet(expected / name)
            )
    book, expected_book = (
        pd.read_excel(d / "report.xlsx", sheet_name=None) for d in (saved, expected)
    )
    assert list(book) == list(expected_book)
    for sheet, frame in book.items():
        pd.testing.assert_frame_equal(frame, expected_book[sheet])

    info, expected_info = _run_info(saved), _run_info(expected)
    assert info["state"] == "finished" and info["pending"] == [] and info["ok"] is True
    assert [s["metric"] for s in info["status"]] == ["ic", "quantile", "category_ic"]
    info.pop("created_at"), expected_info.pop("created_at")
    assert info == expected_info


def test_results_remain_when_interrupted(dataset, monkeypatch):
    """3つ目の metric の途中で Ctrl-C されても、先の2つの結果は xlsx も含めて残る。"""

    def interrupted(self, data):
        raise KeyboardInterrupt

    monkeypatch.setattr(CategoryIC, "compute", interrupted)
    with pytest.raises(KeyboardInterrupt):
        evaluate(dataset.write_config(output=OUTPUT), save=True)

    out = dataset.root / "out"
    info = _run_info(out)
    assert info["state"] == "aborted" and info["ok"] is False
    assert info["pending"] == ["category_ic"]
    assert [(s["metric"], s["status"]) for s in info["status"]] == [
        ("ic", "ok"),
        ("quantile", "ok"),
    ]

    ic = pd.read_csv(out / "ic" / "timeseries.csv", encoding="utf-8-sig", dtype={"date": str})
    assert len(ic) and (out / "quantile" / "returns.parquet").exists()
    assert not (out / "category_ic").exists()
    assert set(pd.read_csv(out / "summary.csv", encoding="utf-8-sig")["metric"]) == {
        "ic",
        "quantile",
    }
    date = ic["date"].iloc[0]
    assert (out / "by_date" / date / "ic" / "timeseries.csv").exists()
    sheets = pd.ExcelFile(out / "report.xlsx").sheet_names
    assert sheets[:3] == ["summary", "status", "ic.timeseries"]
    assert not any(s.startswith("category_ic") for s in sheets)
    assert not [f for f in _files(out) if ".tmp-" in f]


def test_results_remain_when_the_process_is_killed(dataset):
    """強制終了（abort() も呼ばれない）でも、終わった metric の parquet / csv は残る。"""
    config = dataset.write_config(output=OUTPUT)
    script = (
        "import os, sys\n"
        "from alpha_signal_evaluation import evaluate\n"
        "from alpha_signal_evaluation.metrics.category import CategoryIC\n"
        "CategoryIC.compute = lambda self, data: os._exit(137)\n"
        "evaluate(sys.argv[1], save=True)\n"
    )
    result = subprocess.run([sys.executable, "-c", script, str(config)], capture_output=True)
    assert result.returncode == 137, result.stderr.decode()

    out = dataset.root / "out"
    info = _run_info(out)
    assert info["state"] == "running" and info["ok"] is False  # 終わっていない実行だと分かる
    assert info["pending"] == ["category_ic"]
    assert [s["metric"] for s in info["status"]] == ["ic", "quantile"]
    assert len(pd.read_csv(out / "ic" / "timeseries.csv", encoding="utf-8-sig"))
    assert len(pd.read_parquet(out / "quantile" / "returns.parquet"))
    assert set(pd.read_parquet(out / "summary.parquet")["metric"]) == {"ic", "quantile"}
    assert any(f.startswith("by_date/") and "/quantile/" in f for f in _files(out))
    assert not (out / "report.xlsx").exists()  # xlsx は最後にまとめて書く


def test_failure_while_loading_data_is_recorded(dataset, monkeypatch):
    """データ読み込み中に落ちた場合、以前の実行の run_info.json が残ったままにならない。"""
    config = dataset.write_config(output=OUTPUT)
    out = evaluate(config).save()
    assert _run_info(out)["state"] == "finished"

    def fail(cfg):
        raise RuntimeError("database is down")

    monkeypatch.setattr(api, "build_bundle", fail)
    with pytest.raises(RuntimeError, match="database is down"):
        evaluate(config, save=True)
    info = _run_info(out)
    assert info["state"] == "aborted" and info["ok"] is False and info["status"] == []
    assert info["pending"] == ["ic", "quantile", "category_ic"]


def test_config_errors_write_nothing(dataset, tmp_path):
    config = dataset.write_config(metrics=[{"name": "nope"}])
    with pytest.raises(ConfigValidationError):
        evaluate(config, save=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_failed_incremental_write_does_not_lose_results(dataset, monkeypatch, caplog):
    """途中の書き出しに失敗しても計算は続き、最後の書き出しで全 metric が出力される。"""
    add = IncrementalWriter.add

    def flaky(self, run):
        if run.key == "ic":
            raise OSError("disk full")
        add(self, run)

    monkeypatch.setattr(IncrementalWriter, "add", flaky)
    with caplog.at_level(logging.ERROR):
        report = evaluate(dataset.write_config(output=OUTPUT), save=True)
    assert report.ok and "on_result failed for metric ic" in caplog.text
    out = dataset.root / "out"
    assert (out / "ic" / "timeseries.csv").exists()
    assert _run_info(out)["state"] == "finished"


def test_save_accepts_a_directory_and_the_default_writes_nothing(bundle, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = {"version": 1, "metrics": [{"name": "ic"}]}
    evaluate(cfg, data=bundle)
    assert not list(tmp_path.iterdir())  # save を指定しなければ何も書かない

    evaluate(cfg, data=bundle, save=tmp_path / "elsewhere")
    assert (tmp_path / "elsewhere" / "ic" / "timeseries.csv").exists()
    assert not (tmp_path / "out").exists()
    evaluate(cfg, data=bundle, save=True)  # config の output.dir（既定は ./out）
    assert (tmp_path / "out" / "ic" / "timeseries.csv").exists()


def test_cli_run_saves_incrementally(dataset, tmp_path, monkeypatch):
    """CLI も逐次書き出しを使う: 途中で落ちても --output-dir に結果が残る。"""

    def fail(self, data):
        raise KeyboardInterrupt

    monkeypatch.setattr(CategoryIC, "compute", fail)
    out = tmp_path / "cli_out"
    config = dataset.write_config(output=OUTPUT)
    result = CliRunner().invoke(app, ["run", str(config), "--output-dir", str(out)])
    assert result.exit_code != 0
    assert _run_info(out)["state"] == "aborted"
    assert (out / "ic" / "timeseries.csv").exists()
    assert not (dataset.root / "out").exists()  # --output-dir が output.dir より優先される


def test_atomic_write_keeps_the_existing_file_on_failure(tmp_path):
    path = tmp_path / "summary.csv"
    path.write_text("good")
    with pytest.raises(RuntimeError), _atomic(path) as tmp:
        tmp.write_text("half writ")
        raise RuntimeError("died while writing")
    assert path.read_text() == "good"
    assert [p.name for p in tmp_path.iterdir()] == ["summary.csv"]

    with _atomic(path) as tmp:
        tmp.write_text("new")
    assert path.read_text() == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["summary.csv"]


def test_run_info_is_marked_running_before_data_is_loaded(dataset, monkeypatch):
    """データ読み込み中に強制終了されても、以前の実行の run_info.json が終わった実行に見えない。"""
    config = dataset.write_config(output=OUTPUT)
    out = evaluate(config).save()
    assert _run_info(out)["state"] == "finished"
    seen = {}
    build = api.build_bundle

    def spy(cfg):
        seen.update(_run_info(out))  # build_bundle が呼ばれた時点の run_info.json
        return build(cfg)

    monkeypatch.setattr(api, "build_bundle", spy)
    evaluate(config, save=True)
    assert seen["state"] == "running" and seen["ok"] is False and seen["status"] == []
    assert seen["pending"] == ["ic", "quantile", "category_ic"]


def test_failed_metric_is_recorded_before_a_kill(dataset):
    """失敗した metric も、その時点で run_info.json に記録される。"""
    config = dataset.write_config(output=OUTPUT)
    script = (
        "import os, sys\n"
        "from alpha_signal_evaluation import evaluate\n"
        "from alpha_signal_evaluation.metrics.category import CategoryIC\n"
        "from alpha_signal_evaluation.metrics.quantile import QuantilePerformance\n"
        "def boom(self, data):\n"
        "    raise RuntimeError('boom')\n"
        "QuantilePerformance.compute = boom\n"
        "CategoryIC.compute = lambda self, data: os._exit(137)\n"
        "evaluate(sys.argv[1], save=True)\n"
    )
    result = subprocess.run([sys.executable, "-c", script, str(config)], capture_output=True)
    assert result.returncode == 137, result.stderr.decode()
    info = _run_info(dataset.root / "out")
    assert info["state"] == "running" and info["pending"] == ["category_ic"]
    assert [(s["metric"], s["status"]) for s in info["status"]] == [
        ("ic", "ok"),
        ("quantile", "failed"),
    ]
    assert "boom" in info["errors"]["quantile"]


def test_cli_run_with_a_failed_metric(dataset, tmp_path, monkeypatch):
    """一部の metric が失敗しても他の結果は書き出し、終了コード 1 で終わる。"""

    def boom(self, data):
        raise RuntimeError("boom")

    monkeypatch.setattr(QuantilePerformance, "compute", boom)
    out = tmp_path / "cli_out"
    config = dataset.write_config(output=OUTPUT)
    result = CliRunner().invoke(app, ["run", str(config), "--output-dir", str(out)])
    assert result.exit_code == 1
    info = _run_info(out)
    assert info["state"] == "finished" and info["ok"] is False and info["pending"] == []
    assert (out / "ic" / "timeseries.csv").exists() and (out / "category_ic").exists()
    assert not (out / "quantile").exists()


def test_abort_does_not_hide_the_original_exception(dataset, monkeypatch, caplog):
    """中断時の書き出しに失敗しても、元の例外が送出され、中断したことは記録される。"""

    def interrupted(self, data):
        raise KeyboardInterrupt

    def disk_full(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(CategoryIC, "compute", interrupted)
    monkeypatch.setattr(incremental, "write_report", disk_full)
    with caplog.at_level(logging.ERROR), pytest.raises(KeyboardInterrupt):
        evaluate(dataset.write_config(output=OUTPUT), save=True)
    assert "Failed to write the partial results" in caplog.text
    info = _run_info(dataset.root / "out")
    assert info["state"] == "aborted" and info["pending"] == ["category_ic"]


@pytest.mark.parametrize("error", [OSError("disk full"), KeyboardInterrupt()])
def test_failure_during_the_final_write_is_recorded_as_aborted(dataset, monkeypatch, error):
    """最後の書き出し（xlsx）の途中で落ちても、running のまま残らない。"""

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(writers, "_write_xlsx", fail)
    with pytest.raises(type(error)):
        evaluate(dataset.write_config(output=OUTPUT), save=True)
    out = dataset.root / "out"
    info = _run_info(out)
    assert info["state"] == "aborted" and info["ok"] is False and info["pending"] == []
    assert [s["metric"] for s in info["status"]] == ["ic", "quantile", "category_ic"]
    assert (out / "category_ic" / "summary.csv").exists()  # 計算済みの結果は残っている


def test_finished_is_recorded_only_after_everything_is_written(dataset, monkeypatch, tmp_path):
    """日付ごとの断面を書いている間は finished にならない（そこで落ちても完了に見えない）。"""
    report = evaluate(dataset.write_config(output=OUTPUT))
    seen = []
    write_by_date = writers.write_by_date

    def spy(report, directory, *args, **kwargs):
        seen.append(_run_info(directory)["state"])
        return write_by_date(report, directory, *args, **kwargs)

    monkeypatch.setattr(writers, "write_by_date", spy)
    out = report.save(tmp_path / "out")
    assert seen == ["running"] and _run_info(out)["state"] == "finished"

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(writers, "write_by_date", fail)
    with pytest.raises(OSError):
        report.save(tmp_path / "broken")
    assert _run_info(tmp_path / "broken")["state"] == "running"  # 実行の記録は残るが完了ではない


def test_metric_that_could_not_be_written_stays_pending(dataset, monkeypatch, caplog):
    """書けなかった metric は書けたことにせず、次の機会に書き直す。"""
    write_tables = incremental.write_tables
    failed, during = [], {}

    def flaky(report, directory, fmt, *args, **kwargs):
        if "ic" in report.runs and not failed:
            failed.append(fmt)
            raise OSError("disk full")
        if "category_ic" in report.runs and not during:
            during.update(_run_info(directory))  # quantile の add() が終わった時点
        return write_tables(report, directory, fmt, *args, **kwargs)

    def killed(self, data):
        raise KeyboardInterrupt

    monkeypatch.setattr(incremental, "write_tables", flaky)
    with caplog.at_level(logging.ERROR):
        report = evaluate(dataset.write_config(output=OUTPUT), save=True)
    assert report.ok and "on_result failed for metric ic" in caplog.text
    out = dataset.root / "out"
    # ic の書き出しに失敗した直後（quantile の add() の中）で ic を書き直している
    assert [s["metric"] for s in during["status"]] == ["ic", "quantile"]
    assert _run_info(out)["state"] == "finished" and (out / "ic" / "timeseries.csv").exists()

    # 書き直しにも失敗し続ける場合: ic は pending のままで、他の metric は書かれる
    def always_fails(report, directory, fmt, *args, **kwargs):
        if "ic" in report.runs:
            raise OSError("disk full")
        return write_tables(report, directory, fmt, *args, **kwargs)

    out = dataset.root / "out2"
    monkeypatch.setattr(incremental, "write_tables", always_fails)
    monkeypatch.setattr(CategoryIC, "compute", killed)
    monkeypatch.setattr(IncrementalWriter, "abort", lambda self: None)  # 強制終了の代わり
    output = OUTPUT | {"dir": "out2"}
    with pytest.raises(KeyboardInterrupt):
        evaluate(dataset.write_config(output=output), save=True)
    info = _run_info(out)
    assert info["state"] == "running" and info["pending"] == ["ic", "category_ic"]
    assert [s["metric"] for s in info["status"]] == ["quantile"]
    assert not (out / "ic").exists() and (out / "quantile" / "returns.csv").exists()
    assert set(pd.read_csv(out / "summary.csv", encoding="utf-8-sig")["metric"]) == {"quantile"}


def test_final_write_does_not_rewrite_what_was_already_written(dataset, monkeypatch):
    """最後の書き出しで、逐次書き出し済みの parquet / csv を書き直さない。"""
    written = []
    write_table = writers._write_table

    def spy(table, path, fmt, index):
        written.append(str(path.relative_to(dataset.root / "out")))
        return write_table(table, path, fmt, index)

    monkeypatch.setattr(writers, "_write_table", spy)
    evaluate(dataset.write_config(output=OUTPUT), save=True)
    tables = [name for name in written if not name.startswith("summary.")]
    assert len(tables) == len(set(tables)) and "ic/timeseries.csv" in tables
    assert any(name.startswith("by_date/") for name in tables)


def test_parallel_run_stops_promptly_when_interrupted(bundle):
    """並列実行中に中断されたら、残りの metric を実行せずに止まり、終わった分は渡される。"""
    metrics = {"ic": InformationCoefficient()}
    metrics |= {f"slow{i}": SlowMetric(seconds=1.0) for i in range(12)}
    seen = []

    def on_result(run):
        seen.append(run.key)
        if len(seen) == 1:
            raise KeyboardInterrupt

    start = time.perf_counter()
    with pytest.raises(KeyboardInterrupt):
        run_metrics(metrics, bundle, n_jobs=2, on_result=on_result)
    elapsed = time.perf_counter() - start
    assert seen and len(seen) == len(set(seen))
    # 12 個を2並列で最後まで走らせると 6 秒以上かかる。中断後は残りを実行しない
    assert len(seen) < len(metrics) / 2 and elapsed < 5, (seen, elapsed)


def test_writers_keep_existing_files_when_a_write_fails_midway(bundle, tmp_path, monkeypatch):
    """書いている途中で失敗しても、既にあるファイルは壊れず、一時ファイルも残らない。"""
    report = evaluate({"version": 1, "metrics": [{"name": "ic"}]}, data=bundle)
    out = report.save(tmp_path / "out", formats=["csv", "parquet"])
    names = ["run_info.json", "summary.csv", "summary.parquet", "ic/timeseries.csv"]
    before = {n: (out / n).read_bytes() for n in names}
    listing = _files(out)

    def half_json(obj, f, **kwargs):
        f.write('{"created_at": ')
        raise OSError("disk full")

    def half_file(self, path, **kwargs):
        Path(path).write_bytes(b"half")
        raise OSError("disk full")

    monkeypatch.setattr(writers.json, "dump", half_json)
    monkeypatch.setattr(pd.DataFrame, "to_csv", half_file)
    monkeypatch.setattr(pd.DataFrame, "to_parquet", half_file)
    with pytest.raises(OSError):
        writers.write_run_info(report, out, "running", ["x"])
    for fmt in ("csv", "parquet"):
        with pytest.raises(OSError):
            writers.write_summary(report, out, fmt)
        with pytest.raises(OSError):
            writers.write_tables(report, out, fmt)
    monkeypatch.undo()

    assert {n: (out / n).read_bytes() for n in names} == before
    assert _run_info(out)["state"] == "finished"
    assert _files(out) == listing


def test_rerun_removes_previous_files_of_a_failed_metric(dataset, monkeypatch):
    """前回成功した metric が今回失敗したら、その metric の前回の表は残さない。"""
    config = dataset.write_config(output=OUTPUT)
    out = dataset.root / "out"
    evaluate(config, save=True)
    assert (out / "quantile" / "returns.csv").exists()
    assert any(out.glob("by_date/*/quantile/returns.parquet"))
    (out / "notes.txt").write_text("keep")  # 出力が書いたものでないファイルには触れない

    def boom(self, data):
        raise RuntimeError("boom")

    monkeypatch.setattr(QuantilePerformance, "compute", boom)
    evaluate(config, save=True)
    info = _run_info(out)
    assert info["state"] == "finished" and info["errors"].keys() == {"quantile"}
    assert not (out / "quantile").exists()
    assert not any(out.glob("by_date/*/quantile"))
    assert (out / "ic" / "timeseries.csv").exists() and (out / "notes.txt").read_text() == "keep"
    assert not any(s.startswith("quantile.") for s in pd.ExcelFile(out / "report.xlsx").sheet_names)


def test_rerun_replaces_only_the_formats_it_writes(dataset, tmp_path):
    """成功した metric は今回書く形式だけを置き換え、ほかの形式のファイルは残す。"""
    report = evaluate(dataset.write_config())
    out = report.save(tmp_path / "out", ["csv", "parquet"], by_date="all")
    stale = out / "ic" / "old_table.csv"  # 以前の版の metric が書いていた表
    stale.write_text("old")
    report.save(out, ["csv"], by_date="all")
    assert not stale.exists()
    assert (out / "ic" / "timeseries.csv").exists() and (out / "ic" / "timeseries.parquet").exists()


def test_rerun_through_evaluate_replaces_stale_tables(dataset, monkeypatch):
    """evaluate(save=...) で再実行すると、metric を書く時点でその metric の以前の表を消す。"""
    config = dataset.write_config(output=OUTPUT)
    out = dataset.root / "out"
    evaluate(config, save=True)
    stale = out / "ic" / "old_table.csv"
    stale.write_text("old")
    date = sorted(p.name for p in (out / "by_date").iterdir())[0]
    stale_by_date = out / "by_date" / date / "ic" / "old.csv"
    stale_by_date.write_text("old")
    seen = {}
    add = IncrementalWriter.add

    def spy(self, run):
        add(self, run)
        if run.key == "ic":  # finish() より前（ic を書き終えた直後）の状態
            seen["stale"] = stale.exists() or stale_by_date.exists()

    monkeypatch.setattr(IncrementalWriter, "add", spy)
    evaluate(config, save=True)
    assert seen == {"stale": False}
    assert (out / "ic" / "timeseries.csv").exists()


def test_cleanup_does_not_follow_symlinks(dataset, tmp_path):
    """metric のディレクトリがシンボリックリンクなら、リンク先のファイルを消さない。"""
    report = evaluate(dataset.write_config())
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.csv").write_text("keep")
    out = tmp_path / "out"
    out.mkdir()
    (out / "ic").symlink_to(elsewhere, target_is_directory=True)
    try:
        report.save(out, ["csv"])
    except OSError:
        pass  # リンク先に書けるかどうかは環境しだい。消さないことだけを確かめる
    assert (elsewhere / "precious.csv").read_text() == "keep"
