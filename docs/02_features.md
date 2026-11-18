# 何ができるか（仕様）

[マニュアルの目次（docs/README.md）に戻る](README.md)

この文書は、alpha-signal-evaluation が「何を入力に取り、どう処理し、何を出力するか」をまとめた仕様書です。
モジュールを使う人と、その結果を読む人の両方を対象にしています。

この文書が詳しく説明するのは、シグナルとリターンの時点合わせの規則（[4 章](#4-時点合わせの仕様)）、出力されるファイルと実行の記録 `run_info.json`（[6 章](#6-出力)）、
実行が途中で止まったときに残るもの（[6.5](#65-逐次書き出し途中で落ちたときに残るもの)）、CLI と Python API の使い方と並列実行（[7 章](#7-使い方)）です。
それ以外は要点だけを書き、詳しい説明のある文書へリンクしています。

- config.yaml の各キー、DB 接続、エラーメッセージ: [03 設定項目リファレンス](03_config.md#1-全体の骨格)
- 各 metric の計算方法・パラメータ・出力の列: [04 組み込み metric リファレンス](04_metrics.md#2-metric-一覧)
- metric の追加: [05 metric の追加方法](05_adding_metrics.md#2-最短手順)
- なぜこの仕様なのか: [01 設計思想](01_design.md#2-4つの原則)。用語（シグナル、ホライズン、フォワードリターン、kind など）の定義も [01 の「この文書で使う用語」](01_design.md#この文書で使う用語) にあります。

## 1. 全体像

alpha-signal-evaluation は、株式のアルファシグナル（銘柄ごとのスコア）を、その後のリターンと突き合わせて評価するライブラリです。
`config.yaml` に入力データと計算したい metric（評価指標）を書き、CLI の `alpha-eval` か Python API から実行します。

| 段階 | 内容 | 担当するコード（`src/alpha_signal_evaluation/` 以下） |
|---|---|---|
| 入力 | カレンダー、シグナル、リターン、業種分類、ユニバース、ベンチマークウェイト、リスクモデル（ファイル / DB / Barra） | `io/`, `data/` |
| 検証 | 設定ミス（キーの誤記、カレンダーにない列名、存在しない metric 名・ファイルなど）を、データを読み込む前にまとめて検出する（[8 章](#8-実行前の検証validate)） | `config.py`, `validation.py` |
| データ準備 | データを読み込んでカレンダーの行に対応づけ、ホライズンごとのフォワードリターンを作り、`DataBundle` にまとめる。時点合わせはこの段階だけで行う | `pipeline/preprocess.py` |
| 計算 | `DataBundle` を各 metric に渡す。1つの metric が失敗しても他の結果は返す | `pipeline/runner.py`, `metrics/` |
| 出力 | metric ごとの表を csv / parquet / xlsx に書き、実行の記録を `run_info.json` に残す | `report/` |

`DataBundle` は、時点合わせの済んだデータ一式（シグナル、フォワードリターン、分類、リスクモデルなど）を持つオブジェクトです。
metric は `DataBundle` だけを受け取り、時点をずらす処理を一切しません。
フォワードリターンは「シグナルの時点から先のリターン」、ホライズンは「何期間先までのリターンを見るか」です（定義は [4 章](#4-時点合わせの仕様)）。

## 2. 5分で動かす（example1）

`examples/example1` は、合成データ（値はすべて乱数）で一通りの評価を試すサンプルです。

前提は、Python 3.12 以上と uv（Python のパッケージ管理ツール）が入っていて、リポジトリを取得済みであることです。コマンドはすべてリポジトリのルートで実行します。

```sh
uv sync                                                        # 初回のみ
uv run python examples/example1/make_sample_data.py           # examples/example1/input がない場合だけ実行する
uv run alpha-eval validate examples/example1/config.example.yaml
uv run alpha-eval run examples/example1/config.example.yaml
```

`validate` は計算せずに設定を確認します。example1 では警告が1件出ますが、エラーではありません（[4.9](#49-期間の末尾でリターンが足りない場合) を参照）。

```text
warning: Calendar ends 6 row(s) short of the longest horizon; forward returns at the end of the period are missing
OK
```

`run` は、ログと警告に続けて metric ごとの実行状況と出力先を表示します（経過秒数は環境によって変わります。出力先は絶対パスで表示されます）。

```text
               metric               name status  elapsed_sec
                   ic                 ic     ok        0.402
             quantile           quantile     ok        0.267
quantile_q10_specific           quantile     ok        0.038
          category_ic        category_ic     ok        0.119
          alpha_decay        alpha_decay     ok        0.528
             autocorr           autocorr     ok        0.267
   factor_correlation factor_correlation     ok        0.038
Results: .../examples/example1/output
```

出力先（config の `output.dir`。ここでは `examples/example1/output`）には次のファイルができます。各ファイルの内容は [6 章](#6-出力) で説明します。

```text
output/
├── run_info.json                 実行の記録
├── summary.csv                   全 metric の要約
├── ic/                           summary.csv, timeseries.csv
├── quantile/                     cumulative.csv, returns.csv, summary.csv
├── quantile_q10_specific/        cumulative.csv, returns.csv, summary.csv
├── category_ic/                  ic_timeseries.csv, mean_signal.csv, summary.csv
├── alpha_decay/                  decay.csv
├── autocorr/                     summary.csv, timeseries.csv
├── factor_correlation/           matrix.csv, pairs.csv
└── by_date/                      日付ごとの断面（20190329 〜 20260731 の89ディレクトリ）
```

たとえば `ic/summary.csv` の先頭3行は次のとおりです（数値は丸めて表示しています）。

| signal | returns | horizon | mean_ic | std_ic | t_stat | hit_rate | n_periods |
|---|---|---|---|---|---|---|---|
| ai | total | 1 | 0.0843 | 0.0452 | 17.61 | 0.977 | 88 |
| ai | total | 3 | 0.1120 | 0.0386 | 20.44 | 1.000 | 86 |
| ai | total | 6 | 0.1076 | 0.0437 | 13.86 | 1.000 | 83 |

評価期間は89行ですが、`n_periods` はホライズンの分だけ少なくなっています。期間の末尾でフォワードリターンを作れない行があるためです（[4.9](#49-期間の末尾でリターンが足りない場合)）。

## 3. 入力として扱えるデータ

### 3.1 データの種類

どのデータも、config に「ソース」（ファイルや DB などの読み込み元。[3.2](#32-ソースの種類)）として書きます。
ソースの実際の列名は、`columns` で role（`asset_id`・`date`・`weight` など、列の役割を表す決まった名前）に対応づけます。
たとえば example1 のファイルでは銘柄コードの列名が `nri_code`（社内の銘柄コード）なので、`columns: {asset_id: nri_code}` と書きます。
自分のデータでは、実際の列名を書いてください（role の一覧は [03 の「columns: role と実カラム名の対応」](03_config.md#columns-role-と実カラム名の対応)）。

| データ | config のキー | 何に使うか | 必須か | 対応するソース |
|---|---|---|---|---|
| カレンダー | `calendar` | 評価の時間軸の定義（1行 = 1評価期間） | 必須 | 空白区切りのテキストファイル |
| シグナル | `data.signals` | 評価対象のスコア。複数のソースを並べられる | 必須 | file / db |
| リターン | `returns` | フォワードリターンの元になる系列。系列ごとに kind（`total` / `specific`）を持つ | リターンを使う metric には必須 | file / db / barra |
| 業種分類 | `data.classification` | カテゴリ別の metric（`category_ic` など） | 任意 | file / db |
| ユニバース | `universe` | シグナルを評価対象の (日付, 銘柄) に絞る | 任意 | file / db |
| ベンチマークウェイト | `benchmark` | `DataBundle.benchmark_weights` に入る。省略時は、ユニバースの `columns` で role の `weight` に列を対応づけていれば、その列を使う | 任意 | file / db |
| リスクモデル | `risk_models.<名前>` | エクスポージャー、ファクター共分散、スペシフィックリスク、ファクターリターンを1モデル分まとめる | 任意 | file / db / barra |

- kind はリターンの種類です。`total` はトータルリターン、`specific` はリスクモデルのスペシフィックリターンで、期間をまたぐ束ね方が kind で決まります（[4.4](#44-フォワードリターンの定義)）。
- 「必須」は config からデータを読む場合の話です。手元の DataFrame を渡す Python API（[7.2](#72-python-api)）では `calendar` / `data` / `returns` は要りません。
- 組み込み metric が使うリスクモデルのデータは、いまのところエクスポージャーだけです（`factor_correlation`）。
  ベンチマークウェイト、ファクター共分散、スペシフィックリスク、ファクターリターンは `DataBundle` に読み込まれますが、使う組み込み metric はまだありません（[9 章](#9-現時点でできないこと未実装)）。自作の metric からは参照できます。

### 3.2 ソースの種類

| `source` | 読み方 | 補足 |
|---|---|---|
| `file` | 拡張子でリーダーを選ぶ: `.csv` / `.dat` / `.tsv`（`pandas.read_csv`）、`.parquet`、`.pkl` | 区切り文字やヘッダーの有無は `read_options` で `pandas` にそのまま渡す |
| `db` | 接続先の名前（`connection`）と SQL ファイル（`query`）を指定する。期間は SQL の `:start` / `:end` に自動で渡される | SQL Server / MySQL / PostgreSQL。ホスト名と認証情報は config に書かず、環境変数から読む |
| `barra` | 社内 DB の Barra（`JPE4` / `GEMLT` / `GEM3`）を SQL なしで読む | リターン（`total` / `specific`）と、リスクモデルのエクスポージャー・ファクター共分散・ファクターリターン。単位の換算（% → 小数など）は読み込み時に行う。銘柄 ID は、`JPE4` では社内の銘柄コード（nri_code）に変換し、`GEMLT` / `GEM3` では Barra の銘柄 ID（BID）のまま使うのが既定 |

ファイルには2つの置き方があります。

- **行ごとに1ファイル**: パスにカレンダーの列名をプレースホルダとして書きます（例: `./input/alpha/ai/{Label2}.csv`）。カレンダーの各行の値を埋めたファイルを読みます。
- **全期間で1ファイル**: プレースホルダのないパスを書きます。ファイル内の日付列を、`match_on` で指定したカレンダーの列（省略時は `calendar.index`）と突き合わせます。

読み込み時に次の正規化が入ります。

- CSV 系のヘッダー先頭の `#`（例: `#nri_code`）は取り除きます。
- 銘柄コード（`asset_id`）は文字列に揃えます。CSV 系のファイルでは文字列として読むので、先頭のゼロが保たれます（`read_options` に `dtype` を書いた場合は、その指定が優先されます）。
- 日付キーも文字列（`yyyymmdd` または `yyyymm`）のまま扱います。
- 同じ (日付, 銘柄) の行が重複していると、読み込み時にエラーになります。

各ソースのキーの書き方は [03 の 3 章（データソースに共通の項目）](03_config.md#3-データソースに共通の項目) を参照してください。
DB を使うには、DB の種類ごとのドライバーの追加インストールと、接続先ごとの環境変数（ホスト名・ユーザー名・パスワード）の設定が必要です。手順と環境変数名は [03 の 10 章（DB 接続の設定）](03_config.md#10-db-接続の設定) にあります。

### 3.3 カレンダーファイル

カレンダーは空白区切りのテキストファイルで、1行が1評価期間です。`calendar.index` で指定した列（既定は `Base_day`）の値が、すべてのデータに付く日付キーになります。
ファイルの形式（ヘッダー、区切り、列の値の決まり）と example1 のカレンダーの中身は [03 の「calendar」](03_config.md#calendar) を、カレンダーの行とデータの対応は [4.1](#41-カレンダー1行--1評価期間) と [4.2](#42-どのデータがどの行に対応するか) を参照してください。

## 4. 時点合わせの仕様

時点合わせとは、「ある行のシグナルに、どの期間のリターンを対応させるか」を決める処理です。
この処理はデータ準備の段階（`pipeline/preprocess.py`）だけが行い、metric は行いません（理由は [01 の原則 1](01_design.md#原則-1-時点合わせは-pipelinepreprocesspy-に閉じ込める)）。

処理は次の順に進みます。この章では、カレンダー1行分のリターンを「期間リターン」と呼びます。

1. カレンダーから評価の対象になる行を決め、各データをカレンダーの行に対応づける（[4.1](#41-カレンダー1行--1評価期間)、[4.2](#42-どのデータがどの行に対応するか)）。
2. シグナルをユニバースに絞る（[4.7](#47-ユニバースの適用と複数ソースの結合)）。
3. リターンを期間リターンに揃える。ファイルの値はそのまま期間リターンとして扱い、DB / Barra の日次の値は期間に集約する（[4.3](#43-conventionrealized--forward)、[4.5](#45-日次データの期間集約db--barra)）。データの誤りとみなす範囲外の値は、ここで欠損にする（[4.6](#46-valid_range範囲外の値の扱い)）。
4. 期間リターンを h 行分束ねて、各行のシグナルに対応するフォワードリターンを作る（[4.3](#43-conventionrealized--forward)、[4.4](#44-フォワードリターンの定義)）。

欠損の扱いは [4.8](#48-欠損の扱い)、評価期間の末尾でリターンが足りない場合は [4.9](#49-期間の末尾でリターンが足りない場合) にまとめています。

### 4.1 カレンダー1行 = 1評価期間

- 評価の対象になる行（評価行）は、`period.start <= calendar.index の値 <= period.end` を満たす行です。`period` を省略するとカレンダーの全行が対象です。
- `horizons` の単位は**カレンダーの行数**です。月次のカレンダーなら `horizons: [1, 3, 6]` は1・3・6か月、営業日ごとのカレンダーなら1・3・6営業日を意味します。
- ずらしは常に行の位置で行います。日付の差を日数で数えることはありません。

### 4.2 どのデータがどの行に対応するか

どの列でファイルを選んでも、読み込んだデータには `calendar.index` の値が日付キーとして付きます。
example1 のカレンダーの4行を例にします。

| Base_day | Trading_day | Rskmdl_month | Label2 |
|---|---|---|---|
| 20240531 | 20240531 | 202404 | 20240530 |
| 20240628 | 20240628 | 202405 | 20240627 |
| 20240731 | 20240731 | 202406 | 20240730 |
| 20240830 | 20240830 | 202407 | 20240829 |

example1 の config では、行 `20240531` に次のファイルが対応します。いずれのデータも日付キーは `20240531` になります。

| データ | config のパス | 実際に読むファイル |
|---|---|---|
| シグナル `ai` | `./input/alpha/ai/{Label2}.csv` | `input/alpha/ai/20240530.csv` |
| 業種分類 | `./input/labels/{Label2}.csv` | `input/labels/20240530.csv` |
| ユニバース | `./input/univ/{Trading_day}.dat` | `input/univ/20240531.dat` |
| リターン | `./input/returns/{Base_day}.csv` | `input/returns/20240531.csv`（この行までに実現したリターン。[4.3](#43-conventionrealized--forward)） |

シグナルをいつ時点の情報で作り、いつ売買する想定にするかは、このようにカレンダーの列とファイルの対応で利用者が決めます。
モジュールは行を対応づけるだけです。

### 4.3 convention（realized / forward）

convention は、リターンのソースの「行 t の値がどの期間のリターンか」という規約です。リターンのソースごとに指定します。

| convention | 行 t の期間リターンが表す期間 | 行 t のシグナルに対応させる h 行分のリターン |
|---|---|---|
| `realized`（既定） | 行 t−1 → 行 t（その行までに実現したリターン） | 行 t+1 〜 t+h |
| `forward` | 行 t → 行 t+1（その行から先のリターン） | 行 t 〜 t+h−1 |

どちらの規約でも、でき上がるフォワードリターンは「行 t の時点から h 行先の時点まで」のリターンで、行 t のシグナルと同じ行に置かれます。
規約を取り違えると1行分の先読みまたは遅れになるので、ソースのデータがどちらかを必ず確認してください。

### 4.4 フォワードリターンの定義

h 行分の期間リターンを束ねる方法は kind で決まります。利用者は選べません。

| kind | 束ね方 | 式（期間リターンを r として） |
|---|---|---|
| `total` | 複利 | (1 + r₁)(1 + r₂)…(1 + r_h) − 1 |
| `specific` | 加算 | r₁ + r₂ + … + r_h |

example1（convention は `realized`）の銘柄 `01346` を例にします。`input/returns/` の各ファイルの値は次のとおりです。

| ファイル（行） | `ret`（total） | `sret`（specific） |
|---|---|---|
| `20240628.csv` | 0.05548 | −0.04508 |
| `20240731.csv` | −0.10592 | −0.07160 |
| `20240830.csv` | −0.04874 | −0.08778 |

行 `20240531` のシグナルに対応するフォワードリターンは次の値になります（値は丸めて表示しています）。

| 系列 | h = 1 | h = 3 |
|---|---|---|
| total | 0.05548（`20240628.csv` の値） | 1.05548 × 0.89408 × 0.95126 − 1 ≈ −0.1023 |
| specific | −0.04508（`20240628.csv` の値） | −0.04508 − 0.07160 − 0.08778 ≈ −0.2045 |

`realized` なので、行 `20240531` 自身のファイル（`20240531.csv` = 4月末から5月末までのリターン）は使われません。

### 4.5 日次データの期間集約（DB / Barra）

`source: db` と `source: barra` のリターンは日次で取得し、カレンダーの期間に集約します。ファイルのリターンは、すでに期間リターンになっているものとして集約しません。

- 期間の境界は `match_on` で指定したカレンダーの列です。example1 のカレンダーで `match_on: Trading_day`、`realized` とした場合、行 `20240628` の期間は「`20240531` の翌日から `20240628` まで」（`20240531 < 日付 <= 20240628`）です。
- `forward` の場合、行 `20240531` の期間は `20240531 <= 日付 < 20240628` です。
- `match_on` が月（6桁）の列なら、境界はその月の月末日になります。`realized` で連続した月が並ぶカレンダーでは、その月の1日から月末日までが1期間です。
  `forward` では、行の月の月末日から、次の行の月の月末日の前日までが1期間です（たとえば `202405` の行は `20240531 <= 日付 < 20240630`）。
- 期間内の束ね方は [4.4](#44-フォワードリターンの定義) と同じです（`total` は複利、`specific` は加算）。リスクモデルのファクターリターンは常に加算です。
- `source: db` は、必要な範囲全体を1クエリで取得します。SQL では `WHERE dt > :start AND dt <= :end` のように `:start` / `:end` を参照します。

### 4.6 valid_range（範囲外の値の扱い）

リターンのソースごとに `valid_range`（既定は `[-1, 10]`、つまり −100% 〜 +1000%）を持ちます。
範囲の外の値は、DB の特殊値や単位の誤りといったデータの誤りとみなして欠損にし、件数と例を警告に出します。

- DB / Barra では集約前の日次の値、ファイルでは期間の値に適用します。`total` と `specific` の両方が対象です。
- 統計的な外れ値の処理ではありません。ウィンソライズなどが必要な場合は、読み込みの段階ではなく metric 側で行う方針です（理由は [01 の原則 2](01_design.md#原則-2-欠損は欠損のまま)）。

### 4.7 ユニバースの適用と複数ソースの結合

- ユニバースを指定すると、**シグナル**をユニバースにある (日付, 銘柄) に絞ります。分類やリターンは絞りませんが、組み込み metric はシグナルのある銘柄だけを使うので、評価対象はユニバース内になります。
- シグナルのソースが複数ある場合は (日付, 銘柄) で外部結合します。片方のソースにしかない銘柄では、もう片方のシグナルが欠損になります。
- metric がシグナルとリターンを取り出すときは、両方が揃っている (日付, 銘柄) だけが残ります。

### 4.8 欠損の扱い

欠損は欠損のまま扱い、リターンを 0 で埋めることはしません。

| 状況 | 結果 |
|---|---|
| ホライズン h 行のうち1行でも期間リターンが欠損 | その (日付, 銘柄) のフォワードリターンは欠損 |
| 日次データで、期間内の全日が欠損（またはデータがない） | その期間のリターンは欠損 |
| 日次データで、期間内の一部の日だけが欠損 | その日を除いて集約する |
| `valid_range` の外の値 | 欠損にして警告する |
| one-hot の業種分類で、どの列にも 1 が立っていない銘柄 | カテゴリは欠損（複数の列に立っている場合はエラー） |

### 4.9 期間の末尾でリターンが足りない場合

フォワードリターンには、評価行の後ろに最大ホライズン分の行が必要です。そのためリターンは、評価期間の前に1行、後ろに最大ホライズン行だけ広げて読みます。

- カレンダーが後ろに足りない場合、足りない分のフォワードリターンは欠損になり、次の警告が出ます。
  example1 は `period.end` がカレンダーの最終行で `horizons: [1, 3, 6]` なので、6行不足しています。

  ```text
  Calendar ends 6 row(s) short of the longest horizon; forward returns at the end of the period are missing
  ```

  この場合、h = 1 は最後の1行、h = 3 は最後の3行、h = 6 は最後の6行のフォワードリターンが欠損します。
- シグナルのある行のうち、最短ホライズンのフォワードリターンを持つ割合が 50% 未満の系列があると、銘柄コードの書式違いを疑う警告が出ます。
- 評価期間の外の行（前の1行と後ろのホライズン分）のリターンファイルがない場合は、エラーにせずその行を欠損として扱います。この場合はログに警告が出るだけです。評価期間内のファイルがない場合はエラーです。

カレンダーの不足、`valid_range`、銘柄の一致率の警告は、CLI では黄色で表示され、`run_info.json` の `warnings` にも残ります。
リスクモデルに `specific_return`（対応するスペシフィックリターンの系列名）を指定していない場合の警告（`risk_models.<名前>.specific_return is not set`）も同じ扱いです。
警告の文面と対処は [03 の「実行時に出る警告」](03_config.md#実行時に出る警告) にあります。

## 5. 計算できる指標（組み込み metric）

組み込みの metric は次の8つです。

| 名前 | 何を測るか |
|---|---|
| `ic` | シグナルとフォワードリターンのクロスセクション相関（IC）と、その平均・t 値 |
| `quantile` | シグナルの分位ポートフォリオ（等ウェイト）のリターンと、上位 − 下位のスプレッド |
| `category_ic` | カテゴリ（業種など）内の IC と、カテゴリ別の平均シグナル |
| `category_quantile` | カテゴリ内で分けた分位ポートフォリオと、カテゴリ中立のスプレッド |
| `category_returns` | カテゴリ別の平均フォワードリターンと累積リターン |
| `alpha_decay` | ホライズン別の IC と分位スプレッド（予測力の減衰） |
| `autocorr` | シグナルのクロスセクション自己相関・偏自己相関（シグナルの持続性） |
| `factor_correlation` | シグナル同士、およびシグナルとリスクモデルのエクスポージャーの相関 |

- 各 metric が必要とするデータ、パラメータ、出力される表の列は [04 の 2 章（metric 一覧）](04_metrics.md#2-metric-一覧) と [3 章（metric ごとの仕様）](04_metrics.md#3-metric-ごとの仕様) を参照してください。
- config での指定方法（`name` / `id` / `enabled` / `params`）は [03 の 8 章（metrics）](03_config.md#8-metrics) にあります。
- metric は Python ファイルを1つ置くだけで追加できます（[05 の 2 章（最短手順）](05_adding_metrics.md#2-最短手順)）。

## 6. 出力

### 6.1 ディレクトリ構成と形式

出力先は config の `output.dir`（既定は config ファイルと同じ場所の `out`）で、CLI の `--output-dir` で上書きできます。

| パス | 内容 |
|---|---|
| `<metric のキー>/<表の名前>.<拡張子>` | 成功した metric の表。キーは config の `id`、なければ `name` |
| `summary.<拡張子>` | 全 metric の要約（[6.2](#62-summary)）。csv / parquet の場合。xlsx では `report.xlsx` の `summary` シートに入る |
| `report.xlsx` | `formats` に `xlsx` を含めた場合。全部の表を1つのブックに入れる |
| `run_info.json` | 実行の記録（[6.3](#63-run_infojson)）。形式によらず常に書く |
| `by_date/<日付>/...` | 日付ごとの断面（[6.4](#64-日付ごとの断面outputby_date)）。`output.by_date` を指定した場合だけ |

形式は `output.formats` で選びます（複数指定可、既定は `[csv]`）。

| 形式 | 内容 |
|---|---|
| `csv` | 表ごとに1ファイル。Excel で文字化けしないよう BOM 付き UTF-8 |
| `parquet` | 表ごとに1ファイル |
| `xlsx` | `report.xlsx` の1ファイル。`summary`・`status` シートに続けて、表ごとに `<キー>.<表の名前>` のシート（シート名は31文字で切り詰め）。Excel の行数上限を超える表は xlsx にだけ入らない |

出力先に以前の実行のファイルがある場合、metric の表と断面（csv / parquet）は、その metric を書く前に以前の表を消してから書きます（成功した metric は今回書く形式の分、失敗した metric はすべて）。そのほかのファイルは、今回の結果が出た順に上書きします。
そのため、実行中や中断後の出力先には以前の結果が混ざることがあります。扱いは [6.5 の「同じ出力先にもう一度実行する場合」](#同じ出力先にもう一度実行する場合) を参照してください。

### 6.2 summary

`summary.csv` は、各 metric が返す代表的な統計量を1行1統計量の縦持ち（列は `metric`, `stat`, `value`）にまとめたものです。

```text
metric,stat,value
ic,ai/total/h1/mean_ic,0.08431602966900215
ic,ai/total/h1/t_stat,17.609236611658936
ic,ai/total/h3/mean_ic,0.11201454313829742
```

`stat` の書式は metric ごとに決まっています（`ic` は `<シグナル>/<リターン系列>/h<ホライズン>/<統計量>`。一覧は [04 の 1.5（出力の共通ルール）](04_metrics.md#15-出力の共通ルール)）。失敗した metric の行は入りません。

### 6.3 run_info.json

`run_info.json` は「この出力がどの設定で、どこまで計算され、どこまでファイルに書かれたものか」の記録です。
出力形式によらず常に書かれ、実行中も metric が終わるたびに更新されます（書かれる順序は [6.5](#65-逐次書き出し途中で落ちたときに残るもの)）。
出力を使う前に、まず `state` と `status` を確認してください。`state` が `finished` なら、`status` に `ok` と載っている metric の出力は、すべて今回の実行で書かれたものです。

example1 の実行結果を、一部を省略し行を詰めて示します。

```json
{
  "created_at": "2026-11-19T06:47:33",
  "state": "finished",
  "pending": [],
  "ok": true,
  "status": [
    {"metric": "ic", "name": "ic", "status": "ok", "elapsed_sec": 0.402, "error": ""},
    {"metric": "quantile", "name": "quantile", "status": "ok", "elapsed_sec": 0.267, "error": ""}
  ],
  "errors": {},
  "warnings": [
    "Calendar ends 6 row(s) short of the longest horizon; forward returns at the end of the period are missing"
  ],
  "notes": {
    "ic": ["t_stat is Newey-West with lag = horizon - 1 (overlapping horizons)."]
  },
  "config": {"version": 1, "...": "..."}
}
```

| キー | 内容 |
|---|---|
| `created_at` | このファイルを最後に書いた日時（ローカル時刻） |
| `state` | 実行の状態。`running` / `finished` / `aborted` のいずれか（下の表） |
| `pending` | 結果がまだファイルに書かれていない metric のキー（config に書いた順）。まだ計算していない metric のほか、計算は終わったがファイルに書けなかった metric もここに残る |
| `ok` | `state` が `finished` で、全 metric が成功したときだけ `true` |
| `status` | `pending` 以外の metric ごとの成否（`ok` / `failed`）、実行時間（秒）、エラーの最終行 |
| `errors` | 失敗した metric のキー → トレースバック全文 |
| `warnings` | 検証とデータ準備で出た警告 |
| `notes` | metric が付けた注記（計算方法の補足）。結果を読むときに確認する |
| `config` | 実行に使った設定の全体（既定値を補い、パスを解決したもの） |

`state` の意味は次のとおりです。

| `state` | 意味 |
|---|---|
| `running` | 実行中、または書き出しの途中。プロセスがもう動いていないのにこの値が残っていれば、強制終了されています |
| `finished` | 全 metric を実行し、日付ごとの断面を含むすべての出力を書き終えた。すべての書き出しの最後に記録されます。失敗した metric があっても `finished` です（その場合は `ok` が `false`） |
| `aborted` | 例外や Ctrl-C で中断した。最後の書き出し（xlsx など）の途中で中断した場合も含みます |

### 6.4 日付ごとの断面（output.by_date）

`output.by_date` を指定すると、`date` 列を持つ表をその日付の行だけに切り出して、`by_date/<日付>/<metric のキー>/<表の名前>.<拡張子>` にも書きます。
リバランス時点ごとに、全シグナルの値を1か所で見比べるための出力です。

- `all` なら結果に現れる全日付、`["20240531", "20240628"]` のようなリストならその日付だけを出します（日付は `calendar.index` の値）。既定では出しません。
- 切り出した表には `date` 列がありません。日付を持たない表（期間全体の要約など）は含まれません。
- `xlsx` を指定している場合は `by_date/<日付>/report.xlsx` も書きます。

example1 の `by_date/20240531/` には次のファイルができます（`alpha_decay` と `factor_correlation` は日付を持つ表がないので含まれません）。

```text
by_date/20240531/
├── ic/timeseries.csv
├── quantile/returns.csv, cumulative.csv
├── quantile_q10_specific/returns.csv, cumulative.csv
├── category_ic/ic_timeseries.csv, mean_signal.csv
└── autocorr/timeseries.csv
```

### 6.5 逐次書き出し（途中で落ちたときに残るもの）

`alpha-eval run` と `evaluate(save=...)` は、全 metric が終わるのを待たず、metric が1つ終わるたびに結果をファイルに書きます（逐次書き出し）。
計算の途中でプロセスが落ちても、それまでに終わった metric の結果は残り、どこまで終わったかは `run_info.json`（[6.3](#63-run_infojson)）で分かります。

#### 書き出す順序

1. **開始時。** config の検証を通ると、データを読み込む前に出力先を作り、`run_info.json` を `state: running`・全 metric が `pending` の状態で書きます。以前の実行の記録が、今回の結果のように見えることを防ぐためです。出力先を作れない、または書き込めない場合は、ここでエラーになります。
2. **metric が1つ終わるたび。** 次の順に書きます。
   - その metric の表と、日付ごとの断面（csv / parquet）
   - `summary`（そこまでに書けた metric の分。csv / parquet）
   - `run_info.json`（その metric を `pending` から外し、`status` に加える）

   失敗した metric には書く表がないので、`status` と `errors` に記録するだけです。
3. **全 metric が終わった後。** `formats` に `xlsx` がある場合は、ここで初めて xlsx（`report.xlsx` と `by_date/<日付>/report.xlsx`）を書きます。すでに書いた csv / parquet の表は書き直しません。すべて書き終えたら、最後に `run_info.json` を `state: finished` にします。
4. **例外や Ctrl-C で中断したとき。** 3 の代わりに、その時点までに計算の終わった metric の分だけで xlsx を書き（まだ書けていない csv / parquet があればそれも書き）、`run_info.json` を `state: aborted` にします。終わった metric が1つもなければ、`run_info.json` だけを更新します。

最後まで実行できた場合の出力は、`report.save()` でまとめて書いた場合と同じです。

#### 何が起きたら何が残るか

| 何が起きたか | 残るもの | `state` |
|---|---|---|
| 最後まで実行できた | すべての出力 | `finished` |
| ある metric の中で例外が出た | 他の metric の出力はすべて残る。失敗は `errors` に記録され、`ok` は `false` | `finished` |
| Ctrl-C、またはデータ読み込み中などの例外で中断した | それまでに終わった metric の csv / parquet・`summary`・xlsx（1つも終わっていなければ `run_info.json` だけ） | `aborted` |
| 最後の書き出し（xlsx など）の途中で、例外や Ctrl-C により中断した | 全 metric の csv / parquet と `summary`。xlsx は書けていないことがある。`pending` は空 | `aborted` |
| プロセスが強制終了された（メモリ不足など） | 最後に終わった metric までの csv / parquet・`summary`。xlsx は残らない | `running` のまま |
| config の検証でエラーになった | 何も書かない（出力先も作らない） | ― |

example1 を `--n-jobs 1` で実行し、4つ目の metric（`category_ic`）の計算中に Ctrl-C で止めた場合、`run_info.json` は次のようになります（該当部分）。
終わっていた3つ（`ic`、`quantile`、`quantile_q10_specific`）は `status` に載り、表も出力されています。

```json
{
  "state": "aborted",
  "pending": ["category_ic", "alpha_decay", "autocorr", "factor_correlation"],
  "ok": false
}
```

#### そのほかの挙動

- **xlsx は強制終了では残りません。** 1つのブックに全部の表を入れるので、最後（または例外や Ctrl-C で中断した時点）に1回だけ書きます。強制終了に備えるなら `formats` に `csv` か `parquet` を含めてください。`xlsx` だけの場合、途中で更新されるのは `run_info.json` だけで、`status` に載った metric の表も `report.xlsx` が書かれるまではファイルにありません。
- **ファイルに書けなかった metric は `pending` に残ります。** 途中の書き出しに失敗しても（ディスクの空きがないなど）、ログにエラーを出して計算を続けます。書けなかった metric は `status` に載せず `pending` に残し、次の metric が終わったときと、最後（または中断時）の書き出しでもう一度書きます。そのため、csv か parquet を出している場合、`status` に `ok` と載っている metric の表は必ずファイルにあります。
- **書きかけの壊れたファイルは残りません。** どのファイルも一時ファイル（`.tmp-<名前>`）に書いてから置き換えます。強制終了の場合は一時ファイルが残ることがあります。
- **並列実行中の Ctrl-C では、計算中だった metric の結果は残りません。** すでに終わっている metric は書き出します（[7.3](#73-並列実行)）。
- **再開はできません。** 中断した実行を途中から続ける機能はなく、再実行すると全 metric を計算し直します。

#### 同じ出力先にもう一度実行する場合

出力先に以前の実行のファイルがあっても、開始時にまとめて消すことはしません。今回の内容で順に置き換えていきます。

| ファイル | 今回の内容に置き換わる時点 |
|---|---|
| `run_info.json` | 開始時（データを読み込む前） |
| `summary` | metric が1つ終わるたび（そこまでに終わった metric の分だけになる） |
| 各 metric のディレクトリと `by_date/` の下の csv / parquet | その metric が終わったとき。その metric の以前の表を消してから書くので、今回失敗した metric の以前の表も消える。まだ `pending` の metric のディレクトリには、以前の実行のファイルが残る |
| `report.xlsx`（`by_date/<日付>/report.xlsx` も同じ。ただし日付ごとのブックは、今回いずれかの表に行がある日付のものだけ） | 最後。例外や Ctrl-C で中断した場合は、その時点までに終わった metric だけのブックに置き換わる。強制終了された場合と、metric が1つも終わらないうちに中断した場合は、以前のブックが残る |

このため、実行中や中断後の出力先には、今回の結果と以前の結果が混ざります。どれが今回の結果かは、`run_info.json` の `status`（`ok` なら今回書いた metric）と `pending`（まだの metric）で判断してください。
metric が1つも終わらないうちに中断した場合（データ読み込み中の例外など）は、今回のものは `run_info.json` だけで（`status` は空、`pending` は全 metric）、`summary` と `report.xlsx` を含むほかのファイルはすべて以前の実行のものです。
config から外した metric のディレクトリは、以前のものが残ります（今回失敗した metric の以前の表は、その metric が終わった時点で消えます）。
以前の結果を残したい場合や、混ざるのを避けたい場合は、出力先を分けてください（CLI は `--output-dir`、Python API は `save=<ディレクトリ>`）。

#### `report.save()` で書く場合

`evaluate()` に `save` を指定しなかった場合は何も書かれず、`report.save()` を呼んだ時点で全 metric の結果をまとめて書きます。逐次書き出しではないので、計算の途中で落ちると何も残りません。
`run_info.json` は、すべて書き終えた最後に `state: finished` で書きます。日付ごとの断面を出す場合は、断面を書く直前にも `state: running` で書くので、断面の書き出し中に落ちると `running` のまま残ります。

## 7. 使い方

### 7.1 CLI

| コマンド | 内容 | オプション |
|---|---|---|
| `alpha-eval run CONFIG` | 評価を実行して結果を書き出す | `--n-jobs INT`（`execution.n_jobs` を上書き）、`--output-dir PATH`（`output.dir` を上書き） |
| `alpha-eval validate CONFIG` | 計算せずに設定を検証する | `--skip-files`（ファイルの存在確認を省く） |
| `alpha-eval metrics list` | 登録済みの metric と、パラメータの既定値を表示する | `-v` / `--verbose`（パラメータの JSON Schema を表示） |

- 共通オプション `-v` / `--verbose` はデバッグログを出します。サブコマンドの前に書きます（`uv run alpha-eval -v run config.yaml`）。
- CLI は、実行ディレクトリから親に向かって探した `.env` を読み込みます（DB の接続情報用。環境変数名は [03 の 10 章](03_config.md#10-db-接続の設定)）。すでに設定されている環境変数は上書きしません。
- config 内の相対パスは config ファイルの場所が基準です。`--output-dir` の相対パスだけは実行ディレクトリが基準です（[03 の「相対パスの基準」](03_config.md#相対パスの基準)）。

`metrics list` の出力の例です。

```text
ic  IC (spearman or pearson)  (requires: forward_returns, signals)
    signals = null
    returns = null
    horizons = null
    method = "spearman"
    min_obs = 10
```

終了コードは次のとおりです。

| 終了コード | 意味 |
|---|---|
| 0 | 成功（警告があっても 0） |
| 1 | config の誤り（計算前に一覧を表示して終了）、失敗した metric がある（他の結果は書き出し済み）、データ読み込み中の例外（トレースバックを表示） |
| 2 | コマンドの使い方の誤り（config ファイルが存在しない、未知のオプションなど） |
| 130 | Ctrl-C で中断した（それまでに終わった metric の結果は書き出し済み。[6.5](#65-逐次書き出し途中で落ちたときに残るもの)） |

### 7.2 Python API

主な関数は `alpha_signal_evaluation` から import できます。

| 関数 | 内容 |
|---|---|
| `evaluate(config, *, data=None, n_jobs=None, save=False)` | 評価を実行して `EvaluationReport` を返す |
| `validate(config, *, check_files=True)` | 計算せずに検証し、`ValidationReport`（`ok` / `errors` / `warnings`）を返す |
| `make_bundle(signals, period_returns, *, return_kinds, horizons, ...)` | 手元の DataFrame から `DataBundle` を作る |

このほか、`load_config`（config.yaml を読み込んで `EvaluationConfig` を返す）と、型の `EvaluationConfig`・`DataBundle`・`RiskModelData`・`EvaluationReport`・`ConfigValidationError` も同じ場所から import できます。
metric を自作するための `Metric`・`MetricParams`・`MetricResult`・`ReturnAggregation`・`register_metric` は [05 の 4 章（契約の詳細）](05_adding_metrics.md#4-契約の詳細) で、`DataBundle` の属性とメソッドは [05 の 5 章（DataBundle の使い方）](05_adding_metrics.md#5-databundle-の使い方) で説明します。

`config` には config.yaml のパス、dict、`EvaluationConfig` のいずれかを渡せます。dict の場合、config 内の相対パスは実行ディレクトリが基準になります。
以下の例は、リポジトリのルートで実行する前提です。

```python
from alpha_signal_evaluation import evaluate

# example1 の config は n_jobs: 4。ここでは 1 を指定して順に実行する（並列にする場合は下の注意を参照）
report = evaluate("examples/example1/config.example.yaml", n_jobs=1)

report["ic"].tables["summary"]    # metric のキーで結果を取り出す（tables は 表の名前 -> DataFrame）
report["ic"].notes                # metric の注記
report.summary()                  # 全 metric の要約（列: metric, stat, value）
report.status()                   # metric ごとの成否と実行時間
report.ok, report.errors          # 全 metric が成功したか / 失敗した metric のキー -> トレースバック
report.warnings                   # 検証とデータ準備の警告
report.dates()                    # 断面を取り出せる日付の一覧
report.at("20240531")["ic"]["timeseries"]   # ある日付の断面（metric のキー -> 表の名前 -> DataFrame）
report.save()                     # config の output 設定で書き出す（引数で出力先・形式・by_date を指定可）
```

- `evaluate()` は metric の失敗では例外を出しません。失敗した metric は `report.errors` に入り、`report[key]` で取り出そうとすると `RuntimeError` になります。
- config の誤りは `ConfigValidationError`（意味の検証）または `pydantic.ValidationError`（構造の検証）になります（[8 章](#8-実行前の検証validate)）。
- 既定では何も書き出しません。`evaluate(config, save=True)` とすると、CLI と同じ逐次書き出し（[6.5](#65-逐次書き出し途中で落ちたときに残るもの)）で `output.dir` に書きます。`save="path/to/dir"` で出力先を指定できます。
- スクリプトから `n_jobs` を 2 以上にして `evaluate()` を呼ぶ場合（引数で指定する場合も、config の `execution.n_jobs` が 2 以上の場合も）は、呼び出しを `if __name__ == "__main__":` の中に書いてください。並列実行は子プロセスを使い、macOS と Windows では子プロセスがスクリプトを先頭から読み込み直すためです（書き方の例は [7.3](#73-並列実行)）。
- Python API は `.env` を読み込みません。DB を使う場合は、環境変数を設定してから呼び出してください。

手元の DataFrame で評価する場合は、`make_bundle()` で `DataBundle` を作って `data=` に渡します。ファイルや DB の読み込みを通らず、config には metric だけを書けば足ります。
次の例は example1 のファイルを pandas で読み、2行分を評価します。

```python
import pandas as pd
from alpha_signal_evaluation import evaluate, make_bundle

root = "examples/example1/input"

# シグナル: index=(date, asset_id)、columns=シグナル名。date は Base_day、ファイル名は Label2
files = {"20240531": "20240530", "20240628": "20240627"}
signals = pd.concat(
    {
        date: pd.read_csv(f"{root}/alpha/ai/{label2}.csv", dtype=str).set_index("#nri_code")
        for date, label2 in files.items()
    },
    names=["date", "asset_id"],
).astype(float).rename(columns={"score": "ai"})

# 期間リターン: index=date、columns=asset_id。評価行の後ろに最大ホライズン分の行が要る
rows = ["20240531", "20240628", "20240731", "20240830"]
raw = {d: pd.read_csv(f"{root}/returns/{d}.csv", dtype={"nri_code": str}).set_index("nri_code") for d in rows}
total = pd.DataFrame({d: r["ret"] for d, r in raw.items()}).T
specific = pd.DataFrame({d: r["sret"] for d, r in raw.items()}).T

bundle = make_bundle(
    signals,
    {"total": total, "specific": specific},
    return_kinds={"total": "total", "specific": "specific"},
    horizons=[1, 2],                      # convention の既定は realized
)
report = evaluate({"version": 1, "metrics": [{"name": "ic"}]}, data=bundle)
```

`make_bundle()` の時点合わせの規則は config から読む場合と同じです（[4 章](#4-時点合わせの仕様)）。日付キーと銘柄コードの型は正規化しないので、シグナルとリターンで揃えておきます。
`make_bundle()` ではリスクモデルとベンチマークウェイトは設定されません。

metric を1つだけ直接実行することもできます（[04 の 1.6](04_metrics.md#16-python-から-metric-を単体で使う)）。

```python
from alpha_signal_evaluation.metrics.ic import InformationCoefficient

result = InformationCoefficient(method="pearson").compute(bundle)
result.tables["summary"]
```

### 7.3 並列実行

metric を複数のプロセスで並列に実行できます。config の `execution.n_jobs`（既定は 1）、CLI の `--n-jobs`、`evaluate(n_jobs=...)` のいずれかで指定します。

- 並列化の単位は metric です。`n_jobs` が 1、または metric が1つなら順に実行します。
- 結果の並びは、完了した順ではなく config に書いた順です。並列でも出力の内容は変わりません。
- 逐次書き出し（[6.5](#65-逐次書き出し途中で落ちたときに残るもの)）は並列でも同じで、metric が終わった順にファイルへ書きます。
- 実行中に Ctrl-C で中断すると、すでに終わっている metric の結果を書き出したうえで、ワーカープロセスをすぐに止めます。まだ始まっていない metric は実行しません。その時点で計算中だった metric の結果は失われ、`run_info.json` の `pending` に残ります（`state` は `aborted`）。
- ワーカープロセスが異常終了した場合（メモリ不足など）、その時点で終わっていなかった metric は、他のワーカーで実行中だったものや未着手のものも含めて、すべて失敗として記録されます（`state` は `finished`、該当する metric の `status` は `failed`）。終わっていた metric の結果は残ります。
- ファイルに保存せず notebook などで定義した自作の metric は、並列実行で失敗することがあるので `n_jobs=1` で実行してください（[05 の「ファイルに置く前に Python で試す」](05_adding_metrics.md#ファイルに置く前に-python-で試す)）。

Python のスクリプトから `n_jobs` を 2 以上にして `evaluate()` を呼ぶ場合は、呼び出しを `if __name__ == "__main__":` の中に書いてください。
macOS では、これがないと子プロセスの起動に失敗して処理が終わりません（example1 の config は `n_jobs: 4` です）。
子プロセスがスクリプトを先頭から読み込み直す起動方式（spawn）のためで、Windows も同じ方式です。CLI ではこの注意は不要です。

```python
from alpha_signal_evaluation import evaluate

if __name__ == "__main__":
    report = evaluate("examples/example1/config.example.yaml")   # config の execution.n_jobs（4）で並列に実行
```

### 7.4 DB キャッシュ

DB（`source: db` と `source: barra`）から取得した結果は、ローカルの parquet ファイルにキャッシュして再利用します（既定で有効です。ファイルのソースは対象外です）。
SQL を変えた場合や、評価期間・最大ホライズンを変えて取得範囲が変わった場合は取り直しますが、有効期限はないので、DB 側のデータが更新されたときは自分でキャッシュを消す必要があります。
設定（`cache.enabled` / `cache.dir`）と消し方は [03 の「cache」](03_config.md#cache) を参照してください。

## 8. 実行前の検証（validate）

設定の誤りは、データを読み込む前に検出して一覧で報告します。
`alpha-eval validate CONFIG` で検証だけを行えます。`alpha-eval run` と `evaluate()` も同じ検証を通ってから計算を始め、エラーがあれば何も書き出しません。

検証は2段階です。

1. **構造の検証**（config を読み込んだ時点）: キー名の誤記（未知のキーはエラー）、型、必須項目、値の範囲、名前の重複。エラーがあると意味の検証には進みません。
2. **意味の検証**: カレンダーを読み、パスのプレースホルダや `match_on` がカレンダーの列か、metric 名とパラメータが正しいか、metric が必要とするデータがあるか、DB 接続の環境変数があるか、などを確認します。エラーは最初の1件で止めず、すべて報告します。
   データファイルの存在は、この段階の最後に、それまでにエラーがないときだけ確認します（`--skip-files` / `check_files=False` で省略できます）。

`evaluate()` に `data=` で `DataBundle` を渡した場合は、metric と `output.by_date` の検証だけを行います。

検証は、`columns` の role の対応と、各ファイルソースの最初のファイルの見出し（列名）を確かめます。それ以外のデータの中身は、カレンダーを除いて読みません。(日付, 銘柄) の重複、数値でないシグナル、DB に接続できない、ファイルによって列が違う、といった問題は、データの読み込み中に例外として見つかります
（[03 の「検証を通っても実行時に出るエラー」](03_config.md#検証を通っても実行時に出るエラー)）。

出力の読み方とエラーメッセージの一覧は [03 の 11 章（検証とよくある設定ミス）](03_config.md#11-検証とよくある設定ミス) を参照してください。

## 9. 現時点でできないこと（未実装）

| 項目 | 現状 |
|---|---|
| ポートフォリオを入力とする分析 | 未実装。config の `portfolios` は枠だけ予約してあり、指定するとエラーになる |
| 未実装の metric | `factor_exposure`, `residual_ic`, `spread_factor_regression`, `risk_decomposition`, `brinson`。名前だけが決まっていて登録されておらず、config に書くと `Unknown metric` のエラーになる |
| ベンチマークウェイトを使う metric | 未実装。ウェイトの読み込みまではできる |
| ファクター共分散・ファクターリターン・スペシフィックリスクを使う metric | 未実装。データの読み込みまではできる |
| スペシフィックリスクの形式 | 未確定。ファイル / DB からはソースの形式のまま読み込む。`source: barra` では未実装 |
| リターンの整合チェック（トータル ≈ エクスポージャー × ファクターリターン + スペシフィック） | 未実装 |
| 外れ値の処理（ウィンソライズなど） | リターンの読み込みでは行わない（[4.6](#46-valid_range範囲外の値の扱い)）。パラメータとして持つ組み込み metric もまだない |
| HTML 形式のレポート、グラフの出力 | 未実装。出力は表（csv / parquet / xlsx）と `run_info.json` だけ |
| 中断した実行の再開 | 未実装。再実行すると全 metric を計算し直す |
