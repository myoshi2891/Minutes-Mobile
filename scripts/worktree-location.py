#!/usr/bin/python3
"""専用の作業ツリーの置き場所の判定と、作業ツリーの登録の照合（.githooks/pre-push と scripts/check.sh --require-clean が共用する）。

使い方:
  scripts/worktree-location.py check <置き場所> <元のリポジトリ>
      置き場所を実体のパスに解決して標準出力に書く（NUL で終える）。置き場所（自身を含む）か祖先に SwiftFormat の設定（.swiftformat・
      .swift-version）があるもの、元のリポジトリか登録された作業ツリーの中にあるものは認めない。
      SwiftFormat は検査するディレクトリの親のディレクトリの設定も読み、読む範囲を止める指定を持たないので、そうした
      置き場所の作業ツリーの検査に、リポジトリの外の設定や別の作業ツリーの設定が混ざる。
      終了コード: 0 = 認める / 1 = 認めない（理由は標準エラー） / 2 = 判定できない
  scripts/worktree-location.py registered <元のリポジトリ> <パス>
      パスが作業ツリーとして登録されているか（git worktree list の記録の worktree の項目との完全な一致）。
      終了コード: 0 = 登録あり / 1 = 登録なし / 2 = 一覧を取得できない
"""
import os
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

# 引き継いだリポジトリを指す変数を外して git を実行する（-C で指定したリポジトリの一覧を読む）
GIT_ENV = {key: value for key, value in os.environ.items()
           if key not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR")}


def registered_worktrees(source: str) -> List[bytes]:
    """作業ツリーの一覧（git worktree list --porcelain -z）を読む。

    一覧は NUL で区切った項目の並びで、記録（作業ツリー 1 つ）は空の項目で終わり、先頭の項目が「worktree <パス>」。
    改行を含むパスも 1 つのパスとして読む。git が失敗した・形が違う場合は、比べずに認めることはせず終了コード 2。
    """
    try:
        listed = subprocess.run(["git", "-C", source, "worktree", "list", "--porcelain", "-z"], stdout=subprocess.PIPE, env=GIT_ENV)
    except OSError as error:  # 例外のままでは終了コード 1（認めない・登録なし）になり、判定できないことと区別できない
        print(f"git を起動できない: {error}", file=sys.stderr)
        sys.exit(2)
    if listed.returncode != 0:
        print(f"git worktree list が失敗した（終了コード {listed.returncode}）", file=sys.stderr)
        sys.exit(2)
    paths, first = [], True
    fields = listed.stdout.split(b"\0")
    if fields[-1] != b"":
        print("git worktree list の出力が NUL で終わらない", file=sys.stderr)
        sys.exit(2)
    for field in fields[:-1]:
        if not field:
            first = True
            continue
        if first:
            if not field.startswith(b"worktree "):
                print("git worktree list の記録が worktree の項目で始まらない", file=sys.stderr)
                sys.exit(2)
            paths.append(field[len(b"worktree "):])
            first = False
    return paths


def identity(path: str) -> Optional[Tuple[int, int]]:
    """デバイスと inode。ないパスは None。それ以外の失敗（権限など）は判定できないとして終了コード 2。"""
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        print(f"{path}: {error}", file=sys.stderr)
        sys.exit(2)
    return (info.st_dev, info.st_ino)


def check(requested: str, source: str) -> int:
    # 置き場所を作る前に実体のパスへ解決する（まだない部分を含んでよい。git worktree list が示す実体のパスとも比べる）
    base = os.path.realpath(requested)
    # 置き場所（自身を含む）か、その祖先のどれかに設定があれば認めない（専用の作業ツリーの中の設定は、リポジトリの
    # 設定としてそのまま読む）
    ancestor = base
    while True:
        for name in (".swiftformat", ".swift-version"):
            found = os.path.join(ancestor, name)
            # ないことを確かめられたときだけ次へ進む（権限などで調べられないものを、ないものとして認めない）
            try:
                os.lstat(found)
            except FileNotFoundError:
                continue
            except OSError as error:
                print(f"NG: SwiftFormat の設定（{found}）の有無を調べられない: {error}", file=sys.stderr)
                return 2
            print(f"NG: 専用の作業ツリーの置き場所（{base}）の上に SwiftFormat の設定（{found}）がある。設定のない場所を指定する",
                  file=sys.stderr)
            return 1
        parent = os.path.dirname(ancestor)
        if parent == ancestor:
            break
        ancestor = parent
    # 比べるのはパスの文字列ではなく、ファイルシステム上の同一性（デバイスと inode）。置き場所の既存の祖先（自身を含む）の
    # どれかが、元のリポジトリか登録された作業ツリーと同じディレクトリなら認めない（大文字小文字を区別しないボリュームで
    # 綴りの違うパス・ハードリンクなどを、文字列の比較で見逃さない。区別するボリュームの別のディレクトリは別物として認める）
    roots: Dict[Tuple[int, int], str] = {}
    for path in [source] + [os.fsdecode(listed) for listed in registered_worktrees(source)]:
        found_identity = identity(path)
        if found_identity is not None:  # ないものは、登録だけが残りディレクトリのない作業ツリー（その中に置き場所はありえない）
            roots[found_identity] = path
    current = base
    while True:
        info = identity(current)  # None はまだない部分（作る予定の場所）
        if info is not None and info in roots:
            print(f"NG: 専用の作業ツリーの置き場所（{base}）が作業ツリー（{roots[info]}）の中にある。どの作業ツリーの外も指定する",
                  file=sys.stderr)
            return 1
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    # パスは NUL で終える（末尾の改行を含むパスも、呼び出し側が read -d '' でそのまま受け取れる）
    sys.stdout.buffer.write(os.fsencode(base) + b"\0")
    sys.stdout.flush()
    return 0


def main() -> int:
    arguments = sys.argv[1:]
    if len(arguments) == 3 and arguments[0] == "check":
        return check(arguments[1], arguments[2])
    if len(arguments) == 3 and arguments[0] == "registered":
        return 0 if os.fsencode(arguments[2]) in registered_worktrees(arguments[1]) else 1
    print(f"使い方: {sys.argv[0]} check <置き場所> <元のリポジトリ> | registered <元のリポジトリ> <パス>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
