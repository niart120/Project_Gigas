# 08-sdk-compatibility.md — 外部接続の根拠・未確認事項

[文書一覧](README.md)

製品は`openai-codex-cli-bin==0.167.0`の公開app-server v2へ、一つのstdio接続で通信します。Python SDKは使いません。外部契約の根拠と検証範囲を本書、GIGASの責務・操作を[02](02-runtime.md)・[09](09-control-flows.md)・[10](10-ui-interaction.md)に分けます。

会話・Goal・停止の現在の設計は[unit_061](../complete/unit_061/CONVERSATION_EXECUTION_MODEL.md)・[unit_062](../complete/unit_062/GOAL_EXECUTION_MODEL.md)です。新構成では自動試験、実app-server・実モデルとの接続、CH552を使った短いゲーム操作・中断を確認しています。GUIから実機までの通し操作は未確認です。採用版の調査、過去製品の結果、現在の受入を区別し、確認範囲を末尾に記録します。

## 採用版と根拠の扱い

[U02 M0](../complete/unit_016/CODEX_SDK_SELECTION.md)では、SDK `openai-codex==0.167.0`が要求handlerを唯一のreader内で同期実行し、回答保留中に後続受信が止まることを模擬peerで再現しました。[U02 M1](../complete/unit_017/CODEX_DIRECT_CONNECTION.md)で公開app-server直接接続を採用しました。SDK内部の差し替えや二重受信で補いません。

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

Goalなしの作成は`thread/goal/set(objective=..., status=active, origin=user)`、既存Goalの編集はobjectiveだけ、再開はstatus=activeだけを送ります。目的・予算・累積利用量を省略した再開で保持すること、既存目的の編集で利用量をリセットしないことは採用版の実装と[U06 M0](../complete/unit_044/GOAL_INTERACTION_DESIGN.md)の確認範囲です。異なる目的文を暗黙のclear・新規作成へ変換しません。

`thread/goal/clear`は目的の取り下げ、`status=paused`はGoalの休止、`turn/interrupt`はturnの中断です。pause/clear単独が実行中turnやGIGASの入力列を止める保証はありません。製品の削除はclearだけ、共通停止は入力列の取消し・回収とturn中断、active Goalの休止を調整します。Goal非active通知から追加の停止を起動しません。

通常送信は受付応答と対象turnの開始または既観測終端通知を回収するまで共通送信taskを保持します。既に通知で把握した現在turnへの追加指示は、新しい開始通知を要求しません。これにより、応答後・開始通知前の停止も同じ送信taskを回収できます。

Goal変更は応答に対応する通知を処理するまで共通の送信taskに含め、応答が現在値のままなら追加通知を待たず終了します。clearの`cleared=false`も、新たな通知なしで終了します。Goal表示は通知を正本とし、後続値を遅い応答で巻き戻しません。停止と競合した送信済みGoal操作は、その結果・通知を反映して休止要否を判断します。既知turnの中断と機器解除は先に進め、停止中に始まった自律turnも中断します。

[unit_062の境界確認](../complete/unit_062/GOAL_EXECUTION_MODEL.md)では、採用実app-serverとローカル固定モデル応答により、Goal再開・自律turn開始と停止の競合を代表場面で確認しました。予算枯渇時のactive要求はbudgetLimitedのままの成功応答となる場合があり、usageLimitedからはactive受理後に制限へ戻る場合を確認しています。GIGASは再開を先行表示せず、拒否・無変化・後続通知をそのまま反映します。この境界確認は実アカウントの制限や新製品全体の受入ではありません。

停止の正常結果はサーバー側停止と入力解除・回収の完了です。同じMCP入口で通常受付へ戻り、再開用の機器許可や停止済み状態を追加しません。任意の背景プロセス・書込の全終了は保証せず、全端末の列挙・終了を停止契約へ含めません。停止失敗時の接続終了と、通知待ち中の明示切断はunit_061に従います。

## MCP・画像・機器入力

Python MCP SDK `2.3.0`の`MCPServer.streamable_http_app(json_response=True, stateless_http=True)`を既存サービス用asyncioループでホストします。公開形式の根拠は[MCPのツール仕様](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)と[Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)、組込方法は[SDKのASGI文書](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/run/asgi.md)です。

初回thread準備で、127.0.0.1のOS割当ポート、会話ごとのサーバ名・Bearerを`thread/start.config`へ渡します。アプリ専有MCPだけを有効にし、`observe`と`act_and_observe`、required、起動期限10秒、ツール期限300秒を指定します。同一会話の通常指示・Goal再開でMCPを作り直しません。Bearerをプロンプト・原記録へ含めず、切断で入口を閉じます。

`observe`には非破壊・閉じた対象の注釈、`act_and_observe`には副作用あり・非冪等の注釈と当該ツールの`approval_mode=approve`を指定します。これはゲーム機器を操作するツールの設定であり、コマンド・ファイル承認全体を自動許可する設定ではありません。公開MCP直接呼出しの成功だけをモデル経由の承認・画像受信の証拠にしません。

MCPは共通の入力形式を検査し、現在の機器能力は`InputService.start()`が全列へ確認します。MCP側に機器能力や入力許可を複製しません。停止中は会話実行の入口で拒否し、入力taskは解除まで一件だけを所有します。待ち行列は持たず、取消し・期限では捕捉した操作taskだけを回収します。HTTP切断やcancel通知が入力取消しへ届くことに依存せず、共通停止は入力所有者へ直接届けます。

要求期限の根拠は[採用版の期限解決](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/codex-mcp/src/connection_manager.rs)と[要求の時間制限](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/rmcp-client/src/rmcp_client.rs)です。300秒は製品の待機期限で、MCP共通の最大値や解除完了の期限ではありません。progressで延長せず、クライアントとサーバーは別に計時します。内部入力時間の固定上限・暗黙分割・非同期ポーリングは設けません。

画像は同じPNG bytesをMCPのimage contentと保存へ使い、観測ID等をJSON textで返します。保存は非同期で、モデル内部の前処理まで同一性を保証しません。stdio受信は2 MiB、送信は1 MiBの一行上限です。切詰め通知から元画像を復元せず、保存元は元のObservationとします。部品APIと取消し・失敗の詳細は[ゲームI/O MCP](../../docs/observation-mcp.md)を参照します。

## 認証・モデル・実行環境

公開`account/login/start`・`account/read`・`model/list`を使います。資格情報の保管はruntimeが担当し、GIGASへ複製しません。APIキー認証は対象外です。無効なモデル既定値でも接続・ログインを維持し、公開候補から選び直します。

通常指示は選択値をturn/startへ渡します。threadがある場合の選択変更は`thread/settings/update`の成功後に確定し、現在turnの設定は書き換えません。U06 M0では実モデルと固定Responsesの両方で、現在turnの後続推論は旧値、次turnから新値となる境界を確認しました。製品は調査用rolloutを読んで設定を補いません。

Windows準備は`windowsSandbox/readiness`・`setupStart(mode=elevated)`・`setupCompleted`を使います。認証と独立して取得し、明示準備の完了通知と再取得を待ちます。成功後も非readyなら再接続を求めます。通常指示・Goal操作はreadyと有効な候補を要求し、memory限定のthread応答も検査します。未準備を権限拡大やread-onlyへの暗黙切替で補いません。

利用枠は公開値の表示に使います。疎な更新通知では完全値を再取得し、未提供の残高・回復時刻を補いません。利用不能やturn失敗の通知だけをローカル停止・送信拒否へ変換せず、要求の可否をサーバーに任せます。定期監視・課金先変更・予算増額・回復時刻からの自動再開は行いません。

<a id="ui-api-open"></a>

## UI契約と残る確認

| ID | 現在の契約 | 検証の境界 |
| --- | --- | --- |
| UI-A1 | Goal作成・目的編集・目的を保つ再開・削除を分け、停止は共通操作とする | 状態・操作表を自動試験、作成・保持停止・再開・削除を新構成の実接続で確認。再起動後の復帰はU08 |
| UI-A2 | 元要求IDへの質問・承認回答と、Messageからのturn/startを分ける | 原稿・配送不明・失効を自動試験、元IDへの質問回答と質問中停止を実モデルで確認。新構成の実承認は未再検証 |
| UI-A3 | `tools.update_plan.enabled=true`、thread/turn単位の最新plan配列を表示 | 毎turnの出力は保証せず、Goal全体のTODOへ変換しない |
| UI-A4 | 公開summaryだけを表示し、内部推論全文を扱わない | 旧M3の短いsol/low・terra/mediumでは非空summaryを受信せず。固定表示試験と実受信成功を混同しない |
| UI-A5 | 実際の公開ツール名・対象・結果をitem単位で表示 | MCP画像・引数を公開対話へ重複保存せず、turn終端後の遅い内容を補わない |
| UI-A6 | 専用の計画的終了は初期版対象外 | [U07](../planned/unit_012/PLANNED_STOP.md)を初期版の進行条件にしない |

Planの公開項目はstepとpending/inProgress/completed、explanationは省略またはnullを許します。項目IDはなく、本文類似性で合成しません。Goal編集・削除だけでは現在Planを消しません。

質問は要求ID・question IDと選択肢の契約を守り、自由文を複数質問へ複製しません。承認は要求に提示された一回限りの判断だけを返し、既定許可・永続許可へ変換しません。秘密指定を含む質問は要求全体を未対応として返します。採用版の[質問protocol](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/protocol/src/request_user_input.rs)と[応答処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/bespoke_event_handling.rs)の照合では、そのエラーは空回答へ変換されるためturn中断とは扱いません。実モデルから秘密指定を受信した検証ではありません。

## 過去の実行結果と現在の受入

| 記録 | 当時の確認範囲 |
| --- | --- |
| [U02](../complete/unit_007/CODEX_CONNECTION.md) | Windowsの採用runtime、通常応答、質問・承認の保留と応答、受信・終了 |
| [U04 M2](../complete/unit_035/MCP_OBSERVATION_INTEGRATION.md) | 固定画像とUSB Video Deviceの実映像、実モデル画像受信、保存PNG後参照、実画面・通常終了 |
| [U05 M2](../complete/unit_042/INPUT_APPLICATION_INTEGRATION.md) | CH552・9600 baudの短い入力・途中停止・neutral・画面反映。模擬機器の65秒待機・短縮期限も別に確認 |
| [U06 M0](../complete/unit_044/GOAL_INTERACTION_DESIGN.md) | 採用版のGoal状態・目的編集・開始継続、設定反映、限定memory書込 |
| [U06 M2](../complete/unit_049/GOAL_MEMORY_INTEGRATION.md)・[M3](../complete/unit_050/GOAL_ACCEPTANCE.md) | 当時の製品会話・Goal・記憶・入力の結合、新規本人認証、Windows準備、IME、実質問・承認、対象実機構成 |

過去の入力世代・MCP再公開・全端末回収を含む結果は、その当時の記録として完了仕様へ残します。現在の停止保証や新構成の受入へ転用しません。新構成の実接続結果はunit_061/062へ記録します。

2026年11月14日の新構成の確認範囲は次のとおりです。実モデルはapp-server `0.167.0`、`gpt-6.1-sol`・effort `low`、既存のChatGPT認証を使用しました。

| 経路 | 確認結果と限界 |
| --- | --- |
| 通常指示・質問・停止 | 通常応答、元要求IDへの回答、質問中の停止・古い回答拒否・接続回収に成功。受付応答と開始通知の順序、停止・切断の競合は別に自動試験で確認 |
| Goal操作 | 作成→目的保持でpausedへ停止→pausedのまま通常指示→目的を変えずactiveへ再開→turnを止めず削除→共通停止を14.1秒で確認。残存task・記録エラーなし。実アカウントの制限は発生させていない |
| 実モデル→stateless MCP→実機 | COM8・CH552・9600 baudとUSB Video Device。RIGHT 500 ms要求を265 msでneutralへ戻し、約47 ms後に停止結果を受信。映像で右移動と停止後の位置維持を確認。同一入口の次observeとarchive保存に成功 |
| GUI・模擬入力 | GUI→公開stdio、Qt→会話実行→実HTTP MCP→模擬機器を自動試験で確認。最新のunit・integration合計802件成功。GUI→実機の通し操作とは区別 |

実機確認はサービス部品を組み合わせたPythonプログラムによるものです。一回のRIGHTだけを許す制限は検証用の介入で、製品の入力許可状態ではありません。新構成での新規ログインと実承認は未再検証、APIキー認証は対象外です。

未確認は、300秒実待ち、解除ACK、writeの最悪時間、DTR/RTS reset、PokeCon・3DS実機、他OS、長時間連続運転です。実アカウントの利用制限を意図的に発生させず、固定通知・模擬制限による確認と分けます。保存参照・再起動後の復帰は[U08](../planned/unit_013/RESUME.md)、連続運転は[U10](../planned/unit_015/CONTINUOUS_OPERATION.md)で扱います。
