# 終了要求の単一所有 仕様書

## 1. 概要

### 1.1 目的

状態: **wip（設計確定・実装中）**。整理親仕様のM1として、停止理由、対象、完了判定を一つの終了処理へ集約する。ユーザーのM0承認とM1完遂指示を受け、2026-11-13に着手した。

### 1.2 起点 / source

| source | 内容 | path |
|---|---|---|
| 承認済み親仕様 | 停止のローカル受付、終端後一回回収、切断、状態寿命 | [unit054](../unit_054/KISS_SIMPLIFICATION.md) |
| 先行子 | 製品準備必須化、通知採用元と表示締切 | [unit055](../../complete/unit_055/PRODUCT_CONNECTION_ASSEMBLY.md)、[unit056](../../complete/unit_056/TURN_NOTIFICATION_LIFETIME.md) |

### 1.3 use case

| actor / boundary | 入力または状態 | 期待する観測結果 | 制約 |
|---|---|---|---|
| 利用者 | 中断・Goal破棄 | 受付後すぐ戻り、実終了は状態通知で判定 | 停止中の入力・追加指示・回答・新規実行は禁止 |
| runtime | Goal completeと最終応答 | 最終応答を残し、終端後の回収成功でready | 終端待ち中の端末書込は許容 |
| 利用者・アプリ終了 | 切断 | 終端を待たず所有I/Oとtransportを回収 | 任意の子孫プロセスやGoal休止永続化は保証しない |

## 2. 対象範囲

現在turn・未解決start・終了要求の記録、単一終了runner、停止と自然完了、切断、サービスの停止受付記録とshutdown、対応する既存試験・公開説明を変更する。

## 3. 対象外

保存・入力値検査・サービス操作配送構造の整理、新しいタイマー・汎用manager・完了待機API、PR・push・mergeは含めない。実runtime・実機受入は製品green後に親担当が実施する。

## 4. 関連 docs

- [実行管理](../../initial/02-runtime.md)
- [切断](../../initial/09-control-flows.md)
- [API](../../../docs/codex.md)
- [GUI](../../../docs/codex-gui.md)

### 4.1 前提・着手条件

unit056の構造変更・完了移動を24af62cで確認し、cleanな`codex/kiss-simplification-spec`で開始する。仕様設計・M1実装は承認済み。

### 4.2 冒頭の調査・設計

現行の`_send/_set_goal`による早期ready/stopped、`_status`による補正、通知内の終了await、close自己待機を確認した。採用transportにはRPC期限があるため停止専用期限を追加しない。外部通知の任意順再送を支える保証は追加しない。

### 4.3 工程内の分割

D01で停止・自然完了・開始受付の所有を揃える。D02で同ownerの切断収束、D03でサービスの受付記録・shutdownを合わせる。各itemをred→greenし、必要な構造整理はgreen後の別commitにする。

## 5. 振る舞い仕様

| 振る舞い | 入力・状態 | 期待結果 | 備考 |
|---|---|---|---|
| 停止・破棄 | stop/clear | ローカル受付で戻りstopping。Goal操作と既知turn中断を独立に一回試み、終端後の回収とGoal操作完了後にstopped | 通知consumerは終了処理を待たない |
| 自然完了 | Goal complete | 入力を閉じ最終応答と終端を待ち、一回回収後ready | 明示stop/clearで同要求を中断へ変更 |
| 開始中の停止 | 準備中・未解決start | 未送信なら送信抑止、送信済みなら実IDで中断 | 先行終端後も開始応答のIDを返す |
| 通常会話・Goal turn間 | completed | 表示を締め、通常会話はready。active Goalはturn間の入力保留 | 終了要求なしの成功turnに回収を追加しない |
| 失敗・中断 | failed/interrupted | 必要ならGoal pauseし、回収後stopped | 終了済みturnをinterruptしない |
| 正常停止後の合法late turn | turn/started | 入力を戻さず中断し終端後回収 | item/質問から未知turnを採用しない |
| 終了操作失敗 | Goal操作・interrupt・回収失敗 | errorを保持し新規実行を拒否。開始済み独立I/Oは回収 | 以後のlate turnのID・終端・表示は受け取るが、新しい中断・回収を開始しない。closeは保持した現在IDを使用できる。復旧は明示切断 |
| 切断 | close | 準備・開始・Goal lock・終端待ちを打切り、可能な中断と公開端末回収を経て必ずtransport close | 同対象へ発行済みのinterrupt/回収は共有し、失敗済み試行も自動再試行しない |

一回の回収は全ページ列挙・terminate・空一覧確認である。Aの回収開始前に合法Bが始まれば対象をBへ進め、B終端後に回収する。Aの回収が開始済みなら完了まで共有し、その結果をBに流用しない。過去全turnの回収キューは持たない。自然完了後のclearはGoal操作の変更であり、同対象の回収を繰り返さない。

サービスのstop/clear成功記録は`outcome=accepted`とし、RPC配送・実停止の完了とは扱わない。shutdownは入力を閉じ、stopを挟まずcloseする。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | D01 停止受付は即時に戻り、自然完了・中断・開始受付を同じ終了対象として終端と一回回収まで新規実行を閉じる | behavior | unit | pause保留中のstop返却と独立interruptを最初のredにする。旧stop返却=RPC回収完了・complete直後回収の期待を改訂 |
| refactor-skipped | D02 切断は未完の開始・終端・Goal lock待ちを打切り、既存回収を共有してtransportを閉じる | behavior | unit/integration | 自己待機・呼出元取消し・失敗後late turn・同対象の二重回収を回帰確認 |
| refactor-skipped | D03 サービスは停止要求のローカル受付を記録し、shutdownではcloseへ直接進む | behavior | integration | ServiceResult型は増やさず、状態通知を実終了の根拠とする |
| refactor-skipped | D04 切断直後に配達済みの開始通知が届いても、終端待ちへ戻らずcloseする | regression | unit | 独立reviewで切断理由が中断へ上書きされる競合を指摘。D03完了後に一件追加 |

旧`test_turn_operation_failures_share_status_id_with_exception`のstop例は、stop自身のRPC失敗例外という契約を廃止したため除いた。失敗は`test_unconfirmed_terminal_cleanup_keeps_execution_closed`で状態通知として確認する。自然completeの`cleanup_pending`総当たりは早期回収を廃止したため削除し、終端後に回収を保留する3操作の代表例へ統合した。shutdownのruntime_stop失敗2形式は追加stopの廃止に伴って削除し、直接closeと入力close失敗の回収へ分けた。

開始通知なしの未知turn採用、終端済みturnの任意再送、終了通知の重複件数を網羅する模擬入力は保証しない。残る取消し・通信不明・準備失敗・権限境界の試験は維持する。

## 7. 設計メモ

接続が現在turn・未解決start一件・終了要求一件を所有する。turnはID・終端結果・中断試行を持ち、未解決startが先行turnを参照する。終了要求は理由・対象・Goal操作・回収結果・stickyな失敗を持つ。動作中だけrunnerを保持する。全完了/中断ID集合、stopping/finishing_goalと回収task履歴の組合せを除去する。

D01はde3956eで振る舞いを変更し、ce7fd75で重複する送信中flag・ローカルsubmittedを開始受付記録へ統合した。使用しなくなった旧回収wrapperも削除した。

終了要求が存在する間の成功状態は終了ownerだけが決める。開始側のfinallyや表示status関数は補正しない。close呼出元と終了runner自身は所有task回収から除き、取消された操作は閉鎖済み接続のcloseを再awaitしない。

## 8. 対象ファイル

| path | change | 内容 |
|---|---|---|
| `src/gigas/runtime/codex/connection.py`、`models.py` | modify | 状態所有・終了・公開docstring |
| `src/gigas/services/codex.py` | modify | 受付記録とshutdown |
| `tests/unit/test_codex_connection.py`、関連integration | modify | 観測契約の改訂と回帰 |
| `docs/codex.md`、`docs/codex-gui.md` | modify | 受付・実終了・切断の説明 |

## 9. 検証

局所pytestは`--basetemp=tmp/pytest-u057`を使う。実接続・実機は親担当へ引き渡し、子をwipに保つ。

| command | result | notes |
|---|---|---|
| `uv run pytest tests/unit/test_codex_connection.py -q -k test_stop_acceptance_does_not_wait_for_goal_or_turn_completion --basetemp=tmp/pytest-u057` | red: 1 failed / green: 1 passed | Goal pause保留でstopが返らない失敗を確認。接続unit全体は161 passed / 0.75s |
| `uv run pytest tests/unit/test_codex_connection.py -q -k test_close_collects_pending_terminal_cleanup --basetemp=tmp/pytest-u057` | red: 1 failed / green: 1 passed | 回収中closeがterminateを取消しwriterを残す失敗を確認。Goal lock待ちと終端未着のclose、失敗済み回収の非再送も検証 |
| `uv run pytest tests/integration/test_codex_service.py -q -k test_shutdown_closes_without_submitting_an_additional_stop --basetemp=tmp/pytest-u057` | red: 1 failed / green: 1 passed | shutdownで追加stopが呼ばれる失敗を先に確認。停止受付の記録と公開phaseでの再開待ちも確認 |
| `uv run pytest tests/unit/test_codex_connection.py -q -k test_queued_turn_start_cannot_restore_terminal_wait_during_close --basetemp=tmp/pytest-u057` | red: 1 failed / green: 1 passed | 配達済みstartedが切断理由を降格し終端待ちへ戻るTimeoutErrorを確認後、close優先で修正 |
| `uv run pytest tests/unit/test_codex_connection.py -q --basetemp=tmp/pytest-u057` | 163 passed / 0.43s | 最終回帰前のD04確認 |
| `uv run pytest tests/integration/test_codex_service.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_transport.py -q --basetemp=tmp/pytest-u057` | 146 passed / 14.29s | D03関連回帰 |
| ruff format/check、ty、lock check、build、diff check | not run | 公開API・型・配布gate |
| 公開docs/docstring読解 | not run | 実装との意味整合 |
| 実runtime・実機 | not run | 親担当が製品green後に実施 |

## 10. 先送り事項

実runtime有限writer・同接続再開・入力解除の受入は親unit054のM1受入へ引き渡す。保存・入力検証・操作配送の構造変更は後続子が担当する。

## 11. チェックリスト

- [x] 対象範囲と対象外を確認した
- [x] TDD Test Listを具体化した
- [ ] 検証結果と未実行理由を記録した
- [ ] 公開API・型・配布gateを記録した
- [ ] 実接続受入へ引き渡した
