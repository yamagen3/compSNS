# R2で収集成果物をまとめて保管する

X・Threadsの提出ファイルを同じ `run_id` に集め、統合担当へ渡すための保存先です。R2はファイルを保管する基盤であり、投稿の分類・分析や重複排除は各担当が既存プロトコルに従って行います。JSON配列を自動で連結する機能はありません。

この手順の導入によって、研究設計・出力仕様は変更しません。集約と回収の役割分担は [共通プロトコル第4.2節](../protocol/core.md) に従います。

**保存先の区切り（prefix）の既定は `compsns/v2` です。** X産・Threads産を分ける配置にしたため、v1（`compsns/v1`）とは別の区切りにしました。v1で保存したものがある場合、v2の `list`・`pull` には出てきません。必要なら、元のバケットを `COMPSNS_R2_BUCKET`、`COMPSNS_R2_PREFIX=compsns/v1` に指定して取得し（配置が違うため `_irregular/` に保存されます）、v2の各区画へ上げ直します。

## 1. 保存するものと役割

データはGitへ入れず、ローカルでは `data/`、共有用には非公開R2バケットへ保存します。ファイル名・中身は [共通プロトコル第14節](../protocol/core.md#14-出力仕様sns-output-21)、読み比べは [reading.md Y6](../protocol/reading.md#y6-記録) に従います。

保存先は、**どのSNS由来か（platform）→ 誰の成果物か（role）** の順に分けます。X産とThreads産は、R2でも取得したフォルダでも、別の場所に置かれます。

| platform | 意味 |
|---|---|
| `x` | X由来のデータだけ |
| `threads` | Threads由来のデータだけ |
| `both` | 両方を含むデータ（盲検化した入力、判定結果、統合結果、読み比べの記録） |

| CLIのrole | platform | 保存する成果物の例 |
|---|---|---|
| `collector_x` | `x`（固定） | `collection_bundle_x_{run_id}.json`、`collection_report_x_{run_id}.md` |
| `collector_threads` | `threads`（固定） | `collection_bundle_threads_{run_id}.json`、`collection_report_threads_{run_id}.md` |
| `aggregator` | `both`（固定） | 回収担当（GrokBot）が作る、変換済みのデータ、受け取りの点検記録、盲検化した入力 |
| `common_coder` | `both`（固定） | `coded_posts_{run_id}.jsonl`、`coding_report_{run_id}.md` |
| `integrator` | `both`（固定） | `analysis_bundle_{run_id}.json`、`comparison_summary_{run_id}.csv`、`report_{run_id}.md` |
| `reading` | `both`（既定）、`x`、`threads` | `reading_log_{run_id}.md`、`reading_posts_{run_id}.csv` |

- 収集担当のplatformは固定です。ファイル名に `_threads_` を含むものを `x` として、`_x_` を含むものを `threads` として上げようとすると、拒否します（取り違えの防止）。
- `aggregator` と `reading` は保存先を区別するCLI用のroleです。読み比べの記録は他モードと同じ集計に混ぜません。設定票 `config/runs/{run_id}.json` は従来どおりGitで管理します。

### 権限の区画

**書き込みの権限は、SNSごとのバケットで分けます。** R2のキー（API token）は、権限をバケット単位で絞れますが、バケットの中のフォルダ（prefix）単位では絞れません。そこで、platformごとに別のバケットを使います。

| 区画（バケット） | 置くもの | 書き込み・削除できる人 |
|---|---|---|
| `compsns-x` | platform `x` のデータ | Xの収集担当（仮置き：GrokBot）、利用者 |
| `compsns-threads` | platform `threads` のデータ | Threadsの収集担当（Museがファイルを扱えない間は、Threadsの結果を上げる人）、利用者 |
| `compsns-both` | platform `both` のデータ | 利用者（統合担当）だけ |

**読み取りは、全員が3つの区画すべてをできます。** 読み取り専用のキーを1つ作り、3つのバケットすべてに付けます。

| 誰が | 書き込みキー（Object Read & Write） | 読み取りキー（Object Read only） |
|---|---|---|
| 利用者（統合担当） | 3つのバケットすべて | 不要（書き込みキーで読める） |
| Xの収集担当（GrokBot） | `compsns-x` だけ | 3つのバケットすべて |
| Threadsの収集担当 | `compsns-threads` だけ | 3つのバケットすべて |
| 回収担当（GrokBot） | ―（Xの収集担当として持つ `compsns-x` だけ） | 3つのバケットすべて |
| 共通分類担当 | 渡さない | 渡さない |

- 権限はCloudflare側で強制されます。Xの書き込みキーでThreadsの区画に書こうとすると、R2が拒否します（ツールは「このキーでは threads の区画に書けない」と表示します）。削除も同じで、自分の区画の中だけです。
- 区画の中の `role` は保存上の分類で、アクセス制御ではありません。同じ区画の中では、どのroleのファイルも同じ権限で扱えます。
- 共通分類担当には [共通プロトコル第14.3節](../protocol/core.md#143-coded_posts共通分類担当) に従って `masked_key` と本文だけを渡します。`common_coder` は分類結果を保存する分類であり、生の収集データへのアクセス権を与える意味ではありません。
- バケットを1つだけ使うこともできます（`COMPSNS_R2_BUCKET`）。その場合、配置は同じですが、権限は区画に分かれません。
- 1つのバケットの中をフォルダ単位で分ける方法として、R2の一時的な認証情報（prefixに絞れる）もあります。ただし有効期間が最長7日で、発行し直す運用が必要になるため、この手順では使いません（[公式：Temporary credentials](https://developers.cloudflare.com/r2/api/s3/temporary-credentials/)）。

GrokBot・Museなどには、それぞれの所定の成果物を作ってもらいます。ファイルを作成できない場合は、共通プロトコルの規則に従い、ファイル名付きコードブロック等で受け取り、このPCなどから元の内容を所定ファイルへ保存してアップロードします。データ・キー・生成物をWindowsのデスクトップへ保存しないでください。

## 2. Cloudflareで最初に行うこと

1. Cloudflareにログインし、対象アカウントの **Storage & databases → R2 → Overview** を開きます。
2. R2 subscriptionのcheckoutを完了します。支払方法の登録が必要です。無料枠を超えた分は従量課金され、無料枠の上限で自動停止する仕組みではありません。
3. **Create bucket** から、`compsns-x`・`compsns-threads`・`compsns-both`（提案名）の3つを作成します。ストレージクラスは **Standard** にし、公開設定は有効にしません。`r2.dev`、custom domain、CORSの設定は、このCLIから非公開S3 APIで使う構成には不要です。
4. R2の **API Tokens → Manage → Create User API token** から、次のtokenを作ります。対象はすべて **Apply to specific buckets only** で指定します。

   | token | 権限 | 対象のバケット | 渡す相手 |
   |---|---|---|---|
   | 利用者用 | Object Read & Write | 3つすべて | 利用者 |
   | X書き込み用 | Object Read & Write | `compsns-x` だけ | Xの収集担当（GrokBot） |
   | Threads書き込み用 | Object Read & Write | `compsns-threads` だけ | Threadsの収集担当 |
   | 読み取り用 | Object Read only | 3つすべて | Xの収集担当、Threadsの収集担当、回収担当 |

   1つのtokenに「あるバケットは読み書き、別のバケットは読み取りだけ」という組み合わせを持たせる代わりに、書き込み用と読み取り用の2つのキーを分けて渡します。ツールは2つを使い分けます（第3節）。
5. **Account ID・バケット名・Access Key ID・Secret Access Key** を手元のシークレット管理に保管します。Secret Access Keyは作成時しか再表示されません。Git・チャット・プロンプト・レポートへ貼らないでください。

バケットは初期状態で非公開です。CLIを利用するために公開する必要はありません。手順の公式資料は [R2の開始](https://developers.cloudflare.com/r2/get-started/)、[S3用の認証情報](https://developers.cloudflare.com/r2/get-started/s3/#2-generate-api-credentials)、[公開設定](https://developers.cloudflare.com/r2/buckets/public-buckets/) を参照してください。

## 3. このPCから接続する

R2用CLIだけ **Python 3.10以上** が必要です。既存のプロンプト生成・点検ツールのPython要件は変わりません。以下はリポジトリのルートで実行します。Windowsで必要なら `python` を `py` に読み替えます。

### 送信内容を接続せず確認する

`plan` と `--help` は認証情報も追加SDKも不要です。`plan` はファイルを読み、送信先キー・SHA-256・サイズをJSONで表示します。R2へは接続しません。

```powershell
python tools/r2_storage.py --help
python tools/r2_storage.py plan --run-id DAILY-2026W47 --role collector_x --file data/collection_bundle_x_DAILY-2026W47.json
```

### SDKと環境変数を設定する

送受信するときだけR2用の依存関係を導入します。

```powershell
python -m pip install -r requirements-r2.txt
```

| 環境変数 | 設定値 |
|---|---|
| `COMPSNS_R2_ACCOUNT_ID` | Cloudflare Account ID |
| `COMPSNS_R2_BUCKET_X` | Xの区画のバケット名（例：`compsns-x`） |
| `COMPSNS_R2_BUCKET_THREADS` | Threadsの区画のバケット名（例：`compsns-threads`） |
| `COMPSNS_R2_BUCKET_BOTH` | 共通の区画のバケット名（例：`compsns-both`） |
| `COMPSNS_R2_WRITE_ACCESS_KEY_ID`／`COMPSNS_R2_WRITE_SECRET_ACCESS_KEY` | 書き込み用のキー。`upload` と `delete --yes` で使う。読み取りしかしない人は設定しない |
| `COMPSNS_R2_READ_ACCESS_KEY_ID`／`COMPSNS_R2_READ_SECRET_ACCESS_KEY` | 読み取り用のキー。`list`・`pull`・`locate` で使う。未設定なら書き込み用のキーで読む |
| `COMPSNS_R2_PREFIX` | 任意。未設定なら `compsns/v2` |

- バケットを1つだけ使う場合は、3つの代わりに `COMPSNS_R2_BUCKET` を設定します。3つと1つを同時に設定すると、3つの指定が使われます（3つのうち一部だけの設定は拒否します）。
- 書き込み用のキーは、`COMPSNS_R2_WRITE_...` が未設定なら `AWS_ACCESS_KEY_ID`／`AWS_SECRET_ACCESS_KEY` も読みます（従来の設定との互換）。AWSを使っている環境では、`COMPSNS_R2_WRITE_...` を使ってください。

PowerShellで、そのターミナルだけに設定する例です（Xの収集担当の場合）。キーは対話入力し、値をコマンド履歴へ直接書きません。

```powershell
$env:COMPSNS_R2_ACCOUNT_ID = Read-Host "Cloudflare Account ID"
$env:COMPSNS_R2_BUCKET_X = "compsns-x"
$env:COMPSNS_R2_BUCKET_THREADS = "compsns-threads"
$env:COMPSNS_R2_BUCKET_BOTH = "compsns-both"
$env:COMPSNS_R2_WRITE_ACCESS_KEY_ID = Read-Host "X write Access Key ID"
$secret = Read-Host "X write Secret Access Key" -AsSecureString
$env:COMPSNS_R2_WRITE_SECRET_ACCESS_KEY = [System.Net.NetworkCredential]::new("", $secret).Password
$env:COMPSNS_R2_READ_ACCESS_KEY_ID = Read-Host "Read-only Access Key ID"
$secret = Read-Host "Read-only Secret Access Key" -AsSecureString
$env:COMPSNS_R2_READ_SECRET_ACCESS_KEY = [System.Net.NetworkCredential]::new("", $secret).Password
Remove-Variable secret
```

このPowerShellを閉じると、ここで設定した環境変数は失われます。`.env` などのファイルへ自動保存する機能はありません。

## 4. 収集から受け渡しまで

両SNSで同じ `run_id` を指定し、提出ファイルを1個ずつアップロードします。レポートなども、対応するroleと元のファイル名で同じように送ります。platformは収集担当では自動で決まります。読み比べの記録を片方のSNSだけで保存するときは `--platform x` または `--platform threads` を付けます。

```powershell
python tools/r2_storage.py upload --run-id DAILY-2026W47 --role collector_x --file data/collection_bundle_x_DAILY-2026W47.json
python tools/r2_storage.py upload --run-id DAILY-2026W47 --role collector_threads --file data/collection_bundle_threads_DAILY-2026W47.json
python tools/r2_storage.py list --run-id DAILY-2026W47
python tools/r2_storage.py pull --run-id DAILY-2026W47
```

`upload` は、platformに対応するバケットへ上げ、`uploaded`（新規保存）または `exists`（同じ保存先に既存）を返します。自分の区画でないバケットへは、キーの権限で拒否されます。`list` は3つのバケットをまとめて、指定runのキー・バケット・role・SHA-256・ファイル名・サイズを一覧表示します。`pull` はそのrunの全区画・全role・全revisionを取得し、既定では次へ保存します。

```text
data/runs/DAILY-2026W47/
  x/collector_x/<sha256>/collection_bundle_x_DAILY-2026W47.json
  threads/collector_threads/<sha256>/collection_bundle_threads_DAILY-2026W47.json
  both/aggregator/<sha256>/...
  _irregular/...（規則外のキー。第5節）
```

保存先を変える場合は `pull --run-id DAILY-2026W47 --output <保存先>` を使います。指定先がそのrunのルートになり、`run_id` は自動で追加されません。指定先はGit管理外の、デスクトップ以外のデータ用フォルダにしてください。指定先より上のフォルダにリンクがあってもかまいません。指定先の中にリンクがある場合は、外へ書き出さないよう取得を止めます。

統合担当は `list` と取得したファイルを確認し、使うrevisionを明示します。共通分類担当へ渡すのは `masked_key` と本文だけです。`post_id` と `platform` は判定後に統合担当が結合し、品質確認・分析を進めます。R2への格納だけで、必要な成果物がそろったことや内容の研究上の妥当性を保証するものではありません。

### 4.1 GrokBotが回収する

集約先はR2です。GrokBot（回収担当）は、R2に集まったX産・Threads産のデータを、読み取り用のキーで取得します。GrokBotはXの収集担当も兼ねるので、Xの区画（`compsns-x`）にだけ書き込めます。

1. 利用者が、第2節の4で「X書き込み用」と「読み取り用」のtokenを作り、GrokBotに渡します。
2. GrokBotの環境に、R2用のライブラリ（`requirements-r2.txt`）と、第3節の環境変数を設定します。キーの値はGrokBotの環境の秘密情報の機能に入れ、プロンプト・会話・ログに書きません。
3. GrokBotは、自分のXの成果物を `--role collector_x` で直接上げます。
4. GrokBotは `list` と `pull` で両SNSのデータを取得します。取得先は、GrokBotの環境の中の、データ用のフォルダにします。

```text
python tools/r2_storage.py list --run-id DAILY-2026W47
python tools/r2_storage.py pull --run-id DAILY-2026W47 --output <GrokBotのデータ用フォルダ>/DAILY-2026W47
```

5. GrokBotは、取得したデータを出力仕様の形に移し、点検し、盲検化した入力を作ります（共通プロトコル第4.2節）。その成果物は両SNSを含むので共通の区画に置くものです。GrokBotには共通の区画の書き込み権限がないため、利用者に渡し、利用者が `--role aggregator` で上げます。

注意：

- GrokBotの環境に置いた写しも、第6節の削除の対象です。
- Threads由来のデータをGrokBotの環境（xAI側）に置いてよいかは、Metaの規約で確かめたうえで利用者が決めます（共通プロトコル第4.2節）。

## 5. 修正版・整合性・保管期限

R2上のキーは次の構造です。

```text
{prefix}/runs/{run_id}/{platform}/{role}/{sha256}/{ファイル名}
```

- **ファイル名**：半角英数字・`.`・`_`・`-` だけの名前は、そのまま使います。それ以外の名前（日本語、空白、括弧、`%` など）は、拒否せずにURLエスケープ（`%E5%8F%8E...` の形）して保存します。拡張子は残します。`list` と `upload` の結果には、元の名前（`original_name`）も表示します。取得したファイルは、エスケープした名前のまま保存します。エスケープ後に255文字を超える名前は、短くしてから上げます。
- **規則外のキー**：ダッシュボードから直接置いたファイルなど、この構造に合わないキーがあっても、そのrunの `list`・`pull` は止まりません。`list` では `irregular: true` と理由を表示し、`pull` では `_irregular/` の下に、キー全体をエスケープした1つのファイル名で保存します（フォルダの外には書き出しません）。規則外のファイルはSHA-256を照合できないため、`verified: false` になります。必要なものは、規則どおりに上げ直し、規則外のキーは第6節の手順で消します。

- 内容のSHA-256が変わった修正版は別のキーで保存し、旧版を上書きしません。`pull` もSHA-256ごとのフォルダを保ち、「最新」を自動で選びません。同じ内容でもファイル名が違えば別キーになります。
- ダウンロード時にSHA-256を照合します。これは転送内容の確認であり、出力仕様・件数・分類・欠測理由等の確認を置き換えません。
- **このCLIの単一ファイル上限は64MiB**です。超える場合は、そのまま送れません。既存プロトコルの分割提出規則に従い、分割方法と不足がないことを記録します。
- `run_id`・prefixの各部分には半角英数字・`.`・`_`・`-` を使います。ファイル名は既存の出力仕様に合わせます。
- R2の保存先（バケット・prefix・run_id）と保管期限をレポートに記録します。保管期限は既存の研究プロトコルと運用上の決定に従って決めます。この導入では自動削除のlifecycle ruleを設定しません。

## 6. 削除（手動）

旧版を上書きしない設計でも、削除の求めには従います。Xの開発者ポリシーは、保存したX ContentをX上の現在の状態に合わせることを求めており、X上で削除・非公開になった投稿は、できるだけ早く、求めがあれば24時間以内に、削除・変更する必要があります（[Xの開発者ポリシー](https://developer.x.com/en/developer-terms/policy/privacy-and-control-are-key)）。Threads由来のデータの扱いは、Metaの規約で確認します。

削除はR2の中で手動で行います。消せるのは、その区画の書き込みキーを持つ人と、利用者です（Xの区画はXの収集担当、Threadsの区画はThreadsの収集担当、共通の区画は利用者だけ）。手順は次のとおりです。

1. **削除する投稿のIDを集めます。** X上で削除・非公開・凍結になった投稿、投稿者やXからの求め。IDは、Xは数字のID、Threadsは数字のIDまたはURLの投稿コード（`/post/` の後ろ）です。1行に1つずつ書いたファイルにしてかまいません。
2. **どのファイルに含まれるかを調べます。** R2は `locate --run-id`、手元とGrokBotの環境の写しは `locate --local` で調べます。`--local` はキーもネットワークも使いません。

   ```powershell
   python tools/r2_storage.py locate --run-id DAILY-2026W47 --ids-file delete_ids.txt
   python tools/r2_storage.py locate --local data/runs/DAILY-2026W47 --ids-file delete_ids.txt
   ```

3. **該当の投稿を除いた版を作り、上げます。** 中身が変わるのでSHA-256が変わり、別の版として保存されます。
4. **旧版を消します。** `delete` は、`--yes` を付けない限り、消す予定のキーとバケットを表示するだけです（読み取り用のキーだけでも確認できます）。指定したキーがすべてそのrunに実在することを確かめてから消します。1つでも合わなければ、何も消しません。同じキーが複数のバケットにある場合は `--bucket` で指定します。

   ```powershell
   python tools/r2_storage.py delete --run-id DAILY-2026W47 --key <locateで出たキー>
   python tools/r2_storage.py delete --run-id DAILY-2026W47 --key <locateで出たキー> --yes
   ```

5. **写しを消します。** 手元の `data/runs/...` と、GrokBotの環境の写しのうち、2で見つかったファイルを消します。
6. **記録します。** いつ、どのIDを、なぜ削除したか、消したキーを記録します。記録には投稿の本文を書きません。分析で使った数値に影響する場合は、共通プロトコルの状態コード（REMOVED）で扱います。

自動化について：削除の対象を見つける作業（1）は、まだ自動化していません。将来は、X APIに接続しているGrokBotが、保存中の投稿IDの状態を定期的に確かめて削除対象の一覧を作る形にできます。Xの区画の削除（2〜6）は、X書き込み用のキーを持つGrokBotでも行えます。Threadsの区画と共通の区画は、それぞれの書き込みキーを持つ人が行います。
- 収集データ・判定結果・認証情報はGitへ追加しません。`.gitignore` だけに頼らず、コミット前に確認します。

## 7. 無料枠と運用費

2026年11月17日に確認したStandardの月間無料枠は、保存 **10GB-month**、Class A（書込・一覧等）**100万回**、Class B（読込等）**1,000万回**です。超過分は保存 **$0.015/GB-month**、A **$4.50/100万回**、B **$0.36/100万回**。R2から直接出る通信のegress料金は無料ですが、保存量・操作回数や併用サービスの費用は別です。

無料枠はStandardにのみ適用されます。Infrequent Accessには読出し料金と最低30日の保存課金があるため、この手順では選びません。使用量には課金単位の切上げがあります。GB-monthは単なるファイル容量の合計ではなく、保存期間を含む指標です。[公式料金表](https://developers.cloudflare.com/r2/pricing/) を有効化時にも確認してください。

Cloudflareの現在のspending limitはWorkersとDurable Objectsが対象で、R2の費用は止めません。アカウント全体のbudget alertは通知のみです。**無料枠で自動停止する前提を置かず**、Billingの使用量を確認し、通知のしきい値を設定して運用します。[公式：費用上限と通知](https://developers.cloudflare.com/billing/manage/spend-management/)、[支払方法の規定](https://developers.cloudflare.com/billing/understand/billing-policy/)
