# SNS比較調査・共通アウトプット仕様 v1.0

以下を、X担当・Threads担当の双方に同じ条件で指定する。これは出力のひな型であり、調査結果ではない。

## 9. 出力の指定

### 9.1 成果物を三つに固定する

提出物は、読むためのレポート、比較用の一覧データ、再集計用の元データの三つです。本文へ全ログを並べないでください。

1. report_{platform}_{run_id}.md：日次・週次レポート。
2. topic_summary_{platform}_{run_id}.csv：日／週×話題の比較用一覧。
3. research_bundle_{platform}_{run_id}.json：メタデータ、元投稿、測定値、証拠、観測枠、検索ログをまとめたデータ。

platformのファイル名表記はxまたはthreadsに統一します。本文のSNS名はXまたはThreadsとします。成果物の本文・記述は日本語、スキーマのキー名は本仕様の英語表記に固定します。

output_spec_versionの値はSNS-OUTPUT-1.0とします。

保存先が指定されている場合はその出力フォルダを使い、Windows Desktopには保存しないでください。ファイルを作成できない場合は、架空の添付・パス・ダウンロードリンクを作らず、同じファイル名を付けたコードブロックで提出してください。大量の場合は分割し、未出力のデータ・行数・再集計への影響を明示します。話題やログを黙って省略しないでください。

### 9.2 日次・週次レポートの固定構成

次の順序と項目を守ってください。対象外の項目も見出しを残し「未実施／適用外／取得不能」と理由を書きます。

【1：調査ヘッダー】

対象SNS／対象期間とJST表記／実行段階／調査方式／取得日時／プロトコル・設定票・取得条件票・辞書の版／観測範囲／完了・未完了。

DISCOVERの場合は「独立探索・比較前」、VERIFYの場合は「共通候補の検証結果」と明記します。SNS全体を網羅したと誤解される表題は付けません。

【2：要約】

全体300字以内、最大3点。確認できた主な話題、語られ方、最大の制約を含めます。対象SNSの観測事実だけを述べ、他SNSとの違いや優劣を推測しません。未検証段階で「その日の最大トレンド」などと書きません。

【3：日次の話題一覧】

日付は古い順。各日最大10件を本文に表示し、候補全件はCSVとJSONへ残します。10件に満たなくても埋め合わせません。表の直前に、候補総数・本文掲載数・抜粋規則を明記します。

列を次の順に固定します。

表示No.｜話題ID｜出来事｜分野｜注目の根拠A/B/C｜主要指標と観測範囲｜主な語られ方｜根拠ID｜制約

出来事名は原則40字以内、語られ方は原則60字以内に要約します。省略で意味が変わる場合は短さより正確さを優先します。数値は単位・対象範囲とセットで記載し、詳細な測定条件は添付データへ接続します。

DISCOVERでは「候補一覧」と表記し、順位欄を作りません。VERIFYでも完全な順位を作れる条件を満たさなければ「順位未確定」とします。表示No.は行番号であり人気順位ではありません。

【4：週次の話題一覧】

週の開始日は古い順。1週あたり最大10件を表示し、基本列は日次と同一にします。週全体の重複除去後の規模、ピーク日、投稿検出日数、共通閾値による注目継続日数を、取得できたものだけ補足します。週次は元投稿から再集計し、日次順位の平均や日次投稿者数の合計を使いません。

対象期間の開始・終了で切れた週はPARTIAL_PERIODと明記し、観測した期間を表示します。完全な週と同条件の順位比較をしません。単なる投稿検出日数を、注目が続いた日数と呼びません。

【5：主要話題の説明】

対象期間全体で最大3件、各150字程度を目安にします。各件を「確認した事実／投稿に見られた切り口／解釈と限界／根拠ID」に分けます。説明対象の選定理由も示します。文字数を満たすための説明は足しません。

【6：品質・欠測】

予定枠数／完全取得枠数／一部取得・失敗枠数／取得上限に達した候補数／未処理候補数／比較に使える指標／使えない指標／分類に迷った箇所を表で示します。不明な件数は推測せず不明と記載します。

【7：添付と次の段階】

成果物名、実際に出力したレコード数、出力状態（COMPLETE／PARTIAL／NOT_PRODUCED）、未出力範囲を示します。DIAGNOSEでは診断と条件の提案、DISCOVERでは統合担当へ渡す候補・検索語、VERIFYでは未解決事項と監査対象を提出します。

### 9.3 並び順と抜粋を統一する

日次・週次とも、条件票に順位の定義があり、その期間・候補集合で確定順位を作れる場合だけ、その順位で表示します。同順位は出来事名、話題IDの順で並べます。

順位がない場合は、共通分類辞書の分野順→出来事名のUnicodeコードポイント順→話題IDの順とします。基準は閲覧数やAIの印象へ勝手に変更しません。「先頭10件の抜粋」と明記し、人気上位10件の意味を持たせません。

主要話題3件は、完全順位がある場合は先頭3件、ない場合は本文掲載候補のうち異なる分野を固定順に優先して選びます。同じ候補が複数日に出る場合は、説明では一つにまとめます。これは説明対象の選定であり人気の判定ではありません。

### 9.4 比較用CSVの固定仕様

1行は、対象SNS×実行段階×集計期間×話題IDです。日次と週次はgrainで区別します。DISCOVERの仮IDは統合前と明示し、別SNSの仮IDと同じ出来事だと扱いません。

以下のヘッダー名・順序を変更しないでください。

```csv
protocol_id,output_spec_version,config_version,access_spec_version,dictionary_version,run_id,platform,phase,design,grain,period_start,period_end,period_completeness,topic_id,category,event,evidence_types,display_order,sns_rank,rank_basis,rank_status,posts_observed,authors_observed,official_listed_slots,official_valid_slots,official_listing_rate,baseline_ratio,posting_days_observed,attention_days,peak_date,main_angle,evidence_ids,scope_id,metric_statuses,comparability_notes
```

- grain：DAYまたはWEEK。期間はJSTの半開区間。
- period_completeness：FULL_PERIODまたはPARTIAL_PERIOD。
- display_order：その日／週の表示順。sns_rankとは別。
- sns_rank：rank_statusがCONFIRMEDの順位だけ数値。それ以外は空欄とし、部分的な順序情報があればcomparability_notesへ記載。
- rank_status：CONFIRMED／PARTIAL／NOT_ESTIMABLE／NOT_APPLICABLE。部分順位を完全順位に見せない。
- official_listing_rate：0～1の小数。百分率表記はレポート内だけ。
- peak_date：共通定義と根拠がある場合だけYYYY-MM-DD。
- evidence_ids：根拠IDのJSON配列。CSVの引用符規則に従って一つのフィールドに入れる。
- evidence_types：A・B・Cのうち確認できた根拠区分のJSON配列。複数該当を認め、根拠がない場合は[]。
- scope_id：共通取得条件票で定義した観測範囲ID。範囲不明なら空欄とし、比較不能の理由を記載。
- metric_statuses：各指標名をキー、{status, reason, value_kind, scope_id, statistic}を値とするJSONオブジェクト。全ての数値指標について状態、下限値等の性質、測定範囲、集計方法を残す。投稿者識別子の欠測等で下限になった場合もLOWER_BOUNDとする。空欄の理由を比較担当が判別できるようにする。

数値は単位記号・桁区切り・「約」「以上」を混ぜず数値として保存します。下限値などの性質はJSON側のvalue_kindで表現し、レポートでは「少なくとも」と書きます。欠測はCSVで空欄、JSONでnullとし、ゼロやハイフンで埋めません。文字コードはUTF-8、CSVはカンマ・改行・引用符を適切にエスケープします。

### 9.5 再集計用JSONの固定構造

正本はJSONとし、トップレベルを以下に固定します。該当レコードがない配列は[]、未実施の理由はmetadata.output_manifestとpendingに記録します。配列が空であることを「観測範囲内でゼロだった」と解釈させないでください。

metadata／capabilities／topics／topic_summaries／topic_metrics／posts／post_metrics／evidence／observation_slots／official_listings／search_log／pending

metadataには、protocol_id、output_spec_version、config_version、access_spec_version、dictionary_version、run_id、platform、phase、design、period_start、period_end、retrieved_at、report_scope、config_snapshot、scope_definitions、output_manifestを含めます。config_snapshotには認証情報を除く実際の設定票・取得条件・予算・指標定義を、scope_definitionsにはscope_idごとの取得範囲を残します。版番号だけでなく再集計に必要な条件そのものを保存します。

topic_summariesは比較用CSVの全列を同じキー名で保持したオブジェクトの配列です。数値・配列・オブジェクトはJSONの型を保ち、欠測はnullとします。この配列からCSVを出力し、JSON正本から表示順・順位の状態・部分期間・語られ方も復元できるようにしてください。

各配列レコードにもrun_idとplatformを付けます。主要な項目は次のとおりです。

| 配列 | 必須項目（run_id・platformに追加） |
|---|---|
| capabilities | item、status、method、limitations、evidence |
| topics | topic_id、category、event、aliases、query_groups、exclude_terms、decision_rule、id_status、evidence_types |
| topic_metrics | grain、period_start、period_end、topic_id、metric_name、statistic、value、unit、numerator、denominator、scope_id、observed_at、status、reason、value_kind、evidence_ids |
| posts | post_id、author_key、primary_topic_id、secondary_topic_ids、published_at、retrieved_at、post_type、speech_form、stance、flags、scope_ids、query_ids、evidence_ids |
| post_metrics | post_id、metric_name、value、unit、metric_observed_at、age_hours、scope_id、status、reason、value_kind、evidence_ids |
| evidence | evidence_id、topic_ids、post_id、url、published_at、retrieved_at、post_type、verified_fact、verification_method |
| observation_slots | slot_id、planned_at、retrieved_at、retrieval_status、scope_id、listed_topic_ids、evidence_ids、reason |
| official_listings | slot_id、topic_id、listed、status、evidence_ids |
| search_log | query_id、conceptual_query_id、actual_query、period_start、period_end、surface、sort_order、retrieved_at、pages_observed、records_observed、completion_status、reason |
| pending | item、affected_topic_ids、status、reason、required_input、impact |

topic_idはDISCOVERで仮ID、VERIFYで配布された共通IDを使い、id_statusをPROVISIONALまたはCOMMONとします。共通辞書を変更した場合、過去のIDを黙って付け替えません。

author_keyは同じSNS内で期間をまたいで一貫する識別子を使い、不明ならnullです。SNSをまたぐ同一人物推定はしません。postsの同一取得実行内での重複キーはrun_id＋platform＋post_id。期間をまたいだ再集計ではplatform＋post_idを使い、投稿者数はplatform＋author_keyで重複を除きます。author_key欠測による投稿者数の限界も示します。

post_metricsは同じ投稿・指標でも計測時刻が違えば別レコードにします。過去のスナップショットを現在値で上書きしません。日時はオフセット付きISO 8601、数値は数値型、真偽値は真偽値、配列は配列とします。

value_kindはEXACT_WITHIN_SCOPE／LOWER_BOUND／ESTIMATE／UNAVAILABLE。ESTIMATEは推定方法・仮定・重みを説明できる場合だけです。EXACT_WITHIN_SCOPEでもSNS全体の全数を意味しません。既存のOBSERVED等のstatusと別に持ちます。

observation_slots.retrieval_statusはCOMPLETE／PARTIAL／FAILED／UNAVAILABLE。official_listings.listedは1／0／nullとし、取得失敗や一部取得で未確認の話題を非掲載の0にしません。主たる掲載率の分母は、定義した範囲の完全取得枠だけです。

topic_metricsのmetric_nameは、posts_observed、authors_observed、official_listed_slots、official_valid_slots、official_listing_rate、baseline_ratio、posting_days_observed、attention_days、peak_date、sns_rank、likes、replies、quotes、reposts、viewsの該当するものを使います。ピーク日以外の測定値は数値またはnullです。posting_days_observedとattention_daysを混同せず、共通の算式・閾値がない指標は欠測理由を残します。

metric_statusesに使うstatusは共通プロンプトのコードに従います。本文の「順位未確定」等の説明を、数値0へ置き換えません。能力・検索完了・順位のstatusはそれぞれの定義に従います。

### 9.6 段階ごとの出力範囲

DIAGNOSE：レポートのヘッダー、能力診断、共通条件の提案、必要な入力を中心に提出。topic_summaryはヘッダーのみ、JSONの未実施配列は[]とし、NOT_COLLECTEDの理由を残します。

DISCOVER：候補一覧と根拠、実施した検索ログ、取得できた投稿等を提出。sns_rankは空欄、rank_statusはNOT_APPLICABLE。週全体を調べた場合も、人気週順位は作りません。

VERIFY：共通候補の全件をCSV・JSONに含めます。未調査や取得不能の候補も消しません。本文は指定した抜粋規則で表示し、順位を作れない場合はそのまま示します。

チャット上の回答は、ファイルを提出できた場合は1000字以内を目安にし、対象期間、主な観測事実、最大の制約、三つの成果物への実在する参照を示してください。ファイルの本文へは、対象期間の全日・全週の必要な表を残します。

### 9.7 統合担当に渡すための条件

X担当・Threads担当は同じ列・単位・分類で提出します。SNS別レポートに相手SNSの未提供データを埋めません。

統合担当は、両者の期間・設定・辞書・観測範囲・指標定義を照合した後、次の表を作成できます。これは各担当が片方のデータだけで作る表ではありません。

期間｜共通話題ID｜出来事｜Xの根拠・指標｜Threadsの根拠・指標｜語られ方の違い｜共通枠での比較可否｜欠測・制約｜双方の根拠ID

「両方で注目」「Xで相対的に目立つ」「Threadsで相対的に目立つ」の判定は、事前の共通基準と比較可能な証拠がある場合だけです。条件が足りなければ「両方で検出」「片方のみ検出」「比較不能」までにとどめます。

### 9.8 出力の整合性確認

提出前に、本文・CSV・JSONの期間、話題ID、数値、欠測理由が一致しているか確認してください。根拠IDや投稿IDを追跡できるか、週次で重複計上していないか、表示No.を人気順位へ読み替えていないか、未出力データが明示されているかも確認します。

この確認はデータと表現の整合性の確認です。検索外のデータを網羅したことや、SNS全体の代表性を保証するものではありません。
