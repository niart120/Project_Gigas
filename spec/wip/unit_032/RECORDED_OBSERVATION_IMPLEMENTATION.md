# U04 M1 記録付き観測の実装

## 1. 目的・起点

状態: wip。2026-11-12着手。親は[U04](../unit_009/RECORDED_OBSERVATION.md)。ユーザーのM1実装・検証依頼を受け、[M0の採用方針](../../complete/unit_031/RECORDED_OBSERVATION_DESIGN.md)を実行可能な観測・保存APIにする。M0は調査作業の完了であり、本書は製品実装と検証の完了を管理する。

利用者は、作成または開いたプロジェクトへ最新のFrameを記録し、同じPNG bytesと観測IDを受け取る。再読込後も画像を参照でき、破損・部分失敗を成功として扱わない。

## 2. 対象範囲

最小プロジェクトの作成・読込・明示的再検査・OSロック、原寸PNGとJSONLの保存、最新snapshotによる観測、同時処理の拒否と取消し後の保存回収を実装する。

## 3. 対象外

MCP、Codex接続、保存先GUI、R02/R04修正、実機観測、画像加工、監視・自動修復、PR・マージ。保存先GUIは接続中固定と切断・終了の所有に結び付くため、M2で既存メニューへ配線する。M1だけの仮の選択状態や操作画面を追加しない。

## 4. 関連文書と着手条件

[03 game I/O](../../initial/03-game-io.md)、[04 保存契約](../../initial/04-agent-memory.md)、[05 工程](../../initial/05-implementation.md)、[07 検証](../../initial/07-verification.md)を正本とする。U03の不変FrameとCaptureReader.snapshotは実装済み。既存試験を確認し、取得・描画の振る舞いは重複実装しない。外部接続に依存しないM1は、未修正のR02/R04を前提にしない。

## 5. 振る舞いと公開境界

- `ProjectWriter.create(path)` / `open(path)` は同期API。存在しない専用ディレクトリだけに新規作成する。`reload()`は保持中のロックで再検査し、`close()`は書込処理が終わった所有者を解放する。保存中の再検査・終了・二重書込は拒否する。
- `ProjectWriter.record(frame)` は一件の同期処理として符号化・画像公開・追記を行い、不変の`Observation`（ID、元Frame、PNG bytes）を返す。画像変換はgame_ioの符号化処理、永続化はstorageが担当する。MCP・Qt型を渡さない。
- `ObservationService(reader, writer).observe()` はstreamingかつFrameありのsnapshotを一度採用し、同期処理を既存asyncioループのexecutorへ投入する。要求後の新Frameを待たず、同じFrameでも新IDを作る。
- ワーカー投入を一件の処理開始点とする。投入前の要求取消しなら保存せず、投入後は全処理を回収する。`drain()`は実処理が終わるまで待つ。要求取消しはワーカーを取り消さず、枠も解放しない。所有者はdrain完了後にwriterを閉じる。段階別の取消し状態を追加しない。
- WindowsのOSファイルロックを使う。ロックファイルの存在だけで拒否せず、PID表・期限・回復ループを作らない。Windows以外への移植は今回の受入対象外。
- JSONLは共通形式と既知観測payload、正整数の単調増加を検査する。未知種別・余剰項目・欠番は許容し、過去画像を走査しない。不正行と正常な接頭部の範囲をエラーに示し、原本を修復しない。
- PNG保存までの失敗は当該観測だけ失敗。JSONLへ触れた後の失敗は再検査まで追記不可。既存画像・公開済み画像・不完全な原記録を保持する。

M0の「開始前／開始後」は、本実装ではワーカーへの投入を境界として具体化する。OSスレッドが走り始める瞬間を別状態で監視する設計は採らない。保存と応答の同一性はGigas API境界の保証であり、モデル受信はM2で確認する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | P1: 新規専用ディレクトリだけに最小プロジェクトを作り既存物を保持する | new | integration | 部分作成失敗も再初期化しない |
| todo | P2: 正常な既存記録を読み、不正行の位置を報告して追記を拒否する | edge | integration | 未知種別・欠番・余剰項目、原本保持 |
| todo | P3: 別プロセスのwriterを拒否し、終了後に同じプロジェクトを開ける | new | integration | 実Windows OSロック、明示的reload |
| todo | O1: 元寸法・画素・時刻を持つPNGとIDを保存し再読込後も対応する | new | integration | 最大3840×2160、同Frameに別ID |
| todo | O2: 最新snapshotの採用条件と取得停止前後の境界を守る | new | integration | 既存の遅いreader試験を実保存へ統合 |
| todo | F1: 画像失敗とJSONL追記結果不明を区別して原本を保持する | edge | integration | 既存ファイル衝突、flush/fsync失敗 |
| todo | C1: 同時要求を拒否し、要求取消し後も保存と所有権を回収する | edge | integration | 強いTask参照、drain、保存中close拒否 |

## 7. 設計メモ

汎用イベント基盤、独自executor、Frame識別子の追加、待機期限や画像設定を作らない。保存時刻はUTC、採番は末尾sequence+1、永続IDはUUID4。常に同じ値になる変換情報や画像hashを保存しない。Windowsの同階層renameで既存画像を上書きせず公開する。

## 8. 対象ファイル

`src/gigas/storage/project.py`、`src/gigas/game_io/observation.py`、`src/gigas/services/observation.py`、必要なexport、対応するintegration試験、`docs/recorded-observation.md`、親・初期仕様を対象とする。依存追加は不要。

## 9. 検証計画・結果

各itemでred/greenを確認して記録・コミットする。最終gateは`uv lock --check`、ruff format/check、ty、unit/integration全体、`uv build`、`git diff --check`。文書のAPI・事実・リンクをレビューする。現時点では未実行。

## 10. 先送り事項

MCP画像応答と実Codex受信、保存先GUI、接続・終了との配線はM2。R02/R04は接続・会話画面拡張前に修正する。ネットワーク共有・外部同時編集・電源断を跨ぐ二ファイル原子性は提供しない。

## 11. 完了チェックリスト

- [x] 対象範囲・対象外と既存検証を確認した
- [ ] TDD項目を実装・検証・コミットした
- [ ] 公開API・型・docstring・利用者docsを照合した
- [ ] gateと未検証事項を記録した
- [ ] 親のM1状態を更新した（U04全体はwip）

P1: `uv run pytest tests/integration/test_observation_storage.py -q` は未実装importでred、その後2 passed。対象ruff/ty成功。新規作成の所有と既存・部分資材の保全を確認。構造整理は不要。
