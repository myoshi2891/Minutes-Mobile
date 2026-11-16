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
import subprocess
import sys
from typing import List, Tuple


def version_key(text: str) -> Tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def main() -> int:
    parser = argparse.ArgumentParser(description="テストに使うシミュレータの UDID を出力する")
    parser.add_argument("--minimum-os", default="27.2")
    arguments = parser.parse_args()
    try:
        minimum = version_key(arguments.minimum_os)
        listing = subprocess.run(["xcrun", "simctl", "list", "-j"], capture_output=True, text=True, check=True)
        data = json.loads(listing.stdout)
        runtimes = {
            item["identifier"]: version_key(item["version"])
            for item in data["runtimes"]
            if item.get("platform") == "iOS" and item.get("isAvailable")
        }
        candidates: List[Tuple[Tuple[int, ...], str, str]] = [
            (runtimes[runtime], device["name"], device["udid"])
            for runtime, devices in data["devices"].items()
            if runtime in runtimes and runtimes[runtime] >= minimum
            for device in devices
            if device.get("isAvailable") and "iPhone" in device.get("deviceTypeIdentifier", "")
        ]
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
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
