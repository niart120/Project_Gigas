# Codex接続・管理のPython API

GIGASが使用する通信・実行設定は[Codex接続仕様](codex.md)、通常依頼・Goal・停止の振る舞いは[会話機能](../functions/conversations.md)を参照。アプリとして実行する場合は[アプリケーションAPI](application-api.md)を使う。

## 接続API

`gigas.runtime.codex.launch.create_connection(options, emit, *, codex_home=None)`は未開始の`CodexConnection`を作る。`options`は`ConnectionOptions`、`emit`は`RuntimeEvent`を受け取る短い同期コールバック。同じasyncioループ上で`open()`、必要なら`login()`を呼び、最後に`close()`を待つ。接続は通信・認証・モデル候補・Windows実行環境を所有し、会話や機器の実行は所有しない。

`codex_home`を指定するとそのディレクトリを作成して専用homeとして使い、省略時はランタイムの既定homeを使う。既存の資格情報はコピーしない。製品アプリは設定ファイルと同じディレクトリの`codex_home`を指定する。

| API | 利用契約 |
|---|---|
| `request(method, params)` | RPC応答を待ち、サーバー拒否は`RpcError`、通信失敗は`TransportError`として返す |
| `respond(request_id, result)` / `reject(request_id, code, message)` | 元のRPC要求へ回答・エラーを配送する |
| `propose_model(model, effort)` | 公開候補に対する妥当性を確認し、選択案を返す |
| `commit_model(selection)` | 確定した選択を保持・通知する。ロード済みthreadの更新は呼出し側が先に行う |
| `setup_windows_sandbox()` | 明示的な準備を依頼し、完了通知と状態の再取得まで待つ |
| `close()` | 認証処理・受信処理・stdio接続を回収する。会話資源や機器の終了は所有者が別途扱う |

`ConnectionOptions.execution_settings`には`ExecutionSettings(web_search_enabled=True, memory_enabled=True, approvals_reviewer="auto_review")`を渡す。審査者は`"auto_review"`または`"user"`。接続起動時とthreadの新規開始・再開で同じ設定変換を使う。

ログインURLは明示的な`login()`の後に`LoginEvent`で受け取り、呼出し側がブラウザを開く。準備の完了や再接続の必要性は`SandboxEvent`で取得する。`ready`は接続・認証・モデルの準備を表し、sandboxの準備確認は`require_ready()`が別途行う。

## Project管理API

`gigas.runtime.codex.management.CodexManagement(connection)`は、開いている`CodexConnection`を通してProjectを作成・一覧・取得します。接続の終了は呼出し側が担当します。管理操作はモデルの選択やWindows実行環境の準備を要求せず、threadの開始・resume、MCPや記録部品の準備を行いません。

| メソッド | 入力と結果 |
| --- | --- |
| `create_project(name, root, *, idempotency_key)` | 呼出し側が決めた作成操作のキーと絶対パスの`Path`を渡し、`ProjectInfo`を取得する |
| `list_projects(*, cursor=None, limit=None)` | サーバー順の1ページを`ProjectPage`として取得する。省略した値はサーバーの既定値を使う |
| `read_project(project_id)` | 指定IDの最新の`ProjectInfo`を取得する |

`ProjectInfo`は`id`、`name`、`root: Path`を持ちます。作業フォルダーは1件に限り、複数rootsのProjectは対応対象外です。フォルダーの作成・選択・存在確認は呼出し側が行います。管理APIはディレクトリを作成せず、Projectの位置や一覧を別途保存しません。

`ProjectPage.projects`は`ProjectInfo`のtuple、`next_cursor`は次ページ用の文字列または`None`です。cursorの内容は解析せず、次の`list_projects(cursor=page.next_cursor)`へそのまま渡します。ページの自動取得・キャッシュ・失敗時の自動再送は行いません。

相対rootの入力は`ValueError`、不正な応答や非対応のrootsは`CodexConnectionError`です。接続が閉じている場合も`CodexConnectionError`になります。通信失敗・サーバー拒否は既存の`TransportError`・`RpcError`として伝えます。

## 保存済み会話と履歴の取得API

同じ`CodexManagement`から会話の所属・保存cwd・履歴を取得できます。取得だけでは`thread/start`・`thread/resume`を呼ばず、実行対象の選択やモデルの確定値を変更しません。保存cwdが既に存在しなくても取得でき、ディレクトリを再作成しません。

| メソッド | 入力と結果 |
| --- | --- |
| `list_threads(project_id, *, cursor=None, limit=None, sort_direction=None)` | 指定Projectの会話を`ThreadPage`で取得する。`None`は明示的に未所属を選び、全モデルプロバイダーを対象にする |
| `read_thread(thread_id)` | `includeTurns=false`で`ThreadInfo`を取得する。本文の一括取得は行わない |
| `list_turns(thread_id, *, cursor=None, limit=None, sort_direction=None)` | `itemsView=full`を明示し、1ページのturnと各turnに含まれる公開itemを`TurnPage`で取得する |
| `list_items(thread_id, *, turn_id=None, cursor=None, limit=None, sort_direction=None)` | 1ページの公開itemを`ItemPage`で取得する。`turn_id`指定時はそのturnに絞る |

`ThreadInfo`は`id`、`name`、`preview`、`cwd`、`project_id`、`created_at`・`updated_at`、`model`・`reasoning_effort`、公開`status`を持ちます。タイトルやモデル情報が未提供の場合は`None`のまま返し、代替値を補いません。

`TurnInfo`は`id`・`status`、`items`、`items_view`、公開エラー`error`と任意の時刻・所要時間・`root_turn_id`を持ちます。`ThreadItemInfo`は`turn_id`と`item`、任意の`started_at_ms`・`completed_at_ms`を持ちます。itemの本文、画像参照、reasoning、Web検索などの種類別データは、公開されたJSONオブジェクトをそのまま保持します。本文の短縮、表示イベントへの変換、古い質問への回答受付は行いません。

各ページの`threads`・`turns`・`items`はサーバー順のtupleです。`next_cursor`は続きの取得に使い、`backwards_cursor`は`sort_direction`を反転して取得するときに使います。どちらも不透明な文字列または`None`で、クライアントは解析しません。`sort_direction`は`asc`・`desc`、省略時は会話・turn一覧が降順、item一覧が昇順です。ページ取得は1回の要求で終了し、全件を自動収集しません。不正応答・通信失敗の例外はProject管理APIと共通です。

## 保存済みGoalの取得・削除API

`CodexManagement.get_goal(thread_id)`は保存済み会話のGoalを既存の`GoalState`で返し、Goalがなければ`None`を返します。目的、状態、利用量、予算をサーバーの値から補正しません。

`clear_goal(thread_id)`は明示的なユーザー操作として`origin=user`で削除を依頼し、サーバーが返す`cleared`の真偽値を返します。事前のGoal取得は不要で、削除通知も待ちません。`False`は削除が起きなかったことを表します。削除はturnの中断や機器入力の停止ではありません。

どちらも開いている接続とthread IDで操作でき、認証・モデル・Sandboxの実行準備や保存cwdの存在確認を要求しません。会話のresume、MCP・記録部品の準備、自動再送は行いません。不正応答・通信失敗の例外はProject管理APIと共通です。

## イベントと失敗の扱い

`RuntimeEvent`は接続の事実、未変換の`RpcNotification`、`ServerRequest`を含む。呼出し側は通知をthread・turnへ対応付け、要求の有効性と回答を管理する。アプリケーションサービスはこれらを会話表示用のイベントへ変換する。

| 値 | 内容 |
|---|---|
| `SessionStatus` | 接続phase。`connecting / login_required / authenticating / ready / error / closed` |
| `AccountInfo` / `UsageEvent` | 公開アカウント情報と利用枠。`available`は直近取得の成否で、通常利用の可否は公開値を保持 |
| `SandboxEvent` | `ready / notConfigured / updateRequired`または取得不能、準備中、理由、再接続の必要性 |
| `ConversationStatus` | thread・turn・Goalと、送信・停止・回答待ちの状態 |
| `TextEvent` | itemの本文。`replace=False`は追記、`True`は全文の置換。`phase`は公開種別、`completed`はitem完了通知の受信を表す |
| `PlanEvent` | 公開計画の全一覧 |
| `ReasoningEvent` | 公開要約の置換スナップショット。`summary`は`SummaryPart(index, text)`のtupleで、欠番があっても元のindexを保持。`completed`はitem完了通知の受信を表す |
| `ActivityEvent` | 種類・対象・状態・テキスト結果に加え、コマンドの`cwd`・`exit_code`とMCPの`arguments`を別フィールドで保持。`completed`は通知の完了区分、保存履歴では`None` |
| `GoalEvent` | `GoalState(objective, status, tokens_used, time_used_seconds, token_budget)`。削除後は`None` |
| `DialogueEvent` | 完了項目または終端時点の本文スナップショット。`partial`で未確定を識別 |
| `HumanRequest` / `RequestResolved` | 回答要求と受付終了。受付終了は回答の配送成功を意味しない |

`TextEvent.phase`は`commentary / partial_answer / final_answer`または`None`。差分通知は開始itemの値を引き継ぎ、完了itemで更新する。欠落・未知値は`None`とし、本文やturn状態から補わない。phaseはturn・Goalの完了判定には使わない。

表示変換の`HistoryEntry`は、従来の`text`に加えて`phase`・`summary`・`activity`・`completed`を保持する。保存履歴とライブ通知は同じthread・turn・item・表示種別のキーを使う。保存itemにない完了通知の区分は`None`とし、活動の公開`status`とは区別する。`activity.arguments`は公開引数のコピーであり、マスキング処理は行わない。画像結果・メタデータ・構造化結果・生の推論は表示データの対象外。

`DialogueEvent.payload`のreasoningは`summary: [{index, text}, ...]`、activityは`type / title / status / details / cwd / exit_code / arguments`を持つ。messageは本文と、判明した場合のphaseを持つ。中断などの終端では未完了itemを`partial=True`で一度だけ通知する。これらの追加情報をゲームI/O記録へ新たに保存する処理は行わない。

`HumanRequest.token`は接続内の要求を識別する。アプリケーションサービスへ`answer(token, answers={質問ID: 回答})`または`answer(token, decision=判断)`を渡す。回答の成功結果は通信への書込完了を表し、許可対象の実行成功とは別である。未配送は`CodexConnectionError`、配送結果不明は`AnswerDeliveryError`で通知する。

接続失敗の状態と操作結果に共通の`failure_id`がある場合は、接続世代との組で二重表示を防げる。`None`は対応番号なし。同じ理由の文字列だけで別の失敗をまとめない。

実装: [接続](../../../src/gigas/runtime/codex/connection.py)、[管理API](../../../src/gigas/runtime/codex/management.py)、[イベントの値](../../../src/gigas/runtime/codex/models.py)。
