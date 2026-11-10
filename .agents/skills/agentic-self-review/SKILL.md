---
name: agentic-self-review
description: "この Python repo の仕様変更、実装、TDD cycle、PR 前に品質 gate、未実行テスト、未検証リスク、Subagent 指摘の採否を整理する self-review skill。作業完了前、handoff、PR 本文準備、gate 結果報告で使う。"
---

# Agentic Self Review

完了宣言ではなく、何が確認済みで何が未検証かを人間が判断できる形に圧縮する。

## Process

1. 対象の `spec/planned` / `spec/wip` / `spec/complete`、Intent Delta、non-goals を確認する。
2. diff と仕様の明示要件を照合する。
3. 実行した command、validator、test、hook を evidence として記録する。
4. 未実行 gate は `not run`、対象外は `not applicable` と書く。
5. docs / spec / skill だけの変更でも、事実整合、根拠、未検証表示、リンク・参照先、仮テキスト残りを確認する。変更したskillだけにskill validationを実行する。
6. 問題がある場合は findings を先に出す。
7. 問題がない場合も、残る test gap と未検証事項を明記する。

## Gates

| Gate | Evidence |
|---|---|
| Requirements | `AGENTS.md`、`spec/initial`、対象 spec との照合。 |
| Scope | 対象範囲、対象外、先送り事項。 |
| TDD / Tests | red / green 履歴、pytest 結果、未実行理由。 |
| Static | ruff format、ruff check、ty check、skill validation。 |
| Package | `uv lock --check`、`uv build`、package metadata smoke。 |
| Integration Review | diff、scope drift、指摘の採否。 |

## Report

```markdown
## Agentic SDD Report

### Work
- spec:
- intent delta:
- non-goals:

### Findings
| severity | finding | evidence | disposition |
|---|---|---|---|

### Gates
| gate | result | evidence |
|---|---|---|

### Next
- deferred:
- next candidate:
```

## Rules

- evidence が弱い項目を pass にしない。
- docs / spec / skillのみ変更では、変更対象を検査しないPython test・型検査・package buildを必須にしない。その成功を文書品質の根拠にしない。機械的契約の検査がある場合は実行し、変更したskillはskill-creator付属validatorで確認する。
- 工程計画では、前提の循環、担当の抜け、未決事項の担当・期限、親子間の受入条件を読む。planned仕様の執筆完了を製品工程の完了と混同しない。
- unit番号は3状態を通して一意で、移動後の参照が成立することを確認する。調査・設計、製品実装、接続・実機検証の結果を区別する。
- README や公開 docs の変更では、agent 向けの手順や作業履歴が混ざっていないか確認する。
- package metadata に触れた場合は `uv build` を省略しない。
