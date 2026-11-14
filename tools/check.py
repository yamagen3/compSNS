#!/usr/bin/env python3
"""プロトコル文書と設定ひな型の整合性を点検する。

使い方:
    python tools/check.py

点検すること:
  1. 各文書の冒頭（doc_id・version）と、運用モードが要求する共通部分の版
  2. 文書中の {{...}} が、各モードの設定ひな型にすべてあるか（ひな型にあって文書にない項目は警告）
  3. Markdownの表の列数がそろっているか
  4. 節の参照（第x.y節、共通部分第x.y節、D1、K1、付録A など）の行き先があるか
  5. comparison_summary のCSVヘッダーに重複がないか
  6. コードブロックの ``` が閉じているか
  7. 読み比べ用（protocol/reading.md）の冒頭、設定ひな型、表、節の参照、コードブロック

エラーがあれば終了コード1を返します。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_prompt import MODES, PLACEHOLDER, ROOT, satisfies, split_front_matter  # noqa: E402

CONFIGS = {
    "DAILY": ROOT / "config" / "daily.example.json",
    "DEEP_DIVE": ROOT / "config" / "deep-dive.example.json",
}
BUILTIN = {"MODE", "ROLE"}

# 読み比べ用は共通部分と結合しない単独の文書（build_prompt.py の対象外）
READING_DOC = ROOT / "protocol" / "reading.md"
READING_CONFIG = ROOT / "config" / "reading.example.json"
READING_PER_QUERY = {"SNS_NAME", "THEME"}  # 問いごとに入れる項目（設定ひな型には置かない）

errors: list[str] = []
warnings: list[str] = []


def load(path: Path) -> tuple[dict[str, str], str]:
    return split_front_matter(path.read_text(encoding="utf-8"))


def strip_code(text: str) -> str:
    """コードブロックとインラインコードを除いた本文（参照・表の点検用）。"""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", "", text)


def core_sections(body: str) -> set[str]:
    nums = set(re.findall(r"^#{2,3} (\d+(?:\.\d+)?)[.\s]", body, flags=re.M))
    nums |= {f"付録{a}" for a in re.findall(r"^## 付録([A-Z])", body, flags=re.M)}
    return nums


def mode_sections(body: str) -> set[str]:
    return set(re.findall(r"^## ([DKY]\d+)\.", body, flags=re.M))


def check_tables(name: str, body: str) -> None:
    rows: list[str] = []
    for line in strip_code(body).splitlines() + [""]:
        if line.startswith("|"):
            rows.append(line)
            continue
        if rows:
            counts = {row.replace("\\|", "").count("|") for row in rows}
            if len(counts) > 1:
                errors.append(f"{name}: 表の列数がそろっていません（先頭行: {rows[0][:40]}…）")
            rows = []


def main() -> int:
    core_meta, core_body = load(ROOT / "protocol" / "core.md")
    mode_docs = {code: load(path) for code, path in MODES.values()}
    daily_body = mode_docs["DAILY"][1]
    deep_body = mode_docs["DEEP_DIVE"][1]
    reading_meta, reading_body = load(READING_DOC) if READING_DOC.exists() else ({}, "")

    # 1. 冒頭と版
    for name, meta in [("core.md", core_meta)] + [(MODES[k][1].name, mode_docs[MODES[k][0]][0]) for k in MODES]:
        for key in ("doc_id", "version"):
            if key not in meta:
                errors.append(f"{name}: 冒頭に {key} がありません")
    for code, (meta, _) in mode_docs.items():
        req = meta.get("requires_core")
        if req and "version" in core_meta and not satisfies(req, core_meta["version"]):
            errors.append(f"{code}: 共通部分 {req} が必要ですが core.md は {core_meta['version']} です")

    # 2. プレースホルダと設定ひな型
    core_ph = set(PLACEHOLDER.findall(core_body))
    for code, (_, body) in mode_docs.items():
        needed = (core_ph | set(PLACEHOLDER.findall(body))) - BUILTIN
        config = json.loads(CONFIGS[code].read_text(encoding="utf-8"))
        if config.get("mode") != code:
            errors.append(f"{CONFIGS[code].name}: mode が {config.get('mode')} です（{code} が必要）")
        keys = set(config.get("placeholders", {}))
        for key in sorted(needed - keys):
            errors.append(f"{CONFIGS[code].name}: {{{{{key}}}}} が placeholders にありません")
        for key in sorted(keys - needed):
            warnings.append(f"{CONFIGS[code].name}: {key} は文書で使われていません")

    if reading_body:
        for key in ("doc_id", "version"):
            if key not in reading_meta:
                errors.append(f"reading.md: 冒頭に {key} がありません")
        needed = set(PLACEHOLDER.findall(reading_body)) - READING_PER_QUERY
        if not READING_CONFIG.exists():
            errors.append(f"{READING_CONFIG.name} がありません")
        else:
            config = json.loads(READING_CONFIG.read_text(encoding="utf-8"))
            if config.get("mode") != "READING":
                errors.append(f"{READING_CONFIG.name}: mode が {config.get('mode')} です（READING が必要）")
            keys = set(config.get("placeholders", {}))
            for key in sorted(needed - keys):
                errors.append(f"{READING_CONFIG.name}: {{{{{key}}}}} が placeholders にありません")
            for key in sorted(keys - needed):
                warnings.append(f"{READING_CONFIG.name}: {key} は文書で使われていません")

    # 3. 表
    check_tables("core.md", core_body)
    check_tables("daily.md", daily_body)
    check_tables("deep-dive.md", deep_body)
    if reading_body:
        check_tables("reading.md", reading_body)
    for extra in [ROOT / "README.md", ROOT / "gazetteers" / "README.md"]:
        if extra.exists():
            check_tables(extra.relative_to(ROOT).as_posix(), extra.read_text(encoding="utf-8"))

    # 4. 節の参照
    core_secs = core_sections(core_body)
    mode_secs = mode_sections(daily_body) | mode_sections(deep_body) | mode_sections(reading_body)
    targets = [("core.md", core_body, True), ("daily.md", daily_body, False), ("deep-dive.md", deep_body, False)]
    if reading_body:
        targets.append(("reading.md", reading_body, False))
    for name, body, is_core in targets:
        text = strip_code(body)
        refs = re.findall(r"(?:共通部分)?第(\d+(?:\.\d+)?)節", text) if is_core else \
            re.findall(r"共通部分第(\d+(?:\.\d+)?)節", text)
        refs += [f"付録{a}" for a in re.findall(r"付録([A-Z])", text)]
        for ref in refs:
            if ref not in core_secs:
                errors.append(f"{name}: 参照先「{ref}」が core.md にありません")
        if not is_core:
            if re.search(r"(?<!共通部分)第\d+(?:\.\d+)?節", text):
                errors.append(f"{name}: 共通部分の節は「共通部分第x節」と書いてください")
        for ref in re.findall(r"(?<![A-Za-z0-9_])([DKY]\d+)(?![0-9])", text):
            if ref not in mode_secs:
                errors.append(f"{name}: 参照先「{ref}」が運用モードの文書にありません")

    # 5. CSVヘッダー
    match = re.search(r"```csv\n(.+?)\n```", core_body, flags=re.S)
    if not match:
        errors.append("core.md: comparison_summary のCSVヘッダーが見つかりません")
    else:
        cols = match.group(1).strip().split(",")
        dupes = sorted({c for c in cols if cols.count(c) > 1})
        if dupes:
            errors.append(f"core.md: CSVの列名が重複しています: {', '.join(dupes)}")
        print(f"comparison_summary の列数: {len(cols)}")

    # 6. コードブロック
    for name, body in [("core.md", core_body), ("daily.md", daily_body), ("deep-dive.md", deep_body),
                       ("reading.md", reading_body)]:
        if body.count("```") % 2:
            errors.append(f"{name}: ``` が閉じていません")

    for w in warnings:
        print(f"警告: {w}")
    for e in errors:
        print(f"エラー: {e}")
    print(f"点検結果: エラー {len(errors)}件、警告 {len(warnings)}件")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
