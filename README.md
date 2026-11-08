# SNS話題比較プロトコル

同じ期間に、同じ地域について、XとThreadsでそれぞれ何が話題になったかを比べる調査のプロトコル・設定・地名辞典を管理するリポジトリです。調査で集めたデータは入れません（後述）。

## 構成

```
protocol/
  core.md          共通部分（全モード共通。目的、役割、地域フレーム、標本設計、分類、推定・比較、品質、出力仕様）
  daily.md         日常用（全国 NATIONAL_JP を週単位で継続観測）
  deep-dive.md     深掘り用（指定地域・期間限定）
config/
  daily.example.json       日常用の設定ひな型
  deep-dive.example.json   深掘り用の設定ひな型
  runs/                    実際に使った設定（run_id ごとに1ファイル）
gazetteers/
  TEMPLATE.csv     地名辞典のひな型
  README.md        地名辞典の書き方
tools/
  build_prompt.py  共通部分＋運用モード＋役割を、1つのプロンプトに結合する
  check.py         文書と設定ひな型の整合性を点検する
docs/
  research-plan.md 調査計画 v1.0（v3時点）
CHANGELOG.md       変更履歴
```

共通部分は `protocol/core.md` の1ファイルだけです。日常用と深掘り用は、それぞれ別のファイルで管理し、使うときに共通部分と結合します。

## 使い方

Python 3.8以上が必要です（追加のライブラリは不要）。Windowsでは `python` を `py` に読み替えてください。

1. **設定を作る**：ひな型をコピーし、値を入れて `config/runs/` に置きます。
   - 日常用：`config/runs/DAILY-2026W47.json`
   - 深掘り用：`config/runs/DEEP-JP-26-20261116.json`
2. **点検する**：`python tools/check.py`
3. **コミットする**：設定と文書をコミットします。深掘り用では、このコミットが事前登録の記録になります（データを見る前に行う）。
4. **結合する**：役割ごとにプロンプトを作ります。

   ```
   python tools/build_prompt.py --mode daily --role collector_x --config config/runs/DAILY-2026W47.json
   ```

   `dist/daily/collector_x.md` ができます。冒頭に共通部分・運用モードの版と、結合元のコミットIDが入ります。未コミットの変更があると結合しません（試験だけ `--allow-dirty`）。
5. **渡す**：結合したファイルを各担当に渡します。

| 役割（--role） | 担当（仮置き） | 渡すファイル |
|---|---|---|
| collector_x | GrokBot | dist/{mode}/collector_x.md |
| collector_threads | Muse | dist/{mode}/collector_threads.md |
| common_coder | 第三の実行者を推奨 | dist/{mode}/common_coder.md |
| integrator | 人または第三の実行者 | dist/{mode}/integrator.md |

## 版の管理

各文書の冒頭（`---` で囲んだ部分）に版があります。

- 共通部分 `core.md`：4.x.y（protocol_id は SNS-COMPARE-4.x）
- 日常用 `daily.md`・深掘り用 `deep-dive.md`：1.x.y。冒頭の `requires_core` で、組み合わせられる共通部分の版を指定します。

版の上げ方：

| 種類 | 例 | 上げる番号 |
|---|---|---|
| 過去の結果と比べられなくなる変更 | 推定対象、フレームの定義、判定規則、標本設計の変更 | 1番目（メジャー） |
| 過去の結果と比べられる追加 | 項目・節・感度分析の追加 | 2番目（マイナー） |
| 表現の修正 | 誤字、言い回し | 3番目（パッチ） |

変更の流れ：

1. ブランチを作る（例：`git switch -c change/xxx`）
2. 文書を変更し、`python tools/check.py` を通す
3. 冒頭の版と `CHANGELOG.md` を更新する
4. main にマージし、タグを付ける（文書セット全体は `vX.Y.Z`、共通部分の版に合わせる）
5. 深掘りの終了時は `deep/{run_id}` のタグを付ける

運用中の注意：

- 日常用では、地名辞典（L4を除く）・コードブック・判定規則・標本設計の変更は、週の境目からだけ反映します（daily.md D3）。
- 調査に使うプロンプトは、必ずコミット済みの状態から結合します。成果物の metadata に build_commit が残るので、どの版で実行したかを後から確かめられます。

## リポジトリに入れないもの

収集した投稿・本文・判定結果・成果物のデータ（collection_bundle、coded_posts、analysis_bundle など）、認証情報・APIキーは入れません。個人情報を含み、履歴から消すのが難しいためです。別の保存先に置き、保存先と保管期限をレポートに書きます。`.gitignore` で主なものを除外していますが、コミット前に確認してください。

## 履歴と関連資料

- v3.0.0：v3時点の資料の取り込み
- v4.0.0：地域フレーム版（一体版）
- v4.1.0：共通部分・日常用・深掘り用に分割

`git log` と `git diff v3.0.0 v4.0.0` で変更を追えます。`docs/research-plan.md` は v3時点の計画です。言語・地域・話題の発見方法は v4 で置き換えています。

元の作業フォルダにある次のファイルは取り込んでいません：grokbot-x-prompt-v2/v3.md、muse-threads-prompt-v2/v3.md、sns-common-research-prompt-v2.md、grokbot-muse-research-briefs.md、external-input-file-proposal.md。
