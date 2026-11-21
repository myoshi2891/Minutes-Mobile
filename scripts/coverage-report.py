#!/usr/bin/python3
"""Package のカバレッジを集計する（TST-CI-07）。初期は警告だけで、失敗にしない。

swift test --enable-code-coverage が出す JSON から、指定したディレクトリ配下のファイルだけを数える
（合計にはテストコードと自動生成のファイルが混ざるため）。

使い方: scripts/coverage-report.py <codecov の JSON> --sources <数える対象のディレクトリ> --target <目標の %>
終了コード: 0 = 集計できた（目標未満は警告を出す） / 2 = 読めない・対象のファイルがない
"""
import argparse
import json
import math
import os
import sys
from pathlib import Path


def percentage(text: str) -> float:
    """目標の %。有限の数で 0〜100 だけを認める（NaN・無限大・範囲外の値で比較を壊さない）。"""
    try:
        value = float(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"数でない: {text}") from error
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise argparse.ArgumentTypeError(f"0〜100 の有限の数でない: {text}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Package のカバレッジの集計")
    parser.add_argument("report", type=Path)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--target", type=percentage, required=True)
    arguments = parser.parse_args()
    try:
        files = json.loads(arguments.report.read_text(encoding="utf-8"))["data"][0]["files"]
        prefix = os.path.realpath(arguments.sources) + os.sep
        lines = []
        for item in files:
            if not os.path.realpath(item["filename"]).startswith(prefix):
                continue
            count, covered = item["summary"]["lines"]["count"], item["summary"]["lines"]["covered"]
            # 整数（bool を除く）で 0 <= covered <= count のものだけを数える。不正な記録を丸めて集計しない
            if type(count) is not int or type(covered) is not int or not 0 <= covered <= count:
                raise ValueError(f"{item['filename']}: 行数の記録が不正（count={count!r}, covered={covered!r}）")
            lines.append((count, covered))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as error:
        print(f"NG: カバレッジの記録を読めない: {error!r}", file=sys.stderr)
        return 2
    total = sum(count for count, _ in lines)
    if total == 0:
        print(f"NG: {arguments.sources} 配下に数える行がない", file=sys.stderr)
        return 2
    percent = 100.0 * sum(covered for _, covered in lines) / total
    print(f"カバレッジ（{arguments.sources}）: 行 {percent:.1f}%（{len(lines)} ファイル・目標 {arguments.target:.0f}%）")
    if percent < arguments.target:
        print(f"警告: カバレッジが目標の {arguments.target:.0f}% に届いていない（TST-CI-07。初期は警告のみ）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
