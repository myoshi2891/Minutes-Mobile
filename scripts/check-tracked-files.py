#!/usr/bin/python3
"""録音データ・試験の DB・秘密情報のファイルが、Git の管理に入っていないことの検査（SEC-SUP-04・05）。

1. リポジトリ直下の .gitignore に、必須の行がある
2. どの .gitignore にも、除外を取り消す行（! で始まる行）がない
3. インデックス（追跡しているファイルと、ステージしたファイル）に、必須の行が除外する対象のファイルがない
   （git add --force は .gitignore を越えるので、インデックスの側も調べる）

使い方: scripts/check-tracked-files.py [--root <リポジトリ>]
終了コード: 0 = 問題なし / 1 = 違反あり / 2 = 読めない（読めないものを合格にしない）
"""
import argparse
import fnmatch
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent

# .gitignore に必ず置く行 → 理由。末尾が / の行はディレクトリ、それ以外はファイル名の型
REQUIRED_IGNORE_LINES: Dict[str, str] = {
    "*.wav": "録音データ",
    "*.m4a": "録音データ",
    "*.caf": "録音データ",
    "*.zip": "書き出したアーカイブ",
    "*.sqlite": "試験で作った DB",
    "*.sqlite-wal": "試験で作った DB",
    "*.sqlite-shm": "試験で作った DB",
    ".env": "秘密情報",
    ".env.*": "秘密情報",
    "xcuserdata/": "個人の Xcode 設定",
    "DerivedData/": "ビルドの成果物",
    ".build/": "ビルドの成果物",
    "build/": "ビルドの成果物",
}


class Unreadable(Exception):
    """Git の管理下でない・コマンドが失敗したなど、検査そのものができない。"""


def git_paths(root: Path, *arguments: str) -> List[str]:
    """git ls-files の結果を返す。区切りは NUL（空白・日本語を含む名前をそのまま受け取るため）。"""
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", *arguments], capture_output=True)
    if result.returncode != 0:
        raise Unreadable(result.stderr.decode("utf-8", "replace").strip())
    return [name for name in result.stdout.decode("utf-8", "surrogateescape").split("\0") if name]


def ignore_lines(path: Path) -> List[str]:
    """コメントと空行を除いた行を返す。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise Unreadable(f"{path}: {error}") from error
    lines = [line.rstrip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def excluded_by(name: str) -> str:
    """そのパスを除外するはずの必須の行を返す（当てはまらなければ空文字列）。ファイル名は大文字と小文字を区別しない。"""
    parts = name.split("/")
    for pattern in REQUIRED_IGNORE_LINES:
        if pattern.endswith("/"):
            if pattern[:-1] in parts[:-1]:
                return pattern
        elif fnmatch.fnmatchcase(parts[-1].lower(), pattern.lower()):
            return pattern
    return ""


def check(root: Path) -> List[str]:
    errors: List[str] = []
    tracked = git_paths(root, "--cached")
    top = root / ".gitignore"
    if not top.is_file():
        errors.append("[SEC-SUP-04] .gitignore がない")
    else:
        present = set(ignore_lines(top))
        for pattern, reason in REQUIRED_IGNORE_LINES.items():
            if pattern not in present:
                errors.append(f"[SEC-SUP-04] .gitignore に {pattern} の行がない（{reason}）")
    nested = git_paths(root, "--cached", "--others", "--exclude-standard", "--", ".gitignore", ":(glob)**/.gitignore")
    for name in sorted(set(nested)):
        if not (root / name).is_file():
            continue  # インデックスにだけあり、作業ツリーから消したもの
        for line in ignore_lines(root / name):
            if line.startswith("!"):
                errors.append(f"[SEC-SUP-04] {name}: 除外を取り消す行（{line}）を置かない")
    for name in tracked:
        pattern = excluded_by(name)
        if pattern:
            errors.append(f"[SEC-SUP-04] {name}: Git の管理に入れない（{REQUIRED_IGNORE_LINES[pattern]}。{pattern}）")
    if not errors:
        print(f"OK: .gitignore の必須の行 {len(REQUIRED_IGNORE_LINES)} 件があり、追跡している {len(tracked)} 件に録音データ・DB・秘密情報のファイルなし")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="録音データ・試験の DB・秘密情報のファイルが Git の管理に入っていないことの検査")
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    try:
        errors = check(arguments.root.resolve())
    except Unreadable as error:
        print(f"NG: リポジトリを読めない: {error}", file=sys.stderr)
        return 2
    for message in errors:
        print(f"NG: {message}")
    if errors:
        print(f"NG: 追跡するファイルの違反 {len(errors)} 件")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
