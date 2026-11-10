---
name: spec-format
description: "この Python repo の作業仕様を spec/planned、spec/wip、spec/complete で作成・更新・着手・完了移動する skill。ユーザが仕様書、マイルストーンの親仕様、作業仕様、TDD Test List、初期設計に基づく工程整理を依頼したとき、または spec/ 配下を扱うときに使う。"
---

# 仕様書構成様式

作業仕様を `spec/planned` → `spec/wip` → `spec/complete` で管理する。仕様書は対象範囲、着手条件、TDD 状態、検証結果、先送り事項を束ねる作業単位でもある。

## 配置

| 状態 | path |
|---|---|
| 未着手 | `spec/planned/unit_XXX/FEATURE_NAME.md` |
| 着手中 | `spec/wip/unit_XXX/FEATURE_NAME.md` |
| 完了済み | `spec/complete/unit_XXX/FEATURE_NAME.md` |

- `FEATURE_NAME.md` は UPPER_SNAKE_CASE にする。
- 連番は3つの状態ディレクトリすべてを確認し、存在する最大番号の次を選ぶ。工程番号 U01〜U10 とは別で、未作成の子仕様の番号を予約しない。
- 着手時・完了時は同じunitをdirectoryごと移し、各状態へ複製しない。移動した仕様と参照元のリンク、本文の状態を更新する。検証済み範囲は実績に基づいて記し、移動だけで未実施の検証を完了にしない。
- 1 unitに1仕様書を置き、親と子も別unitにする。
- `spec/initial/` は初期設計の正本として扱い、作業仕様の完了管理には使わない。

## テンプレート

新規作成時は `references/template.md` を読む。親仕様では工程の受入観点を記し、詳細な子のAPIやTDDを先に固定しない。テンプレート内の案内や空欄は出力に残さない。

## 親仕様と子仕様

- 親は到達点、前提、冒頭の調査・設計、工程内の分割、受入条件、未決事項の担当・期限を所有する。具体的な未着手親仕様は先に `planned` へ置ける。
- 親仕様を執筆しただけでは工程へ着手したことにしない。その工程の最初の調査・設計に着手するときに親を `wip` へ移す。
- 子は対象部分の詳細契約・TDD・検証結果を所有し、その作業へ進む前提が揃ってから作成・詳細化する。空の雛形を一括生成しない。
- 調査・設計判断、製品実装、接続・実機検証を分けて記録する。親は子の完了だけでなく工程全体の受入条件を満たしたときに完了する。

## 記述ルール

- source、use case、対象範囲、対象外を分ける。
- 事実、推論、未検証仮説を分ける。
- TDD Test List は観測可能な振る舞いで書く。実装ファイル名や内部構造だけを item にしない。
- 検証には実行 command、結果、未実行理由を残す。
- 未着手仕様の検証は計画と実行結果を分け、実装・接続未実施を明記する。設計上の決定を実装済み・実機確認済みとして記さない。
- 先送り事項には観測、先送り理由、後続の置き場を書く。何もなければ `none` と書く。
- package metadata、CI、release workflow、public API に触れる場合は `uv lock --check` と `uv build` を検証へ含める。

## Workflow

1. `AGENTS.md`、関連する `spec/initial/*.md` を読む。
2. 既存 `spec/planned` / `spec/wip` / `spec/complete` を確認する。
3. 新規、更新、着手移動、完了移動のどれかを決め、工程番号とunit番号を区別する。
4. 新規なら `references/template.md` を使って作成する。
5. TDD で進める作業では `tdd-test-list` と接続する。
6. 対象作業後は検証結果と checklist を更新し、完了条件が揃った場合だけ `spec/complete` へ移す。

## 完了移動の条件

- checklist が更新されている。
- 検証 command と結果、または未実行理由がある。
- package / release / public API に触れた場合の gate が記録されている。
- 先送り事項が `none` か、後続 source として使える粒度になっている。
