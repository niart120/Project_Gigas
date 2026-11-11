# U02 M1 公開app-server接続と通常ターン

## 1. 目的・起点

状態: **wip**。2026-11-11のユーザー回答で公開app-server直接接続への変更が承認された。[M0](../../complete/unit_016/CODEX_SDK_SELECTION.md)のSDK不採用判断を引き継ぎ、[U02親](../unit_007/CODEX_CONNECTION.md)の製品接続部を実装する。

利用者はChatGPT認証でモデルとeffortを確認して通常ターンを開始し、質問・承認を保留・回答・中断できる。SDKのprivate APIや二重受信は使わない。

## 2. 対象範囲・対象外

対象は固定runtime `openai-codex-cli-bin==0.167.0`、単一stdio接続、認証状態・ChatGPTログイン、モデル検証、同梱指示配送、通常ターン、質問・承認の要求ID管理、停止、部分起動失敗と終了。GUI操作はM2で接続する。

ゲーム機器、MCPサーバ、Goals、独自攻略ループ、自動再接続は対象外。APIキー方式の実ログイン・課金確認は親の期限（U06まで）を維持し、U02で未対応なら明示する。

## 3. 前提と関連文書

[02](../../initial/02-runtime.md)、[06](../../initial/06-repository.md)、[07](../../initial/07-verification.md)、[08](../../initial/08-sdk-compatibility.md)、[09](../../initial/09-control-flows.md)。U01は完了。直接接続への明示承認によりM0の方式判断待ちは解消した。固定runtimeから生成したJSON schemaを調査し、必要なv2契約だけ実装する。

## 4. 所有・公開契約

`runtime/codex/transport.py`の`StdioTransport`が一つの子プロセス、単一reader、writer lock、client requestのID/Futureを所有する。`start()`で開始し、`request(method, params)`、`notify()`、`respond(id, result)`、`reject(id, code, message)`、`next_event()`、`close()`を提供する。イベントはID付きserver request、notification、閉鎖を区別する。JSONは境界で検証し、生の辞書をGUIへ流さない。

readerは振分けだけを行い、人間の回答・他RPCを待たない。Future登録は送信前。未知・重複・取消し後の応答を別要求へ適用しない。受信不正・EOFはすべての待機へ伝え、自動再接続しない。診断へ受信payload・stderr・資格情報をそのまま含めない。

`close()`は冪等で所有app-serverの終了確認を待つ。期限付きterminate後、必要ならkillし、公開asyncio protocolのprocess_exitedで終了を確認してからstdioを閉じる。子孫がpipeを継承していても、所有processの終了とpipeのEOF待ちを結合しない。Windowsは引数配列、shellなし、CREATE_NO_WINDOW。行サイズ上限を明示する。closeは任意のツール子孫・旧ファイル書込の終了まで保証しない（U07/U09で検証）。

`runtime/codex/connection.py`の`CodexConnection`が認証、モデル、thread/turn、要求種別・有効性、型付き表示イベントを所有する。設定値保存と認証・モデル利用を区別する。同梱play.mdはresourcesで取得しthread/startのdeveloperInstructionsへ渡す。空資材・読込・配送失敗は開始成功にしない。開発repoを実行cwdに使わない。

停止意思をturn ID確定前から保持する。保留要求のGUI回答を先に無効にしてinterruptを送り、質問へ空回答や取消し用JSON-RPC errorを送らない。0.167.0は一般の質問応答エラーを空回答へ変換するため、停止はruntimeのturn中断で失効させる。停止受付とturn/completedを分け、遅い開始・要求・回答で停止を解除しない。要求の重複回答・既解決・別世代を拒否する。不明な要求はJSON-RPC errorで拒否し、許可へ変換しない。

認証の保存主体はruntime。既存の標準CODEX_HOMEを使用でき、明示したhomeで隔離できる。アプリ設定に資格情報を保存せず、接続時のaccount/readだけでは通常ターン成功を表示しない。ログインは明示操作で開始し、URLを表示して利用者がブラウザを開く。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | T01 server requestを保留しても通知と順不同RPC応答を受信できる | new | integration | 実peerで文字列/整数ID、保留要求、逆順応答、通知、拒否返信を確認 |
| refactor-done | T02 EOF・不正入力・timeout・取消しで待機を解放し遅い応答を誤適用しない | edge | integration | EOFの全待機解放、不正frame8種、timeout/取消し、重複/未知応答、秘匿診断を確認 |
| refactor-done | T03 部分起動失敗・連続close・強制終了で所有プロセスとタスクを回収する | edge | integration | 起動/終了取消し・並行呼出し・Windows非表示・terminate/kill後waitを実peerで確認 |
| refactor-skipped | T04 子孫がpipeを継承しても所有プロセス終了後にcloseを完了する | edge | integration | process_exitedとpipe閉鎖を公開asyncio APIで分離 |
| refactor-done | C01 初期化・認証・モデル検証を経て指示を配送してturnを開始する | new | unit/integration | 公開RPC境界のfakeで認証、全cursor、モデル/effort、資材失敗、要求設定照合を確認 |
| refactor-skipped | C02 質問・承認を一度だけ回答し解決・停止後の回答を拒否する | new | unit/integration | 型付き質問/command/file、明示判断、resolved/完了/停止後の回答拒否を確認 |
| refactor-done | C03 開始と停止の競合、通知・切断・終了を順序に従って扱う | edge | unit/integration | 開始応答前の停止・通知、login競合、EOF、並行closeと取消しを確認 |
| refactor-skipped | C04 拡張承認や確認対象欠落を許可へ変換しない | edge | unit | 未対応権限とfile対象欠落/変更を拒否、cancel送信前に受付を閉じる |

各itemをred→green→必要なrefactor→検証→commitの順に進め、履歴を本書へ記録する。固定語句の文書検査は製品テストにしない。

### T01 実行履歴

- red: `uv run pytest tests/integration/test_codex_transport.py -q` は `gigas.runtime` 未実装の `ModuleNotFoundError` で失敗した。
- green: 単一stdout reader、要求別Future、writer lock、IDを保持するserver request/notification、結果・拒否返信を追加。同じpytestは1 passed。
- 検証: `uv run pytest tests/integration/test_codex_transport.py -q --basetemp=tmp/pytest-transport`、対象5ファイルのruff check/format、ty checkが成功。実peerは認証不要で、実Codex受入の代替ではない。
- refactor-skipped: transport/peer/公開イベント型の境界を確認し、追加の構造変更は不要。EOF・timeout・取消しはT02、部分起動と終了保証はT03で続ける。
- commit: `feat(codex): 単一受信のstdio接続で要求と通知を振り分ける`。

### T02 実行履歴

- red: `uv run pytest tests/integration/test_codex_transport.py -q --basetemp=tmp/pytest-transport -x` はEOF後にRPC/イベント待機が残り、試験の5秒打切りで失敗した（1 failed、1 passed）。
- green: 端末イベントの共有、全pending Futureの失敗、要求期限と取消し後のID除去、不正frameの秘匿エラーを実装し13 passed。時間切れ後の送信済み要求は自動再送せず、後続要求へ遅い応答を誤適用しない。
- refactor-done: green後にUTF-8/行サイズ/JSONの検証を `_decode_frame` へ分離し、同じ試験が13 passed。上限は改行を含む1 MiBで送受信双方に適用する。
- 検証: 対象pytest、ruff check/format、ty checkが成功。JSON payloadとstderrを診断へ転記せず、通常RPCエラー後は接続を継続できる。
- commit: `fix(codex): 接続失敗と要求取消しで待機を解放する`。

### T03 実行履歴

- red: 起動失敗の対象pytestは、生の `FileNotFoundError` が返り公開 `TransportError` と終了イベントにならないため1 failed。
- green: startup/cleanupを共有taskにし、起動途中のclose・取消しでも生成されたprocessを回収する。close呼出しの取消しではcleanupを取り消さず、次のcloseで同じ処理を待つ。対象試験は20 passed。
- refactor-done: green後に閉鎖検査とpipe排出を共有処理へ整理した。終了は所有processのwait完了まで確認し、任意の子孫や書込収束を保証しない。端末イベント単独をprocess終了の根拠にはしない。
- Windows実行: python launcherと実processのPIDを混同しないようpeerはbase interpreterを使う。非表示起動、cwd/env、320 KiBのstderr排出を確認。kill分岐はterminateだけをno-opへ故障注入し、実peerへのkillとwait完了を観測した。
- 検証: `uv run pytest tests/integration/test_codex_transport.py -q --basetemp=tmp/pytest-transport` は20 passed。対象ruff check/format・ty check、`uv lock --check`、`uv build` が成功。公開型はAny/cast/ignoreを追加せず、Google style docstringで寿命・取消し・失敗契約を記載した。
- commit: `fix(codex): 起動取消しと終了競合から子プロセスを回収する`。

### T04 実行履歴

- 独立レビューで、親peerを終了しても孫processがstdout/stderrを継承すると、高水準 `Process.wait()` がpipe EOF待ちを続けることを再現した。孫が4秒保持した試行では `close_timeout=0.1` に対しcloseは4.02秒かかった。
- red: `uv run pytest tests/integration/test_codex_transport.py -q --basetemp=tmp/pytest-transport-review -k descendant` は、孫processへpipe解放を通知する前のcloseが1秒以内に完了せず1 failed。後処理で孫を解放し、試験processは残さない。
- green: `loop.subprocess_exec` と公開 `SubprocessProtocol` / `SubprocessTransport` を使い、所有processの `process_exited` を待ってからstdioを `close()` する。private asyncio API、子孫の一括kill、資格情報は使わない。stderrはprotocol callbackで保持せず排出し、stdinのflow controlとstdoutの行上限を維持した。
- 検証: 対象transport pytestは21 passed。対象3ファイルのruff format/check、tyが成功。起動途中のclose、開始取消し、close取消し、terminate/kill、320 KiBのstderr、要求と通知の振分けを同じ実peer試験で再確認した。
- refactor-skipped: processとpipeを分ける内部protocolは今回の修正に必要な構造であり、green後の追加整理は不要と判断した。公開 `StdioTransport` APIは維持し、closeのdocstringへ継承pipeの回収範囲を記載した。
- commit: `fix(codex): 子孫の継承パイプで接続終了を待たせない`。

### C01 実行履歴

- red: `uv run pytest tests/unit/test_codex_connection.py -q --basetemp=tmp/pytest-connection` は `gigas.runtime.codex.connection` 未実装の `ModuleNotFoundError` で失敗した。
- green: 認証を変更しないaccount/read、明示ログインと完了後の再確認、全cursorのモデル取得、モデル/effort照合、resourcesからの指示読込とthread/start配送を実装。未認証・未対応APIキー・資材不正・実行設定不一致ではturn/startへ進まない。
- refactor-done: green後に起動設定検証・URL検証・thread開始を分離。表示イベントとJSON境界を分け、Protocolは実I/O境界の一つに限定した。
- 検証: 対象pytestは10 passed。対象ruff format/check、ty、`uv lock --check`、`uv build`が成功。`uv add openai-codex-cli-bin==0.167.0`を実施し固定依存をlockへ記録。sandbox内のWindows PE生成拒否後、通常権限の同じuv addで成功した。
- 公開APIの型・docstring・利用者向け `docs/codex.md` を照合した。実runtimeのconnection経路・GUI受入は後続で行い、このfake成功で代替しない。
- commit: `feat(codex): 認証とモデルを確認して同梱指示を配送する`。

### C02 実行履歴

- red: 対象pytestは `HumanRequest` 未実装のImportErrorで失敗した。
- green: 元RPC IDと接続を跨いで一意な回答tokenを分離し、質問の選択肢・自由回答・秘匿表示、コマンド・ファイル承認の許可された一回限りの判断を型へ変換した。明示回答なしでは許可せず、重複・解決済み・turn完了・停止後の回答を拒否する。fileChangeのpath/diffは対応するitem/startedから保持する。
- 非blocking質問もターン終端で失効する。根拠は固定版の[TurnComplete処理](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/app-server/src/bespoke_event_handling.rs#L191)と[質問応答処理](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/app-server/src/bespoke_event_handling.rs#L1707)。serverRequest/resolvedは保留解除を意味し、承認・実行成功とは扱わない。
- 検証: `uv run pytest tests/unit/test_codex_connection.py -q --basetemp=tmp/pytest-connection` は17 passed。対象ruff format/checkとtyが成功。実質問・実承認の受入はrootの製品経路試験に残す。
- refactor-skipped: wire変換、回答検証、token失効をそれぞれ小さい関数へ分けた状態を確認し、このcycle後の追加構造変更は不要と判断した。
- commit: `feat(codex): 質問と承認の回答を要求の有効期間に制限する`。

### C03 実行履歴

- red: 対象pytestはthread/start応答前のstopが `stopped` を早期表示するため1 failed、17 passedだった。
- green: 停止意思を保持し、thread開始待ちならturnを抑止、turn受付待ちならID確定後に一度だけinterruptする。先着のturn/started・本文差分・確定本文・質問・終端を扱い、遅い開始応答や回答送信完了で実行状態を復活させない。failed/interruptedを通常成功と分ける。
- login完了の先着と重複開始を扱い、失効したURLを出さない。EOFでは質問受付を閉じる。open/sendの取消しは所有接続を回収し、共有closeは呼出し側の取消し後も継続する。既知の保留loginは終了時にcancelを要求する。
- refactor-done: 開始応答の検証、本文通知、turn終端、login完了、所有operation/cleanupを分離して通知分岐と寿命管理を整理した。
- 検証: `uv run pytest tests/unit/test_codex_connection.py -q --basetemp=tmp/pytest-connection` は30 passed。対象ruff format/checkとtyが成功。公開型・Google style docstring・docsを更新。実runtime受入は別に実施し、このfake成功だけでは完了扱いにしない。
- commit: `fix(codex): 開始停止と通知の競合から接続状態を保護する`。

### C04 実行履歴

- 起点: read-only reviewでnetworkApprovalContext/additionalPermissions/kind=writeStdinとfile grantRootが表示情報から落ちてもaccept可能なことが判明した。対象を表示しない許可を防ぐ回帰itemとして追加した。
- red: 実schemaに沿うnetworkApprovalContext付きcommandからHumanRequestが出ることを再現し1 failed、30 passed。追加のcancel送信待ち試験も、他の質問が有効のまま残るため1 failed、41 passedだった。
- green: network/追加権限/writeStdin/humanCommand/別environment/grantRootを明示拒否する。通常commandでも本文・cwd欠落では許可を提示しない。fileはpath/diff/kindを確認し、移動先も保持する。patchUpdatedなどで提示対象が変わったらtokenを失効して要求を拒否し、変更前の内容への回答を送らない。
- 実wire観測で通常commandにも `environmentId="local"` が付くことを確認し、localは対応値としてdetailsへ保持した。未知environmentの拒否を維持する。
- cancel判断は送信前に停止意思と全質問の失効を確定し、respond待ち中のterminal先着でもstoppedを保つ。停止時に質問へ空回答やJSON-RPC errorを送らない契約は維持した。
- 検証: 対象pytestは42 passed、対象ruff format/checkとtyが成功。サーバに提示されなかったdecline等を追加する処理はない。通常承認の実runtime受入はrootの製品経路で別途確認する。
- refactor-skipped: 変更対象検証と承認形式検証を専用関数へ分離済みで、このcycle後の追加構造変更は不要と判断した。
- commit: `fix(codex): 未対応の権限と未確認の変更を承認から除外する`。

## 6. 対象ファイル

`src/gigas/runtime/`、対応unit/integration tests、`pyproject.toml`/`uv.lock`、利用者向け`docs/codex.md`、本書と初期設計。GUI/app寿命結合はM2で実装する。

## 7. 検証

TDD対象pytest、ruff format/check、ty、依存lock、build、既存unit/integration回帰、配布指示smokeを実施する。実runtimeで認証・通常turn・中断・終了を確認し、模擬peerと区別する。GUIの要求応答・応答性はM2の実画面と結合試験で受け入れる。

現状: 実装・実接続受入はnot run。M0の限定初期化を本製品経路の成功に転用しない。

## 8. 先送り事項

GUIはM2、APIキー実接続はU06まで。Goal/MCP/旧書込終了はM0と親仕様の担当を維持する。

## 9. チェックリスト

- [x] 明示承認と対象範囲・所有契約を記録した
- [ ] TDD項目をcommitと対応付けて完了した
- [ ] 製品・依存・型・docstring・配布gateを記録した
- [ ] 実runtime確認と未検証事項を区別しM2へ引き継いだ
