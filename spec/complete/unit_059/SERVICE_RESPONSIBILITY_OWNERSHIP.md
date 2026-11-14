# サービス責務と状態所有の整理 仕様書

## 1. 概要

### 1.1 目的

完了した子仕様。保存資源と入力 run の操作・状態・寿命を所有者へ移し、Qt サービスにある横断参照を取り除いた。公開契約を変えない構造整理であり、単なる DTO 移動や大きい async core への一括移設は行わない。

### 1.2 起点 / source

| source | 内容 | path |
|---|---|---|
| user request | 状態と責務を往復し、帰属を見つける | [親仕様 5.9](../../wip/unit_054/KISS_SIMPLIFICATION.md#59-巨大クラスの責務状態所有を分ける) |
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

- [親仕様](../../wip/unit_054/KISS_SIMPLIFICATION.md)
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
| refactor-done | D02 run 許可と終端の単独所有 | characterization | integration | 公開 HTTP・記録・配送結果、既存の入力 close / 保存失敗境界を維持 |

## 7. 設計メモ

Resources の準備は ThreadConfiguration と準備済み Observation / Recording を返す。Run.begin は InputGrant を返す。サービスは同じ resources_lock 内で値を受け渡し、待機状態として保持しない。Resources は Run を、Run は Resources・writer・MCP・接続・Qt を参照しない。

状態の削除は独立 thread ID と service の run task 登録、縮小は GoalState → status。writer / recording / observation / MCP、Grant、発行前の停止意思、質問待ち、配送不明は必要な状態として操作と一緒に移す。非blocking質問でもゲーム入力を保留するため waiting_answers は running phase から導出できない。unit058 後のプロセス内実験では、この条件だけを除いた既存 HTTP 試験が入力を再許可して失敗した。

D01 では、保存資源を Resources へ移し、サービスの独立 thread ID を削除した。初回 `record_thread` は Observation / Recording を返し、同じ service lock 内で run を開始して MCP へ bind する。`test_goal_keeps_one_run_across_turns_and_stops_on_pause` の直接状態・手作り phase 期待は、`test_goal_service_pause_conversation_resume_and_recording` の実 CodexConnection・保留した Goal 応答・HTTP 入力・run ID 記録へ統合した。thread 保存失敗の試験も公開 send と保存 failure signal / run_started 記録へ変更し、旧 private 属性の代替取得口は作らなかった。

D01 は `7b04abf` に記録し、独立レビューで Resources の逆参照、lock を外れた組立、thread ID の消失、終了順の阻害指摘なし。D02 では Run.prepare を同期処理にし、準備の await 前に許可の初期値を決める。その後の stop を後続準備が上書きしない。Run.begin の戻り値だけを Resources.bind へ渡し、サービスに Grant を保持しない。

run 終端 task は Run にのみ保持し、次の準備・切断・サービス最終終了で drain する。独立レビュー時に終端 task へ例外を注入する追加試験と、例外専用の回収拡張を検討したが不採用とした。実 InputService.finish_run は入力/観測の失敗を `gather(return_exceptions=True)` で回収し、保存は RecordingService の別 task へ渡す。通常の入力・符号化・保存障害が finish task の例外になる経路はなく、他所有者からの task 取消しもない。専用の `_failed` / `_finished` callback を撤去し、予期せぬプログラム上の例外は必要な drain でそのまま顕在化させる。InputService.close の実機器 close 失敗と、RecordingService の保存失敗通知は維持した。到達根拠のない D03 や故障組合せ試験は作っていない。

AST で `3cf71c3` と最終コードを比較した結果、CodexServiceThread は class 本体 896 → 673 行、初期属性 39 → 22。Resources は 193 行 / 9 属性、Run は 148 行 / 11 属性となった。属性数は依存参照を含み、所有者参照・依存参照が加わるため全体の属性総数削減とは扱わない。実際に消した状態は独立 thread ID と GoalState 全体の複製、二重の task 登録であり、残る資源・入力政策は操作と寿命を一緒に移した。サービスから所有者の private 属性を参照する箇所は 0 件で、返り値の Observation / Recording / Grant をサービス属性へ再保持していない。

## 8. 対象ファイル

| path | change | 内容 |
|---|---|---|
| `src/gigas/services/codex.py` | modify | 所有者の組立、Qt と世代の配送 |
| `src/gigas/services/_conversation_resources.py` | new | 保存・観測・MCP の資源所有 |
| `src/gigas/services/_conversation_run.py` | new | run 許可・終了の所有 |
| `tests/integration/test_codex_service.py` | modify | 既存試験の境界整理 |
| `spec/complete/unit_059/SERVICE_RESPONSIBILITY_OWNERSHIP.md` | new | 設計・状態移管・検証 |

## 9. 検証

関連 311 試験に加え、InputService / observation / recording / project / MCP の該当試験を実行する。ruff、ty、lock、build、公開 docstring と文書参照を確認する。

| command | result | notes |
|---|---|---|
| `uv run pytest tests/integration/test_codex_service.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_transport.py tests/unit/test_codex_connection.py -q --basetemp=tmp/pytest-u059-baseline --tb=short` | 311 passed / 14.91s | 変更前の動作基準 |
| `uv run pytest tests/integration/test_codex_service.py -q --basetemp=tmp/pytest-u059-resources --tb=short` | 64 passed / 11.92s | D01 のサービス・資源境界 |
| baseline と同じ 6 ファイル、`--basetemp=tmp/pytest-u059-resources-regression --tb=short` | 310 passed / 14.78s | D01 後。1 試験は重複する状態確認を HTTP/記録へ統合 |
| 対象 Python の `ruff format` / `ruff check`、`uv run ty check --no-progress` / `git diff --check` | passed | D01 の形式・型・差分 |
| `uv run pytest tests/integration/test_codex_service.py -q --basetemp=tmp/pytest-u059-run --tb=short` | 64 passed / 12.00s | D02 の run 組立・入力保留・終了 |
| baseline と同じ 6 ファイル、`--basetemp=tmp/pytest-u059-run-regression --tb=short` | 310 passed / 14.95s | D02 抽出後。終端専用 callback の削除前 |
| `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress` | passed | Python 223 ファイル、型境界 |
| `uv lock --check` / `uv build` / `git diff --check` | passed | lock 整合、sdist / wheel、空白 |
| `uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u059-full --tb=short` | 846 passed / 60.91s | callback 撤去後の最終コード。project / input / observation / recording / MCP を含む全体回帰 |

公開 API の引数・戻り値・DTO は維持し、公開 DTO の移動は行わなかった。必要な戻り値型を private 所有者の境界へ明示し、型の ignore / cast / Any、追加依存、互換層は導入していない。公開 docstring と docs/codex.md・docs/codex-gui.md・docs/recorded-observation.md の所有・配送・停止・保存・終了順を読み、公開説明を変える差分がないことを確認した。

実装はD01 `7b04abf`、D02 `f063856`。独立レビューと親担当のソース・試験レビューで阻害指摘なし。親担当が最終コードを固定した状態で次節の実機受入を実行した。

### 9.1 実runtime・実機受入

採用runtime 0.167.0、既存認証済みの隔離home、gpt-6.1-sol / low、現行設定のCH552・COM8とUSB映像を使用した。新規login・sandbox setup・自動承認・ユーザー回答は行っていない。

```powershell
uv run python -X utf8 tmp/u054/m1_acceptance.py --execute --mode capture --settings C:/Users/train/AppData/Local/GIGAS/settings.toml
uv run python -X utf8 tmp/u054/m1_acceptance.py --execute --mode input --settings C:/Users/train/AppData/Local/GIGAS/settings.toml --codex-home E:/documents/VSCodeWorkspace/Project_Gigas/tmp/u06-m3/fresh-20261113-180214-566284/home --direction LEFT --hold-ms 500 --reviewed-frame E:/documents/VSCodeWorkspace/Project_Gigas/tmp/u054/m1-capture-20261113-231328-004204/after-5s.png
```

captureの結果は`tmp/u054/m1-capture-20261113-231328-004204/report.json`、inputの結果は`tmp/u054/m1-input-20261113-231603-615948/report.json`で、いずれもpassed。検証器の入力制限はサービス構築時にInputServiceへ適用し、新しいRunが同じ制限付きInputServiceを参照する。サービス構築後に参照を書き換える方法は使わない。thread IDは実thread開始応答から観測した。

| 観測 | 結果と限界 |
|---|---|
| 短い入力と停止 | LEFT 500ms要求を一回だけ受け、送出中に停止。非中立送出完了から中立送出完了まで203ms。機器ACKや最悪停止時間の測定ではない |
| ローカル受付と回収 | stop受付264178.031、turn終端264178.406、stopped 264178.562。終端後の1ページ列挙・1件終了・空一覧再確認を製品が実行 |
| 有限書込 | 最大300回・100ms間隔の前景書込は136行で停止。停止後1秒と同接続・同threadの再開後で不変。検証器から終了RPCを送っていない |
| 自然最終応答 | 再開後に最終マーカーを受信。自然終端264197.656→ready 264197.687、空一覧の列挙・再確認のみで再中断なし |
| 映像 | 親担当がbefore・after-stop・after-resumeを視認。入口右隣から入口へ1マス移動し、23:16:58の停止後と23:17:16の再開後で位置を維持。NPCの変化も確認。自動reportのphysical_release_confirmedは未測定のfalseのまま保持 |
| 終了 | service・settings・capture・serialを全回収、shutdown errorなし、所有PID残存0 |

この単位で変更した入力runと資源組立の受入であり、全機器・長時間運転の保証ではない。接続側を分離した後の受入は後続子と親で別に確認する。

最終の connection_audit 独立レビューで阻害指摘なし。Run の依存は InputService だけで、Resources への例外 callback・task 二重登録・他所有者の private 属性参照はない。必要点での drain、機器 close 失敗を扱う既存 finally、保存資源の回収を確認した。文書の参照先・仮テキストと public API / docs の整合は実装担当が確認し、実機記録と最終 complete 移動は親担当へ引き渡す。

## 10. 先送り事項

接続本体の HumanRequests / モデル / 環境 / 認証の所有分離は後続子。公開 DTO は依存の削減に必要な場合だけ本単位で移す。

## 11. チェックリスト

- [x] 対象範囲と対象外を確認した
- [x] TDD Test List を更新した
- [x] 削除と移動、所有者と終了順を確認した
- [x] 検証結果と未実行範囲を記録した
- [x] 公開境界・型・package gate を確認した
- [x] 親の最終レビューと実runtime・実機受入を受けた
