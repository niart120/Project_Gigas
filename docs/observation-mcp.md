# 観測MCP API

`gigas.mcp.observation.ObservationMcpServer`は、[観測サービス](recorded-observation.md)の画像をStreamable HTTPで公開するPython APIです。現在のGUI・Codex接続からの自動起動には未接続です。

## 所有と利用順

既存のasyncioループ上で`ObservationMcpServer(observations)`を作り、`await server.start()`で起動します。映像取得元や保存先は開きません。`127.0.0.1`のOS割当ポートで待ち受け、接続ごとに異なるサーバ名とBearerを発行します。

起動後の`server.server_name`は設定キー、`server.config`は`url`と`http_headers`を持つ設定値です。ランタイム固有の設定への組立は呼出側が行います。ヘッダーの認証値をプロンプト・原記録・通常ログへ含めないでください。起動前・終了開始後の`url`と`config`参照は`RuntimeError`です。

`await server.close()`は新しい要求の受付を閉じ、HTTPとMCP SDKの終了を待ちます。終了待ちを取り消してもサーバTaskは所有され、再度`close()`で終了を確認できます。同じインスタンスは再起動せず、新しい接続では新規インスタンスを作ります。起動失敗も例外として返し、アプリのプロセス全体を終了させません。

HTTPの終了後、アプリ所有者が`await observations.drain()`で進行中の画像生成・保存を回収してからwriterとイベントループを閉じます。サーバはwriterを所有せず、取得サービスの停止も行いません。保存に失敗していても回収は完了し得るため、終了確認と保存成功を分けて扱います。

## ツールと応答

公開するツールは引数なしの`observe`です。最新の有効なFrameから観測サービスが確定した画像を返します。

- image content: 完成したPNGをbase64化したもの。MCP公開部で画像加工・再符号化は行いません。
- text content: JSONの`observation_id`、`captured_at`、`source_frame`、`image`。元Frameの識別情報・寸法と出力画像の形式・寸法を区別します。

保存には同じObservationを使います。保存開始・完了・進行状況を、MCP応答や次の観測の待機条件にしません。観測IDに対応する画像ファイルが応答時点で存在する保証はありません。

取得・符号化失敗は画像を含まない`isError: true`の結果とし、textに`code`と`message`を返します。保存失敗は観測成功を変更せず、観測サービスの保存エラー通知で扱います。

Bearer欠落・不一致はHTTP 401、終了開始後の受付は503です。待受停止後は接続自体が成立しません。Hostと、存在する場合のOriginは当該loopbackアドレスに限定します。MCPセッションの履歴・再配送用ストアは保持しません。

実HTTP経路での画像受信と、Codexモデルが画像を解釈したことは別の確認です。このAPI単独ではモデルの受信・解釈・会話保存を保証しません。
