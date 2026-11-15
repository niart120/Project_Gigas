# U08 M2 記録と新規会話開始の結合

## 1. 目的・状態

状態: **complete**。[U08親仕様](../../complete/unit_013/RESUME.md)のM2を担当する。管理APIの利用とゲームI/O保存を分離し、Projectあり・なしの新規会話で、実際のthread IDに結び付いた記録を残す。

起点は[M0の確定設計](../../complete/unit_068/MANAGEMENT_STORAGE_DESIGN.md)と[M1の完了](../../complete/unit_069/MANAGEMENT_API.md)。利用者は保存場所や資料の形式を準備しなくても未所属で始められ、Projectを選んだ場合はその作業資料と記録を同じフォルダーで扱える。単独Python利用では呼出し側が指定した記録先だけを必要とする。

## 2. 対象範囲・対象外

GameIoRecordWriterの公開API、5種JSONL・PNG、限定本文、単純追記・排他・drainを実装する。作業場所の選択、MCP準備、thread/start、記録先の結合、通常送信・Goal開始までをつなぎ、旧ProjectWriterとproject.json・固定memoryへの依存を呼出し元から取り除く。既存Projectメニューと公開文書も新しい契約へ合わせる。

設定の実行時適用・標準AGENTS/SkillsはM3、既存会話の選択・再接続はM4、サイドバー・履歴表示はM5。ゲーム攻略性能の評価、独立MCPサーバーの起動機能、旧データ・旧APIの互換、削除・移行・復旧機構は追加しない。実データを削除しない。

## 3. 前提・関連文書

M1は製品実装と実接続の受入を完了した。保存契約は[04](../../initial/04-agent-memory.md)、資源所有は[02](../../initial/02-runtime.md)、公開経路は[08](../../initial/08-sdk-compatibility.md)、開始・停止は[09](../../initial/09-control-flows.md)、受入観点は[07](../../initial/07-verification.md)に従う。

既存のConversationExecution._prepareは通常送信とGoal開始の共通入口である。MCPサーバーはcallableを受け取るため、thread作成前のMCP公開と、作成後のwriter・ObservationService結合を分離できる。仮のwriterやthreadを作る必要はない。管理APIはM1のCodexManagementを用いる。

## 4. 振る舞い仕様

| 境界 | 入力・状態 | 結果 |
|---|---|---|
| 保存部品 | 呼出し側が指定した記録先 | create/openで書込所有権を取得し、04の5種JSONLと元のPNG bytesだけを保存する。Projectやthreadの管理、資料雛形の生成を行わない |
| 追記・画像 | 既存の壊れた行・改行のない末尾、今回の書込失敗 | 過去を走査・修復せず追記する。今回の失敗を伝え、次の新規記録は受け付ける。排他と画像の上書き拒否を維持する |
| 作業場所 | 任意のProject ID、または未所属 | Projectは公開readの唯一のrootを使う。未所属は初回明示実行でunassigned/UUIDを作る。既存の作業フォルダーがなければ再作成しない |
| 新規実行 | 通常送信・Goal開始 | 作業場所とMCPを準備し、cwd・runtimeWorkspaceRootsをその場所にし、所属のprojectId（未所属はnull）をthread/startへ渡す。返却された所属・cwd・実効権限を確認し、応答の実IDを保持してrecords/実IDへ結合した後に実行を提出する。保存先案内のための追加APIやMCP応答変更は行わない |
| 本文 | 既存の提出イベント、確定したmessage | ユーザー提出本文と進捗を含む確定本文だけを保存する。受付成功との照合、最終回答の選別、未完本文の補完を行わない |
| 終了・失敗 | 保存中の停止・切断、PNG/JSONL保存失敗 | 保存をMCP応答・入力・停止の前提にしない。既存通知で失敗を伝え、終了時は提出済みの符号化・保存をdrainしてwriterを閉じる |

保存部品のcreateは親がある新規記録先を作り、openは既存の記録先を開く。open時に必要なevents.jsonl・observationsがなければ作成するが、既存内容の検査・初期化はしない。作業フォルダーとrecords親の作成判断はアプリ側に置き、writerが欠けた祖先を再帰作成しない。

新規会話は作業フォルダーをworkspace-writeの通常書込先とし、追加のwritable rootsは空、network_access=falseと一時領域の除外を維持する。developerInstructionsの保存案内は04の汎用構造と原記録を編集しない運用に置き換え、固定資料名・正確な会話別保存先は要求しない。これらは新しい配置で開始するためにM2で適用し、その他の実行設定と標準指示の読込整理をM3へ引き継ぐ。

Projectの既存メニューは接続済み・会話開始前に公開APIを使う。既存フォルダーは公開一覧から同じrootのProjectを選び、未登録の場合だけ登録する。新規フォルダーは名前と配置先を選び、既存パスと衝突すれば上書きしない。前回パスはピッカーの補助情報として使い、起動時の独自Project読込みを撤去する。選択だけではwriterやゲームMCPを開始しない。

## 5. 設計・責務と実施順

GameIoRecordWriterは同期保存、RecordingServiceは非同期処理の所有・直列化を担当する。ConversationResourcesは作業場所・MCP・観測・writerの寿命を持ち、ConversationExecutionは実thread IDと実行要求を持つ。公開Projectの選択・照合は管理APIを使う部品へ置き、CodexServiceThreadへ一覧解析・位置台帳を集めない。

MCPはResourcesの委譲メソッドへ接続し、実ID取得後に記録先を結び付ける。bind失敗時にも取得済みthread IDを捨てず、次の明示操作は同じthreadへの準備を行う。自動再試行はしない。準備中の取消しでも取得済み資源を所有し、既存の終了経路で回収する。

I1で単独保存API、I2で公開Projectと作業場所の解決を試験し、I3で既存の呼出し元・UI入口をまとめて結合する。I3終了時には旧保存契約を残さない。新旧API間の変換・aliasは作らない。M2の受入はI4の製品経路確認までを含み、部品だけのgreenでは完了しない。各itemのgreen後に責務・重複・依存方向を確認し、必要な構造変更と同じ試験の再実行を記録する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | I1: 単独Python利用で指定先へ5種のJSONLとPNGを残し、履歴破損・単発の書込失敗後も新規追記できる | new/edge | unit/integration | red collection error→新規22件・関連106件green。ID検査の例外範囲を整理し同じ試験でgreen。入力記録型・既存生成元・試験を同時更新 |
| refactor-done | I2: 公開Projectの選択・登録と未所属の新規作業場所を解決し、既存資料を保持する | new/edge | unit/integration | red collection error→11件green。試験の型import・assert・例外範囲を整理して同じ11件green。位置台帳なし |
| refactor-done | I3: 製品の通常送信・Goal開始がcwd/MCP→実thread ID→records結合を通り、限定本文・観測・入力を新形式へ保存する | new/regression | unit/integration | 記録19件・protocol45件・GUI8件をred→green。bind失敗・パス境界もred→green。非同期保存の重複検査・未使用戻り値を整理して再実行。全体871件成功 |
| refactor-skipped | I4: 実app-serverと既存GUIからProjectあり／なしの新規開始・保存・停止を確認する | acceptance | integration/GUI | 実GUIの未所属送信・Project作成とGoal開始/停止/通常送信、実Codex＋模擬frameの観測1回、公開metadataと保存先の一致を確認。製品追加修正なし |

## 7. 対象ファイル

storageの記録API・入力記録型、servicesの記録・観測・会話資源・実行・Codexサービス、runtime/codexのthread設定、app/shellのProject入口、prompts、対応するtests/unit・tests/integration、docsの保存・Codex利用例。親仕様・初期設計には進捗と必要な参照を反映する。

## 8. 検証

各itemのred・green・構造確認を記録する。公開型はtype-boundary-review、公開docstring・利用例はdocstring-style、UI操作はProduct Designで確認する。全体の完了前に独立レビューとagentic-self-reviewを行う。

| command / 確認 | result | 検査対象 |
|---|---|---|
| 対象itemのpytest | I1〜I3成功 | 保存・公開API・開始順序・資源回収の振る舞い |
| uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m2-root -q --tb=short | I3で871 passed（69.09秒） | 旧契約の試験を撤去・置換後の実行・停止・UI回帰 |
| uv run ruff format --check . / uv run ruff check . / uv run ty check --no-progress | I3成功（259ファイル） | 形式・lint・型境界 |
| uv lock --check / uv run python scripts/check_package.py | I3成功 | lock、sdist/wheel内の共通指示、外部の新規環境でcapture worker・module/console起動と終了 |
| 実app-server・既存GUIによる新規開始と停止 | I4成功 | 未所属送信、Project作成・Goal開始・生成中停止・停止後送信・正常終了、実Codexと模擬frameの観測保存を確認 |
| git diff --check、文書内容・参照の確認 | I3差分検査成功 | 独立レビューの旧thread ID記述、GUI通知内容、過去実機結果の範囲を修正 |

I1のred: `uv run pytest tests/integration/test_game_io_records.py --basetemp=tmp/pytest-m2-writer -q`は、gigas.storage.records未実装によるcollection errorを確認した。5種の形式・画像対応、破損の無修復保持、書込失敗後の継続、排他・終了を対象とする。

I1では`GameIoRecordWriter`・`RecordError`・`RecordBusyError`を旧ProjectWriterから独立して実装した。型からthread/turnを削除し、入力生成元と影響する既存試験も更新した。新規22件と関連106件でgreen後、観測ID検査の例外範囲をUUID変換へ絞る構造整理を行い、同じ22件・106件でgreenを確認した（refactor-done）。旧ProjectWriterと現在の非同期保存はI3で置換するまで使用し、新旧を接続する互換処理は追加していない。

関連試験のコマンドは`uv run pytest tests/integration/test_game_io_records.py tests/integration/test_observation_storage.py tests/integration/test_observation_service.py tests/integration/test_input_observation_path.py tests/integration/test_game_io_mcp.py --basetemp=tmp/pytest-m2-writer -q`。

公開型・docstringと新しい単独利用節を照合し、文書の最初のPython例を実行して5種記録・PNG bytes一致を確認した。全体ruff・format（260ファイル）・ty、lock・build・差分検査は成功。独立レビューでは保存形式、PNGの上書き拒否、排他、初期化失敗時の資源回収、部分書込み後の継続に修正必須指摘なし。

全unit/integrationの初回は883 passed・2 failed。切断後の回収試験に入力開始payloadのthread_idを参照する旧期待が残っていたため、削除後の契約へ更新した。操作ID・観測ID・PNGと別会話へ混入しないことの検査は保持した。対象の`uv run pytest tests/integration/test_codex_service.py -k disconnect_drains_old_results --basetemp=tmp/pytest-m2-root -q`は2 passed。

更新後の全体再実行は885 passed（66.21秒）。変更した既存試験のruff・formatも成功。製品の新規開始と新writerの結合はI3、実接続受入はI4で行うため、I1の成功をM2完了とは扱わない。

I2の`uv run pytest tests/integration/test_workspaces.py --basetemp=tmp/pytest-m2-workspaces`は、未実装のservices._workspacesによるcollection errorから11 passedとなった。Projectの公開一覧・read・登録と実フォルダーを境界に、ページ照合、既存内容の保持、新規衝突・登録失敗、欠落rootの非再作成、未所属の独立、遅いファイル操作中のイベントループ継続を検証した。

I2の構造確認はrefactor-done。状態のない2関数にファイル操作を閉じ、選択・接続・記録の所有や位置台帳を加えていない。試験の型import・assert・例外範囲を整理後に同じ11件が成功。対象ruff・format・ty、全体ruff・format・ty、差分検査は成功。独立レビューで修正必須指摘なし。新規の内部部品と試験だけの追加であり、全体pytestとbuildの再実行は製品結合を行うI3に置く。公開API・利用手順はI2では変更していない。

I3の記録呼出し置換は15 failed・4 passedから19 passed、protocolは15 failed・30 passedから45 passed、GUIは7 failed・1 passedから8 passedとなった。初期bind失敗をacceptedとする旧経路をnot_sentへ改め、同じthread IDでの明示再試行を確認した。記録先を逸脱する5種のthread IDは、書込前に拒否する試験でred→greenとした。

対象結合は記録・I/O86件、実行・通知・制御87件、protocol・接続91件、関連GUI80件が成功。取消し中のwriter取得を回収し、切断時に保存drain後に閉じる。公開Project選択の遅い応答は、送信開始・切断・接続世代変更後には採用しない。CodexServiceThreadから記録専用wrapperを削除し、Project一覧照合はI2の部品へ置いた。

I3の構造確認はrefactor-done。ObservationServiceの二重busy検査とRecordingServiceの未使用task戻り値を除き、同じ19件、関連86件を再実行した。core・protocol・GUIでは既存の所有者を使い、追加managerや状態複製は不要と判断した（各部はrefactor-skipped）。独立レビューで修正必須のコード指摘なし。文書のPython例2件を実行し、旧保存APIと固定資料の説明を撤去した。

全体初回は870 passed・GUIのstdio対話1件が要求カード待ちでtimeoutとなった。対象単独とGUI・transport83件では再現せず、原因は未確定。試験ではカード待ちに先立って送信結果を検査するようにし、準備失敗がtimeoutに隠れないようにした。製品や待ち時間は変更していない。その後の全体871件は成功した。

Product Designの実画面確認では、変更前後の1920×1110の画面を比較した。映像・対話の配置を維持し、切断中のProjectメニュー無効、接続後の有効化、初回送信後の無効化、前回Projectの非自動選択を確認した。画像は`tmp/u08-m2-ui-baseline/screenshots`と`tmp/u08-m2-ui-after/screenshots`に保管。Project作成ピッカーと選択後の実行はI4で続ける。

I4は2026-11-15にapp-server 0.167.0、gpt-6.1-sol / low、M1で認証・sandbox準備済みの専用homeで実施した。`uv run python tmp/live_m1.py --root tmp/u08-m1-acceptance-20261115`から起動した製品GUIで、未所属の通常送信・応答・本文2種の保存、切断後の再接続を確認した。新規Projectのネイティブピッカーは起動cwdを初期表示し、指定した`tmp/u08-m2-project-native`を空のフォルダーとして作成・登録した。選択だけではrecordsを生成しなかった。

同ProjectでGoalを開始し、1〜1000の列挙が157まで生成された時点で中断した。Goalの目的を保持した一時停止と、停止後の通常指示への応答を確認し、GUIを正常終了した（exit 0）。recordsは実thread IDの1フォルダーで、提出2件・確定本文1件となった。停止した未完本文は補完保存しなかった。後の`thread/read`・`project/read`・`thread/goal/get`で公開root・cwd・保存先IDの一致とGoalのpausedを確認した。画像は同じafterフォルダーの04〜08、要約は`tmp/u08-m2-project-audit.json`に保管した。

`uv run python tmp/live_m2_observe.py --output tmp/u08-m2-observe-unassigned.json`は実CodexServiceThreadと64×48の赤い模擬Frameを使い、モデル提出1回・observe1回で成功した。HTTP直列化前のMCP CallToolResultと保存PNGの168 bytes・観測IDが一致し、提出1件・進捗を含む確定本文2件・観測1件を保存した。turn完了、共通停止、保存drain、終了が成功し、記録エラーはなかった。実thread/startの未所属、cwdとruntimeWorkspaceRoots、workspaceWrite・追加rootsなし・network=false・一時領域除外・on-request/userは要求と一致した。補助プログラムの最初の試行は型注釈の実行時import不足でMCP起動時に失敗したが、オフラインで修正を確認してから初回モデル提出を行った。製品変更・指示再送は行っていない。

`uv run python tmp/audit_m2_public.py --workspace <対象作業場所> --output <要約先>`でGUIと観測の未所属2会話、Project1会話を公開metadataだけで照合した。未所属の結果は`tmp/u08-m2-public-audit.json`、Projectは上記要約へ保存した。cold readが公開しない実効権限はthread/startの確認と区別した。JSONLの共通4項目、本文textのみ、観測のID・取得時刻・相対画像パス・両寸法を確認した。製品コードの変更はなく、I4の構造変更は不要と判断した（refactor-skipped）。

## 9. 先送り事項

M3以降は親仕様の範囲と順序に従う。既存会話のrecords再利用・履歴閲覧時にファイルを作らないことはM4で結合確認する。新規Projectのサイドバー管理はM5で追加する。M2の画像受入は模擬Frameであり、USB Video Device・COM8を含むGUIから実機の通し受入はM7へ残す。数時間運転はU10。I3の全体試験で一度だけ発生した要求カード待ちtimeoutは原因未確定だが、対象83件と全体再実行では成功しており、再発時に送信結果を先に確認できる試験へ改めた。

## 10. 完了チェックリスト

- [x] M1の完了とM2の範囲・責務を確認した
- [x] I1〜I4を検証し、各cycleの構造確認を記録した
- [x] 旧保存契約と固定資料入口を呼出し元・公開文書から撤去した
- [x] 型・docstring・公開docs・配布資材を実装へ照合した
- [x] 必要な品質ゲートと製品経路の受入を完了した
- [x] 親仕様・初期設計の進捗を更新した
