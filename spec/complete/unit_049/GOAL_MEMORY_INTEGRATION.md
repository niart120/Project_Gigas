# U06 M2 Goal・記憶・記録の結合

## 目的・状態
2026-11-13の依頼によりM0/M1の契約を製品へ結合し、実装・検証を完了した。起点b8a296f、branch codex/u06-m0-goal-design、着手時clean。U06親はM3未着手のためwipを維持する。

## 対象と対象外
Goal開始・目的更新・自律turn・一時停止・同一接続再開・破棄・完了、run/Grant/MCP寿命、memory限定書込、公開対話保存、Goal UIと自律turnへの設定反映を扱う。M3の認証拡張・実機工程受入、U07終了/U08再起動復旧/U09記憶整理/U10長時間、PR・マージは対象外。

## 関連・着手条件
[親](../../wip/unit_011/GOAL_EXECUTION.md)、[M0](../../complete/unit_044/GOAL_INTERACTION_DESIGN.md)、[最新M1](../../complete/unit_048/NEXT_TURN_MODEL.md)、[UI](../../initial/10-ui-interaction.md)、[記録](../../initial/04-agent-memory.md)。採用runtime0.167.0の公開schema・M0隔離結果と製品結合の証拠を区別する。

## 最終契約と根拠

Goalは公開状態を正本とし、goal/setだけで開始する。目的更新は状態と利用量を維持し、同文steerを重ねない。activeのturn間は入力を保留して同じrun/Grant/MCPを保持する。停止ではGrant失効を先行し、Goalのpause/clearとturn中断・公開端末回収を独立して試行する。遅い通知・回答で入力許可を戻さない。再開は旧処理の収束と現在Frameを確認して同じthreadへ新MCPを公開する。paused中の通常会話にゲーム入力を許可しない。

turn/interruptの終端だけでは、thread内の同期exec_commandも終了しないことを実測した。停止では公開thread/backgroundTerminals/listの全ページを取得し、processIdごとにterminateして、最後に一度一覧の空を確認する。接続が一つの共有taskで回収を所有し、回収中はstopping、未確認失敗はerrorとして新規送信・再開を拒否する。Goal自然停止、失敗・中断、承認cancel、遅いturnも同じ経路を使う。監視・再試行・OS PID終了は追加しない。公開一覧に属さない任意の子孫プロセスや外部書込の終了までは保証しない。

Goal本文とPlanは別の標準Transcript内で選択・コピー・更新中スクロール保持を行う。独自の跨ぎ選択は作らない。目的更新だけで既受信Planを消さず、公開通知にないGoal改訂との対応を推測しない。目標モードの切替は原稿を保持しAPIを呼ばない。受理成功でモードをオフへ戻し、提出時と同じ原稿だけ消す。編集中の別原稿、失敗・受付不明の原稿は保持する。

不足時だけguide/currentを生成して既存内容を保持する。notesは必要時にCodexが作る。書込はmemoryに限定し、原記録はアプリが既存RecordingServiceへ非同期提出する。公開対話はthread/turn/run ID、提出と配送を対応付けるrequest_id、提出時received_at、保存時occurred_atを記録する。本文・公開summary・activityは項目完了または中断時の部分スナップショットを保存し、未送信原稿・秘密入力・内部推論全文を保存しない。

U05の即時最新cache観測、入力・観測・保存の分離、提出済み設定適用が停止と独立して続く契約を維持する。MCPの300秒は各呼出しの期限で、Goal全体の期限ではない。Goalのモデル・推論レベル変更は公開thread/settings/updateを使い、受理後の次turnへ反映して現在turnを変えない。独自継続ループなし。

## TDD Test List

| 状態 | item | 既存試験への反映 |
|---|---|---|
| refactor-skipped | D01 記憶入口・限定起動環境・対話保存 | storage/recording/launchへ保全・順序・失敗・取消しを統合 |
| refactor-skipped | D02 公開Goal操作・自律turn・配送と停止競合 | connection/transportの要求寿命・遅い通知を拡張 |
| refactor-skipped | D03 Goal run/Grant/MCP・通常会話・対話記録 | service/input/settingsで実行保持・停止と適用の独立を確認 |
| refactor-skipped | D04 Goal操作と本文保持 | GUI/Planの原稿・モード・閲覧保持を改訂 |
| refactor-skipped | D05 公開端末回収と再開前の終了確認 | connectionの停止・失敗・close・承認cancelを拡張 |

未実装API、readyでのrun終了、Goal終了後の要求残存、目標モード残存、端末回収前の再開をそれぞれredで確認して修正した。新しいテスト基盤や文言一致検査は作らず、既存試験の改訂・統合を優先した。

## 対象ファイル
runtime/codex、services、storage、ui、app/shell、prompts、既存tests、docs、初期設計と親仕様。

## 検証

| command | 結果・検証範囲 |
|---|---|
| `uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u049-terminal-final --junitxml=tmp/u049-terminal-final.xml` | 793 passed。通常turn、Goal、停止、要求寿命、設定適用、入力・観測・保存、GUIの回帰 |
| `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress` | 成功。公開型/docstring、Protocol/fake一致を確認。ignore/cast・依存追加なし |
| `uv lock --check` / `uv build` / `uv run python scripts/check_package.py` | 成功。隔離install、guide/current/play同梱、capture worker終了、module/scriptの画面起動・終了 |
| `uv run python tmp/u06-m1/validate_docs.py` / `git diff --check` | 文書リンク、unit一意性、構造と差分の検査。意味・根拠・未検証表示は文書を読んで照合 |
| `uv run python tmp/u06-m2-runtime/product_probe.py` | 最終run-20261113-170939/report.json成功。実runtime・製品サービスとloopback Responses、固定認証/catalog、合成映像・模擬入力 |
| `uv run python tmp/u06-m2-runtime/memory_interrupt_product.py` | memory-product-20261113-170826/report.json成功。probe側terminateなしで製品停止・書込不変・同Goal再開・サービス終了を確認 |
| `uv run python -X utf8 tmp/u06-m2-storage/permission_probe.py` | report-product-layout.json成功。製品launch/thread設定、memory内書込、親guide読取、親events書込拒否・原本不変 |

自律継続プローブは2turnの同run保持、pause/interrupt/neutral、同threadのunsubscribe/resume、新endpoint・新run、再開後入力、clear、公開対話/入力/観測記録を確認した。推論レベルの実送信はlow→low→medium→mediumで、現turnの後続推論は変わらない。実モデル・実機の工程受入ではない。

記憶書込はthread内の前景100回×100msで確認した。修正前のmemory-interrupt-20261113-165622では、同turnの公開interrupted後も3→30 bytesへ増加し、公開list/terminateを手動で接続すると止まった。最終製品版では停止後1秒と再開後にも3 bytesのまま、一覧は空だった。サービス終了、検証runtime/shellの終了、fixture設定復元を確認した。所有runtimeはtransportがterminateして回収するため、終了コード0とは主張しない。中間版の一度のshutdown待ちtimeoutは失敗証拠として保持し、最終版の2プローブでは終了を確認した。原因の断定や全条件での再発否定はせず、M3の実接続終了でも再確認する。

権限プローブはprocess cwd=隔離root/runtime、原記録=別archive、thread cwd/runtimeWorkspaceRoots=archive/memoryという製品と同じ分離を使った。command/exec単独はthreadに属さず、これだけで全モデルツール経路の隔離成功とはしない。process cwdをarchiveに置く対照構成では原記録への書込も許可されたため、製品の配置を維持する。子プロセスだけCODEX_WINDOWS_REGISTERED_CORE/CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILYを除外し、親環境や認証は変えない。

Windows sandbox未準備のhomeはreadOnlyへの縮退を製品が検出して拒否する。新規homeのセットアップ取消し、検証ホストによる状態DB/desktop作成拒否は製品sandboxの拒否と区別した。準備済み隔離homeで同じ書込範囲を保った実行により確認し、共有home変更・承認自動accept・権限拡大による再試行は製品へ追加しない。

固定通知のQt描画で[通常画面](assets/normal-paused.png)と[800×600](assets/small-paused.png)を確認した。Goal/Planの長文選択・コピー、状態更新、追記・計画更新中のスクロール保持は既存試験で確認。文字拡大専用の試験は追加していない。

## 最終レビューとコミット

開始拒否で既存runを止めないこと、Goal応答前readyの同run保持、遅いturn・回答の失効、設定適用と停止の独立、Goal目的更新とPlan保持、非同期保存の所有を相互レビューした。停止時の端末回収は公開APIの有界処理として既存接続へ追加し、独立した監視・継続・復旧層は作らない。

D01 7834b2a、D02 9a58856、D03 d339acf、D04 3d69edb、Goalモード修正0a82b7b、端末回収f49d098。最終仕様と公開API説明を整合して本子をcompleteへ移す。

## 未確認事項・M3の着手条件

M3は本子とM0/M1を引き継ぎ、認証とモデル妥当性の分離、新規homeのWindows sandbox準備、新規ChatGPT（2026-11-13の後続合意でAPIキーは対象外）、利用制限表示、実モデルと指定実機の一連操作を受け入れる。機器はUSB Video Device、COM8・CH552・9600 baud。初回observeの実モデル遵守、実機停止と記憶書込中断の一連操作、実summary、実IME・複数DPI、実接続終了は未確認。今回の固定Responses/合成Frameや同期shell単体の成功を、その受入へ拡張しない。公開一覧にない背景子孫の終了・記憶整理は保証せず、U07以降の所有境界へ引き継ぐ。

## 完了条件
- [x] D01〜D05の実装と対象検証
- [x] 最終全体gate、公開API文書・初期設計・親仕様の整合
- [x] 実装コミット、確認済み範囲とM3残件の記録
