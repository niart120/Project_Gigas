# 08-sdk-compatibility.md — 外部接続の根拠・未確認事項

[文書一覧](README.md)

製品は`openai-codex-cli-bin==0.167.0`の公開app-server v2へ、一つのstdio接続で通信します。Python SDKは使いません。外部契約の根拠と検証範囲を本書、GIGASの責務・操作を[02](02-runtime.md)・[09](09-control-flows.md)・[10](10-ui-interaction.md)に分けます。

## 採用版と根拠の扱い

要求・通知の受信は一つの接続処理で所有し、要求への回答待ちで後続受信を塞ぎません。SDK内部の差し替えや二重受信は設けません。

採用版ソースはtag `rust-v0.167.0`、commit `086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`へ固定して照合します。公開Web文書の現在値と採用版が異なる場合は、採用版のschema・実装・実行結果を区別して判断します。旧版のIssueや調査用内部rolloutを、製品の状態取得・復旧経路には使いません。

| 根拠 | 確認した境界 |
| --- | --- |
| 同梱runtimeの`app-server generate-json-schema --experimental` | thread・turn・Goal、要求・通知、設定の公開形式 |
| [採用版の設定schema](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/config.schema.json) | MCPのURL・headers・ツール制限・起動期限・要求期限 |
| [Goal API](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/ext/goal/src/api.rs)・[Goal runtime](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/ext/goal/src/runtime.rs) | 目的編集と状態・利用量の保持、更新後の介入・自律継続 |
| [thread処理](https://github.com/openai/codex/blob/rust-v0.167.0/codex-rs/app-server/src/request_processors/thread_processor.rs) | 通常指示・追加指示を同じturn/startへ送る経路と中断 |
| [公開experimental定義](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server-protocol/src/protocol/common.rs) | thread/settings/updateにはinitializeのexperimentalApi有効化が必要 |
| [MCP画像・モデル配送](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/mcp_tool_call.rs) | モデルへ渡す結果と、通知の切詰めpreviewは別 |

## 通常指示・Goal・明示停止

通常指示と追加指示は`turn/start`へ一度送り、実行中turnへの介入か新turnの開始かをサーバーに任せます。製品は宛先turnを指定する別経路や配送待ち行列を持ちません。受付応答から対象IDを得ますが、実行中・終了は通知で判断し、遅い応答で終端を巻き戻しません。

Goalなしの作成は`thread/goal/set(objective=..., status=active, origin=user)`、既存Goalの編集はobjectiveだけ、再開はstatus=activeだけを送ります。目的・予算・累積利用量を省略した再開で保持すること、既存目的の編集で利用量をリセットしないことは採用版のGoal実装に従います。異なる目的文を暗黙のclear・新規作成へ変換しません。

`thread/goal/clear`は目的の取り下げ、`status=paused`はGoalの休止、`turn/interrupt`はturnの中断です。pause/clear単独が実行中turnやGIGASの入力列を止める保証はありません。採用版の[Goal要求処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/request_processors/thread_goal_processor.rs)ではclearが保存済みthreadのGoalをロードなしで操作でき、購読なしでも通知を配送します。製品は閲覧中の選択IDへclearを直接送り、実行環境ready・作業場所・MCP・writer・resumeを削除条件にしません。製品の削除はclearだけ、共通停止は入力列の取消し・回収とturn中断、active Goalの休止を調整します。Goal非active通知から追加の停止を起動しません。

通常送信は受付応答と対象turnの開始または既観測終端通知を回収するまで共通送信taskを保持します。既に通知で把握した現在turnへの追加指示は、新しい開始通知を要求しません。これにより、応答後・開始通知前の停止も同じ送信taskを回収できます。

Goal変更は応答に対応する通知を処理するまで共通の送信taskに含め、応答が現在値のままなら追加通知を待たず終了します。clearの`cleared=false`も、新たな通知なしで終了します。Goal表示は通知を正本とし、後続値を遅い応答で巻き戻しません。停止と競合した送信済みGoal操作は、その結果・通知を反映して休止要否を判断します。既知turnの中断と機器解除は先に進め、停止中に始まった自律turnも中断します。

予算枯渇時のactive要求が制限状態のまま応答する場合や、受理後に制限状態へ戻る場合を扱います。GIGASは再開を先行表示せず、拒否・無変化・後続通知をそのまま反映します。実アカウントの制限発生を強制せず、代表通知による試験と区別します。

停止の正常結果はサーバー側停止と入力解除・回収の完了です。同じMCP入口で通常受付へ戻り、再開用の機器許可や停止済み状態を追加しません。任意の背景プロセス・書込の全終了は保証せず、全端末の列挙・終了を停止契約へ含めません。停止失敗時の接続終了と、通知待ち中の明示切断は[02](02-runtime.md)に従います。

## MCP・画像・機器入力

Python MCP SDK `2.3.0`の`MCPServer.streamable_http_app(json_response=True, stateless_http=True)`を既存サービス用asyncioループでホストします。公開形式の根拠は[MCPのツール仕様](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)と[Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、組込方法は[SDKのASGI文書](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/run/asgi.md)です。

新規会話のthread/startと既存会話のthread/resumeのconfigへ、127.0.0.1のOS割当ポート、現在の会話用サーバ名・Bearerを渡します。アプリ専有MCPだけを有効にし、`observe`と`act_and_observe`、required、起動期限10秒、ツール期限300秒を指定します。同一会話の通常指示・Goal再開でMCPを作り直しません。Bearerをプロンプト・原記録へ含めず、切断で入口を閉じます。

`observe`には非破壊・閉じた対象の注釈、`act_and_observe`には副作用あり・非冪等の注釈と当該ツールの`approval_mode=approve`を指定します。これはゲーム機器を操作するツールの設定であり、コマンド・ファイル承認全体を自動許可する設定ではありません。公開MCP直接呼出しの成功だけをモデル経由の承認・画像受信の証拠にしません。

MCPは共通の入力形式を検査し、現在の機器能力は`InputService.start()`が全列へ確認します。MCP側に機器能力や入力許可を複製しません。停止中は会話実行の入口で拒否し、入力taskは解除まで一件だけを所有します。待ち行列は持たず、取消し・期限では捕捉した操作taskだけを回収します。HTTP切断やcancel通知が入力取消しへ届くことに依存せず、共通停止は入力所有者へ直接届けます。

要求期限の根拠は[採用版の期限解決](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/codex-mcp/src/connection_manager.rs)と[要求の時間制限](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/rmcp-client/src/rmcp_client.rs)です。300秒は製品の待機期限で、MCP共通の最大値や解除完了の期限ではありません。progressで延長せず、クライアントとサーバーは別に計時します。内部入力時間の固定上限・暗黙分割・非同期ポーリングは設けません。

画像は同じPNG bytesをMCPのimage contentと保存へ使い、観測ID等をJSON textで返します。保存は非同期で、モデル内部の前処理まで同一性を保証しません。stdio受信は2 MiB、送信は1 MiBの一行上限です。切詰め通知から元画像を復元せず、保存元は元のObservationとします。部品APIと取消し・失敗の詳細は[ゲームI/O MCP](../../docs/observation-mcp.md)を参照します。

## 認証・モデル・実行環境

公開`account/login/start`・`account/read`・`model/list`を使います。資格情報の保管はruntimeが担当し、GIGASへ複製しません。APIキー認証は対象外です。無効なモデル既定値でも接続・ログインを維持し、公開候補から選び直します。

通常指示は選択値をturn/startへ渡します。ロード済み実行対象の選択変更はthread/settings/updateの受理後に確定し、現在turnは変更しません。新規入力・履歴閲覧中は公開候補に対して検証した選択を入力欄に保持し、明示実行時のthread/start・thread/resumeのmodelとconfig.model_reasoning_effortへ渡します。閲覧だけの変更でsettings/updateやresumeを送りません。採用版の[設定更新処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/request_processors/turn_processor.rs)はロード済みthreadを必要とし、保存済みIDだけでは更新できません。製品は内部rolloutを読んで設定を補いません。

Windows準備は`windowsSandbox/readiness`・`setupStart(mode=elevated)`・`setupCompleted`を使います。認証と独立して取得し、明示準備の完了通知と再取得を待ちます。成功後も非readyなら再接続を求めます。通常指示・Goal作成・目的編集・再開はreadyと有効な候補を要求し、U08では作業フォルダーを指定したthread応答も検査します。未準備を権限拡大やread-onlyへの暗黙切替で補いません。

利用枠は公開値の表示に使います。疎な更新通知では完全値を再取得し、未提供の残高・回復時刻を補いません。利用不能やturn失敗の通知だけをローカル停止・送信拒否へ変換せず、要求の可否をサーバーに任せます。定期監視・課金先変更・予算増額・回復時刻からの自動再開は行いません。

<a id="ui-api-open"></a>

## UI契約と残る確認

| ID | 現在の契約 | 検証の境界 |
| --- | --- | --- |
| UI-A1 | Goal作成・目的編集・目的を保つ再開・削除を分け、停止は共通操作とする | 閲覧中の削除はresumeなし。再開は押下後に準備・resumeする。M4・M5・M6で操作と状態を確認 |
| UI-A2 | 元要求IDへの質問・承認回答と、Messageからのturn/startを分ける | 原稿・配送不明・失効と停止の競合を扱う。履歴上の古い要求へ再回答させない |
| UI-A3 | tools.update_plan.enabled=true、thread/turn単位の最新plan配列を表示 | 毎turnの出力は保証せず、Goal全体のTODOへ変換しない |
| UI-A4 | 公開summaryだけを表示し、内部推論全文を扱わない | 非空summaryの提供は保証しない。固定表示試験と実受信の検証を区別 |
| UI-A5 | 実際の公開ツール名・対象・結果をitem単位で表示 | MCP画像・引数を公開対話へ重複保存せず、turn終端後の遅い内容を補わない |
| UI-A6 | 専用の計画的終了は初期版対象外 | [U07](../planned/unit_012/PLANNED_STOP.md)を初期版の進行条件にしない |

Planの公開項目はstepとpending/inProgress/completed、explanationは省略またはnullを許します。項目IDはなく、本文類似性で合成しません。Goal編集・削除だけでは現在Planを消しません。

質問は要求ID・question IDと選択肢の契約を守り、自由文を複数質問へ複製しません。承認は要求に提示された一回限りの判断だけを返し、既定許可・永続許可へ変換しません。秘密指定を含む質問は要求全体を未対応として返します。採用版の[質問protocol](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/protocol/src/request_user_input.rs)と[応答処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/bespoke_event_handling.rs)の照合では、そのエラーは空回答へ変換されるためturn中断とは扱いません。実モデルからの秘密指定受信と応答の実行確認は未実施です。

## U08の採用契約と確認範囲

Project等の実験的APIを使うためinitialize.capabilities.experimentalApi=trueを維持します。以下は採用版の公開契約です。GIGASの責務と後続への引継ぎは[M0](../complete/unit_068/MANAGEMENT_STORAGE_DESIGN.md)、製品受入条件は[07](07-verification.md)へ集約します。

| 機能 | 採用する公開経路・制約 |
|---|---|
| Project | project/create・list・read。rootsは絶対パス1件。既存フォルダー登録もcreateを使い、内容は変更しない。Project削除と実ファイル削除は別 |
| 未所属・所属会話 | thread/startのprojectId・cwd・runtimeWorkspaceRoots。thread/listのprojectIdはnullで未所属、IDで所属を絞る。保存cwdはthread/readから取得し、独自対応DBを作らない |
| 履歴・Goal閲覧 | thread/read、thread/turns/list、thread/items/list、thread/goal/get。閲覧にresumeは不要 |
| 既存会話の再接続 | 停止→thread/unsubscribe→旧資源回収→現在設定でthread/resume。unsubscribe単独は停止・解放ではない。購読者なし・idle・非実行の既存threadは、設定差分があるresumeで終了・再構築される |
| active Goal | resume後に継続し得る。追加指示はturn/startへ送る。受付前のGoal進行を許容し、現在の資源と選択モデル・設定はresume前に準備する |
| 新規thread | 実thread IDはthread/startの応答で保持する。記録先の正確なパス案内は要件にせず、そのための追加API・再resume・MCP結果拡張は行わない |

根拠は採用版の[Project schema](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server-protocol/src/protocol/v2/project.rs)、[thread処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/request_processors/thread_processor.rs)、[Goal継続](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/ext/goal/src/runtime.rs)です。製品経路での実行結果と未検証範囲は、担当子仕様へ記録します。

### 設定の所有と適用

専用CODEX_HOMEは%LOCALAPPDATA%/GIGAS/codex_homeです。GIGASのsettings.tomlをUI設定の正本とし、開発用のconfig.tomlや認証を同期しません。既存account/login/start→公開ログインURLでの本人操作→完了通知・account/readをこのhomeで使います。接続・ログインの前にProject作成・選択を要求せず、選択したProjectごとにhomeを切り替えません。Windows実行環境のreadiness・setupは既存の明示操作を維持します。

起動時の設定上書きは現在のsettings.tomlから組み立てます。Web・記憶・承認の保存後は、新規thread/startと既存thread/resumeへ最新値を明示し、稼働中threadの即時切替はしません。[設定解決処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/config_manager.rs)は要求のconfigをCLI上書きへ重ねて読み込みます。既存threadへの再参加では無視される場合があるため、前表の購読解除・idleの条件を満たすことが必要です。ファイル保存だけで稼働中の設定が変わるとは扱いません。

| 設定 | 渡す値・反映単位 |
|---|---|
| Web検索 | web_search=live／disabled。新規開始・再接続のconfigへ渡す |
| 標準記憶 | features.memories、memories.generate_memories、memories.use_memoriesを同じON/OFFへ対応。memories.disable_on_external_context=falseを使い、ゲームMCP利用だけで生成対象から除外しない |
| 指示・Skills | project_doc_max_bytes=0、skills.include_instructions=falseの上書きを撤去。新規開始・再接続のcwdで標準探索する |
| 承認 | approvalPolicy=on-request、approvalsReviewer=auto_review／userを開始・resumeの公開項目で指定 |
| sandbox | workspace-write、cwdとruntimeWorkspaceRoots=[作業フォルダー]、追加writable_roots=[]、network_access=false、一時領域除外。返却された実効設定を確認 |
| 固定の機能範囲 | apps・plugins・hooks・codex_hooks・plugin_hooks=false、notify=[]、default_mode_request_user_input=true、tools.update_plan.enabled=true。ゲームMCP以外の導入経路は追加しない |

モデル・推論強度は本書の認証・モデル節に従い、閲覧中とロード済み実行対象で適用経路を分けます。上の設定をそのたびに再適用する仕組みへ広げません。専用homeのconfig.tomlはCodex標準設定として扱い、GIGASが所有する項目は要求上書きで決定します。要求されていない項目の編集UI・双方向同期は設けません。

[記憶の起動処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/memories/write/src/start.rs)は非ephemeral・記憶機能有効・root会話を対象とします。phase1は許可された対話source、memory_mode、経過・idle時間等から候補を選びます。app-serverのsourceはvscodeで、[候補source](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/rollout/src/lib.rs)に含まれます。[MCP呼出し処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/mcp_tool_call.rs)はdisable_on_external_context=falseならMCPによるpolluted化を行いません。これは採用版の既定値とも一致します。

OFFでは起動時生成と標準参照を無効にし、既存ファイルの削除APIを呼びません。generate_memories=falseで作ったthreadの生成対象状態はdisabledとして保存されます。ONへ戻す際に過去threadを一括変更・強制再抽出する処理は追加しません。将来の候補選択・抽出・統合は標準処理に任せます。モデルによる抽出・統合の製品確認はM3・M6で行い、即時生成を開始条件にしません。

[M3](../complete/unit_071/EXECUTION_SETTINGS_AND_INSTRUCTIONS.md)ではON/OFFの実効値、公開参照経路、OFF時の既存記憶保持と、標準統合処理による試験用summaryの形式整理を確認しました。実会話からの抽出は未観測でM6へ引き継ぎます。ゲームMCP利用後の生成対象状態はM6・M7で確認し、条件を変えて抽出を強制する機構は追加しません。

[Skills探索](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/ext/skills/src/host_roots.rs)はcwd側・祖先側の.agents/skills、$HOME/.agents/skills、専用CODEX_HOME/skills等を扱います。独自の探索器は不要です。

workspace-writeを要求しても実効sandboxがreadOnlyになる場合があるため、要求値だけで権限の成功としません。製品はreadinessと実効sandboxを検査し、M2・M3・M6で通常書込み・他会話資料参照・通信制限・auto_review/userの要求応答を検証します。自動審査は無条件許可ではなく、GIGASによる独自審査や拒否後の自動的な人間承認への切替は設けません。
