"""project.xcproj（JSON 形式・末尾カンマあり）を読む。検査スクリプトが共有する。"""
import json
from pathlib import Path
from typing import List, Tuple


class Unreadable(Exception):
    """設定ファイルを読めない。読めないものを合格にしないために、呼び出し側で失敗として扱う。"""


def strip_trailing_commas(text: str) -> str:
    """文字列の外にある、閉じ括弧の直前のカンマだけを除く（標準の JSON パーサーで読めるようにする）。

    値の後のカンマだけが末尾カンマである。開き括弧の直後のカンマ（「{,}」「[,]」）は除かず、
    JSON パーサーに不正な入力として拒否させる（壊れた入力を空の辞書・配列として読まない）。
    """
    out: List[str] = []
    in_string = False
    escaped = False
    index = 0
    previous = ""  # 文字列の外で直前に書いた、空白でない文字
    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
            out.append(char)
            previous = char
        elif char == ",":
            rest = index + 1
            while rest < len(text) and text[rest] in " \t\r\n":
                rest += 1
            if rest >= len(text) or text[rest] not in "}]" or previous in "{[":
                out.append(char)
            previous = char
        else:
            out.append(char)
            if char not in " \t\r\n":
                previous = char
        index += 1
    return "".join(out)


def reject_constant(name: str) -> None:
    """標準の JSON にない数値の定数（NaN・Infinity・-Infinity）を拒否する（json.loads は既定で読んでしまう）。"""
    raise ValueError(f"標準の JSON にない定数 {name}")


def reject_duplicate_keys(pairs: List[Tuple[str, object]]) -> dict:
    """同じオブジェクトの中で重複した鍵を拒否する（json.loads は既定で後の値を黙って採用し、前の値を検査から隠すため）。"""
    data: dict = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"重複した鍵 {key!r}")
        data[key] = value
    return data


def load_project(path: Path) -> dict:
    try:
        data = json.loads(strip_trailing_commas(path.read_text(encoding="utf-8")), parse_constant=reject_constant,
                          object_pairs_hook=reject_duplicate_keys)
    # json.JSONDecodeError は ValueError の派生。入れ子が深すぎる入力は RecursionError（ValueError の派生ではない）になる
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as error:
        raise Unreadable(f"{path}: {error}") from error
    if not isinstance(data, dict):
        raise Unreadable(f"{path}: 最上位がオブジェクトでない")
    return data
