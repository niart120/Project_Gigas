# 文脈内の質問・承認と常設Plan 仕様書

2026-11-13の後続合意: 本書の検証実績は当時の範囲を保持する。現在の後続範囲は[U06親](../../wip/unit_011/GOAL_EXECUTION.md)を正とし、APIキー認証とモニター間DPI遷移はM3の対象外。次turn設定はM1、Goal結合はM2で完了済み。

## 1. 概要

U06 M1の修正子仕様。2026-11-13に合意したQt試作を製品へ反映する。質問と判断対象を操作から切り離さず、会話の連続選択・コピー・閲覧位置を保つ。

状態: **実装・検証・コミット完了**。D01 `dffd19b`、D03部品 `315330f`、D02結合 `9d9ae43`、D04配置 `b8ea1b3`。U06親はM2/M3が残るためwipを維持する。

後続の[常設目標と一体型入力](../../complete/unit_047/GOAL_AND_MESSAGE_COMPOSER.md)で、目標欄の非表示、枠外見出し、独立した送信/中断列を置き換えた。本書の画像・検証はunit_046時点の結果として残す。

起点は利用者の再設計承認と「仕様反映からM1実装・検証・コミットまで」の依頼。前提は[旧M1](../../complete/unit_045/COMMON_DIALOGUE_UI.md)、[M0](../../complete/unit_044/GOAL_INTERACTION_DESIGN.md)と[U06親](../../wip/unit_011/GOAL_EXECUTION.md)。試作は根拠であって製品受入とはしない。

## 2. 対象範囲

左に会話と通常入力、右上に16:9のプレビュー枠、右下に常設Plan。質問・承認の専用操作部を会話の該当本文へ配置し、送信先選択と独立した承認欄を撤去する。通常文字サイズで小画面を確認する。文字拡大専用の例・検証・分岐を削除する。

## 3. 対象外

M2以降、Goalの製品制御・仮表示、PR作成・マージ、実機入力、新しい認証、再起動復旧、記憶保守。OSの通常の文字描画やDPI処理を壊して拡大検証撤去の代わりにしない。

## 4. 関連文書と着手条件

[初期UI設計](../../initial/10-ui-interaction.md)、[工程順](../../initial/05-implementation.md)、[公開API](../../initial/08-sdk-compatibility.md)。既存の要求ID・配送証拠・停止・非同期設定適用を再利用する。UIの変更でruntime/API契約を拡張しない。

着手時branchは`codex/u06-m0-goal-design`、worktreeはclean。採用依存はlock通り。独立担当はPlan/映像と文書を担当し、親が会話部品と結合・品質gateを所有する。

## 5. 振る舞い仕様

| 対象 | 契約 |
|---|---|
| 会話 | 単一文書の本文を文字として更新し、複数発言・質問をまたぐ選択とコピーを維持。選択方向、日本語・絵文字、既読位置を保ち、末尾で未選択の場合だけ追従する。操作部の出現・高さ変更・撤去も同じ規則に従う |
| 質問 | 本文と選択肢/自由入力を同じ時系列位置に表示。選択肢を展開して明示選択、許可された自由入力だけを受け付ける。一つの要求の必須回答が揃ったとき一括配送し、異なる要求を混ぜない。送信後は回答自体を残し、配送中・失敗・結果不明は明示。原稿は成功証拠まで保持する |
| 承認 | 対象本文の直下に許可/拒否、必要な他の応答は補助メニュー。要求が許す応答だけを表示。提出後は判断そのものを残し、実行成功とはしない。失効/中断で再回答できない |
| Message | 通常送信/途中指示だけを扱う。質問到着が原稿・選択・IMEを奪わない。Enter改行、Ctrl+Enter送信。通常原稿と質問原稿を転用・二重配送しない |
| Plan | 現turnの公開項目と任意説明を専用部品に表示。常設見出しは今回の作業、空は—。完了/進行中/未着手は公開項目を根拠とし、中断・終了は欄の状態として表示。終端時にHistoryにも残し、新turnで現在欄を空にする。旧turn通知は現在欄を上書きしない。更新中の選択・コピー・閲覧位置を保つ |
| Goal境界 | 明示Goal.objectiveと公開Planは別。内部turn目標の専用フィールドはなく説明を目標と断定しない。将来の目標段はM2で確定Goalを結合してから提供する |
| 映像 | 表示枠を16:9にし、枠内のレターボックス/ストレッチは既存設定に従う。元の取得画像と観測画像は変更しない |
| 停止・設定 | 中断は常に同じ場所で識別可能。U05の操作権失効・入力停止、最新キャッシュ即応、責務分離、中断しても提出済み設定適用が続く契約を維持する |

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | D01 文脈内操作の追加・撤去と追記で本文選択・コピー・閲覧位置が保たれる | regression | integration | 単一文書と既存差分更新を維持。inline 7件成功 |
| refactor-done | D02 質問・承認へその場で応答し通常原稿・要求寿命・配送証拠を保つ | regression | integration | 既存GUI/要求寿命/stdio往復を改訂、送信先comboと共有編集切替を撤去 |
| refactor-skipped | D03 常設Planが更新・中断・空状態でも正しい本文と閲覧位置を保つ | regression | integration | 既存差分更新を再利用。独立部品2件と既存GUIへ終端・訂正を統合 |
| refactor-skipped | D04 16:9表示枠と左右配置で既存プレビュー方式・通常小画面操作が成立する | regression | integration | 拡大専用ケースを通常文字サイズへ統合。画素・小画面・既存設定経路を確認 |

## 7. 設計メモ

本文の選択を別々のラベルへ分割しない。既存Transcriptの差分更新と要求レコードを使い、操作部だけを本文位置に配置する。汎用チャット基盤、独自の配送キュー、自動再送、Goalの代用状態は追加しない。必要な配置・更新契約は試験で確認し、試作のsetHtml全置換を製品へ移植しない。

本文は複数発言・質問をまたいで選択できる。選択肢のラベル/説明と回答欄は各ネイティブ部品内で選択・コピーし、本文から部品内までの連続ドラッグ選択は扱わない。送信後の展開表示は元の選択肢・説明・全回答を本文に戻す。既存項目の置換範囲内の選択は新しい文字境界へ収め、範囲外の選択と向き・閲覧位置を保持する。

## 8. 対象ファイル

`src/gigas/ui`のTranscript、要求表示、接続パネル、Plan、MainWindow、VideoPanel。既存unit/integration、初期UI設計、U06親、旧M0/M1参照、利用者docs。依存追加なし。

## 9. 検証

対象pytestのred/green、unit/integration全体、静的検査・配布物検査とWindowsの製品部品表示を確認した。実モデル・物理機器の再受入は今回のUI修正と区別する。

D01: `uv run pytest tests/integration/test_inline_transcript.py -q`。API未実装の5件redからgreen、スクロールバー出現時の横幅と空itemの同期layout通知の回帰を加えて7 passed。複数本文をまたぐ逆方向選択・コピー、操作部の追加/撤去/高さ変更、閲覧アンカー・末尾追従、Tab到達を確認。本文更新中の既存layout guardを共用し、遅延タイマーや位置clampによる回避は追加しない。担当ruff/format/ty成功。

D03: `uv run pytest tests/unit/test_plan_panel.py tests/integration/test_codex_gui.py -q`は35 passed。Plan部品未実装のredから2件green。既存GUI試験へ終端後の訂正・中断後の次turn・通常終了後の切断を統合し各red→green。正常終端で未完項目が残れば欄は「終了」、全項目が公開completedなら「完了」、中断時は「中断」。終端後の進行中ラベル/強調を外し、公開状態は変更しない。退避は現turn所有時だけ行い、別の退避フラグは持たない。部品のコミット後、既存接続パネルへの結合をD02でコミットした。

D02: 既存質問試験を会話内操作へ改訂し、`request_views`未実装のredを確認して結合。`test_codex_gui.py`、`test_answer_lifecycle.py`、`test_ui_control_lifetime.py`の41件が成功。stdio往復、複数質問一括配送、同名質問の分離、通常原稿とIME、成功/失敗/不明、中断・失効を維持。レビューで見つけた旧世代callbackの失効と、回答展開時の選択肢説明欠落も既存試験でred→green。先頭の回答を表示し、展開時に全質問・選択肢・回答を戻す。配送状態は既存RequestRecordを再利用し、二重の状態機械や再送を追加しない。

D04: 既存映像試験へ16:9枠と4:3画像の両モード描画を統合し、旧枠の縦横比不一致からred→green。3寸法×2モードで黒帯/伸長と元画像不変を確認。通常文字サイズの800×600で長文・Plan・2行入力・Tabでの承認到達・追加指示・中断を確認した。初期activation前にfocus操作する試験を、実利用と同じactivation待ちへ修正し製品の自動スクロール条件は緩めていない。VideoViewの既存描画処理は変更せず、外枠だけを16:9にした。文字拡大専用のテスト分岐を撤去し、製品srcに専用拡大ロジックがないことを検索と差分レビューで確認。

| command / 対象 | 結果と確認対象 |
|---|---|
| `uv sync --dev` / `uv lock --check` | 成功。採用依存とlock整合、依存変更なし |
| `uv run pytest tests/unit -q --basetemp=tmp/pytest-m1-unit-final` | 320 passed。Plan通常終了の最終修正は上記35件で再確認 |
| `uv run pytest tests/integration -q --basetemp=tmp/pytest-m1-integration-final` | 420 passed。既存U05操作権・停止・非同期保存/設定適用を含む |
| `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress` | 成功。201 Pythonファイル、型・docstring・整形 |
| `uv build` / `uv run python scripts/check_package.py` | 成功。wheel/sdist、別環境へのinstall、資材、capture子プロセス終了、両entrypointのウィンドウ起動/終了 |
| `uv run python tmp/u06-m1/validate_docs.py` / `git diff --check` | 867ローカル参照・46unitの一意性・仮テキスト・差分の検査成功。意味上の整合は文書と実装を読んで確認 |

Windowsの現行MainWindowへ固定通知・合成画像を渡す検証fixtureで、通常1280×720と小画面800×600を同じ11ptで確認した。[通常](assets/native-normal.png)、[小画面](assets/native-small.png)、[中断後の選択保持](assets/native-selection-stopped.png)。質問の明示選択と自由回答を一括送信、承認の拒否、短い回答表示、スクロールでの操作到達を確認。複数発言をまたぐ選択は中断時の追記後も位置・選択文字列を保ち、Ctrl+CでコピーしてMessageへ貼り付けた文字列が一致した。テスト用ウィンドウは終了済み。画像・通知は合成で、実ゲーム・実モデル・入力機器は起動していない。

KISS・型境界・docstringの独立レビューを実施。Planの重複退避による巻き戻り、旧接続の遅い回答、展開時の選択肢説明欠落は採用して修正。公開型にAny/cast/ignoreを追加せず、Qt所有関係で操作部品を回収する。通常フォント/DPI処理は維持し、拡大専用の旧3画像と試作分岐を撤去した。過去工程の検証実績自体は書き換えていない。

## 10. 先送り事項

M2の着手条件は、このM1修正とM0契約を基にGoal/run/Grant・guide/memory・対話記録の実装仕様を具体化すること。今回M2の子仕様・製品実装は作成していない。確定Goal.objectiveの表示、設定反映と自律turnの結合は各担当工程で扱う。

実モデルGoal・次turn設定・新規認証・実summary/activityと機器停止の工程受入はM3でnot run。対象はUSB Video Device、COM8・CH552・9600 baud。通常turnの既存実接続結果を、このカードUIやGoalの実機受入へ読み替えない。実IME・複数モニターDPIもnot runで、合成preedit/Qt試験とは区別する。長時間履歴・300秒実待機はU10、計画的終了はU07、再起動復旧はU08、記憶保守はU09。PR/mergeは今回not applicable。

## 11. チェックリスト

- [x] 合意案・対象外・既存契約を確認した
- [x] 既存試験を改訂しTDD項目をコミットした
- [x] 製品表示・gate・未検証範囲を記録した
- [x] 文書・リンク・KISSと実装整合を再レビューした
