# 設計思想（開発方針）

この文書は、alpha-signal-evaluation（株式アルファシグナルの評価モジュール。CLI は `alpha-eval`）が「なぜこう作られているか」を説明します。モジュールを使う方、metric を追加する方、設計判断を確認したい方が対象で、コードを読んでいなくても読めるように書いています。

マニュアル全体の目次と、目的別にどの文書を読めばよいかは [docs/README.md](README.md) にあります。

使い方や個々の設定はこの文書では扱いません。何ができるかは [02_features.md](02_features.md)、config.yaml の項目は [03_config.md](03_config.md)、組み込み metric は [04_metrics.md](04_metrics.md)、metric の追加手順は [05_adding_metrics.md](05_adding_metrics.md) にあります。

## この文書で使う用語

| 用語 | 意味 |
|---|---|
| シグナル | 銘柄ごとのスコア（アルファスコア）。評価の対象です。 |
| metric | 評価指標の1つ（IC、分位分析など）。Python のクラスとして書き、config.yaml から名前で指定します。 |
| example1 | リポジトリの `examples/example1` にあるサンプル。合成データ（値は乱数）と、それを評価する config（`config.example.yaml`）の組です。マニュアルの例に使います。 |
| カレンダー | 評価の時間軸を定義するファイル。1行が1評価期間です。example1 では1行が1か月で、列は `Base_day` / `Trading_day` / `Label2` などです。 |
| 日付キー | カレンダーの行を表す文字列。config の `calendar.index` で指定した列（example1 では `Base_day`）の値で、`20240531` のような8桁の日付か、`202405` のような6桁の月です。読み込んだすべてのデータに付きます。 |
| ホライズン | 何期先までのリターンで評価するか。単位はカレンダーの行数です。 |
| 期間リターン | カレンダー1行分（1評価期間）のリターン。 |
| フォワードリターン | ある行の時点から、ホライズン分だけ先の行の時点までのリターン。期間リターンをホライズンの行数だけ束ねて作ります。 |
| 時点合わせ | ある行のシグナルに、どの期間のリターンを対応させるかを決める処理。 |
| リターン系列 | config の `returns` で名前を付けたリターン。metric はこの名前でリターンを指定します。名前は自由に付けられ（`total`、`specific`、`excess`、`usd_total` など）、名前によって計算が変わることはありません。 |
| aggregation | リターン系列ごとに指定する、複数の期間のリターンを束ねる方法。`cumprod`（複利）か `cumsum`（加算）です。`source: barra` では選べず、読むデータ（DRTN / SRTN）で決まります。 |
| role | 列の役割を表す共通の名前（`date` / `asset_id` / `category` / `factor` / `value` / `weight`）。入力元（ソース）ごとに違う実際の列名を、config の `columns` で role に対応づけます。 |
| DataBundle | 時点合わせを済ませたデータ一式（シグナル、フォワードリターン、分類など）。metric が受け取る唯一の入力です。 |
| データ準備 | config に従ってデータを読み込み、時点合わせをして DataBundle を組み立てる処理。コードでは `pipeline/preprocess.py` が担当します。 |

## 1. 目的と、最優先していること

このモジュールの目的は、シグナルとフォワードリターンから評価指標（metric）を、全員が同じデータ・同じ時点合わせで計算できるようにすることです。

設計で最優先しているのは **拡張性** です。具体的には、他の運用者が `src/alpha_signal_evaluation/metrics/` に Python ファイルを1つ置くだけで指標を追加できることを指します。metric のクラスに `@register_metric` を付けて書いたファイルを置くと、他のファイルを編集しなくても次のことが起きます。

- 自動で登録され、config.yaml の `metrics` から名前で呼べる
- パラメータと必要なデータが、計算を始める前に検証される
- 契約テスト（全 metric に共通のテスト）の対象になる
- 結果が csv / parquet / xlsx に書き出される

これを成り立たせるために、metric の作者が考えなくてよいことを他の層がすべて引き受けています。この文書で説明する設計は、どれも次の表のいずれかを実現するためのものです。場所は `src/alpha_signal_evaluation/` からの相対パスで書きます（以下同じ）。

| metric の作者が考えなくてよいこと | 引き受けている場所 | 説明している箇所 |
|---|---|---|
| ファイル・DB・カレンダーからの読み込み | `io/`、`data/`、`pipeline/preprocess.py` | [3 章](#3-レイヤー構成と依存方向) |
| シグナルとリターンの時点合わせ | `pipeline/preprocess.py` | [原則 1](#原則-1-時点合わせは-pipelinepreprocesspy-に閉じ込める) |
| 欠損や壊れた値の扱い | `pipeline/preprocess.py` | [原則 2](#原則-2-欠損は欠損のまま) |
| パラメータと必要なデータの検証 | metric のパラメータ定義（`Params`）、`validation.py` | [原則 3](#原則-3-設定ミスは計算前にすべて検出する) |
| metric の登録 | `metrics/registry.py`（`metrics/` 配下の自動探索） | [3 章](#3-レイヤー構成と依存方向) |
| 結果の書き出し、日付ごとの断面（ある日付の行だけを切り出した表） | `report/` | [4 章](#4-データの流れ)、[5 章](#5-失敗の扱い) |
| 例外や並列実行が他の metric に与える影響 | `pipeline/runner.py` | [5 章](#5-失敗の扱い) |

その代わり、metric の作者が守る約束は次の4つだけです（手順は [05 の 2 章](05_adding_metrics.md#2-最短手順)、理由つきの一覧は [05 の 8 章](05_adding_metrics.md#8-やってはいけないこと)）。

1. DataBundle を受け取り、結果（`MetricResult`）を返すだけにする。データ取得やファイル出力をしない。
2. 時点をずらさない。
3. 欠損を 0 で埋めない。
4. 使うデータを `requires` に宣言し、それ以外を使わない。

## 2. 4つの原則

このモジュールを使うときにも、変更するときにも前提になる原則が4つあります。

| # | 原則 | 防いでいること |
|---|---|---|
| 1 | 時点合わせはデータ準備（`pipeline/preprocess.py`）に閉じ込める | 先読み（未来の情報を使った評価）と、metric によって対応する期間が食い違うこと |
| 2 | 欠損は欠損のまま | 欠損を 0% のリターンとして数え、気づかないまま指標が歪むこと |
| 3 | 設定ミスは計算前にすべて検出する | 長い計算の途中で止まること、書き間違いが黙って無視されること |
| 4 | 認証情報・接続先ホスト名をコードにも config にも書かない | 認証情報や社内の接続先が、リポジトリや出力ファイルに残ること |

各原則をコード上のどこで保証し、どのテストで守っているかは、[末尾の節](#このモジュール自体を変更する人向け) にまとめています。

### 原則 1: 時点合わせは pipeline/preprocess.py に閉じ込める

**何を守るか。** フォワードリターンの計算や、日次リターンの期間への集約といった、行をまたいで時点を合わせる処理は、データ準備（`pipeline/preprocess.py`）だけで行います。metric は、同じ行に並んだシグナルとフォワードリターンを `DataBundle.aligned()` で取り出すだけで、自分ではリターンをずらしません。

**なぜ。** metric ごとにリターンをずらすコードを書くと、次の問題が起きます。

- **先読み。** ずらす向きや行数を1つ間違えると、シグナルを作った時点ですでに実現していたリターンと突き合わせてしまいます。結果はそれらしい数値になるので、気づきにくい誤りです。
- **指標間の不整合。** metric によってずらし方が違うと、IC と分位スプレッドが別の期間のリターンを見ていることになり、比べられません。
- **追加する人の負担。** 入力データの規約（下で説明する `convention`）を、metric の作者全員が理解する必要が出てきます。

**利用者から見える挙動。**

- カレンダーファイルが時間軸の唯一の定義です。ホライズンは日数ではなくカレンダーの行数で数えます（example1 は月次カレンダーなので、`horizons: [1, 3, 6]` は 1・3・6 か月先）。ずらしは日付の差ではなく、行の位置で行います。
- 読み込んだデータは、config の指定に従ってカレンダーの行に対応づけられ、その行の日付キーが付きます。この対応づけは行をずらしません（[02 の 4.2](02_features.md#42-どのデータがどの行に対応するか)）。
- リターンのソースには、行 t の値がどの期間のリターンかを表す規約（`convention`）があります。`realized`（既定）は「行 t-1 → t の実現リターン」、`forward` は「行 t → t+1 のリターン」です。どちらの規約でも、でき上がるフォワードリターンの行 t は「行 t の時点から h 行先の時点まで」のリターンで、行 t のシグナルと同じ行に置かれます。

規約の詳細、DB の日次リターンを期間に集約する規則、リターン系列ごとの束ね方（`aggregation` の `cumprod` は複利、`cumsum` は加算）は、[02 の 4 章](02_features.md#4-時点合わせの仕様) にあります。

`realized` 規約・`aggregation: cumprod` のリターン系列で、ある銘柄の期間リターンが次のとおりだった場合に、データ準備が作るフォワードリターンです。

| 行（`Base_day`） | 期間リターン | フォワードリターン h=1 | フォワードリターン h=2 |
|---|---|---|---|
| 20190329 | 0.01 | 0.02 | 欠損 |
| 20190430 | 0.02 | 欠損 | 欠損 |
| 20190531 | 欠損 | 0.04 | 0.092 |
| 20190628 | 0.04 | 0.05 | 欠損 |
| 20190731 | 0.05 | 欠損 | 欠損 |

行 20190329 の h=1 は、次の行（20190430）の期間リターン 0.02 です。行 20190531 の h=2 は、続く2行を複利で束ねた 1.04 × 1.05 − 1 = 0.092 です。欠損になる箇所の理由は原則 2 で説明します。

metric の中のコードは、次の1行で時点合わせ済みの組を受け取ります（example1 のシグナル `ai`、リターン系列 `total`、ホライズン 1）。

```python
df = data.aligned("ai", "total", 1)
# index=(date, asset_id)、columns=["ai", "forward_return"]
```

なお、シグナルを自分自身の過去の行と比べる metric（`autocorr`）は、評価対象の全カレンダー行を持つ表を `DataBundle.signal_panel()` で受け取り、行数でラグを数えます。これはシグナルどうしの比較で、シグナルとリターンの対応づけではないので、この原則には反しません。

### 原則 2: 欠損は欠損のまま

**何を守るか。** リターンの欠損を 0 で埋めません。壊れた値（リターンのソースごとに config で指定する有効範囲 `valid_range` の外の値）は欠損にします。統計的な外れ値の処理は、データ準備には入れません。

**なぜ。** 0 で埋めると、上場前・売買停止・データ未着の銘柄が「リターン 0% だった」ことになり、IC や分位リターンが歪みます。しかも銘柄数は減らないので、歪んだことに気づけません。欠損のままにしておけば、その銘柄は metric の計算対象から外れ、銘柄数（たとえば `ic` の `timeseries` という表の `n` 列）に表れます。

**利用者から見える挙動。**

- ホライズン h 行のうち1行でも期間リターンが欠損なら、そのフォワードリターンは欠損です。原則 1 の表では行 20190531 の期間リターンが欠損なので、それを束ねる行 20190430 の h=1 と、行 20190329・20190430 の h=2 が欠損になっています（[02 の 4.8](02_features.md#48-欠損の扱い)）。
- ホライズン分の行がカレンダーの末尾までにない場合も欠損になり、警告が出ます。原則 1 の表の行 20190731 の h=1、行 20190628・20190731 の h=2 と同じ形です（[02 の 4.9](02_features.md#49-期間の末尾でリターンが足りない場合)）。
- `valid_range` の外のリターンは欠損にして、件数と例を警告に出します（[02 の 4.6](02_features.md#46-valid_range範囲外の値の扱い)）。

**`valid_range` とウィンソライズの役割分担。** 「壊れた値を除く」ことと「外れ値の影響を抑える」ことは別の処理として分けています。

| | `valid_range` | ウィンソライズなどの外れ値処理 |
|---|---|---|
| 目的 | データの誤り（DB の特殊値、単位の誤りなど）を除く | 統計的な外れ値の影響を抑える |
| 行う場所 | データ準備（リターンのソースごとの設定） | metric のパラメータ |
| 現状 | 既定は `[-1, 10]`（−100% 〜 +1000%） | この処理を持つ組み込み metric はまだありません |

外れ値処理をデータ準備に入れない理由は、そこで加工するとすべての metric に効いてしまうからです。処理が必要かどうかも、その方法も、metric によって違います。`valid_range` は外れ値処理の代わりではないので、壊れた値だけを外す緩い範囲にします。

### 原則 3: 設定ミスは計算前にすべて検出する

**何を守るか。** config.yaml の誤りは、データを読み込む前に、見つかったものをまとめて報告します。最初の1件で止めません。

**なぜ。** 評価は DB からの取得や多数の metric の計算で時間がかかります。計算の途中で設定ミスが見つかると、直して再実行し、また次のミスで止まる、という往復になります。また、書き間違えたキーが黙って無視されると、既定値で計算された結果を、意図した設定の結果だと誤解します（`n_quantiles` を `n_quantile` と書いた場合など）。

**利用者から見える挙動。**

- 検証は2段階です。まず構造（キー名、型、必須項目、値の範囲）を、次に意味（カレンダー、登録済みの metric、環境変数、ファイルの存在との突き合わせ）を検証します。構造のエラーがあるうちは、意味の検証には進みません。構造の検証のうち、config 全体を見渡す検査（metric のキーの重複、リターン系列名の重複など）は、ほかの構造のエラーがなくなってから行います。
- metric の `params` は、その metric 自身のパラメータ定義（`Params`。型と既定値を宣言する pydantic のモデル）で検証します。このため、追加した metric のパラメータも、何もしなくても計算前の検証の対象になります。
- `alpha-eval validate` は検証だけを行います。`alpha-eval run` と Python API の `evaluate()` も最初に同じ検証を行い、エラーがあれば計算を始めず、出力先にも何も書きません。
- 検証は、`columns` の role の対応と、各ファイルソースの最初のファイルの見出し（列名）を確かめます。値の中身（行の重複、数値でない値など）は読まないので、そうした誤りはデータを読み込んだ時点でエラーになります。

example1 の config に誤りを5つ入れて `alpha-eval validate` を実行すると、5件がまとめて報告されます。入れた誤りは、シグナルのパスの `{Label2}` を `{Label9}` に変更、`ic` の `signals` に存在しない `momentum` を指定、`quantile` の `n_quantiles` を `n_quantile` と記述、未登録の metric `sharpe` を追加、`output.by_date` に評価期間外の `20190101` を指定、の5つです。

```sh
uv run alpha-eval validate config.yaml
```

```text
warning: Calendar ends 6 row(s) short of the longest horizon; forward returns at the end of the period are missing
error: data.signals[0].path: placeholders ['Label9'] are not calendar columns ['Base_day', 'Monthend_day', 'Trading_day', 'Rskmdl_month', 'Label1', 'Label2']
error: metrics[ic]: unknown signals ['momentum']; available: ['ai', 'dss']
error: metrics[quantile].params.n_quantile: Extra inputs are not permitted
error: metrics[sharpe]: Unknown metric 'sharpe'. Available: ['alpha_decay', 'autocorr', 'category_ic', 'category_quantile', 'category_returns', 'factor_correlation', 'ic', 'quantile']
error: output.by_date: dates ['20190101'] are not in the evaluation period (20190329..20260731, 89 dates)
```

先頭の `warning` は example1 がもともと出す警告で、エラーではありません（評価期間の末尾がカレンダーの末尾と同じなので、原則 2 のとおり末尾のフォワードリターンが欠損になります）。

検出できる項目、エラーメッセージの一覧、検証を通っても実行時に出るエラーは、[03 の 11 章](03_config.md#11-検証とよくある設定ミス) にあります。

### 原則 4: 認証情報・接続先ホスト名をコードにも config にも書かない

**何を守るか。** DB のホスト名、ユーザー名、パスワードを、コードにも config.yaml にも書きません。公開リポジトリなので、社内のホスト名、IP アドレス、URL もコミットしません。

**なぜ。** リポジトリは公開されており、config.yaml も共有やコミットの対象です。さらに、実行した config の内容は出力の `run_info.json`（実行の記録）に書かれるので、config に認証情報を書くと出力ファイルにも残ります。

**利用者から見える挙動。**

- config.yaml には `connection: risk_models` のように、接続先の名前だけを書きます。ホスト名、ユーザー名、パスワードは環境変数から読みます。
- 環境変数が足りない接続先は、原則 3 の検証が計算前に報告します。
- 接続先ごとの環境変数名と、`.env` ファイルの使い方は、[03 の 10 章](03_config.md#10-db-接続の設定) にあります。

## 3. レイヤー構成と依存方向

コードは層に分かれており、依存（import）の向きは一方向です。矢印は「左が右を使う」ことを表します。

```text
cli → api → validation / pipeline → data → io
                        metrics → data.bundle
                        report  → pipeline.runner
```

大事なのは2行目です。metric が依存するのは DataBundle（と列名の定数、設定の型）だけで、ファイルや DB を読む層を知りません。データの読み込み方を変えても metric は影響を受けず、metric を足しても読み込み側を変える必要がありません。これが「1ファイル置くだけ」を支えています。

| レイヤー | 場所 | 責務 | やらないこと |
|---|---|---|---|
| cli | `cli/main.py` | 引数の解釈、ログの設定、`.env` の読み込み、結果の表示と終了コード | 評価のロジックを持たない（`api` を呼ぶだけ） |
| api | `api.py` | `evaluate()` と `validate()`。検証 → データ準備 → metric の実行 → レポートの流れを束ねる | データの加工、時点合わせ |
| 設定の型 | `config.py` | config.yaml のスキーマと構造の検証。各層から参照される | データを読まない |
| validation | `validation.py` | 意味の検証（原則 3 の2段目） | データの中身を読まない（カレンダーと、列名を確かめるための各ソースの最初のファイルの見出しを除く）。DataBundle を作らない |
| `pipeline`（データ準備） | `pipeline/preprocess.py` | 期間への集約、フォワードリターン、ユニバースの適用、DataBundle の組み立て | ファイルや DB の物理的な読み込み（`data/` に任せる） |
| `pipeline`（metric の実行） | `pipeline/runner.py` | metric の実行、並列実行、失敗の隔離、完了の通知 | 結果の書き出し |
| data | `data/` | カレンダー、列名の変換、日付キーと銘柄コードの正規化、ソースの読み込みとカレンダー行への対応づけ（`loaders.py`。Barra は `barra.py`）、DataBundle の定義 | 行をまたぐ時点合わせ（期間への集約、フォワードリターン） |
| io | `io/` | ファイルの読み込み、DB 接続、DB 取得結果のキャッシュ | カレンダーやシグナルといった意味を知らない。`data/` の型を使わない |
| metrics | `metrics/` | DataBundle から `MetricResult` を計算する | データ取得、ファイル出力、時点のずらし |
| report | `report/` | 結果の保持、parquet / csv / xlsx / `run_info.json` の書き出し、日付ごとの断面、逐次書き出し | 計算 |

- `metrics/` 直下のモジュールは自動で import され、`@register_metric` を付けたクラスが登録されます。ファイル名が `_` で始まるもの（`_template.py`、`_common.py`）と、仕組みを提供する `base.py`・`registry.py` は対象外です。別のパッケージに置いた metric も登録できます（[05 の 10 章](05_adding_metrics.md#10-別パッケージから追加する場合entry-points)）。
- 日付ごとの断面（`output.by_date`）は、`date` 列を持つ表を `report/` がその日付の行だけに切り出したものです。metric 側の対応は不要です。

## 4. データの流れ

`alpha-eval run config.yaml`（Python では `evaluate("config.yaml")`）を実行すると、次の順に処理されます。

```text
config.yaml
   │  1. 読み込みと構造の検証    config.py
   ▼
設定（EvaluationConfig）
   │  2. 意味の検証              validation.py
   │  3. データ準備              pipeline/preprocess.py
   ▼
DataBundle（時点合わせ済み）
   │  4. metric の実行           pipeline/runner.py、metrics/
   ▼
metric ごとの結果（MetricResult）
   │  5. レポート                report/
   ▼
出力ディレクトリ（csv / parquet / xlsx、run_info.json）
```

- 手順 1・2 でエラーがあれば、一覧を示して終了します（原則 3）。
- 手順 3 では、データを読み込んで列名と日付キー・銘柄コードをそろえ、リターンをフォワードリターンにして DataBundle にまとめます。時点合わせはこの段階ですべて終わります。警告（カレンダー末尾の不足、範囲外のリターンなど）もここで集めます。
- 手順 4 では、すべての metric が同じ DataBundle を受け取ります。DataBundle が持つ属性とメソッドの一覧は、[05 の 5 章](05_adding_metrics.md#5-databundle-の使い方) にあります。
- 手順 5 で、`alpha-eval run` は metric が1つ終わるたびにファイルへ書き出します（[5 章](#5-失敗の扱い)）。

手元の DataFrame から評価する場合は、`make_bundle()` で DataBundle を作って `evaluate(config, data=bundle)` に渡します（[02 の 7.2](02_features.md#72-python-api)）。このときは手順 3 を通らず、手順 2 では metric の設定（と `output.by_date` の日付）だけを検証します。`make_bundle()` の時点合わせの規則は、手順 3 と同じです。

## 5. 失敗の扱い

長い実行の途中で何かが失敗しても、それまでの結果を失わないことを方針にしています。ここでは方針と理由を説明します。何が起きたときに何が残るかの一覧は、[02 の 6.5](02_features.md#65-逐次書き出し途中で落ちたときに残るもの) にあります。

### metric 単位の隔離

ある metric の計算中に例外が起きても、他の metric は最後まで実行され、結果も出力されます。1つの metric の不具合で、他の metric の結果まで失わないようにするためです。失敗した metric はトレースバックとともに記録され、`report.errors` と `run_info.json` で確認できます。

### 並列実行

metric は複数のプロセスで並列に実行できます。指定方法と注意点は [02 の 7.3](02_features.md#73-並列実行) にあります。設計上の要点は次の3つです。

- **DataBundle はワーカープロセスごとに1回だけ渡します。** metric のたびに大きなデータを受け渡すことを避けるためです。metric ごとに渡すのはキーとインスタンスだけなので、metric のインスタンスには、パラメータ以外の大きな状態を持たせません。
- **並列でも結果は変わりません。** 結果は完了順ではなく config の順序で返り、逐次実行と同じ値になります。
- **Ctrl-C で止めたら、すぐに止まります。** 止めたあとで残りの metric の計算を待たせないためです。すでに終わっていた metric の結果を書き出したうえでワーカーを止め、残りの metric は実行しません。その時点で実行中だった metric の結果は残りません。

ワーカープロセス自体が異常終了した場合（メモリ不足など）は、終わっていなかった metric をまとめて失敗として記録し、終わっていた metric の結果は残します。

### 逐次書き出し

`alpha-eval run` と、Python API で `save` を指定した `evaluate()` は、metric が1つ終わるたびに、その metric の表と日付ごとの断面（csv / parquet）、そこまでの要約（`summary`）、実行の記録（`run_info.json`）を書き出します。プロセスが途中で落ちても、終わった分は残ります。どこまで終わったかは、`run_info.json` の `state`（実行の状態）と `pending`（まだ結果をファイルに書いていない metric）で分かります（[02 の 6.3](02_features.md#63-run_infojson)）。

設計上の判断と、その理由は次のとおりです。

| 判断 | 理由 |
|---|---|
| metric が終わるたびに書く | 評価には時間がかかり、メモリ不足による強制終了のように、後始末を実行できない落ち方もあります。最後にまとめて書く方式では、その場合に何も残りません。 |
| xlsx だけは最後に1回書く | 1つのブックにすべての表を入れる形式なので、metric が終わるたびに書くと毎回ブック全体を書き直すことになります。例外や Ctrl-C で止まった場合はその時点までの分を書きますが、強制終了では残りません。確実に残したい場合は `output.formats` に csv か parquet を含めます。 |
| 一時ファイルに書いてから置き換える | 書いている途中で落ちても、壊れたファイルを残さず、すでにあるファイルを元のまま保つためです。 |
| 書き出しの失敗で計算を止めない | 失敗はログに記録して計算を続けます。書けなかった metric は `pending` に残し、次に書き出すときにもう一度書きます。`run_info.json` が、ファイルにない結果を「ある」と示さないようにするためです。 |
| `run_info.json` をデータを読み込む前に書き、`finished` は最後に書く | 最初に `state: running` で書くのは、出力先に残っている以前の実行の記録が、今回の結果に見えないようにするためです。`finished` は、日付ごとの断面まで含めてすべて書き終えてから書きます。`finished` であれば出力がそろっている、と判断できます。 |
| 出力先に書き込めないときは、計算の前にエラーにする | 長い計算が終わってから、結果を書けないと分かることを避けるためです。 |

同じ出力先に再実行すると、metric を書くときにその metric の以前の表を消してから書きます。失敗した metric の以前の結果が、今回の結果のように見えないようにするためです。まだ `pending` の metric と config から外した metric のファイルには触れないので、以前の結果を残したい場合は出力先を分けます（`--output-dir`）。

## 6. 規約

コード全体で統一している約束事です。metric を書くときにも関係します。

| 規約 | 内容 | 理由 |
|---|---|---|
| 日付キーは文字列のまま | `yyyymmdd`（8桁）または `yyyymm`（6桁）の文字列で扱い、日付型に変換しない | カレンダーの値をそのままファイルパスに埋め込む（`{Label2}.csv` など）ため。日付と月のキーが混在しても同じ扱いにできる |
| 銘柄コード（`asset_id`）も文字列 | 読み込み時に文字列にそろえる | ソースによって数値と文字列が混在しても結合できる。先頭のゼロ（`01346` など）を保つ |
| 内部の列名は role 名 | 実際の列名は、読み込み直後に role 名に変換する。対応は config の `columns` に書く（[03 の 3 章](03_config.md#columns-role-と実カラム名の対応)） | ソースごとの列名の違いを、読み込みより後の処理に持ち込まない |
| 期間の束ね方は系列ごとの `aggregation` で決まる | 複数の期間のリターンを束ねる方法を、リターン系列ごとに `cumprod`（複利）か `cumsum`（加算）で指定する。系列の名前からは決まらない。`source: barra` だけは例外で、読むデータ（DRTN / SRTN）によって決まる（[02 の 4.4](02_features.md#44-フォワードリターンの定義)） | 期間への集約、フォワードリターン、1期間あたりへの換算、累積リターンの既定値が同じ指定を使うので、処理によって食い違わない |
| 期間をまたぐ集計は `ReturnAggregation` | 平均は算術平均と幾何平均の両方を出す。累積リターンの方法（`cumprod` / `cumsum`）だけは、metric のパラメータでリターン系列ごとに変えられる（[04 の 1.3](04_metrics.md#13-リターンの集計規約-aggregation)） | 算術平均は予測力の評価、幾何平均は運用したときの成長率の目安で、用途が違う |
| metric のパラメータは `MetricParams` | `MetricParams` を継承して定義し、全項目に既定値を持たせる | config で `params` を省略でき、原則 3 の検証が自動で効く |
| ファクター共分散の形と単位 | ソースによらず、index=(date, factor)、columns=ファクター名の正方行列。値は年率で、単位は小数の2乗。Barra（`source: barra`）は読み込み時に換算し、それ以外のソースはこの単位で用意する | どのソースのリスクモデルでも、metric が同じ形と単位で受け取れる |
| 相対パスは config ファイル基準 | config.yaml に書いた相対パスは、config ファイルの場所から解決する（例外は [03 の 1 章](03_config.md#相対パスの基準)） | どのディレクトリで実行しても同じファイルを指す |

## 7. 未実装・未決事項

現時点で実装していないものの一覧は、[02 の 9 章](02_features.md#9-現時点でできないこと未実装) にあります。設計に関わる範囲では、次の点が未実装または未決です。

- **リスクモデルを使う分析の大半。** リスクモデルとベンチマークウェイトは読み込めますが、使っている組み込み metric は `factor_correlation`（エクスポージャー）だけです。
- **ポートフォリオを入力とする分析。** config の `portfolios` と DataBundle の `portfolio_weights` は、枠だけを予約しています。
- **スペシフィックリスクの形式。** 入力ファイルの形式が決まっていないため、列をそろえず、ソースの形式のまま読み込みます。

## このモジュール自体を変更する人向け

metric を追加するだけなら、この節を読む必要はありません。データ準備、検証、出力などモジュール本体を変更する場合に、原則をコードのどこで保証し、どのテストで守っているかをまとめます。テストのパス（`tests/` 以下）はリポジトリのルートからの相対パスで、`uv run pytest` で実行します。

| 対象 | コード上で保証している場所 | 守っているテスト |
|---|---|---|
| 原則 1（時点合わせ） | `pipeline/preprocess.py` の `make_plan()`（評価する行と、リターンを読むために前後に延長する行を決める）、`to_period_returns()`（日次 → 期間）、`forward_returns()`（期間 → フォワードリターン）。`data/loaders.py` はカレンダー行への対応づけだけ、`DataBundle.aligned()` は内部結合だけを行う | `tests/pipeline/test_time_alignment.py`、`tests/test_end_to_end.py` の `test_file_pipeline_is_time_aligned`（ファイル）と `test_db_pipeline_aggregates_daily_returns`（DB の日次） |
| 原則 2（欠損） | `pipeline/preprocess.py`。期間への集約は `sum(min_count=1)`、フォワードリターンは `rolling(h, min_periods=h)` で、欠損を 0 として数えない。`valid_range` の適用は `_drop_out_of_range()` | `tests/pipeline/test_time_alignment.py` の `test_missing_period_stays_missing`、`test_all_missing_period_is_not_zero`。`tests/test_end_to_end.py` の `test_tail_beyond_calendar_is_missing_with_warning`、`test_period_returns_out_of_range_become_missing`、`test_valid_range_can_be_narrowed` |
| 原則 3（検証） | 構造は `config.py`（pydantic。全モデルが `extra="forbid"`）、意味は `validation.py` の `validate_config()`。metric の `params` は、各 metric の `Params` と `Metric.check()` | `tests/test_config.py`（`test_unknown_keys_are_rejected` など構造の検証）、`tests/test_end_to_end.py` の `test_validation_reports_all_problems_at_once`、`test_evaluate_raises_before_computing`、`tests/test_incremental_save.py` の `test_config_errors_write_nothing` |
| 原則 4（認証情報） | `io/db/config.py`（接続先ごとの設定クラスが環境変数から読む） | `tests/test_end_to_end.py` の `test_db_connection_settings_are_checked` |
| 失敗の扱い（5 章） | `pipeline/runner.py` の `run_metrics()`（隔離、並列実行）、`report/incremental.py` の `IncrementalWriter`（逐次書き出し）、`report/writers.py`（一時ファイル、`run_info.json`） | `tests/test_incremental_save.py`（失敗した metric があっても他の metric が実行されることは `test_runner_reports_each_finished_metric`、強制終了は実際に子プロセスを落とす `test_results_remain_when_the_process_is_killed`）、`tests/test_end_to_end.py` の `test_parallel_matches_serial`（並列と逐次で結果が一致すること） |
| 規約（6 章） | 日付キーと銘柄コードの正規化は `data/keys.py`、role 名の定数は `data/columns.py`、リターン系列の束ね方は `config.ReturnSeries` の `aggregation`（`source: barra` の対応は `config.BARRA_AGGREGATION`） | `tests/test_input_formats.py`、`tests/test_monthly_calendar.py`、`tests/test_end_to_end.py` の `test_series_aggregation_decides_forward_and_cumulative_returns` |

4 章の手順 1〜5 の入口は、順に `load_config()`、`validate_config()`、`build_bundle()`、`run_metrics()`、`EvaluationReport`（逐次書き出しは `IncrementalWriter`）です。

変更するときは次を守ります。

- **時点合わせを変更したら、次の2つを必ず通します。** 1つ目は `to_period_returns()` と `forward_returns()` の単体テストで、規約ごとにどの行のリターンを束ねるかを固定しています。2つ目は、「次の行のトータルリターンそのもの」をシグナルにした合成データを、ファイル読み込みから IC まで通すテストです。時点合わせが正しければ h=1 の IC は全日付で 1 になり、1行でもずれると崩れます。

  ```sh
  uv run pytest tests/pipeline/test_time_alignment.py "tests/test_end_to_end.py::test_file_pipeline_is_time_aligned"
  ```

- **新しい設定項目を足すときは、`config.py`（構造）と `validation.py`（意味）の両方に検証が必要かを検討します。**
- **出力処理を変更するときは、「metric 1つ分だけ書く」場合と「最後に全体を書く」場合で同じ結果になるようにします**（`report/incremental.py`）。
- **接続先を足すときは、`io/db/config.py` に設定クラスを追加します**（[03 の 10 章](03_config.md#10-db-接続の設定)）。
- **コードのコメントと docstring は日本語で書きます。** 型エイリアスは `TypeAlias` で書きます。
- **コードを変更したら、対応する文書も更新します**（[マニュアルの保守](README.md#マニュアルの保守)）。

次の2点は機械的に検査していないので、レビューで確認します。

- metric のコードがリターンをずらしていないこと（metric を追加・変更するとき）。
- ホスト名、IP アドレス、URL、認証情報がコードや config に混入していないこと（コミットの前）。

未決の事項として、DB からの取得の速度は実測していません。遅い場合は、分割して読み込む方法などを検討します。
