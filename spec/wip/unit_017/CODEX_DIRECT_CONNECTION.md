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

`close()`は冪等で所有app-serverのwait完了を待つ。期限付きterminate後、必要ならkillしてwaitし、reader/stderrを回収する。Windowsは引数配列、shellなし、CREATE_NO_WINDOW。行サイズ上限を明示する。closeは任意のツール子孫・旧ファイル書込の終了まで保証しない（U07/U09で検証）。

`runtime/codex/connection.py`の`CodexConnection`が認証、モデル、thread/turn、要求種別・有効性、型付き表示イベントを所有する。設定値保存と認証・モデル利用を区別する。同梱play.mdはresourcesで取得しthread/startのdeveloperInstructionsへ渡す。空資材・読込・配送失敗は開始成功にしない。開発repoを実行cwdに使わない。

停止意思をturn ID確定前から保持する。保留要求のGUI回答を先に無効にし、必要な拒否・取消しを返信してinterruptを送る。停止受付とturn/completedを分け、遅い開始・要求・回答で停止を解除しない。要求の重複回答・既解決・別世代を拒否する。不明な要求はJSON-RPC errorで拒否し、許可へ変換しない。

認証の保存主体はruntime。既存の標準CODEX_HOMEを使用でき、明示したhomeで隔離できる。アプリ設定に資格情報を保存せず、接続時のaccount/readだけでは通常ターン成功を表示しない。ログインは明示操作で開始し、URLを表示して利用者がブラウザを開く。

## 5. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | T01 server requestを保留しても通知と順不同RPC応答を受信できる | new | integration | 実peerで文字列/整数ID、保留要求、逆順応答、通知、拒否返信を確認 |
| todo | T02 EOF・不正入力・timeout・取消しで待機を解放し遅い応答を誤適用しない | edge | integration | payloadを例外へ漏らさない |
| todo | T03 部分起動失敗・連続close・強制終了で所有プロセスとタスクを回収する | edge | integration | Windows非表示、実process |
| todo | C01 初期化・認証・モデル検証を経て指示を配送してturnを開始する | new | unit/integration | 認証なし・無効モデル・資材失敗も対象 |
| todo | C02 質問・承認を一度だけ回答し解決・停止後の回答を拒否する | new | unit/integration | 明示要求schemaと拒否既定 |
| todo | C03 開始と停止の競合、通知・切断・終了を順序に従って扱う | edge | unit/integration | turn終了と中断受付を分ける |

各itemをred→green→必要なrefactor→検証→commitの順に進め、履歴を本書へ記録する。固定語句の文書検査は製品テストにしない。

### T01 実行履歴

- red: `uv run pytest tests/integration/test_codex_transport.py -q` は `gigas.runtime` 未実装の `ModuleNotFoundError` で失敗した。
- green: 単一stdout reader、要求別Future、writer lock、IDを保持するserver request/notification、結果・拒否返信を追加。同じpytestは1 passed。
- 検証: `uv run pytest tests/integration/test_codex_transport.py -q --basetemp=tmp/pytest-transport`、対象5ファイルのruff check/format、ty checkが成功。実peerは認証不要で、実Codex受入の代替ではない。
- refactor-skipped: transport/peer/公開イベント型の境界を確認し、追加の構造変更は不要。EOF・timeout・取消しはT02、部分起動と終了保証はT03で続ける。
- commit: `feat(codex): 単一受信のstdio接続で要求と通知を振り分ける`。

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
