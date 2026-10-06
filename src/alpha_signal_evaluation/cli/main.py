"""typer CLI。api.evaluate() を呼ぶ薄いラッパー。"""

import json
import logging
from pathlib import Path
from typing import Annotated

import typer
from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError

from ..api import evaluate, validate
from ..config import load_config
from ..metrics.registry import list_metrics
from ..validation import ConfigValidationError

app = typer.Typer(no_args_is_help=True, help="Alpha signal evaluation")
metrics_app = typer.Typer(no_args_is_help=True, help="Registered metrics")
app.add_typer(metrics_app, name="metrics")

ConfigArg = Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="config.yaml")]


@app.callback()
def _setup(
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # 認証情報は環境変数から読む。カレントディレクトリの .env があれば読み込む
    load_dotenv(find_dotenv(usecwd=True))


def _load(config: Path):
    try:
        return load_config(config)
    except ValidationError as e:
        typer.secho(f"Invalid config: {config}\n{e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@app.command()
def run(
    config: ConfigArg,
    n_jobs: Annotated[int | None, typer.Option(help="Override execution.n_jobs")] = None,
    output_dir: Annotated[Path | None, typer.Option(help="Override output.dir")] = None,
) -> None:
    """config.yaml に従って評価を実行し、結果を書き出す。"""
    cfg = _load(config)
    try:
        report = evaluate(cfg, n_jobs=n_jobs)
    except ConfigValidationError as e:
        typer.secho(str(e), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    out = report.save(output_dir)
    for w in report.warnings:
        typer.secho(f"warning: {w}", fg=typer.colors.YELLOW, err=True)
    typer.echo(report.status().drop(columns="error").to_string(index=False))
    typer.echo(f"Results: {out}")
    if not report.ok:
        for key, error in report.errors.items():
            typer.secho(f"[{key}] {error}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


@app.command("validate")
def validate_command(
    config: ConfigArg,
    skip_files: Annotated[bool, typer.Option(help="Skip checking file existence")] = False,
) -> None:
    """計算せずに config を検証する（構造・意味・ファイルの存在）。"""
    report = validate(_load(config), check_files=not skip_files)
    for w in report.warnings:
        typer.secho(f"warning: {w}", fg=typer.colors.YELLOW, err=True)
    if not report.ok:
        for e in report.errors:
            typer.secho(f"error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.secho("OK", fg=typer.colors.GREEN)


@metrics_app.command("list")
def list_command(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show parameter schemas")
    ] = False,
) -> None:
    """登録済みの metric とパラメータ一覧を表示する。"""
    for name, cls in list_metrics().items():
        requires = ", ".join(sorted(cls.requires)) or "-"
        typer.secho(name, bold=True, nl=False)
        typer.echo(f"  {cls.description}  (requires: {requires})")
        schema = cls.Params.model_json_schema()
        for param, prop in schema.get("properties", {}).items():
            if verbose:
                typer.echo(f"    {param}: {json.dumps(prop, ensure_ascii=False)}")
            else:
                default = json.dumps(prop.get("default"), ensure_ascii=False)
                typer.echo(f"    {param} = {default}")
