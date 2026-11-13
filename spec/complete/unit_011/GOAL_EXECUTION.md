# U06 Goalによる継続と途中介入 親仕様

## 1. 目的・状態・起点

通常turnで成立した観測・入力をGoalの継続攻略へ接続し、依頼・記憶・途中介入・公開表示を対応付ける。攻略判断とターン間の継続はCodexへ委任する。

状態: **complete、2026-11-13にM0〜M3と工程全体の受入を完了**。M1は文脈内の質問/承認、常設目標と一体型入力、次turnのモデル選択までを含む。各子工程の根拠は第6節、今回の実証と制約は[M3](../unit_050/GOAL_ACCEPTANCE.md)へ集約する。APIキー認証・PR・mergeは対象外。公開API・隔離接続・Qt試作の結果を製品/実機の受入と混同しない。

起点は[実装計画](../../initial/05-implementation.md)、[認証と実行設定の分離](../../initial/02-runtime.md)、[主画面とメッセージ操作](../../initial/10-ui-interaction.md)。利用場面は明示的な継続開始、公開計画の確認、質問回答/追加指示/承認、停止と同一セッション再開。

## 2. 前提と着手条件

[U02](../../complete/unit_007/CODEX_CONNECTION.md)の接続・要求応答、[U04](../../complete/unit_009/RECORDED_OBSERVATION.md)の最新キャッシュ観測と非同期保存、[U05](../../complete/unit_010/INPUT_AND_STOP.md)の操作権・停止・MCP再公開を使う。開始時はmain/ad69559/clean、採用runtimeは0.167.0。M0で現コード・テスト・依存・公開schemaを確認した。

M2で公開Goalの自律turnを採用し、サービスのrunを明示開始から停止まで保持するよう結合した。通常turnは従来どおり終端でrunを終了し、Goalのturn間は入力を保留する。毎turnの新Grant、独自continue投入、別の状態機械は追加しない。

## 3. 対象範囲・対象外

対象はGoal開始・更新・自律継続・休止・同一セッション再開・破棄・完了、共通Message、公開Goal/Plan/History/reasoning/activity、質問・承認・配送結果、guide/memory入口・公開対話保存、認証・モデル/effort・利用制限。

計画的終了の受入はU07、再起動後/保存参照からの復旧はU08、記憶整理はU09、長時間運転はU10。アプリ独自の攻略判断、継続再投入、無通知の診断、自動復旧・自動課金切替は対象外。

## 4. 関連文書

[02 実行管理](../../initial/02-runtime.md)、[04 記憶・保存](../../initial/04-agent-memory.md)、[07 検証](../../initial/07-verification.md)、[08 公開APIと根拠](../../initial/08-sdk-compatibility.md)、[09 処理順序](../../initial/09-control-flows.md)、[10 UI](../../initial/10-ui-interaction.md)。API調査はM0、製品結合はM1/M2、実接続受入と残る制約はM3子仕様が所有する。

## 5. 採用した契約と確認範囲

| 領域 | 決定・確認範囲 |
|---|---|
| Goalと通常送信 | goal/set(active)だけで開始し、turn/startを重ねない。既存Goalの目的更新は利用量/状態を維持。pause/clearとは別にturn/interruptが必要。M3で実モデルの複数自律turn、目的更新、pause/paused中会話、同thread再開、自然最終応答、clearを確認 |
| U05の再利用 | Goalの複数turnで同じrun/Grant/endpointを保持。停止で旧Grantを失効し、明示再開のunsubscribe→同thread resumeで新MCPを公開。M3で本人手動後の初回observe、旧Grant拒否・旧MCP終了、新run/endpointを確認。3DS公開更新は模擬機器の確認に限る |
| Messageと要求応答 | 通常入力と追加指示はMessage、回答・承認は履歴内の要求カードへ分ける。質問ID別原稿を要求ごとに一つのanswersへまとめ、追加指示は対象turnへのsteerへ渡す。M0で確認した配送契約を維持し、M1修正で送信先リストを撤去した。turn間に配送キューを作らない |
| 公開表示 | tools.update_plan.enabled=trueが必要。Planは現在turnの公開一覧、reasoningは公開summaryのみ、activityは実item。Qtの選択/コピー/閲覧保持は固定通知、実Plan/応答と本人のIME操作はM3で確認。今回の製品設定・短いsol/lowとterra/mediumで非空summaryは0件。全モデルの非対応や実summary表示成功とはしない |
| 記憶・記録 | guide/currentは不足時だけ生成、既存保持。Codex書込をmemoryへ限定し、原記録はアプリが同じRecordingServiceで非同期保存。実モデルのmemory書込、sandbox内から親原記録の読取成功・親の使捨てファイル作成拒否を確認。原記録への破壊的書込は試さない |
| 認証・設定 | ログインとモデル妥当性を分離。公開experimental thread/settings/updateを自律次turnへ、通常送信は次turn/startへ使い、現在turnは変えない。M3で資格情報を複製しない新規ログイン、準備後の再接続ready、通常/自律turnそれぞれの実モデル/推論レベル反映を確認。APIキーは今回対象外 |

同期MCPの300秒は各ツール要求の期限でありGoal全体の期限ではない。即時の最新キャッシュ、入力・観測・保存の分離、停止しても提出済み設定適用が続くU05方針を維持する。再開時は現在readerを確認し、共通指示で最初のobserveを求める。保存完了や要求後の新Frameを待たない。

公開experimental設定経路が利用できなければ制約を表示して採用版/要件を見直す。独自継続ループでは補わない。最新公式文書と採用版で目的変更の利用量扱いが異なるため、固定版の実測を正とする。

## 6. 子工程と進行条件

| 子工程 | 状態・開始条件 | 到達点 |
|---|---|---|
| [M0 API・操作契約・Qt](../../complete/unit_044/GOAL_INTERACTION_DESIGN.md) | 調査・設計完了 | 公開形式/実呼出し/模擬機器/Qtと未検証を分け、KISSで設計を確定 |
| [M1 共通対話UI](../../complete/unit_045/COMMON_DIALOGUE_UI.md) | 従来実装・検証完了。配置・回答操作は後続修正で置換 | 公開通知、配送結果、下書き、選択/コピー/閲覧保持を結合した履歴を残す |
| [M1 文脈内対話UI](../../complete/unit_046/CONTEXTUAL_DIALOGUE_UI.md) | 修正・検証・コミット完了 | 左の会話内質問/承認と通常Message、右の16:9映像枠と常設Planを結合。letterbox/stretchと要求寿命を維持し、選択・コピー・更新中閲覧保持を受け入れる |
| [M1 常設目標と一体型入力](../../complete/unit_047/GOAL_AND_MESSAGE_COMPOSER.md) | 実装・検証・コミット完了 | 目標と今回の作業を常設し、空欄時のMessage表示・本文・操作・既存設定入口を一体化。配送と閲覧保持を再利用 |
| [M2 Goal・記憶・記録](../../complete/unit_049/GOAL_MEMORY_INTEGRATION.md) | 実装・検証・コミット完了 | Goalとrun寿命、停止/再開/MCP更新、guide/memory・公開対話記録を結合。M1で常設した目標欄の本文・状態を確定Goalへ結合。起動環境/sandboxを製品で再確認 |
| [M3 設定・認証と工程受入](../unit_050/GOAL_ACCEPTANCE.md) | 実装・検証完了 | 認証分離・利用制限/配送競合、新規ChatGPT、実モデル・短い実機依頼を確認。APIキーは対象外 |

M1では目標欄と今回の作業を常設し、未提供の本文を—とする。通常turnのモデル/推論レベル選択は入力枠のポップオーバーから次turn/startへ反映し、設定ダイアログへの転送は撤去した。Goal自律turnへの設定反映はM2、認証分離・明示実行準備・利用状況はM3で結合した。文字拡大専用の例・検証・分岐は合意により撤去した。

## 7. 振る舞い仕様・TDD Test Listの受入観点

| 観点 | 工程全体の受入 |
|---|---|
| 継続・介入・停止 | turn終了をGoal達成とせず、対象の自律turnへ入力/通知を結合。pause/stopで入力停止を先行し、遅い通知/回答で復活しない。completeでは入力・追加指示・要求を閉じて公開端末を回収し、現在turnの最終応答と自然終端を待つ。終端後の回収確認でready、待機中の明示stop/clearはinterruptしてstoppedとする。保持Goalだけ明示再開できる |
| 共通UI | Goalと公開Plan、会話内の要求カード、通常Messageの役割が分かれる。複数質問・自由回答不可・承認を明示的に配送し、本文と判断が離れない。秘密入力指定は要求全体を未対応で拒否する。カード・Planの更新中も選択/コピー/閲覧位置を保ち、16:9映像枠と既存の方式選択が両立する |
| 記憶・記録 | 既存記憶を保全しCodexが必要なmemoryだけ編集。公開対話・入力・観測を追跡でき、原画像を再編集しない。保存失敗と入力停止を結合しない |
| 認証・設定・制限 | 無効既定値でもログイン/選び直し可能。通常/自律turnそれぞれに次turn設定が反映し現turn不変。取得不能の値を捏造せず、制限停止後の自動再開/課金変更をしない |

T06〜T10、T15〜T17、T18の同一セッション部分、T19の関連保存、T20指示資材、T21設定に対応する。既存配送・要求寿命・停止・保存試験の改訂/統合を先に検討し、削除済み閲覧試験19件の一律復活や文言一致テストを作らない。

## 8. 対象ファイル

M0は本親・子仕様、初期設計、移動参照、Qt証跡と隔離調査資材。M1以降は必要なruntime/codex、services、app、ui、storage、promptsと既存試験/docsを各子で具体化する。公開API/型/docstring・配布資材を変更する子で対応skill/gateを使う。

## 9. 検証状態

M0のschema/source・隔離API・模擬モデル/機器・Qt試作、M1の表示/配送/閲覧保持、M2のGoal/記憶/記録結合は各子仕様へ記録した。M3では新規認証と実行準備、実IME、通常/自律次turn設定、実質問保留中のsteer、承認accept後の実行、cancelだけによるturn中断を製品経路で確認した。実要求にdeclineは提示されず、その応答は固定試験の確認に限る。実Plan・応答を受信した一方、非空summaryは今回の条件で未提供だった。

M3の実機はUSB Video Device、COM8・CH552・9600 baud。入力・memory書込・設定適用の同時停止では中立送出、公開端末回収と書込不変、提出済み設定の適用継続、同Goal再開後の自然最終応答を確認した。約11秒間の画面停止を映像で確認したが、機器ACKと瞬間の物理解除は未観測であり、PC側の送出間隔を解除時間とはしない。本人手動後の再開では新MCPによる現在画面の初回observe、旧Grant拒否・旧MCP task終了、clearと全所有プロセス終了を確認した。記録と未達だった先行probeの切り分けはM3子仕様へ残す。

## 10. 制約・後続への引継ぎ

2026-11-13の最終レビューで、未確認事項、既知の制約、合意済み対象外を次のとおり整理した。M3の実証を設定UI整理後の実操作や他環境へ広げない。

### 未確認事項と確認する工程

| 事項 | 確認済み範囲・残件 | 担当・判断時点 |
|---|---|---|
| 非空の公開summary・承認decline | 今回の実モデル条件では未提供。表示・配送・閲覧保持は固定通知で確認済み。実提供時の結合確認は残る | U10 M0で条件を選び、実提供された場合にM1で確認。未提供なら未確認のまま記録し、全モデルの非対応としない |
| 実利用制限による停止 | 実アカウント・利用枠取得を確認。ordinaryUsageAllowed=falseと最終失敗による停止は固定試験。実上限到達は未検証 | U10 M0で検証条件を決める。意図的な課金超過は行わず、未発生なら固定試験と実取得の証拠を維持 |
| 物理解除・他機器・長時間 | USB Video Device、COM8・CH552・9600 baudで中立送出・画面停止・本人手動後の再開を確認。機器ACK・瞬間の解除時間、PokeCon/3DS実機、300秒実待機、長時間の履歴・記録量・資源使用は未検証 | U10 M0で対象と測定方法を限定しM1/M2へ割り当てる。PC送出間隔を物理解除時間としない |
| 既定homeの準備状態 | M3時点ではupdateRequired。本人準備・再接続readyの実証はfresh homeに限定。現在の既定homeは再確認していない。最初の準備失敗原因も復元不能 | U07以降の次の実接続前に対象homeでreadinessを確認。必要時は本人の明示setupと再接続を行い、readyまでmemory付き開始を拒否する |
| 設定UI整理後の実操作 | unit_051〜053でQt描画・固定イベント・統合試験を確認。配置変更後の実ログイン・Windows準備操作は再実行していない | U07以降の次の実接続時に最新設定画面の導線と状態反映を確認。実施まではM3の旧配置の実証と区別する |

### 既知の制約

- 再開後の初回observeは共通指示に従った実測であり、APIによる順序保証ではない。最新cacheの利用可否はアプリが確認するが、新Frameや保存完了は待たない。
- 停止時の回収は公開backgroundTerminals一覧の範囲。そこに属さない背景子孫や任意の外部書込の終了保証は含めない。監視・OS PID終了・自動復旧で補わない。
- 利用率・残高・reset時刻から利用不能や回復を推定しない。明示許可が戻ってもGoalは自動再開しない。
- 採用runtimeは0.167.0。公開experimental設定経路の変更時は採用版・要件を再確認し、独自継続ループを追加しない。起動時のCODEX_WINDOWS_*二変数の除去とmemory書込範囲検証を維持する。

### 後続工程と対象外

[U07](../../planned/unit_012/PLANNED_STOP.md)は追加指示による計画的終了、[U08](../../planned/unit_013/RESUME.md)は保存参照・アプリ再起動後の復旧、[U09](../../planned/unit_014/MEMORY_MAINTENANCE.md)は記憶の整理・保守、[U10](../../planned/unit_015/CONTINUOUS_OPERATION.md)は上表の運転条件と長時間受入を所有する。共通配送・停止・同一セッションの保持Goal・原記録を入口とし、U06ではこれらを実装しない。

APIキー認証、アプリ内ログアウト・アカウント切替、モニター間DPI遷移、文字拡大専用の受入は今回の合意済み対象外であり、未完のU06項目へ戻さない。旧api_key設定をChatGPTへ明示変更するUIは、APIキー認証の対応を意味しない。

## 11. 完了チェックリスト

- [x] M0の調査・設計、根拠と未確認事項を記録した
- [x] M1〜M3の製品実装・必要な実接続を完了した
- [x] 工程全体の実機/GUI受入を完了した
- [x] 利用手順と後続引継ぎを更新し、親をcompleteへ移した

M1最終修正は[通常turnのモデル選択](../../complete/unit_048/NEXT_TURN_MODEL.md)、M2の固定試験・模擬機器による製品結合は[unit_049](../../complete/unit_049/GOAL_MEMORY_INTEGRATION.md)、M3の品質ゲート・実接続・実機受入は[unit_050](../unit_050/GOAL_ACCEPTANCE.md)を正本とする。認証・準備・利用状況の設定集約は[unit_051](../unit_051/CODEX_SETTINGS_UI.md)、配置・利用状況・見出しの修正は[unit_052](../unit_052/CODEX_SETTINGS_LAYOUT_FIX.md)、認証行への集約と旧設定の明示変更は[unit_053](../unit_053/AUTH_ROW_CONSOLIDATION.md)で完了した。U07以降には未着手。

## 12. 最終レビューの追従修正（2026-11-13）

起点6576a5f、作業ツリーclean。サンドボックス未準備時のエラー案内を現在の設定画面へ変更し、接続API文書の撤去済みメニュー案内と残割合の説明、親仕様の設定UI先送り表記を修正した。未準備の通常送信・Goal開始で旧案内が出ることは修正前に固定RPCで再現済み。Goal・停止・認証の制御処理、API、型、依存は変更しない。KISSレビューでは追加の抽象化・継続ループ・自動復旧は不要と判断した。

検証:

- `uv run pytest tests/unit/test_codex_connection.py -q -k 'sandbox or memory_preparation or memory_start' --basetemp=tmp/u06-final-review-tests`: 18 passed。未準備・状態取得待ち・停止競合・準備失敗の既存契約を確認。
- 固定RPCによる通常送信・Goal開始の手動再現で、両方のエラーが「設定 → Codex → サンドボックス環境」を案内することを確認。自然言語の完全一致テストは追加せず、実際の文言と現在の画面配置を照合した。
- `uv run ruff format --check .`、`uv run ruff check .`、`uv run python tmp/u06-m1/validate_docs.py`、`git diff --check`: 成功。撤去済み導線の残存検索も実施。
- U07の旧ボタン表現を現行の中断・破棄へ揃え、U07/U10の前提から本引継ぎ表へ参照を追加した。両工程はplannedのままであり、着手・検証済みとはしない。

直前の製品全体検証はunit_053の847 passed。本修正は案内文字列と仕様・手順だけの変更のため、全体テスト・型検査・lock/build・実ログイン・実機は再実行していない。未確認事項の記録を実確認の代替にはしない。
