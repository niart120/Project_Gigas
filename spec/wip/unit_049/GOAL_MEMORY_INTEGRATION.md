# U06 M2 Goal・記憶・記録の結合

## 目的と状態
2026-11-13の依頼によりM2へ着手。起点b8a296f、codex/u06-m0-goal-design、clean。M0とM1の契約を製品へ結合する。親はwipを維持。

## 対象と対象外
Goal開始・目的更新・自律turn・一時停止・同一接続再開・破棄・完了、run/Grant/MCP寿命、memory限定書込、公開対話記録、Goal UI。M3の認証拡張・工程実機受入、U07終了/U08再起動復旧/U09記憶整理/U10長時間、PR・マージは対象外。

## 関連・着手条件
[親](../unit_011/GOAL_EXECUTION.md)、[M0](../../complete/unit_044/GOAL_INTERACTION_DESIGN.md)、[最新M1](../../complete/unit_048/NEXT_TURN_MODEL.md)、[UI](../../initial/10-ui-interaction.md)、[記録](../../initial/04-agent-memory.md)。採用runtime0.167.0の公開schemaとM0隔離結果を照合し、製品結合を今回の試験で確認する。

## 契約と根拠
Goalは公開状態を正本としgoal/setだけで開始。目的更新は状態を維持しsteerを重ねない。activeのturn間は入力をsuspendしGoalの同run/Grantを保持。停止はGrant失効が先、pause/clearとturn/interruptを独立して試行する。遅い通知で操作権を戻さない。再開は現在Frameと旧処理収束を確認し同threadに新MCPを公開する。paused中の通常会話に入力許可を出さない。

Goal本文・Planは各Transcript内で選択/コピー/更新中スクロール保持。本文を跨ぐ独自選択は作らない。目的更新だけで既受信Planを消さず、Goal改訂対応を推測しない。目標モード切替は原稿を保持しAPIを呼ばない。成功応答時だけ提出時と同じ原稿を消す。

不足時だけguide/currentを生成し既存保持。Codex書込はmemoryだけ、原記録はアプリが既存RecordingServiceへ非同期提出。観測は即時最新cache、入力・観測・保存の分離を維持。MCP300秒とGoal寿命を混同しない。モデル選択をGoalへ接続する場合は公開thread/settings/updateを使い現turnを変えない。独自継続ループなし。

## TDD Test List
| 状態 | item | 検証 |
|---|---|---|
| refactor-skipped | D01 記憶入口・限定起動環境・対話保存 | 既存storage/recording/launch試験改訂 |
| refactor-skipped | D02 公開Goal操作・自律turn・配送と停止競合 | 既存connection/transport試験改訂 |
| refactor-skipped | D03 Goal run/Grant/MCP・通常会話・対話記録 | 既存service/input/settings試験改訂 |
| green | D04 Goal操作と本文保持 | 既存GUI/Plan試験改訂 |

## 対象ファイル
runtime/codex、services、storage、ui、app/shell、prompts、既存tests、docs、初期設計と親仕様。

## 検証
各担当のred/greenと全体gateを実行。下記に確認済み範囲と未確認を分ける。

## 先送り
実機工程受入はM3（USB Video Device、COM8 CH552 9600）。実summary・実IME・複数DPIと認証拡張は従前どおりM3。M2の未確認は結果に応じて明記する。

## 完了条件
- [ ] D01〜D04実装と検証
- [ ] 要件ごとの根拠と残件、品質gate、仕様整合
- [ ] コミット完了


## D01 記憶・保存・起動環境の検証記録

不足時だけguide/currentを生成し、既存内容を保持するProjectWriter.ensure_memoryと、既存の保存所有・lockを使うRecordingService.ensure_memoryを追加した。notesディレクトリはCodexが必要時に作成する。対話はdialogue.*の共通envelopeで、payloadにthread_id、分かる場合のturn_id/run_id、提出時のreceived_at、選別済み公開contentを持つ。保存時のoccurred_atと受信時刻を分け、提出時にcontentを複製する。原記録はCodexの書込先へ含めない。

既存storage/observation試験へ既存メモ保全・対話順序・内容複製・失敗通知・取消し後drain回収を統合した。新API未実装によるAttributeErrorのredを3件確認してから実装した。起動環境はStdioTransportのunset_envを使い、子プロセスに限りCODEX_WINDOWS_REGISTERED_COREとCODEX_WINDOWS_SANDBOX_PACKAGE_FAMILYを除外する。親環境や認証を変更しない。

| command | 結果・検証範囲 |
|---|---|
| `uv run pytest tests/integration/test_observation_storage.py tests/integration/test_observation_service.py tests/integration/test_codex_transport.py -q` | 95 passed。保存と起動環境除去の既存試験を改訂・統合 |
| 対象ファイルの `uv run ruff format` / `uv run ruff check`、`uv run ty check --no-progress` | 成功。公開型・docstringと所有境界を確認、ignore/cast追加なし |
| `uv build` とwheelのzip内容確認 | 成功。guide.md/current.md/play.mdを配布物へ同梱 |
| `uv run python -X utf8 tmp/u06-m2-storage/permission_probe.py` | 製品launch/open/thread準備を使う採用0.167.0の検証。report-product-layout.jsonでthread設定受理、memory内書込、親guide読取、親events書込拒否・原本不変を確認 |

実runtime検証はプロセスcwdを隔離root/runtime、原記録先をroot/archive-product-layout、thread cwd/runtimeWorkspaceRootsをarchive/memoryとし、製品と同じ分離を使った。モデル呼出しとゲーム入力は行っていない。command/execはthreadに属さない公開実行APIであり、この成功を自律モデルの全ツール経路の受入へ拡張しない。

通常の検証ホストsandboxでは共有Codex homeのsqlite状態DBへの書込が拒否されinitialize前に終了した。許可された同じ検証の昇格実行で確認し、製品sandboxの拒否と区別した。対照実験としてprocess cwdを原記録先archiveに置いた場合は親eventsへの書込が許可された。M0のprocess cwd=memoryと、製品のruntime/原記録先を分離した配置では拒否される。起動cwdを原記録先へ変更しないことを境界として残し、任意配置の隔離を保証しない。

## D02〜D04の検証とレビュー

Goal API未実装、Goal UI signal未実装のredから実装。D03は既存readyでrunが終了するredを確認し、既存サービス試験へ改訂を統合した。記録先cwdの変更に伴い既存試験の参照をmemoryの親へ変更し、日本語の公開会話を保存するため読取encodingをUTF-8へ明示した。

| command | 根拠 |
|---|---|
| `uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u049-final` | 779 passed（最終受入実行 `tmp/pytest-u049-acceptance`）。通常turn、停止、配送、設定適用、入力/観測/保存の既存回帰を含む |
| `uv run pytest tests/integration/test_codex_service.py -q --basetemp=tmp/pytest-u049-service-final` | 62 passed。最後のGoal応答前readyでrunを保持する変更を再検証 |
| `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress` | 成功。公開型/docstringの所有・None境界、Protocol/fake一致を確認 |
| `uv lock --check` / `uv build` / `uv run python scripts/check_package.py` | 成功。依存追加なし。隔離install、資材、capture worker終了、module/script起動と終了 |
| `uv run python tmp/u06-m2-runtime/product_probe.py` | 採用0.167.0と製品CodexServiceThread/CodexConnection、loopback Responses、固定認証/catalog、合成映像・模擬入力。run-20261113-163051/report.jsonで成功 |

実runtimeプローブは2自律turnの同run保持、pause/interrupt/neutral、同threadのunsubscribe/resume、新MCP endpoint/新run、再開後の入力、clear、公開対話/入力/観測の対応を確認。途中の推論レベル変更で実送信はlow→low→medium→mediumとなり、現turnの後続推論を変えず次自律turnと再開後へ反映した。ホストモデルや実機の試験ではない。runtimeとサービスは正常終了した。

隔離homeにWindows sandbox設定がない場合、runtimeはworkspace-write要求をreadOnlyへ縮退し、製品は入力前に設定不一致として拒否した。成功プローブは隔離homeの`[windows] sandbox="elevated"`を使用。アプリが共有home設定を自動変更したり権限を広げて再試行したりはしない。新規home準備はM3認証/起動受入へ引き継ぐ。

Qtは固定通知で[通常画面](assets/normal-paused.png)と[800×600](assets/small-paused.png)を確認。Goal/Plan各欄の長文選択、コピー、同じ目標の状態更新、本文末尾追記、Plan更新中のスクロール位置保持を既存試験へ統合した。実IME/複数DPIの受入とは区別する。

相互レビューの必須指摘は修正済み: 新規Goal拒否で既存通常runを止めない、clear後の遅い自律turnも中断、失敗/外部中断終端でGoalを休止、入力設定変更はGoal停止と独立して適用を継続、Goal応答前readyでも同run保持。記録は提出本文と配送結果を同じrequest_idへ対応付け、取得時刻と保存時刻を分離。request_idは既存コマンド単位の識別であり独自Goal ID・配送キューではない。

KISSレビュー: Goalとturn/操作権を巨大な状態列挙へ展開せず、既存停止/要求/通知/保存の経路を利用。自律継続はruntimeに委任し独自continue・再試行・監視・自動復旧なし。Goal本文とPlanの別標準文書境界を明示し、独自跨ぎ選択は実装しない。

最後の受入監査で、Goalがcomplete/blocked/usageLimited/budgetLimitedへ変化した後も質問/承認へ回答できる8ケースを既存試験でredにした。公開停止理由を保持して要求失効・停止意思固定・turn中断する修正後は成功。提出済み回答の遅い完了でも実行状態へ戻らない。runtime114件＋service62件、合計176件成功。実runtimeプローブもrun-20261113-163521/report.jsonで再成功し、処理は終了済み。

D01コミット7834b2a、D02コミット9a58856。最終受入全unit/integrationは779 passed、ruff/format/ty/lock/build成功。配布物起動検査は同依存・同起動経路で成功、最終runtime修正後のbuildも成功。
