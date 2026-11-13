# サービス責務と状態所有の整理 仕様書

## 1. 概要

### 1.1 目的

着手中の子仕様。保存資源と入力 run の操作・状態・寿命を所有者へ移し、Qt サービスにある横断参照を取り除く。公開契約を変えない構造整理であり、単なる DTO 移動や大きい async core への一括移設は行わない。

### 1.2 起点 / source

| source | 内容 | path |
|---|---|---|
| user request | 状態と責務を往復し、帰属を見つける | [親仕様 5.9](../unit_054/KISS_SIMPLIFICATION.md#59-巨大クラスの責務状態所有を分ける) |
| preceding unit | 操作文脈と束縛関数、GUI 拒否の保存境界 | [unit058](../../complete/unit_058/SERVICE_OPERATION_DISPATCH.md) |

### 1.3 use case

| actor / boundary | 入力または状態 | 期待する観測結果 | 制約 |
|---|---|---|---|
| GUI | project・接続・入力設定・送信 | 従来の signal と配送結果 | Qt 受付と世代はサービスが所有 |
| runtime | 準備・thread 開始・公開イベント | 同じ保存物、MCP、入力許可 | Resources と Run は相互参照しない |
| 終了 | 停止・切断・アプリ終了 | 入力失効と受付済み処理の回収 | 失敗があっても残る回収へ進む |

## 2. 対象範囲

- ConversationResources に project / writer / Recording / Observation / MCP の取得・記録・回収を移す。
- ConversationRun に Grant・入力許可・質問と回答の保留・Goal 準備・run 終端 task の寿命を移す。
- 独立 thread ID を撤去し、ObservationService.thread_id を記録に使う。GoalState 全体は status に縮小する。
- 既存試験の private 属性参照を、公開受付・記録・HTTP または所有者の操作境界へ変更する。

## 3. 対象外

- runtime の停止・通知契約、保存形式、入力検証、GUI の操作変更。
- 新しい manager 群、汎用 registry、停止期限、close task、別 async core。
- 実 runtime・物理入力の実行は実装担当の範囲外。構造 green とレビュー後に親担当が現在の配線を受入確認し、本仕様へ追記する。過去の受入で代用しない。

## 4. 関連 docs

- [親仕様](../unit_054/KISS_SIMPLIFICATION.md)
- [Codex 利用手順](../../../docs/codex.md)
- [会話と機器の GUI](../../../docs/codex-gui.md)
- [保存 API](../../../docs/recorded-observation.md)

### 4.1 前提・着手条件

unit058 は `3cf71c3` で complete へ移動済み。着手時は clean、branch は `codex/kiss-simplification-spec`、3 状態の最大 unit は 058。関連 311 試験を変更前に実行し成功した。

### 4.2 冒頭の調査・設計

サービスの初期属性は依存参照を含めて 39 個。Resources と Run への移動だけで状態数削減とは扱わず、書込主体・取得と回収・分岐の所在で評価する。各所有者は他の private 属性へ触れない。project と接続の排他に使う同じ resources_lock はサービスに残す。

### 4.3 工程内の分割

1. D01: Resources の取得・準備・記録・回収と thread ID の独立保持廃止。
2. D02: Run の許可・開始・終了、Goal status 縮小、終端 task の二重所有廃止。
3. 影響範囲の gate と独立レビューを行い、親の最終レビューへ引き渡す。

## 5. 振る舞い仕様

| 振る舞い | 入力・状態 | 期待結果 | 備考 |
|---|---|---|---|
| project 切替 | 切断中 | 所有 writer を切り替え、失敗時は現所有を保持 | 接続判定と切替は同じ service lock 内 |
| 準備 | 明示送信・Goal 開始・再開 | Resources が設定と準備済み Observation / Recording を返す | サービス属性に返り値を複製しない |
| run 開始 | 実 thread ID と準備済みサービス | Run.begin が Grant を返し、Resources.bind で MCP へ関連付ける | 初回 thread callback も同じ lock 区間で組み立てる |
| 記録 | 公開本文・状態・配送結果 | Resources が ObservationService.thread_id と渡された turn / run ID で保存 | run 終了後も会話 ID を保持、session 回収で失う |
| 入力保留 | blocking / nonblocking 質問と配送中回答 | 質問待ちと配送中の両方が解消した後にだけ再許可 | unknown は保留を維持し、自動再送しない |
| 終了 | run 停止・次準備・切断 | 同期失効後、Run が一つの終端 task を所有して回収 | service._tasks へ重複登録しない |
| 切断・終了 | 正常または途中失敗 | 入力失効→入力/観測操作回収→runtime と endpoint 終了→符号化・保存→必要な writer 終了 | 選択 project は切断後保持、アプリ終了で閉じる |

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | D01 保存資源と thread ID を所有者へ統合 | characterization | integration | baseline 311 → 旧直接状態試験を HTTP 試験へ統合して 310 passed。人工的な red は作らない |
| todo | D02 run 許可と終端の単独所有 | characterization | integration | 公開 HTTP・記録・配送結果と既存故障注入で確認 |

## 7. 設計メモ

Resources の準備は ThreadConfiguration と準備済み Observation / Recording を返す。Run.begin は InputGrant を返す。サービスは同じ resources_lock 内で値を受け渡し、待機状態として保持しない。Resources は Run を、Run は Resources・writer・MCP・接続・Qt を参照しない。

状態の削除は独立 thread ID と service の run task 登録、縮小は GoalState → status。writer / recording / observation / MCP、Grant、発行前の停止意思、質問待ち、配送不明は必要な状態として操作と一緒に移す。非blocking質問でもゲーム入力を保留するため waiting_answers は running phase から導出できない。unit058 後のプロセス内実験では、この条件だけを除いた既存 HTTP 試験が入力を再許可して失敗した。

D01 では、保存資源を Resources へ移し、サービスの独立 thread ID を削除した。初回 `record_thread` は Observation / Recording を返し、同じ service lock 内で run を開始して MCP へ bind する。`test_goal_keeps_one_run_across_turns_and_stops_on_pause` の直接状態・手作り phase 期待は、`test_goal_service_pause_conversation_resume_and_recording` の実 CodexConnection・保留した Goal 応答・HTTP 入力・run ID 記録へ統合した。thread 保存失敗の試験も公開 send と保存 failure signal / run_started 記録へ変更し、旧 private 属性の代替取得口は作らなかった。

## 8. 対象ファイル

| path | change | 内容 |
|---|---|---|
| `src/gigas/services/codex.py` | modify | 所有者の組立、Qt と世代の配送 |
| `src/gigas/services/_conversation_resources.py` | new | 保存・観測・MCP の資源所有 |
| `src/gigas/services/_conversation_run.py` | new | run 許可・終了の所有 |
| `tests/integration/test_codex_service.py` | modify | 既存試験の境界整理 |
| `spec/wip/unit_059/SERVICE_RESPONSIBILITY_OWNERSHIP.md` | new | 設計・状態移管・検証 |

## 9. 検証

関連 311 試験に加え、InputService / observation / recording / project / MCP の該当試験を実行する。ruff、ty、lock、build、公開 docstring と文書参照を確認する。

| command | result | notes |
|---|---|---|
| `uv run pytest tests/integration/test_codex_service.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_transport.py tests/unit/test_codex_connection.py -q --basetemp=tmp/pytest-u059-baseline --tb=short` | 311 passed / 14.91s | 変更前の動作基準 |
| `uv run pytest tests/integration/test_codex_service.py -q --basetemp=tmp/pytest-u059-resources --tb=short` | 64 passed / 11.92s | D01 のサービス・資源境界 |
| baseline と同じ 6 ファイル、`--basetemp=tmp/pytest-u059-resources-regression --tb=short` | 310 passed / 14.78s | D01 後。1 試験は重複する状態確認を HTTP/記録へ統合 |
| 対象 Python の `ruff format` / `ruff check`、`uv run ty check --no-progress` / `git diff --check` | passed | D01 の形式・型・差分 |

## 10. 先送り事項

接続本体の HumanRequests / モデル / 環境 / 認証の所有分離は後続子。公開 DTO は依存の削減に必要な場合だけ本単位で移す。

## 11. チェックリスト

- [x] 対象範囲と対象外を確認した
- [ ] TDD Test List を更新した
- [ ] 削除と移動、所有者と終了順を確認した
- [ ] 検証結果と未実行範囲を記録した
- [ ] 公開境界・型・package gate を確認した
- [ ] 親の最終レビューを受けた
