# U05 M2 入力の設定・公開・アプリ結合

## 1. 目的・状態・起点

状態: complete（製品結合・対象構成の受入完了）。M1の入力実行を設定UI・通常ターン・公開ツール・停止へ接続した。

## 2. 対象範囲

ポート・protocol・baud設定、OK／適用による接続変更、GameIoMcpServerによる観測と入力の公開、runと旧呼出しの失効、GUI・質問／承認待ち・終了の停止、模擬結合・実機受入を扱う。

## 3. 対象外

BT、Goal、自動再接続、入力の暗黙分割、dynamic tool、非同期タスク受付・ポーリング。観測・保存失敗による入力停止は導入しない。

## 4. 関連文書・着手条件

[U05親](../unit_010/INPUT_AND_STOP.md)、[M0](../../complete/unit_036/INPUT_AND_STOP_DESIGN.md)、[M1](../../complete/unit_038/INPUT_EXECUTION_AND_OBSERVATION.md)、[責務分離](../../complete/unit_041/INPUT_FAILURE_SEPARATION.md)、[ゲームI/O](../../initial/03-game-io.md)、[SDK互換性](../../initial/08-sdk-compatibility.md)に従う。

M1内部API・最新キャッシュ観測・非同期保存を利用する。公開経路は同期MCPを継続し、呼出し期限の初期値を300秒とする。採用Codexの既定値と数秒〜1分中心の用途を根拠とした設計判断であり、MCP共通の最大値ではない。Codexへは`tool_timeout_sec`を明示し、Gigas側も受付後の期限を持つ。無期限化・progressによる延長は前提にしない。非同期タスク・ポーリング、dynamic tool経路への製品変更は今回行わない。

## 5. 振る舞い仕様

### 設定と接続

ポートは列挙identifierを持つ非編集comboと一覧更新、未接続を用意する。保存値が一覧にない場合も保持して表示する。protocolはM1のPROTOCOLS、baudは選択protocolの候補と既定値を使用する。protocol変更ではbaudを既定値へ戻す。Noneと具体的な既定baudは接続効果を同一視する。

一覧更新・設定を開く操作・起動だけでは入力接続を開始しない。OK／適用で全設定を保存し、成功後に接続へ適用する。取消しは未適用編集だけを破棄する。同じ実効条件で接続中なら維持し、未接続・失敗後は明示適用で再試行する。入力設定を変更した場合と、全設定の実効値が未変更の明示適用だけを入力接続へ反映する。他項目だけの変更は入力へ影響しない。入力と観測解像度だけの変更では映像を開始・再接続しない。複数項目の変更でも各接続への効果を独立に決める。入力変更では旧列を停止・解除・closeしてから新接続を開く。保存失敗は旧接続を維持する。

### 実行・公開・停止

GameIoMcpServer(observations, inputs, protocol)がobserveとact_and_observeを公開する。各通常送信のprepare_turn(prompt, thread_id)で新endpointを作り、適用中protocol・対応入力をツール説明へ含める。初回はthread/start、次回はthread/unsubscribeの完了後に同IDのthread/resumeへ新MCP設定を渡し、読み込み後にturnを開始する。thread_idは初回だけNoneとする。

通常依頼のrunと固定Grantをbind_grantで一度だけendpointへ対応付け、終端・停止後の古い入力要求を拒否する。observeはGrantの有効性に依存しない。入力受付は映像や記録の成功を前提としない。新runでは旧endpointを閉じ、新しいサーバー名・ポート・Bearerを使う。会話の自動置換や旧Grantの復活は行わない。

利用者の停止・接続変更・切断・終了は入力を先に止め、Codex応答や保存を待たない。質問・承認待ちは一時保留とし、待ちが解消し、送信中の回答もなくなってから新要求だけを許可する。外部で要求が解消した場合も保留を解除し、取消した列は再開しない。初回thread準備中に停止・入力接続変更があった場合、そのrunへ後から操作権を与えない。設定・確認モーダルからも停止できる。観測と保存の失敗は入力権限を失効させない。

入力サービスは既存サービススレッドの寿命で接続を所有し、チャット・プロジェクト作成を接続条件にしない。観測・記録の依存はrun開始時に渡して既存Grantへ固定し、run終了で旧文脈への要求を閉じる。Codex切断ではrunを終了するが正常な入力接続は維持し、アプリ終了でcloseする。新しい汎用ルーティング基盤は作らない。

### 要求の期限

MCP公開側の300秒は入力と事後観測を待つ期限であり、内部APIの要素数・入力時間へ固定上限を加えない。時間合計だけで通信・符号化の所要時間は決まらないため、入力時間の合計による別の上限・自動分割は設けない。

期限切れや呼出し元の取消しでは、入力実行中なら共通停止へ進み、開始済みwriteの収束後に解除する。観測中ならその観測応答だけを取り消し、次の入力や操作権を失効させない。GUI停止・run終了は実行中入力と観測応答の両方を止める。開始済みの画像符号化・保存は取得・保存側が回収する。

期限エラーを返せる場合はTOOL_TIMEOUT、phase=unknown、input_sent=unknown、neutral=unconfirmedとする。クライアントとサーバーの計時開始点は異なるため、期限内のエラー配送や物理解除の完了は保証しない。結果を受け取れない呼出しは一部実行済みの可能性があり、自動再送しない。HTTP切断・cancel通知の到達を停止の前提とせず、アプリは入力所有者へ直接停止を届ける。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| green | T01 設定UIでport/protocol/baudを選択・保持し、取消しでは適用しない | regression | integration | 既存settings試験を改訂。省略baudは既定表示・明示保存 |
| green | T02 OK／適用が保存後に入力の実効条件だけを変更し、停止・失敗・再適用を扱う | regression | integration | 複数項目同時変更、最新適用先、開始中停止、初回open/close失敗後の明示切断を模擬検証 |
| green | T03 共通ツール公開から列検査・実入力・画像応答・操作記録まで成立する | regression | integration | 実HTTP・採用runtime・製品サービスから画像とID・保存まで確認。CH552では実モデルの通常turnから操作成功 |
| green | T04 通常送信・turn終端・質問／承認待ち・切断でGrantの有効性を維持する | regression | integration | 旧要求拒否、外部の要求解消後の保留解除、初回thread準備中の接続変更後に旧runが復活しないことを確認 |
| green | T05 モーダル・設定障害・終了中にも入力停止が先行する | regression | integration | 設定・通知詳細・保存中停止を自動試験、実GUIの設定モーダル停止と通常終了を実機で確認 |
| green | T06 同期300秒の期限・長時間操作・途中停止の契約が採用公開経路で成立する | new | integration/runtime | 300秒設定で65秒wait成功、65秒press途中停止、短縮期限でTOOL_TIMEOUTを確認。300秒経過の実待機は未実施 |
| green | T07 対象機器で設定から入力・解除・画面確認まで成立する | new | hardware | USB Video Device、COM8・CH552・9600 baudで設定OK、入力、モーダル停止、移動後の停止映像と保存を確認 |

## 7. 対象ファイル

ui/settings_dialog.py、app/shell.py、services/codex.py、runtime/codex/、mcp/、必要な入力所有境界、対応する既存tests・docs。公開API・型とdocstringは同時に更新する。

## 8. 検証計画と結果

### 自動試験と再レビュー

- `uv run pytest tests/unit tests/integration -q --basetemp tmp/pytest-m2-final`: 660 passed in 53.70s。設定保存・接続適用・HTTP公開・通常turn・停止・期限・記録を確認。
- `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress`: pass。公開型・docstringを照合。
- `uv lock --check` / `uv build`: pass。依存追加なし、sdist/wheel生成成功。
- `git diff --check`: pass。
- `uv run python tmp/validate_m2_docs.py`: 変更文書24件のローカルリンク330件、コードフェンス、仮テキスト残り、42 unitの番号一意性を確認。親と本書をcompleteへ移し、参照を更新した。

既存試験を優先して改訂し、外部RequestResolved後の入力保留、初回thread準備中の接続変更による旧run復活をred→greenで修正した。観測Aの取消しが入力BとGrantを止めないこと、GUI停止・run終了では両方を止めることも確認した。符号化後の成功を期待する試験は既存drainで回収を待ち、処理順の偶然へ依存しない。全体試験後の再レビューで未修正の指摘はない。

### 採用runtimeと模擬機器

`uv run python tmp/mcp-input-investigation/probe_mcp.py --run-name complete-runtime-probe`: exit 0。採用0.167.0の実app-server、隔離CODEX_HOME、localhostのResponses mock、製品MCP・入力・観測・保存を使った。入力は模擬Device、映像は固定Frameであり、外部実モデル・実シリアル・GUIの試験ではない。

300秒設定で65秒waitが正常完了（turn全体65.12秒）し、画像・operation_id・observation_id・保存PNGが一致した。65秒pressの共通停止でINTERRUPTEDとneutralを確認した。同threadのunsubscribe→resumeで3DSの説明・touch座標へ更新され、旧endpointがactive toolsから外れ、模擬touchが成功した。期限は試験プロセス内だけGigas側0.25秒・Codex側1秒へ短縮し、5秒pressにTOOL_TIMEOUTとneutralを確認した。300秒経過の試験ではない。Codex・HTTP・保存を回収した。根拠は`tmp/mcp-input-investigation/RESULT.md`と`complete-runtime-probe/report.json`。

### 実GUI・実モデル・CH552（2026-11-13）

`tmp/u05-m2-live/gui.py`で製品ApplicationShellと隔離設定を起動し、設定画面のOKでUSB Video DeviceとCOM8・CH552・9600 baudを適用した。採用runtime 0.167.0、既存ChatGPT認証、gpt-6.1-sol/lowを使用した。

| 通常run | 結果 |
|---|---|
| RIGHT 100 ms + wait 300 ms | completed、2要素、neutral sent、観測ID・保存PNGを確認 |
| B 65000 ms | 約15秒後に設定ダイアログの停止を押し、interrupted、0完了要素、neutral sentを記録 |
| LEFT 350 ms + wait 500 ms | completed、2要素、neutral sent。左移動して止まる実映像を目視し、モデルの説明と保存PNGを確認 |

3runは同じthread IDを保持し、run・operation・turn・observation IDの対応を記録した。証跡は`tmp/u05-m2-live/chats/7787ddab-7379-46b9-b44f-09f19fac436e/events.jsonl`と同保存先のPNG。Alt+F4でexit 0となり、所有プロセスを回収した。B停止のneutral sentは送出確認であり、機器からの解除ACKを確認した結果ではない。

### 公開方式の根拠

採用0.167.0のdynamic toolは[公開thread型](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server-protocol/src/protocol/v2/thread.rs)にresume時のdynamicTools更新がなく、隔離した実app-serverと模擬Responsesによる確認でも新定義が反映されなかった。同threadでの能力更新には、M0で確認したMCPのunsubscribe→resumeを採用する。

### 呼出し期限とポーリングの比較

想定用途は、1回の入力列が数秒〜1分程度の操作を中心とする。期限設計の比較対象はCodex・Gemini CLI・Claude Codeとし、Copilot CLIやクラウドAPIの制限へ合わせた共通上限は作らない。以下は2026-11-13時点の公式資料・ソースの確認であり、各サービスを接続して長時間操作した結果ではない。

| 経路 | 通常呼出しの期限 | 根拠と区別 |
|---|---|---|
| 採用Codex 0.167.0 | 既定300秒、`tool_timeout_sec`で変更可 | [採用版の解決処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/codex-mcp/src/connection_manager.rs)。0は無期限化ではない。progressで経過時計はリセットしない |
| Gemini CLI v0.68.0 | 既定600秒、サーバー別timeoutをmsで変更可 | [公式設定](https://geminicli.com/docs/tools/mcp-server/)・[呼出し実装](https://github.com/google-gemini/gemini-cli/blob/v0.68.0/packages/core/src/tools/mcp-client.ts)。progressは表示するが期限リセットを指定しない |
| Claude Code | 通常呼出しの経過期限は既定約28時間、サーバー別timeoutで変更可 | [公式時間制限](https://code.claude.com/docs/en/mcp#time-limits-on-mcp-tool-calls)。HTTP等は最初の応答までの時計と、既定5分の無応答時計も持つ。約28時間だけ待てるとは解釈しない |

[MCPのtimeout規定](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle#timeouts)はprogressによる期限リセットを必須にしていない。採用Codexの[要求・結果処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/rmcp-client/src/rmcp_client.rs)は通常CallToolResultを扱い、MCP Tasksの作成・照会を持たない。数秒〜1分中心の用途に合わせ、同期300秒と2ツールを維持する。非同期タスク・独自ポーリングは採用しない。HTTP待機・session寿命・クラウドAPI全体のbackground処理を、このツール期限の解除とは扱わない。

## 9. 未確認事項

B解除ACK、writeの最悪所要時間、DTR/RTSによるreset、PokeCon・3DS実機、他OS、長時間連続運転、300秒経過の実待機は未検証。[U10](../../planned/unit_015/CONTINUOUS_OPERATION.md)の機器条件・継続運転の確認へ引き継ぐ。65秒の成功、短縮期限、CH552の短い実操作という確認範囲を越えた保証はしない。

## 10. 完了条件

- [x] 既存仕様・コードと工程の前提を確認し、必要な子仕様を作成
- [x] 設定・通常turn・同期MCP・停止の結合を検証
- [x] 自動gateと再レビューを完了
- [x] 実モデル・GUI・対象実機の必要な受入を確認
