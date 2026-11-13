# U04 M1 観測と記録所有のレビュー

## 1. 目的・起点

状態: complete（レビュー・修正・検証完了）。2026-11-12の追加レビュー依頼に着手・完了。[U04親](../../complete/unit_009/RECORDED_OBSERVATION.md)・[M1実装](../../complete/unit_032/RECORDED_OBSERVATION_IMPLEMENTATION.md)を対象に、最新画像一枚の保存・返却を基準として責務と不要な仕組みを見直す。ユーザーは、MCPの観測保存と、その後のCodex解釈・対話履歴保存を同じロックで妨げないかを指摘した。

開始時は`codex/u04-m0-observation-design`、`23b8fad`。開始時の未追跡`debug.log`は作業中に利用者自身が削除したと回答を受けたため復元しない。

後続の[チャット・永続化の改訂](../../complete/unit_034/CHAT_OBSERVATION_PERSISTENCE.md)で、MCP応答の保存待ちとプロジェクト未選択時の制限を撤回した。本書の保存後返却はM1実装・検証時点の契約として残す。新契約の製品実装はM2であり、本書の完了をその実装済み根拠にしない。

## 2. 対象範囲

観測と保存の責務、原記録の書込所有、保存タイミング、空analysisと既存テストの要否を修正・検証し、コミットする。

## 3. 対象外

MCP/GUI結合、対話履歴の製品保存API、Codexによるmemory編集の実接続、汎用イベント基盤、監視・自動修復、PR・マージ。後続機能のためのキューや追加状態を作らない。

## 4. 関連文書・前提

[03 I/O](../../initial/03-game-io.md)、[04 保存](../../initial/04-agent-memory.md)、[M0](../../complete/unit_031/RECORDED_OBSERVATION_DESIGN.md)、[U06](../../wip/unit_011/GOAL_EXECUTION.md)、[公開API](../../../docs/recorded-observation.md)。現コードのOSロックは`.gigas.lock`の1 byteだけであり、他ファイルの読取・memory編集をOSとして排他しない。現Codex接続はread-onlyであり、別Pythonプロセスによる読書きの成功をCodexの書込権限確認としない。

## 5. 判断と振る舞い

| 論点 | 採用する契約・理由 |
|---|---|
| 保存タイミング | 観測の原画像・ID・取得時刻をツール成功応答前に保存する。後からCodexが解釈するまで保存を遅らせず、保存済み観測を解釈で書き換えない |
| 解釈・対話履歴 | 公開されたCodex応答等をアプリが通知受信時に別の原記録として残す。memoryはCodexが編集する。両者の実装はU06へ残す |
| 所有単位 | アプリが選択プロジェクトにつき原記録writerを一つ持つ。MCP/観測と将来の対話記録はその参照を借り、呼出しごとにopen/closeしない。接続終了とプロジェクト終了を同一視しない |
| 排他の対象 | OSロックは協調するアプリ同士の原記録writer専有。画像・ログ読取やCodexのmemory編集を排他しない。原記録の編集・追記はアプリだけが担当する |
| API責務 | `ObservationService`の一件のワーカーで`Observation.from_frame`→`ProjectWriter.record(observation)`を行う。writerは符号化済み画像を保存する。PNG符号化中にwriterの同期操作ロックを持たない |
| 空analysis | 観測にもMCP応答にも必要ない予約欄を廃止する。schema 1の読込では旧analysisを余剰項目として許容し、既存行は書き換えない。版1の画像・ID等の意味は変えない |
| 状態と終了 | 保存中の同期操作mutex、別アプリ排他、取消し後のTask参照は異なる資源を守るので維持する。符号化中のwriter.closeは自動拒否されない。アプリ所有者が受付終了→drain後に保存先を閉じる。別の状態機械は追加しない |

`ProjectWriter`はプロジェクト全体やCodexの会話を管理する型ではなく、アプリの原記録への書込所有者である。名前だけを変える改修は行わない。将来、対話記録を追加する際は同じwriterへ短い永続化処理を直列化する。観測のBUSYを対話履歴の破棄に流用せず、その通知順・保存失敗・終了との結合はU06で確定する。

## 6. TDD Test List・既存検証の整理

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | S1: 符号化済み観測をそのbytesとIDのまま保存し、旧analysis付き行も原本保持して再読込する | regression | integration | 既存保存試験を書換え。analysis欠落だけをassertする試験は作らない |
| refactor-done | S2: writer保有中も別プロセスから原記録を読んでmemoryを編集でき、別writerだけ拒否される | characterization | integration | 既存のプロセス間排他と終了試験へ統合 |
| refactor-done | S3: 符号化中も取消し後の一件を回収し、完了した同じ観測を保存する | regression | integration | 既存の成功・失敗・取消し試験を保存前の符号化境界へ展開 |

実行前cancelの専用試験はobserve本体が一度も動かずasyncioの性質だけを検査するため削除する。未知schema・破損・上書き拒否・部分追記の試験は原本保護の観測可能な契約なので維持する。最大画像の保存・復号も、実寸法と容量の受入として残す。

## 7. 設計メモ

MCPはツール境界であり、プロジェクトの所有者や画像解釈の主体ではない。MCP待機終了をwriter解放の条件にせず、アプリ所有のライフサイクルへ戻す。writerのmutexは同期の保存・再検査・終了を保護し、OSロックの代用やモデルの読取禁止には使わない。

## 8. 対象ファイル

観測・storage・serviceの3モジュール、既存観測/保存試験、公開docs、M0/M1/親、initial03/04とU06の引継ぎを対象とする。依存追加なし。

## 9. 検証

| command / 確認 | result | 対象と限界 |
|---|---|---|
| `uv run pytest tests/unit tests/integration -q` | 408 passed、33.54秒 | 既存全体。初回M1の411件から統合・削除し、原記録読取/memory編集と符号化取消しを既存検証へ組込み |
| `uv run ruff format --check .` | pass（166 files） | 製品・試験等のPythonファイル |
| `uv run ruff check .` / `uv run ty check --no-progress` | pass | `record(Observation)->None`とserviceの返却・例外の型/説明が一致。型抑制追加なし |
| `uv lock --check` / `uv build` | pass | 依存・lock変更なし、wheel/sdist生成 |
| `uv run python -` のwheelプローブ | pass | wheel内のAPIから符号化済み保存・非同期観測・drain・再open。既存依存環境を使用 |
| 公開docsのPython例 | 2例実行成功 | 同期API、非同期観測からプロジェクト終了・再open。実MCPなし |
| `git diff --check` / 文書機械検査 | pass | 参照先、unit一意性、仮テキストを確認 |
| 実MCP/Codex記憶書込/対話履歴保存 | not run | M2/U06の実装・実接続で確認。別Pythonプロセスによる共存試験と区別 |

独立レビュー2担当の指摘（ロック説明の過広さ、符号化責務、空analysis、asyncioだけの試験）は採用。mutex・OSロック・Task参照・明示reloadはそれぞれ現在の保全責務があり維持した。改修後の独立レビューは追加欠陥なし、storage/service 46件を別basetempで確認した。`test-desiderata-review`、`type-boundary-review`、`docstring-style`、`agentic-self-review`で検証価値と契約を確認した。

### 項目別記録

S1: storage既存試験を書換えたredは13 failed（符号化済みObservationを旧Frame APIが再符号化しようとした）。責務分離後、storage/service/captureの56 passed、対象ruffと全体ty成功。旧analysis付き行の原本保持・追記も同じ保存試験で確認。空analysisの不正値だけを検査したパラメーター1件を削除し、符号化失敗の検証はS3のサービス試験へ移す。

S2: 既存のOS排他・プロセス終了の2試験を1試験へ統合。`pytest tests/integration/test_observation_storage.py -k archive_owner` 1 passed（初回greenの現動作確認）。別Pythonが画像/JSONL/管理情報を読みmemoryを作成でき、原記録は不変、別writerだけ拒否。close後と異常プロセス終了後の再取得も確認。対象ruff/ty成功。実Codexの権限・解釈の試験ではない。

S3: 既存service試験へ符号化容量失敗と、符号化中取消し後の回収を統合。符号化中もwriterの再検査が可能で、別writerの専有は拒否される。保存中のclose/reload拒否と取消し後の成功/失敗回収も維持。未実行コルーチン取消しだけの試験を削除。service/capture 17 passed（初回green）、ruff/全体ty成功。製品の符号化責務はS1で移動済み。

## 10. 後続

M2: 実MCPへ借用する観測サービスを配線し、成功応答前の原記録保存を確認。切断時にはサービスをdrainするが、プロジェクト自体を終了するまではwriterを保持する。

U06: 公開応答・依頼等のイベント形式、同じwriterでの直列保存、記録と会話/観測IDの対応、memoryの書込権限を実通知・実接続で確認する。今回は未実装・未検証。

## 11. 完了チェックリスト

- [x] 現実装と後続責務を照合した
- [x] 必要な修正・既存試験の整理を終えた
- [x] 公開API・仕様と検証結果を更新した
- [x] 変更をコミットした

M2向けの[追加改訂](../unit_034/CHAT_OBSERVATION_PERSISTENCE.md)では、Frame本体を持たない加工・符号化済みObservationと、オリジナル／FullHD／HD設定を採用した。保存中も次観測を妨げない。本書の原寸・Frame保持・保存待ちの実績はM1時点のものであり、この改訂の実装完了を示さない。
