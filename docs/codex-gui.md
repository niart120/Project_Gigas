# Codex接続画面

## サービスAPI

`gigas.services.codex.CodexServiceThread` は専用QThread内のasyncioループで接続を所有します。`start()`だけではCodexに接続しません。`connect_runtime(ConnectionOptions)`が返す世代番号と、`received`の`ServiceEvent.generation`、`completed`の`ServiceResult.generation`を照合して通知を受け取ります。

GUIスレッドから`login()`、`send(prompt)`、`answer(token, decision=..., answers=...)`、`interrupt()`、`disconnect_runtime()`を呼べます。呼出しは処理を予約して戻ります。`completed`は操作呼出しの終了を通知し、ターン終端は`SessionStatus`で判定します。`interrupt()`の完了だけではターン終了を意味しません。

`shutdown()`は新規操作を閉じ、接続と所有taskを回収します。GUIを動かしたまま`finished`と`wait(0)`を確認し、`shutdown_error`が空である場合に終了成功とします。`QThread.quit()`ではこのサービスのasyncioループは終了しません。
