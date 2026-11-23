---
name: update-specs
description: Use when adding, changing, or retiring anything in the MinutesMobile specification documents under docs/ — requirements (FR/NFR/CON/INV/OQ), security (SEC), test requirements (TST), cost ledger (COST), owner decisions (UD), basic design decisions (BD), or writing a new detailed design document (docs/03-detailed-design/). Triggers on "仕様書を更新", "要件を追加", "要件を変更", "要件を廃止", "設計書を直す", "詳細設計を書く", "基本設計を更新", "OQ を解決", "UD を記録", "update the spec", "add a requirement". Runs impact analysis by ID, keeps every dependent document in sync in the same change, runs scripts/verify-docs.py, and updates PROGRESS.md (including the mandatory 次のアクション). Not for Swift implementation work — use tdd-implement for that.
---

# 仕様書の更新

このリポジトリの正は `docs/` にある。1 か所だけ直して終わりにすると、要件 ID で結ばれたほかの文書との食い違いが残る。このスキルは、影響の調べ方と、同じ作業で直す範囲を手順にしたものである。

規則の本文は写さない。下の「正」を開いて読む（写したものは古くなるため）。

| 規則 | 正 |
| --- | --- |
| ID の体系・付ける列・廃止の書き方 | `docs/README.md` §3 |
| 版管理・同期・確認日の規則 | `docs/README.md` §4 |
| 詳細設計の方針・文書の検査 | `docs/03-detailed-design/README.md` §2・§3 |
| 依存を足す 7 手順 | `docs/01-requirements/dependencies-and-costs.md` §4 |

## 手順

### 1. 変更の種類を決める

| 種類 | 書く場所 |
| --- | --- |
| FR / NFR / CON / INV / OQ | `docs/01-requirements/requirements.md` |
| SEC | `docs/01-requirements/security-requirements.md` |
| TST | `docs/01-requirements/test-requirements.md` |
| COST | `docs/01-requirements/dependencies-and-costs.md` |
| UD | `docs/README.md` §5 |
| OQ の解決 | `requirements.md` の OQ の行と `docs/02-basic-design/README.md` §3 の両方 |
| BD | 基本設計の各文書の §1（`architecture.md` だけは §3） |
| 詳細設計の新しい文書 | `docs/03-detailed-design/<基本設計と同じ名前>.md` |

### 2. オーナーに確認してから進めるもの

次のどれかに当たったら、書く前に止めて利用者に確認する。ここは自分で決めない。

- 費用が発生しうるもの・GPL 系・独自ライセンスの依存（UD-05）。依存を足すときは 7 手順を踏む
- 新しい UD（オーナーの決定）。決定の文言は利用者の言葉で書く
- Bundle Identifier（CON-09。決めたら変えられない）
- 要件の削除・フェーズの変更（MVP ↔ P2）

### 3. 影響を調べる

変える ID で `docs/` と、`CLAUDE.md`・ルートの `README.md`・`.claude/skills/`・`PROGRESS.md` を検索し、参照している文書をすべて挙げる。`CLAUDE.md` とスキル（`.claude/skills/`）は実装の指示を要約して持ち、`PROGRESS.md` は次のアクションに ID を挙げるので、要件を変えたときに古い指示・進捗が残りやすい（`verify-docs.py` が ID を照合するのは `docs/`・`CLAUDE.md`・`README.md` だけなので、`.claude/skills/` と `PROGRESS.md` は下の検索の結果を目で読む）。文書は ID を省略して書くことが多いので、完全一致だけで終わらせない。次の 3 つの書き方をすべて拾う（例は `FR-REC-02`）。

| 書き方 | 例 | 拾い方 |
| --- | --- | --- |
| 完全一致 | `FR-REC-02` | ID そのもので検索する |
| 群（領域の並びの省略を含む） | `FR-REC 群`、`FR-CNS・REC・INT 群` | 接頭辞（`FR-`）と領域（`REC`）で検索し、`群` の付いた行を読む |
| 範囲・番号の並びの省略 | `FR-REC-01〜03`、`FR-REC-01・02` | 領域の接頭辞（`FR-REC-`）で検索し、範囲・並びが変える番号を含むかを読む |
| スラッシュ区切りの番号の省略 | `SEC-NET-03 / 04`、`FR-REC-01/02` | 領域の接頭辞の後ろの `/` で検索し、並びが変える番号を含むかを読む |

```bash
grep -rn "FR-REC-02" docs/ CLAUDE.md README.md .claude/skills/ PROGRESS.md                          # 完全一致
grep -rnE "FR-([A-Z0-9]+・)*REC(・[A-Z0-9]+)* ?群" docs/ CLAUDE.md README.md .claude/skills/ PROGRESS.md   # 群（並びの省略を含む）
grep -rnE "FR-REC-[0-9]+(〜|・)" docs/ CLAUDE.md README.md .claude/skills/ PROGRESS.md               # 範囲・並び。番号が含まれるかは目で確かめる
grep -rnE "FR-REC-[0-9]+ ?/ ?[0-9]+" docs/ CLAUDE.md README.md .claude/skills/ PROGRESS.md          # スラッシュ区切り。番号が含まれるかは目で確かめる
```

`scripts/verify-docs.py` も範囲（〜）・並び（・）・スラッシュ区切り（`/`）と群を展開して ID を照合している（展開の規則は同じ）。ただし検査が確かめるのは参照先の ID が定義されていることだけで、変えた ID を参照している箇所の内容が正しいかは確かめないので、上の検索の結果を目で読む。検索の結果から、参照している文書と節をすべて挙げてから直す。

- 要件を変えたら、それを「満たす要件:」に挙げている設計書の節と、それを「対応する要件」に持つ TST を同じ作業で直す。
- TST を変えたら、詳細設計の「要件 ID ↔ テスト」の対応表と、実装済みならテストコードの表示名（`@Test("TST-…")`）も直す。
- BD を変えたら、その BD を引用している詳細設計の節も直す。

### 4. 書く

- **ID を再利用しない。** 廃止は行を消さず「廃止（理由・日付）」と書く。
- 接頭辞ごとに必須の列を埋める（出典・フェーズ・MASVS 分類・対応する要件。`docs/README.md` §3）。
- 基本設計・詳細設計の各 `##` 節の冒頭に「満たす要件:」の行を置く（ない節は「満たす要件: —」）。
- 各文書に「セキュリティ」節と「テスト観点」節を置く。詳細設計は「要件 ID ↔ テスト」の対応表と、基本設計の「詳細設計への申し送り」をすべて解決する表も置く。
- 実装がある箇所のコード片には `<!-- file: パス -->`（全文）または `<!-- excerpt: パス -->`（一部）の印を付ける。まだ作っていないファイルには印を付けない。
- 技術の版数・提供状況は記憶で書かない。確認日と出典 URL を添える（確認日から暦の 3 か月後の日以降、`verify-docs.py` が警告する）。
- 実機で確かめていない事項は「断定しない事項」節に置き、保証値として書かない。
- 1 文書は 2,000 行未満にする。超えそうなら機能単位に分ける（`dev-environment.md` はすでに 1,500 行を超えている）。
- 図は Mermaid、コードフェンスは言語を指定する。

### 5. 状態を書いている場所を直す

文書の状態（初版・作成中・未着手など）が変わったら、次の場所を同じ作業で直す。

- `docs/README.md` §1
- `docs/02-basic-design/README.md` §1 または `docs/03-detailed-design/README.md` §1
- ルートの `README.md` の「現在の状態」
- `CLAUDE.md` の「プロジェクトの現状」（書いてある内容と食い違う場合だけ）
- `PROGRESS.md` §1（作業の順番の状態）

### 6. 検査する

```bash
scripts/verify-docs.py                    # 文書の整合（ID・リンク・節の構成・申し送り・コード片）。作業ツリーを検査する
scripts/verify-docs.py --update-snippets  # 実ファイルを変えた後に、全文のコード片を書き直すとき
git add <変えた文書と実ファイル> && scripts/check.sh --staged   # コミット前の範囲。ステージした内容（インデックス）だけを検査する
```

- `scripts/check.sh --staged` は作業ツリーではなくインデックスを検査する。文書（とコード片の元の実ファイル）を**ステージしてから**実行する。ステージしていない変更は検査されず、ステージした文書だけが古いまま照合される。
- コミット前の最後の `--staged` は、手順 7 で `PROGRESS.md` を更新・ステージした後に実行する（手順 7 の最後の項目）。
- コミットしない（利用者に頼まれていない）場合は `--staged` を実行しない。その場合は作業ツリーを対象とする `scripts/verify-docs.py` の結果で検査とする（上の 1 行目は必ず実行する）。

失敗したら文書の側を直す。検査を弱めない。

### 7. PROGRESS.md を更新する（必須）

- §1 の状態を直す。
- **§2 の次のアクションを書き換える。** 空にしない。ファイル・節・コマンドの単位で、次に手を付ける 1 手を書く。止まっているなら、待っているものとその間にできる作業を書く。
- §3 にオーナーの判断待ちが増えた・解消したら直す。
- §4 の記録の先頭に 1 行足す。
- コミットする場合は、`PROGRESS.md` もステージしてから `scripts/check.sh --staged` を実行し直す（手順 6 の `--staged` は更新前の進捗を検査しているため、更新後の進捗を含めた内容で検査をやり直す）。

### 8. コミット

利用者に頼まれたときだけコミットする。形式は `docs(<scope>): <subject>`（`<scope>` は `requirements` / `design` / `dev` など、既存の履歴に合わせる）。

- **最後のコミット（`PROGRESS.md` の更新を含む）の後に `scripts/check.sh --require-clean` を実行する。** 開始時のコミットを専用の作業ツリーで検査し、未コミットの変更があれば手順を始めずに失敗する。PR を作る場合は、出力の合格の行（コミットのハッシュを含む）を PR の説明に貼る（`docs/03-detailed-design/dev-environment.md` §4.2）。PR を更新したら、更新後のコミットで実行し直す。
- **PR の統合の記録には、統合するコミットでの手順 0〜10 すべての結果（`--steps` を付けない `--require-clean` の合格の行）が要る。** 一部の手順だけ（`--steps 0,2,3,5,6,10` など）の結果は、統合の証跡として貼らない。作業単位 2 が済む前は手順 1・4・7〜9 が失敗するので、一部の手順の結果は手元の確認にだけ使い（`tdd-implement` の手順 5 と同じ）、その間の文書の PR は、`docs/02-basic-design/dev-environment.md` §6.2 の「初期導入の手順」による（全手順の結果と失敗を「全手順の合格ではない」と明記して記録し、オーナーの承認の記録を得てから統合する。承認を自分で書かない）。
