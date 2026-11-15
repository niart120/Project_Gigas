# U08 M1 専用home・設定・管理API

## 1. 目的・状態

状態: **complete**。[U08親仕様](../../complete/unit_013/RESUME.md)のM1を担当する。M0の[確定設計](../unit_068/MANAGEMENT_STORAGE_DESIGN.md)を製品の起動・設定・管理APIへ反映し、既存の新規実行・停止を維持する。

利用者はGIGAS専用のCodexへ接続・ログインし、Project・会話・履歴・Goalの保存情報を公開APIから利用する。履歴を読むだけでは会話を実行しない。

## 2. 対象範囲・対象外

専用homeによる起動、GIGAS設定の保存、Projectのcreate/list/read、所属・未所属thread一覧とread、turn/itemページ、Goalのget/clearを扱う。認証・設定のコピーや同期、位置台帳、独自履歴保存は設けない。

記録部品と新規開始の置換はM2、設定の実行時適用と標準指示はM3、選択と実行対象の分離・再接続はM4、会話管理UIはM5。後続の未着手子仕様は作らない。M1で表示や記録の独自実装を先行追加しない。

## 3. 前提・関連文書

M0はcomplete。採用版は0.167.0。契約は[02 実行](../../initial/02-runtime.md)、[04 保存・設定](../../initial/04-agent-memory.md)、[08 公開経路](../../initial/08-sdk-compatibility.md)、受入は[07](../../initial/07-verification.md)に従う。APIの入出力は同梱runtimeから生成したschemaと採用版ソースに照合する。

## 4. 振る舞い仕様

| 境界 | 入力・状態 | 結果 |
|---|---|---|
| 起動 | GIGASの設定ファイル配置 | 同じアプリ領域のcodex_homeを接続生成に明示指定。親プロセスのCODEX_HOMEを継承せず、認証等をコピーしない。接続前は起動・保存しない |
| 設定 | Web検索・標準記憶・承認方法 | settings.tomlに保存。Web/記憶は既定ON、承認者はauto_review既定/user選択。記憶OFFでも既存ファイルを削除しない。実行への適用はM3 |
| Project | 1件の絶対rootをcreate、list/read | サーバー応答を返し、別の位置正本を作らない。実ファイルの作成や選択は呼出し側の責務 |
| 会話 | 所属IDまたは未所属を指定、保存済みthread ID | 公開一覧・metadata・履歴ページを取得。resume・MCP・writer・機器操作を起動しない |
| Goal | 保存済みthreadのget/clear | 現在Goalを取得・削除。実行準備を要求せず、削除は停止ではない |

## 5. 設計・責務

接続と認証・単一受信はCodexConnection、プロセス構築はlaunch、設定永続化はstorage/settingsが所有する。管理要求の形式・応答変換は会話実行とは独立したruntime/codexの部品へ置く。管理APIのためにConversationExecutionやCodexServiceThreadへProject/履歴の状態・変換を集中させない。

各TDD itemのgreen後に依存方向・重複・クラスの責務を確認し、必要な整理と同じ試験の再実行を記録する。ファイル分割だけの空基盤や、未実装を成功にする仮経路は追加しない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | I1: 既定サービス接続はGIGAS専用homeを使い、接続前は作成せず、開発用homeを変更しない | new | integration | 起動・終了と既存接続の回帰を確認、70bc1f7 |
| refactor-skipped | I2: Web・記憶・承認者の既定値、保存往復、不正値拒否、他設定の保持 | new | unit/integration | UIと実行への適用はM3/M5。red10→関連70件green、全814件green |
| refactor-skipped | I3: Project create/list/readは唯一のrootと公開応答を扱い、RPC失敗を伝える | new | unit/integration | red collection error→新規19件green、接続と合わせ65件green。実runtimeで作成・一覧・read・再起動後readを確認、0f955d2 |
| refactor-done | I4: 所属・未所属一覧、thread read、turn/itemのページ取得が実行を開始しない | new | unit/integration | 新規17件red→I3込み36件green、接続を含む82件green。公開型・クライアント・応答変換を分離、298c5e8 |
| refactor-skipped | I5: 保存済みGoalのget/clearが実行準備なしで成立し、削除を停止扱いしない | new | unit/integration | 新規11件red→green、管理・接続・既存Goal解析を含め131件green。実runtimeのcold get/clearも確認、87e88af。UI結合はM4/M5 |
| refactor-done | B1: ブラウザ起動APIが失敗したときに通知し、URL・認証待ち・接続を保持する | bug | integration | I6で見つかった既存ログイン経路の修正。新規2件red→対象4件green、関連65件green。ブラウザの実起動失敗原因は未確定 |
| refactor-skipped | I6: 専用homeの起動・認証、管理API永続化、既存の新規実行・停止が製品経路で成立 | new | integration/acceptance | 本人ログイン、再起動後の保存済み認証、sandbox準備と再接続、通常応答・実行中停止・停止後応答・正常終了を実GUIで確認 |

## 7. 対象ファイル

src/gigas/runtime/codex、services/codex.py、storage/settings.py、対応するtests/unit・tests/integration、docs/codex.md・設定の利用文書。UIの再配置は対象外。

## 8. 検証

以下のプローブコマンドは完了後の配置で記載する。実行時は同じ資材をwip配下に置いていた。各itemのred→green→構造確認とcommitを分ける。対象試験、ruff、ty、全unit/integration、公開APIのbuild、git diff --checkを変更範囲に応じて行う。実app-serverの確認と認証は専用homeで行い、利用者の開発用資格情報をコピーしない。

I1の専用home経路を実装。初回redはCODEX_HOME未指定、レビュー後の追加redはhome未作成。`uv run pytest tests/integration/test_codex_home.py tests/integration/test_codex_service.py tests/integration/test_app_entry.py -q`は41 passed。ruff・tyは成功、`uv lock --check`・`uv build`・差分検査は成功。

I1の構造確認はrefactor-skipped。専用homeは既存factoryへpartialで束縛し、作成は既存launchの明示home経路に置いた。サービスへ設定や認証コピーの状態を追加していない。レビュー指摘のhome実在条件を修正し、同じ試験でgreenを確認した。[実runtimeプローブ](assets/probe_home.py)を`uv run python -X utf8 spec/complete/unit_069/assets/probe_home.py --output-root tmp/u08-m1-home-20261114-02 --result spec/complete/unit_069/assets/probe-home-result.json`で実行し、終了コード0。[結果](assets/probe-home-result.json)は専用home作成・login_required・非認証・正常終了・auth.json未作成を示す。本人認証・管理APIの結果は後述のI3〜I6に記録する。

I1の全unitは349 passed、全integrationは455 passed。全体ruffではM0の既存調査資材に53件の違反があり、別の資材修正として解消済み。全体ruff・format・tyも成功。I1の製品・テスト・調査資材のruff、全体format、tyは成功。実機入力はM1のAPI確認には不要。M7でUSB Video Device・COM8を使えることはユーザー承認済み。

I2: Web・記憶の既定ON、承認者auto_review/userをSettingsとTOMLへ追加。新規10件は未実装フィールドと不正値未検査でred、既存設定・アプリ設定を含む70件がgreen。構造確認はrefactor-skipped（既存bool検査を項目名付きへ一般化して共用し、永続化へruntimeや記憶削除を持ち込んでいない）。独立レビューで修正必須指摘なし。全体ruff・format・tyとbuildは成功。全unit/integrationの814件成功後、並走させた追加試験が共通basetempと競合したため、pytestを直列化して全814件を再実行し、63.74秒・終了コード0で確認した。

I3: `CodexManagement`と不変の`ProjectInfo`・`ProjectPage`を追加。採用版schemaの`idempotencyKey`、`roots=[{path: ...}]`、`projectId`、`data`と`nextCursor`に照合した。`uv run pytest tests/unit/test_codex_management.py --basetemp=tmp/pytest-m1-projects -q`は管理モジュール未実装によるcollection errorでred、実装後19 passed。未認証・無効モデル・Sandbox未準備でも管理要求を配送し、相対root、不正応答、複数roots、RPC拒否を検査した。

I3の構造確認はrefactor-skipped。管理部品は既存の接続requestとProject応答変換だけを所有し、ファイル作成・選択状態・位置台帳・再試行を持たない。create/readで同じ応答変換を共用し、接続・会話実行・サービスへ管理責務を追加していない。`uv run pytest tests/unit/test_codex_management.py tests/unit/test_codex_connection.py --basetemp=tmp/pytest-m1-projects -q`は65 passed。全unitは368 passed、全体ruff・format・ty、実runtimeプローブのty、build・差分検査も成功。公開型とGoogle形式docstring、`docs/codex.md`の入力・戻り値・例外を照合した。

I3の[実runtimeプローブ](assets/probe_projects.py)を`uv run python -X utf8 spec/complete/unit_069/assets/probe_projects.py --output-root tmp/u08-m1-projects-20261114-01 --result spec/complete/unit_069/assets/probe-projects-result.json`で実行し、終了コード0。[結果](assets/probe-projects-result.json)は製品APIによるProject作成・一覧・read、再起動後のID・名前・rootの保持、未認証と両接続の正常終了を示す。新規の隔離homeを使い、認証コピー・モデル実行・機器操作は行っていない。本人認証と残る管理APIは後述のI4〜I6で確認した。

I4: `list_threads`・`read_thread`・`list_turns`・`list_items`を追加した。`projectId:null`は未所属、`modelProviders:[]`は全プロバイダーとし、metadata取得は`includeTurns:false`、turn取得は`itemsView:full`を明示する。本文・公開itemの種類別payloadを保持し、各ページの`nextCursor`・`backwardsCursor`を解析せず返す。独立したschema調査でも、turn要求の既定summaryと応答の既定fullの違い、item一覧のturnId付きwrapper、19種のitem共通id/typeを確認した。

`uv run pytest tests/unit/test_codex_history.py --basetemp=tmp/pytest-m1-history -q`は未実装の4メソッドで17 failedを確認した。実装後、I3を含む36 passed。green後に値型を`management_models.py`、応答変換を`_management_protocol.py`へ移し、公開クライアントを要求の配送へ絞った。`management`からの型importは維持し、接続・会話実行・サービス・GUIには管理状態を追加していない。構造変更後の`uv run pytest tests/unit/test_codex_history.py tests/unit/test_codex_management.py tests/unit/test_codex_connection.py --basetemp=tmp/pytest-m1-history -q`は82 passed。対象ruff・format・差分検査と全体tyは成功し、型境界にAny・cast・ignoreを追加していない。公開型のdocstringと`docs/codex.md`を入力・戻り値・例外・ページ単位の振る舞いに照合した。独立レビューではschemaとの不一致・本文欠落・責務の混在による修正必須の指摘なし。実runtime・統合gateは以下に記録する。

I4の[実runtimeプローブ](assets/probe_history.py)を`uv run python -X utf8 spec/complete/unit_069/assets/probe_history.py --output-root tmp/u08-m1-history-20261115-02 --result spec/complete/unit_069/assets/probe-history-result.json`で実行し、終了コード0。[結果](assets/probe-history-result.json)は所属別一覧、保存cwd、全文・turn/item対応、cursor、プロセス再起動後のresumeなしの履歴取得を示す。利用不能なローカル接続先へ合成本文を提出し、公開item確定後に明示中断して履歴を作った。モデル応答・認証・ゲーム操作の確認ではない。全unitは385 passed、全体ruff・format・ty、lock・build・差分検査も成功した。

I5: `get_goal(thread_id) -> GoalState | None`と`clear_goal(thread_id) -> bool`を追加。getはGoalの省略・nullをNoneとし、clearは明示ユーザー操作の`origin:user`を送り、`cleared`の真偽値を返す。`uv run pytest tests/unit/test_codex_management_goals.py --basetemp=tmp/pytest-m1-goals -q`は2メソッド未実装による11 failedから11 passedとなった。未認証・モデル不正・Sandbox未準備、保存cwd欠落でも実行準備を要求せず、clearは事前get・resume・停止・通知待ちを追加しない。

I5の構造確認はrefactor-skipped。要求生成・Goal解析・削除結果の検査は既存`goal_params`・`goal_state`・`goal_clear_result`を共用し、GoalState以外の独自型や状態を増やしていない。`uv run pytest tests/unit/test_codex_management_goals.py tests/unit/test_codex_management.py tests/unit/test_codex_history.py tests/unit/test_codex_connection.py tests/unit/test_conversation_protocol.py --basetemp=tmp/pytest-m1-goals -q`は131 passed。対象ruff・format・差分検査と全体tyは成功し、公開docstring・`docs/codex.md`を戻り値・例外・副作用に照合した。独立レビューでschemaとの不一致や実行経路への依存による修正必須指摘なし。実runtime・統合gateは以下に記録する。

I5の[実runtimeプローブ](assets/probe_goals.py)を`uv run python -X utf8 spec/complete/unit_069/assets/probe_goals.py --history-root tmp/u08-m1-history-20261115-02 --result spec/complete/unit_069/assets/probe-goals-result.json`で実行し、終了コード0。[結果](assets/probe-goals-result.json)は、隔離homeの保存済みGoalをプロセス再起動後に未認証・モデル未選択で取得・削除でき、threadがnotLoadedのまま維持されること、削除後のgetと再clearが不在を返すことを示す。実モデルや機器操作は行っていない。`uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m1-root -q`は861 passed（66.07秒）。全体ruff・format・ty、lock・build・差分検査も成功した。

B1: `QDesktopServices.openUrl`がFalseを返しても結果を捨てる欠陥を修正した。既存の`ConnectionPanel.failed`→`Notice`→`NotificationCenter`で失敗とURLの手動コピー手順を通知し、認証URLを通知履歴へ複製しない。認証待ち・接続状態・URLを保持し、自動再試行や原因の推測は加えていない。設定ダイアログの編集可／不可を通した新規2件が通知なしでred、実装後は既存ログイン経路と合わせ4件green。green後に早期returnで条件のネストを整理し、同じ4件でgreenを確認した（refactor-done）。`uv run pytest tests/integration/test_codex_gui.py tests/integration/test_conversation_notifications.py --basetemp=tmp/pytest-browser-diagnosis -q`は65 passed。対象ruff・format・全体ty・差分検査も成功した。

Product Designの画面検査では、実際の設定部品・通知部品に合成URLと起動失敗を与え、①設定を開いたまま主画面上部に失敗通知が見え、URLが残ること、②設定を閉じて通知の詳細を開くと回復手順を確認できることを確認した。詳細の閲覧には設定を閉じる既存の操作が必要で、通知時に別ダイアログを自動表示しない。実ブラウザの起動成否を再現した検証ではなく、ユーザー報告時の失敗原因もこの試験では確定していない。

再接続の案内は、映像やシリアル接続との混同を避けるため「Codex App Serverの再接続が必要」へ変更し、sandbox準備失敗時の通知と公開docsでも対象を明示した。合成状態を620×470の設定ダイアログへ表示し、全文が切れずに収まることを確認した。関連65件・対象ruff・format・差分検査は成功。文言だけの変更で構造整理は不要と判断し、refactor-skippedとした。固定文言だけを検査するテストは追加していない。

I6の検証用ApplicationShellには`tmp/u08-m1-acceptance-20261115/settings.toml`を渡し、同じ親のcodex_homeを使う既定サービスで起動した。開発用の認証・設定はコピーしていない。本人が認証URLを手動で開いてログインし、公開イベントの`AccountInfo.authenticated=true`と`SessionStatus.phase=ready`を確認した。アプリ終了・再起動後も、ログイン操作なしの接続で同じ状態になった。

sandbox準備は、低整合性の検証プロセスから起動した場合にsetup helper起動エラー1223で失敗した。同じhomeを使い通常権限で起動した場合は本人操作で成功し、Codex App Serverの切断・再接続後に`readiness=ready`となった。GIGAS側の権限設定は緩和していない。1223だけを本人のキャンセルと断定しない。

実GUIで「M1送信確認」の応答を受信し、続く1〜1000の列挙を生成中に停止した。製品の`events.jsonl`には1〜159の途中本文、対象turnの`interrupted`、停止の`accepted`と空のerrorが残った。公開状態も実行中→停止中→turn終了→停止成功となった。同じ会話へ次の指示を送り「M1停止後送信確認」の応答を受信し、待機へ戻った。アプリ終了コードは0。ゲーム機器は接続せず、実機I/OはM7の対象とした。

この確認では補助イベント記録のTextEvent処理に存在しない属性の参照があり、その補助ログだけ欠落した。補助処理は修正し、提出3件・受付成功3件・確定応答2件と中断結果は製品原記録、公開状態ログ、GUI表示で照合した。製品の記録欠落とは扱っていない。I6は既存経路の受入であり、新しい構造変更はなくrefactor-skipped。I1〜I5と独立レビューの結果を合わせ、M1の完了条件を満たした。

最終gateの`uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m1-root -q`は863 passed（66.59秒）。全体ruff・format・ty、`uv lock --check`、差分検査は成功した。buildはI5までの公開API変更で成功済みで、以後はUI通知・文言・検証記録だけを変更した。

## 9. 先送り事項

M2〜M7は親仕様の順序に従う。記録先結合・設定適用・再接続・UI・通し受入をM1の完了へ読み替えない。

M2では保存部品だけでなく、旧Projectメニューからのcreate/open・起動時last_project_pathの選択・固定memoryを使うthread設定も同時に置換する。既存入口を旧ProjectWriterへ残さず、Projectの位置は管理APIから解決し、前回選択を別の正本にしない。サイドバーの追加はM5で行う。

## 10. 完了チェックリスト

- [x] 合意・範囲・責務と既存コードの境界を確認した
- [x] I1〜I6を検証し、各cycleの構造確認を記録した
- [x] 型・docstring・公開docsと実APIの整合を確認した
- [x] 必要な品質ゲートと実接続・認証の確認を完了した
- [x] 親仕様・初期設計の進捗を更新した
