# 設定項目（config.yaml）リファレンス

[マニュアルの目次（README.md）](README.md) に戻る

config.yaml に書けるすべての項目について、「何を指定するキーか」「省略するとどうなるか」「間違えると何と表示されるか」を引くための文書です。
評価を実行する人（config を書く人）を対象にしています。はじめて config を書く場合は、[動く最小構成](#動く最小構成) と
[自分のデータで config を書く手順](#自分のデータで-config-を書く手順) から読んでください。

- モジュールの全体像は [02 の 1 章](02_features.md#1-全体像)、シグナルとリターンの時点の対応（時点合わせ）の規則は [02 の 4 章](02_features.md#4-時点合わせの仕様)、
  metric ごとのパラメータは [04 の 3 章](04_metrics.md#3-metric-ごとの仕様) にあります。
- シグナル、カレンダー、ホライズン、フォワードリターン、DataBundle などの用語は [01 の「この文書で使う用語」](01_design.md#この文書で使う用語) にまとめています。
  この文書では、カレンダーの `calendar.index` の列の値（`20240531` のような8桁の日付、または `202405` のような6桁の月）を「日付キー」と呼びます。
- 例には、リポジトリに同梱の2つのサンプルを使います。example1（`examples/example1`）は日付キーが8桁の日付の月次データ、
  example2（`examples/example2`）は日付キーが6桁の月の月次データで、どちらも値が乱数の合成データです（[12 章](#12-付録-すべての項目を載せた例)）。
  コマンドは、リポジトリのルートで実行する前提で書いています。

この文書の表の「既定値」は、キーを省略したときに使われる値です。「必須」と書いたキーは省略できません。

## 1. 全体の骨格

### トップレベルのセクション一覧

| セクション | 必須か | 既定値 | 役割 |
|---|---|---|---|
| `version` | 必須 | — | config の書式バージョン。現在は `1` のみ |
| `calendar` | config からデータを読む場合は必須 | なし | カレンダーファイル。評価の時間軸の唯一の定義 |
| `period` | 任意 | カレンダーの全行 | 評価期間 |
| `horizons` | 任意 | `[1]` | フォワードリターンのホライズン（単位はカレンダーの行数） |
| `data` | config からデータを読む場合は必須 | なし | シグナル（`signals`）と分類（`classification`） |
| `universe` | 任意 | なし | 評価対象のユニバース |
| `benchmark` | 任意 | なし | ベンチマークウェイト |
| `returns` | 任意 | `[]` | リターンのソース。フォワードリターンを使う metric には必要 |
| `risk_models` | 任意 | `{}` | リスクモデル（エクスポージャー等） |
| `metrics` | 任意 | `[]` | 実行する metric とパラメータ |
| `execution` | 任意 | `n_jobs: 1` | 並列数 |
| `output` | 任意 | `dir: out`、`formats: [csv]` | 出力先と形式 |
| `cache` | 任意 | `enabled: true`、`dir: .cache` | DB の取得結果のキャッシュ |
| `portfolios` | 予約 | `[]` | 未実装。要素を書くとエラー |

`calendar` と `data` が構造上は省略できるのは、Python API で DataBundle（時点合わせ済みのデータ）を直接渡す使い方があるためです。
`alpha-eval run` のように config からデータを読む場合は、どちらも必須です（[2段階の検証](#検証は2段階)で確認されます）。

### 動く最小構成

次の内容を `examples/example1/` の下に保存すると（ファイル名は任意。以下では `config.min.yaml` とします）、そのまま検証と実行を通ります。
`examples/example1/input` がなければ、`uv run python examples/example1/make_sample_data.py` で作ります。

```yaml
version: 1

calendar:
  path: ./input/calendar.dat

data:
  signals:
    source: file
    path: ./input/alpha/ai/{Label2}.csv
    columns: {asset_id: nri_code}
    values: {ai: score}

returns:
  - source: file
    path: ./input/returns/{Base_day}.csv
    columns: {asset_id: nri_code}
    series:
      total: {column: ret, aggregation: cumprod}

metrics:
  - name: ic
```

```sh
uv run alpha-eval validate examples/example1/config.min.yaml   # 計算せずに設定を検証する
uv run alpha-eval run examples/example1/config.min.yaml        # 評価して結果を書き出す
```

省略した項目は既定値になります。この例では、カレンダーの全行を評価期間とし、ホライズンは 1、結果は config と同じ場所の `out/` に csv で書き出します。
カレンダーの最終行まで評価するので、`Calendar ends 1 row(s) short of the longest horizon; ...` という警告が1件出ます。
最終行のフォワードリターンを作れないという意味で、エラーではありません。

`nri_code` は社内の銘柄コードで、example1 のファイルでは銘柄コードの列名にもなっています。config で必須の名前ではありません。
自分のデータで銘柄コードの列が `code` なら、`columns: {asset_id: code}` と書きます（[columns: role と実カラム名の対応](#columns-role-と実カラム名の対応)）。

config が指しているファイルの中身を、example1 の同梱の config（`examples/example1/config.yaml`）が使うものも含めて示します。
各ファイルの先頭2行です（ユニバースは先頭2行がコメントなので3行）。カレンダーの `Base_day` が `20240531` の行は、
`Trading_day` が `20240531`、`Label2` が `20240530` です。この行には、下の `20240530.csv` や `20240531.csv` が対応します。

カレンダー `input/calendar.dat`（タブまたはスペースで区切る。[calendar](#calendar)）

```text
#Base_day	Monthend_day	Trading_day	Rskmdl_month	Label1	Label2
20180131	20180131	20180131	201712	201712	20180130
```

シグナル `input/alpha/ai/20240530.csv`（ヘッダーの先頭に `#` が付いている。[data.signals](#datasignals)）

```text
#nri_code,score
01346,-0.6599524211792704
```

シグナル `input/alpha/dss/20240530.csv`（ヘッダーなし。列は銘柄コード、スコアの順）

```text
01346,-0.1
01356,0.1
```

リターン `input/returns/20240531.csv`（`ret` がトータルリターン、`sret` がスペシフィックリターン。[6. returns](#6-returns)）

```text
nri_code,ret,sret
01346,0.13095565673423995,0.10822830159011734
```

業種分類 `input/labels/20240530.csv`（縦持ち。1行1銘柄で、`label` 列に業種コードが入る形式。[data.classification](#dataclassification)）

```text
#nri_code,label
01346,L005
```

ユニバース `input/univ/20240531.dat`（`#` で始まるコメント行のあとに、銘柄コードが1行に1つ並ぶ。[5. universe / benchmark](#5-universe--benchmark)）

```text
# format = code
# date = 20240531
01346
```

ベンチマーク `input/bm/20240531.dat`（`#` で始まるコメント行のあとに、銘柄コードとウェイトがタブ区切りで並ぶ）

```text
# format = weight
# date = 20240531
07079	0.185375
```

### 自分のデータで config を書く手順

1. **カレンダーを用意する。** 1行が1評価期間（リバランスの時点ごとに1行）のテキストファイルを作ります。
   列には、結果の日付として使う列のほかに、データのファイル名に入っている日付や月を、種類ごとに1列ずつ持たせます。
   example1 では、シグナルのファイル名が前営業日の日付なので、その日付の列 `Label2` があります。書式は [calendar](#calendar) を参照してください。
2. **`calendar.index` と、各ファイルを選ぶ列を決める。** `calendar.index` には、結果の `date` 列に出したい列を指定します（`period` と `output.by_date` も、この列の値で書きます）。
   次にデータごとに、ファイル名に入っている値がカレンダーのどの列の値かを確かめ、`path` のその位置に `{列名}` と書きます。
   全期間が1つのファイルにまとまっているデータは、`path` をそのまま書き、ファイルの日付の列を `columns` の `date` に、それと突き合わせるカレンダーの列を `match_on` に指定します。
   書き方は [source: file](#source-file)、行とファイルの対応の具体例は [02 の 4.2](02_features.md#42-どのデータがどの行に対応するか) にあります。
3. **リターンの convention を確認する。** リターンのデータで、カレンダーの行 t の値が「行 t−1 から行 t まで」のリターン（`realized`）か、
   「行 t から行 t+1 まで」のリターン（`forward`）かを確かめます。取り違えると、シグナルに対応させるリターンが1行ずれます。
   キーの説明は [returns のソースに共通のキー](#returns-のソースに共通のキー-series--aggregation--convention--valid_range)、定義は [02 の 4.3](02_features.md#43-conventionrealized--forward) にあります。
4. **`columns` / `values` / `series` を書く。** 各ソースの `columns` で、銘柄コードなどの列を role に対応付けます（[columns: role と実カラム名の対応](#columns-role-と実カラム名の対応)）。
   シグナルは `values` で、読む列とシグナル名を指定します（[data.signals](#datasignals)）。リターンは `series` で、読む列・系列名・束ね方（`aggregation`）を指定します（[6. returns](#6-returns)）。
   カンマ区切りでヘッダーのある csv 以外のファイル（タブ区切り、ヘッダーなし、コメント行つきなど）には、`read_options` も書きます（[source: file](#source-file)）。
5. **検証してから実行する。** 次の順に進めます（`config.yaml` は自分の config のパスに読み替えてください）。
   コマンドの出力の読み方は [11. 検証とよくある設定ミス](#11-検証とよくある設定ミス) にあります。

   ```sh
   uv run alpha-eval validate --skip-files config.yaml   # キー名、カレンダーの列名、metric のパラメータを確かめる
   uv run alpha-eval validate config.yaml                # 加えて、データファイルがそろっているかを確かめる
   uv run alpha-eval run config.yaml                     # 実行する
   ```

   検証は各ファイルソースの最初のファイルの見出しで列名を確かめますが、値の中身（重複や数値でない値）は読まないので、そうした誤りは `run` で初めて見つかります（[検証を通っても実行時に出るエラー](#検証を通っても実行時に出るエラー)）。
   最初は `period` で評価期間を数行に絞り、`metrics` を1つだけにして試すと、短い時間で確かめられます。

### 相対パスの基準

相対パスは、実行ディレクトリではなく **config ファイルのある場所** を基準に解決されます。
対象は `calendar.path`、各ソースの `path` と `query`、`output.dir`、`cache.dir` で、既定値（`out`、`.cache`）も同じ扱いです。`~` はホームディレクトリに展開されます。

例外が2つあります。

- `alpha-eval run --output-dir` に渡した相対パスは、実行ディレクトリが基準です。
- Python API に config を dict で渡した場合は、基準にする場所がないので、相対パスは実行ディレクトリが基準になります。

### YAML の書き方の注意

| 書きたいもの | 書き方 | 理由 |
|---|---|---|
| 日付キー（`period.start`、`period.end`、`output.by_date`） | `20190329` でも `"20190329"` でもよい | クォートなしの整数は文字列に変換されます。`2019-03-29` は不可です |
| タブ区切り | `sep: "\t"`（ダブルクォート） | ダブルクォートの中だけ `\t` がタブになります |
| 正規表現の区切り | `sep: '\s+'`（シングルクォート） | `"\s+"` は YAML の構文エラーになります |
| Python の `None` | `header: null` | `read_options` でヘッダーなしを指定するとき等 |
| `{` で始まる値 | `path: "{Label2}.csv"` | クォートしないと YAML の構文エラーになります。`./input/{Label2}.csv` のように途中にある場合は不要です |

未知のキーは、どの階層でもエラーになります（キー名の書き間違いを計算前に見つけるため）。
例外は、中身を自由に書けるマッピングである `read_options`、`columns`、`factor_groups` です。これらの中のキーは構造の検証では確認されません
（`columns` の role 名を間違えると、必要な role の対応がないものとして意味の検証でエラーになります。[意味の検証で出るエラー](#意味の検証で出るエラー) を参照）。
`metrics` の `params` の中身は、metric ごとの定義に照らして意味の検証で確認されます。

### 検証は2段階

config は次の2段階で検証され、どちらも計算を始める前に行われます。

1. **構造の検証**: キー名・型・必須項目・値の範囲・config 内の相互参照（名前の重複など）。config を読み込んだ時点で行います。
2. **意味の検証**: カレンダーを読み、プレースホルダや `match_on` がカレンダーの列か、metric が必要とするデータがあるか、ファイルが存在するか等を確認します。

計算せずに検証だけを行うには `uv run alpha-eval validate config.yaml` を使います。
出力の読み方とエラーメッセージの一覧は [11. 検証とよくある設定ミス](#11-検証とよくある設定ミス) にまとめています。

## 2. 時間軸: version / calendar / period / horizons

評価の時間軸を決める4つのセクションです。example1 では次のように書いています。

```yaml
version: 1
calendar:
  path: ./input/calendar.dat
  index: Base_day
period:
  start: "20190329"
  end: "20260731"
horizons: [1, 3, 6]
```

`version` は必須の整数で、値は `1` だけです。将来、書式を変えたときに古い config を見分けるための番号です。

### calendar

カレンダーは、1行が1評価期間を表す空白区切りのテキストファイルです。評価の時間軸はこのファイルだけで決まります。

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `path` | パス | 必須 | カレンダーファイル |
| `index` | 文字列 | `Base_day` | 内部ですべてのデータに付ける日付キーの列名。結果の `date` 列はこの列の値になります |
| `columns` | 文字列のリスト | なし（ファイルのヘッダーを使う） | ヘッダー行のないファイルの列名。指定するとファイルのヘッダーより優先されます |

example1 のカレンダーは次の形です。列は自由に増やせ、ファイルのパスのプレースホルダや `match_on` から列名で参照します。

```text
#Base_day	Monthend_day	Trading_day	Rskmdl_month	Label1	Label2
20180131	20180131	20180131	201712	201712	20180130
20180228	20180228	20180228	201801	201801	20180227
```

ファイルの読み方は次のとおりです。

- 文字コードは UTF-8 です。区切りはタブ・スペース・全角スペースのいずれでもよく、混在していても読めます。空行は無視します。
- 先頭が数字の最初の行を最初のデータ行とみなし、その直前の行をヘッダーにします（先頭の `#` は除きます）。それより前の行はコメントとして読み飛ばします。
  データ行の間にある `#` で始まる行も無視します。
- 値はすべて文字列のまま扱います。8桁は日付（`yyyymmdd`）、6桁は月（`yyyymm`）です。月だけの1列のファイルでも構いません。
- `index` の列は、空の値がなく、重複がなく、昇順でなければなりません。

### period

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `start` | 日付キー（6桁または8桁の数字） | なし（カレンダーの先頭から） | 評価期間の開始。この値を含みます |
| `end` | 日付キー（同上） | なし（カレンダーの末尾まで） | 評価期間の終了。この値を含みます |

`start <= calendar.index の値 <= end` を満たす行が評価の対象になります。
比較は文字列として行うので、**`calendar.index` と同じ桁数で書いてください**。8桁のカレンダーに `end: "202607"` と書くと、`20260731` の行は含まれません。

### horizons

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `horizons` | 正の整数のリスト | `[1]` | フォワードリターンのホライズン。単位はカレンダーの行数。重複は不可で、昇順に並べ替えて保持します |

1行が1か月のカレンダーなら、`3` は3か月先までのリターンです。評価期間の末尾からカレンダーの最終行までが最大ホライズンに足りないと、
足りない分のフォワードリターンは欠損になり、警告（`Calendar ends 6 row(s) short of the longest horizon; ...`）が出ます。エラーではありません
（どの行が欠損になるかは [02 の 4.9](02_features.md#49-期間の末尾でリターンが足りない場合)）。

## 3. データソースに共通の項目

`data.signals`、`data.classification`、`universe`、`benchmark`、`returns`、`risk_models` の各構成要素は、どれも「ソース」として同じ書き方をします。
ソースの種類は `source` で選びます。`file` と `db` はどこでも使え、`barra` は `returns` と `risk_models` だけで使えます。

### source: file

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `source` | `file` | 必須 | ソースの種類 |
| `path` | パス | 必須 | ファイルのパス、または `{列名}` を含むテンプレート |
| `read_options` | マッピング | `{}` | 読み込み関数にそのまま渡す引数 |
| `columns` | マッピング | `{}` | `{role: 実カラム名}`（[後述](#columns-role-と実カラム名の対応)） |
| `match_on` | 文字列 | `calendar.index` | 単一ファイルのとき、ファイルの日付の列と突き合わせるカレンダーの列 |

`path` の書き方で、読み方が2通りに分かれます。

**日付ごとのファイル（プレースホルダあり）**: `{Label2}` のように、カレンダーの列名を波括弧で囲んで書きます。
カレンダーの行ごとに、その行の値を埋め込んだファイルを読みます。どの列でファイルを選んでも、データに付く日付キーは `calendar.index` の値です。
example1 では、`Base_day` が `20240131` の行は `Label2` が `20240130` なので、`./input/alpha/ai/20240130.csv` が `20240131` のデータとして読まれます。
複数の行が同じファイルを指しても構いません（1回だけ読みます）。この読み方では `match_on` を使いません。
ファイルの中に日付の列があっても、行の対応付けには使いません（`columns` で `date` に対応付けた列は読み捨てます）。

**単一ファイル（プレースホルダなし）**: 全期間が縦に並んだ1つのファイルとして読みます。`columns` に `date` の指定が必要で、
その列の値を `match_on` の列の値と突き合わせて、カレンダーの行に対応付けます。どの行にも対応しないデータは捨てます。
ファイルの日付は、`match_on` の列と同じ桁数（8桁の日付または6桁の月）に揃えてから比べます。
次の例のファイルは説明用で、リポジトリには含まれていません。

```yaml
data:
  signals:
    - source: file
      path: ./input/alpha/value_all.parquet
      match_on: Label2                       # ファイルの日付の列をこの列と突き合わせる
      columns: {asset_id: code, date: dt}
      values: [value_score]
```

ファイルの形式は拡張子で決まります。

| 拡張子 | 読み込み関数 | 備考 |
|---|---|---|
| `.csv`、`.dat` | `pandas.read_csv` | 区切り・コメント行・ヘッダーの有無は `read_options` で指定します |
| `.tsv` | `pandas.read_csv` | 区切りの既定がタブになります |
| `.parquet` | `pandas.read_parquet` | |
| `.pkl` | `pandas.read_pickle` | |

`read_options` には、区切り（`sep`）、コメント行の記号（`comment`）、ヘッダーなし（`header: null`）と列名（`names`）などを書きます。
書き方の例は [5. universe / benchmark](#5-universe--benchmark) にあります。

読み込み時に、次の処理が自動で入ります。

- `.csv` / `.dat` / `.tsv` では、ヘッダーの先頭の `#`（例: `#nri_code`）を除きます。
- `.csv` / `.dat` / `.tsv` では、銘柄コードの列を文字列として読みます（先頭のゼロを保つため）。`read_options` に `dtype` を書いた場合は、そちらが使われます。
- 銘柄コード（`asset_id`）は文字列に、日付は8桁または6桁の文字列に揃えます。日付は `2024-01-30`、`2024/01/30`、整数の `20240130`、日時型のいずれでも読めます。

### source: db

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `source` | `db` | 必須 | ソースの種類 |
| `connection` | 文字列 | 必須 | 接続先名（[10. DB 接続の設定](#10-db-接続の設定)）。例: `risk_models`、`factor_jp` |
| `query` | パス | 必須 | SQL ファイル（UTF-8）。リポジトリには含まれていないので、自分で用意します |
| `columns` | マッピング | `{}` | `{role: 実カラム名}`。`date` の指定が常に必要です |
| `match_on` | 文字列 | `calendar.index` | DB の日付と突き合わせるカレンダーの列。`returns` と `factor_returns` では必須です |

次は、日次のリターンを DB から読む例です（`series` は [6. returns](#6-returns) で説明します）。

```yaml
returns:
  - source: db
    connection: risk_models
    query: ./sql/barra_returns_daily.sql
    match_on: Trading_day
    columns: {date: dt, asset_id: code}
    series:
      total:    {column: ret,  aggregation: cumprod}
      specific: {column: sret, aggregation: cumsum}
```

SQL の書き方の決まりは次のとおりです。

- 期間は `:start` と `:end` で参照します。値（8桁の `yyyymmdd` の文字列）は自動で渡されます。取得する範囲は `:start` より後、`:end` 以前です。
- SQL の中に文字としてのコロンを書くときは `\:` と書きます。
- 1つのソースにつき、必要な期間全体を1回のクエリで取得します。

```sql
-- 書き方の例（テーブル名・列名は仮のもの）
SELECT dt, code, ret, sret
FROM daily_returns
WHERE dt > :start AND dt <= :end
```

`match_on` の列は、日付（8桁）か月（6桁）の列でなければなりません。取得したデータの使い方は用途で異なります。

- **`returns` と `factor_returns`**: 日次のデータを取得し、`match_on` の列を期間の境界としてカレンダーの期間に集約します（[期間集約のルール](#期間集約のルール)）。
- **それ以外（シグナル・分類・ユニバース等）**: 日付が `match_on` の列の値と一致する行だけを、その行のデータとして使います。
  `match_on` が月の列の場合は、月内のすべての日がその月の行に対応するので、SQL は（月, 銘柄）ごとに1行だけを返す必要があります。

### columns: role と実カラム名の対応

role は、列の意味を表す内部の名前です。ソースごとに異なる列名を、`columns` で role に対応付けます。

| role | 意味 |
|---|---|
| `asset_id` | 銘柄コード |
| `date` | 日付（単一ファイルと DB で使う） |
| `category` | 分類（業種など） |
| `weight` | ウェイト |
| `factor` | ファクター名 |
| `value` | 値（ファクターリターン） |

用途ごとに必要な role は次のとおりです。**必要な role は、実カラム名が role 名と同じでも省略できません**（`columns: {asset_id: asset_id}` のように書きます）。
`columns` に書かなかった列は、元の名前のまま残ります。

| 用途 | 必要な role |
|---|---|
| `data.signals`、`returns` | `asset_id` |
| `data.classification` | `asset_id`、`category`（`one_hot: true` なら `asset_id` だけ） |
| `universe` | `asset_id`（`weight` も書けるが使わない。[5 章](#5-universe--benchmark)） |
| `benchmark` | `asset_id`、`weight` |
| `risk_models.*.exposures`、`risk_models.*.specific_risk` | `asset_id` |
| `risk_models.*.factor_covariance` | `factor` |
| `risk_models.*.factor_returns` | `factor`、`value` |

単一ファイルと DB のソースでは、どの用途でも、これに加えて `date` が必要です。

role の対応の不足は、意味の検証でエラーになります（ファイルを読まないので `--skip-files` でも見つかります）。`columns` に書いた列がファイルにない場合は、ファイルの存在の確認に続けて、各ソースの最初のファイルの見出しを読んで見つけます（[意味の検証で出るエラー](#意味の検証で出るエラー)）。ファイルによって列が違う場合（途中のファイルだけ列名が変わっているなど）は、データを読む時点でエラーになります（[検証を通っても実行時に出るエラー](#検証を通っても実行時に出るエラー)）。

## 4. data（signals・classification）

### data.signals

シグナルのソースです。共通の項目に加えて `values` を持ちます。ソースが1つなら、リストにせずそのまま書けます。

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `values` | 文字列のリスト、または `{シグナル名: 実カラム名}` | 必須（空は不可） | 読み込むシグナルの列。リストなら列名がそのままシグナル名になります |

```yaml
data:
  signals:
    - source: file
      path: ./input/alpha/ai/{Label2}.csv
      columns: {asset_id: nri_code}
      values: {ai: score}
    - source: file
      path: ./input/alpha/dss/{Label2}.csv   # ヘッダーなしのファイル
      read_options: {header: null, names: [nri_code, score]}
      columns: {asset_id: nri_code}
      values: {dss: score}
```

- シグナル名は、すべてのソースを通して一意でなければなりません。上の例のように列名が同じ（`score`）場合は、マッピングの形で名前を付け替えます。
- シグナルの値は数値でなければなりません。数値に変換できない値があると、読み込み時にエラーになります。
- 複数のソースは（日付, 銘柄）で外部結合します。あるソースにしかない銘柄では、他のソースのシグナルは欠損になります。

### data.classification

業種などの分類です。`category_ic` など、カテゴリ別の metric に必要です。共通の項目に加えて `one_hot` を持ちます。
ファイルの持ち方は、縦持ち（1行1銘柄で、分類の列にカテゴリの値が入る）と one-hot（カテゴリごとに 0/1 の列がある）のどちらにも対応しています。

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `one_hot` | 真偽値 | `false` | `false` なら縦持ち（`columns` の `category` に対応づけた列の値をカテゴリにする）。`true` なら one-hot（`asset_id` 以外のすべての列を 0/1 の列とみなし、1 が立っている列名をカテゴリにする） |

縦持ち（example1）:

```yaml
data:
  classification:
    source: file
    path: ./input/labels/{Label2}.csv        # 列は nri_code, label（値は L001 など）
    columns: {asset_id: nri_code, category: label}
```

one-hot:

```yaml
data:
  classification:
    source: file
    path: ./input/labels/{Label2}.csv        # 列は nri_code, L001, ..., L010（0/1）
    columns: {asset_id: nri_code}
    one_hot: true
```

- 縦持ち（`one_hot: false`、既定）のときは、`columns` に `category` を指定します。
- `one_hot: true` のとき、どの列も立っていない銘柄のカテゴリは欠損になり、1銘柄に複数の列が立っているとエラーになります。
- どちらで読んでも、DataBundle の `classification` は同じ形（`category` 列にカテゴリの値）になります。

## 5. universe / benchmark

どちらも共通の項目だけを持つソースです（追加のキーはありません）。

| セクション | 役割 | 必要な role |
|---|---|---|
| `universe` | シグナルを、ここにある（日付, 銘柄）に絞ります。使うのは銘柄だけです | `asset_id` |
| `benchmark` | ベンチマークウェイト。ベンチマークを使う metric（ベンチマーク対比の超過リターンなど）に必要です | `asset_id`、`weight` |

```yaml
universe:
  source: file
  path: ./input/univ/{Trading_day}.dat
  read_options: {sep: "\t", comment: "#", header: null, usecols: [0], names: [nri_code]}
  columns: {asset_id: nri_code}

benchmark:
  source: file
  path: ./input/bm/{Trading_day}.dat
  read_options: {sep: "\t", comment: "#", header: null, names: [nri_code, weight]}
  columns: {asset_id: nri_code, weight: weight}
```

- ユニバースのファイルは、銘柄の列だけを持つ形が基本です。ウェイトなどほかの列があっても構いません（使いません）。
- ヘッダーのないファイルでは、上の例のように `read_options` に `usecols: [0]` と `names: [nri_code]` を書くと1列目だけを読むので、銘柄だけのファイルと、2列目にウェイトがある旧形式のファイルのどちらも読めます。
- ユニバースで絞るのはシグナルだけです。分類・リターン・ベンチマークウェイトは絞りません。
- **ベンチマークウェイトは `benchmark` からだけ読みます。** `benchmark` を書かなければベンチマークウェイトはなく、ベンチマークを使う metric（`requires` に `benchmark_weights` を持つもの）は計算前の検証でエラーになります（`` requires data that is not configured: ['benchmark_weights'] (set `benchmark`) ``）。ユニバースのウェイトで代わりにすることはしません（ベンチマークを指定し忘れたときに、意図しないウェイトでベンチマーク対比の結果が計算されるのを防ぐため）。
- 以前の版では、`universe.columns` に `weight` を書くと、`benchmark` がないときにそのウェイトをベンチマークとして使っていました。互換のため、`universe.columns` の `weight` は今もエラーにはしませんが、使わずに警告（`` universe.columns: `weight` is ignored ... ``）を出します。そのウェイトをベンチマークにしたい場合は、同じファイルを `benchmark` に指定してください（`path`・`read_options` は同じで、`columns` に `asset_id` と `weight` を書きます）。
- 現在の組み込み metric は、ベンチマークウェイトを使いません。読み込んだウェイトは DataBundle に入り、追加した metric から使えます（[05 の 5 章](05_adding_metrics.md#5-databundle-の使い方)）。

## 6. returns

リターンは「名前付きの系列」の集合として定義します。`returns` はソースのリストで、各ソースが1つ以上の系列を持ちます。
metric は系列名（例: `total`、`specific`、`excess`、`usd_total`）でリターンを指定します。

### returns のソースに共通のキー: series / aggregation / convention / valid_range

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `series` | `{系列名: {column, aggregation}}` | 必須（1件以上。`barra` は書き方が異なり、既定値があります） | 系列の定義 |
| `series.<系列名>.column` | 文字列 | 必須 | ソース上の実カラム名 |
| `series.<系列名>.aggregation` | `cumprod` / `cumsum` | 必須 | 複数の期間のリターンを束ねる方法 |
| `convention` | `realized` / `forward` | `realized` | カレンダーの行 t のリターンが、どの期間のリターンかの約束 |
| `valid_range` | `[下限, 上限]` | `[-1, 10]` | リターンとして有効な範囲（両端を含む）。範囲外の値は欠損にします |

それぞれの規則の詳しい説明と数値例は、[02 の 4 章](02_features.md#4-時点合わせの仕様) にあります。ここでは、config を書くときに必要な点だけを述べます。

**series と aggregation**: `aggregation` には、その系列を複数の期間にわたって束ねる方法を書きます。

| 値 | 束ね方 | 累積の計算 | サンプルの config で使っている系列 |
|---|---|---|---|
| `cumprod` | 複利 | `(1 + r).cumprod() - 1` | トータルリターン（`total`） |
| `cumsum` | 加算 | `r.cumsum()` | スペシフィックリターン（`specific`） |

系列の `aggregation` は、次の計算に使います（式は [02 の 4.4](02_features.md#44-フォワードリターンの定義)）。

- 日次のリターンからカレンダーの期間への集約（`db` と `barra`）
- フォワードリターンの計算
- `alpha_decay` の1期間あたりへの換算（`spread_per_period`）
- 累積リターンの表の既定の計算方法

系列名は自由に付けられますが、すべてのソースを通して一意でなければなりません。
束ね方は系列名からは決まらないので、`total` という名前の系列でも `aggregation` は省略できません（`source: barra` だけは `series` の書き方が異なります。[source: barra のリターン](#source-barra-のリターン)）。

**古い config からの移行**: 系列の `kind` は廃止しました。`kind: total` は `aggregation: cumprod` に、`kind: specific` は `aggregation: cumsum` に書き換えます。
metric の `aggregation.cumulative` のキーは系列名になり、指定は書いた系列だけに効きます。同じ方法にしたい系列が複数あれば、系列ごとに書きます。値の `compound` は `cumprod` に、`sum` は `cumsum` に書き換えます。
`source: barra` の `series`（`{total: total, specific: specific}` の形）は、そのまま使えます。

**convention**: 行 t の値が「行 t−1 から行 t まで」のリターンなら `realized`、「行 t から行 t+1 まで」のリターンなら `forward` と書きます。
取り違えると、シグナルに対応させるリターンが1行ずれます（先読み、または1行の遅れ）。定義は [02 の 4.3](02_features.md#43-conventionrealized--forward) にあります。

**valid_range**: 範囲外の値は、データの誤り（DB の特殊値や単位の誤りなど）とみなして欠損にし、件数と例を警告します。
下限は -1（-100%）以上、かつ上限より小さくなければなりません。統計的な外れ値の処理ではないので、壊れた値だけが外れる緩い範囲にしてください
（何に適用されるかは [02 の 4.6](02_features.md#46-valid_range範囲外の値の扱い)）。

### source: file のリターン

共通の項目（[source: file](#source-file)）に、上のキーが加わります。ファイルの値は、すでにその行の期間リターンであるとみなし、集約はしません。

```yaml
returns:
  - source: file
    path: ./input/returns/{Base_day}.csv
    convention: realized
    columns: {asset_id: nri_code}
    series:
      total:    {column: ret,  aggregation: cumprod}
      specific: {column: sret, aggregation: cumsum}
```

リターンは、評価期間の1行前から、評価期間の末尾の最大ホライズン行先まで読みます。評価期間の外にあたる行のファイルは、なくてもエラーにならず、その行が欠損になります
（実行時のログに `N optional file(s) outside the period not found, treated as missing: ...` と出ます）。評価期間の中の行のファイルがないと、検証でエラーになります
（[02 の 4.9](02_features.md#49-期間の末尾でリターンが足りない場合)）。

### source: db のリターン

共通の項目（[source: db](#source-db)）に、上のキーが加わります。`match_on` は必須です。日次のリターンを取得し、カレンダーの期間に集約します。
書き方の例は [source: db](#source-db) にあります。

### source: barra のリターン

社内 DB の Barra の日次リターンを、SQL を書かずに読みます。`path`、`query`、`columns` は書きません。
以下の BID は Barra の銘柄 ID、nri_code は社内の銘柄コードです。

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `source` | `barra` | 必須 | ソースの種類 |
| `model` | `JPE4` / `GEMLT` / `GEM3` | 必須 | Barra のモデル |
| `match_on` | 文字列 | 必須 | 期間の境界にするカレンダーの列（日付または月） |
| `convention` | `realized` / `forward` | `realized` | 上記と同じ |
| `currency` | `local` / `USD` | `local` | トータルリターンの通貨。`USD` なら日次で為替を掛けて USD 建てにします。スペシフィックリターンには影響しません |
| `asset_id` | `nri_code` / `bid` | `JPE4` は `nri_code`、`GEMLT` と `GEM3` は `bid` | 結果の銘柄コードを nri_code にするか、BID のままにするか（下の説明を参照） |
| `series` | `{系列名: total または specific}` | `{total: total, specific: specific}` | 読む系列。値で Barra のデータを選びます（列名と `aggregation` は選べません） |
| `valid_range` | `[下限, 上限]` | `[-1, 10]` | 上記と同じ。小数への換算（USD 換算を含む）の後の日次の値を検査します |

```yaml
returns:
  - source: barra
    model: JPE4
    match_on: Trading_day
    currency: local
    series: {total: total, specific: specific}
```

- `series` の値が `total` の系列は Barra の日次トータルリターン（DRTN）を読み、`cumprod` で束ねます。値が `specific` の系列は日次スペシフィックリターン（SRTN）を読み、`cumsum` で束ねます。どちらも % を小数に換算します。
- 系列名は自由に付けられます（例: `series: {jp_total: total, jp_specific: specific}`）。
- `asset_id: nri_code` は BID を nri_code に変換し、対応のない銘柄は捨てます。`asset_id: bid` は BID のまま使い、シグナルのある銘柄だけを取得します。シグナルの銘柄コードと同じ体系になるほうを選びます。
- 接続先 `risk_models` と `fs_trfac_glb` の環境変数が必要です（`asset_id: bid` なら `risk_models` だけ）。

### 期間集約のルール

`db` と `barra` のリターンは日次で取得し、`match_on` の列を期間の境界として、カレンダーの1行ごとのリターンに集約します（`file` のリターンは集約しません）。
行 t の1期間は、`realized` なら「前の行の境界日の翌日から、行 t の境界日まで」、`forward` なら「行 t の境界日から、次の行の境界日の前日まで」です。
境界日は、日付の列ではその日付、月の列ではその月の月末日で、月が1か月ずつ進まないカレンダーでも同じ規則です。
例と詳しい説明は次の場所にあります。

| 知りたいこと | 説明している場所 |
|---|---|
| 1期間がどの日からどの日までか（`match_on` が日付の列の場合と月の列の場合、`realized` と `forward`） | [02 の 4.5](02_features.md#45-日次データの期間集約db--barra) |
| 束ね方（各系列の `aggregation`。リスクモデルのファクターリターンは常に加算） | [02 の 4.4](02_features.md#44-フォワードリターンの定義) と [4.5](02_features.md#45-日次データの期間集約db--barra) |
| 行 t のシグナルに、どの行のリターンを対応させるか | [02 の 4.3](02_features.md#43-conventionrealized--forward) |
| 欠損の扱い（リターンを 0 で埋めない） | [02 の 4.8](02_features.md#48-欠損の扱い) |

config を書くうえでの制約は1つです。`match_on` の列は期間の境界に使うので、リターンを読む範囲の行で、重複がなく昇順でなければなりません（検証で確認されます）。

metric のパラメータにある `aggregation.cumulative` は、`quantile`、`category_quantile`、`category_returns` が出力する累積リターンの表の計算方法を、系列ごとに変える設定です（[04 の 1.3](04_metrics.md#13-リターンの集計規約-aggregation)）。
キーは `returns` で定義した系列名、値は `cumprod` か `cumsum` です。書かなかった系列は、その系列の `aggregation` で累積します。変わるのは累積リターンの表だけで、期間への集約とフォワードリターンは変わりません。

```yaml
metrics:
  - name: quantile
    params:
      aggregation:
        cumulative: {specific: cumprod}   # 系列 specific の累積だけを複利にする
```

## 7. risk_models

リスクモデルは `risk_models.<モデル名>` の形で、モデルごとにまとめて定義します。モデル名は自由に付けられ、metric のパラメータ（`risk_model`）から参照します。
定義の方法は、構成要素を1つずつソースで書く方法と、`source: barra` と書いて社内 DB の Barra を読む方法の2通りです。

組み込み metric でリスクモデルを使うのは `factor_correlation`（`exposures` と `factor_groups`）だけです。
それ以外の構成要素は、読み込まれて DataBundle に入り、追加した metric から使えます（[05 の 5 章](05_adding_metrics.md#5-databundle-の使い方)）。

### 構成要素をソースで定義する

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `exposures` | ソース（`file` / `db`） | なし | エクスポージャー。`asset_id` 以外の数値の列が、すべてファクターになります |
| `factor_covariance` | ソース（`file` / `db`） | なし | ファクター共分散。日付ごとの行列を縦に積んだ形 |
| `specific_risk` | ソース（`file` / `db`） | なし | スペシフィックリスク。形式が未確定のため、現状はソースの形式のまま読み込みます |
| `factor_returns` | ソース（`file` / `db`） | なし | ファクターリターン。long 形式（`factor`、`value`）。`convention`（既定 `realized`）も書けます |
| `specific_return` | 文字列 | なし | このモデルに対応するスペシフィックリターンの系列名。`returns` で定義した名前でなければなりません |
| `factor_groups` | `{グループ名: [ファクター名, ...]}` | なし | ファクターの分類 |

構成要素はどれも任意で、書いたものだけが読み込まれます。

```yaml
risk_models:
  barra:
    exposures:
      source: file
      path: ./input/barra/{Rskmdl_month}/exposure.dat
      read_options: {sep: "\t"}
      columns: {asset_id: code}
    factor_covariance:
      source: file
      path: ./input/barra/{Rskmdl_month}/covariance.dat
      read_options: {sep: "\t"}
      columns: {factor: factor_name}
    specific_return: specific
    factor_groups:
      style: [SIZE, MOMENTUM, VALUE, VOLATILITY]
```

- `factor_covariance` のファイルは、`factor` の列が行のファクター名、残りの数値の列が各ファクターの値、という形にします。
  日付ごとに正方行列になっていなければエラーになります。値は換算しないので、年率の小数の2乗で用意します。
- `factor_returns` を `db` で読む場合は `match_on` が必須で、日次の値を期間に加算で集約します。
- `specific_return` を書かないと、実行時に `risk_models.<モデル名>.specific_return is not set` という警告が出ます。
- example1 と example2 には、リスクモデルのファイルは含まれていません。上の例のパスは `examples/config.full.example.yaml` のものです。

### source: barra のリスクモデル

`risk_models.<モデル名>` の直下に `source: barra` と書くと、構成要素を社内 DB の Barra から読みます。

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `source` | `barra` | 必須 | Barra から読むことを示します |
| `model` | `JPE4` / `GEMLT` / `GEM3` | 必須 | Barra のモデル |
| `match_on` | 文字列 | 必須 | エクスポージャー・共分散の基準日と、ファクターリターンの期間の境界に使うカレンダーの列 |
| `convention` | `realized` / `forward` | `realized` | ファクターリターンの時点の約束 |
| `components` | リスト | `[exposures, factor_covariance, factor_returns]` | 読み込む構成要素。この3つから選びます（1件以上） |
| `specific_return` | 文字列 | なし | 上と同じ |
| `factor_groups` | `{グループ名: [ファクター名, ...]}` | なし（Barra のファクター一覧から自動で作る） | ファクターの分類 |
| `asset_id` | `nri_code` / `bid` | `JPE4` は `nri_code`、`GEMLT` と `GEM3` は `bid` | エクスポージャーの銘柄 ID の扱い。`returns` の `source: barra` と同じです |

```yaml
risk_models:
  jpe4:
    source: barra
    model: JPE4
    match_on: Trading_day
    components: [exposures, factor_covariance, factor_returns]
    specific_return: specific
```

- エクスポージャーと共分散は、`match_on` の日付（月の列なら月末日）以前で最新の Barra の営業日のデータを使います。31日より前のデータしかない場合は、欠損として警告します。
- 共分散は年率の小数の2乗に換算されます。ファクターリターンは日次の値を期間に加算で集約します。
- `factor_groups` を省略すると、Barra のファクター一覧のグループ（`risk_indices`、`industries` など）から作ります。`risk_indices` は `style` という名前でも参照できます。
- Barra のスペシフィックリスク（`specific_risk`）は未実装で、`components` に書けません（未実装の項目の一覧は [02 の 9 章](02_features.md#9-現時点でできないこと未実装)）。

## 8. metrics

実行する metric のリストです。リストの1要素が1回の実行にあたります。

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `name` | 文字列 | 必須 | metric 名 |
| `id` | 文字列 | `name` と同じ | 結果を区別する名前。同じ metric を2回以上使う場合は、一意な `id` が必要です |
| `enabled` | 真偽値 | `true` | `false` なら実行しません |
| `params` | マッピング | `{}` | metric ごとのパラメータ。省略した項目は既定値になります |

`id`（なければ `name`）を、この文書では「metric のキー」と呼びます。結果を取り出すときの名前で、出力のディレクトリ名とエラーメッセージの位置（`metrics[<キー>]`）にも使われます。

```yaml
metrics:
  - name: ic
    params: {method: spearman}
  - name: quantile
    params: {n_quantiles: 5}
  - name: quantile
    id: quantile_q10_specific
    params: {n_quantiles: 10, signals: [ai], returns: [specific], horizons: [1]}
  - name: factor_correlation
    enabled: false
```

- `name` には、登録されている metric の名前を書きます。組み込みの8つの一覧は [04 の 2 章](04_metrics.md#2-metric-一覧) にあります。
- `id` は出力のディレクトリ名になるので、英数字で始まる英数字・`_`・`-` だけ（64文字まで）で付けます。
  `/` や `..` を含む id（`ic/spearman`、`../x` など）、大文字・小文字の違いだけで他のキーと重なる id（`ic` と `IC`）、
  `by_date` は、構造の検証でエラーになります（大文字・小文字を区別しないファイルシステムでは同じディレクトリになるため）。
- 多くの metric は `signals`、`returns`、`horizons` というパラメータを持ちます。省略すると、config で定義したすべてが対象になります。
  `horizons` は、トップレベルの `horizons` に含まれる値だけを指定できます（[04 の 1.2](04_metrics.md#12-共通パラメータ-signals--returns--horizons)）。
- `params` の中身は、metric ごとの定義に照らして検証されます。未知のパラメータはエラーになります。
- `enabled: false` の metric は意味の検証の対象になりません。ただし、キーの重複の確認には含まれます。

登録されている metric とパラメータは、次のコマンドで確認できます。各 metric の計算内容とパラメータの意味は [04 の 3 章](04_metrics.md#3-metric-ごとの仕様) を参照してください。

```sh
uv run alpha-eval metrics list       # パラメータと既定値
uv run alpha-eval metrics list -v    # パラメータの型と制約（JSON Schema）
```

## 9. execution / output / cache / portfolios

### execution

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `n_jobs` | 1 以上の整数 | `1` | metric を並列に実行するプロセス数。1 のとき、または metric が1つのときは、順に実行します |

`alpha-eval run --n-jobs 4` のように、コマンドラインから上書きできます。
並列実行の挙動と、Python のスクリプトから使うときの注意は [02 の 7.3](02_features.md#73-並列実行) にあります。

### output

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `dir` | パス | `out` | 出力先のディレクトリ |
| `formats` | リスト | `[csv]` | 出力形式。`csv`、`parquet`、`xlsx` から選びます（複数可） |
| `by_date` | `all`、または日付キーのリスト | なし（出さない） | 日付ごとの断面も出力します |

```yaml
output:
  dir: ./output
  formats: [csv, xlsx]
  by_date: ["20240531", "20240628"]
```

- 出力先は `alpha-eval run --output-dir` で上書きできます。
- `csv` と `parquet` は `<dir>/<metric のキー>/<表の名前>.csv` のように、表ごとのファイルになります。全 metric の要約は `<dir>/summary.csv` に書きます。
  `xlsx` は `<dir>/report.xlsx` の1ファイルです。csv は、Excel で開いても文字化けしないように BOM 付きの UTF-8 で書きます。
- `by_date` を指定すると、日付（`date`）を持つ表をその日付の行だけに切り出して、`<dir>/by_date/<日付>/` の下に同じ形（`<metric のキー>/<表の名前>.csv`、`report.xlsx`）で書きます。
  `all` は結果に現れるすべての日付、リストは指定した日付だけです。リストの日付は `calendar.index` の値で書き、評価期間にない日付はエラーになります。
- 形式によらず、実行の記録として `<dir>/run_info.json` が書かれます。
- `alpha-eval run` と `evaluate(save=...)` は、metric が1つ終わるたびに、その metric の表と日付ごとの断面（csv / parquet）、要約、`run_info.json` を書きます。
  xlsx だけは最後（例外や Ctrl-C で中断した場合はその時点）にまとめて書くので、プロセスが強制終了されると、その実行の xlsx は書かれません。
  どこまで書けたかは `run_info.json` の `state` と `pending` で分かります（[02 の 6.5](02_features.md#65-逐次書き出し途中で落ちたときに残るもの)）。
- 同じ出力先で実行し直すと、`run_info.json` は開始時に、要約は metric が1つ終わるたびに、
  各 metric の表と断面はその metric が終わったときに、`report.xlsx` は最後（中断した場合はその時点）に、今回の内容で置き換わります。
  metric の表と断面（csv / parquet）は、書く前にその metric の以前の表を消してから書くので、今回の実行で失敗した metric のディレクトリに前回の csv / parquet は残りません。
  `report.xlsx`（`by_date/<日付>/report.xlsx` を含む）は今回 xlsx を書いたときだけ置き換わるので、xlsx を書かない実行や、今回どの表にも行がない日付のブックには、前回の内容が残ります。
  途中で止まった場合、`pending` に残った metric のディレクトリにあるのは前回の実行のファイルです。config から外した metric のディレクトリも前回のまま残ります。
  以前の結果を残したい場合や、混ざるのを避けたい場合は、出力先を分けてください（CLI は `--output-dir`、Python API は `save=<ディレクトリ>`）。
- 出力されるファイルの構成と `run_info.json` の各キーは、[02 の 6.1](02_features.md#61-ディレクトリ構成と形式) と [6.3](02_features.md#63-run_infojson) にあります。

### cache

`source: db` と `source: barra` の取得結果を、ローカルの parquet ファイルに保存して再利用します。ファイルソースは対象外です。

| キー | 型 | 既定値 | 説明 |
|---|---|---|---|
| `enabled` | 真偽値 | `true` | キャッシュを使うか |
| `dir` | パス | `.cache` | キャッシュの保存先 |

キャッシュは「接続先名・SQL の本文・期間などのパラメータ」が同じなら再利用されます。
SQL を変えた場合や、評価期間や最大ホライズンを変えて取得する範囲が変わった場合は、DB から取り直します。
**有効期限はありません。** DB のデータが更新された場合は、`cache.dir` の中のファイルを削除するか、`enabled: false` にして実行してください。

### portfolios（予約のみ）

`portfolios` は、将来のポートフォリオの入力のために名前だけを予約しています。未実装で、要素を書くとエラーになります。省略するか、`portfolios: []` としてください
（未実装の項目の一覧は [02 の 9 章](02_features.md#9-現時点でできないこと未実装)）。

## 10. DB 接続の設定

`connection` には接続先名（DB 名の小文字）を書きます。ホスト名・ユーザー名・パスワードは config に書かず、環境変数から読みます
（理由は [01 の原則 4](01_design.md#原則-4-認証情報接続先ホスト名をコードにも-config-にも書かない)）。
リポジトリ直下の `.env.example` を `.env` にコピーし、使う接続先の値だけを記入します（`.env` はコミットしません）。

| 接続先名（`connection`） | DB の種類 | ホスト | ユーザー名 | パスワード |
|---|---|---|---|---|
| `ai_model`、`common`、`dss_data`、`equity`、`factor_glb`、`factor_jp`、`tvl`、`xebral` | MySQL | `LB903_HOST` | `LB903_USERNAME` | `LB903_PASSWORD` |
| `global`、`jp_market` | SQL Server | `IRDDB_HOST` | `IRDDB_USERNAME` | `IRDDB_PASSWORD` |
| `risk_models` | SQL Server | `IRDDB_HOST` | `RISK_MODELS_USERNAME` | `RISK_MODELS_PASSWORD` |
| `trc` | SQL Server | `TRC_HOST` | `TRC_USERNAME` | `TRC_PASSWORD` |
| `trs`、`nmt202` | SQL Server | `TRS_HOST` | `TRS_USERNAME` | `TRS_PASSWORD` |
| `k_gx`、`t_gx`、`t_gxl`、`idsqe` | SQL Server | `GX_HOST` | `GX1000_USERNAME` | `GX1000_PASSWORD` |
| `dss_data1`、`jetfv` | SQL Server | `DSS_HOST` | `DSS_USERNAME` | `DSS_PASSWORD` |
| `fs_trfac_glb`、`ffdl` | PostgreSQL | `RDS_APP_002_HOST` | `RDS_APP_002_USERNAME` | `RDS_APP_002_PASSWORD` |

- ユーザー名とパスワードには、`.env.example` に載っている別名も使えます（`LB903_*` の代わりに `LABDB903_USERNAME` / `LABDB903_PASSWORD`、`IRDDB_*` の代わりに `LB904_USERNAME` / `LB904_PASSWORD`、
  `GX1000_*` の代わりに `GX_USERNAME` / `GX_PASSWORD`）。両方が設定されている場合は、表の名前が優先されます。
- `.env` を自動で読み込むのは CLI（`alpha-eval`）だけです。実行ディレクトリから親に向かって `.env` を探し、すでに設定されている環境変数は上書きしません。
  Python API から使う場合は、環境変数を自分で設定してください。
- 環境変数が足りない接続先は、検証の時点でエラーとして報告されます（`connection 'risk_models' (RiskModelsConfig): host, username, password not set; ...`）。検証では実際の接続は試しません。
- 表にない接続先を使うには、`src/alpha_signal_evaluation/io/db/config.py` に接続先のクラスを追加します。DB 名の小文字が、そのまま接続先名として使えるようになります。

DB の種類ごとに、ドライバーの追加インストールが必要です。`uv sync` は指定しなかった extra のドライバーを取り除くので、
複数の種類を使う場合は、1つのコマンドに `--extra` を並べます。

```sh
uv sync --extra mssql                    # SQL Server（ODBC Driver 17 for SQL Server が必要）
uv sync --extra postgres                 # PostgreSQL
uv sync --extra mysql                    # MySQL
uv sync --extra mssql --extra postgres   # 複数を使う場合（例: source: barra で asset_id: nri_code のとき）
uv sync --all-extras                     # すべて
```

## 11. 検証とよくある設定ミス

### alpha-eval validate の使い方

```sh
uv run alpha-eval validate config.yaml                # 構造・意味・ファイルの存在を検証する
uv run alpha-eval validate --skip-files config.yaml   # データファイルの存在の確認を省く
```

問題がなければ `OK` と表示されます。エラーがあれば終了コード 1 で終わります。
`alpha-eval run` と Python API の `evaluate()` も、計算の前に同じ検証を必ず行い、エラーがあれば計算を始めず、出力先にも何も書きません。

意味の検証の結果は、次の形で表示されます（[動く最小構成](#動く最小構成) の config で、`{Label2}` を `{Label3}` に変え、`ic` に `params: {returns: [specific]}` を足した場合）。

```text
warning: Calendar ends 1 row(s) short of the longest horizon; forward returns at the end of the period are missing
error: data.signals[0].path: placeholders ['Label3'] are not calendar columns ['Base_day', 'Monthend_day', 'Trading_day', 'Rskmdl_month', 'Label1', 'Label2']
error: metrics[ic]: unknown return series ['specific']; available: ['total']
```

- `warning:` は、評価はできるが結果に影響しうる事項です。`error:` が1つでもあると評価は始まりません。
- 各行の先頭は config 上の位置です（`data.signals[0]` は `data.signals` の1番目、`metrics[ic]` はキーが `ic` の metric）。
- 意味の検証のエラーは、最初の1つで止めず、すべてまとめて表示します。データファイル（`source: file` の `path`）の存在の確認は、ほかのエラーがなくなってから行い、ファイルがすべてそろっていれば、続けて各ファイルソースの最初のファイルの見出しで列名を確認します。
- `--skip-files` は、ファイル数が多いときや、データのない環境で設定だけを確かめたいときに使います。省くのはデータファイルの存在の確認と、見出しでの列名の確認です。カレンダーファイルと SQL ファイルの存在、`columns` の role の対応は常に確認します。
- `alpha-eval run` が同じエラーを見つけた場合は、`Config validation failed:` という行に続けて、同じメッセージが `error:` の代わりに `  - ` で始まる行として並びます。

構造の検証で見つかったエラーは、次の形で表示されます。`calendar.indx` の行が config 上の位置、その次の行が内容です。
検証に使っているライブラリ pydantic のメッセージがそのまま出るもので、各エラーの後ろには pydantic の説明ページを案内する行が続きます（下の例では省略しています）。
構造のエラーが残っている間は、意味の検証には進みません。

```text
Invalid config: config.yaml
1 validation error for EvaluationConfig
calendar.indx
  Extra inputs are not permitted [type=extra_forbidden, input_value='Base_day', input_type=str]
```

位置の中の数字は 0 始まりの順番で、`file`、`db`、`barra` はソースの種類を表します（`returns.0.file.valid_range` は、`returns` の1番目の `source: file` の `valid_range`）。

Python API では、`validate(config, check_files=False)` が `--skip-files` にあたります。意味の検証の結果は、戻り値の `ok` / `errors` / `warnings` で受け取ります。
`evaluate()` は、意味の検証のエラーを `ConfigValidationError` として送出します。構造の検証のエラーは、どちらの関数でも `pydantic.ValidationError` になります。
`evaluate()` に `data=` で DataBundle を直接渡した場合は、config からデータを読まないので、metric の設定と `output.by_date` の日付だけを検証します。

### 構造の検証で出るエラー

| 設定ミス | 表示される位置 | メッセージ |
|---|---|---|
| キー名の書き間違い（例: `horizon`、`calendar.indx`） | そのキー | `Extra inputs are not permitted` |
| 必須のキーがない（例: `version`、`values`） | そのキー | `Field required` |
| `version` が 1 以外 | `version` | `Input should be 1` |
| `period.start` が `end` より後 | `period` | `Value error, period.start (20260731) is after period.end (20190329)` |
| 日付キーの書式が違う（例: `"2019-03-29"`） | `period.start` | `String should match pattern '^\d{6}(\d{2})?$'`（クォートなしの `2019-03-29` は `Input should be a valid string`） |
| ホライズンに 0 以下の値 | `horizons` | `Value error, horizons must be positive integers: [0, 1]` |
| ホライズンの重複 | `horizons` | `Value error, horizons must be unique: [1, 1]` |
| `source` の値が違う（例: `files`）。`returns` 以外に `barra` と書いた場合も同じ | そのソース | `Input tag 'files' found using 'source' does not match any of the expected tags: 'file', 'db'`（`returns` では末尾が `'file', 'db', 'barra'`） |
| `source` を書いていない | そのソース | `Unable to extract tag using discriminator 'source'` |
| `values` が空 | `data.signals.0.file.values` | `Value error, values must not be empty` |
| シグナル名の重複 | `data` | `Value error, Duplicate signal names across sources: ['ai']` |
| 同じ metric を `id` なしで2回書いた | （位置の表示なし） | ``Value error, Duplicate metric keys: ['quantile']. Set a unique `id` when using the same metric twice`` |
| リターンの系列名の重複 | （位置の表示なし） | `Value error, Duplicate return series names across sources: ['specific', 'total']` |
| `specific_return` が未定義の系列を指している | （位置の表示なし） | `Value error, risk_models.m.specific_return refers to unknown return series 'spec'. Defined series: ['total', 'specific']` |
| 廃止した `kind` を系列に書いた（[移行の方法](#returns-のソースに共通のキー-series--aggregation--convention--valid_range)） | `returns.0.file.series.total` | ``Value error, `kind` was replaced by `aggregation`: write `aggregation: cumprod` (was kind: total) or `aggregation: cumsum` (was kind: specific)`` |
| 系列の `aggregation` の値が違う | `returns.0.file.series.total.aggregation` | `Input should be 'cumprod' or 'cumsum'` |
| 系列に `aggregation` がない | `returns.0.file.series.total.aggregation` | `Field required` |
| `convention` の値が違う | `returns.0.file.convention` | `Input should be 'realized' or 'forward'` |
| `valid_range` の下限が -1 未満 | `returns.0.file.valid_range` | `Value error, valid_range lower bound must be >= -1 (-100%), got -2.0` |
| `valid_range` の下限が上限以上 | `returns.0.file.valid_range` | `Value error, valid_range lower bound must be below the upper bound, got (1.0, 0.5)` |
| DB のリターンに `match_on` がない | `returns.0.db.match_on` | `Field required` |
| Barra の `series` で列名や `aggregation` を変えようとした（例: `total: {column: DRTN, aggregation: cumprod}`）、または値が `total` / `specific` 以外の文字列 | `returns.0.barra.series` | ``Value error, series 'total': write the Barra return type only (`total` or `specific`); Barra sources have no column or aggregation to choose`` |
| Barra の `model` が違う | `returns.0.barra.model` | `Input should be 'JPE4', 'GEMLT' or 'GEM3'` |
| `n_jobs` が 0 以下 | `execution.n_jobs` | `Input should be greater than or equal to 1` |
| `formats` の値が違う | `output.formats.0` | `Input should be 'parquet', 'xlsx' or 'csv'` |
| `formats` や `horizons` をリストにしていない | そのキー | `Input should be a valid list` |
| metric の `id` に使えない文字がある（例: `../x`、`ic/spearman`） | `metrics.0.id` | `String should match pattern '^[A-Za-z0-9][A-Za-z0-9_-]*$'` |
| metric のキーが大文字・小文字の違いだけで重なる（例: `ic` と `IC`） | （位置の表示なし） | `Value error, Metric keys ['IC', 'ic'] differ only in upper/lower case; they are used as directory names, so make them differ in more than case` |
| metric のキーが `by_date`（日付ごとの断面のディレクトリ名） | （位置の表示なし） | ``Value error, Metric keys ['by_date'] are reserved for output directories; use another `id` `` |
| `portfolios` に要素を書いた | `portfolios` | `Value error, portfolios is reserved for a future version and not supported yet` |

YAML として読めないファイルと、トップレベルがマッピングでないファイル（`Config file must be a YAML mapping: ...`）は、整形されたメッセージではなく Python の例外として表示されます。

### 意味の検証で出るエラー

| 設定ミス | メッセージ（`error:` の後ろ） |
|---|---|
| `calendar` または `data` がない | `calendar: required to load data from config`（`data` も同じ形） |
| カレンダーファイルがない | `calendar: [Errno 2] No such file or directory: '...'` |
| `calendar.index` がカレンダーの列にない | `calendar: Calendar index column 'BaseDay' not found. Available: ['Base_day', ...]` |
| `calendar.columns` の数がデータの列数と合わない | `calendar: Calendar ...: calendar.columns has 2 columns but data rows have [6] columns` |
| ヘッダー行のないカレンダーに `calendar.columns` を書いていない | `calendar: Calendar file has no header row: .... Set calendar.columns (e.g. [Month])` |
| `calendar.index` の列に重複がある | `calendar: Calendar column 'Base_day' has duplicated values: [...]` |
| 評価期間にカレンダーの行がない | `period: No calendar rows in period start=20300101, end=20301231 (calendar Base_day: 20180131..20260731)` |
| パスのプレースホルダがカレンダーの列にない | `data.signals[0].path: placeholders ['Label3'] are not calendar columns ['Base_day', ...]` |
| `match_on` がカレンダーの列にない | `universe.match_on: calendar has no column 'TradingDay'` |
| 対応していない拡張子 | `universe.path: unsupported file type; supported: ['.csv', '.dat', '.parquet', '.pkl', '.tsv']` |
| 接続先名が違う | `returns[0].connection: unknown connection 'risk_model'; available: ['ai_model', ...]` |
| SQL ファイルがない | `returns[0].query: file not found: ...` |
| DB・Barra の `match_on` が日付や月の列でない | `returns[0].match_on: DB sources must match on a date (yyyymmdd) or month (yyyymm) column; 'Label2' is not`（example2 のカレンダーの場合。`Label2` の値が `87c` のような文字列です） |
| リターン・ファクターリターンの DB・Barra の `match_on` の列に重複がある | `returns[0].match_on: Calendar column '<列名>' has duplicated values: [...]` |
| DB の環境変数が未設定 | `connection 'risk_models' (RiskModelsConfig): host, username, password not set; set the environment variables (see .env.example)` |
| metric 名が違う | `metrics[icc]: Unknown metric 'icc'. Available: ['alpha_decay', ...]` |
| metric に未知のパラメータ | `metrics[ic].params.methods: Extra inputs are not permitted` |
| metric のパラメータの値が違う | `metrics[ic].params.method: Input should be 'spearman' or 'pearson'` |
| metric が未定義のシグナルを指している | `metrics[ic]: unknown signals ['xx']; available: ['ai', 'dss']` |
| metric が未定義のリターン系列を指している | `metrics[ic]: unknown return series ['specific']; available: ['total']` |
| `aggregation.cumulative` のキーが未定義の系列名 | `metrics[quantile]: aggregation.cumulative: unknown return series ['specific']; available: ['total']` |
| `aggregation.cumulative` の値が違う | `metrics[quantile].params.aggregation.cumulative.total: Input should be 'cumprod' or 'cumsum'` |
| metric の `horizons` がトップレベルにない | `metrics[ic]: horizons [12] are not in the configured horizons [1, 3, 6]` |
| metric に必要なデータが config にない | `` metrics[category_ic]: requires data that is not configured: ['classification'] (set `data.classification`) `` |
| metric が未定義のリスクモデルを指している | `metrics[factor_correlation]: unknown risk_model 'barra'; available: []` |
| `output.by_date` の日付が評価期間にない | `output.by_date: dates ['20100131'] are not in the evaluation period (20190329..20260731, 89 dates)` |
| 日付ごとのファイルが足りない | `data.signals[0]: 89 of 89 files not found: .../20190328.csv, ... (and 86 more)` |
| 単一ファイルがない | `data.signals[0]: file not found: ...` |
| `columns` に必要な role の対応がない（[3 章](#columns-role-と実カラム名の対応)の表） | `data.signals[0].columns: no column is mapped to required role(s) ['asset_id']; add e.g. columns: {asset_id: <column name>}` |
| `columns` に書いた列がファイルにない | `data.signals[0].columns: columns ['code'] not found in .../20190328.csv; available: ['nri_code', 'score'] (if the file has no header row, ...)` |
| ヘッダーのないファイルに、`read_options` の `header: null` と `names` を書いていない | 上と同じメッセージで、`available` にデータの1行目が並びます（`available: ['01346', '0.1']`） |
| `values` に書いた列がファイルにない | `data.signals[0].values: columns ['scores'] not found in .../20190328.csv; available (after renaming by columns): ['asset_id', 'score']` |
| `series` の `column` に書いた列がファイルにない | `returns[0].series: columns ['rets'] not found in .../20190329.csv; available (after renaming by columns): ['asset_id', 'ret', 'sret']` |

`no column is mapped to required role(s)` に出る role が `date` の場合は、単一ファイルか DB のソースに `date` の指定がありません。
`category` の場合は、分類のソースに `category` の指定も `one_hot: true` もありません。
role 名を書き間違えた場合（例: `asset_id` を `asset`）も、必要な role がないものとして同じエラーになります。

role の対応の確認はファイルを読まずに行うので、`--skip-files` でも行います。列名の確認は、データファイルがすべてそろっているときに、ソースごとに評価に使う最初のファイルの見出しだけを読んで行います（`--skip-files` では省きます）。DB のソースは、role の対応だけを確認します（列名は SQL を実行しないと分からないため）。

`requires data that is not configured` には、足りないデータの名前と、それを用意するために書く config の項目（`set ...`）が出ます。対応は、`signals` が `data.signals`、`classification` が `data.classification`、`forward_returns` が `returns`、`benchmark_weights` が `benchmark`、`risk_models.<構成要素>` が `risk_models.<モデル名>.<構成要素>` です。

### 検証を通っても実行時に出るエラー

検証は、ファイルソースの最初のファイルの見出しのほかは、データの中身を読みません（カレンダーを除く）。DB への接続も試しません。そのため次の誤りは、`alpha-eval run` や `evaluate()` がデータを読む時点で、Python の例外になります。
CLI ではトレースバックが表示され、その末尾に下の表のメッセージが出ます（長いメッセージは複数の行に折り返されます）。終了コードは 1 です。
metric の計算はまだ始まっていないので、出力先に書かれるのは `run_info.json` だけで、その `state` は `aborted`、`pending` は全 metric になります
（結果を書き出さない `evaluate()` の呼び出しでは、何も書かれません）。

config の書き方の誤りのうち、検証では見つからず、データを読む時点で出るもの（最初のファイルにはあるが、途中のファイルで列名が変わっている場合と、DB のソースの場合）:

| 設定ミス | 例外のメッセージ |
|---|---|
| `columns` に書いた列がデータにない | `ValueError: Missing expected columns in DataFrame: ['code']. Available: ['nri_code', 'score']` |
| `values` に書いた列がデータにない | `ValueError: Signal columns ['scores'] not found in .../{Label2}.csv. Available: ['date', 'asset_id', 'score']` |
| `series` の `column` に書いた列がデータにない | `KeyError: 'rets'` |

データの中身の誤りで出るもの（メッセージの中のパスは、config の `path` を絶対パスにしたもので、`{Label2}` などは埋め込まれません。下の表では先頭を `...` と略しています。重複の例は先頭の5件までです）:

| データの誤り | 例外のメッセージ |
|---|---|
| 同じ（日付, 銘柄）の行が2つ以上ある | `ValueError: Duplicated (date, asset_id) rows in .../{Label2}.csv: [{'date': '20240531', 'asset_id': '01346'}, {'date': '20240628', 'asset_id': '01346'}]` |
| シグナルに数値でない値がある | `ValueError: Non-numeric signal values in .../{Label2}.csv: Unable to parse string "abc" at position 1` |
| リターンに数値でない値がある | `ValueError: could not convert string to float: 'abc'` |
| `one_hot: true` の分類で、1銘柄に複数の列が立っている | `ValueError: one_hot classification has multiple categories per asset: [('20240531', '01346'), ('20240628', '01346')]` |
| `one_hot: true` の分類のファイルに、数値でない列（銘柄名など）がある | `ValueError: Unable to parse string "foo" at position 0` |
| ファクター共分散が、日付ごとの正方行列になっていない | `ValueError: Factor covariance in .../{Rskmdl_month}/covariance.dat must be a square matrix on each date; on 20240531: columns without a row ['MOM'], rows without a column []` |
| ファクター共分散に、同じ（日付, ファクター）の行が2つ以上ある | `ValueError: Duplicated (date, factor) rows in .../{Rskmdl_month}/covariance.dat: [{'date': '20240531', 'factor': 'SIZE'}, {'date': '20240628', 'factor': 'SIZE'}]` |

DB を使う場合に出るもの（検証は環境変数が設定されているかを見るだけで、実際の接続は試しません）:

| 原因 | 例外のメッセージ |
|---|---|
| DB の種類に対応するドライバーをインストールしていない（[10. DB 接続の設定](#10-db-接続の設定)） | `ModuleNotFoundError: No module named 'pymysql'`（MySQL の場合。SQL Server は `'pyodbc'`、PostgreSQL は `'psycopg2'`） |
| DB に接続できない（環境変数のホスト名の誤り、ネットワークの不通など） | MySQL の例: `OperationalError: (pymysql.err.OperationalError) (2003, "Can't connect to MySQL server on '<ホスト名>' ([Errno 61] Connection refused)")`。PostgreSQL の例: `OperationalError: (psycopg2.OperationalError) connection to server at "<ホスト名>", port 5432 failed: Connection refused`。文面は DB の種類、原因、実行環境によって変わります |


### 実行時に出る警告

| 警告 | 意味と対処 |
|---|---|
| `Calendar ends N row(s) short of the longest horizon; ...` | 評価期間の末尾のフォワードリターンが欠損になります。意図どおりなら対処は不要です |
| `'total' period returns: N values outside [-1, 10] treated as missing (e.g. [...])` | `valid_range` の外の値を欠損にしました。例に出た値を確認してください。`db` と `barra` のソースでは `period returns` の部分が `daily returns` になります |
| `Only 47% of signal rows have 'total' forward returns (h=1); check that asset_id formats match across sources` | シグナルとリターンで銘柄が半分も一致していません。銘柄コードの体系や書式が違う可能性があります |
| `risk_models.<モデル名>.specific_return is not set` | リスクモデルに対応するスペシフィックリターンの系列が指定されていません |
| `` universe.columns: `weight` is ignored; the universe only selects assets. ... `` | `universe.columns` に `weight` を書いていますが、ユニバースは銘柄の絞り込みにだけ使うので、そのウェイトは使いません。ベンチマークにしたい場合は同じファイルを `benchmark` に指定し、`universe.columns` から `weight` を消してください（[5 章](#5-universe--benchmark)）。`validate` でも出ます |

## 12. 付録: すべての項目を載せた例

[examples/config.full.example.yaml](../examples/config.full.example.yaml) は、config に書ける項目をすべて載せたコメント付きの例です。
省略できる項目には、省略時の値が書いてあります。パスや SQL ファイルは説明用のもので、そのままでは実行できません。

実行できる例は次の2つで、どちらも合成データ（各ディレクトリの `make_sample_data.py` で作る）で動きます。
Barra から読む書き方は、[examples/config.full.example.yaml](../examples/config.full.example.yaml) と、上の 6 章・7 章の `source: barra` の例を参照してください（社内 DB に接続できる環境と実データが必要です）。

| ファイル | 内容 |
|---|---|
| [examples/example1/config.yaml](../examples/example1/config.yaml) | 日付キーが8桁の日付（`Base_day`）、縦持ちの業種分類、銘柄だけのユニバースと別ファイルのベンチマーク |
| [examples/example2/config.yaml](../examples/example2/config.yaml) | 日付キーが6桁の月（`Base_month`）、シグナルソースが1つ、ベンチマークを別ファイルで指定する例 |
