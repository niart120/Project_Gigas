# Codex接続

Codex接続には `openai-codex-cli-bin==0.167.0` に同梱されたランタイムを使用します。Python SDKは使用せず、公開app-serverプロトコルの標準入出力で接続します。Goalによるターンの開始・継続はapp-serverへ任せ、GIGASは指示の配送、公開情報の表示・記録、機器操作、明示停止を担当します。

認証情報はCodexランタイムが管理します。既定では標準のCodex homeに保存されたChatGPT認証を利用し、GIGASの設定へ資格情報を保存しません。APIキー認証は未対応です。切断はログアウトではありません。

モデルと推論レベルは接続時のモデル一覧に照合します。空欄は一覧の既定値を使い、無効な値を別の候補へ自動変更しません。無効な既定値でも接続・ログインを維持し、入力欄から選び直せます。有効な選択まで送信・Goal操作はできません。モデル一覧の取得失敗では診断を確認し、明示的に切断・再接続してください。

利用枠は認証後と公開更新通知の受信時に取得します。公開された期間、使用済み割合から求めた残割合、リセット日時等を表示し、未提供の値は補いません。取得失敗時は最後の取得値と取得不能の状態を表示します。利用枠の表示値だけで送信・再開を拒否したり、ターンや入力を停止したりしません。実際の要求の可否はサーバーが判断します。自動再試行、予算増額、課金先変更は行いません。

## 接続APIと会話の所有

`gigas.runtime.codex.launch.create_connection(options, emit, *, codex_home=None)` は接続を作成します。`options` は `ConnectionOptions`、`emit` は `RuntimeEvent` を受け取る短い同期コールバックです。作成時にはプロセスを起動しません。同じasyncioループで `open()`、必要なら `login()` を呼び、最後に `close()` を待ちます。

`CodexConnection`は通信、認証、モデル候補と確定選択、Windows実行環境を所有します。`request(method, params)`、`respond(request_id, result)`、`reject(request_id, code, message)`でRPCを配送し、会話の通知・要求をコールバックへ渡します。接続自身は会話・Goal・機器入力を制御しません。アプリでの送信・Goal・停止は[CodexServiceThread](codex-gui.md#サービスapi)を使い、内部の会話実行が調整します。

`open()`は初期化・認証状態・モデル一覧を確認します。未認証時の`LoginEvent.url`は明示的な`login()`後にだけ届きます。ブラウザは利用側が開き、`account/login/completed`とアカウント再取得で結果を確認します。URLは認証処理の終了時に破棄してください。

`propose_model(model, effort)`は候補の妥当性を検査し、`commit_model(selection)`は確定選択を通知します。製品では会話実行が、threadがあれば`thread/settings/update`の成功を待って確定します。通常指示は選択値を`turn/start`へ渡し、Goalの自律ターンはサーバーが設定を受理した後から使います。実行中のターンの設定は書き換えません。

`SessionStatus`は接続・認証の状態で、phaseは`connecting / login_required / authenticating / ready / error / closed`です。`ConversationStatus(thread_id, turn_id, goal, sending, stopping, waiting_for_answer)`は会話実行が保持するサーバー情報と処理中taskの投影です。送信、停止、回答待ちを接続phaseへ混ぜず、表示側に独立した実行状態機械を持ちません。

`AccountInfo`は公開アカウント情報、`UsageEvent`は利用枠の取得結果です。`available`は直近取得の成否、`ordinary_usage_allowed`は公開された通常利用の可否です。背景取得taskは接続側が回収し、部分通知から値を推測せず情報を再取得します。これらは表示情報であり、会話の受付条件には使いません。

## 実行環境と初回準備

`SandboxEvent(readiness, setting_up, detail, reconnect_required)`はWindows実行環境の状態です。`readiness`は`ready / notConfigured / updateRequired`、取得不能は`None`です。接続後に認証と独立して取得します。`setup_windows_sandbox()`は明示的な準備を依頼し、開始受付だけでなく完了通知と状態再取得を待ちます。Windowsの確認操作は本人が行います。

準備失敗・取得不能は理由を残して失敗を返し、接続を維持します。成功後も非readyなら`reconnect_required=True`となり、明示的な再接続が必要です。受付不明では再送せず通知を待ちます。待機中も切断できますが、Windows側の準備取消しは保証しません。GUIはturn・active Goal・送信・停止処理がある間の準備操作を無効にします。

通常指示とGoal操作は、接続・認証、有効なモデルと推論レベル、準備済みの実行環境を要求します。別の送信または停止処理中は受け付けません。turn実行中、質問待ち、機器未接続、映像未取得、利用枠の表示だけでは拒否しません。回答は元要求の有効性と接続・停止状態で判断し、通常指示の送信中にも配送できます。

初回の通常指示またはGoal作成で、記録先、`memory/`、観測・記録サービス、ゲームI/O MCPを準備して永続threadを作ります。接続・ログインだけでは記録先を要求しません。threadのcwdと書込先は記録先の`memory/`に限定し、追加書込先・一時領域・ネットワークは許可しません。応答のモデル、cwd、承認設定、sandboxを検証します。ユーザー側のMCP設定は接続用に無効化し、GIGASが明示した設定だけを使います。

配布物内の`play.md`と、`observations/<観測ID>.png`・`events.jsonl`の参照方法を開発者指示として渡します。MCPのURL・認証値をその案内や表示・原記録へ含めません。画像保存は非同期で、応答直後のファイル存在や障害時の全画像保存は保証しません。

同じ会話ではthreadとMCP入口を再利用します。送信・Goal再開ごとの`thread/unsubscribe`・`thread/resume`や入口の作り直しは行いません。初回thread IDの記録は非同期に提出し、保存完了をターン開始の条件にしません。準備失敗は操作の失敗として返し、認証を維持します。

## 通常指示とGoal

通常指示と追加指示はどちらも`turn/start`へ一度送ります。既存turnへの介入か新turnの開始かはサーバーが判断し、特定turnだけへ届ける保証は持ちません。質問への回答はこの経路へ変換しません。送信処理は受付応答と対象turnの開始または終端通知を確認して完了します。既に把握した現在turnへの追加では新たな開始通知を待ちません。指示への遵守やゲーム操作の成功を意味せず、応答・通知を確認する前に切断した場合は自動再送せず原稿を保持します。

| 操作 | 要求 |
| --- | --- |
| Goalなしで目的を送る | `thread/goal/set(objective=..., status=active, origin=user)` |
| 既存Goalの目的を編集する | `thread/goal/set(objective=..., origin=user)`。状態・予算・累積利用量を変えない |
| 保持Goalを再開する | `thread/goal/set(status=active, origin=user)`。目的文・予算を送らない |
| Goalを削除する | `thread/goal/clear(origin=user)`。現在turnと操作列を中断しない |

Goal操作に通常メッセージや追加のターン開始を重ねません。completeの目的編集でも暗黙に削除・新規作成せず、別のGoalとして開始したい場合は削除してから作成します。通常メッセージの「再開して」は本文のまま送り、Goal再開には変換しません。

Goalの表示は通知を正本とします。変更応答に対応する通知を反映するまで同じ送信taskを保持し、応答が現在値のままなら追加通知を待たず終了します。遅い応答で後続の通知値を巻き戻しません。制限中の再開が拒否された場合や、成功応答でも制限状態のままの場合は、その状態を維持し、処理終了後に再開操作を再び受け付けます。

Goalのcomplete・paused・blocked・制限状態、turnの自然終了・失敗、質問待ちだけでは、GIGASから機器停止を起動しません。非activeのGoalがあっても通常指示によるゲーム操作はできます。

## 共通停止と切断

共通停止は送信中にも割り込みます。停止taskがある間は新しい指示・Goal操作・回答・機器入力を拒否します。入力列へ取消しを届け、開始済みwriteと必要なneutral送出を回収します。未提出の送信を取り消し、提出済みの要求・回答は配送結果を確認します。既知turnの中断と入力解除は、Goal変更の決着を待たずに進めます。

送信済みGoal操作の結果と通知を処理した上で、Goalがactiveなら保持したままpausedを要求します。completeや制限状態など既に非activeの値は上書きしません。停止中に新たな自律turnが始まった場合も同じ停止処理で中断し、各対象の中断応答と終端通知を待ちます。

停止の正常結果は、サーバー側の停止と捕捉した入力列の解除・回収が完了した意味です。完了後は通常の受付へ戻り、別の再受付状態や機器の再許可操作はありません。保持Goalの自律継続には利用者の再開操作が必要です。停止は全バックグラウンドプロセスの終了や、ゲーム内セーブの成功を保証しません。

入力解除だけが失敗した場合は機器を使用不能として停止失敗を返し、サーバー側停止を確認できた会話は維持します。サーバー側停止の失敗ではサービスが接続・MCPを閉じ、ローカル処理を回収して失敗を返します。通知待ちが続く間も明示切断できます。切断は応答待ちを打ち切りますが、開始済み機器I/O・符号化・保存は所有者が回収します。

`CodexConnection.close()`は認証処理・受信処理・stdio接続を終了するAPIです。会話・MCP・入力・保存を含む製品の切断はサービスが一つの終了taskで所有します。並行する切断はこれを共有し、停止taskを待つ循環を作りません。切断だけでGoalを削除せず、Goal休止の永続化や任意の子孫プロセス終了は保証しません。

## 公開イベントと回答

`TextEvent`は`item_id`単位で、`replace=False`なら追記、`True`なら確定本文への置換です。`PlanEvent`は公開計画の全一覧、`ReasoningEvent`は公開要約だけ、`ActivityEvent`は公開されたツール対象とテキスト結果を持ちます。内部推論全文、MCP引数・画像・メタデータ、生RPCやstderrを画面へ流しません。

`turn/started`から現在turnを採用し、`TurnStarted`を発行します。本文・item・質問はそのturnへ対応付け、`turn/completed`で内容を締めます。後から届く同turnの本文・活動を表示・記録へ補いません。`turn/start`応答は受付IDの確認に使い、通知より遅い応答で終了済みturnを実行中へ戻しません。Planや要約の毎turn出力は保証しません。

`GoalEvent(goal)`は`GoalState(objective, status, tokens_used, time_used_seconds, token_budget)`、削除後は`None`です。`token_budget`は未指定なら`None`です。`DialogueEvent`は保存向け公開項目のスナップショットで、項目完了は`partial=False`、終端・切断時の未確定部分は`partial=True`として一度だけ確定します。

質問・承認の`HumanRequest.token`は接続内の要求を識別します。回答は`answer(token, answers={質問ID: 回答})`、承認は`answer(token, decision=判断)`で元要求へ返します。提示された選択肢・判断だけを使い、自動許可や永続許可へ変換しません。`RequestResolved`は受付終了を表し、回答の配送成功ではありません。回答結果は通信への書込完了で、許可対象の実行成功ではありません。

停止・turn終端・切断で失効した要求に遅い回答を送りません。未配送は`CodexConnectionError`、送信結果不明は`AnswerDeliveryError`として区別し、自動再送しません。MCP追加情報要求と、`isSecret=true`を含む質問全体は未対応として返します。採用runtimeは質問エラーを空回答として扱うため、拒否はturn中断を意味しません。

コマンド承認は一回限りの実行を対象とし、コマンド・cwd・提供された`local`環境を示します。追加権限、ネットワーク固有承認、既存端末への入力、別環境、継続的な書込許可は未対応です。ファイル承認は種別・パス・移動先・差分を対象とし、不足・提示後の変更があれば許可しません。

接続失敗の状態と操作結果に共通の`failure_id`がある場合は、接続世代との組で二重表示を防げます。`None`は対応番号なしであり、同じ理由というだけで別の失敗を消しません。

## 制約と検証範囲

`codex_home`を指定すると認証・履歴の保存先を分離できます。資格情報をその場所へコピーしません。プロセスのcwdは絶対パスの既存フォルダーを指定し、開発リポジトリから分けてください。

標準入出力のJSON一行は末尾改行を含め受信2 MiB、送信1 MiBまでです。MCP画像はHTTP応答で配送し、stdioのpreviewから元画像を復元しません。

会話・GUI・MCP・機器入力の分担は模擬app-server・機器と実HTTPで試験し、実モデルとの接続とCH552による短いゲーム操作・中断も確認しています。GUIから実機までの通し操作、他の入力機器・OS、長時間運転は未検証です。詳細は[実接続の確認](codex-verification.md)を参照してください。専用の計画的終了は初期版対象外で、「保存して終えて」も通常指示として扱います。
