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

着手時はM1コードと既存試験が存在し、MCP・保存先UIは未結合だった。R02/R04を再確認・修正したうえで結合した。現在はMCP SDK 2.3.0とCodex 0.167.0による製品経路の固定画像受信、後のターンでの保存画像参照、終了後の公開thread/readを確認済み。短命Bearerは当該実会話のrolloutと今回のアプリ記録に残らなかった。走査範囲を超える非保存や実機受入の証明とはしない。詳細と残件は第9節に記録する。

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
| refactor-skipped | M2-02 保存を止めても応答と次観測が進み、失敗報告・取消し・終了回収が成立する | regression | integration | 既存service試験を改訂 |
| refactor-skipped | M2-03 R02/R04の現在状態を再現し、原稿と次の明示送信を保つ | regression | unit/GUI | 接続・会話UI拡張前の独立修正 |
| refactor-skipped | M2-04 観測解像度の適用・取消し・保存が取得再接続を起こさず次観測へ反映される | new | unit/GUI | M2-06/07でサービスループへ配線し、次の実HTTP観測への反映も確認 |
| refactor-skipped | M2-05 本MCPで同じ画像/IDを返し、認証・停止・旧接続失効が成立する | new | integration | 実HTTPと模擬取得。モデル受信とは区別 |
| refactor-skipped | M2-06/07 保存先操作・復元・未選択チャット・永続thread対応が接続と終了に結合する | new | GUI/integration/runtime | 製品・模擬検証、実会話の公開read、ネイティブ作成・開く・再起動復元を確認 |
| todo | M2-08 実Codexが固定画像と実映像を受信し、保存と同一、停止・終了が進む | new | runtime/hardware | 固定画像・保存PNGの後参照は成功。実機受入は機器選択待ち |

## 7. 設計メモ

着手調査で、プロジェクト操作・writer所有・MCP終了回収は既存CodexServiceThreadのループへ統合できると判断した。SettingsWorkerの汎用化や別管理サービスを前提にしない。明示選択writerは切断後も保持し、未選択チャットのwriterは切断後に閉じても記録を削除しない。最終配線はM2-05/06で検証する。

設定結合の注意: 現SettingsDialog.valuesはSettingsを再構築するため、UIに出さない前回パスを失わないよう保存済みsnapshotから更新する必要がある。Shellの既存適用はcapture=Trueなので、観測解像度だけの変更では取得の反映を行わない。一般設定の既存適用・明示再試行を壊さないことも確認する。

新しいサービス用ループ、監視・自動復旧、保存段階の状態表を追加しない。後続の公開会話記録はU06。並列実装を前提にせず、必要な排他範囲を現コードから選ぶ。保存方式と設定の所有を確定した時点で、本節に具体的な選択理由を追記する。

M2-06/07の結合方針: connect/loginは既存のruntime用cwdで行い、初回sendの中で保存先・ObservationService・MCPを準備する。保存先の障害をログイン失敗にしない。runtimeの既存送信排他・停止・取消しを再利用し、thread/start前の非同期準備と実thread ID受領後の非同期対応記録を、アプリ側callbackの二つの境界で待つ。writerやSDKサーバはruntimeへ渡さない。対応記録後にだけturn/startへ進み、準備失敗は既存の通知で報告して原稿と認証済み接続を保つ。実thread IDと保存先を診断へ残し、空threadの自動削除や自動再送は行わない。

ProjectWriterにはthread.started行の追記と観測行のthread ID引数を追加し、現在thread・索引・登録済みID集合を持たせない。チャット外のPython観測APIにはthreadを要求しないが、アプリからのチャット観測には実IDを渡す。同期I/Oの待機が取消されても、取得したwriterと進行中書込の回収を終えるまで所有を保つ。

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
- [x] 静的検査・単体/結合・配布gateを通した
- [ ] 実接続・実映像の受入と残リスクを確認した
- [x] 公開docsと親仕様を実装実績へ更新した

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

M2-02は3b15b6eへコミット済み。独立レビューで修正必須なし。追加の構造整理は行っていない。

### M2-03 R02/R04修正

R02は原稿消去を空エラーのServiceResultから既存TurnStartedへ移した。送信後に編集した原稿を一致条件で保護する。R04はfailed終端でfailure_idと理由を残してready（停止要求中はstopped）へ戻し、次の明示送信を許可する。通信失敗のclose/errorは変更せず、自動再送や新しい状態を追加しない。

RED: 既存GUI送信試験と既存terminal試験の選択実行で2 failed / 1 passed（原稿消失、error状態からの復帰不能）。GREEN: 接続・GUI対象59 passed、停止有無を既存terminal試験へ加えて61 passed。全体`uv run pytest tests/unit tests/integration -q`は418 passed。独立レビューの提案で受理通知前に原稿を編集する場合も既存GUI試験へ統合し、最終の接続・GUI対象は62 passed。最終追加はテストだけで、全体試験の再実行は省略。実Codex接続の回帰確認はM2-08で実施する。

レビュー採否: M2-01の不要なJSONL再書込削除とM2-03の編集原稿保持ケースを採用した。M2-02は所有・取消し・drainを確認し追加修正なし。通常の非同期保存に必要なTask集合と標準Lockだけを持ち、監視・保存見送り・上限を追加していない。

M2-03は30df8e6へコミット済み。追加の構造整理なし。

### 実接続・機器の事前確認

2026-11-12、現create_connectionを既定認証HOMEでopen/closeし、ready・authenticated=true・catalog既定gpt-6.1-sol/lowを確認した。sandbox内では接続失敗となり、同じ非推論確認を承認済みの権限で再実行して成功。新規ログイン・モデル呼出しは行っていない。公式App Server資料も再読したが、採用版の成立根拠はローカル実行結果を優先する。

現在の実機一覧はElite Go GC313Pro、USB Video Device、GV-USB2, Analog Capture。保存済み機器はこの一覧に一致しない。機器は開かず、実映像受入の選択を利用者へ問い合わせた。実装・模擬検証はこの回答と独立して進める。

### M2-04 設定部分の実装・検証

Settings.observation_resolutionとTOML observation.resolutionを追加し、既存I/O→映像入力の3択へ配線。保存・適用・取消し・保存失敗は既存経路を使う。観測解像度だけが変更された場合だけcapture反映を外し、一般設定のみ・無変更適用の従来動作は維持する。設定UIや値のための状態管理サービスは追加していない。

RED: video_settings/settings_apply_contract/settings_acceptの既存試験改訂で16 failed / 57 passed。GREEN同対象73 passed、関連settings/GUI8ファイル116 passed。root全体`uv run pytest tests/unit tests/integration -q`: 430 passed。全体ruff format（168files）/ruff check/ty、uv build、git diff --check成功。設定コードとGUIの実ラベルを読んで確認した。実画面の目視は最終結合時に行う。

現アプリにObservationServiceの所有がまだないため、未使用hookは作らず、適用値はbaselineへ保持する。サービスループへの反映と次観測に効くことはM2-06の結合条件に残し、設定部分のgreenを全結合完了とは扱わない。

### M2-05 MCP公開部の実装・検証

ObservationMcpServerは既存サービスループで使うloopback HTTP公開部とし、接続ごとの名前とBearer、OS割当ポートを持つ。MCP SDK 2.3.0のstateless HTTPとuvicorn 0.54.0を使用。observeは同じObservationのPNGをbase64化して画像とJSONテキストを返し、保存を待たない。writerや記録先は所有しない。受付終了後のHTTP/SDK回収はclose、保存回収はアプリからObservationService.drainを呼ぶ。SDK起動失敗でUvicornがSystemExitを出す場合もアプリ終了にせず起動例外へ変換する。

RED: 新しい実HTTP試験はgigas.mcp未実装により収集失敗。GREEN: `uv run --no-sync pytest tests/integration/test_observation_mcp.py -q` 4 passed。保存を停止したまま二つの応答、保存PNG/ID/メタデータとの一致、Bearer/Host/Origin拒否、旧Bearer拒否、起動失敗、close取消し後の再回収を確認。固定Frameを使い、実モデルの画像受信とは区別する。

`uv run --no-sync pytest tests/unit tests/integration -q --basetemp=tmp/pytest-mcp-full`: 434 passed。ruff format（172 files）/ruff check/ty、uv lock --check、uv build、git diff --check成功。rootで公開API、所有、認証、応答の型とdocsを照合し、追加の構造整理なし。

依存の宣言・lockはuv addで更新したが、起動中のgigas.exe（PID65012）が実行ファイルを保持しているため同期は承認済み権限でも失敗した。アプリを停止せず、必要依存だけuv pip installで反映し、試験は--no-syncで実行した。`uv sync --dev`の最終成功は未確認として残す。Codexへの製品配線・実モデル・GUI・実機は後続の受入に残る。

### M2-06/07 保存先・永続thread・終了の結合

CodexServiceThreadが既存ループ上でwriter・ObservationService・MCPを所有する。接続/ログインはruntime用cwdで行い、初回sendのprepare_threadで明示選択先またはchats/<UUID>を用意する。thread/startはephemeral:falseで実保存先をcwdとし、on_thread_startedが実IDのthread.started行を保存した後にturn/startへ進む。writerへ現在threadや索引は持たせず、観測開始時に実thread IDを固定して保存引数へ渡す。対応記録失敗は原稿・認証を保ち、実IDと保存先を通知へ残す。

「プロジェクト」メニューの新規作成/開く、TOMLのproject.last_path（Settings.last_project_path）の復元を既存設定I/Oへ統合した。既存データを上書きせず、失敗時は選択済みwriterを保持。未選択チャットの記録先は切断後も削除しない。設定編集は保存済みsnapshotから更新し、UIにない前回パスを消さない。記録失敗をCodex失敗で上書きしないため、既存通知の発生元に「記録」を追加した。主画面の説明文や専用保存画面は追加していない。

受付終了と保存回収は既存サービスの終了へ統合。同期I/O待機の取消しでもwriter取得・追記を回収する短いhelperを使う。保存先操作/準備/終了のロックは同じwriterの所有保護だけに使い、observeは取得しない。明示選択の有無はboolだけとし、writer.pathを重複保持しない。保存方式は既存同期writerを標準async Lockで順序付ける選択を維持した。

RED/GREEN: storage/service既存試験の改訂は17 failed / 45 passed→62 passed。runtime既存試験の改訂は7 failed / 50 passed→57 passed、受信2 MiBの境界追加は1 failed / 8 passed→runtime/transport合計79 passed。送信上限は1 MiBのまま。GUI/settingsは新項目未対応4 failedと旧service構成による4ケースの失敗を確認後、関連86 passed。fixture自体の誤用による失敗はREDの根拠から除いた。サービス結合は新構成未実装3 failed→全service 20 passed。観測設定の次HTTP呼出しへの反映、選択/未選択の記録先、実ID関連、保存中終了の応答性を確認した。

独立レビューで二件を採用した。runtime自発終了後も旧MCP要求がHTTP200で成功することを実socketで再現し、既存closedイベントを終了処理へ接続した。MCPclose失敗時のdrain未到達は、実HTTP終了後の例外と保存停止を組み合わせて再現した。回帰試験3 failed / 2 passed→全service 23 passed。共有の終了Taskで同時closeと通知からの再入をまとめ、HTTP終了失敗を報告しながらfinallyで保存とwriterを回収する。監視・自動復旧・保存上限は追加していない。再レビューで追加指摘なし。

並行担当のpytestが既定一時フォルダーを共有して失敗したため、担当ごとのbasetempへ分けて対象試験を再検証した。各担当のruff/tyは成功。全体gateは全担当の編集停止後にrootで実行する。実モデル・終了後公開thread/read・実映像・GUI目視はこの模擬検証では完了とせず、M2-08に残す。

rootの最終gate: `uv run --no-sync pytest tests/unit tests/integration -q --basetemp=tmp/pytest-m2-lifecycle-full` 465 passed（42.59秒）。ruff format（173 files）、ruff check、ty、uv lock --check、uv build、git diff --check成功。uv syncは再試行したが、承認済み権限からのプロセス確認でもPID65012のGigasが継続しており、実行ファイル置換で失敗した。アプリ終了の依頼は保留し、当該gateを成功としない。

M2-05は0cab964、M2-06/07の製品結合はa269a24へコミット済み。変更Markdown12件のローカル参照141件と35unitの一意性を確認した。

### M2-08 固定画像の実モデル受入と承認経路の修正

`uv run --no-sync python -X utf8 tmp/m2-product-model-probe.py --run-model`を承認済み権限で実行。既定の製品factory、既存ChatGPT認証、gpt-6.1-sol/low、固定4K Frameを使用した。初回（tmp/m2-product-model-buz3w2e5/results.json）はthreadの永続化と終了後公開readに成功したが、observeは失敗し、画像記録0件だった。公開readのMCPエラーはuser rejected MCP tool call。利用者の拒否操作ではなく、製品が対応していない承認経路で拒否されたものとして調査した。

モデルを使わない公開mcpServer/tool/callでは製品MCPの画像返却・保存に成功した（tmp/m2-direct-product-mcp-rlstaikh/results.json）。この限定RPCはモデル経由の承認を通らず、以前の限定成功では承認経路を検証できていなかった。[採用tagの判定](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/mcp_tool_call.rs)と[MCP ToolAnnotations](https://modelcontextprotocol.io/specification/2025-06-18/schema#toolannotations)を確認。annotations未指定では破壊的変更/開いた外界との相互作用を既定で仮定する。

observeにdestructiveHint:false、openWorldHint:falseを付けた。操作対象はアプリの映像と記録先に限られ、保存は新規画像と追記で既存記録を破壊しない。保存の副作用があるためreadOnlyHint:trueやidempotentHint:trueとはしない。承認UIの追加、既存承認の自動応答、全ツールの許可設定変更は不要だった。既存実HTTP試験へtools/listの公開契約を統合し、RED 1 failed / 3 passed（annotations欠落）→GREEN 4 passed。対象ruff/ty成功。

同じ実モデル受入を再実行した結果（tmp/m2-product-model-f0zxgi8v/results.json）、中央のランダム英数字・四隅色・観測IDが全一致した。observeは一回、保存PNGは3,802,894 bytesで加工結果とhash一致、記録失敗なし、通常終了とwriter再open成功。終了後に新しいapp-serverの公開thread/read(includeTurns:true)で同ID/cwd・ユーザー入力・観測ID付き回答を再読込できた。

実MCP完了通知は切詰め済みtextで、受信JSON一行は1,049,142 bytesだった。旧受信1 MiBを超え、変更した2 MiB内に収まった。公開thread/readのMCP結果も切詰め済みで、通知や履歴のbase64から原PNGを復元する経路ではないことを実測した。モデル内部のリサイズ等までPNG bytes一致と保証するものではない。この初回成功時点では実機確認・保存画像の後参照・実turn時の秘密値残存・GUI目視を未完了とし、後続の確認結果を以下に分けて記す。

修正後の局所回帰: MCP/サービス27 passed、全体ruff format/check・ty・uv build・git diff --check成功。全体465件はa269a24時点の結果であり、この修正では影響する実HTTP/サービスと実モデルを再検証した。

### M2-08 保存画像の後参照・会話記録の実検証

承認経路修正はae2136bへコミット済み。`uv run --no-sync python -X utf8 tmp/m2-product-model-probe.py --run-model --verify-reopen`を同じ製品factory・認証・採用版で実行した。ignored `tmp/m2-product-model-q2zg1zw4/results.json`に、2ターンとも正常終端、observe一回、別ターンのimageView一回を記録した。初回は画像内のランダム英数字・四隅色・観測IDが一致し、次ターンは保存PNGを開き直して同じ英数字とIDを回答した。再観測はない。imageViewのturn IDと保存パスを照合しており、回答だけの一致を後参照成功の根拠にしていない。

保存PNGは3,796,983 bytes、3840×2160から1920×1080への加工結果とSHA-256が一致。thread.startedとobservation.recordedの実thread IDが対応し、通常終了後のwriter再openに成功した。別app-serverの公開thread/readは同ID/cwd・2ターンの入力と回答・保存画像参照を返した。MCP完了通知のJSON一行は1,049,142 bytesで、通知と履歴のMCP結果は切詰め済みtextだった。

R02/R04はM2-03の模擬RPC・実Qtによる固有境界の回帰確認に、この実モデル2ターンの通常接続回帰を合わせて修正受入とする。実runtimeで受理前中断・failed終端を誘発しておらず、両不具合の実Codex上での再現・修正確認をしたとは扱わない。当該障害経路は未検証リスクとして残し、実映像の必須受入とは区別する。

Bearerはメモリ内で捕捉し、その完全値・接頭辞を除いた値とJSONエスケープ表現を照合した。当該threadの公開pathで特定したrollout 1件（10,319,942 bytes）と今回のアプリ生成ファイル7件で一致0、読取失敗0。既定HOME全体・認証ファイル・設定DBは読んでいない。隔離HOMEでの非モデル確認と、この実turnの限定走査は別の証拠として扱う。秘密値やraw応答を検証結果へ保存していない。

### 実画面と最終レビューの確認範囲

検証用の別ApplicationShellを`tmp/m2-ui-acceptance/settings.toml`で起動し、computer-useで「プロジェクト」→「新規作成」「開く」のメニューと新規作成のネイティブダイアログ表示・取消しを確認した。sandbox起動では入力が反映されなかったため、同じ検証アプリを通常の実行権限で再起動した。その実行では入力でき、`native-project`の作成、タイトルへの選択パス表示、TOMLのproject.last_path保存、同じフォルダーを「開く」、正常終了後の再起動による自動復元を確認した。最後に通常終了してexit 0とウィンドウ消失、別writerでの再openを確認した。製品コードに入力回避策は追加していない。

設定I/Oの「観測解像度」でオリジナル/FullHD/HDの3択を目視し、HDを選択・適用してTOMLのobservation.resolutionがhdになることを確認した。検証用ウィンドウは通常終了し、process exit 0とウィンドウ消失を確認した。起動中の利用者のGigasは操作していない。sandbox起動の機器列挙は失敗表示となったため、実機接続の成功には含めない。

独立レビューでは、同一Observationの分岐、符号化だけの同時実行制限、保存だけの標準Lock、終了回収用Task所有が合意した5条件に整合し、追加すべきキュー・上限・保存見送り・互換処理はないと確認した。`uv run --no-sync pytest tests/integration/test_observation_storage.py tests/integration/test_observation_service.py tests/integration/test_observation_mcp.py -q --basetemp=tmp/pytest-observation-finalreview`は66 passed。公開docsに残る旧未接続記述と保存失敗のdrain伝播説明を修正した。

起動中Gigasの実行ファイル保持を避けるため、`UV_PROJECT_ENVIRONMENT=tmp/m2-clean-environment`を指定した新規環境で`uv sync --dev`を実行し、82件解決・73件導入に成功した。同環境の`uv run --no-sync pytest tests/unit tests/integration -q --basetemp=tmp/pytest-m2-clean-environment`も465 passed（64.57秒）。利用者の既存`.venv`は再同期していないが、新規導入のgateは満たした。同期のために利用者へGigas終了を求める必要はなくなった。

残る受入は指定機器による実映像であり、機器選択への回答は未着。明示した機器一件だけを使うignored `tmp/m2-hardware-model-probe.py`を準備し、dry-runと構文のみ確認した。機器列挙・取得・実モデル実行はまだ行っていない。M2子・U04親はwipを維持し、固定画像の成功だけで実機まで完了にしない。

今回更新した10文書を実装・上記証拠と照合し、ローカルリンク129件の参照先、3状態を通したunit番号35件の一意性、仮テキスト残りを検査した。エラー0件、`git diff --check`成功。文書だけの最終追記に対して製品テスト・型検査・buildは再実行せず、先行する製品検証と文書検証を区別した。
