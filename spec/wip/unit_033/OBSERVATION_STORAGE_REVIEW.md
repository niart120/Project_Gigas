# U04 M1 観測と記録所有のレビュー

## 1. 目的・起点

状態: wip。2026-11-12の追加レビュー依頼に着手。[U04親](../unit_009/RECORDED_OBSERVATION.md)・[M1実装](../../complete/unit_032/RECORDED_OBSERVATION_IMPLEMENTATION.md)を対象に、最新画像一枚の保存・返却を基準として責務と不要な仕組みを見直す。ユーザーは、MCPの観測保存と、その後のCodex解釈・対話履歴保存を同じロックで妨げないかを指摘した。

開始時は`codex/u04-m0-observation-design`、`23b8fad`。未追跡`debug.log`を保全する。

## 2. 対象範囲

観測と保存の責務、原記録の書込所有、保存タイミング、空analysisと既存テストの要否を修正・検証し、コミットする。

## 3. 対象外

MCP/GUI結合、対話履歴の製品保存API、Codexによるmemory編集の実接続、汎用イベント基盤、監視・自動修復、PR・マージ。後続機能のためのキューや追加状態を作らない。

## 4. 関連文書・前提

[03 I/O](../../initial/03-game-io.md)、[04 保存](../../initial/04-agent-memory.md)、[M0](../../complete/unit_031/RECORDED_OBSERVATION_DESIGN.md)、[U06](../../planned/unit_011/GOAL_EXECUTION.md)、[公開API](../../../docs/recorded-observation.md)。現コードのOSロックは`.gigas.lock`の1 byteだけであり、他ファイルの読取・memory編集をOSとして排他しない。現Codex接続はread-onlyであり、別Pythonプロセスによる読書きの成功をCodexの書込権限確認としない。

## 5. 判断と振る舞い

| 論点 | 採用する契約・理由 |
|---|---|
| 保存タイミング | 観測の原画像・ID・取得時刻をツール成功応答前に保存する。後からCodexが解釈するまで保存を遅らせず、保存済み観測を解釈で書き換えない |
| 解釈・対話履歴 | 公開されたCodex応答等をアプリが通知受信時に別の原記録として残す。memoryはCodexが編集する。両者の実装はU06へ残す |
| 所有単位 | アプリが選択プロジェクトにつき原記録writerを一つ持つ。MCP/観測と将来の対話記録はその参照を借り、呼出しごとにopen/closeしない。接続終了とプロジェクト終了を同一視しない |
| 排他の対象 | OSロックは協調するアプリ同士の原記録writer専有。画像・ログ読取やCodexのmemory編集を排他しない。原記録の編集・追記はアプリだけが担当する |
| API責務 | `ObservationService`の一件のワーカーで`Observation.from_frame`→`ProjectWriter.record(observation)`を行う。writerは符号化済み画像を保存する。PNG符号化中にwriterの同期操作ロックを持たない |
| 空analysis | 観測にもMCP応答にも必要ない予約欄を廃止する。schema 1の読込では旧analysisを余剰項目として許容し、既存行は書き換えない。版1の画像・ID等の意味は変えない |
| 状態と終了 | 保存中の同期操作mutex、別アプリ排他、取消し後のTask参照は異なる資源を守るので維持する。アプリ所有者は受付終了→drain後に保存先を閉じる。別の状態機械は追加しない |

`ProjectWriter`はプロジェクト全体やCodexの会話を管理する型ではなく、アプリの原記録への書込所有者である。名前だけを変える改修は行わない。将来、対話記録を追加する際は同じwriterへ短い永続化処理を直列化する。観測のBUSYを対話履歴の破棄に流用せず、その通知順・保存失敗・終了との結合はU06で確定する。

## 6. TDD Test List・既存検証の整理

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | S1: 符号化済み観測をそのbytesとIDのまま保存し、旧analysis付き行も原本保持して再読込する | regression | integration | 既存保存試験を書換え。analysis欠落だけをassertする試験は作らない |
| todo | S2: writer保有中も別プロセスから原記録を読んでmemoryを編集でき、別writerだけ拒否される | characterization | integration | 既存のプロセス間排他と終了試験へ統合 |
| todo | S3: 符号化中も取消し後の一件を回収し、完了した同じ観測を保存する | regression | integration | 既存の成功・失敗・取消し試験を保存前の符号化境界へ展開 |

実行前cancelの専用試験はobserve本体が一度も動かずasyncioの性質だけを検査するため削除する。未知schema・破損・上書き拒否・部分追記の試験は原本保護の観測可能な契約なので維持する。最大画像の保存・復号も、実寸法と容量の受入として残す。

## 7. 設計メモ

MCPはツール境界であり、プロジェクトの所有者や画像解釈の主体ではない。MCP待機終了をwriter解放の条件にせず、アプリ所有のライフサイクルへ戻す。writerのmutexは同期の保存・再検査・終了を保護し、OSロックの代用やモデルの読取禁止には使わない。

## 8. 対象ファイル

観測・storage・serviceの3モジュール、既存観測/保存試験、公開docs、M0/M1/親、initial03/04とU06の引継ぎを対象とする。依存追加なし。

## 9. 検証

既存試験を先に書き換え、対象pytest、ruff、ty、unit/integration全体、lock/build、文書リンク・参照先・仮テキスト検査を行う。現時点の検証結果は未実行。

## 10. 後続

M2: 実MCPへ借用する観測サービスを配線し、成功応答前の原記録保存を確認。切断時にはサービスをdrainするが、プロジェクト自体を終了するまではwriterを保持する。

U06: 公開応答・依頼等のイベント形式、同じwriterでの直列保存、記録と会話/観測IDの対応、memoryの書込権限を実通知・実接続で確認する。今回は未実装・未検証。

## 11. 完了チェックリスト

- [x] 現実装と後続責務を照合した
- [ ] 必要な修正・既存試験の整理を終えた
- [ ] 公開API・仕様と検証結果を更新した
- [ ] 変更をコミットした

S1: storage既存試験を書換えたredは13 failed（符号化済みObservationを旧Frame APIが再符号化しようとした）。責務分離後、storage/service/captureの56 passed、対象ruffと全体ty成功。旧analysis付き行の原本保持・追記も同じ保存試験で確認。空analysisの不正値だけを検査したパラメーター1件を削除し、符号化失敗の検証はS3のサービス試験へ移す。
