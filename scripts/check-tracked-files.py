#!/usr/bin/python3
"""録音データ・試験の DB・秘密情報のファイルが、Git の管理に入っていないことの検査（SEC-SUP-04・05）。

1. リポジトリ直下の .gitignore に、必須の行がある
2. どの .gitignore にも、除外を取り消す行（! で始まる行）がない
3. インデックス（追跡しているファイルと、ステージしたファイル）に、必須の行が除外する対象のファイルがない
   （git add --force は .gitignore を越えるので、インデックスの側も調べる）

1・2 は、インデックスにある .gitignore の内容で判定する（作業ツリーの内容ではない。コミット前の検査で、ステージしていない
.gitignore の変更に合格を左右させない）。追跡していない入れ子の .gitignore は、インデックスに内容がないが git add の対象を
変えうるので、2 だけを作業ツリーの内容で判定する。
git の環境のうち、別のリポジトリを指しうる変数（GIT_DIR など）と、パスの指定の解釈を変える変数（GIT_LITERAL_PATHSPECS など）は
外す。GIT_INDEX_FILE は残す（部分的なコミットのフックと check.sh --staged が、検査するインデックスをこの変数で渡すため）。
置き換えのオブジェクト（git replace）は使わない（--no-replace-objects。インデックスが指す元の blob を読む）。

使い方: scripts/check-tracked-files.py [--root <リポジトリ>]
終了コード: 0 = 問題なし / 1 = 違反あり / 2 = 読めない（読めないものを合格にしない）
"""
import argparse
import fnmatch
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parent.parent

# .gitignore に必ず置く行 → 理由。末尾が / の行はディレクトリ（先頭も / ならリポジトリ直下だけ）、それ以外はファイル名の型
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
    # build はリポジトリ直下だけ（下位の build という名前のディレクトリは追跡して検査する。.gitignore の注記と同じ）
    "/build/": "ビルドの成果物",
}


# 外から引き継いだ場合に、--root と別のリポジトリ・オブジェクトの置き場所を git に選ばせる変数（setup.sh と同じ考え方）。
# GIT_INDEX_FILE は外さない（検査するインデックスを呼び出し側が渡すため）
REPOSITORY_LOCATION_VARIABLES = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)

# 外から引き継いだ場合に、パスの指定（:(glob)**/.gitignore・-- の後の名前）の解釈を変える変数。
# GIT_LITERAL_PATHSPECS は :(glob) の指定を名前そのものとして読ませ、入れ子の .gitignore を見落とさせる
PATHSPEC_VARIABLES = ("GIT_LITERAL_PATHSPECS", "GIT_GLOB_PATHSPECS", "GIT_NOGLOB_PATHSPECS", "GIT_ICASE_PATHSPECS")


class Unreadable(Exception):
    """Git の管理下でない・コマンドが失敗したなど、検査そのものができない。"""


def git_env() -> Dict[str, str]:
    """リポジトリの場所を指す変数とパスの指定の解釈を変える変数を除いた環境。git には --root からリポジトリを探させる。"""
    removed = REPOSITORY_LOCATION_VARIABLES + PATHSPEC_VARIABLES
    return {key: value for key, value in os.environ.items() if key not in removed}


def git(root: Path, *arguments: str) -> bytes:
    # 置き換えのオブジェクト（refs/replace/）で、インデックスの blob を別の内容に見せかけさせない
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *arguments], capture_output=True, env=git_env())
    if result.returncode != 0:
        raise Unreadable(result.stderr.decode("utf-8", "replace").strip())
    return result.stdout


def git_paths(root: Path, *arguments: str) -> List[str]:
    """git ls-files の結果を返す。区切りは NUL（空白・日本語を含む名前をそのまま受け取るため）。"""
    output = git(root, "ls-files", "-z", *arguments)
    return [name for name in output.decode("utf-8", "surrogateescape").split("\0") if name]


def meaningful_lines(text: str) -> List[str]:
    """コメントと空行を除いた行を返す。"""
    lines = [line.rstrip() for line in text.splitlines()]
    return [line for line in lines if line and not line.startswith("#")]


def indexed_lines(root: Path, name: str) -> List[str]:
    """インデックスにある内容（ステージした内容）の行。cat-file はフィルタ（textconv など）を通さない blob のバイト列を返す。"""
    try:
        text = git(root, "cat-file", "blob", f":{name}").decode("utf-8")
    except UnicodeDecodeError as error:
        raise Unreadable(f"{name}（インデックス）: {error}") from error
    return meaningful_lines(text)


def working_lines(path: Path) -> List[str]:
    """作業ツリーのファイルの行（追跡していない .gitignore だけに使う）。"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise Unreadable(f"{path}: {error}") from error
    return meaningful_lines(text)


def excluded_by(name: str) -> str:
    """そのパスを除外するはずの必須の行を返す（当てはまらなければ空文字列）。ファイル名は大文字と小文字を区別しない。
    ファイル名の型（*.wav・.env など）は、git と同じくパスのどの階層の名前にも当てはめる（rec.wav/ の下のファイルも除外の対象）。"""
    parts = name.split("/")
    for pattern in REQUIRED_IGNORE_LINES:
        if pattern.startswith("/") and pattern.endswith("/"):
            if len(parts) > 1 and parts[0] == pattern[1:-1]:
                return pattern
        elif pattern.endswith("/"):
            if pattern[:-1] in parts[:-1]:
                return pattern
        elif any(fnmatch.fnmatchcase(part.lower(), pattern.lower()) for part in parts):
            return pattern
    return ""


def check(root: Path) -> List[str]:
    errors: List[str] = []
    tracked = git_paths(root, "--cached")
    patterns = ("--", ".gitignore", ":(glob)**/.gitignore")
    indexed = sorted(set(git_paths(root, "--cached", *patterns)))
    if ".gitignore" not in indexed:
        if (root / ".gitignore").is_file():
            errors.append("[SEC-SUP-04] .gitignore がインデックスにない（追跡していない .gitignore はコミットに入らない。git add .gitignore）")
        else:
            errors.append("[SEC-SUP-04] .gitignore がない")
    else:
        present = set(indexed_lines(root, ".gitignore"))
        for pattern, reason in REQUIRED_IGNORE_LINES.items():
            if pattern not in present:
                errors.append(f"[SEC-SUP-04] .gitignore に {pattern} の行がない（{reason}。インデックスの内容で判定）")
    # 取り消しの行: 追跡している .gitignore はインデックスの内容（作業ツリーから消したものも含む）、
    # 追跡していない .gitignore は作業ツリーの内容で判定する
    untracked = sorted(set(git_paths(root, "--others", "--exclude-standard", *patterns)) - set(indexed))
    sources = [(name, indexed_lines(root, name)) for name in indexed]
    sources += [(name, working_lines(root / name)) for name in untracked if (root / name).is_file()]
    for name, lines in sources:
        for line in lines:
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
