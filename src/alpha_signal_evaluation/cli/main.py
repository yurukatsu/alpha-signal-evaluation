"""typer CLI。api.evaluate() を呼ぶ薄いラッパー。

コマンド（エントリポイント ``alpha-eval``）:

- ``alpha-eval run CONFIG``: 評価を実行して結果を書き出す（``api.evaluate()``）
- ``alpha-eval validate CONFIG``: 計算せずに config を検証する（``api.validate()``）
- ``alpha-eval metrics list``: 登録済みの metric とパラメータを表示する

レイヤーの最上位で、引数の解釈・ログ設定・``.env`` の読み込み・結果の表示と終了コードだけを
担う。評価のロジックは持たない。config の誤りは赤字のメッセージと終了コード 1 で報告する。

各コマンドの docstring のうち ``\\f`` より前の部分が ``--help`` に表示される。
"""

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
    """全コマンドに共通の前処理（ログ設定と ``.env`` の読み込み）。

    \f
    ルートのログレベルを ``--verbose`` なら DEBUG、それ以外は INFO に設定する。
    DB の認証情報・ホスト名は環境変数から読むため、カレントディレクトリから親へ向かって
    探した ``.env`` があれば読み込む（既に設定されている環境変数は上書きしない）。

    Args:
        verbose: デバッグログを出すか（``--verbose`` / ``-v``）。
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # 認証情報は環境変数から読む。カレントディレクトリの .env があれば読み込む
    load_dotenv(find_dotenv(usecwd=True))


def _load(config: Path):
    """config.yaml を読み込む。構造の検証エラーはメッセージを表示して終了する。

    pydantic の ``ValidationError`` は赤字で標準エラーに表示し、終了コード 1 で終了する
    （トレースバックは出さない）。それ以外の例外（YAML の構文エラー等）はそのまま送出される。

    Args:
        config: config.yaml のパス（typer が存在を確認済み）。

    Returns:
        検証済みの ``EvaluationConfig``。

    Raises:
        typer.Exit: 構造の検証に失敗した場合（終了コード 1）。
    """
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
    """config.yaml に従って評価を実行し、結果を書き出す。

    \f
    config を読み込んで ``api.evaluate()`` で評価し、``EvaluationReport.save()`` で
    結果を書き出す。書き出し先は ``--output-dir``（省略時は config の ``output.dir``）、
    形式は config の ``output.formats``。``--output-dir`` の相対パスは実行ディレクトリ基準。
    その後、警告を黄色で、metric ごとの実行状況（metric / name / status / elapsed_sec）の
    表と出力先を表示する。

    一部の metric が失敗しても他の結果は書き出したうえで、失敗した metric のエラー
    （トレースバック）を赤字で表示し、終了コード 1 で終了する。config の意味の検証で
    エラーがあった場合は、計算せずにエラー一覧を表示して終了コード 1 で終了する。
    データ読み込み中の例外はそのまま送出される。

    Args:
        config: config.yaml のパス。
        n_jobs: config の ``execution.n_jobs`` を上書きする並列数（``--n-jobs``）。
        output_dir: config の ``output.dir`` を上書きする出力先（``--output-dir``）。

    Raises:
        typer.Exit: config の誤り、または失敗した metric がある場合（終了コード 1）。
    """
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
    """計算せずに config を検証する（構造・意味・ファイルの存在）。

    \f
    ``api.validate()`` を呼び、警告を黄色、エラーを赤字で標準エラーに表示する。
    エラーがなければ ``OK`` を表示する。

    Args:
        config: config.yaml のパス。
        skip_files: データファイルの存在確認を省略するか（``--skip-files``）。

    Raises:
        typer.Exit: 構造または意味の検証でエラーがあった場合（終了コード 1）。
    """
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
    """登録済みの metric とパラメータ一覧を表示する。

    \f
    同梱の metric と entry points で登録された外部 metric を名前順に表示する。
    各 metric について名前・説明・要求データ（``requires``）を1行で出し、続けて
    パラメータを1行ずつ表示する。既定では ``パラメータ名 = 既定値``（JSON）を、
    ``--verbose`` では各パラメータの JSON Schema を表示する。

    Args:
        verbose: パラメータの JSON Schema を表示するか（``--verbose`` / ``-v``）。
    """
    for name, cls in list_metrics().items():
        requires = ", ".join(sorted(cls.requires)) or "-"
        typer.secho(name, bold=True, nl=False)
        typer.echo(f"  {cls.description}  (requires: {requires})")
        schema = cls.Params.model_json_schema()
        # default_factory の既定値は JSON Schema に出ないため、インスタンスから取る
        defaults = cls.Params().model_dump(mode="json")
        for param, prop in schema.get("properties", {}).items():
            if verbose:
                typer.echo(f"    {param}: {json.dumps(prop, ensure_ascii=False)}")
            else:
                typer.echo(f"    {param} = {json.dumps(defaults.get(param), ensure_ascii=False)}")
