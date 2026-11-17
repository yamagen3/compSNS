#!/usr/bin/env python3
"""時間窓の一覧を、共通シードから作る（core.md 第7.1節）。

使い方:
    python tools/make_windows.py --version PILOT-20261117-v1 --start 2026-11-17 --days 7 \\
        --length-seconds 15 --per-stratum 8 --seed 20261117

層は「日×時間帯」（JSTの 00–04、04–08、08–12、12–16、16–20、20–24）です。
各層を --length-seconds 秒の区画に区切り、その中から --per-stratum 個を重ならないように
無作為に選びます。選んだ窓の半数を発見用（DISCOVERY）、残りを推定用（ESTIMATION）に、
同じシードで無作為に割り当てます。

同じ引数とシードなら、同じ一覧になります。出力は dist/windows/{version}.json です
（dist/ はリポジトリに入れません。引数とシードを設定に記録すれば作り直せます）。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))
BAND_HOURS = 4
BAND_SECONDS = BAND_HOURS * 3600
BANDS = range(0, 24, BAND_HOURS)
RNG_NOTE = (
    "Python の random.Random(seed).random() だけを使う。層は日付順・時間帯順に処理する。"
    "各層で、区画番号 0..n-1 の並びを末尾から Fisher–Yates で入れ替え"
    "（j = int(random() * (i + 1))）、入れ替えた末尾の per_stratum 個を窓とする。"
    "割り当ては、選んだ区画を選ばれた順に並べ、前半を DISCOVERY、後半を ESTIMATION とする。"
)


def pick(rng: random.Random, n: int, k: int) -> list[int]:
    """0..n-1 から k 個を、重複なしで無作為に選ぶ。選ばれた順に返す。"""
    slots = list(range(n))
    chosen = []
    for i in range(n - 1, n - 1 - k, -1):
        j = int(rng.random() * (i + 1))
        slots[i], slots[j] = slots[j], slots[i]
        chosen.append(slots[i])
    return chosen


def build(start: date, days: int, length: int, per_stratum: int, seed: int,
          frame_id: str, platforms: list[str]) -> list[dict]:
    slots_per_band = BAND_SECONDS // length
    rng = random.Random(seed)
    windows = []
    for d in range(days):
        day = start + timedelta(days=d)
        for band in BANDS:
            stratum_id = f"{day:%Y%m%d}-{band:02d}"
            band_start = datetime(day.year, day.month, day.day, band, tzinfo=JST)
            chosen = pick(rng, slots_per_band, per_stratum)
            assignment = {slot: ("DISCOVERY" if n < per_stratum // 2 else "ESTIMATION")
                          for n, slot in enumerate(chosen)}
            for n, slot in enumerate(sorted(chosen), start=1):
                begin = band_start + timedelta(seconds=slot * length)
                windows.append({
                    "window_id": f"{stratum_id}-{n:02d}",
                    "frame_ids": [frame_id],
                    "stratum_id": stratum_id,
                    "start": begin.isoformat(),
                    "end": (begin + timedelta(seconds=length)).isoformat(),
                    "assignment": assignment[slot],
                    "platforms": platforms,
                })
    return windows


def main() -> int:
    parser = argparse.ArgumentParser(description="時間窓の一覧を、共通シードから作る")
    parser.add_argument("--version", required=True, help="窓一覧の版（WINDOW_LIST_VERSION。出力ファイル名にも使う）")
    parser.add_argument("--start", required=True, help="最初の日（JST、YYYY-MM-DD）")
    parser.add_argument("--days", required=True, type=int, help="日数")
    parser.add_argument("--length-seconds", required=True, type=int, help="窓の長さ（秒）")
    parser.add_argument("--per-stratum", required=True, type=int, help="層ごとの窓の数（偶数）")
    parser.add_argument("--seed", required=True, type=int, help="乱数シード（SAMPLING_SEED）")
    parser.add_argument("--frame", default="NATIONAL_JP", help="frame_id（既定: NATIONAL_JP）")
    parser.add_argument("--platforms", default="x,threads", help="対象SNS（カンマ区切り。既定: x,threads）")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "windows", help="出力先のフォルダ（既定: dist/windows）")
    args = parser.parse_args()

    try:
        start = date.fromisoformat(args.start)
    except ValueError:
        print(f"エラー: --start は YYYY-MM-DD で指定してください（{args.start}）。", file=sys.stderr)
        return 1
    if args.days < 1:
        print("エラー: --days は1以上にしてください。", file=sys.stderr)
        return 1
    if args.length_seconds < 1 or BAND_SECONDS % args.length_seconds:
        print(f"エラー: --length-seconds は {BAND_SECONDS} 秒（4時間）を割り切れる値にしてください。", file=sys.stderr)
        return 1
    if args.per_stratum < 2 or args.per_stratum % 2:
        print("エラー: --per-stratum は2以上の偶数にしてください（発見用と推定用に半分ずつ分けるため）。", file=sys.stderr)
        return 1
    slots_per_band = BAND_SECONDS // args.length_seconds
    if args.per_stratum > slots_per_band:
        print(f"エラー: --per-stratum が層の区画数（{slots_per_band}）を超えています。", file=sys.stderr)
        return 1
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    if not platforms:
        print("エラー: --platforms が空です。", file=sys.stderr)
        return 1

    windows = build(start, args.days, args.length_seconds, args.per_stratum, args.seed, args.frame, platforms)
    design_id = f"STRAT-DAYx6-L{args.length_seconds}S-M{args.per_stratum}"
    end_day = start + timedelta(days=args.days)
    result = {
        "metadata": {
            "window_list_version": args.version,
            "sampling_design_id": design_id,
            "sampling_seed": args.seed,
            "frame_id": args.frame,
            "platforms": platforms,
            "period_start": datetime(start.year, start.month, start.day, tzinfo=JST).isoformat(),
            "period_end": datetime(end_day.year, end_day.month, end_day.day, tzinfo=JST).isoformat(),
            "strata": "日×時間帯（JST 00–04、04–08、08–12、12–16、16–20、20–24）",
            "window_length_seconds": args.length_seconds,
            "windows_per_stratum": args.per_stratum,
            "sampling_fraction_all_windows": args.per_stratum * args.length_seconds / BAND_SECONDS,
            "rng": RNG_NOTE,
            "windows_total": len(windows),
        },
        "windows": windows,
    }

    args.out.mkdir(parents=True, exist_ok=True)
    out_path = args.out / f"{args.version}.json"
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    per_day_seconds = len(BANDS) * args.per_stratum * args.length_seconds
    print(f"出力: {out_path}")
    print(f"窓の数: {len(windows)}（1日 {len(BANDS) * args.per_stratum} 窓・{per_day_seconds} 秒、半数が推定用）")
    print(f"設定に記録する値: SAMPLING_DESIGN_ID={design_id}、SAMPLING_SEED={args.seed}、WINDOW_LIST_VERSION={args.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
