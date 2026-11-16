# R2で収集成果物をまとめて保管する

X・Threadsの提出ファイルを同じ `run_id` に集め、統合担当へ渡すための保存先です。R2はファイルを保管する基盤であり、投稿の分類・分析や重複排除は各担当が既存プロトコルに従って行います。JSON配列を自動で連結する機能はありません。

**現在は接続用コードを準備した段階です。Cloudflareアカウントの変更、R2の有効化・バケット作成・実データの送受信はまだ行っていません。** この手順の導入によって、プロトコルの版・研究設計・出力仕様は変更しません。

## 1. 保存するものと役割

データはGitへ入れず、ローカルでは `data/`、共有用には非公開R2バケットへ保存します。ファイル名・中身は [共通プロトコル第14節](../protocol/core.md#14-出力仕様sns-output-21)、読み比べは [reading.md Y6](../protocol/reading.md#y6-記録) に従います。

| CLIのrole | 保存する成果物の例 |
|---|---|
| `collector_x` | `collection_bundle_x_{run_id}.json`、`collection_report_x_{run_id}.md` |
| `collector_threads` | `collection_bundle_threads_{run_id}.json`、`collection_report_threads_{run_id}.md` |
| `common_coder` | `coded_posts_{run_id}.jsonl`、`coding_report_{run_id}.md` |
| `integrator` | `analysis_bundle_{run_id}.json`、`comparison_summary_{run_id}.csv`、`report_{run_id}.md` |
| `reading` | `reading_log_{run_id}.md`、`reading_posts_{run_id}.csv` |

`reading` は保存先を区別するCLI用のroleです。読み比べの記録は他モードと同じ集計に混ぜません。設定票 `config/runs/{run_id}.json` は従来どおりGitで管理します。

**roleは保存上の分類で、アクセス制御ではありません。** バケット限定キーでも、そのバケットの全roleを読めます。キーは保管・統合担当が管理し、共通分類担当には [共通プロトコル第14.3節](../protocol/core.md#143-coded_posts共通分類担当) に従って `masked_key` と本文だけを渡します。`common_coder` は分類結果を保存する分類であり、生の収集データへのアクセス権を与える意味ではありません。

GrokBot・Museなどには、それぞれの所定の成果物を作ってもらいます。ファイルを作成できない場合は、共通プロトコルの規則に従い、ファイル名付きコードブロック等で受け取り、このPCなどから元の内容を所定ファイルへ保存してアップロードします。AIへR2のキーを渡すことや、AIがR2 APIへ直接書き込めることは前提にしません。データ・キー・生成物をWindowsのデスクトップへ保存しないでください。

## 2. Cloudflareで最初に行うこと

1. Cloudflareにログインし、対象アカウントの **Storage & databases → R2 → Overview** を開きます。
2. R2 subscriptionのcheckoutを完了します。支払方法の登録が必要です。無料枠を超えた分は従量課金され、無料枠の上限で自動停止する仕組みではありません。
3. **Create bucket** から `compsns-data`（提案名）を作成します。ストレージクラスは **Standard** にし、公開設定は有効にしません。`r2.dev`、custom domain、CORSの設定は、このCLIから非公開S3 APIで使う構成には不要です。
4. R2の **API Tokens → Manage → Create User API token** から、自分用のAPI tokenを作成します。権限は **Object Read & Write**、対象は **Apply to specific buckets only** でこのバケットだけに絞ります。
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
| `COMPSNS_R2_BUCKET` | 作成したバケット名（例：`compsns-data`） |
| `COMPSNS_R2_PREFIX` | 任意。未設定なら `compsns/v1` |
| `AWS_ACCESS_KEY_ID` | バケット限定のAccess Key ID |
| `AWS_SECRET_ACCESS_KEY` | 対応するSecret Access Key |

PowerShellで、そのターミナルだけに設定する例です。キーは対話入力し、値をコマンド履歴へ直接書きません。

```powershell
$env:COMPSNS_R2_ACCOUNT_ID = Read-Host "Cloudflare Account ID"
$env:COMPSNS_R2_BUCKET = "compsns-data"
$env:COMPSNS_R2_PREFIX = "compsns/v1"
$env:AWS_ACCESS_KEY_ID = Read-Host "R2 Access Key ID"
$r2Secret = Read-Host "R2 Secret Access Key" -AsSecureString
$env:AWS_SECRET_ACCESS_KEY = [System.Net.NetworkCredential]::new("", $r2Secret).Password
Remove-Variable r2Secret
```

このPowerShellを閉じると、ここで設定した環境変数は失われます。`.env` などのファイルへ自動保存する機能はありません。

## 4. 収集から受け渡しまで

両SNSで同じ `run_id` を指定し、提出ファイルを1個ずつアップロードします。レポートなども、対応するroleと元のファイル名で同じように送ります。

```powershell
python tools/r2_storage.py upload --run-id DAILY-2026W47 --role collector_x --file data/collection_bundle_x_DAILY-2026W47.json
python tools/r2_storage.py upload --run-id DAILY-2026W47 --role collector_threads --file data/collection_bundle_threads_DAILY-2026W47.json
python tools/r2_storage.py list --run-id DAILY-2026W47
python tools/r2_storage.py pull --run-id DAILY-2026W47
```

`upload` は `uploaded`（新規保存）または `exists`（同じ保存先に既存）を返します。`list` は指定runのキー・role・SHA-256・ファイル名・サイズを一覧表示します。`pull` はそのrunの全role・全revisionを取得し、既定では次へ保存します。

```text
data/runs/DAILY-2026W47/
  collector_x/<sha256>/collection_bundle_x_DAILY-2026W47.json
  collector_threads/<sha256>/collection_bundle_threads_DAILY-2026W47.json
```

保存先を変える場合は `pull --run-id DAILY-2026W47 --output <保存先>` を使います。指定先がそのrunのルートになり、`run_id` は自動で追加されません。指定先はGit管理外の、デスクトップ以外のデータ用フォルダにしてください。

統合担当は `list` と取得したファイルを確認し、使うrevisionを明示します。共通分類担当へ渡すのは `masked_key` と本文だけです。`post_id` と `platform` は判定後に統合担当が結合し、品質確認・分析を進めます。R2への格納だけで、必要な成果物がそろったことや内容の研究上の妥当性を保証するものではありません。

## 5. 修正版・整合性・保管期限

R2上のキーは次の構造です。

```text
{prefix}/runs/{run_id}/{role}/{sha256}/{元のファイル名}
```

- 内容のSHA-256が変わった修正版は別のキーで保存し、旧版を上書きしません。`pull` もSHA-256ごとのフォルダを保ち、「最新」を自動で選びません。同じ内容でもファイル名が違えば別キーになります。
- ダウンロード時にSHA-256を照合します。これは転送内容の確認であり、出力仕様・件数・分類・欠測理由等の確認を置き換えません。
- **このCLIの単一ファイル上限は64MiB**です。超える場合は、そのまま送れません。既存プロトコルの分割提出規則に従い、分割方法と不足がないことを記録します。
- `run_id`・ファイル名・prefixの各部分には半角英数字・`.`・`_`・`-` を使います。ファイル名は既存の出力仕様に合わせ、CLIが拒否するパスや予約名を使わないでください。
- R2の保存先（バケット・prefix・run_id）と保管期限をレポートに記録します。保管期限は既存の研究プロトコルと運用上の決定に従って決めます。この導入では自動削除のlifecycle ruleを設定しません。
- 収集データ・判定結果・認証情報はGitへ追加しません。`.gitignore` だけに頼らず、コミット前に確認します。

## 6. 無料枠と運用費

2026年11月17日に確認したStandardの月間無料枠は、保存 **10GB-month**、Class A（書込・一覧等）**100万回**、Class B（読込等）**1,000万回**です。超過分は保存 **$0.015/GB-month**、A **$4.50/100万回**、B **$0.36/100万回**。R2から直接出る通信のegress料金は無料ですが、保存量・操作回数や併用サービスの費用は別です。

無料枠はStandardにのみ適用されます。Infrequent Accessには読出し料金と最低30日の保存課金があるため、この手順では選びません。使用量には課金単位の切上げがあります。GB-monthは単なるファイル容量の合計ではなく、保存期間を含む指標です。[公式料金表](https://developers.cloudflare.com/r2/pricing/) を有効化時にも確認してください。

Cloudflareの現在のspending limitはWorkersとDurable Objectsが対象で、R2の費用は止めません。アカウント全体のbudget alertは通知のみです。**無料枠で自動停止する前提を置かず**、Billingの使用量を確認し、通知のしきい値を設定して運用します。[公式：費用上限と通知](https://developers.cloudflare.com/billing/manage/spend-management/)、[支払方法の規定](https://developers.cloudflare.com/billing/understand/billing-policy/)
