# アプリケーションサービスAPI

GUIとサービスを接続するPython APIです。全体の責務は[アーキテクチャ](../../architecture.md)、画面操作は[利用ガイド](../../guides/codex.md)を参照してください。

## 画面部品とサービスの接続

`gigas.ui.conversation_sidebar.ConversationSidebar`は取得済みの公開情報を表示する部品です。`set_projects(ProjectPage, append=False)`と`set_threads(project_id, ThreadPage, append=False)`でページを設定し、`append=True`で続きを追加します。Project一覧の再取得では一致する所属の開閉と選択表示を保持し、返却順へ並べ直します。`upsert_project(ProjectInfo)`は一覧の続きを示すcursorを変更せず一件だけ追加・更新します。未所属の`project_id`は`None`です。

`set_selection(project_id, thread_id)`で確定した対象の強調を更新します。会話行のクリックまたはキーボードでの確定では、対象を変更せず`thread_requested(thread_id)`を通知します。矢印キーでの候補移動だけでは通知しません。所属の開閉は`group_opened(project_id)`、右端の追加ボタンは`new_requested(project_id)`、追加ページは`projects_more_requested(cursor)`または`threads_more_requested(project_id, cursor)`で別々に通知します。部品自身はRPC・会話開始・停止を行いません。

`set_actions_enabled(bool)`は会話の選択・新規操作、`set_listing_enabled(bool)`は追加ページの取得操作を切り替えます。初期状態では両方とも無効です。所属の開閉は維持します。`set_projects_loading(bool)`と`set_threads_loading(project_id, bool)`は対応する一覧だけを読込中にします。`ConversationHeading.set_target(project, title, loading=False)`は所属・会話名と読込状態を表示し、選択の正本を持ちません。

`gigas.ui.connection_panel.ConnectionPanel.activate(generation)`は接続世代と実行状態を更新し、表示中の会話を交換しません。`replace_target(project, title, thread_id=None, entries=(), goal=None)`で確認済みの対象へ表示を交換すると、旧履歴・要求操作部・Planを破棄し、渡された履歴とGoalを表示します。呼出し側でサービスの対象参照を確認してください。入力中の原稿と現在の接続のモデル候補は維持します。`set_browse_goal(goal)`は閲覧対象の確定Goalだけを更新し、activeでも実行中とは扱いません。空の実行状態通知は閲覧Goalの削除を意味しません。

初回履歴の取得中は`begin_loading(project, title)`で本文・Goal・Planを隠し、入力・モデル選択・送信・Goal操作を無効にします。停止と切断の受付条件は維持します。成功時は`replace_target`、失敗・取消し時は`cancel_loading()`を呼びます。後者は同じ文書と閲覧位置を再表示します。`draft`はIME未確定文字を除いた現在の原稿を返し、確認後の`discard_draft()`は原稿とIME編集中状態を破棄します。会話ごとの下書き保存は行いません。

`update_target_heading(project, title)`は表示名だけを更新し、live本文や原稿を消しません。`set_input_busy(bool)`は入力所有者の受付・回収状態を反映し、会話切替を禁止して停止入口を維持します。入力busyだけでは同じ会話への追加指示やGoal削除を禁止しません。`can_list_conversations`と`can_switch_conversation`は接続・実行・表示から求めた受付可否で、値が変わると`state_changed`を通知します。

`gigas.app.conversations.ConversationController(window, service, parent)`は一覧・選択・履歴結果と表示部品を結びます。`activate(generation)`で接続世代を渡し、`set_available(available, project_pending=False)`で終了・picker待機を反映します。選択の正本はサービスの`selected_target`です。取得済みmetadataと要求中のUIだけを保持し、追加履歴とGoal通知は世代・対象参照を照合します。`confirm_switch(action)`は現在の原稿破棄を確認し、受付条件を再確認してから操作を呼びます。`adopt_project(result)`は現在世代の公開Projectだけを採用します。

一覧の更新と表示名の反映は`ConversationController`が担当します。画面側で会話の受付条件や履歴の取得を重ねて管理せず、サービス結果と現在の対象を照合します。画面の挙動は[デスクトップUI](../ui/desktop.md)を参照してください。

## 選択対象と履歴

`gigas.services.conversations`の`NewConversationTarget(project_id=None)`は、まだ開始していない会話の所属だけを表します。`StoredConversationTarget(thread_id, cwd, project_id=None)`は、公開応答で確認した既存ID・保存作業場所・所属を表します。どちらも選択対象の値であり、接続済み・実行準備済みを意味しません。`None`の所属は未所属です。

`BrowseResult(thread, goal, history)`は公開metadata・Goal・初回の`HistoryPage`、`HistoryResult(target, page)`は取得開始時の対象と追加の`HistoryPage`を保持します。`HistoryPage.content`は25件までのitem本文と、別に取得したturnの状態・エラーを結合した降順の`TurnPage`です。turn内の本文は時系列順で、全件収集や表示用の本文への変換はしません。`next_cursor`は本文用cursor・状態用cursor・境界turn以降の取得済み状態を持つ不変な`HistoryCursor`です。`load_history(cursor=...)`へそのまま渡し、失敗した場合も同じ値で再試行できます。本文が尽きた後も、状態だけの追加ページが返る場合があります。

サービスの読み取り専用`selected_target`は現在の不変な対象参照です。追加ページや閲覧Goal通知の受信時は、その`target is service.selected_target`を確認してください。同じ会話へ選び直した場合も以前の要求結果を採用しません。読み出した参照自体は変化しませんが、現在の選択は非同期に進み得ます。選択変更はサービスのキュー経由で行います。閲覧結果のactiveなGoalは、ローカルで開始した実行状態ではありません。

履歴の表示部品には`gigas.runtime.codex.history.history_entries(thread_id, page.content)`を使えます。降順のturnと時系列順の本文を`HistoryEntry(key, kind, text)`へ変換し、`Transcript.prepend_items(entries)`で既存本文の前へ追加します。turnの途中でもページを分割でき、重複キーは既存表示を優先し、選択・閲覧位置・ライブの質問操作部を保持します。中断・失敗はturn IDに対応する固定キーで重複を防ぎます。変換と描画は通信・ゲーム操作・本文保存・過去の要求への回答を行いません。

ライブ通知と保存履歴には同じ表示キーを使い、`Transcript`が重複をまとめます。変換対象や履歴と実行の違いは[会話と実行](../functions/conversations.md)、表示形式は[デスクトップUI](../ui/desktop.md)を参照してください。

## サービスの生成と寿命

`gigas.services.application.ApplicationServiceThread(data_root, reader, factory=..., input_factory=...)` は専用QThread内の一つのasyncioループでCodexServiceとInputServiceを動かします。`data_root`は専用Codex homeと未所属会話の作業場所を置くアプリデータフォルダー、`reader`は既存映像取得サービスの`CaptureReader`です。`input_factory`は解決済み入力設定から機器を作り、既定では`SerialInput`を使います。`start()`だけではCodexにも入力機器にも接続しません。`connect_runtime(ConnectionOptions)`が返す世代番号と、`received`の`ServiceEvent.generation`、`completed`の`ServiceResult.generation`を照合して通知を受け取ります。

Qtを使わない呼出し側は`gigas.services.codex.CodexService(data_root, reader, inputs, factory=..., received=..., completed=..., project_completed=..., management_completed=..., browse_goal_changed=..., recording_failed=...)`を使用できます。各コールバックはQt Signalと同じ内容を所有ループ上で同期的に受け取り、例外を送出しないものとします。`connect_runtime(options, generation)`には呼出し側の要求番号を渡します。コマンドは同じループ上で呼び、結果はコールバックで受け取ります。`await close()`はCodex・会話・記録を回収しますが、借用した`inputs`の接続は閉じません。入力側の終了は呼出し側で別途待ってください。

接続変更などのアプリ側連携には`execution_busy`と`await stop_execution()`を使えます。後者はCodex実行の停止を待ち、確認できない場合は例外を返します。コマンド結果は通知せず、会話がなければ入力へ作用しません。ApplicationServiceThreadの`interrupt()`は入力解除も含めた共通停止として、既存の`completed`へ結果を通知します。入力設定・機器生成・適用結果は[InputService](input-api.md)が所有します。

`update_manual(InputState)`・`release_manual()`は同じサービスループへ配送します。手動受付は`CodexService.manual_blocked`と入力側の機器・操作所有、停止・終了要求から計算し、`manual_changed(bool, protocol)`でGUIへ通知します。配送時にも確認するため、GUIの通知遅延を操作権として扱いません。`manual_completed(OperationResult)`は失敗の診断に使います。`set_exit_requested(bool)`で終了確認中の受付を閉じ、取消しでは新しい操作から再開します。手動入力専用のスレッドや会話・記録先は作りません。

Codex切断では、切断開始時に捕捉した有限操作の終了を待ちます。その解除失敗は切断結果にも通知し、後から受け付けた手動操作は停止・待機しません。入力機器の接続終了は入力側が所有します。

注入する`factory`は`(options, emit)`を受け取り、未開始の`CodexConnection`を返します。接続・認証は接続部、thread・Goal・送信・停止は内部の会話実行、MCP・観測・記録先は会話資源が所有します。初回送信で記録先とMCPを準備し、同じ会話では入口を維持します。ランタイム自発終了時もサービスの共通終了処理で捕捉した有限操作列を止め、MCP受付と保存を回収します。詳しくは[接続API](codex-api.md)を参照してください。

## 会話の一覧・選択・再開

会話の一覧・選択は次のサービスAPIを使います。`management_completed`は`ManagementResult(generation, operation, value, error, project_id)`を返し、空の`error`が成功です。結果の世代を現在の接続と照合してください。会話の選択と初回履歴取得は一件ずつ受け付けます。

| メソッド | operationと成功時のvalue |
| --- | --- |
| `list_projects(*, cursor=None)` | `projects` / 公開`ProjectPage`の1ページ |
| `list_threads(project_id, *, cursor=None)` | `threads` / 指定所属の`ThreadPage`。`None`は未所属 |
| `new_conversation(project_id=None)` | `new` / `NewConversationTarget`。フォルダーとthreadは明示実行まで作らない |
| `select_conversation(thread_id)` | `select` / `BrowseResult`。保存cwdが欠けていても閲覧できる |
| `load_history(*, cursor=None)` | `history` / `HistoryResult`。追加取得は送信・切替を妨げない |

## プロジェクトの編集・削除API

`ApplicationServiceThread`と`CodexService`は次の操作を提供します。結果は同じ`management_completed`へ要求時の世代・project_id付きで返します。

| メソッド | operationと成功時のvalue |
|---|---|
| `probe_project_folder(project)` | `project_folder` / `ProjectFolderStatus`。一覧取得済みの保存値に対する所在確認だけを行う |
| `inspect_project(project_id)` | `project_inspect` / `ProjectDetails`。登録、通常・アーカイブ済みの全所属、所在確認を返す |
| `update_project(expected, *, name, root)` | `project_update` / `ProjectDetails`。編集開始時の`ProjectInfo`と照合し、登録と既存チャットを更新する |
| `delete_project(expected)` | `project_delete` / `ProjectDeleted`。全ページの一覧で登録がないことを確認する |

`ProjectDetails.pending`は登録rootと異なるcwdの通常チャットです。永続的な進捗ではありません。更新はサービス内で直列化し、保存中の送信・新規開始・切替を拒否します。停止は管理更新を中断して接続を回収し、部分失敗・結果不明は再接続・再取得後の明示的な再試行で扱います。通常のフォルダー追従は購読解除→読取り専用resume→購読解除を使い、次の明示実行で書込み範囲と実行資源を準備し直します。詳細は[プロジェクト管理仕様](../functions/projects.md)を参照してください。

サイドバーの`edit_requested(project_id)`・`delete_requested(project_id)`は管理操作の意図だけを通知します。`set_management_enabled(bool)`は管理メニューの受付、`set_directory_status(project_id, status)`は`available`・`missing`・`unavailable`の所在アイコン、`remove_project(project_id)`は登録行の削除です。`ConnectionPanel.set_project_busy(bool)`は管理保存中の送信・切替を閉じ、原稿編集・停止・切断を残します。

## 選択・再開の受付条件

切替はturn・active Goal・送信・停止・入力回収が終わってから行います。実行中の切替は拒否し、自動停止や予約はしません。候補取得後に旧購読を解除し、要求を失効させ、MCP受付・回答配送・保存を回収してから採用します。同じcwdの別会話でも旧MCP入口を閉じます。取得・解放に失敗すると選択と表示済み履歴を保持しますが、解放済みの実行をロード済みとは扱いません。

閲覧中の`select_model`はモデル候補だけを変更し、`clear_goal`は選択IDへ直接削除を要求します。削除には接続・認証が必要ですが、モデル・sandbox・cwd・MCPの準備は不要です。削除が発生した応答は対応する公開通知まで待ち、削除なしの応答は通知を待ちません。`browse_goal_changed(generation, BrowseGoalEvent(target, goal))`は閲覧対象のGoal通知で、実行状態の`ConversationStatus`へ合成しません。削除待機中も停止・切断・終了を受け付けます。

新規startで確認した実IDは記録先を開く前に保持します。以後の記録先結合失敗や切断でもIDを捨てず、既存IDへの送信を新規開始へ置き換えません。新しい会話を開始するときは`new_conversation`を明示します。閲覧中のactive Goalはローカル実行ではなく、停止・終了で勝手にpauseしません。

保存済み対象の`send`・`set_goal`・`resume_goal`は、保存cwd・同じrecords・現在のMCPを準備して`thread/resume`を送り、その確認後に指定操作を続けます。要求前からIDに対する通知・質問・保存を受け付け、active Goalの先行実行を許容します。準備中と応答未確認の状態ではモデル変更を受け付けません。resume送信後の失敗では実行を再試行せず、明示切断・再接続が必要です。Goal削除はこの検証成功に依存せず、接続・認証と保持Goalがあれば使えます。

接続済みで実行と入力回収が停止していれば、`create_project(path)`と`open_project(path)`で公開Projectを選び、その所属の新規会話へ切り替えられます。前者は新規フォルダーを作って登録し、後者は既存フォルダーに対応する公開Projectを選択または登録します。取得待ちの間に接続・実行所有者・選択対象が変わると結果を採用せず、現在の選択を保持します。選択だけでは会話・MCP・writerを開始しません。

`project_completed`の`ProjectResult(generation, path, project, error)`は、成功・失敗とも要求時の接続世代を保持します。空の`error`が選択成功で、`project`に公開`ProjectInfo`、`path`にそのrootが入ります。失敗時は`project=None`、`path`は要求したフォルダーです。受信側は待機解除と世代の照合を分け、古い世代の結果で現在の表示を更新しないでください。

## 設定と入力状態の通知

`input_busy_changed(bool)`は入力操作の受付と回収完了を通知します。停止要求やturn終了だけでは`False`にせず、neutralへの復帰完了まで入力所有者の状態に従います。Codex接続の世代と独立したサービス全体の通知で、調整役からPanelへそのまま反映します。

`set_observation_resolution(value)`は以後の観測へ設定を適用します。`recording_failed(identifier, path, reason)`は保存と記録先準備の失敗を通知します。実行前に記録先を開けなければ送信せず、結合後の個別保存失敗は入力・観測・停止を止めません。

`set_execution_settings(ExecutionSettings)`は保存に成功したWeb検索・自動記憶・承認方法を、サービスのループへ渡します。次の新規開始・resumeの準備開始時に値を固定し、同じ値で要求と応答確認を行います。ロード済み会話への設定RPCや自動再接続は送りません。接続前に渡した値は、後続の`connect_runtime(options)`で指定する設定に従います。アプリは最後に保存に成功した同じ値を両方へ渡します。

## 実行と結果の受け取り

GUIスレッドから`login()`、`setup_windows_sandbox()`、`send(prompt)`、`answer(token, decision=..., answers=...)`、`set_goal(objective)`、`resume_goal()`、`clear_goal()`、`select_model(model, effort)`、`interrupt()`、`disconnect_runtime()`を呼べます。各呼出しは処理を予約して戻り、`completed`が実際の操作終了を通知します。`interrupt()`の成功結果はサーバー側停止と入力の解除・回収の完了です。`clear_goal()`の成功は目的の削除で、turn中断ではありません。送信処理中でも停止を要求でき、停止中の再操作は同じtaskを待ちます。

`SessionStatus`は接続・認証、`ConversationStatus`はthread・turn・Goalと送信・停止・回答待ちの表示に使います。Goalの変更は対応する通知の反映まで同じ送信taskで扱い、無変化応答ならそのまま終了します。遅い応答で後続のGoal通知を巻き戻しません。実行環境の準備結果も開始受付ではなく、完了通知と準備状態の確認後に返します。

通常指示・追加指示・`goal_set`の`ServiceResult.message`は`MessageResult(outcome, turn_id)`です。`accepted`は公開応答での受付、`not_sent`は未配送または明確な拒否、`unknown`は受付不明を表します。通常送信・追加指示の`accepted`には確認したturn IDが入り、Goal送信は`accepted`でも`turn_id=None`です。どちらもturnの完了・指示の遵守を意味しません。

resumeの結果と指示本文の受付は別です。本文が`not_sent`でもresumeによるGoal・turnが開始済みの場合があります。再開要求待機中の共通停止はその要求と早期turnを回収し、明示切断は接続・MCP・所有taskを終了します。

`answer_requested`は`AnswerSubmission`を通知し、元のRPC IDではなく接続固有tokenを接続へ返します。質問IDだけで要求を識別せず、異なる要求の同名質問を混同しません。

回答の`ServiceResult.answer`は`AnswerResult(token, outcome)`です。`outcome`は`sent`（通信への書込完了）、`not_sent`（送信前の拒否）、`unknown`（送信結果不明）のいずれかです。世代とtokenの両方を照合してください。`RequestResolved`は回答受付の終了を表し、送信成功の根拠にはなりません。終了中の未処理回答にも結果を通知します。

`ServiceResult.failure_id`は同じ失敗を知らせた`SessionStatus.failure_id`と共通です。接続世代と番号の組で重複通知を判定します。`None`の失敗結果は独立した通知として扱い、理由の文字列だけで別の試行を消さないでください。

## 終了

`shutdown()`は新規操作を閉じ、接続と所有taskを回収します。GUIを動かしたまま`finished`と`wait(0)`を確認し、`shutdown_error`が空である場合に終了成功とします。`QThread.quit()`ではこのサービスのasyncioループは終了しません。

製品サービスは攻略資料の編集に作業フォルダーへの書込を要求するため、Windows実行環境が準備済みである必要があります。未準備または要求した書込範囲を確認できない場合、通常指示・Goal作成・再開を拒否して接続を維持します。閲覧とGoal削除にはこの準備を要求しません。権限を広げた自動再試行はしません。
