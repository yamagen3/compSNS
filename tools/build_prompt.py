#!/usr/bin/env python3
"""共通部分と運用モードの文書を結合し、役割ごとのプロンプトを作る。

使い方:
    python tools/build_prompt.py --mode daily --role collector_x --config config/runs/DAILY-2026W47.json
    python tools/build_prompt.py --mode deep-dive --role integrator --config config/runs/DEEP-JP-26-20261116.json

出力は dist/{mode}/{role}.md です。dist/ はリポジトリに入れません。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODES = {
    "daily": ("DAILY", ROOT / "protocol" / "daily.md"),
    "deep-dive": ("DEEP_DIVE", ROOT / "protocol" / "deep-dive.md"),
}
ROLES = {
    "collector_x": "COLLECTOR_X",
    "collector_threads": "COLLECTOR_THREADS",
    "common_coder": "COMMON_CODER",
    "integrator": "INTEGRATOR",
}
PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
JST = timezone(timedelta(hours=9))


def split_front_matter(text: str) -> tuple[dict[str, str], str]:
    """先頭の --- で囲まれた部分を key: value として読み、本文と分ける。"""
    text = text.replace("\r\n", "\n")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            meta: dict[str, str] = {}
            for line in text[4:end].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = value.strip().strip('"')
            return meta, text[end + 5:].lstrip("\n")
    return {}, text


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def satisfies(requirement: str, version: str) -> bool:
    """'>=4.1.0,<5.0.0' のような条件を満たすか。"""
    ops = {
        ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b,
        ">": lambda a, b: a > b,
        "<": lambda a, b: a < b,
        "==": lambda a, b: a == b,
    }
    for part in filter(None, (p.strip() for p in requirement.split(","))):
        match = re.fullmatch(r"(>=|<=|>|<|==)(\d+\.\d+\.\d+)", part)
        if not match:
            raise ValueError(f"版の条件を読めません: {part}")
        op, target = match.groups()
        if not ops[op](version_tuple(version), version_tuple(target)):
            return False
    return True


def git_state() -> tuple[str | None, bool | None]:
    """HEADの短いコミットIDと、protocol/config/gazetteers に未コミットの変更があるか。"""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", "protocol", "config", "gazetteers"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return commit, bool(status)
    except (OSError, subprocess.CalledProcessError):
        return None, None


def render(value: object) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="共通部分と運用モードを結合し、役割ごとのプロンプトを作る")
    parser.add_argument("--mode", required=True, choices=sorted(MODES))
    parser.add_argument("--role", required=True, choices=sorted(ROLES))
    parser.add_argument("--config", type=Path, help="設定ファイル（config/runs/*.json）")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="出力先のフォルダ（既定: dist）")
    parser.add_argument("--allow-dirty", action="store_true",
                        help="未コミットの変更があっても結合する（試験用。本番では使わない）")
    args = parser.parse_args()

    mode_code, mode_path = MODES[args.mode]
    role_code = ROLES[args.role]

    core_meta, core_body = split_front_matter((ROOT / "protocol" / "core.md").read_text(encoding="utf-8"))
    mode_meta, mode_body = split_front_matter(mode_path.read_text(encoding="utf-8"))

    for name, meta in (("core.md", core_meta), (mode_path.name, mode_meta)):
        for key in ("doc_id", "version"):
            if key not in meta:
                print(f"エラー: {name} の冒頭に {key} がありません。", file=sys.stderr)
                return 1

    requirement = mode_meta.get("requires_core", "")
    if requirement and not satisfies(requirement, core_meta["version"]):
        print(f"エラー: {mode_path.name} は共通部分 {requirement} を必要としますが、"
              f"core.md は {core_meta['version']} です。", file=sys.stderr)
        return 1

    values: dict[str, object] = {}
    config_label = "なし（すべて未設定）"
    if args.config:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if config.get("mode") != mode_code:
            print(f"エラー: 設定ファイルの mode が {config.get('mode')} です（{mode_code} が必要）。", file=sys.stderr)
            return 1
        values.update({k: v for k, v in config.get("placeholders", {}).items() if v is not None})
        try:
            config_label = args.config.resolve().relative_to(ROOT).as_posix()
        except ValueError:
            config_label = args.config.as_posix()
    values["MODE"] = mode_code
    values["ROLE"] = role_code

    commit, dirty = git_state()
    if dirty and not args.allow_dirty:
        print("エラー: protocol/・config/・gazetteers/ に未コミットの変更があります。"
              "コミットしてから結合してください（試験なら --allow-dirty）。", file=sys.stderr)
        return 1
    commit_label = commit or "不明（gitの外で実行）"
    if dirty:
        commit_label += "（未コミットの変更を含む・試験用）"

    header = "\n".join([
        "<!-- このファイルは tools/build_prompt.py が生成したものです。直接編集しないでください。 -->",
        f"# 結合プロンプト：{mode_code}／{role_code}",
        "",
        f"- 共通部分：{core_meta['doc_id']} {core_meta['version']}",
        f"- 運用モード：{mode_meta['doc_id']} {mode_meta['version']}",
        f"- 結合元のコミット：{commit_label}",
        f"- 設定ファイル：{config_label}",
        f"- 生成日時：{datetime.now(JST).isoformat(timespec='seconds')}",
        f"- あなたの役割：{role_code}",
        "",
        f"成果物の metadata には、mode に「{mode_code}」、mode_spec_version に「{mode_meta['version']}」、"
        f"build_commit に「{commit or 'null'}」を記録してください。",
        "",
        "---",
        "",
        "",
    ])
    text = header + core_body.rstrip() + "\n\n---\n\n" + mode_body.rstrip() + "\n"
    text = PLACEHOLDER.sub(lambda m: render(values[m.group(1)]) if m.group(1) in values else m.group(0), text)
    missing = sorted(set(PLACEHOLDER.findall(text)))

    out_path = args.out / args.mode / f"{args.role}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)

    print(f"出力: {out_path}")
    if missing:
        print(f"未設定の項目（{len(missing)}件）: {', '.join(missing)}")
    else:
        print("未設定の項目: なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
