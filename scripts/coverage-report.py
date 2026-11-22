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


def shown(value: float, target: float) -> str:
    """表示する % の値。目標との大小が丸めで逆に見えない桁数まで出す（89.96% を 90.0% と表示して目標 90% に届いたと
    見せない）。比較そのものは丸めない値で行う。"""
    for digits in range(1, 16):
        text = f"{value:.{digits}f}"
        if (float(text) < target) == (value < target):
            return text
    return repr(value)


def target_text(value: float) -> str:
    """目標の % を丸めずに表示する（90.5 を 90 や 91 と表示しない）。"""
    return f"{value:.15g}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Package のカバレッジの集計")
    parser.add_argument("report", type=Path)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--target", type=percentage, required=True)
    arguments = parser.parse_args()
    try:
        files = json.loads(arguments.report.read_text(encoding="utf-8"))["data"][0]["files"]
        # 数える対象かは、記録したパスで決める（現在のファイルシステムのシンボリックリンクをたどらない）。swift test の記録は
        # Package の起点を実体のパスに解決し、起点の中のパスはそのまま書く（2026-11-22 に一時的に置いた Package で、/tmp を
        # 経由して指定しても /private/tmp の起点になり、Sources の中のシンボリックリンクのファイルはリンクのパスのまま記録される
        # ことを確かめた）。そこで起点の側だけを実体のパスにし、記録したパスは字面のまま正規化して比べる
        prefix = os.path.realpath(arguments.sources) + os.sep
        lines = []
        for item in files:
            filename = item["filename"]
            if not isinstance(filename, str) or not os.path.isabs(filename):
                raise ValueError(f"記録したパスが絶対パスでない: {filename!r}")
            if not os.path.normpath(filename).startswith(prefix):
                continue
            count, covered = item["summary"]["lines"]["count"], item["summary"]["lines"]["covered"]
            # 整数（bool を除く）で 0 <= covered <= count のものだけを数える。不正な記録を丸めて集計しない
            if type(count) is not int or type(covered) is not int or not 0 <= covered <= count:
                raise ValueError(f"{item['filename']}: 行数の記録が不正（count={count!r}, covered={covered!r}）")
            lines.append((count, covered))
    # 深い入れ子の JSON は RecursionError になる（読めない記録として扱う）
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, KeyError, IndexError, TypeError,
            ValueError) as error:
        print(f"NG: カバレッジの記録を読めない: {error!r}", file=sys.stderr)
        return 2
    total = sum(count for count, _ in lines)
    if total == 0:
        print(f"NG: {arguments.sources} 配下に数える行がない", file=sys.stderr)
        return 2
    percent = 100.0 * sum(covered for _, covered in lines) / total
    target = target_text(arguments.target)
    print(f"カバレッジ（{arguments.sources}）: 行 {shown(percent, arguments.target)}%（{len(lines)} ファイル・目標 {target}%）")
    if percent < arguments.target:
        print(f"警告: カバレッジが目標の {target}% に届いていない（TST-CI-07。初期は警告のみ）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
