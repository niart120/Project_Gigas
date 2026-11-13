# 常設目標と一体型メッセージ入力 仕様書

## 1. 概要

U06 M1の追加修正。利用者の2026-11-13の指示を起点に、目標と今回の作業を別見出しで常設し、Messageチップを入力枠内へ置く。一体型入力欄は通常送信と途中指示の既存配送を使う。

## 2. 対象範囲

目標の空状態、履歴の外側見出し撤去、入力枠内のMessageチップ・本文・操作列・モデル/推論量表示と既存Codex設定への入口。実行中の原稿ありは最終指示に従い送信と小さい中断を両方表示する。

## 3. 対象外

Goal接続・Goal送信モード・pause/resumeはM2。候補モデル取得・会話中の次turn設定反映はM3。音声・添付・PR・マージは行わない。レターボックス/ストレッチ、U05の停止・設定保存・観測の責務分離は維持する。

## 4. 関連文書・着手条件

[初期UI](../../initial/10-ui-interaction.md)、[U06親](../unit_011/GOAL_EXECUTION.md)、[前回M1](../../complete/unit_046/CONTEXTUAL_DIALOGUE_UI.md)を引き継ぐ。開始branchはcodex/u06-m0-goal-design、HEAD 0ad2a09、worktree clean。既存PromptEdit、Transcript、PlanPanel、設定画面を使い、依存を追加しない。

## 5. 振る舞い仕様

| 対象 | 契約 |
|---|---|
| 目標/今回の作業 | 右側映像下で両見出しを常設。M1の目標本文は—。入力やPlanからGoalを捏造しない。Planの更新・終端・選択・コピー・閲覧保持は既存契約を維持 |
| 入力 | 一つの枠内にMessageチップ、本文、下部操作列。チップは原稿文字列・コピー・送信へ混入しない。History/Messageの枠外見出しは置かず、アクセシブル名は維持 |
| 操作 | 待機中は送信、実行中かつ原稿なしは同じ右端位置に中断。実行中かつ原稿ありは右端に送信と隣に小さい中断。中断送信中は重複操作を防ぎ原稿を保持。Ctrl+Enterは送信専用で空欄時の中断に転用しない |
| 配送 | 通常turn/steerの既存可否・成功証拠と原稿保持を再利用。IME、質問・承認、送信中の追記、失敗/不明を維持 |
| モデル/推論量 | 入力枠内は実接続の値を表示し、既存設定のCodexタブを開く。未接続は設定入口名のみ。現在接続へ未反映の保存値を実行中の値として表示しない。次turn反映を先行実装しない |

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | D01 目標と今回の作業が空/更新/終端でも常設され閲覧を保持する | regression | unit | 既存Plan試験を改訂、2件成功 |
| todo | D02 一体入力が状態別操作・途中指示・原稿/IME保持・設定入口を満たす | regression | integration | 既存GUI/keyboard/window/settings試験を改訂・統合 |

## 7. 設計メモ

表示用のMessageComposerへ既存PromptEditと操作部をまとめる。配送・状態はConnectionPanelが所有し二重化しない。目標欄の表示とGoal API接続の時期を分ける。将来Goal送信モードでは枠内バッジで識別するが、M1では未接続の操作を出さない。

M1の空の目標値は選択可能な標準ラベル、Planは既存単一Transcriptを維持する。Goal本文とPlanをまたぐ選択は、実本文を結合するM2で具体化する。

## 8. 対象ファイル

src/gigas/uiのPlanPanel、MessageComposer、ConnectionPanel、MainWindow、設定入口のapp/shell。関連既存テスト、初期UI/工程順/U06親/利用者docs。

## 9. 検証

対象のred/green、unit/integration、ruff format/check、ty、lock/build、文書リンク、Windows通常/小画面で配置と操作を確認する。結果は作業後に記録する。

D01: `uv run pytest tests/unit/test_plan_panel.py -q`で未実装属性のred→2 passed。表示順・空欄常設・Plan更新で目標値不変を既存試験へ統合。既存GUIのPlan回帰5 passed。担当ruff/format/ty成功。GoalのAPI・状態・架空の本文は追加しない。

## 10. 先送り事項

Goalの実接続はM2、次turn設定はM3。実モデル/実機、実IME・複数DPIの工程受入は既存のM3引継ぎを維持する。

## 11. チェックリスト

- [x] 意図と対象外を確認した
- [ ] TDD項目を実装・検証・コミットした
- [ ] 表示・品質gateと未検証範囲を記録した
- [ ] 仕様の整合をレビューした
