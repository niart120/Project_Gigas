# ゲームI/O MCP API

`gigas.mcp.game_io.GameIoMcpServer`は、[観測サービス](recorded-observation.md)と[入力サービス](input-api.md)をStreamable HTTPで公開するPython APIです。アプリは通常ターンの送信準備で起動し、Codexの`thread/start`または`thread/resume`へ設定を渡します。[会話と保存先の扱い](codex-gui.md)はCodexサービスが所有します。

## 所有と利用順

既存のasyncioループ上で`GameIoMcpServer(observations, inputs, protocol)`を作り、`await server.start()`で起動します。`protocol`は適用中の`InputProtocol`、入力未接続なら`None`です。映像・入力機器や保存先は開きません。`127.0.0.1`のOS割当ポートで待ち受け、インスタンスごとに異なるサーバ名とBearerを発行します。

実thread IDが確定したら、入力サービスの`begin_run(...)`で得た操作権を`server.bind_grant(grant)`で一度だけ対応付けます。対応付け前や操作権の失効後も`observe`を利用できます。`act_and_observe`は対応付けたrunだけに入力し、停止・run終了後の古い操作権を拒否します。同じサーバーへ次のrunを再設定することはできません。

起動後の`server.server_name`は設定キー、`server.config`は`url`と`http_headers`を持つ設定値です。ランタイム固有の設定への組立は呼出側が行います。ヘッダーの認証値をプロンプト・原記録・通常ログへ含めないでください。起動前・終了開始後の`url`と`config`参照は`RuntimeError`です。

`await server.close()`は新しい要求の受付を閉じ、HTTPとMCP SDKの終了を待ちます。終了待ちを取り消してもサーバTaskは所有され、再度`close()`で終了を確認できます。同じインスタンスは再起動せず、次のrunには新しいインスタンスを作ります。起動失敗も例外として返し、アプリのプロセス全体を終了させません。

アプリ所有者は入力を先に停止し、HTTPを終了します。続いて`await observations.drain()`で画像生成、`await recording.drain()`で保存を回収してからwriterとイベントループを閉じます。サーバーは入力接続・writerを所有せず、映像取得サービスの停止も行いません。保存に失敗していても回収は完了し得るため、終了確認と保存成功を分けて扱います。

アプリは次の明示送信時にサーバーを作り直し、適用中プロトコルをツール説明へ反映します。Codex側は同じthread IDのまま購読を解除して再開し、新しい公開情報を読み込みます。以前のURL・認証情報は引き継ぎません。設定値はapp-serverへだけ渡し、グローバル設定ファイル・会話本文・原記録へ保存しません。

## ツールと応答

`observe`は引数なしで最新の有効なFrameの画像を返します。`act_and_observe(sequence)`は有限・非空の入力列全体を検査してから実行・解除し、直後の最新キャッシュを画像にします。入力形式は[入力状態と操作列API](input-api.md)を参照してください。どちらも新しいフレームを待たず、操作が画像へ反映されたことは保証しません。

`act_and_observe`の説明には適用中のプロトコル、対応ボタン、stick・touchの形式と対応可否を含めます。未接続時は入力不可と表示し、実際の入力要求も拒否します。入力枠は列と解除の終了時に解放し、画像符号化中は次の入力を許可します。映像・符号化・保存の失敗だけで入力を停止しません。

`tools/list`で`observe`は`destructiveHint: false`、`openWorldHint: false`を公開します。`act_and_observe`は機器を操作するため`readOnlyHint: false`、`destructiveHint: true`、`idempotentHint: false`、`openWorldHint: true`です。これらの情報だけで操作の承認や成功が保証されるわけではありません。

- image content: 完成したPNGをbase64化したもの。MCP公開部で画像加工・再符号化は行いません。
- text content: JSONの`observation_id`、`captured_at`、`source_frame`、`image`。`act_and_observe`には`operation_id`も含みます。元Frameの識別情報・寸法と出力画像の形式・寸法を区別します。

保存には同じObservationを使います。保存開始・完了・進行状況を、MCP応答や次の観測の待機条件にしません。観測IDに対応する画像ファイルが応答時点で存在する保証はありません。

失敗は画像を含まない`isError: true`の結果とし、textに`code`と`message`を返します。入力要求では判明している`phase`、`completed_steps`、`input_sent`、`neutral`と、実行を開始した場合の`operation_id`も返します。操作記録には入力内容・実行結果と、応答画像が得られた場合の観測IDを残します。観測失敗でも完了済みの入力結果は変えません。保存失敗は応答成功を変更せず、記録サービスの失敗通知で扱います。

## 待機期限と停止

アプリはCodexのMCPツール待機に300秒を指定します。Gigas側も`act_and_observe`の入力と観測応答を待つ期限を300秒とします。同期呼出しの待機期限であり、内部APIの入力時間・要素数・合計時間へ上限を加えるものではありません。非同期受付やポーリングは提供しません。

期限時に入力実行中なら操作権を失効させ、開始済みwriteが戻ってから解除します。観測待ちならその応答だけを取り消し、後続入力と操作権を維持します。開始済みの符号化・保存は別途継続します。GUI停止やrun終了は観測応答だけでなく、実行中の入力も止めます。

期限エラーを返せる場合は`code: TOOL_TIMEOUT`、`phase: unknown`、`input_sent: unknown`、`neutral: unconfirmed`を返します。クライアントとサーバーは別々に計時するため、期限内のエラー配送や実機の解除完了を保証しません。結果を受け取れない要求には一部実行済みの可能性があり、同じ列を自動再送せず、観測して状態を確認してください。

HTTP切断やMCPの取消し通知が入力の停止へ届くことは保証しません。アプリはHTTP応答を待たず入力所有者へ停止を届けます。MCPサーバーの終了だけを入力停止として扱わないでください。

## 接続上の制約

Bearer欠落・不一致はHTTP 401、終了開始後の受付は503です。待受停止後は接続自体が成立しません。Hostと、存在する場合のOriginは当該loopbackアドレスに限定します。MCPセッションの履歴・再配送用ストアは保持しません。

実HTTP経路での画像受信と、Codexモデルが画像を解釈したことは別の確認です。このAPI単独ではモデルの受信・解釈・会話保存を保証しません。

採用Codexの実MCP経路では、模擬機器で65秒wait、途中停止、同threadでの3DS能力再読込、短縮期限のTOOL_TIMEOUTを確認しました。Windowsの製品GUI・実モデル・CH552では入力、設定画面からの停止、移動後に止まる映像と保存を確認しています。短縮期限は300秒経過の試験ではありません。機器の解除ACK、writeの最悪時間、DTR/RTSのreset、PokeCon・3DS実機、他OS、長時間連続運転、300秒経過は未検証です。
