---
name: tdd-implement
description: Use when implementing or changing Swift code in MinutesMobile (the app target, Packages/MinutesKit, or test targets) — any feature, bug fix, or refactor of recording, storage, finalize, recovery, transcription, UI, or export. Triggers on "TDDで実装", "テストから書いて", "実装して", "次の作業単位を進めて", "PROGRESS の次のアクションをやって", "implement with TDD", "write the failing test first". Enforces the Red → Green → Refactor cycle with a recorded Red, checks that the unit's detailed design exists before coding, ties every test to a requirement ID, keeps design-document code snippets in sync, runs scripts/check.sh, and updates PROGRESS.md (including the mandatory 次のアクション). For document-only changes use update-specs instead.
---

# TDD での実装

完了の定義（`docs/01-requirements/test-requirements.md` §6）は「Red で失敗することを確認した記録がある」ことを求める。このスキルは、その記録を残しながら 1 つの作業単位を進める手順である。

規則の本文は写さない。下の「正」を開いて読む。

| 規則 | 正 |
| --- | --- |
| テスト容易性（注入・時刻・一時ディレクトリ・純粋関数・決定性・合成音声） | `test-requirements.md` §2（TST-GEN-01〜08） |
| 必須の試験シナリオ | `test-requirements.md` §3 |
| 自動検査のゲート | `test-requirements.md` §4 |
| 完了の定義 | `test-requirements.md` §6 |
| 静的解析で禁止するもの | `security-requirements.md` §10.1 |
| PR のセキュリティチェックリスト | `security-requirements.md` §10.2 |
| ビルド・テストのコマンド | `CLAUDE.md` の「ビルド・テスト」 |

## 手順

### 0. 着手してよいか確かめる

次のどれかが欠けていたら、コードを書かずに止める。

1. **`PROGRESS.md` §2 の次のアクションがこの作業である。** 違うなら利用者に確認する。前の行が「完了」でない単位に着手しない（実装の順序は上流から: 録音 → 保存 → 確定 → 復旧 → 文字起こし → 表示 → 書き出し）。
2. **その単位の詳細設計がある。** 対応する基本設計の「詳細設計への申し送り」がすべて解決されている。ないならスキル `update-specs` で先に書く。設計にない型や振る舞いを、実装の側で決めない。
3. **テストを動かす場所がある。** `Packages/MinutesKit` とテストターゲットは作業単位 2 で作る。それまでは機能のテストを書けないので、作業単位 2 を先に行う。
4. 実装する要件 ID（FR / NFR / SEC）と、それに対応する TST を列挙する。TST がない要件は `update-specs` で TST を先に足す。

**作業単位 2（プロジェクト設定）の場合。** 設定の作業単位なので、検査の手順を Red / Green として扱う。

- Red: 今失敗している `scripts/check.sh` の手順 4・7〜9（`dev-environment.md` §4.1）。手順 4 の失敗の出力を `PROGRESS.md` §2 の「Red の記録」に貼る。
- Green: `dev-environment.md` §12.3 の条件どおりに直し、手順 0〜10 がすべて通る。最後に `scripts/setup.sh` でフックを有効にする。
- 機能の Red → Green → Refactor（下の 1〜3）は、Package ができた後の最初の単位から始める。

### 1. Red: 失敗するテストを書く

- 1 回に 1 つの振る舞いだけを書く。
- **要件 ID はテストの表示名に入れる**（TST-GEN-03）。Swift Testing のタグは Swift の識別子なので、`TST-AUD-01` はタグ名にできない。

  ```swift
  @Suite("WAV ヘッダ")
  struct WAVHeaderTests {
      @Test("TST-AUD-01 RIFF / fmt / data の各フィールドが 16 kHz・16 bit・モノラルの仕様どおり")
      func headerLayout() throws {
          // Arrange
          // Act
          // Assert
      }
  }
  ```

- 状態遷移は、純粋関数への入出力の表を `@Test(arguments:)` で渡し、全遷移を網羅する（TST-GEN-06）。
- 時刻・タイマー・UUID・乱数・ファイルのルート・OS の通知・STT エンジンは、注入した偽物で与える。`sleep` / `Task.sleep` で実時間を待たない（TST-GEN-01・04・07）。
- ファイルを使うテストは、テストごとに一意の一時ディレクトリを作って終わりに消す（TST-GEN-02。Swift Testing は並列で動く）。
- 試験用の音声はコードで合成する。実際の録音をリポジトリに入れない（TST-GEN-08）。
- 正常系と異常系の両方を書く。

**Red は「正しい理由で」失敗させる。** 型や関数がなくてコンパイルが通らないだけの失敗は、Red の確認として弱い。先にシグネチャだけの空実装（固定値を返す、など）を置いてコンパイルを通し、**アサーションが失敗する**ことを確かめる。

```bash
swift test --package-path Packages/MinutesKit --filter WAVHeaderTests   # Package のテスト
# アプリ側は CLAUDE.md の -only-testing の例を使う
```

**Red を記録する。** 失敗したテストの表示名と失敗の理由（期待値と実際の値）を `PROGRESS.md` §2 の「Red の記録」に書く。PR を作るときに PR の説明へ移す。

### 2. Green: 最小の実装で通す

- テストを通す最小の変更だけを入れる。まだテストのない振る舞いを足さない。
- 禁止事項（`print(`、`try!`、強制アンラップ、空の `catch {}`、通信の API、`.documentDirectory` など）は静的解析で止まる。回避の書き方を探さず、設計どおりの手段（`os.Logger`、注入した型）を使う。
- 録音を止める実装にしない（Recording-First）。物理的に続けられない場合だけ例外（NFR-REL-05）。
- 同じテストを再実行して通ることを確かめる。

### 3. Refactor

- テストが通ったまま、重複・名前・層の境界を整える。振る舞いを変えない。
- 整える前後でテストを実行する。

1〜3 を、その単位の TST がすべて揃うまで繰り返す。

### 4. 設計書と同期する

- 詳細設計にコード片として載っているファイルを作ったら、コード片の直前に `<!-- file: パス -->`（全文）または `<!-- excerpt: パス -->`（一部）の印を付ける。
- 実ファイルを変えたら `scripts/verify-docs.py --update-snippets` で全文のコード片を書き直す。一部のコード片は手で直す。
- 詳細設計の「要件 ID ↔ テスト」の対応表に、テストの表示名と結果を書く。
- 実装の途中で設計の誤りに気付いたら、コードで黙って直さない。`update-specs` で設計を直してから実装を合わせる。
- 静的解析の規則を変えたら、`tools/lint-fixtures/<規則名>/` の違反例と正常例も同じ作業で直す。

### 5. 検査する

```bash
scripts/check.sh            # 手順 0〜10
scripts/check.sh --staged   # コミット前の範囲
```

- 作業単位 2 が済んだあとは、全手順が通ることを完了の条件にする。
- 作業単位 2 が済む前は、手順 1・4・7〜9 が失敗する（`docs/03-detailed-design/dev-environment.md` §4.1）。それ以外の手順が通ることを確かめる。
- **検査を弱めて通さない。** 失敗したらコードか設計を直す。
- 実機でしか確かめられない要件（割り込み・保護クラス・バックグラウンド継続など）は、試験記録（`docs/04-test-records/`）に「未実施」と理由・予定を書く。シミュレータで通ったことを実機の合格として扱わない。

### 6. 完了を確かめる

`test-requirements.md` §6 の 6 項目と、`security-requirements.md` §10.2 のチェックリストを 1 つずつ確かめる。満たせない項目があれば完了にしない。

### 7. PROGRESS.md を更新する（必須）

- §1 の状態を直す（完了なら日付を入れる）。
- **§2 の次のアクションを書き換える。** 空にしない。次の行の作業を、ファイル・節・コマンドの単位で 1 手書く（次が設計なら「詳細設計 `<名前>.md` の §n を書く」など）。
- Red の記録を PR の説明へ移したら、§2 から消す。
- §4 の記録の先頭に 1 行足す。

### 8. コミット

利用者に頼まれたときだけコミットする。コミットの前に型・静的解析のエラーがないことを確かめる。形式は `<type>(<scope>): <subject>`（`feat` / `fix` / `refactor` / `test` / `build` など）。`dev` ブランチで作業し、`main` へは PR で入れる。
