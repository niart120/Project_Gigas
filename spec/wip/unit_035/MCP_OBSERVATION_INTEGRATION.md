# U04 M2 観測・永続チャット・MCP結合

## 1. 目的・起点・状態

状態: wip（2026-11-12着手）。[U04親](../unit_009/RECORDED_OBSERVATION.md)のM2実装子仕様。利用者の「合意した仕様をコミット後、子仕様を作成し、既存実装・テストを見直して実装・検証する」というGoalを扱う。仕様確定コミットはb7e3ee9。

[合意済み契約](../../complete/unit_034/CHAT_OBSERVATION_PERSISTENCE.md)がIntent Deltaの正本。加工済みObservationをMCP応答と非同期保存へ渡し、保存で応答や次観測を待たせない。未選択チャットにも原記録と永続threadを残す。旧形式の識別・移行・互換維持、保存見送り・キュー・保持上限の先行設計はしない。

## 2. 対象範囲

最新snapshotの縮小と符号化、Observationの変更、保存から独立した応答、既存設定への画像サイズ追加、プロジェクト新規作成・開く・前回復元、未選択チャットの保存先、組込みMCP、永続threadとの対応、終了回収、実接続・実映像の受入。R02/R04は接続・会話UI拡張前に独立した修正コミットで扱う。

## 3. 対象外

入力、Goal自動継続、旧チャットを開くUI、可搬な会話全文の保存（U06）、記憶整理、汎用画像編集、縮小時の元解像度画像の別保存、独立MCPサーバ入口、PR作成・マージ。

## 4. 関連文書・着手条件・調査

[03 I/O](../../initial/03-game-io.md)、[04 保存](../../initial/04-agent-memory.md)、[07 検証](../../initial/07-verification.md)、[08 SDK](../../initial/08-sdk-compatibility.md)、[10 UI](../../initial/10-ui-interaction.md)、[M1実装](../../complete/unit_032/RECORDED_OBSERVATION_IMPLEMENTATION.md)を参照する。

M1コードと既存試験は存在する。MCP・保存先UIは未結合。R02/R04は現在のコード・試験で再確認し、独立した観測処理の改訂を不必要に止めない。採用候補MCP SDK 2.3.0とCodex 0.167.0の実接続・永続thread・短命Bearerの残存は未確認。SDKの模擬HTTP試験を製品の実接続と同一視しない。実接続に必要な経路が成立しない場合は依存する結合を再検討する。

## 5. 振る舞い仕様

- snapshotを一度採用し、新フレームを待たない。ObservationはPNG bytes・出力形式と寸法・ID・元FrameのUTC/世代/連番/寸法を持ち、Frame本体を保持しない。オリジナル/FullHD（既定）/HDのみ。比率維持縮小、拡大なし。確定後は再加工しない。
- 同じ画像とIDをMCPへ包装し、アプリが非同期保存する。保存開始・完了・進行でMCP応答と次観測を待たせない。保存失敗は独立して既存通知・詳細へ報告する。通常終了で進行中の保存を回収するが、障害時の無損失は保証しない。
- 保存形式は新契約へ直接変更する。画像の並列書込は可能で、直列／並列は実装時に選ぶ。プロセスロックとアプリ内の同期を混同せず、共有JSONLの採番・追記を保護する。
- 設定は既存I/Oタブの映像入力グループの観測解像度とTOML observation.resolutionへ統合する。適用済み設定を観測開始時に一度読む。変更だけで取得元・Codexを再接続しない。
- プロジェクトメニューは新規作成・開く。前回パス一件を復元する。未選択チャットはアプリデータ配下chatsへ永続保存する。同じwriterを使い、接続中に保存先を差し替えない。
- 接続ごとのloopback HTTP/Bearerを既存サービス用ループで所有し、切断で旧受付を閉じる。MCPはwriterを所有しない。thread/startは永続化し、実thread IDと記録先を結ぶ。画像応答と実モデル受信は別に検証する。

## 6. TDD Test List

一度に一項目をred→green→検証→コミットする。既存試験の置換・統合を先に行い、内部構造や廃止機能の不在だけを試験しない。

| status | item | type | layer | notes |
|---|---|---|---|---|
| todo | M2-01 加工後の画像と取得元情報が対応し、同じbytesを保存・再読込できる | regression | unit/integration | 原寸・保存試験を置換。旧形式互換なし |
| todo | M2-02 保存を止めても応答と次観測が進み、失敗報告・取消し・終了回収が成立する | regression | integration | 既存service試験を改訂 |
| todo | M2-03 R02/R04の現在状態を再現し、原稿と次の明示送信を保つ | regression | unit/GUI | 接続・会話UI拡張前の独立修正 |
| todo | M2-04 観測解像度の適用・取消し・保存が取得再接続を起こさず次観測へ反映される | new | unit/GUI | 既存settings試験へ統合 |
| todo | M2-05 本MCPで同じ画像/IDを返し、認証・停止・旧接続失効が成立する | new | integration | 実HTTPと模擬取得。モデル受信とは区別 |
| todo | M2-06 保存先の新規作成・読込・復元・未選択チャットが接続と終了に結合する | new | GUI/integration | 既存settings/終了所有へ統合 |
| todo | M2-07 永続threadと観測記録が対応し、終了後の公開readと画像参照が成立する | new | integration/runtime | 秘密値の残存も隔離確認 |
| todo | M2-08 実Codexが固定画像と実映像を受信し、保存と同一、停止・終了が進む | new | runtime/hardware | 模擬試験では完了にしない |

## 7. 設計メモ

新しいサービス用ループ、監視・自動復旧、保存段階の状態表を追加しない。後続の公開会話記録はU06。並列実装を前提にせず、必要な排他範囲を現コードから選ぶ。保存方式と設定の所有を確定した時点で、本節に具体的な選択理由を追記する。

## 8. 対象ファイル

src/gigas/game_io/observation.py、services/observation.py、storage/project.py、storage/settings.py、runtime/codex、app、uiの既存境界と新しいmcp公開部。対応する既存tests/unit・tests/integration、docs、必要な依存とuv.lock。

## 9. 検証

開始時にコード・試験・Gitを確認。各項目のred/greenコマンド・結果をここへ記録する。

- R02/R04監査: FakePeer＋実Connectionで両方未修正を再現。R02はthread/start保留中の停止でturn/startなしなのにQt原稿が消える。R04はfailed終端後もpeerは開いているが次の明示送信が拒否される。既存関連単体試験4 passed, 45 deselectedはこの問題を捕捉していない。実runtime再現ではない。
- 隔離runtime調査: `uv run python -X utf8 tmp/m2-thread-persistence-probe.py`。同梱0.167.0へinitialize→thread/start(ephemeral:false)→EOF終了→再起動→thread/read(includeTurns:true)を実行し、同ID/cwd・空turnsを取得できた。開始直後の同readは-32601（list_turns is not supported yet）。隔離HOMEに明示したfake bearer文字列は残らなかった。認証・モデル呼出しなし、無効化したMCP設定・空threadのみであり、有効MCP/実会話の秘密値非保存や復元を証明しない。証拠はignored tmp/m2-thread-persistence-70_jf54s/results.json。製品構成での検証はM2-07/08に残す。
- `uv lock --check`: 成功（開始時依存55件、依存変更なし）。

必要gate: uv sync --dev、uv lock --check、ruff format/check、ty、unit/integration、uv build、git diff --check。公開APIの型・docstring・利用者docsを照合する。実HTTP・実Codex・実機は別結果として記録する。

## 10. 先送り事項

U05: 入力と事後観測・操作直前の権限照合。U06: 公開会話全文の可搬記録、同一thread再開UI、記憶編集。U08: 再起動後の明示再開。M2内の実接続と実映像は先送りせず受入対象として残す。

## 11. 完了チェックリスト

- [x] 合意済み仕様をコミットし、実装子仕様を作成した
- [ ] 既存実装・試験を見直して各項目を実装・検証・コミットした
- [ ] 静的検査・単体/結合・配布gateを通した
- [ ] 実接続・実映像の受入と残リスクを確認した
- [ ] 公開docsと親仕様を実装実績へ更新した
