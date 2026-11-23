#!/usr/bin/python3
"""テストに使うシミュレータを選び、その UDID を出力する。

機種名を決め打ちにしない（導入済みのランタイムと機種は Mac ごとに違い、
最低 OS より古いランタイムの機種を指定するとビルドが失敗する）。
最低 OS（CON-01）以上のランタイムにある iPhone のうち、ランタイムの版が最も低いもの、
同じ版の中では名前の順で最初のものを選ぶ（同じ環境では毎回同じ結果になる）。

使い方: scripts/find-simulator.py [--minimum-os 27.2]
終了コード: 0 = 見つかった / 1 = 条件を満たすシミュレータがない / 2 = 一覧を取得できない
"""
import argparse
import json
import os
import re
import subprocess
import sys
from typing import List, Tuple


# シミュレータの UDID は UUID（8-4-4-4-12 桁の 16 進数）。simctl は大文字で出すが、大文字小文字は区別しない
UDID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


def version_key(text: str) -> Tuple[int, ...]:
    """版を比較用のタプルにする。末尾の 0 は除く（27.2 と 27.2.0 を同じ版として扱う）。

    各要素は ASCII の数字 1 桁以上だけを認める（int が読める符号・空白・下線・全角数字の値を版としない）。
    """
    components = text.split(".")
    if not all(re.fullmatch(r"[0-9]+", component) for component in components):
        raise ValueError(f"版の形が違う: {text!r}")
    parts = [int(component) for component in components]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="テストに使うシミュレータの UDID を出力する")
    parser.add_argument("--minimum-os", default="27.2")
    arguments = parser.parse_args()
    # 単独で実行しても check.sh と同じ Xcode を使う（DEVELOPER_DIR がない・空のときだけ既定にし、利用者の指定は変えない）
    env = dict(os.environ)
    env["DEVELOPER_DIR"] = env.get("DEVELOPER_DIR") or "/Applications/Xcode.app/Contents/Developer"
    try:
        minimum = version_key(arguments.minimum_os)
        listing = subprocess.run(["xcrun", "simctl", "list", "-j"], capture_output=True, text=True, check=True, env=env)
        data = json.loads(listing.stdout)
        devices_by_runtime = data["devices"]
        # 形が違う一覧は取得の失敗として扱う（空の辞書は候補なしとして下で扱う）
        if not isinstance(devices_by_runtime, dict):
            raise TypeError(f"devices が辞書でない: {type(devices_by_runtime).__name__}")
        # 各項目も辞書であること（項目が辞書でない一覧を、項目を読み飛ばして候補なしと取り違えない）
        if not isinstance(data["runtimes"], list) or not all(isinstance(item, dict) for item in data["runtimes"]):
            raise TypeError("runtimes が辞書の配列でない")
        # 版は文字列であること（数値などを version_key の split で例外にせず、取得の失敗として扱う）
        for item in data["runtimes"]:
            # platform は空でない文字列であること（ない・数値・空の項目を、iOS でないとして読み飛ばして候補なしと取り違えない。
            # 正しい形の iOS 以外のランタイム（watchOS など）は下の絞り込みで除く）
            if not isinstance(item.get("platform"), str) or not item.get("platform"):
                raise TypeError(f"runtimes の {item.get('identifier')!r} の platform が空でない文字列でない")
            # 使えるかの値は真偽値であること（文字列の "false" などを真と取り違えない）
            if not isinstance(item.get("isAvailable"), bool):
                raise TypeError(f"runtimes の {item.get('identifier')!r} の isAvailable が真偽値でない")
            if item.get("platform") == "iOS" and item.get("isAvailable") and not isinstance(item.get("version"), str):
                raise TypeError(f"runtimes の {item.get('identifier')!r} の version が文字列でない")
            # 候補になりうるランタイムの識別子は空でない文字列であること（null・空・数値の識別子を辞書の鍵にすると、
            # devices の鍵と一致せず候補なしと取り違えるか、別のランタイムの鍵と取り違える）
            identifier = item.get("identifier")
            if item.get("platform") == "iOS" and item.get("isAvailable") and not (isinstance(identifier, str) and identifier):
                raise TypeError(f"runtimes の使える iOS のランタイムの identifier が空でない文字列でない: {identifier!r}")
        for runtime, devices in devices_by_runtime.items():
            if not isinstance(devices, list) or not all(isinstance(device, dict) for device in devices):
                raise TypeError(f"devices の {runtime} が辞書の配列でない")
            # 機種の識別子は文字列であること（配列や辞書に「iPhone」を含めた一覧を iPhone と取り違えない）
            for device in devices:
                if not isinstance(device.get("isAvailable"), bool):
                    raise TypeError(f"devices の {runtime} の isAvailable が真偽値でない")
                if not isinstance(device.get("deviceTypeIdentifier", ""), str):
                    raise TypeError(f"devices の {runtime} の deviceTypeIdentifier が文字列でない")
        runtimes = {
            item["identifier"]: version_key(item["version"])
            for item in data["runtimes"]
            if item.get("platform") == "iOS" and item.get("isAvailable")
        }
        # 候補になりうる機種（最低 OS 以上の使えるランタイムの、使える機種）は、機種の識別子が空でない文字列であること
        # （ない・空の識別子を iPhone でないとして読み飛ばし、候補なしと取り違えない）
        for runtime, devices in devices_by_runtime.items():
            if runtime in runtimes and runtimes[runtime] >= minimum:
                for device in devices:
                    identifier = device.get("deviceTypeIdentifier")
                    if device.get("isAvailable") and not (isinstance(identifier, str) and identifier):
                        raise TypeError(f"devices の {runtime} の使える機種の deviceTypeIdentifier がない・空")
        candidates: List[Tuple[Tuple[int, ...], str, str]] = [
            (runtimes[runtime], device["name"], device["udid"])
            for runtime, devices in devices_by_runtime.items()
            if runtime in runtimes and runtimes[runtime] >= minimum
            for device in devices
            if device.get("isAvailable") and "iPhone" in device.get("deviceTypeIdentifier", "")
        ]
        # 候補の名前と UDID は文字列で、UDID は UUID の形であること（並べ替えや出力の前に確かめ、形の違う一覧を取得の失敗にする。
        # destination の id= に UUID でない値を渡さない）
        for _, name, udid in candidates:
            if not isinstance(name, str) or not isinstance(udid, str) or not UDID.fullmatch(udid):
                raise TypeError(f"シミュレータの name・udid の形が違う: {name!r}, {udid!r}")
    # 深い入れ子の一覧は json の読み込みが RecursionError になる（形の違う一覧と同じく取得の失敗として扱う）
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, RecursionError, KeyError, TypeError, ValueError) as error:
        print(f"NG: シミュレータの一覧を取得できない: {error!r}", file=sys.stderr)
        return 2
    if not candidates:
        print(
            f"NG: iOS {arguments.minimum_os} 以上の iPhone のシミュレータがない。"
            "Xcode の Settings > Components でランタイムを入れるか、機種を追加する",
            file=sys.stderr,
        )
        return 1
    print(sorted(candidates)[0][2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
