# metric の追加方法

[マニュアルの目次に戻る](README.md)

新しい評価指標（metric）をこのモジュールに追加する手順書です。Python と pandas は書けるが、このモジュールのコードは読んでいない方を対象にしています。追加に必要なのは Python ファイル1つです。

コードの下書きを AI チャットツールに書かせたい場合は、[06_ai_prompt.md の「プロンプト」](06_ai_prompt.md#プロンプト) を使ってください。この文書で説明する契約を、AI に渡せる形にまとめてあります。

ほかの文書との関係は次のとおりです。

- 組み込みの metric の仕様: [04_metrics.md の 3 章](04_metrics.md#3-metric-ごとの仕様)
- config.yaml の `metrics` の書き方: [03_config.md の 8 章](03_config.md#8-metrics)
- 背景にある設計方針: [01_design.md の 2 章](01_design.md#2-4つの原則)
- notebook で試作しながら進めたい場合: 同じ流れを別の metric を題材にたどるチュートリアル [notebooks/metric_development_tutorial.ipynb](../notebooks/metric_development_tutorial.ipynb)

## 1. 全体像

metric は「`DataBundle` を受け取り `MetricResult` を返すクラス」です。

- `DataBundle`: データ準備の段階で用意される評価データ一式です。シグナル、フォワードリターン、分類、リスクモデルなどを持ち、同じ行のシグナルとフォワードリターンは時点合わせ済みです。
- `MetricResult`: 計算結果の入れ物です。表（`tables`）、数値の要約（`summary`）、注記（`notes`）を持ちます。

データ準備とは、config に従ってデータを読み込み、時点合わせをして `DataBundle` を組み立てる処理です（コードでは `pipeline/preprocess.py`）。時点合わせは、ある行のシグナルに、その行より後の期間のリターンを対応させる処理で、データ準備だけが行います（仕様は [02_features.md の 4 章](02_features.md#4-時点合わせの仕様)）。

metric はデータの結合と集計だけを行います。データの読み込み、時点合わせ、ファイルへの出力は metric の外側で行われます。

カレンダー（1行が1評価期間）、ホライズン、フォワードリターン、リターン系列（config の `returns` で名前を付けたリターン）などの用語は [01_design.md の「この文書で使う用語」](01_design.md#この文書で使う用語) にまとめてあります。そこにない用語で、この文書で使うものは次のとおりです。

| 用語 | 意味 |
|---|---|
| metric のキー | 実行結果を区別する名前。config の `metrics` で付けた `id`、なければ `name` です。出力先のディレクトリ名になります（[03_config.md の 8 章](03_config.md#8-metrics)） |
| 契約 | metric のクラスが満たす決まり（定義する項目、データの受け取り方、結果の返し方）。[4 章](#4-契約の詳細) で説明します |
| 契約テスト | 登録されている全 metric が契約を満たすことを自動で確かめるテスト（[9 章](#9-テスト)） |

追加する方法は2つあります。

| 方法 | 置き場所 | 使う場面 |
|---|---|---|
| ファイルを置く | `src/alpha_signal_evaluation/metrics/<名前>.py` | このリポジトリに組み込む（通常はこちら） |
| entry points | 自分のパッケージ | このリポジトリを変更せずに追加する（[10 章](#10-別パッケージから追加する場合entry-points)） |

どちらの方法でも、次の機能は自動で付きます。metric のファイル以外のコード（登録、config、CLI、出力）を変更する必要はありません。

| 自動で付くもの | 内容 |
|---|---|
| config からの実行 | config.yaml の `metrics` に `- name: <名前>` と書けば実行されます。`params` は metric の `Params` で検証されます（[03_config.md の 8 章](03_config.md#8-metrics)） |
| 計算前の検証 | 未知のパラメータ、値の範囲外、存在しないシグナル名・リターン系列名・ホライズン、必要なデータの不足を、データを読む前に検出します（[03_config.md の 11 章](03_config.md#11-検証とよくある設定ミス)） |
| CLI | `alpha-eval metrics list` に名前・説明・必要なデータ・パラメータの既定値が表示されます |
| ファイルへの出力 | `tables` の各表が `<出力先>/<キー>/<表の名前>.csv`（または parquet、xlsx のシート）に、`summary` が `summary.csv` に、`notes` が `run_info.json` に書き出されます（[02_features.md の 6.1](02_features.md#61-ディレクトリ構成と形式)） |
| 日付ごとの断面 | `date` 列を持つ表は、config の `output.by_date` を指定すると `by_date/<日付>/` にも書き出されます（[02_features.md の 6.4](02_features.md#64-日付ごとの断面outputby_date)） |
| 逐次書き出し | `alpha-eval run` と `evaluate(save=...)` は、metric が1つ終わるたびに、その表と日付ごとの断面（csv / parquet）、`summary`、`run_info.json` を書き出します。xlsx は最後にまとめて書きます。実行が途中で止まっても、どの metric まで書けたかは `run_info.json` の `state` と `pending` でわかります（[02_features.md の 6.5](02_features.md#65-逐次書き出し途中で落ちたときに残るもの)） |
| 並列実行 | config の `execution.n_jobs`（または `--n-jobs`）を 2 以上にすると、metric を複数のプロセスで並列に実行します（[02_features.md の 7.3](02_features.md#73-並列実行)） |
| 失敗の隔離 | `compute` の中で例外が起きても、その metric だけが失敗として記録され、他の metric の結果は出力されます |
| 契約テスト | `tests/metrics/test_contract.py` が登録済みの全 metric を対象にするので、追加した metric も自動で検証されます（[9 章](#9-テスト)） |

## 2. 最短手順

リポジトリのルートで実行します。`<名前>` は追加する metric の名前です。

1. テンプレートをコピーします。ファイル名は `_` で始めないでください（`_` で始まるモジュールは自動で読み込まれません）。

   ```sh
   cp src/alpha_signal_evaluation/metrics/_template.py src/alpha_signal_evaluation/metrics/<名前>.py
   ```

2. コピーしたファイルの、クラス名・`name`・`description`・`requires`・`Params`・`compute` を書き換えます。クラスに付いている `@register_metric` はそのまま残します。これが metric を登録します。`name` は必ず変えてください（テンプレートの `my_metric` のままだと、`tests/metrics/test_registry.py` の「テンプレートは登録されない」ことを確かめるテストが失敗します）。
3. 登録されたことを確認します。

   ```sh
   uv run alpha-eval metrics list
   ```

4. config.yaml の `metrics` に追加します。`params` には `Params` で定義した項目を書きます（下はテンプレートの `min_obs` を指定した例です。`metrics` の各項目の書き方は [03_config.md の 8 章](03_config.md#8-metrics)）。

   ```yaml
   metrics:
     - name: <名前>
       params: {min_obs: 20}     # 省略するとすべて既定値
   ```

5. 検証してから実行します。

   ```sh
   uv run alpha-eval validate config.yaml
   uv run alpha-eval run config.yaml
   ```

6. テストと lint を通します。

   ```sh
   uv run pytest tests/metrics/test_contract.py   # 契約テスト（追加した metric も自動で対象になる）
   uv run pytest                                  # 全体
   uv run ruff check src tests
   uv run ruff format src tests
   ```

## 3. 動く完成例: `top_pct_excess`

例として「シグナル上位の銘柄の超過リターン」を追加します。日付ごとに、シグナル上位 `top_pct` % の銘柄のフォワードリターンの等ウェイト平均から、全銘柄の平均を引いたものです。

この metric はこのマニュアルのための例で、リポジトリには含まれていません。試す場合は、次の内容を `src/alpha_signal_evaluation/metrics/top_pct_excess.py` として保存します。試し終えたら、このファイルと、後で作る `examples/example1/config.yaml` を削除してください（置いたままにすると `top_pct_excess` が登録されたままになります）。

### ファイルの全文

```python
"""シグナル上位銘柄の超過リターンの metric ``top_pct_excess``。

日付ごとに、シグナル上位 ``top_pct`` % の銘柄のフォワードリターンの等ウェイト平均から
全銘柄の平均を引いた超過リターンを求め、その時系列を要約する。
"""

import pandas as pd
from pydantic import Field

from ..data.bundle import DataBundle
from ..data.columns import DATE
from ._common import concat, flatten_summary, summarize_series
from .base import Metric, MetricResult, SignalReturnParams
from .registry import register_metric


class TopPctExcessParams(SignalReturnParams):
    """``top_pct_excess`` のパラメータ。

    Attributes:
        signals: 評価するシグナル名。デフォルト ``None``（全シグナル）。
        returns: 評価するリターン系列名。デフォルト ``None``（全系列）。
        horizons: 評価するホライズン。デフォルト ``None``（全ホライズン）。
        top_pct: 上位として扱う銘柄の割合（%、0 より大きく 100 未満）。デフォルト 20。
        min_obs: 超過リターンを計算する日付ごとの最小銘柄数（2 以上）。デフォルト 10。
            未満の日付の ``excess`` は欠損。
    """

    top_pct: float = Field(default=20.0, gt=0, lt=100)
    min_obs: int = Field(default=10, ge=2)


@register_metric
class TopPctExcess(Metric):
    """シグナル上位 ``top_pct`` % の銘柄の超過リターン（上位の平均 − 全銘柄の平均）。

    シグナル × リターン系列 × ホライズンの組ごとに、``data.aligned`` で取り出した
    同じ (date, asset_id) のシグナルとフォワードリターンについて、日付ごとに次を求める。
    全銘柄は、その日付にシグナルとフォワードリターンが揃っている銘柄。上位は、シグナルの
    降順の順位（同順位は出現順）が「銘柄数 × top_pct / 100」以下の銘柄。

    超過リターンの時系列から平均・標準偏差・t 値・正の割合・期間数を求める。t 値は
    lag = horizon − 1 の Newey-West。h > 1 でも h 期間のリターンのまま扱う。

    出力:
        tables["timeseries"]: 日付ごとの値（評価対象の全カレンダー行。データのない日付は
            欠損）。columns=[date, top_mean, universe_mean, n_top, n, excess, signal,
            returns, horizon]。``n`` は全銘柄数、``n_top`` は上位の銘柄数。
        tables["summary"]: 1行が (signal, returns, horizon) の1組。
            columns=[signal, returns, horizon, mean_excess, std_excess, t_stat, hit_rate,
            n_periods]。
        summary: ``"{signal}/{returns}/h{horizon}/mean_excess"`` と
            ``"{signal}/{returns}/h{horizon}/t_stat"``。
    """

    name = "top_pct_excess"
    description = "Excess forward return of the top X% assets by signal over the universe mean"
    requires = frozenset({"signals", "forward_returns"})
    Params = TopPctExcessParams

    def compute(self, data: DataBundle) -> MetricResult:
        """全ての (signal, returns, horizon) の組について超過リターンの時系列と要約を求める。

        Args:
            data: データ準備で時点合わせを済ませたデータ。

        Returns:
            ``timeseries`` / ``summary`` の表、平均超過リターンと t 値の summary、
            注記を持つ MetricResult。
        """
        p = self.params
        dates = pd.Index(data.dates, name=DATE)  # 評価対象の全カレンダー行
        series, summary = [], []
        for signal in data.resolve_signals(p.signals):
            for ret in data.resolve_returns(p.returns):
                for h in data.resolve_horizons(p.horizons):
                    # index=(date, asset_id)、columns=[signal, "forward_return"]。時点合わせ済み
                    df = data.aligned(signal, ret, h)
                    by_date = df.groupby(level=DATE)
                    # 日付内の降順の順位を銘柄数で割ったもの（最大のシグナルが 1 / 銘柄数）
                    rank = by_date[signal].rank(ascending=False, method="first", pct=True)
                    top = df["forward_return"].where(rank <= p.top_pct / 100).groupby(level=DATE)
                    ts = pd.DataFrame(
                        {
                            "top_mean": top.mean(),
                            "universe_mean": by_date["forward_return"].mean(),
                            "n_top": top.count(),
                            "n": by_date.size(),
                        }
                    )
                    # 銘柄数が足りない日付は欠損にする（0 にはしない）
                    excess = ts["top_mean"] - ts["universe_mean"]
                    ts["excess"] = excess.where(ts["n"] >= p.min_obs)
                    ts = ts.reindex(dates)  # データのない日付も欠損の行として残す
                    key = {"signal": signal, "returns": ret, "horizon": h}
                    series.append(ts.assign(**key).reset_index())
                    summary.append({**key, **summarize_series(ts["excess"], h)})

        summary = pd.DataFrame(summary).rename(columns={"mean": "mean_excess", "std": "std_excess"})
        return MetricResult(
            tables={"timeseries": concat(series), "summary": summary},
            summary=flatten_summary(
                summary, ["signal", "returns", "horizon"], ["mean_excess", "t_stat"]
            ),
            notes=[
                f"Top = top {p.top_pct:g}% of assets by signal (equal-weighted).",
                "t_stat is Newey-West with lag = horizon - 1 (overlapping horizons).",
            ],
        )
```

このファイルで押さえている点は次のとおりです。

- シグナルとフォワードリターンの組は `data.aligned()` で取り出し、時点はずらしていません。
- 銘柄数が足りない日付は `where` で欠損にしています。0 では埋めていません。
- 時系列の表は `data.dates`（評価対象の全カレンダー行）で reindex し、`date` 列を持たせています。
- t 値などの要約は共通ヘルパー（`summarize_series`）を使い、組み込みの `ic` と定義を揃えています。

### 登録の確認

ファイルを置くだけで登録されます。

```sh
uv run alpha-eval metrics list
```

```text
top_pct_excess  Excess forward return of the top X% assets by signal over the universe mean  (requires: forward_returns, signals)
    signals = null
    returns = null
    horizons = null
    top_pct = 20.0
    min_obs = 10
```

### config に書いて実行する

`examples/example1/config.example.yaml` を `examples/example1/config.yaml` にコピーし、`metrics` の末尾に次を追加します（`examples/example1/input` がない場合は、先に `uv run python examples/example1/make_sample_data.py` でサンプルデータを作ります）。同じ metric を設定を変えて複数回使う場合は、2つ目以降に一意な `id` を付けます（[03_config.md の 8 章](03_config.md#8-metrics)）。

```yaml
  - name: top_pct_excess
    params: {top_pct: 10, returns: [specific], horizons: [1, 3]}
  - name: top_pct_excess          # 同じ metric を別の設定で使うときは id が必須
    id: top_pct_excess_30
    params: {top_pct: 30, signals: [ai], returns: [specific], horizons: [1]}
```

```sh
uv run alpha-eval validate examples/example1/config.yaml
uv run alpha-eval run examples/example1/config.yaml
```

実行すると、組み込みの metric と並んで結果が出ます（example1 のデータは乱数から作った合成データです。シードが固定なので、`make_sample_data.py` で作り直しても下と同じ値になります）。次は `alpha-eval run` の表示のうち実行状況の表で、ログと警告の行は省いています。`elapsed_sec`（実行時間）は実行のたびに変わります。

```text
               metric               name status  elapsed_sec
                   ic                 ic     ok        0.332
             quantile           quantile     ok        0.244
quantile_q10_specific           quantile     ok        0.035
          category_ic        category_ic     ok        0.105
          alpha_decay        alpha_decay     ok        0.432
             autocorr           autocorr     ok        0.224
   factor_correlation factor_correlation     ok        0.032
       top_pct_excess     top_pct_excess     ok        0.072
    top_pct_excess_30     top_pct_excess     ok        0.023
```

出力先（`examples/example1/output`）には次のファイルができます。

| ファイル | 元になったもの |
|---|---|
| `top_pct_excess/summary.csv`、`top_pct_excess/timeseries.csv` | `tables` の2つの表 |
| `top_pct_excess_30/...` | `id: top_pct_excess_30` の分 |
| `summary.csv` | 全 metric の `summary`（列は `metric`, `stat`, `value`） |
| `run_info.json` | 実行の状態、metric ごとの成否、`notes`（各キーの意味は [02_features.md の 6.3](02_features.md#63-run_infojson)） |
| `by_date/<日付>/top_pct_excess/timeseries.csv` | `date` 列を持つ表の、その日付の行（example1 は `output.by_date: all`） |

`top_pct_excess/summary.csv`:

```text
signal,returns,horizon,mean_excess,std_excess,t_stat,hit_rate,n_periods
ai,specific,1,0.012236576241420398,0.009185391061809586,12.56855400233744,0.8863636363636364,88.0
ai,specific,3,0.02456284685472661,0.014243736342471305,15.412139635005811,0.9534883720930233,86.0
dss,specific,1,0.00794629344040993,0.010648102028072247,7.0406935309621215,0.8181818181818182,88.0
dss,specific,3,0.0143978362409434,0.0178827545574405,7.164991309081864,0.7674418604651163,86.0
```

`by_date/20240531/top_pct_excess/timeseries.csv`（`timeseries.csv` のうち `date` が `20240531` の行です。`date` 列は除かれます。metric 側の対応は不要です）:

```text
top_mean,universe_mean,n_top,n,excess,signal,returns,horizon
0.02075480266715708,0.0024344679879296145,50.0,506.0,0.018320334679227463,ai,specific,1
0.04899604090649594,0.003587259544751112,50.0,506.0,0.045408781361744824,ai,specific,3
0.022124852479017684,0.0024344679879296145,50.0,506.0,0.019690384491088068,dss,specific,1
0.04403326388864414,0.003587259544751112,50.0,506.0,0.040446004343893026,dss,specific,3
```

設定を間違えた場合は、計算を始める前にまとめて報告されます。次は `top_pct: 150`、未知のキー `top`、存在しないシグナル名とホライズン、metric 名のタイプミスを書いたときに `alpha-eval validate` が出すエラーです（`a`〜`c` は確認用に付けた `id` です。検証の仕組みとエラーの一覧は [03_config.md の 11 章](03_config.md#11-検証とよくある設定ミス)）。

```text
error: metrics[a].params.top_pct: Input should be less than 100
error: metrics[b].params.top: Extra inputs are not permitted
error: metrics[c]: unknown signals ['aii']; available: ['ai', 'dss']
error: metrics[c]: horizons [2] are not in the configured horizons [1, 3, 6]
error: metrics[top_pct_exces]: Unknown metric 'top_pct_exces'. Available: ['alpha_decay', 'autocorr', 'category_ic', 'category_quantile', 'category_returns', 'factor_correlation', 'ic', 'quantile', 'top_pct_excess']
```

## 4. 契約の詳細

metric のクラスは、基底クラス `Metric`（`metrics/base.py`）を継承し、次の項目を定義します。`check` は必要な場合だけ上書きします。

| 項目 | 種類 | 内容 |
|---|---|---|
| `name` | クラス属性（必須） | config.yaml から参照する名前 |
| `description` | クラス属性（契約テストで必須） | `alpha-eval metrics list` に表示する1行の説明 |
| `requires` | クラス属性（既定は空） | 使うデータの宣言 |
| `Params` | クラス属性（既定は項目のない `MetricParams`） | パラメータの定義（pydantic モデル） |
| `compute(self, data)` | メソッド（必須） | `DataBundle` を受け取り `MetricResult` を返す |
| `check(cls, params, spec)` | クラスメソッド（任意） | 計算前の検証の追加 |

### `name` と `description`

- `@register_metric` は登録時に、`Metric` のサブクラスであること、`compute` を実装していること、`name` が英数字で始まる英数字・`_`・`-` だけの文字列（64 文字まで。出力のディレクトリ名になるため）であること、`Params` が `MetricParams` のサブクラスであることを確認します。満たさない場合は `TypeError` になります。
- `name` は他の metric と重複できません。重複すると登録時に `ValueError` になります。`metrics/` に置いたファイルでこれが起きると、metric の読み込みがそこで止まり、`alpha-eval` のコマンドは `Metric name 'ic' is already registered by ...` というエラーになります。読み込みはファイル名の順なので、まだ読み込まれていなかった metric が `Unknown metric` と報告されることもあります。その場合も原因は `name` の重複です。
- `name`（config で `id` を付けた場合は `id`）は、出力先のディレクトリ名と xlsx のシート名（`<キー>.<表の名前>`、31 文字で切り詰め）に使われます。組み込みの metric は英小文字とアンダースコアの短い名前にしています。
- `description` は空にできません（契約テストで検証されます）。組み込みの metric は英語の1行で書いています。
- 1つのファイルに複数の metric を置くこともできます（`category.py` は3つ定義しています）。

### `requires`: 使うデータの宣言

`compute` で使うデータの名前を `frozenset` で書きます。書ける名前は次のとおりです。これ以外の名前（例: `"signal"`）を書くと、クラスを定義した時点で `TypeError` になります。

| 名前 | 使えるデータ | config で用意される条件 |
|---|---|---|
| `signals` | `data.signals` | `data` セクションがある |
| `forward_returns` | `data.forward_returns`、`data.return_aggregations` | `returns` が1つ以上ある |
| `classification` | `data.classification` | `data.classification` がある |
| `risk_models` | `data.risk_models`（全構成要素） | `risk_models` がある |
| `risk_models.exposures` / `risk_models.factor_covariance` / `risk_models.specific_risk` / `risk_models.factor_returns` | リスクモデルのその構成要素だけ | `risk_models` にその構成要素がある |
| `benchmark_weights` | `data.benchmark_weights` | `benchmark` がある（`universe` のウェイトでは代わりにしない） |
| `portfolio_weights` | `data.portfolio_weights` | 未実装（config から用意する方法がありません。宣言した metric を config から実行すると必ず検証エラーになり、契約テストも通りません。未実装の項目の一覧は [02_features.md の 9 章](02_features.md#9-現時点でできないこと未実装)） |

`requires` は2か所で使われます。

- 計算前の検証: config で用意されていないデータを要求すると、`` metrics[<キー>]: requires data that is not configured: ['classification'] (set `data.classification`) `` のようなエラーになります。ベンチマーク対比の超過リターンのように、ベンチマークがないと意味のない metric は `benchmark_weights` を宣言しておけば、`benchmark` を書いていない config ではこのエラーで止まり、計算されません。
- 契約テスト: 宣言していないデータを空にした `DataBundle` が渡されます。宣言を忘れたデータを使っているとテストが失敗します。

「あれば使う」任意のデータは `requires` に書かず、`compute` の中で有無を確認します（組み込みの `factor_correlation` は `if data.risk_models:` のようにしてリスクモデルを任意で使っています）。

### `Params`: パラメータ

パラメータは pydantic モデルで定義します。config.yaml の `params` はこのモデルで検証されます。

| 基底クラス | 持っている項目 | 使う場面 |
|---|---|---|
| `MetricParams` | なし | 下の3項目を使わない metric |
| `SignalReturnParams` | `signals` / `returns` / `horizons`（いずれも既定は `None` = `DataBundle` にある全て） | シグナル × リターン系列 × ホライズンの組を評価する metric |

守ることは次のとおりです。

- 全項目に既定値を付けます。config で `params` を省略しても動くことが契約テストで要求されます。
- 値の範囲は `Field(ge=..., lt=...)` などで宣言します。項目どうしの関係は pydantic の `model_validator` で検証できます。
- 未知のキーは自動で拒否されます（基底クラスが `extra="forbid"`）。生成後の変更もできません（`frozen=True`）。`compute` の中では `self.params` を読むだけにします。
- `signals` / `returns` / `horizons` / `risk_model` という名前の項目は、config に存在する名前かどうかが自動で検証されます（`SignalReturnParams` を継承していなくても、この名前の項目があれば対象です）。
- 外れ値の処理（ウィンソライズなど）が必要な場合は、metric のパラメータとして実装します。データの読み込み側には入れない方針です（読み込み側は、config の `valid_range` の範囲外にあるリターンをデータの誤りとみなして欠損にします。これは外れ値の処理ではありません。[02_features.md の 4.6](02_features.md#46-valid_range範囲外の値の扱い)）。

Python から直接使うときは、次のどの形でもインスタンスを作れます。config.yaml の `params` は2つ目の形（辞書）で渡されます。

```python
from alpha_signal_evaluation.metrics import get_metric

cls = get_metric("top_pct_excess")
cls(top_pct=10)
cls({"top_pct": 10})
cls(cls.Params(top_pct=10))
```

### `compute`: 計算

`compute(self, data: DataBundle) -> MetricResult` を実装します。パラメータは `self.params` にあります。

- 省略可能な `signals` / `returns` / `horizons` は `data.resolve_signals(p.signals)` などで「全て」に解決します（[5 章](#5-databundle-の使い方)）。
- 戻り値は必ず `MetricResult` にします。それ以外を返すと、その metric は `TypeError` で失敗として記録されます。
- `compute` の中の例外は、metric を実行する側が metric ごとに捕捉します。失敗は `run_info.json` と CLI の表示に記録され、他の metric の実行は続きます。

### `check`: 計算前の検証

`check` はデータを読み込む前に呼ばれ、エラーメッセージのリストを返します（問題がなければ空リスト）。`alpha-eval validate` と、`alpha-eval run` / `evaluate()` の最初に実行されます。基底クラスの `check` は次を検証するので、通常は上書き不要です。

- `requires` のデータが config で用意されているか
- `params.signals` / `params.returns` / `params.horizons` が config に存在するか
- `params.aggregation.cumulative` のキーが定義済みのリターン系列か（`aggregation` が `ReturnAggregation` の場合）
- `params.risk_model` が定義済みのリスクモデルか

`Params` だけでは判断できない検証を足す場合は上書きし、`super().check()` の結果に追加して返します。第2引数の `spec`（`DataSpec`）は、データの中身を持たない「何が揃っているか」の記述で、`provides`（使えるデータ名）、`signals`、`return_aggregations`（リターン系列名 → `cumprod` / `cumsum`）、`horizons`、`risk_models`（名前 → 構成要素名）を持ちます。次は、`aggregation` が `cumsum` のリターン系列を受け付けない metric の例です。

```python
@classmethod
def check(cls, params, spec):
    errors = super().check(params, spec)  # 上の一覧の検証
    for name in params.returns or spec.return_aggregations:
        if spec.return_aggregations.get(name) == "cumsum":
            errors.append(f"return series {name!r} uses cumsum; only cumprod is supported")
    return errors
```

## 5. DataBundle の使い方

`compute` が受け取る `DataBundle`（`data/bundle.py`）の属性とメソッドの一覧です。日付キー（各行の日付を表す値）はすべて文字列（`yyyymmdd` または `yyyymm`）で、config の `calendar.index` に指定した列（example1 では `Base_day`）の値です。`asset_id` も文字列です。カレンダーは1行が1評価期間で、ホライズンの単位はカレンダーの行数です。

`DataBundle` と、その中の DataFrame は複数の metric が共有します。読むだけにして、書き換えないでください（[8 章](#8-やってはいけないこと)）。

列名・index 名の定数は `data/columns.py` にあります（`DATE = "date"`、`ASSET_ID = "asset_id"`、`CATEGORY = "category"`、`FACTOR = "factor"`、`VALUE = "value"`、`WEIGHT = "weight"`）。

### よく使うメソッド

| メソッド | 戻り値 | 内容 |
|---|---|---|
| `aligned(signal, returns, horizon)` | DataFrame。index=(`date`, `asset_id`)、columns=[シグナル名, `forward_return`] | 同じ (date, asset_id) のシグナルとフォワードリターンを結合します。どちらかが欠損の行は除かれます。存在しないシグナル名・リターン系列名・ホライズンは `KeyError` |
| `signal_panel(signal)` | DataFrame。index=`date`（`dates` の全行）、columns=`asset_id` | シグナルを wide 形式にします。シグナルが1件もない日付も欠損の行として残ります |
| `resolve_signals(names)` | `list[str]` | `None` なら全シグナル名、指定があればそのまま |
| `resolve_returns(names)` | `list[str]` | `None` なら定義済みの全リターン系列名、指定があればそのまま |
| `resolve_horizons(horizons)` | `list[int]` | `None` なら全ホライズン、指定があればその昇順 |
| `risk_model(name=None)` | `(名前, RiskModelData)` | リスクモデルを名前で引きます。`None` はリスクモデルが1つだけのときに使えます。該当がなければ `ValueError` |

`aligned()` の行 (t, i) には「行 t の銘柄 i のシグナル」と「行 t から h 行先までの銘柄 i のリターン」が並んでいます。この対応はデータ準備の段階で済んでいるので、metric は同じ行をそのまま使います。評価期間の末尾でホライズン分のリターンがない行は、フォワードリターンが欠損なので `aligned()` には現れません（[02_features.md の 4.9](02_features.md#49-期間の末尾でリターンが足りない場合)）。

### 属性

| 属性 | 形 | 内容 |
|---|---|---|
| `signals` | DataFrame。index=(`date`, `asset_id`)、columns=シグナル名 | シグナル値 |
| `forward_returns` | 辞書。リターン系列名 → ホライズン → DataFrame（index=`date`、columns=`asset_id`） | フォワードリターン。行 t の値は、行 t のシグナルに対応する h 行先までのリターンです。h 行分を束ねる方法はリターン系列の `aggregation` で決まり、`cumprod` なら複利、`cumsum` なら加算です（[02_features.md の 4.4](02_features.md#44-フォワードリターンの定義)）。通常は `aligned()` で取り出します |
| `return_aggregations` | 辞書。リターン系列名 → `"cumprod"` / `"cumsum"` | リターン系列の束ね方。期間集約、フォワードリターン、1期間あたりへの換算に使われ、累積リターンの既定にもなります |
| `classification` | DataFrame。index=(`date`, `asset_id`)、columns=[`category`, ...]。なければ `None` | 銘柄の分類（業種など） |
| `risk_models` | 辞書。リスクモデル名 → `RiskModelData` | リスクモデル。なければ空の辞書。通常は `risk_model()` で取り出します |
| `benchmark_weights` | DataFrame。index=(`date`, `asset_id`)、columns=[`weight`]。なければ `None` | ベンチマークウェイト |
| `portfolio_weights` | 辞書 | 将来の拡張用。現状は常に空です |
| `dates` | `tuple[str, ...]` | 評価対象のカレンダー行の日付キー（昇順）。config から実行した場合は、評価期間の全行が欠けずに入り、シグナルが全くない行も含みます |
| `signal_names` | `list[str]` | シグナル名（`signals` の列の順） |
| `return_names` | `list[str]` | リターン系列名（`forward_returns` のキーの順） |
| `horizons` | `list[int]` | 全ホライズン（昇順） |

このほかに、メソッド `spec()`（データの中身を持たない記述 `DataSpec` を返す。[`check` の節](#check-計算前の検証)を参照）と `restrict(requires)`（宣言していないデータを空にしたコピーを返す。契約テストが使います）があります。metric の `compute` で使うことは通常ありません。

### リスクモデル（`RiskModelData`）

`name, model = data.risk_model(p.risk_model)` で取り出します。構成要素は config で定義したものだけが入り、なければ `None`（`forward_factor_returns` は空の辞書）です。

| 属性・メソッド | 形 | 内容 |
|---|---|---|
| `exposures` | index=(`date`, `asset_id`)、columns=ファクター名 | ファクターエクスポージャー |
| `factor_covariance` | index=(`date`, `factor`)、columns=ファクター名 | 日付ごとの正方行列を縦に積んだもの。単位は年率の小数²です（`source: barra` は換算済み。ファイル・DB のソースは値を換算しないので、この単位で用意します） |
| `specific_risk` | index=(`date`, `asset_id`)、列はソースのまま | スペシフィックリスク（形式は未確定です。`source: barra` のリスクモデルでは未実装で、常に `None` です） |
| `forward_factor_returns` | 辞書。ホライズン → DataFrame（index=`date`、columns=ファクター名） | フォワードファクターリターン。`forward_returns` と同じ規則で時点合わせ済みで、h 行分を束ねる方法は加算です |
| `specific_return` | `str` または `None` | 対応するスペシフィックリターンの系列名（`forward_returns` のキー） |
| `factor_groups` | グループ名 → ファクター名のリスト、または `None` | 例: `{"style": [...], "industry": [...]}` |
| `factors(group=None)` | `list[str]` | エクスポージャーのファクター名。`group` を指定するとそのグループだけ。`factor_groups` がない場合や `group` が `factor_groups` にない場合は、エラーにならず全ファクターを返します |
| `components` | `frozenset[str]` | データが揃っている構成要素名 |

パラメータに `risk_model: str | None = None` を持たせると、存在しないリスクモデル名が計算前に検出されます。

## 6. 結果の返し方

`compute` は `MetricResult(tables=..., summary=..., notes=...)` を返します。`tables` と `summary` の少なくとも一方は空でないことが契約テストで要求されます。

| 項目 | 型 | 出力先 |
|---|---|---|
| `tables` | 表の名前 → DataFrame | `<出力先>/<キー>/<表の名前>.csv`（または parquet）、`report.xlsx` のシート |
| `summary` | キー → float | 全 metric をまとめた `summary.csv`（列は `metric`, `stat`, `value`） |
| `notes` | 文字列のリスト（計算方法の補足。組み込みの metric は英語の短い文で書いています） | `run_info.json` の `notes` |

出力先のディレクトリ構成と形式は [02_features.md の 6.1](02_features.md#61-ディレクトリ構成と形式) にあります。

表の作り方の慣例は次のとおりです。組み込みの metric に合わせておくと、利用者が結果を同じ方法で読めます。

- 時系列の表には `date` 列を持たせます。`date` 列（または index の `date` レベル）を持つ表は、`output.by_date` を指定したときに自動で日付ごとの断面に切り出されます。値は `data.dates` の日付キーのままにします。
- 時系列は `pd.Index(data.dates, name=DATE)` で reindex します。データのない日付も欠損の行として残るので、他の metric の時系列と行が揃います。
- 表の index は `reset_index()` で通常の列にします（`RangeIndex` 以外の index は列として書き出されます）。
- 組を表す列は `signal` / `returns` / `horizon` にします。表の名前は、期間全体の要約（1行が1組）を `summary`、日付ごとの値を `timeseries` とするのが組み込みの metric（`ic`、`autocorr` など）の慣例です。
- 欠損は欠損のまま返します。
- xlsx に書けるのは 1,048,575 行までです。超える表は xlsx からのみ除外されます（csv / parquet には書かれます）。

`summary` の慣例は次のとおりです。

- キーは `/` 区切りにします。シグナル × リターン系列 × ホライズンの組を評価する metric では `"{signal}/{returns}/h{horizon}/{stat}"` の形です（例: `ai/specific/h1/mean_excess`）。
- 値は float にします（契約テストで検証されます）。int や文字列は入れません。
- 要約の表から作る場合は `flatten_summary(frame, keys, stats)` を使うと、この形のキーと float の値になります。

## 7. 共通ヘルパー

`metrics/_common.py` に、組み込みの metric が使っている計算の部品があります。自前で書かずに使うと定義が揃います。どの関数も時点はずらしません。

| 関数 | 内容 | 使いどころ |
|---|---|---|
| `cross_sectional_corr(df, x, y, method, min_obs, by=None)` | 日付（と `by` の列）ごとのクロスセクション相関。戻り値は index=(`date`, ...)、columns=[`corr`, `n`] | IC など |
| `summarize_series(x, horizon=1)` | 時系列の `mean` / `std` / `t_stat` / `hit_rate` / `n_periods`。t 値は lag = horizon − 1 の Newey-West | 日付ごとの値の要約 |
| `newey_west_tstat(x, lags)` | 平均がゼロかどうかの Newey-West t 値 | ホライズンが重複する系列の t 値 |
| `assign_quantiles(values, n_quantiles, by=None)` | 日付（と `by`）ごとに 1（最小）〜 n の分位を割り当てる | 分位ポートフォリオ |
| `quantile_portfolios(df, quantile, n_quantiles, dates)` | 分位ごとの等ウェイト平均リターン（long）と、Q1..Qn・spread の wide | 分位ポートフォリオ |
| `summarize_portfolios(wide, horizon, aggregation)` | 各ポートフォリオの算術平均・幾何平均。spread には t 値と正の割合 | 分位ポートフォリオの要約 |
| `cumulative_portfolios(wide, method)` | ポートフォリオごとの累積リターン（long）。`method` は `method_for` の戻り値を渡します | ホライズン 1 の累積リターン |
| `per_period_rate(r, horizon, method)` | h 期間のリターンを1期間あたりに換算（`cumprod` / `cumsum`）。`method` には、そのリターンを束ねた方法 `data.return_aggregations[ret]` を渡します | ホライズン間の比較 |
| `flatten_summary(frame, keys, stats)` | 要約の表を `"{signal}/{returns}/h{horizon}/{stat}"` の辞書にする | `MetricResult.summary` |
| `concat(frames)` | DataFrame のリストを縦に連結（空リストなら空の DataFrame） | 組ごとの結果の連結 |
| `CorrMethod` | 型エイリアス（`"spearman"` / `"pearson"`） | 相関の種類を選ぶパラメータの型（例: `method: CorrMethod = "spearman"`） |

期間をまたぐリターンの集計（平均や累積）には `ReturnAggregation`（`metrics/base.py`）を使います。規約は次の2つです。

- 平均は算術平均と幾何平均の両方を出します（`summarize`）。
- 累積の方法はリターン系列ごとに決まります。既定は系列の `aggregation`（`data.return_aggregations[ret]`）で、`cumprod` は複利 `(1 + r).cumprod() - 1`、`cumsum` は加算 `r.cumsum()` です。`method_for(series, default)` で方法を取り出し、`cumulate(returns, method)` で累積します。

`Params` に項目として持たせると、利用者が config で `aggregation: {cumulative: {specific: cumprod}}`（`specific` はリターン系列名）のように系列ごとに変更できます。定義されていない系列名は、基底クラスの `check` が計算前にエラーにします（利用者から見た仕様は [04_metrics.md の 1.3](04_metrics.md#13-リターンの集計規約-aggregation)）。

```python
from pydantic import Field

from .base import ReturnAggregation, SignalReturnParams


class MyParams(SignalReturnParams):
    aggregation: ReturnAggregation = Field(default_factory=ReturnAggregation)


# compute の中（p = self.params、ret はリターン系列名）。r はホライズン 1 のリターンの時系列（index=date）
method = p.aggregation.method_for(ret, data.return_aggregations[ret])  # "cumprod" / "cumsum"
stats = p.aggregation.summarize(r)               # {"arith_mean": ..., "geo_mean": ...}
cumulative = p.aggregation.cumulate(r, method)   # r と同じ形の累積リターン
```

累積はホライズン 1 のリターンにだけ使います。ホライズンが 2 以上のフォワードリターンは隣り合う日付で期間が重なるため、累積には使えません。

`aggregation.cumulative` で変わるのは累積リターンだけです。フォワードリターンと1期間あたりへの換算は、系列の `aggregation` のままです。

## 8. やってはいけないこと

| やってはいけないこと | 理由 |
|---|---|
| シグナルとリターンの時点をずらす（`shift`、日付の付け替え、フォワードリターンの自前計算） | 時点合わせはデータ準備（`pipeline/preprocess.py`）だけで行う方針です。1行ずれると、先読み（未来の情報を使った評価）になるか、対応しない期間のリターンで評価することになります。どちらもエラーにはならず、結果だけが変わります（方針の理由は [01_design.md の原則 1](01_design.md#原則-1-時点合わせは-pipelinepreprocesspy-に閉じ込める)） |
| 欠損を 0 で埋める（`fillna(0)`） | リターンやシグナルがない銘柄を「リターン 0」「シグナル 0」として集計に入れることになり、平均や相関が歪みます。`sum()` は全て欠損でも 0 を返すので、合計には `sum(min_count=1)` を使います。ローリングの集計は `min_periods` を窓の長さにして、データが足りない所を欠損のままにします |
| データを自分で読みに行く（ファイル、DB、環境変数） | 計算前の検証の対象外になり、時点合わせも保証されません。必要なデータは `requires` に宣言して `DataBundle` から受け取ります |
| ファイルに書く | 出力形式、日付ごとの断面、逐次書き出しは、出力を担当する側（`report/`）が一括して扱います。結果は `MetricResult` で返します |
| `requires` にないデータを使う | 計算前の検証で不足を検出できず、実行の途中で失敗します。契約テストも失敗します |
| インスタンスに大きな状態を持つ（DataFrame を属性に保存するなど） | 並列実行ではインスタンスを pickle して各プロセスに渡します。インスタンスが持つのは `self.params` だけにします |
| 受け取った DataFrame を書き換える（列の追加、`inplace=True` など） | 同じ `DataBundle` を複数の metric が共有するので、後に実行される metric の結果が変わります。加工はコピーに対して行います |

シグナルどうしのラグ（自己相関など）を取る場合だけは、`data.signal_panel(signal)` を行方向にずらして構いません。全カレンダー行を持つので、行のずれがそのままカレンダーの行数になります（組み込みの `autocorr` がこの方法です）。

## 9. テスト

### 契約テストが自動で検証する内容

`tests/metrics/test_contract.py` は、登録済みの全 metric について次を検証します。テストを書き足す必要はありません。

1. `requires` に宣言したデータだけを残した `DataBundle` で、既定のパラメータの `compute` が `MetricResult` を返すこと
2. `tables` か `summary` の少なくとも一方が空でないこと。`tables` の値が DataFrame、`summary` の値が float であること
3. `name` が登録名と一致し、`description` が空でないこと
4. `Params` が `MetricParams` のサブクラスで、`Params()` が既定値だけで作れ、未知のパラメータを拒否すること。JSON Schema を作れること（`alpha-eval metrics list` が使います）
5. `check()` が、利用できるデータに対してエラーを返さないこと

検証に使うデータは、次の節で説明する合成データ（`make_synthetic_bundle()`）です。

```sh
uv run pytest tests/metrics/test_contract.py
```

### 自分のテストを書く

契約テストは形式だけを見ます。計算の正しさは自分のテストで確かめます。`alpha_signal_evaluation.testing.make_synthetic_bundle()` が、ファイルや DB なしで時点合わせ済みの `DataBundle` を作ります。既定では次の内容です。

| 項目 | 内容 |
|---|---|
| 大きさ | 36 行（月末日）× 80 銘柄。引数 `n_dates` / `n_assets` / `horizons` / `seed` / `missing_rate` で変更可 |
| シグナル | `alpha`（次の行のスペシフィックリターンを予測する）、`noise`（無関係）、`mixed`（両者の平均）。約 5% がランダムに欠損 |
| リターン系列 | `total`（`aggregation` は `cumprod`）、`specific`（`aggregation` は `cumsum`）。ホライズンは 1 と 3 |
| その他 | 分類（`category` 列に `S0`〜`S4`）、リスクモデル `barra`（エクスポージャー、ファクター共分散、スペシフィックリスク、ファクターリターン）、ベンチマークウェイト |

答えがわかっているデータなので、「`alpha` は効き、`noise` は効かない」ことを確かめるテストが書けます。`tests/conftest.py` に、この `DataBundle` を返すフィクスチャ `bundle` があります（全テストで共有されるので、書き換えないでください）。次は `tests/metrics/test_top_pct_excess.py` の例です。

```python
import pytest

from alpha_signal_evaluation.metrics import get_metric


def test_informative_signal_has_positive_excess(bundle):
    # 合成データでは alpha は効く・noise は効かないとわかっている
    result = get_metric("top_pct_excess")(returns=["specific"], horizons=[1]).compute(bundle)
    table = result.tables["summary"].set_index("signal")
    assert table.loc["alpha", "mean_excess"] > 0 and table.loc["alpha", "t_stat"] > 3
    assert abs(table.loc["noise", "t_stat"]) < 2
    expected = table.loc["alpha", "mean_excess"]
    assert result.summary["alpha/specific/h1/mean_excess"] == pytest.approx(expected)


def test_matches_manual_calculation_on_one_date(bundle):
    result = get_metric("top_pct_excess")(
        top_pct=25, signals=["alpha"], returns=["total"], horizons=[1]
    ).compute(bundle)
    date = bundle.dates[5]
    df = bundle.aligned("alpha", "total", 1).xs(date, level="date")
    n_top = int(len(df) * 0.25)
    expected = df.nlargest(n_top, "alpha")["forward_return"].mean() - df["forward_return"].mean()
    ts = result.tables["timeseries"].set_index("date")
    assert ts.loc[date, "excess"] == pytest.approx(expected)
    assert ts.loc[date, "n_top"] == n_top


def test_min_obs_gives_missing(bundle):
    # 銘柄数が足りない日付は 0 ではなく欠損になる
    result = get_metric("top_pct_excess")(min_obs=10_000).compute(bundle)
    assert result.tables["timeseries"]["excess"].isna().all()
```

```sh
uv run pytest tests/metrics/test_top_pct_excess.py
```

### ファイルに置く前に Python で試す

metric のクラスは、`metrics/` に置く前に notebook やスクリプトの中で定義して試せます。

`metrics/` の外で定義するときは、import を絶対 import に置き換えてください。[3 章のコード](#ファイルの全文) は `from ..data.bundle import DataBundle` のような相対 import で書いてあり、notebook にそのまま貼ると `ImportError` になります。置き換え後の import は [10 章の「絶対 import での書き方」](#絶対-import-での書き方) にあります。

クラスを定義したら、インスタンスを作って `compute` を直接呼べます（`compute` を直接呼ぶだけなら、登録は不要です）。

```python
from alpha_signal_evaluation.testing import make_synthetic_bundle

result = TopPctExcess(top_pct=10).compute(make_synthetic_bundle())  # TopPctExcess は定義済みのクラス
result.tables["summary"]
```

組み込みの metric と並べて実行する場合は `evaluate()` を使います。`data` を渡すと、ファイルや DB を読まずにその `DataBundle` で評価します。metric を名前で指定するには登録が必要です。3 章のコードのように `@register_metric` を付けたクラスは、定義した時点で登録されます（同じセルを実行し直しても構いません）。付けていないクラスは `register_metric(クラス)` を呼ぶと登録されます。同じ `name` の metric を `metrics/` にも置いていると、名前の重複で `Metric name ... is already registered` というエラーになるので、試すときはどちらか一方にしてください。

```python
from alpha_signal_evaluation import evaluate
from alpha_signal_evaluation.testing import make_synthetic_bundle

# top_pct_excess が登録済みであること（metrics/ に置いたか、@register_metric を付けて定義した）
report = evaluate(
    {
        "version": 1,
        "metrics": [
            {"name": "ic"},
            {"name": "top_pct_excess", "params": {"top_pct": 10}},
        ],
    },
    data=make_synthetic_bundle(),
)
report.status()                               # metric ごとの成否
report["top_pct_excess"].tables["summary"]    # MetricResult から表を取り出す
```

notebook で定義したクラスは、`n_jobs=1`（既定）で実行してください。並列実行（`n_jobs` が 2 以上）では metric を別のプロセスに渡しますが、notebook の中で定義したクラスは別のプロセスから読み込めないことがあります。その場合は、その metric と、その時点で終わっていなかった他の metric が、`BrokenProcessPool` というエラーで失敗として記録されます。`metrics/` に置いた metric は並列実行できます。

合成データではなく手元の DataFrame で試す場合は、`make_bundle()` で `DataBundle` を作って `data` に渡します（[02_features.md の 7.2](02_features.md#72-python-api)）。

## 10. 別パッケージから追加する場合（entry points）

このリポジトリを変更せずに、自分のパッケージから metric を追加できます。自分のパッケージの `pyproject.toml` に、entry points の group `alpha_signal_evaluation.metrics` を登録します。

```toml
[project.entry-points."alpha_signal_evaluation.metrics"]
top_pct_excess = "my_metrics.top_pct_excess"
```

- 右辺は、import すると `@register_metric` が実行されるモジュールです。左辺の名前は、読み込みに失敗したときのログに出るだけで、metric の `name` とは無関係です。
- metric のファイルは `metrics/` に置く場合と同じ内容で、import だけを絶対 import に変えます（下の「絶対 import での書き方」）。
- 読み込みは、metric を最初に名前で引いたとき、または一覧を取得したときに1回だけ、組み込みの metric の後に行われます。
- entry points に登録したモジュールの読み込みに失敗した場合（import エラー、組み込みの metric と `name` が重複した場合など）は、`Failed to load metric plugin ...` というエラーログが出て、そのモジュールだけが無視されます。組み込みの metric はそのまま使えます。
- パッケージを `alpha_signal_evaluation` と同じ Python 環境にインストールすれば、`alpha-eval metrics list` に表示され、このリポジトリの契約テストの対象にもなります。uv では、たとえば `uv run --with-editable <パッケージのパス> alpha-eval metrics list` で確認できます。
- 自分のパッケージ側のテストには `alpha_signal_evaluation.testing.make_synthetic_bundle()` が使えます。

### 絶対 import での書き方

`metrics/` の外（自分のパッケージ、notebook、スクリプト）で metric を定義するときは、[3 章のコード](#ファイルの全文) の import のうち、`from ..` と `from .` で始まる5行を次の import に置き換えます。それ以外の行は変えません。

```python
from alpha_signal_evaluation import DataBundle
from alpha_signal_evaluation.data.columns import DATE
from alpha_signal_evaluation.metrics import (
    Metric,
    MetricResult,
    SignalReturnParams,
    register_metric,
)
from alpha_signal_evaluation.metrics._common import concat, flatten_summary, summarize_series
```

`MetricParams` と `ReturnAggregation` も `alpha_signal_evaluation.metrics` から import できます。

## 11. 追加前のチェックリスト

- [ ] ファイル名が `_` で始まっていない。クラスに `@register_metric` が付いている
- [ ] `name` が他の metric と重複していない。`description` を1行で書いた
- [ ] `compute` で使うデータをすべて `requires` に書いた
- [ ] `Params` の全項目に既定値がある。値の範囲を `Field` で宣言した
- [ ] シグナルとリターンは `data.aligned()` で取り出し、時点をずらしていない
- [ ] 欠損を 0 で埋めていない（`fillna(0)` を使っていない。合計は `sum(min_count=1)`）
- [ ] ファイルや DB を読んでいない。ファイルに書いていない
- [ ] 時系列の表に `date` 列があり、`data.dates` で reindex している
- [ ] `summary` の値は float、キーは `"{signal}/{returns}/h{horizon}/{stat}"` の形
- [ ] docstring（日本語）に、何を測るか・パラメータ・出力する表と列・`summary` のキーを書いた
- [ ] `uv run alpha-eval metrics list` に表示される
- [ ] 答えがわかっているデータでのテストを `tests/metrics/` に追加した
- [ ] `uv run pytest`、`uv run ruff check src tests`、`uv run ruff format src tests` が通る
