# U06 Goalによる継続と途中介入 親仕様

## 1. 目的・状態・起点

通常turnで成立した観測・入力をGoalの継続攻略へ接続し、依頼・記憶・途中介入・公開表示を対応付ける。攻略判断とターン間の継続はCodexへ委任する。

状態: **wip、M0調査・設計完了、M1以降未着手**。2026-11-13の依頼により親をplannedから移し、[M0子仕様](../../complete/unit_044/GOAL_INTERACTION_DESIGN.md)だけを作成した。製品実装・PR・mergeは今回の対象外。M0の公開API・隔離接続・Qt試作の結果を、製品/実機の受入と混同しない。

起点は[実装計画](../../initial/05-implementation.md)、[認証と実行設定の分離](../../initial/02-runtime.md)、[主画面とメッセージ操作](../../initial/10-ui-interaction.md)。利用場面は明示的な継続開始、公開計画の確認、質問回答/追加指示/承認、停止と同一セッション再開。

## 2. 前提と着手条件

[U02](../../complete/unit_007/CODEX_CONNECTION.md)の接続・要求応答、[U04](../../complete/unit_009/RECORDED_OBSERVATION.md)の最新キャッシュ観測と非同期保存、[U05](../../complete/unit_010/INPUT_AND_STOP.md)の操作権・停止・MCP再公開を使う。開始時はmain/ad69559/clean、採用runtimeは0.167.0。M0で現コード・テスト・依存・公開schemaを確認した。

現接続部は通常送信中しかturnを採用せず、サービスは通常turn終端でrunを終了する。Goalへの結合は未実装。M2で既存runを明示開始から停止までの区間へ広げ、自律turnをその中へ対応付ける。毎turnの新Grant、独自continue投入、別の状態機械は追加しない。

## 3. 対象範囲・対象外

対象はGoal開始・更新・自律継続・休止・同一セッション再開・破棄・完了、共通Message、公開Goal/Plan/History/reasoning/activity、質問・承認・配送結果、guide/memory入口・公開対話保存、認証・モデル/effort・利用制限。

計画的終了の受入はU07、再起動後/保存参照からの復旧はU08、記憶整理はU09、長時間運転はU10。アプリ独自の攻略判断、継続再投入、無通知の診断、自動復旧・自動課金切替は対象外。

## 4. 関連文書

[02 実行管理](../../initial/02-runtime.md)、[04 記憶・保存](../../initial/04-agent-memory.md)、[07 検証](../../initial/07-verification.md)、[08 公開APIと根拠](../../initial/08-sdk-compatibility.md)、[09 処理順序](../../initial/09-control-flows.md)、[10 UI](../../initial/10-ui-interaction.md)。詳細API・検証証跡・未確認事項はM0子仕様が所有する。

## 5. M0の決定と根拠

| 領域 | 決定・確認範囲 |
|---|---|
| Goalと通常送信 | goal/set(active)だけで開始し、turn/startを重ねない。既存Goalの目的更新は利用量/状態を維持。pause/clearとは別にturn/interruptが必要。実モデルで継続/再開/paused中会話を確認 |
| U05の再利用 | Goalの複数turnで同じrun/Grant/endpointを保持。停止で旧Grantを失効し、明示再開のunsubscribe→同thread resumeで新MCPを公開。実runtime+模擬モデル/機器で2自律turn、停止、3DS公開更新を確認 |
| 共通Message | 質問ID別の回答原稿を一つの欄で編集し、一つのanswers応答へまとめる。追加指示は対象turnへのsteer、承認は明示ボタン。turn間は下書き保持とし配送キューを作らない。質問待ちsteerと複数回答を実runtimeで確認 |
| 公開表示 | tools.update_plan.enabled=trueが必要。Planは現在turnの公開一覧、reasoningは公開summaryのみ、activityは実item。Qt標準部品で選択/コピー/閲覧保持を確認。実モデルsummaryは今回未受信 |
| 記憶・記録 | guide/currentは不足時だけ生成、既存保持。Codex書込をmemoryへ限定し、原記録はアプリが同じRecordingServiceで非同期保存。実モデル書込、sandboxの親原記録読取/書込拒否を確認 |
| 認証・設定 | ログインとモデル妥当性を分離。公開experimental thread/settings/updateを次turnへ使い、通常/自律turnへのモデル/effort反映を実モデルと実送信で確認。現在turnは変えない。APIキー/新規ログインはM3未検証 |

同期MCPの300秒は各ツール要求の期限でありGoal全体の期限ではない。即時の最新キャッシュ、入力・観測・保存の分離、停止しても提出済み設定適用が続くU05方針を維持する。再開時は現在readerを確認し、共通指示で最初のobserveを求める。保存完了や要求後の新Frameを待たない。

公開experimental設定経路が利用できなければ制約を表示して採用版/要件を見直す。独自継続ループでは補わない。最新公式文書と採用版で目的変更の利用量扱いが異なるため、固定版の実測を正とする。

## 6. 子工程と進行条件

| 子工程 | 状態・開始条件 | 到達点 |
|---|---|---|
| [M0 API・操作契約・Qt](../../complete/unit_044/GOAL_INTERACTION_DESIGN.md) | 調査・設計完了 | 公開形式/実呼出し/模擬機器/Qtと未検証を分け、KISSで設計を確定 |
| M1 共通対話UI | 着手可能。通常turnの共通入力/表示経路は成立 | 左右配置、共通Message/History、質問・承認、Plan/reasoning/activityを実通知へ接続。旧質問入力を置換し、選択/下書き/閲覧保持を確認 |
| M2 Goal・記憶・記録 | M1完了とM0契約の実装詳細化 | Goalとrun寿命、停止/再開/MCP更新、guide/memory・公開対話記録を結合。起動環境/sandboxを製品で再確認 |
| M3 設定・認証と工程受入 | M2完了 | 認証と独立した次turn設定、制限/配送競合、新規ChatGPT/APIキー、実モデル・短い実機依頼を確認 |

M1にGoalの仮表示・偽の成功や旧新UI切替を作らない。M3の設定変更をUI置換へ混ぜない。未着手の子仕様/全TDD項目は先行作成せず、次の作業はM1一つを選ぶ。

## 7. 振る舞い仕様・TDD Test Listの受入観点

| 観点 | 工程全体の受入 |
|---|---|
| 継続・介入・停止 | turn終了をGoal達成とせず、対象の自律turnへ入力/通知を結合。pause/stopで入力停止を先行し、遅い通知/回答で復活しない。保持Goalだけ明示再開できる |
| 共通UI | Goal/Plan/History/Messageの役割が分かれ、質問中も共通欄が見える。複数質問・自由回答不可・秘密回答・承認を明示的に配送し、選択/コピー/スクロールが成立する |
| 記憶・記録 | 既存記憶を保全しCodexが必要なmemoryだけ編集。公開対話・入力・観測を追跡でき、原画像を再編集しない。保存失敗と入力停止を結合しない |
| 認証・設定・制限 | 無効既定値でもログイン/選び直し可能。通常/自律turnそれぞれに次turn設定が反映し現turn不変。取得不能の値を捏造せず、制限停止後の自動再開/課金変更をしない |

T06〜T10、T15〜T17、T18の同一セッション部分、T19の関連保存、T20指示資材、T21設定に対応する。既存配送・要求寿命・停止・保存試験の改訂/統合を先に検討し、削除済み閲覧試験19件の一律復活や文言一致テストを作らない。

## 8. 対象ファイル

M0は本親・子仕様、初期設計、移動参照、Qt証跡と隔離調査資材。M1以降は必要なruntime/codex、services、app、ui、storage、promptsと既存試験/docsを各子で具体化する。公開API/型/docstring・配布資材を変更する子で対応skill/gateを使う。

## 9. 検証状態

M0の実ChatGPTモデル、採用runtime+模擬Responses/機器、Qt試作、schema/source/文書確認は子仕様へ記録した。製品Goalの単体/結合試験とGUI/実機はnot run。通常turnのU05実機受入を流用しない。

M3の対象機器はUSB Video Device、COM8・CH552・9600 baud。初回observe、複数turn、介入、質問/承認、pause/再開/clear、設定適用と停止、記録IDを短い実機依頼で対応付ける。300秒実待機や長時間はU10。制限検証で意図的な課金超過は起こさない。

## 10. 未確認事項・後続への引継ぎ

| 事項 | 担当・期限 |
|---|---|
| 小画面/大文字、秘密回答、選択箇所自体の更新、IME/DPI、実summary | M1受入。800×600/16ptの試作ではHistoryが64pxになったため高さ/スクロールを調整 |
| Goal開始/再開/完了/制限と要求・停止・設定変更の競合、初回observe、memory書込 | M2/M3受入。公開API単体の成功を製品結合の成功にしない |
| 独立runtime起動環境 | M2。desktop由来の2つのCODEX_WINDOWS_*起動変数でsandbox内書込が失敗し、子プロセスから除去すると成立した根拠に従う |
| 新規ブラウザ認証/APIキー/利用不能時 | M3。既存ChatGPT認証の成功と区別する |
| 計画的終了/再起動復旧/記憶整理/長時間 | [U07](../../planned/unit_012/PLANNED_STOP.md)・[U08](../../planned/unit_013/RESUME.md)・[U09](../../planned/unit_014/MEMORY_MAINTENANCE.md)・[U10](../../planned/unit_015/CONTINUOUS_OPERATION.md)。共通配送/停止/保持Goalと記録を入口とし、U06では実装しない |

## 11. 完了チェックリスト

- [x] M0の調査・設計、根拠と未確認事項を記録した
- [ ] M1〜M3の製品実装・必要な実接続を完了した
- [ ] 工程全体の実機/GUI受入を完了した
- [ ] 利用手順と後続引継ぎを更新し、親をcompleteへ移せる状態にした
