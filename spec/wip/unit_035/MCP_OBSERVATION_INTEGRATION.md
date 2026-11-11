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
| refactor-done | M2-01 加工後の画像と取得元情報が対応し、同じbytesを保存・再読込できる | regression | unit/integration | 原寸・保存試験を置換。旧形式互換なし |
| green | M2-02 保存を止めても応答と次観測が進み、失敗報告・取消し・終了回収が成立する | regression | integration | 既存service試験を改訂 |
| todo | M2-03 R02/R04の現在状態を再現し、原稿と次の明示送信を保つ | regression | unit/GUI | 接続・会話UI拡張前の独立修正 |
| todo | M2-04 観測解像度の適用・取消し・保存が取得再接続を起こさず次観測へ反映される | new | unit/GUI | 既存settings試験へ統合 |
| todo | M2-05 本MCPで同じ画像/IDを返し、認証・停止・旧接続失効が成立する | new | integration | 実HTTPと模擬取得。モデル受信とは区別 |
| todo | M2-06 保存先の新規作成・読込・復元・未選択チャットが接続と終了に結合する | new | GUI/integration | 既存settings/終了所有へ統合 |
| todo | M2-07 永続threadと観測記録が対応し、終了後の公開readと画像参照が成立する | new | integration/runtime | 秘密値の残存も隔離確認 |
| todo | M2-08 実Codexが固定画像と実映像を受信し、保存と同一、停止・終了が進む | new | runtime/hardware | 模擬試験では完了にしない |

## 7. 設計メモ

着手調査で、プロジェクト操作・writer所有・MCP終了回収は既存CodexServiceThreadのループへ統合できると判断した。SettingsWorkerの汎用化や別管理サービスを前提にしない。明示選択writerは切断後も保持し、未選択チャットのwriterは切断後に閉じても記録を削除しない。最終配線はM2-05/06で検証する。

設定結合の注意: 現SettingsDialog.valuesはSettingsを再構築するため、UIに出さない前回パスを失わないよう保存済みsnapshotから更新する必要がある。Shellの既存適用はcapture=Trueなので、観測解像度だけの変更では取得の反映を行わない。一般設定の既存適用・明示再試行を壊さないことも確認する。

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

### M2-01 実装・検証

Observationはcaptured_at、FrameSource（世代・連番・元寸法）、png_bytes、出力width/heightを保持し、mime_typeはimage/png固定とした。整数計算で比率を維持して縮小し、PNGを一度符号化する。JSONL payload.imageにmime_type/width/heightを追加し、新形式へ直接変更した。

RED: `uv run pytest tests/integration/test_observation_storage.py -q --tb=short`は9 failed / 36 passed。新しい取得時刻・解像度引数・画像メタデータ未対応を確認。GREEN: 同commandで45 passed、storage/service/capture_serviceで62 passed。既存試験へoriginal・HD・既定FullHD・縦横比・整数丸め・色/全画面保持を統合した。Frame本体不在だけをassertする試験は追加していない。

rootで`uv run pytest tests/unit tests/integration -q`: 415 passed。`uv run ruff format --check .`（168 files）、`uv run ruff check .`、`uv run ty check --no-progress`、`uv build`、`git diff --check`成功。追加の構造整理は不要と判断。サービスの保存待ちはM2-02で変更するため、現時点の公開docsは同期保存の実装事実を維持する。実MCP/実機は未実施。M2-01は9e80a31へコミット済み。

M2-01の独立レビューで必須修正なし。既存試験に残っていたJSONLの再シリアライズ・書戻しを除き、writerが生成した実バイト列をそのまま再読込検証へ使うよう整理した。`uv run pytest tests/integration/test_observation_storage.py -q`: 45 passed。新規試験や製品動作の変更なし。

### M2-02 実装・検証

観測の加工Taskと保存Taskの所有を分離した。同じObservationを返し、独立した保存へ渡す。現同期writerを再利用するため保存だけasyncio.Lockで直列化し、観測はこのロックを待たない。独自キュー・上限・保存見送りは追加しない。on_save_error（省略時logging）でIDと例外を報告し、drainでは進行中加工と保存を回収する。後続加工が失敗しても先行保存を待ってから加工例外を返す。

RED: `uv run pytest tests/integration/test_observation_service.py tests/integration/test_capture_service.py -q --tb=short`: 4 failed / 13 passed（未対応callback、保存停止中の応答timeout）。GREEN: 同対象18 passed。旧保存中BUSY試験を応答・次観測・失敗通知・取消し回収へ書換え、後続加工失敗時の先行保存回収を1件追加した。待機には明示Eventを使い、短いsleepだけで保存完了を推測しない。

root: `uv run pytest tests/unit tests/integration -q` 416 passed。全体ruff format（168 files）/ruff check/ty、uv build、git diff --check成功。公開API・docsの保存成功と観測成功を分けた。GUI通知への結合とMCPは未実装。

### 実runtimeへのMCP公開経路（製品受入とは別）

`uv run --with mcp==2.3.0 --with httpx==0.28.1 --link-mode copy python -X utf8 tmp/m2-mcp-runtime-probe.py`で実socketとSDK2.3.0、採用app-server0.167.0を使用した。thread/start.configでURL/Bearer/observeのみの公開、mcpServerStatus/listで認証・ツール認識、mcpServer/tool/callで固定PNG一致、Bearer欠落/誤値401、server/lifespan終了を確認。終了後接続は1秒ConnectTimeout。証拠はignored tmp/m2-mcp-runtime-f4m_uvsg/results.json。認証・turn/start・モデル呼出しは0回であり、モデル画像受信ではない。

liveのthread/read(includeTurns:true)は-32601、falseは成功。その後の終了・再起動ではtrueで空threadを読めた。先行試行tmp/m2-mcp-runtime-ydxcz76dではreadなしのEOF直後にrolloutがなく再読込失敗。空threadの保存タイミングは安定保証としない。有効MCP設定のfake bearer単純文字列走査は隔離HOMEで0件だが、実turn/他の保存表現まで非保存を証明しない。通常ターン終端後の会話永続化・秘密値・画像受信はM2-07/08の受入へ残す。
