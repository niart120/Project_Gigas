# 08-sdk-compatibility.md — SDK互換性・未確認事項

[文書一覧](README.md)

第2〜6節には2026-11-10の初回調査と、その後に採用した契約を分けて記載します。2026-11-11のU02 M0では配布SDK・runtime `0.167.0` を再調査し、SDK方式の不成立を確認しました。製品にはruntime `openai-codex-cli-bin==0.167.0` と公開app-server直接接続を採用しています。採用版の現在の判断は冒頭のU02〜U06実行結果を優先し、過去のSDK調査や現行Web文書の記述で上書きしません。

## U02の採用判断

`openai-codex==0.167.0`（対応runtime `openai-codex-cli-bin==0.167.0`）は、要求handlerを唯一のreader内で同期実行するため製品接続に不採用です。模擬peerを使い、回答保留中は後続通知と別要求の応答が読めず、回答後に双方届くことを再現しました。公開の保留・後送APIは確認できません。現在の開発コミット`039d0f27d56c31913ffc0f2055a3432b1bcb0b06`も同じclient.pyで、より新しい公開prereleaseはありません。

Windows x64/Python 3.13.5の隔離環境で、固定runtimeのinitialize、未認証account/read、model/list、close呼出しと調査Pythonの正常終了を確認しました。これは認証・モデル利用・通常ターン・異常時の全資源終了の保証ではありません。closeはkill後のwaitや内部スレッド生存の確認結果を公開しないため、終了契約も別途解決が必要です。

詳細な配布物hash、API一覧、再現コード、GUI・サービス所有設計は[M0子仕様](../complete/unit_016/CODEX_SDK_SELECTION.md)にあります。2026-11-11にユーザーが公開app-server直接接続を承認しました。[M1](../complete/unit_017/CODEX_DIRECT_CONNECTION.md)で単一stdio接続を実装し、既存ChatGPT認証で通常応答、質問への回答・中断、command承認の取消し、file承認の拒否を確認しました。SDKは製品依存に追加していません。U02完了時のGoal・MCP・旧書込終了は入口の存在確認に留まりました。その後の観測MCPはU04 M2、入力・停止はU05で製品確認済みです。GoalはU06 M0で限定確認を終え、製品結合と旧書込の収束に関する残作業は第7節で区別します。

採用版のGUI結合・全体品質ゲートは[U02親仕様](../complete/unit_007/CODEX_CONNECTION.md)に記録します。新規ブラウザ認証、APIキー、承認accept後の実行、別版・別環境は、上記の受入結果に含みません。

## U04 M0のMCP公開経路（2026-11-12）

[M0子仕様](../complete/unit_031/RECORDED_OBSERVATION_DESIGN.md)で、同梱runtime `0.167.0`の公開schemaを生成し、公開tag `rust-v0.167.0`（`086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`）の設定・通知処理を読んだ。以下の表はM0時点の調査記録であり、その時点では製品MCP、認証付きHTTP、画像の実モデル配送は実行していない。現在の実装・実行結果は次節で区別する。

| 根拠 | 確認した事実・設計への適用 |
|---|---|
| 同梱実行ファイルの`app-server generate-json-schema --experimental` | `ThreadStartParams.config`、`mcpServerStatus/list`のthreadId/serverName、`mcpServer/tool/call`のthreadId/server/tool/argumentsを確認。後二つは限定接続検証に利用できるが、専用の製品操作経路は作らない |
| [採用tagの設定schema](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/config.schema.json#L4208)・[公式MCP設定](https://learn.chatgpt.com/docs/extend/mcp) | URL、http_headers、enabled_tools、required、startup_timeout_sec、tool_timeout_secが公開されている。現製品の外部MCP無効化とthread/start.configへ、アプリ専有サーバを追加する方針 |
| [MCPのimage content](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | 同じPNG bytesをbase64のimage contentで返し、観測ID・取得UTCを短いJSON textで返せる。structuredContentとの二重配送を必須にしない |
| [採用tagのモデル配送と通知](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/mcp_tool_call.rs#L695) | 通知用には切詰め結果、モデル側には元の結果を渡す。Gigasの保存PNGとMCP応答の同一性を、モデル内部の画像前処理まで拡張しない |
| [通知結果上限](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/utils/pty/src/lib.rs#L25)・[切詰め処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/utils/output-truncation/src/lib.rs#L69) | 結果全体を最大1 MiBのpreviewへ制限する。外側JSONが加わるためM0当時のGigas受信1 MiBでは不足し得る。M2で2 MiBの有界受信と実通知サイズを確認する |
| [通知画像省略feature](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/features/src/lib.rs#L1922) | 開発中・既定false。切詰め後のtextに残る画像断片を消す保証もないため、U04の必須設定にしない |
| [公式Python MCP SDK v2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0)・[ASGI組込み](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/run/asgi.md)・[実行設定](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/run/index.md) | Python >=3.10。`MCPServer.streamable_http_app(json_response=True, stateless_http=True)`を既存サービス用ループ上でホストする候補。lifespanによるsession manager起動・終了が必要。依存導入・Windows実行は未実施 |
| [Streamable HTTPの寿命](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) | 接続／session IDを操作許可と同一視しない。U04はサーバ寿命に固定したAuthorization値を使い、切断後は再利用しない。入力世代との対応はU05で設計 |

## U04 M2の製品経路と実行結果（2026-11-12）

**実装済みの経路**: Python MCP SDK 2.3.0の`MCPServer.streamable_http_app(json_response=True, stateless_http=True)`を、既存CodexServiceThreadのasyncioループでホストする。初回送信時に記録先とサーバを準備し、127.0.0.1のOS割当ポート・接続ごとのMCP名・不透明なBearer値を`thread/start.config`へ渡す。アプリ専有サーバだけを有効にし、`enabled_tools=["observe"]`、`required=true`、起動期限10秒・ツール期限30秒を指定する。ハンドラは観測サービスを借用し、readerとwriterはアプリの観測・保存側が所有する。加工・符号化済みObservationを同じ画像のMCP応答と非同期保存へ渡し、設定ファイル書換え・token履歴表・別のstdio受信者を作らない。`read-only`・`on-request`・`user`を維持した永続threadを使い、記録先と実thread IDの対応保存後にターンを開始する。

`observe`は`destructiveHint=false`・`openWorldHint=false`を公開する。初回の実モデル試験では、この注釈がないツール呼出しが`user rejected MCP tool call`で失敗した。公開`mcpServer/tool/call`だけでは成功しており、直接呼出しをモデル経由の承認経路の証明にできなかった。採用tagの承認判定と実際のツール作用に合わせて非破壊・閉じた対象であることを明示し、再試験でモデル経由の観測に成功した。原記録の追加があるためreadOnlyHintをtrueとせず、承認方針全体を無効化して回避していない。

**実モデルで確認済み**: Windows、採用runtime 0.167.0、既存ChatGPT認証、`gpt-6.1-sol`・low、製品サービスの既定factoryを使った。プロンプトに正解を含めない4K固定FrameをFullHDへ縮小し、3,796,983 bytesのPNGを返した。ランダム英数字・四隅の色・観測IDの回答、保存PNGとの一致、通常終了でのサービス回収を確認した。同接続の次ターンでは観測を再実行せず、保存先のPNGを`imageView`で開き直して英数字・IDを回答した。新しいapp-serverプロセスの公開`thread/read(includeTurns=true)`で、両ターンと画像参照を取得できた。アプリの会話再開UIや再起動後の攻略復帰の確認ではない。

通知は1,049,142 bytesとなり、旧1 MiB受信枠を超えたが現行の2 MiB枠で受信できた。送信は1 MiBを維持する。通知の結果は切り詰めtextであり、後日の`thread/read`でも画像全体を復元する原典には使わない。保存は通知のbase64復元ではなく、MCPへ渡す同じObservationのPNGから行う。モデル内部の画像前処理まで保存PNGとの同一性を保証しない。

MCPのBearerはモデル向け指示やアプリ原記録へ保存しない。実モデル試験で作ったrollout 1件とアプリ生成ファイル7件を完全bytes・JSONエスケープ形で調べ、一致0件を確認した。既定HOME全体や設定DB・認証ファイルは読んでおらず、そこでの残存なしを保証する結果ではない。隔離HOMEの非モデル試験による設定残存確認は別の根拠とする。

**実機・実画面で確認済み**: 保存済みの機器識別子と再列挙したUSB Video Deviceの一致を確かめ、FullHD・60 fps設定の実映像を未選択チャットから観測した。実モデルの画面説明・タイトル文字・観測ID、MCP応答と保存PNG・付随情報の一致を確認した。実保存中のプレビュー描画、回答生成中のメニュー表示、映像取得中の設定開閉、Codex切断後の取得継続、通常終了と別writerでの再読込も確認した。ネイティブ画面での新規作成・読込、前回選択の再起動後復元、観測解像度HDの適用・保存は別の試験で確認済みである。他機器・長時間運転、R02/R04の障害条件を実runtimeで発生させる試験は未実施であり、後続工程の権限更新・会話再開まで確認した結果ではない。全体の工程受入と計測条件・コマンド・根拠は[M2子仕様](../complete/unit_035/MCP_OBSERVATION_INTEGRATION.md)に記録する。

依存導入は新規の検証用環境で`uv sync --dev`に成功し、同環境の全unit/integration試験465件が成功した。利用中の既存`.venv`がロックされ再同期できない件とは分ける。

## U05 M0の操作権更新・模擬入力の実接続（2026-11-12）

[M0子仕様](../complete/unit_036/INPUT_AND_STOP_DESIGN.md)で採用runtime 0.167.0とMCP 2.3.0を使い、公開thread/unsubscribe後の同ID thread/resumeで、既存2ターンを保持して新MCPの検出・呼出しが成立することを確認した。loaded状態のresumeだけでは設定は更新されない。旧server名・旧Bearer・旧Grantで新runへ入力できないことも確認した。製品の操作取消しや実機入力の検証ではない。

既存ChatGPT認証、gpt-6.1-sol/lowの実モデルでも、同threadの2ターンで模擬act_and_observeが各1回成功した。副作用注釈（readOnly=false/destructive=true/idempotent=false/openWorld=true）と当該toolのapproval_mode=approveを使い、全体のon-request/userを維持した。指定A100ms・neutralの模擬記録、64×64 PNG・観測ID、runtimeとHTTPの正常終了を確認した。実機書込はない。

停止・通常turn終端でGrantを失効し、次の明示送信では新Grantへ替える。MCP handlerはendpointに固定したGrantを渡し、入力サービスの要求受付で判定する。resume失敗時は送信失敗とし、新会話へ自動置換しない。新configには新serverの完全設定を渡し、旧一時serverの無効化設定だけを残さない。

根拠は `tmp/u05-mcp-generation-probe.py` / `tmp/u05-mcp-generation-bljcvlpg/results.json` と `tmp/u05-mcp-model-probe.py` / `tmp/u05-mcp-model-n0rrwo9a/results.json`。公開schemaと[採用tagの設定](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/config.schema.json)、[承認処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/mcp_tool_call.rs)、[公式資料](https://learn.chatgpt.com/docs/app-server)も照合した。製品の結合・実機受入はM2で扱い、通常会話の保持をU06の保持Goal再開の受入へ拡張しない。

## U05 M2の公開方式と要求期限

採用runtime 0.167.0の[期限解決](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/codex-mcp/src/connection_manager.rs)では、`tool_timeout_sec`省略時も300秒の既定期限を使います。設定で変更できる値であり、300秒が固定の最大値ではありません。0は期限無効化ではなくゼロ秒です。[要求実行の経過時間制限](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/rmcp-client/src/rmcp_client.rs)はprogress通知でも延長されません。これは採用版ソースの確認結果です。

[U05 M2](../complete/unit_042/INPUT_APPLICATION_INTEGRATION.md)は同期MCPを採用し、Codexのtool_timeout_secとGigasのact_and_observeの待機期限を300秒に設定します。数秒〜1分中心の用途に対する設計判断であり、MCP共通の最大値ではありません。[U05 M1](../complete/unit_038/INPUT_EXECUTION_AND_OBSERVATION.md)の内部APIには入力時間・要素数・合計時間の固定上限を設けません。dynamic tool、非同期タスク受付・ポーリング、入力の暗黙分割は採用しません。

GameIoMcpServer(observations, inputs, protocol)がobserveとact_and_observeを公開します。prepare_turn(prompt, thread_id)を通常送信ごとに呼び、適用中プロトコルを説明へ反映した新endpointとrunを対応付けます。初回はthread/start、次回からはthread/unsubscribe→同ID thread/resumeで設定を読み込ませます。bind_grantは各endpointへ一度だけ行い、停止・turn終端後の旧要求は入力不可とします。

要求の期限・取消しは、入力実行中なら共通停止と開始済みwrite後の解除、観測中なら当該応答の取消しに分けます。観測中の取消しは次の入力や操作権を失効させません。開始済み符号化・保存は独立して回収します。GUI停止・run終了は両方を止めます。クライアントとサーバーは別々に計時し、期限内の結果配送・物理解除を保証しません。結果不明の列は自動再送しません。HTTP切断やcancel通知の到達を停止条件にせず、アプリの入力所有者へ停止を届けます。

**模擬機器で確認済み**: 採用実app-serverとlocalhostのResponses mock、製品MCP・入力・観測・保存を通し、300秒設定で65秒wait完了、途中停止、同threadの3DS能力再読込とtouchを確認しました。試験プロセス内だけGigas側0.25秒・Codex側1秒へ短縮し、TOOL_TIMEOUTとneutralを確認しました。300秒経過の試験ではありません。根拠は`tmp/mcp-input-investigation/RESULT.md`と`complete-runtime-probe/report.json`です。

**製品GUI・実モデル・実機で確認済み**: Windows、USB Video Device、COM8・CH552・9600 baud、採用0.167.0と既存ChatGPT認証、gpt-6.1-sol/lowで3runを実行しました。短い左右入力、設定モーダルからのB長押し停止（interrupted/neutral sent）、左移動後に止まる映像、同thread・ID・保存PNG、正常終了を確認しました。詳細は[U05 M2](../complete/unit_042/INPUT_APPLICATION_INTEGRATION.md)に置きます。B解除ACK、writeの最悪時間、DTR/RTS reset、PokeCon・3DS実機、他OS、長時間連続運転、300秒経過は未検証で、[U10](../planned/unit_015/CONTINUOUS_OPERATION.md)へ引き継ぎます。

## U06 M0のGoal・途中介入・次ターン設定（2026-11-13）

[M0子仕様](../complete/unit_044/GOAL_INTERACTION_DESIGN.md)で、同梱0.167.0の公開schema、採用tag `086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`、実app-serverを照合した。調査用コードとQt試作の確認であり、製品にGoal・共通Message・Planを実装した結果ではない。

**実モデルで確認済み**: 既存ChatGPT認証を隔離した一時HOMEで使い、公開stdio APIを実行した。`thread/goal/set(objective,status=active,origin=user)`だけで初回ターンが始まり、ツール呼出しを含むターン完了後に次ターンが自律開始した。`turn/start`の重複送信やアプリの継続ループは不要である。`status=paused`と`clear`はGoalを変更するが、実行中ターンは`inProgress`のままだった。別途`turn/interrupt`して`interrupted`を受ける。目的を省略した`status=active`で同じGoalの利用量と作成時刻を保って再開できた。pausedのまま通常`turn/start`を送って応答を得てもGoalはpausedを維持した。

既存Goalの目的変更も利用量・作成時刻を保持した。[採用版Goal API](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/ext/goal/src/api.rs#L207)は既存Goalを更新し、Goalがない場合に作成する。[現行公式App Server文書](https://learn.chatgpt.com/docs/app-server#manage-a-thread-goal)の「異なるobjectiveで利用量をリセット」という説明とは一致しないため、採用版の実測を契約にする。目的変更時の現在ターンへの指示投入は[採用版runtime](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/ext/goal/src/runtime.rs#L222)で確認した。公開応答の現在Goalを表示し、同じ文章を別のstart/steerへ重複配送しない。

`thread/settings/update`で実行中にモデル・effortを変更でき、`thread/settings/updated.threadSettings`で次ターン設定を受ける。実モデルではGoalのsol/low実行中にterra/mediumを設定し、次の自律ターンへ適用された。通常ターンのterra/medium実行中にsol/lowを設定した場合も次の通常ターンへ適用された。同一thread・同一接続の4ターンで確認し、runtimeの調査用rolloutの`turn_context`と照合した。rollout内部形式へ製品を依存させない。`turn/started`はモデル・effortを返さないため、現在ターンの設定と確定した次ターン設定を区別する。この設定APIと通知は[公開experimental](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server-protocol/src/protocol/common.rs#L713)であり、`initialize.capabilities.experimentalApi=true`が必要。現在ターンを変更する別APIの`turn/settings/update`は採用しない。

**実runtime・模擬モデル/機器で確認済み**: 同じrun/GrantのままGoalが二つの自律ターンから製品MCPを呼べた。停止でneutralと旧Grant拒否を確認し、pausedの同threadをunload/resumeして新しい3DS能力のendpointを公開し、Goalを再開できた。U05のMCP一呼出し300秒とGoal全体の期間は別である。自律ターンごとのrun再作成は行わず、明示停止・Goal終端で入力を閉じる。設定提出後の適用処理は中断と独立して回収する。

同じ模擬モデル経路で、質問保留中の`turn/steer`、複数question IDへの一括回答、回答に含めた追加指示と別steerの一回ずつの配送、終了後steerの`-32600`拒否を確認した。`tools.update_plan.enabled=true`で2項目と説明の`turn/plan/updated`を受信した。これは採用版で既定falseの[Planツール設定](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/tools/spec_plan.rs#L1301)を明示する必要がある。自然文を質問回答と追加指示へ自動分類せず、質問IDと送信対象を選ぶ。承認は要求IDに対応する明示操作だけで返す。

M1で秘密入力の採用を撤回した。採用版の[protocol定義](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/protocol/src/request_user_input.rs#L15)には既定falseの`isSecret`があるが、[モデル向け質問ツール定義](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/tools/handlers/request_user_input_spec.rs#L39)では公開されない。GIGASの用途と実モデルからの受信証拠はなく、専用UIを維持しない。1問でもtrueなら既存の未対応エラーで要求全体を拒否する。[app-serverの応答処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/bespoke_event_handling.rs#L1707)はそのエラーを空回答へ変換するため、拒否をturnの中断と同一視しない。以上は固定版ソースの確認と製品decoderの設計であり、実モデルから秘密質問を受信して拒否した実測ではない。

設定適用の境界は実runtimeとローカルResponses mockの送信JSONでも確認した。ツール保留中に設定を変えても、同じターンのツール結果後の推論は旧モデルを使い、次の自律ターンから新モデル・effortを使った。実モデルの開始時記録だけを根拠に同ターン内の適用時点まで断定していない。

実行根拠は`tmp/u06/runtime-goal/live-20261113-021248/report.json`（Goal）、`live-20261113-021441/report.json`（設定）、`wire-20261113-022053/report.json`（設定適用境界）、`live-20261113-021808/report.json`（paused中の通常送信）、`tmp/u06/mcp-20261113-021320/report.json`（操作権）、`tmp/u06/interaction-20261113-021655/report.json`（質問・steer・Plan）。短い実モデル試験では公開reasoning要約を受信しておらず、全認証・モデルでの提供を保証しない。このM0結果に続くM1では製品の表示結合を確認した。M2でGoalと質問/承認の競合を製品の固定通知試験で確認し、M3の実モデル・実機の一連操作は後続確認とする。300秒経過、長時間継続、APIキー課金経路、制限の実発生はこの結果に含まない。

M1の製品結合: [子仕様](../complete/unit_045/COMMON_DIALOGUE_UI.md)で共通入力・公開表示・配送結果を実装し、固定stdio peer経由の質問/steer/承認/終端を確認した。既存ChatGPT認証と実モデル`gpt-6.1-sol`・lowでは、製品ApplicationShellから通常turnを一度開始し、2回のPlan更新、応答の表示、下書き消去、終端とサービス終了を確認した。公開summaryは未受信。Goal制御・実機・新規認証の証拠には使わない。

# 1. この文書の位置づけ

**公開app-server v2プロトコルへの単一接続を採用します。** Codex固有処理と通信管理は一つのCodex接続実装へ集約します。SDKの内部クラスや非公開メソッドへの依存、非公開ハンドラの差し替え、同じ通信をSDKと独自処理が二重に受信する構成は採りません。runtimeは0.167.0へ固定しています。通常ターンはU02、観測はU04、入力・停止はU05で製品確認済みです。GoalはU06 M2で製品へ結合しました。実モデル・実機の一連操作と認証拡張はM3へ残ります。

通信の管理主体は一つのCodex接続部に保ちます。asyncio subprocessのreaderで受信を振り分け、回答待ちはreaderの外で扱います。SDK改修・非公開API差替え・二重受信は行いません。以下のSDK調査記述は不採用判断の根拠であり、製品方式の現行契約ではありません。

次の三つは別の根拠として扱います。

| 根拠 | 確認できること | 確認できないこと |
| --- | --- | --- |
| 公式文書 | 提供方針、公開 API、接続方式 | 採用する版の不具合や個別環境での動作 |
| GitHub の確認時点のソース | そのコミットの具体的な実装 | 公開済みパッケージへの収録、安定 API としての保証 |
| 採用版での実行 | 固定したランタイム・接続実装・Windows の組み合わせの動作 | 未検証の版や環境の動作 |

初回のSDK調査対象は `main` のコミット `27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26` でした。この時点のPythonパッケージ定義は `0.0.0-dev` でした。製品の採用版はこの開発版ではなく、冒頭に記載した配布runtime 0.167.0です。

# 2. プロセスと接続

初回調査のPython SDKは同期・非同期APIを備え、別プロセスの `codex app-server --listen stdio://` と行単位JSONで通信していました。非同期クライアントも内部では同期クライアントをスレッドで呼び出しており、要求保留中の受信停止がU02での不採用理由となりました。製品は固定runtimeを直接起動し、一つの非同期stdio受信者で要求と通知を振り分けます。

ゲームMCPのStreamable HTTPは別の接続です。ゲームI/OとMCPサーバはアプリ内に置き、app-serverがHTTPクライアントとして接続します。URL・認証ヘッダー・期限は公開configで渡し、U05では一呼出し300秒に統一しました。入力APIへ固定の合計時間上限を追加する契約ではなく、Goal全体の期間とも別です。

操作権はMCP endpointへ固定したGrantに対応付け、停止後の旧要求を拒否します。U05で `thread/unsubscribe` → 同IDの `thread/resume` による新MCP設定の公開を確認しました。U06 M0では同じ経路を保持Goalの再開にも限定確認しました。Goalの自律turn間は同じrun/Grantを保持し、毎turnの再接続を行いません。

2026-11-10の資料調査時点では、Windowsのネイティブ実行に関する公式案内だけで、本構成の子プロセス起動、ローカルHTTP接続、プロジェクトへの書き込み、GUIとの並行動作を確認済みとはしませんでした。その後のU02・U04で確認した製品接続・HTTP・保存・ネイティブUIとUSB Video Deviceの実映像との結合は、本書冒頭の実行結果に記載しています。この構成での実績を他機器・長時間運転の確認へ広げません。

# 3. 通常ターンと Goal の継続

採用する公開app-serverにはスレッドの作成・再開、ターン開始、通知、実行中ターンへの `steer`、`interrupt` があります。スレッドはコンテキストを継続する単位であり、ゲームの進行状況やセーブデータそのものではありません。

Goalは `thread/goal/set`・`get`・`clear` と更新通知で扱います。U06 M0で開始・自律継続・休止・保持再開・破棄を実モデル確認しました。初回調査のPython SDKにもGoal用の低水準処理は存在しましたが、製品ではその内部処理を利用しません。

U06でCodex接続部へ結合するのは、通常ターンの通知に加えて複数ターンにまたがるGoal更新です。`turn/completed`を依頼全体の達成とみなさず、公開Goal状態、実行中ターン、利用者の停止意思を別々に保持します。

Goal の継続は実行状態、予算、保留入力などの条件を受けます。Goal が active であれば無条件に永久実行するという保証はありません。公式 Cookbook はツール呼び出しを含まない継続ターンで、自動継続が抑制される条件も説明しています。アプリ側で独立した無限再投入ループを重ねません。

Goal開始前に永続thread・通知購読・操作権を用意します。採用版では既存Goalの目的更新も累積利用量を保持します。別の新規依頼は旧Goalをclearしてから作成し、保持再開はobjectiveを省略します。即時停止では先にゲーム入力を閉じ、その後Goalの一時停止または破棄とターン中断を行います。pause単独では実行中ターンもコントローラー押下も止まりません。

# 4. 介入・表示・利用者への要求

公開プロトコルには `turn/steer`、`turn/interrupt`、`turn/plan/updated` があります。計画通知はエージェントが公開した手順の更新として UI に表示できますが、思考の全内容や常時最新の中期計画を保証するものではありません。アプリは自身でゲーム攻略計画を生成しません。

質問は `item/tool/requestUserInput` の要求ID・question IDを保持して画面へ渡します。質問、コマンド承認、ファイル承認、MCPの追加情報要求は別の要求です。U02で質問・承認の保留と応答、U06 M0で複数質問と途中steerを確認しました。中断・解決通知で失効した要求へ後から回答を送信しません。

初回SDK調査では、標準承認ハンドラがacceptを返す実装と、唯一の受信スレッド内でハンドラを同期実行する構造を確認しました。回答保留で後続受信が止まる問題はU02の模擬peerで再現し、SDK不採用の根拠としました。

製品のCodex接続部は一つの公開app-server接続で受信を継続し、回答待ちを受信処理の外で扱います。要求IDに対応する有効な回答だけを返し、承認はGUIの明示操作で送ります。通常ターンの保留・中断・失効はU02、ゲーム入力の独立した停止はU05で確認済みです。共通Messageへの結合はU06 M1の固定stdio往復で確認し、Goalとの競合はM2の固定通知試験で確認し、実モデル受入はM3へ残します。別受信経路やSDK内部handlerの差し替えは追加しません。

# 5. 認証・モデル・利用制限

公開app-serverの `account/login/start`・`account/read`・`model/list` を使います。既存ChatGPT認証とモデル/effort一覧は実行確認済みで、新規ChatGPTログインの受入結果はU06 M3で区別して記録します。APIキー認証は2026-11-13の合意で今回対象外です。認証とモデル妥当性を分離し、特定モデル名やeffortの全種類を固定しません。

app-server の利用枠情報は ChatGPT 利用枠が対象です。取得できた使用率や回復予定時刻を表示します。API キー利用時の課金残高やクレジット上限を、同じ通知から必ず取得できるとはしません。

利用制限や課金関連エラーは、得られたエラーと利用枠情報を添えて停止理由にします。ランタイム自身が再試行中の通知と、最終的な実行失敗は分けます。初期版の再開は利用者の明示操作に限り、回復時刻到来だけで入力を再開しません。

# 6. 継続制御への委任と制約

計画的終了の旧案は元Goalを休止し、同じスレッドの通常ターンでプレイ終了処理を行う方式でした。[10のUI合意](10-ui-interaction.md)では追加指示を入口とし、共通の追加指示・公開通知・停止で成立するかをU07 M0で確認します。専用ターン切替は採用せず、未成立時は必要な設計差分を記録します。通常攻略ではCodexの判断と公開状態を正本とし、アプリ側で実行中の状態整合性検査・停滞推定・自動修復を行いません。以下の調査結果はCodexへ任せる範囲と制約の根拠であり、外側に同等の監視・復旧機構を実装する要求ではありません。処理フローは[09](09-control-flows.md)を参照します。

初回の継続制御調査ではコミット`0811df39768427445b9730c039d6c04020670e04`を読みました。以下の旧ソース参照は当時の判断根拠です。現在の採用版ソース・実行結果は冒頭のU06 M0を参照し、旧コミットを採用版の動作証明には使いません。

| 確認対象 | 確認した根拠 | 採用方針への含意 |
| --- | --- | --- |
| 一時的エラー | [app-server文書](https://learn.chatgpt.com/docs/app-server)には内部再試行と最終失敗を扱う通知があります | 再試行はCodexへ任せ、アプリは受け取った通知を表示します。別の開始や操作再送を重ねません |
| ターン間継続 | [Goals文書](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)はイベント駆動の継続と抑制条件を説明しています | activeと実ターン進行を同一視せず、無操作・無通知から異常を推定しません |
| ランタイムの継続処理 | [extension.rs](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/codex-rs/ext/goal/src/extension.rs)のidleイベントから継続処理へ進み、[runtime.rs](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/codex-rs/ext/goal/src/runtime.rs)は保留・ライブスレッド・Goal状態・開始受付を確認します | この内部処理をアプリで再実装しません。Codexが解消できない停滞はユーザーが停止・再開します |
| 最終的なターンエラー | `on_turn_error`とruntime側にはusage_limitedやblockedへ移る経路があります | 通知を理由として停止します。アプリから自動でactiveへ戻しません |
| Goal更新と開始の差 | [api.rs](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/codex-rs/ext/goal/src/api.rs)では状態保存とランタイムへの反映が分かれています | ユーザー操作への受付結果と実際の開始通知を区別して表示します。開始通知が来ないことを補う自動診断・再設定は行いません |
| 継続保留 | [goals.rs](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/codex-rs/state/src/runtime/goals.rs)には保存済みの継続保留があります | 内部DBを編集したり、状態を再設定し続けたりして保留を解除しません |
| 接続の実体 | U02の[製品transport](../../src/gigas/runtime/codex/transport.py)がローカルapp-serverと一つのstdio接続を所有します | 明示的な通信故障では入力を止めます。自動再接続・再起動して攻略を復帰させる機構は設けません |

#

## 明示的な操作に必要な状態取得

`thread/goal/get`や`thread/read`などの状態取得は、開始・明示的再開・計画的終了の手順で必要なときに利用します。通常実行中の表示は公開通知に従い、整合性検査のための定期取得や、通知の取りこぼしを推定して状態を再構築する機構は作りません。

`thread/resume`はスレッドを読み込む操作であり、生存確認としては使いません。同一セッションの明示再開では、先にunsubscribeして新MCP設定を読み込ませます。読込だけで入力を許可せず、保持Goalと開始条件を確認します。質問・承認は実際の要求とIDを扱い、欠けた本文を推測しません。受付・応答・失効は一つのCodex接続実装が公開app-serverプロトコルで扱います。

### ユーザーの明示的再開と、その限界

同じGoalの明示再開は、`objective`を送らず `thread/goal/set(status=active, origin=user)` を使います。目的・予算・累積利用量を保つことはU06 M0で確認済みです。受付と実際の開始は受け取った通知どおりに扱い、開始が起きなくても自動再設定しません。通信失敗で結果不明なら成功扱いや自動再送をせず、入力を閉じてエラーを表示します。

ユーザーが停止・アプリ再起動・再開を行う場合も、旧入力の失効と旧実行の終了確認は必要です。Python側の待機取消しだけで、ワーカーや実機入力まで終了したとは扱いません。受信者と子プロセス終了はU02、入力write後の解除はU05の所有契約を使い、Goalとの結合をU06 M2/M3で確認します。アプリ再起動後の復旧はU08です。

2026-11-10の調査記録では、[Issue #58421](https://github.com/openai/codex/issues/58421)に、0.163.0のLinuxで中断後のキュー入力がidleのまま起動しないという利用者報告があります。調査時点のduplicateによるclosedは、修正済みを意味しません。本構成・Windowsでの再現事実とは区別し、この報告を根拠に無条件の`turn/start`による復旧を採用しません。

### 本実装での確認条件

| 条件 | 確認する結果 |
| --- | --- |
| 内部再試行・長い推論・質問待ち・通常ターン間待機 | 外側が新しいターンを重ねません |
| 無通知、またはactiveかつ実行中ターンなし | 最後の公開状態と時刻を記録し、表示は10に従います。時間経過から診断・状態取得・自動停止・再投入を始めません |
| 明示的な最終失敗・通信切断 | 入力を閉じてエラーを表示します。自動再接続・自動攻略復帰を行いません |
| 追加指示による計画的終了 | 共通配送・公開応答・Goal休止・入力と旧実行の収束で確認します。専用ターン・権限・結果スキーマを前提にしません |
| ユーザーの停止・再開・アプリ再起動 | 旧世代を拒否し、現在画面と記憶を渡します。同一セッションの保持Goal再開はU06、保存参照・再起動・新規スレッドでの復帰はU08。破棄Goalを復活させません |
| 再開要求の重複や通信失敗 | 重複送信・過去の入力再送をしません。確認できない成功を捏造しません |

# 7. 採用版の確認結果と後続の検証

U02で0.167.0と公開app-server直接接続を採用し、U04/U05の製品確認、U06 M0の限定確認まで進みました。次表は現在の結果と残る検証を区別します。過去のソース調査だけで採用版・製品の成立を保証しません。

| 対象 | 確認対象 | 現在の結果と残る検証 |
| --- | --- | --- |
| 起動・版・通常ターン | Windows配布runtime、公開API、受信と子プロセス終了の所有者 | U02で版を固定し、通常ターンの開始・応答・中断・終了をWindowsで確認済み |
| 質問・承認 | 公開経路での保留・回答・失効 | U02で通常turnの受信・停止・失効を確認済み。共通MessageはU06 M1、Goalとの競合はM2の固定通知で確認、実モデル受入はM3 |
| 認証・モデル | ChatGPTの経路、資格情報の保管責任、利用可能モデルと設定値の検証 | U02はChatGPTで最小接続を確認。新規ChatGPT認証をU06で確認。APIキーはM3対象外。設定文字列の保存で代用しない |
| MCPと画像 | U02で設定経路を調査し、U04冒頭でサーバと画像応答形式を決定 | U04 M2で固定Frame・USB Video Deviceの実映像の実モデル受信、保存画像・観測IDとの対応と実画面の応答性を確認。固定Frameの同接続で次ターンの保存PNG参照、再起動後の会話読込も確認 |
| MCP接続と実行世代 | 新endpoint/Grantと同threadの設定再読込 | U05の製品で停止後の旧要求拒否を確認済み。Goalの自律turnと同run保持・新MCPでの再開はU06 M0の模擬条件で確認、製品結合はM2で確認、実機受入はM3 |
| Goalの継続 | 複数ターン、目的更新、pause/resume/clearとinterrupt | U06 M0で実モデルの限定確認済み。Goal達成と質問/承認・停止の競合はM2固定試験、実runtime製品結合もM2確認。実モデル/実機はM3 |
| 追加指示・計画・制限 | steer、公開Plan、利用枠、終了との競合 | U06 M0でsteer・Planの実runtime配送、ChatGPT利用枠を確認。共通表示はM1、Goal競合はM2、認証/制限の製品結合はM3。受付不明を再送しない |
| 追加指示による終了作業と休止 | U07 M0で共通配送から終了作業・休止・公開結果を確認 | 必要な差分の実装前。元Goalを保持し、作業報告とアプリが確認できた停止・結果を分ける。専用切替が必要とは仮定しない |
| 同じGoalと新規スレッドでの再開 | 保持Goalと保存参照・再起動後の復帰を区別 | 同一セッションはU06 M0で限定確認済み、M2で製品結合確認、実機確認はM3。保存スレッド読込・新規再構成はU08 M0。再開後の初回observeはモデルへの指示でありAPI保証ではない |
| 整理スレッドと書き込み終了 | U02で実行終了の観測方法を調べ、U09冒頭で整理の権限・終了・ファイル更新の扱いを確認 | U09の復元実装へ進む前。非同期待機の取消しだけを終了とみなさず、復元を開始できる条件を決定 |

「公開経路の調査」「採用版による限定的な接続確認」「製品フロー全体の検証」は別々に記録します。後続の確認で必要経路が成立しなければ、依存する実装へ進む前に制約と選択肢を示します。公開APIの不足を独自の攻略継続ループや暗黙再接続で補わず、必要なら別の設計変更として扱います。

確認結果の詳細は各子仕様、確認済みの版と残る未確認事項は本書へ反映します。工程の依存関係と親仕様への入口は[05](05-implementation.md)を参照します。

<a id="ui-api-open"></a>

## UI合意に対応する確認済み範囲と未確認事項（2026-11-13）

画面と操作の採用要求は[10-ui-interaction.md](10-ui-interaction.md)、M0の決定と実行条件は[子仕様](../complete/unit_044/GOAL_INTERACTION_DESIGN.md)に集約します。公開schema、模擬モデルを使った実runtime、実モデル、Qt試作、製品結合の確認範囲を区別します。

**確認済みの形式**: 同梱`codex-cli 0.167.0`の`app-server generate-json-schema`から、`TurnPlanUpdatedNotification`の必須項目が`threadId`・`turnId`・`plan`であること、`explanation`が省略可能かつstring/nullであることを確認しました。各計画項目は`step`と`status`を持ち、状態は`pending`・`inProgress`・`completed`です。項目IDはありません。[公式app-server資料](https://learn.chatgpt.com/docs/app-server#turn-events)でもエージェントが計画を公開・変更する通知として案内されています。形式の確認は、Goal継続中の通知頻度・順序・再開後の取得を実行確認した意味ではありません。

**現在の製品との隔たり**: 接続部は通常ターン用であり、GUIは質問カードと通常入力を分けています。回答検証は質問IDごとの値を要求し、選択式で自由回答不可の要求も扱います。通常送信の有効化だけでは共通入力や実行中の介入は成立しません。plan・reasoningの表示、Goal操作を既存GUIが実装済みとは扱いません。

**pause／clear／interrupt**: 0.167.0のschemaと上記実モデル試験で、それぞれGoal保持、Goal除去、ターン中断の別操作であることを確認しました。明示操作は`origin=user`で送り、Gigasの一時停止・破棄は先に入力を遮断・解除してからGoal変更と実行中ターンの中断を行います。Codexデスクトップの各ボタンの内部実装を確認した意味ではありません。

| ID | M0の確認・決定と残る検証 | 担当・判断期限 |
| --- | --- | --- |
| UI-A1 | 実モデルでGoal開始・自律継続・目的更新・pause・resume・clear・interruptとpaused中通常送信を確認。旧Grant拒否と新MCP公開は実runtime/模擬機器で確認。再開前は最新cacheの利用可否を確認し、再開後まずobserveする指示を渡す。Goal setに画像入力欄はない | M2で製品配線・要求失効と、固定Responsesを使う実runtimeの同thread再開・新MCP経由入力/観測を確認。実モデルによる初回observe遵守と短い実機操作はM3。保存参照からの復帰はU08 |
| UI-A2 | 実runtimeで保留質問中steer、複数質問への回答、終了後steer拒否を確認。履歴内の要求カードで回答対象を明示し、全question IDの回答を揃えて一括返答する。選択限定回答へ自由文を混ぜず、追加指示はMessageから別送信。普通の文章を承認へ変換しない | M1で既存回答・配送・下書き試験を改訂しQtへ結合。承認待ち・Goal終了との競合はM2の固定試験で確認、実モデルはM3 |
| UI-A3 | `tools.update_plan.enabled=true`で実runtimeの2項目・説明通知を確認。thread/turn単位で最新配列を置換し、空配列は空表示とする。ターン切替で前計画を現在欄へ残さず、履歴は保持する。Goal全体のTODOと同一視しない | M1で遅延通知・空配列と表示、M2でGoalターン切替を確認。再接続後の再構成はU08 |
| UI-A4 | 公開要約schemaは確認。短い実モデル試験では未受信。受け取ったsummaryだけを設定で表示し、未提供の要約を生成しない。生の内部推論は取得・表示しない | M1で固定通知の断片・完了・非表示を既存表示試験へ統合、M3で採用モデルの提供範囲を記録 |
| UI-A5 | U04/U05とM0の実runtime MCP通知を使い、thread/turn/item IDに対応する実ツール名・開始/完了/失敗を履歴へ出す。ツールの待機をGoal停止や達成と同一視せず、固定の攻略動作・推測回数を生成しない | M1の実通知表示、M2でGoal/停止に結合、実モデル/実機受入はM3 |
| UI-A6 | 追加指示を受けたエージェントの区切り・必要なセーブ・メモ更新・Goal休止と公開結果。共通の配送・通知・停止で何を確認できるか。作業中の質問・承認とpause／stop、同一セッション再開を確認。未成立なら制約と必要な設計差分を記録 | U07 M0、必要差分の実装前。専用終了ターン・権限・結果スキーマ・自然文分類を前提にしない |

これらを満たす公開経路が未成立なら、制約と代替操作案を担当M0で提示し、合意前に別入力欄、独自の文章分類、回答全文の複数質問への複製、アプリ独自の攻略継続ループを追加しません。PySide6の表示・選択・履歴量はAPIの未確認事項と分け、10とU06のGUI確認で扱います。

# 8. 参照先

公開文書:

- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)

- [App-server protocol](https://learn.chatgpt.com/docs/app-server)

- [Using Goals in Codex](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)

- [Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox)

- [MCP configuration](https://learn.chatgpt.com/docs/extend/mcp)

コミットを固定した実装参照:

- [Python API reference](https://github.com/openai/codex/blob/27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26/sdk/python/docs/api-reference.md)

- [同期クライアント・承認・Goal](https://github.com/openai/codex/blob/27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26/sdk/python/src/openai_codex/client.py)

- [非同期クライアント](https://github.com/openai/codex/blob/27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26/sdk/python/src/openai_codex/async_client.py)

- [高水準 API](https://github.com/openai/codex/blob/27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26/sdk/python/src/openai_codex/api.py)

- [Goal の通知管理](https://github.com/openai/codex/blob/27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26/sdk/python/src/openai_codex/_goal.py)

- [承認モードの変換](https://github.com/openai/codex/blob/27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26/sdk/python/src/openai_codex/_approval_mode.py)

- [Python パッケージ定義](https://github.com/openai/codex/blob/27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26/sdk/python/pyproject.toml)

## U04 M2前の追加調査: 永続チャットと完全出力

2026-11-12の[仕様改訂](../complete/unit_034/CHAT_OBSERVATION_PERSISTENCE.md)で、未選択チャットの観測・記録とephemeral撤去を採用方針にした。以下は改訂時点の調査と確認計画であり、その時点では製品実装・本MCPの実接続は未実施だった。現行実装と実モデルの観測・保存PNG後参照・再起動後のthread読込は、本書冒頭のU04 M2実行結果を参照する。

| 根拠・事項 | 確認済みと未確認 |
|---|---|
| 同梱codex-cli 0.167.0のgenerate-json-schema --experimental | ThreadStartParamsにephemeral/cwd/config、ThreadResumeParamsにthreadId/config/cwd、ThreadReadParamsにthreadId/includeTurnsがある。隔離CODEX_HOMEで生成成功。認証・実ターンなし |
| [公式App Server](https://learn.chatgpt.com/docs/app-server) | 保存会話をread/resumeする公開経路を確認。現行docsの説明を採用版の保存・終了後の読込成功と同一視しない |
| 永続化の副作用 | 会話・指示・ツール結果の保存量、flush時点、MCP configと短命Bearerの残存はM2実接続前に隔離環境で確認。既存認証のHOMEをチャットごとに分割しない |
| 完全出力 | SDKへ包装する前の加工済みObservationをアプリの保存処理へ渡す。MCP側で再加工せず同じPNG/ID/時刻となることをM2で確認。middlewareは前提にしない。Codex通知の切詰め結果を原画像の正本にしない |
| 保存完了を待たない応答 | 本MCPで保存を止めても画像応答が成立し、後続保存が同一であることをM2で確認。応答成功は保存・配送先での解釈成功を保証しない |
| 再接続・再開 | 永続化と同一thread再開は別。新しい認証値の適用と旧値失効はU05/U08の入力公開・再開前に確認する |

未成立なら依存する結合方式を見直し、認証値の秘密保持や再開成功を推測で確定しない。

## U06 M2の製品結合

[unit_049](../complete/unit_049/GOAL_MEMORY_INTEGRATION.md)で採用runtime0.167.0と製品サービスを接続し、loopback Responses・固定認証/catalog・合成映像/模擬入力で2自律turnの同run保持、pause/neutral、同thread再開の新MCP/新run、再開後入力、clearと記録を確認した。Goal中の推論レベル変更は現turnの後続推論を変えず、次自律turnへ反映した。M0の隔離APIだけの結果とは区別する。

追加の実runtime検証で、thread内の同期`exec_command`によるmemory書込が同turnの公開interrupted後も継続した。公開`thread/backgroundTerminals/list`・`terminate`を停止経路へ結合し、最終`tmp/u06-m2-runtime/memory-product-20261113-170826/report.json`では製品停止後1秒と同Goal再開後も3 bytes不変、公開一覧が空であること、サービスと検証プロセスの終了を確認した。[採用版のAPI実装](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/app-server/src/request_processors/thread_processor.rs)と[端末終了実装](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/core/src/unified_exec/process_manager.rs)を照合し、schemaだけの可能性と実呼出しの成功を区別した。公開一覧外の背景子孫や外部書込の全終了は保証しない。自律継続・設定・MCPの最終再確認はrun-20261113-170939/report.jsonに記録した。

製品launchと分離したprocess cwd/runtime・archive/memory構成でmemory書込、親原記録読取/書込拒否を確認した。Windows sandbox未準備の隔離homeでは要求がreadOnlyへ縮退し製品は拒否する。成功条件はWindows sandboxの有効な環境で、新規home準備はM3の受入項目とする。M2はホストモデル・実機・新規認証・長時間の検証ではない。

M3の製品契約は公開`windowsSandbox/readiness`と`windowsSandbox/setupStart(mode=elevated)`、`windowsSandbox/setupCompleted`を使う。初回状態取得は認証と独立し、準備は明示操作に限る。開始受付だけでは成功とせず、完了通知後にreadinessを再取得する。非readyなら明示再接続を求め、受付不明時は再送せず切断可能な待機を維持する。memory付き開始のready確認とthread応答の書込範囲検証を別に行い、read-only送信は準備状態で拒否しない。schema・固定通知試験と新規homeの実受入は区別し、結果は[M3子仕様](../wip/unit_050/GOAL_ACCEPTANCE.md)に記録する。
