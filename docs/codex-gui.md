# Codex接続画面

## 接続と通常ターン

「設定…」でモデル・エフォート・認証方式を保存してから「接続」を押します。空のモデルとエフォートはruntimeの既定値を使います。接続中の保存は次の接続へ適用され、接続画面には実際に選ばれたモデルとエフォートを表示します。

起動だけでは接続・ログインしません。既存のChatGPT認証が使えないときは「ログイン」を押し、表示されたURLを「ブラウザで開く」で開きます。認証完了はruntimeからの通知とアカウント再確認で判定します。APIキー認証は未対応です。

下部の入力欄から「送信」で通常ターンを開始します。「中断」はそのターンの中断を要求し、「停止」表示まで終端確認を待ちます。「切断」はruntime接続を終了します。実行中の追加指示とゲーム操作は未対応です。送信失敗時の入力は残りますが、受付結果が不明な要求を自動再送しません。

会話の作業場所は設定ファイルと同じフォルダーの`runtime`です。接続時に作成します。会話履歴をGIGASのプロジェクト記録へ保存・復元する機能は未実装です。

## 質問と承認

質問カードはすべての質問へ回答すると「回答」が有効になります。選択肢は初期選択せず、Codexが自由回答を許す質問では「その他」と入力欄を使えます。秘密入力は画面で伏せ、提出・失効時に入力欄から消します。

承認カードにはCodexが提示したコマンド・変更内容と、利用できる「許可」「拒否」「取消し」を表示します。既定の許可や自動回答はありません。送信した回答、中断・切断・ターン終了で失効した要求には再回答できません。

`ConnectionPanel.cards[token]`は表示中の`RequestCard`です。`editors[question_id]`に選択肢と回答欄、`decision_buttons[decision]`に承認操作があります。`answer_requested`は`AnswerSubmission`を通知し、元のRPC IDではなく接続固有tokenを接続へ返します。

## サービスAPI

`gigas.services.codex.CodexServiceThread` は専用QThread内のasyncioループで接続を所有します。`start()`だけではCodexに接続しません。`connect_runtime(ConnectionOptions)`が返す世代番号と、`received`の`ServiceEvent.generation`、`completed`の`ServiceResult.generation`を照合して通知を受け取ります。

GUIスレッドから`login()`、`send(prompt)`、`answer(token, decision=..., answers=...)`、`interrupt()`、`disconnect_runtime()`を呼べます。呼出しは処理を予約して戻ります。`completed`は操作呼出しの終了を通知し、ターン終端は`SessionStatus`で判定します。`interrupt()`の完了だけではターン終了を意味しません。

`shutdown()`は新規操作を閉じ、接続と所有taskを回収します。GUIを動かしたまま`finished`と`wait(0)`を確認し、`shutdown_error`が空である場合に終了成功とします。`QThread.quit()`ではこのサービスのasyncioループは終了しません。
