# U08 M5 会話管理UI

## 1. 目的・状態・起点

状態: **wip**。[U08親仕様](../unit_013/RESUME.md)のM5を担当する。[M4](../../complete/unit_072/CONVERSATION_SELECTION_AND_RESUME.md)で完成した選択・履歴取得・切替・再接続のサービスを、合意済みの開閉式サイドバーと既存の対話画面へ結合する。

Intent Delta: none。配置と操作は[10 UI](../../initial/10-ui-interaction.md)、[M0](../../complete/unit_068/MANAGEMENT_STORAGE_DESIGN.md)、[採用モック](../../complete/unit_068/assets/conversation-sidebar-proposal.png)を正とする。画像は配置資料であり、例文・ゲーム名・モデル値・色・寸法を製品の固定値にしない。管理・実行の所有は[02](../../initial/02-runtime.md)、手順は[09](../../initial/09-control-flows.md)に従う。

利用者はProjectまたは未所属の会話を閲覧し、必要なときだけ送信・Goal操作で再開する。同じProjectで新規会話を始める場合も、資料の保存先と現在対象を識別できるようにする。

## 2. 対象範囲・対象外

サイドバーと所属別の新規会話、公開履歴の描画・過去ページ取得、Web activity、現在対象の見出し、閲覧Goal、読込・失敗・下書き、モデル・送信・Goal・停止の活性を対象とする。Project作成/既存フォルダー選択、設定、映像・入力接続、終了経路を維持する。

画面の再設計、WebEngine・新しい描画基盤、会話台帳、recordsや内部rolloutからの履歴補完、本文内の画像一覧、過去の質問/承認操作の再有効化、会話別の下書き保存、独自Goal制御、空threadの先行作成は対象外とする。実機の通し受入はM7。M6は状態を跨ぐ障害・設定の検証を担い、基本画面の実装を持ち越さない。

## 3. 着手条件と冒頭確認

M4の全体968件・静的検査・配布build、実app-serverの閲覧・再開・記録追記・Goal停止は完了済み。UIはまだ会話一覧を持たず、ConnectionPanel.activateが接続ごとに履歴・Goalを消す。閲覧Goalを実行状態として扱わず、接続世代の更新と表示対象の変更を分ける必要がある。

Product Designのaudit手順とComputer Useを使い、現在画面を`tmp/u08-m5-ui/01-before.jpeg`へ保存した。未接続の実画面は左に対話・入力、右に映像・Goal/今回の作業があり、設定アイコン・既存メニュー・左右別の状態表示を持つ。合意モックと照合し、この骨格へサイドバーと対象見出しを加える。保存コンテキストは未登録で、既存仕様・モックを使用する。実装後に同じ骨格と操作状態を再撮影する。

## 4. 振る舞い仕様

| 対象 | 観測する振る舞い |
|---|---|
| 配置 | 上部左端にサイドバー開閉アイコンと「設定」。既存Project/接続/表示メニューとCtrl+,を維持する。右側の対話・映像/Goalを保ち、閉じた幅を戻す |
| 所属と一覧 | Project/未所属をアコーディオンで並べ、各見出し右端にその所属の新規会話アイコンを置く。開閉と新規は別の操作。公開一覧の続きはcursorで取得し、全件の先行取得を要求しない |
| 現在対象 | サイドバーを閉じても所属名と会話名を対話欄上部に表示する。未選択は「未所属 / 新しい会話」。所属別の新規では入力欄へフォーカスし、空threadや作業フォルダーを作らない |
| 保存履歴 | 公開の本文・進捗本文・reasoning要約・command/file/MCP/Web activity、Plan本文、公開された質問/回答/承認の文面、turnの中断/失敗を表示する。保存されていない途中状態を補わない |
| 履歴と新着 | 直近ページを時系列に表示し、「以前の履歴」で過去へ遡る。同じ公開turn/itemは二重表示せず、新着を同じ対話欄へ接続する。履歴の描画でゲーム操作・本文再保存・回答配送は起こらない |
| 初回読込 | 選択候補の名前と「読込中」を表示し、以前の本文・Goal・Planは見せない。入力・送信・Goal・会話切替・新規会話を無効にし、映像とサイドバー/所属の開閉は維持する。失敗時は以前の選択・表示を保持する |
| 局所読込 | 一覧は該当所属だけを読込中とし、取得済み所属の新規や現在対話まで無効にしない。追加履歴は現在表示を残し、そのボタンだけを無効にする。空の一覧でも見出しと新規を残す |
| 切替と下書き | 空なら即時切替。本文があれば10の破棄確認を先に行う。取消しでは何も変えず、破棄後に読込を始める。同一会話の再選択とサイドバー開閉は下書きを維持する |
| 実行中の管理 | turn・activeな実行Goal・送信・停止・入力回収中は切替/新規を無効にする。利用者の停止後に操作でき、予約や自動停止をしない。閲覧Goalがactiveというだけでは切替を禁止しない |
| 閲覧中の実行操作 | モデル選択は候補保持だけ。Goal削除は接続・認証と保存Goalを使い、送信・停止・切断中を除く共通の受付条件に従う。モデル/sandbox/cwdの追加準備を要求しない。送信・Goal作成/目的編集/再開はM4の明示実行へ渡す |
| 失敗・切断 | 既存通知と詳細表示へ理由を残し、入力と履歴を勝手に消さない。再開失敗を停止済みと表示せず、停止・切断を妨げない。再接続自体は会話を実行しない |

Transcriptの文字選択・コピー・閲覧位置・末尾追従、IME、進捗/Planの更新、live質問/承認カードを維持する。過去ページ追加のためにclearして全文を作り直さない。公開ページを表示した範囲だけ保持し、別の全文キャッシュを作らない。

## 5. 責務と実施順

MainWindowは配置、Sidebarは所属見出し・会話行と局所状態の表示を担当する。既存の対話/映像用QSplitterは内側に維持し、2領域前提の状態表示・サイズpresetを壊さない。

一覧・選択・履歴結果を結ぶUIの調整役をApplicationShellから分ける。調整役は接続世代・選択参照を照合し、下書き確認と現在表示への反映を行う。threadの実行・Goal・再開を独自管理せず、サービスの事実を使う。Shellは組立と設定・機器・終了の既存所有を保つ。

履歴の公開値から表示項目への変換は副作用を持たせない。過去データをruntime通知として再送せず、保存・要求管理へ入れない。Transcriptは項目と過去ページの挿入、ConnectionPanelは入力・liveカード・実行操作の表示を担当する。初回読込中は以前の表示を隠し、成功時に置換・失敗時に再表示する等、別の全文復元キャッシュを増やさず成立させる。

L1→L2→L3a→L3b→L3c→L4の順で進める。L2の表示部品だけでは製品の会話管理完了とせず、未結合の操作や所属見出しを有効な入口・実対象として公開しない。L3は表示対象と接続の分離、サービスからの事実の受渡し、UI調整役の結合に分ける。いずれも本unit内の作業であり、到達点やマイルストーンは追加しない。各itemはred→green→refactor-after-green→同じ試験→独立レビュー→commitを行う。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| done | L1: 保存履歴と新着Web activityを既存表現へ描画し、過去ページ追加でも選択・閲覧位置を保つ | new/regression/edge | unit/integration | 公開項目、turn/item/種別の対応、Plan/過去要求の非操作性、順序・重複、reasoning表示 |
| done | L2: 合意配置のサイドバー・所属別新規・対象見出しが開閉と操作可否を分けて表示する | new/regression | integration/GUI | 部品と配置、空/局所読込/ページ、幅復帰、既存メニューとサイズ。サービス結合はL3c |
| done | L3a: 再接続と表示対象の交換を分け、閲覧Goal・初回読込・下書きの表示操作を成立させる | new/regression/edge | integration/GUI | 再接続で履歴・下書き・表示Goalを保持。実行のactive Goalと閲覧Goalを混同せず、Goal削除は共通の受付条件だけで活性化 |
| done | L3b: Project選択結果と入力回収状態をUIへ渡し、停止済み会話からのProject選択を成立させる | new/regression/edge | unit/integration | 公開Project metadata・接続世代を結果へ添え、遅い結果の選択上書きを防ぐ。入力所有者のbusyを通知し、独自の実行状態台帳を増やさない |
| done | L3c: 会話UI調整役で一覧・新規・選択・履歴・picker・下書き確認を結合する | new/regression/edge | integration | Project/未所属、世代/選択参照の照合、局所読込、初回読込失敗、閲覧Goal更新、停止・入力回収前の切替禁止、開始時の実IDへの表示同期 |
| todo | L4: 実画面と実app-serverで履歴閲覧・停止後切替・新規/再開のUIが成立する | acceptance | GUI/integration | native撮影、合意モックとの照合、操作・活性・回収。実機はM7 |

## 7. 対象ファイルと検証

対象はuiのMainWindow・ConnectionPanel・Transcript・新規Sidebarと必要な表示部品、appの組立と会話UI調整、runtime/codexの履歴表示変換・Web activity、対応する試験と公開docs。サービスの基本経路を作り直さない。

| gate | 状態 | 検査する内容 |
|---|---|---|
| 各itemのpytestと独立レビュー | L1・L2・L3a/b/c完了、L4 not run | 表示と操作の振る舞い、既存選択/IME/カード/サイズの回帰 |
| uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m5-root -q | not run | M5結合後の全体回帰 |
| uv run ruff format --check . / uv run ruff check . / uv run ty check --no-progress | not run | 形式・lint・型/所有境界 |
| uv lock --check / uv build | not run | 配布資材と公開APIの整合 |
| 実画面・実app-server | 着手画面とL2の配置・開閉を確認 | 会話管理操作のnative受入はL4。実モデル提出数・未観測項目を記録する |
| git diff --check・文書/リンク検査 | 着手時に実施 | 合意・実装範囲・根拠・unitの一意性 |

公開型・docstring変更は対応skillを使う。Product Designでは現画面・合意モック・実装後の画面を照合し、説明文を追加せずに対象と操作が識別できることを確認する。スクリーンショットだけでキーボードや支援技術の適合を断定しない。

### L1の実装・検証

`history_entries`は公開TurnPageを時系列の表示値へ変換し、Transcriptは既存項目を残して先頭へ追加する。通知・要求管理・本文保存へ履歴を再投入しない。保存Planは本文として表示し、過去の質問と出力は公開された文面だけを扱う。専用の承認履歴型やすべての回答の保存は仮定しない。保存Webには状態を補わず、ライブWebの開始・完了は通知から表示する。

表示キーはthread・turn・item・表示種別を共有する。提出本文のローカルIDと公開履歴IDは推測して照合せず、表示用IDをrecordsへ加えない。初回の関連回帰では同IDのreasoningとactivityの上書きを1件検出し、種別をキーに含めて既存試験を保ったまま修正した。

| command | 結果・範囲 |
|---|---|
| `uv run pytest tests/unit/test_history_presentation.py tests/integration/test_saved_history_gui.py --basetemp=tmp/pytest-m5-history -q` | red: 9 failed。表示変換・先頭追加API未実装とWeb未表示。green: 9 passed。構造整理後と最終修正後も同じ9件成功 |
| `uv run pytest tests/unit/test_history_presentation.py tests/unit/test_codex_history.py tests/integration/test_saved_history_gui.py tests/integration/test_codex_gui.py tests/integration/test_inline_transcript.py tests/integration/test_conversation_notifications.py --basetemp=tmp/pytest-m5-history -q` | 最終98 passed。既存の本文・Plan・カード・選択・閲覧位置・通知との回帰 |
| `uv run ruff check .` / 変更Python 8ファイルへの`ruff format --check` / `uv run ty check --no-progress` | 成功。公開型・docstring・呼出し元を照合。ignore/cast追加なし |
| `uv lock --check` / `uv build` | 成功。83依存、sdist・wheelを生成 |

Refactor status: **refactor-done**。先頭追加の辞書作成を項目ごとの再構築からページ単位へ整理し、同じ9件が成功した。活動の変換・表示キーを履歴とライブで共有する。独立レビューは公開schemaとの対応、既存項目を保つ挿入、試験境界を確認し、最終差分に必須修正なし。会話一覧からの履歴表示はL3、native画面と実接続はL4で結合・検証する。L1では実app-server・モデル・実機を使っていない。

### L2の実装・検証

Sidebarは公開ページから所属・会話名・cursorを表示し、展開、新規、会話選択、追加ページの意図だけを通知する。選択の正本や通信を所有しない。MainWindowは既存2領域splitterの外へSidebarを置き、設定をラベル式にした。現在対象見出しはL3cまで非表示とし、未結合の会話操作は無効に保つ。一覧の取得可否と会話の切替可否を分け、実行中の折り畳みを禁止しない。

| command | 結果・範囲 |
|---|---|
| `uv run pytest tests/integration/test_conversation_sidebar.py --basetemp=tmp/pytest-m5-sidebar -q --tb=short` | module未実装でred→5件green。一覧取得の独立活性、keyboard選択、Project返却順の問題も失敗を確認して修正し、最終7 passed。描画補正後も同じ7件成功 |
| `uv run pytest tests/integration/test_window_presets.py tests/integration/test_app_settings.py tests/integration/test_settings_source_menu.py tests/integration/test_project_gui.py tests/integration/test_codex_gui.py tests/integration/test_inline_transcript.py tests/integration/test_saved_history_gui.py --basetemp=tmp/pytest-m5-sidebar-regression -q --tb=short` | 109 passed。最後の見出し描画補正前に、既存サイズ・設定・Project・対話・カード・履歴の回帰を確認 |
| 変更Python 4ファイルへの`ruff format --check` / `ruff check`、`uv run ty check --no-progress` | 最終差分で成功。公開API・型・docstringと利用文書を照合 |
| `uv lock --check` / `uv build` | 成功。追加したアイコンを含む配布資材を確認 |

Refactor status: **refactor-done**。確定選択の色を戻す処理をselection変更の一経路へ集約し、クリック側の重複を削除した。独立レビューで、矢印/Spaceで候補だけを移動したときの選択色の不一致を検出し、Enter等で確定するまでは表示中の会話の色を保持する修正を確認した。最終差分に必須修正なし。L3のa→b→c分割も初期設計と親の受入条件へ照合し、担当漏れ・依存の矛盾なし。

nativeでは2回起動し、Sidebarを閉じると幅と下部状態表示が既存2領域へ戻ること、設定ラベルから既存ダイアログを開けることを確認した。展開済み見出しが選択色で塗られる問題は、開閉状態を保ちながら描画だけ塗り無し・左寄せへ修正し、再起動後の`tmp/u08-m5-ui/04-l2-open.jpeg`と`05-l2-folded.jpeg`で確認した。`03-l2-closed.jpeg`は閉状態、`audit.md`に手順と限界を記録した。両processは通常終了。Codex・映像・入力への接続とモデル提出は0回。会話データの一覧・対象見出し・実行中の活性はL3結合後に確認し、空一覧の画面確認を支援技術全体の検証とは扱わない。

### L3aの実装・検証

接続世代の更新と表示対象の交換を分けた。再接続では同じ履歴文書・選択・下書き・表示Goal/Planを保ち、古い要求カードは操作を失効させて残す。明示的な対象交換でだけ以前の内容を破棄する。初回読込は旧内容を隠し、成功時に交換、失敗時に同じ文書と閲覧位置を再表示する。閲覧Goalは実行状態にせず、Goal削除は認証等の共通受付条件だけで活性化する。

| command | 結果・範囲 |
|---|---|
| `uv run pytest tests/integration/test_conversation_view.py --basetemp=tmp/pytest-m5-view -q` | red: 5 failed→green: 5 passed。構造整理後・最終差分も同じ5件成功。読込成功/失敗、再接続、Goal、下書き、停止を確認 |
| `uv run pytest tests/integration/test_conversation_view.py tests/integration/test_codex_gui.py tests/integration/test_saved_history_gui.py tests/integration/test_inline_transcript.py tests/integration/test_conversation_sidebar.py tests/integration/test_project_gui.py --basetemp=tmp/pytest-m5-view -q` | 92 passed。既存カード・モデル・本文・Project経路の回帰 |
| `uv run pytest tests/integration/test_window_presets.py --basetemp=tmp/pytest-m5-view -q` | 6 passed。表示部品の親変更後のウィンドウ配置 |
| 変更Python 4ファイルへの`ruff format --check` / `ruff check`、`uv run ty check --no-progress` | 成功。公開API・型・docstringと利用文書を照合 |
| `uv lock --check` / `uv build` | 成功。83依存、sdist・wheelを生成 |

Refactor status: **refactor-done**。文書・見出し・Goal/Plan・読込時の可視性をConversationDisplayへ分け、ConnectionPanelは接続・受付・カード・原稿を担当する。独立レビューで表示寿命・通知の境界・試験と文書を照合し、必須修正なし。サービス・picker・サイドバーの結合は変更せず、L3b/cへ引き継ぐ。native画面・実app-server・実機はこのitemでは使用していない。

### L3bの実装・検証

停止済みのロード済み会話からも、公開Projectの作成・既存フォルダー選択を受け付ける。取得の前後で接続世代・実行所有者・選択参照と共通の停止条件を確認し、遅い結果がその間の選択を上書きしない。結果は成功・失敗とも要求世代を持ち、成功時には公開Project情報を返す。InputServiceは操作の受付と回収完了を通知し、停止要求だけではbusyを解除しない。

| command | 結果・範囲 |
|---|---|
| `uv run pytest tests/integration/test_input_service.py tests/integration/test_codex_service.py tests/integration/test_conversation_switching.py -k "input_busy_notifications or repeated_stop_during_release or late_project_selection or project_picker or common_stop_reuses_endpoint" --basetemp=tmp/pytest-m5-service -q --tb=short` | red: 11 failed→green: 11 passed。構造確認後の最終も11 passed。遅延Project、停止済み会話からの選択、入力の成功/失敗/開始前取消し/解除待ち、MCPからQtへの通知 |
| `uv run pytest tests/integration/test_input_service.py tests/integration/test_codex_service.py tests/integration/test_conversation_switching.py tests/integration/test_project_gui.py --basetemp=tmp/pytest-m5-service -q --tb=short` | 90 passed。既存入力・停止・会話切替・Projectメニューの回帰 |
| 変更Python 6ファイルへの`ruff format --check` / `ruff check`、`uv run ty check --no-progress` | 成功。公開型・docstring・全呼出し元と文書を確認 |
| `uv lock --check` / `uv build` | 成功。83依存、sdist・wheelを生成 |

Refactor status: **refactor-done**。旧Project専用の開始前制限を共通の切替条件と解放経路へ統合し、green後は遅延試験名と結果型を契約へ整理した。独立レビューで所有者・世代・回収完了の境界を確認し、必須修正なし。GUIでの世代照合、Project待機の解放、入力回収中の停止表示・切替禁止はL3cで結合する。native画面・実app-server・実機はこのitemでは使用していない。

### L3cの実装・検証

ConversationControllerが公開一覧・履歴・選択結果をUIへ結合する。取得済みmetadata、要求中の表示、破棄確認だけを保持し、選択の正本と実行はサービスへ残した。初回送信の実IDは表示だけへ同期し、受付後の一覧更新でもlive本文と次の原稿を消さない。先行する一覧があれば完了後に一度更新する。Shellは設定・機器・終了とProject pickerの所有を保ち、古い世代のpicker結果でも待機解除を行う。

空の確定原稿にIME未確定文字だけがある場合も、確認なしの切替で編集中状態を持ち越さない。対象変更後の見出しとタイトルバーは確定した所属・作業場所へ揃え、読込候補で先行変更しない。入力回収中は切替・新規・pickerを禁止して停止入口を保ち、同じ会話への追加指示とGoal削除は既存の受付条件に従う。

| command | 結果・範囲 |
|---|---|
| `uv run pytest tests/integration/test_conversation_controller.py --basetemp=tmp/pytest-m5-conversations -q --tb=short` | 初回1 failed→1 passed。境界追加時に古い履歴結果による待機解除とIME持越しを検出して修正。整理前・整理後・最終すべて6 passed |
| `uv run pytest tests/integration/test_conversation_controller.py tests/integration/test_project_gui.py tests/integration/test_conversation_view.py tests/integration/test_conversation_sidebar.py tests/integration/test_codex_gui.py tests/integration/test_inline_transcript.py tests/integration/test_saved_history_gui.py tests/integration/test_app_settings.py tests/integration/test_settings_apply_contract.py tests/integration/test_settings_recovery.py tests/integration/test_settings_accept.py tests/integration/test_settings_source_menu.py tests/integration/test_window_presets.py --basetemp=tmp/pytest-m5-conversations-regression -q --tb=short` | 最終180 passed。途中の1件はstdio模擬peerが追加の一覧取得を次turnと誤認したため、対話中も読み取り専用一覧へ応答するよう修正。既存の追加指示・要求配送を維持 |
| 変更Python 10ファイルへの`ruff format --check` / `ruff check`、`uv run ty check --no-progress` | 成功。公開型・docstringと利用文書を確認 |
| `uv lock --check` / `uv build` | 成功。83依存、sdist・wheelを生成 |

Refactor status: **refactor-done**。green後に旧can_select_projectの開始前制限を撤去し、UIの切替判定をcan_switchへ統一した。確定した閲覧結果の表示交換を一つの処理へまとめ、独自の会話状態機械や全文キャッシュは追加していない。独立レビューで初回/局所読込・原稿・通知参照・停止・終了・責務を確認し、必須修正なし。native画面と実app-serverの確認はL4で行う。

## 8. 先送り事項

M6は新規/閲覧/実行を跨ぐ設定・権限・承認・障害の結合と記憶生成対象を確認し、M7はUSB Video DeviceとCOM8で通し受入を行う。M3で未観測の記憶抽出は標準条件の範囲で確認し、強制抽出を製品へ加えない。長時間履歴の性能と攻略の有用性はU10。

## 9. 完了チェックリスト

- [x] M4完了と合意モック・現画面・UI責務を確認した
- [ ] L1〜L4と構造確認・独立レビューを記録した
- [ ] 公開文書・画面・配布資材を整合させた
- [ ] 品質ゲート・native UI・実接続の範囲を分けて記録した
- [ ] 親・初期設計へ完了範囲とM6以降の引継ぎを反映した
