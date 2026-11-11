# Codex接続

Codex接続には `openai-codex-cli-bin==0.167.0` に同梱されたランタイムを使用します。Python SDKは使用せず、公開app-serverプロトコルの標準入出力で接続します。

認証情報はCodexランタイムが管理します。既定では標準のCodex homeに保存されたChatGPT認証を利用します。GIGASの設定には資格情報を保存しません。APIキー認証は現在未対応です。

モデルとエフォートは接続時のモデル一覧に照合します。空欄は一覧の既定値を使い、無効な値を別モデルや別エフォートへ自動変更しません。設定の保存だけでは認証やターンの成功になりません。

## Python API

`gigas.runtime.codex.launch.create_connection(options, emit, codex_home=None)` は接続を作成します。`options` は `models.ConnectionOptions`、`emit` は `models.ConnectionEvent` を受け取る短い同期コールバックです。作成時にプロセスは起動しません。

同じasyncioループで `open()`、必要なら明示的に `login()`、認証後に `send(prompt)` を呼びます。`open()` は初期化・認証状態・モデル一覧を確認します。未認証時の `LoginEvent.url` は `login()` の開始後にだけ届き、ブラウザを開く操作は利用側で行います。`account/login/completed` とアカウントの再取得によって認証結果を確認します。

`send()` は配布物内の `play.md` を開発者指示として配送し、読み取り専用・人間による承認・一時スレッドで通常ターンを開始します。利用者のMCP設定は接続用に無効化します。モデルから届く本文以外に、生のプロトコル応答やstderrを表示イベントへ流しません。

`CodexConnectionError` は表示可能な短い理由を持ちます。通信で受付を確認できなかった要求を自動再送しません。最後に `close()` を待ち、所有するapp-serverとイベント受信処理を終了してからasyncioループを停止してください。

質問・承認は `HumanRequest` として届きます。回答にはその `token` を使い、質問は `answer(token, answers={質問ID: 回答})`、承認は `answer(token, decision=判断)` を呼びます。判断は `decisions` に含まれる値から明示的に選びます。自動許可や永続許可への変換はありません。`RequestResolved(token)` が届いた要求は回答操作を無効にしてください。解決済み、停止後、ターン完了後の回答は送信しません。MCPの追加情報要求は現在未対応として拒否します。

`stop()` は質問の回答受付を閉じ、実行中ターンへ中断を要求します。`stop()` が戻った時点と `SessionStatus.phase == "stopped"` は別です。ランタイムからターンの終端を確認するまで停止完了として表示しません。停止時に空回答を送る処理はありません。

`codex_home` を指定すると認証・履歴の保存先を分離できます。既存の資格情報をこのディレクトリへコピーする処理はありません。作業ディレクトリは絶対パスの既存フォルダーとし、開発リポジトリと分けてください。

この接続はゲーム機器の制御、Goalsによる継続攻略、APIキー認証には対応していません。
