#!/usr/bin/python3
"""Package のカバレッジを集計する（TST-CI-07）。初期は警告だけで、失敗にしない。

swift test --enable-code-coverage が出す JSON から、指定したディレクトリ配下のファイルだけを数える
（合計にはテストコードと自動生成のファイルが混ざるため）。

使い方: scripts/coverage-report.py <codecov の JSON> --sources <数える対象のディレクトリ> --target <目標の %>
  --measured-sources <絶対パス>  --sources の代わりに、測ったときに記録した実体のパスを渡す（現在のファイルシステムで解決しない。
                                 測った後に作業ツリーを移しても、記録したパスのまま数える）
終了コード: 0 = 集計できた（目標未満は警告を出す） / 2 = 読めない・対象のファイルがない
"""
import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

from xcproj import reject_duplicate_keys

TARGET_DIGITS = 30  # 目標の小数の桁数の上限（1e-999999999 のような値を正確な分数に展開させない）


def percentage(text: str) -> Decimal:
    """目標の %。有限の 10 進数で 0〜100 だけを認める（NaN・無限大・範囲外の値で比較を壊さない）。"""
    try:
        value = Decimal(text)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError(f"数でない: {text}") from error
    if not value.is_finite() or not 0 <= value <= 100:
        raise argparse.ArgumentTypeError(f"0〜100 の有限の数でない: {text}")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -TARGET_DIGITS:
        raise argparse.ArgumentTypeError(f"小数の桁が {TARGET_DIGITS} を超える: {text}")
    return value


def shown(value: Fraction, target: Fraction) -> str:
    """表示する % の値。目標との大小が丸めで逆に見えない桁数まで出す（89.96% を 90.0% と表示して目標 90% に届いたと
    見せない）。丸めも正確な整数で行う。桁を増やせば丸めた値は value に近づくので、必ず終わる。"""
    digits = 1
    while True:
        scale = 10 ** digits
        scaled = round(value * scale)
        if (Fraction(scaled, scale) < target) == (value < target):
            return f"{scaled // scale}.{scaled % scale:0{digits}d}"
        digits += 1


def target_text(value: Decimal) -> str:
    """目標の % を丸めずに表示する（90.5 を 90 や 91 と表示しない）。"""
    text = f"{value:f}"
    return (text.rstrip("0").rstrip(".") if "." in text else text) or "0"


def main() -> int:
    parser = argparse.ArgumentParser(description="Package のカバレッジの集計")
    parser.add_argument("report", type=Path)
    origin = parser.add_mutually_exclusive_group(required=True)
    origin.add_argument("--sources")
    origin.add_argument("--measured-sources")
    parser.add_argument("--target", type=percentage, required=True)
    arguments = parser.parse_args()
    if arguments.measured_sources is not None and not os.path.isabs(arguments.measured_sources):
        parser.error(f"--measured-sources が絶対パスでない: {arguments.measured_sources}")
    sources = arguments.measured_sources if arguments.measured_sources is not None else os.path.realpath(arguments.sources)
    try:
        files = json.loads(arguments.report.read_text(encoding="utf-8"),
                           object_pairs_hook=reject_duplicate_keys)["data"][0]["files"]
        # 数える対象かは、記録したパスで決める（現在のファイルシステムのシンボリックリンクをたどらない）。swift test の記録は
        # Package の起点を実体のパスに解決し、起点の中のパスはそのまま書く（2026-11-22 に一時的に置いた Package で、/tmp を
        # 経由して指定しても /private/tmp の起点になり、Sources の中のシンボリックリンクのファイルはリンクのパスのまま記録される
        # ことを確かめた）。そこで起点の側だけを実体のパスにし、記録したパスは字面のまま正規化して比べる
        # 区切りは末尾に 1 つだけ付ける（起点が「/」のときに「//」にしない。名前の似た隣のディレクトリは含めない）
        prefix = os.path.normpath(sources).rstrip(os.sep) + os.sep
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
        print(f"NG: {sources} 配下に数える行がない", file=sys.stderr)
        return 2
    # 行数の比と目標を分数のまま正確に比べる（浮動小数点の丸めで、目標のすぐ下を届いたと、ちょうどを届かないと判定しない）
    percent = Fraction(sum(covered for _, covered in lines) * 100, total)
    goal = Fraction(arguments.target)
    target = target_text(arguments.target)
    print(f"カバレッジ（{sources}）: 行 {shown(percent, goal)}%（{len(lines)} ファイル・目標 {target}%）")
    if percent < goal:
        print(f"警告: カバレッジが目標の {target}% に届いていない（TST-CI-07。初期は警告のみ）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
