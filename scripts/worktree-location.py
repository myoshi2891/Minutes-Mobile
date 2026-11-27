#!/usr/bin/python3
"""専用の作業ツリーの置き場所の判定と、作業ツリーの登録の照合（.githooks/pre-push と scripts/check.sh --require-clean が共用する）。

使い方:
  scripts/worktree-location.py check <置き場所> <元のリポジトリ>
      置き場所を実体のパスに解決して標準出力に書く（NUL で終える）。置き場所（自身を含む）か祖先に SwiftFormat の設定（.swiftformat・
      .swift-version）があるもの、元のリポジトリか登録された作業ツリーの中にあるもの、判定の前後で作業ツリーの一覧が変わったものは
      認めない。
      SwiftFormat は検査するディレクトリの親のディレクトリの設定も読み、読む範囲を止める指定を持たないので、そうした
      置き場所の作業ツリーの検査に、リポジトリの外の設定や別の作業ツリーの設定が混ざる。
      終了コード: 0 = 認める / 1 = 認めない（理由は標準エラー） / 2 = 判定できない
  scripts/worktree-location.py registered <元のリポジトリ> <パス>
      パスが作業ツリーとして登録されているか（git worktree list の記録の worktree の項目との完全な一致）。
      終了コード: 0 = 登録あり / 1 = 登録なし / 2 = 一覧を取得できない
  scripts/worktree-location.py nested <元のリポジトリ> <パス>
      パスの下（パス自身は含まない）に登録された作業ツリーがあるか。
      終了コード: 0 = ない / 1 = ある（場所は標準エラー） / 2 = 判定できない
"""
import os
import stat
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
    confirm_enumerable(source)
    return paths


def confirm_enumerable(source: str) -> None:
    """登録の置き場所（<共通のディレクトリ>/worktrees）を列挙できること。できなければ終了コード 2（ないのは認める）。

    git worktree list は、列挙できない登録を黙って省いて成功する（終了コード 0）ので、一覧が欠けていないことを別に確かめる。
    """
    try:
        common = subprocess.run(["git", "-C", source, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                stdout=subprocess.PIPE, env=GIT_ENV)
        if common.returncode != 0 or not common.stdout.endswith(b"\n"):
            print(f"共通のディレクトリを特定できない（終了コード {common.returncode}）", file=sys.stderr)
            sys.exit(2)
        directory = os.path.join(common.stdout[:-1], b"worktrees")
        for entry in os.listdir(directory):
            try:
                # 名前だけ読めて、中を調べられない場合も省かれる
                if not stat.S_ISDIR(os.stat(os.path.join(directory, entry)).st_mode):
                    continue  # 登録ではない（Finder の .DS_Store など）
            except FileNotFoundError:
                continue  # 列挙の後に外された登録
            # 登録された場所の記録（gitdir）を読めない・空の登録も省かれる。登録先のディレクトリがないことは問わない
            record = os.path.join(directory, entry, b"gitdir")
            try:
                with open(record, "rb") as handle:
                    readable = bool(handle.read().strip())
            except FileNotFoundError:
                if not os.path.isdir(os.path.join(directory, entry)):
                    continue  # 列挙の後に外された登録
                readable = False
            if not readable:
                print(f"作業ツリーの登録を列挙できない: {os.fsdecode(record)} がないか空", file=sys.stderr)
                sys.exit(2)
    except FileNotFoundError:
        return  # 登録がない（git がなければ一覧の取得で止まっている）
    except OSError as error:
        print(f"作業ツリーの登録を列挙できない: {error}", file=sys.stderr)
        sys.exit(2)


def identity(path: str, registered: bool = False) -> Optional[Tuple[int, int]]:
    """デバイスと inode。ないパスは None。それ以外の失敗（権限など）は判定できないとして終了コード 2。

    registered（登録された作業ツリーのパスとその祖先）では、途中の要素がディレクトリでない（登録だけが残り、親のディレクトリが
    ファイルに置き換わったなど）場合も、ないパスとする。置き場所のパスでは、作れない場所なので判定できないものとする。
    """
    try:
        info = os.stat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        if registered and isinstance(error, NotADirectoryError):
            return None
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
    listed_first = registered_worktrees(source)
    for path in [source] + [os.fsdecode(listed) for listed in listed_first]:
        found_identity = identity(path, registered=True)
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
    # 判定の間に作業ツリーが登録・解除された場合は、判定に使った一覧が古いので認めない（呼び出し側は置き場所を作った後にも
    # 判定し直す。最初の一覧にあった作業ツリーの中は、上で拒否している）
    if sorted(registered_worktrees(source)) != sorted(listed_first):
        print(f"NG: 専用の作業ツリーの置き場所（{base}）の判定の間に作業ツリーの登録が変わった。やり直す", file=sys.stderr)
        return 1
    # パスは NUL で終える（末尾の改行を含むパスも、呼び出し側が read -d '' でそのまま受け取れる）
    sys.stdout.buffer.write(os.fsencode(base) + b"\0")
    sys.stdout.flush()
    return 0


def under(path: str, parent_identity: Tuple[int, int]) -> bool:
    """登録されたパスの祖先（自身は含まない）のどれかが、parent_identity のディレクトリと同じか。"""
    current = os.path.dirname(path)
    while True:
        if identity(current, registered=True) == parent_identity:
            return True
        upper = os.path.dirname(current)
        if upper == current:
            return False
        current = upper


def nested(source: str, parent_path: str) -> int:
    parent_identity = identity(parent_path)
    if parent_identity is None:
        print(f"{parent_path}: ディレクトリがない", file=sys.stderr)
        return 2
    found = False
    for listed in registered_worktrees(source):
        path = os.fsdecode(listed)
        # 祖先（自身は含まない）のどれかが同じディレクトリなら、その下にある（check と同じく文字列では比べない）。登録の後に
        # 途中の要素がシンボリックリンクに置き換わると、登録されたパスの祖先には現れないので、リンクを解決したパスの祖先も比べる
        if under(path, parent_identity) or under(os.path.realpath(path), parent_identity):
            print(f"NG: 専用の作業ツリー（{parent_path}）の下に別の作業ツリー（{path}）が登録されている。"
                  "git worktree move か remove で外してからやり直す", file=sys.stderr)
            found = True
    return 1 if found else 0


def main() -> int:
    arguments = sys.argv[1:]
    if len(arguments) == 3 and arguments[0] == "check":
        return check(arguments[1], arguments[2])
    if len(arguments) == 3 and arguments[0] == "registered":
        return 0 if os.fsencode(arguments[2]) in registered_worktrees(arguments[1]) else 1
    if len(arguments) == 3 and arguments[0] == "nested":
        return nested(arguments[1], arguments[2])
    print(f"使い方: {sys.argv[0]} check <置き場所> <元のリポジトリ> | registered <元のリポジトリ> <パス> | nested <元のリポジトリ> <パス>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
