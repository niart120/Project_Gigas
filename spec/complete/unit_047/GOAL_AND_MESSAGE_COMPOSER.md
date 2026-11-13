# 常設目標と一体型メッセージ入力 仕様書

2026-11-13の後続合意: 本書の検証実績は当時の範囲を保持する。現在の後続範囲は[U06親](../../wip/unit_011/GOAL_EXECUTION.md)を正とし、APIキー認証とモニター間DPI遷移はM3の対象外。次turn設定はM1、Goal結合はM2で完了済み。

## 1. 概要

U06 M1の追加修正。利用者の2026-11-13の指示を起点に、目標と今回の作業を別見出しで常設し、空欄時のMessageプレースホルダーを入力枠内へ置く。一体型入力欄は通常送信と途中指示の既存配送を使う。

状態: 実装・検証完了。D01は`7677ebb`、D02は`b9f1471`へコミットした。U06親はM2/M3未着手のwipを維持する。

## 2. 対象範囲

目標の空状態、履歴の外側見出し撤去、入力枠内のMessageプレースホルダー・本文・操作列・モデル/推論量表示と既存Codex設定への入口。実行中の原稿ありは最終指示に従い送信と小さい中断を両方表示する。

## 3. 対象外

Goal接続・Goal送信モード・pause/resumeはM2。候補モデル取得・会話中の次turn設定反映はM3。音声・添付・PR・マージは行わない。レターボックス/ストレッチ、U05の停止・設定保存・観測の責務分離は維持する。

## 4. 関連文書・着手条件

[初期UI](../../initial/10-ui-interaction.md)、[U06親](../../wip/unit_011/GOAL_EXECUTION.md)、[前回M1](../../complete/unit_046/CONTEXTUAL_DIALOGUE_UI.md)を引き継ぐ。開始branchはcodex/u06-m0-goal-design、HEAD 0ad2a09、worktree clean。既存PromptEdit、Transcript、PlanPanel、設定画面を使い、依存を追加しない。

## 5. 振る舞い仕様

| 対象 | 契約 |
|---|---|
| 目標/今回の作業 | 右側映像下で両見出しを常設。M1の目標本文は—。入力やPlanからGoalを捏造しない。Planの更新・終端・選択・コピー・閲覧保持は既存契約を維持 |
| 入力 | 一つの枠内に本文と下部操作列。空欄時だけ薄いMessageプレースホルダーを表示し、入力すると消える。固定バッジを置かず、案内文字は原稿・コピー・送信へ混入しない。History/Messageの枠外見出しは置かず、アクセシブル名は維持 |
| 操作 | 待機中は送信、実行中かつ原稿なしは同じ右端位置に中断。実行中かつ原稿ありは右端に送信と隣に小さい中断。中断送信中は重複操作を防ぎ原稿を保持。Ctrl+Enterは送信専用で空欄時の中断に転用しない |
| 配送 | 通常turn/steerの既存可否・成功証拠と原稿保持を再利用。IME、質問・承認、送信中の追記、失敗/不明を維持 |
| モデル/推論量 | 入力枠内は実接続の値を表示し、既存設定のCodexタブを開く。未接続は設定入口名のみ。現在接続へ未反映の保存値を実行中の値として表示しない。次turn反映を先行実装しない |

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | D01 目標と今回の作業が空/更新/終端でも常設され閲覧を保持する | regression | unit | 既存Plan試験を改訂、2件成功 |
| refactor-skipped | D02 一体入力が状態別操作・途中指示・原稿/IME保持・設定入口を満たす | regression | integration | 既存GUI/window/settings試験を改訂・統合。最終関連61件成功 |

## 7. 設計メモ

表示用のMessageComposerへ既存PromptEditと操作部をまとめる。配送・状態はConnectionPanelが所有し二重化しない。目標欄の表示とGoal API接続の時期を分ける。将来Goal送信モードでは枠内バッジで識別するが、M1では未接続の操作を出さない。

M1の空の目標値は選択可能な標準ラベル、Planは既存単一Transcriptを維持する。Goal本文とPlanをまたぐ選択は、実本文を結合するM2で具体化する。

## 8. 対象ファイル

src/gigas/uiのPlanPanel、MessageComposer、ConnectionPanel、MainWindow、設定入口のapp/shell。関連既存テスト、初期UI/工程順/U06親/利用者docs。

## 9. 検証

対象のred/green、unit/integration、静的検査・配布物、文書リンク、Windows通常/小画面の表示と操作を確認した。

D01: `uv run pytest tests/unit/test_plan_panel.py -q`で未実装属性のred→2 passed。表示順・空欄常設・Plan更新で目標値不変を既存試験へ統合。既存GUIのPlan回帰5 passed。担当ruff/format/ty成功。GoalのAPI・状態・架空の本文は追加しない。

D02: 部品未実装のred→部品3件green、既存GUIの途中指示6件と設定入口1件を未実装のredから結合。既存GUI/設定/小画面58件成功。最終は`uv run pytest tests/unit/test_message_composer.py tests/integration/test_codex_gui.py tests/integration/test_app_settings.py tests/integration/test_window_presets.py -q --basetemp=tmp/pytest-u047-render-final`で61 passed。通常原稿の選択/コピー、空欄Ctrl+Enterの中断誤操作防止、原稿ありの送信/中断併置、既存のIME・配送結果・要求寿命を維持。表示面の分離は専用部品に限定し追加の配送状態を持たない。

固定Messageバッジは採用せず、利用者の訂正に従いQt標準placeholderを使う。本文背景を一体面へ揃え、モデル名は小数文字幅を切り上げて省略判定と一致させた。Windows通常フォント/DPR 1.5で整数丸めによる末尾省略を再現し修正。拡大専用分岐・検証は追加していない。

| command | 結果・対象 |
|---|---|
| `uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u047-placeholder-final` | 743 passed。最終文字幅修正は上記61件とWindows対象1件で再確認 |
| `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress` | 成功、204 Pythonファイル。type-boundary-review/docstring-styleで公開引数・所有・None境界を照合 |
| `uv lock --check` / `uv build` | 成功、依存変更なし、wheel/sdist生成 |
| `uv run python scripts/check_package.py` | 成功、別環境install、資材・capture子プロセス終了、両entrypointの起動/終了 |
| `uv run python tmp/u06-m1/validate_docs.py` / `git diff --check` | 成功、参照・unit一意性・仮テキスト・差分。意味上の契約は文書と実装を読んで照合 |

Windowsの製品MainWindowへ固定通知と合成画像を渡し、[通常1280×720の空欄](assets/native-normal-empty.png)と[800×600の原稿あり](assets/native-small-draft.png)を同じフォント条件で確認した。薄いMessageが入力で消え、途中指示の受付成功後に戻ること、原稿あり時の送信/小さい中断、空欄時の右端中断、両見出しの常設とPlan中断表示を確認。fixture記録は対象turnへのsteerが1回・stopが1回。実モデルや機器へ接続した試験ではない。検証用ウィンドウは終了済み。

独立レビューで配送/停止提出中/原稿/IME/設定入口に必須修正なし。設定入口は既存I/O制約と同一ダイアログを共有し、送信・原稿を部品側へ二重所有させていない。通常フォントでの画素・モデル名幅の検証は描画不具合の回帰に限定した。

## 10. 先送り事項

Goalの実接続と本文の選択結合はM2、次turn設定はM3。設定入口は現在の保存/次接続契約までで、会話中の候補選択UIと次turn反映は未実装。実モデル/実機、実IME・複数モニターDPI遷移の工程受入はnot runとして既存M3引継ぎを維持する。単一画面のDPR 1.5での描画確認を複数DPIの受入へ広げない。M2以降・PR/mergeは今回not applicable。

## 11. チェックリスト

- [x] 意図と対象外を確認した
- [x] TDD項目を実装・検証・コミットした
- [x] 表示・品質gateと未検証範囲を記録した
- [x] 仕様の整合をレビューした

後続の[unit_048](../unit_048/NEXT_TURN_MODEL.md)で設定入口を選択ポップオーバーへ置換し、通常turnの次turn反映をM1へ前倒しした。本書はその前の検証履歴。
