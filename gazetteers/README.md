# 地名辞典

形式は `TEMPLATE.csv` のヘッダーに従います。項目の意味は `protocol/core.md` 第5.2節を参照してください。

## ファイル名

- 全国：`national-jp.csv`
- 深掘り地域：`region-{地域コード}.csv`（例：`region-JP-26.csv`）

## 列の書き方

| 列 | 内容 |
|---|---|
| term_id | 一意のID。全国は `N-`、深掘りは `R-{地域コード}-` で始める |
| layer | L1〜L4 |
| canonical | 正式表記 |
| reading | 読み（ひらがな） |
| romaji | ローマ字表記 |
| variants_en／variants_zh_hant／variants_zh_hans／variants_ko／variants_other | 他言語・他表記。複数ある場合は `;` で区切る |
| parent_region | 含まれる上位地域の term_id |
| region_code | ISO 3166-2:JP または全国地方公共団体コード |
| ambiguity_flag | true／false |
| decision_rule | 該当／非該当の判定規則 |
| exclude_terms | 除外語。複数ある場合は `;` で区切る |
| added_in_version | 追加した地名辞典の版 |
| status | ACTIVE／PROPOSED／RETIRED（削除せずRETIREDにする） |

## 変更の規則

- 語を削除せず、使わなくなった語は status を RETIRED にします。
- 変更したら地名辞典の版を上げ、CHANGELOG に記録します。
- 日常用では、L4以外の変更は週の境目からだけ反映します。
