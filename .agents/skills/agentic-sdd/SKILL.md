---
name: agentic-sdd
description: "この Python repo の Agentic Spec-Driven Development を開始、継続する入口 skill。ユーザが Agentic SDD、工程計画、spec/planned からの着手、次の作業単位、仕様からの実装、TDD と gate を含む進行を求めるときに使う。"
---

# Agentic SDD

`AGENTS.md`、関連する `spec/initial/*.md`、対象の作業仕様を Constitution として読み、次に実行する作業単位を 1 つだけ選んで進める。工程計画の整備を依頼された場合は、その文書整備を一つの作業とし、複数の未着手親仕様を具体化できる。文書整備から製品実装へ自動的に進まない。

## Bootstrap

1. `AGENTS.md` を読む。
2. 関連する `spec/initial/*.md` を読む。
3. `spec/planned/unit_*`、`spec/wip/unit_*`、`spec/complete/unit_*` を確認する。番号は3つの状態を通して一意にする。
4. 変更を伴う作業では branch と `git status --short` を確認する。
5. ユーザ入力から Intent Delta を抽出する。なければ `none`。
6. 実装済み範囲と既存 test を確認する。
7. 05の工程順序と親仕様の前提を照合し、次の作業単位を 1 つだけ選ぶ。必要な調査・設計を実装の前に置く。

開始時は短く提示する。

```text
Agentic SDD bootstrap:
- Constitution:
- Git Context:
- Intent Delta:
- Selected Work:
- Non-goals:
- Gates:
```

## Work Selection

| 候補 | 選択条件 |
|---|---|
| roadmap / initial spec | `spec/initial` に明示された優先順がある。 |
| planned milestone | 親仕様の前提・着手条件を満たし、その工程の最初の調査・設計へ着手できる。 |
| spec TDD item | `spec/wip` の TDD Test List に小さい振る舞いがある。 |
| release / package item | package metadata、CI、PyPI、公開 API に影響する。 |
| cleanup item | green 後の責務分離、命名、重複除去が必要。 |

- 選択していない work unit や対象外機能は実装しない。
- 大きい作業は TDD item へ分割する。
- 仕様なしで大きな実装へ入らない。
- 設定値の保存、資料の読取、製品実装、接続・実機検証を区別する。未検証の外部仕様に依存する作業を成立済みとして進めない。
- 調査で前提が崩れたら影響する仕様へ戻る。独立して進められる作業と、成立確認を待つ作業を親仕様へ記す。
- package metadata、CI、release workflow に触れる場合は `uv lock --check` と `uv build` を gate に含める。

## Implementation Loop

1. `spec-format` で既存の `planned` を確認する。着手する仕様は同じunitを `wip` へ移して参照を更新する。親工程の最初の子作業に着手するときは親も `wip` へ移す。未着手仕様がなければ、今回必要な仕様だけを作る。
2. TDD が適する場合は `tdd-workflow` を使う。
3. red の理由が期待した失敗であることを確認する。
4. green を作る。
5. green 後に必要な構造変更だけ `tidy-first` と `refactor-after-green` で分ける。
6. gate 失敗時は、Spec、Plan、Tasks、Implement のどこへ戻るか決める。
7. 終了時は `agentic-self-review` で gate と未検証リスクを整理する。

親の調査・設計、製品実装、接続・実機検証の状態は別に記録する。子の調査完了だけで親を完了にしない。未着手の親仕様は `planned` に置き、詳細な子仕様やTDD項目を一括生成しない。

## Git Context

- read-only 調査では branch 作成は不要。
- 作業 branch 上で clean ならそのまま進める。
- default branch 上で clean なら、変更前に作業 branch を作るかユーザ指示を確認する。
- dirty worktree では既存変更を読み、ユーザ変更を破棄しない。
- remote 未設定なら `pr-merge-cleanup` は PR 作成前に止まる。
