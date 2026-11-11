# 08-sdk-compatibility.md — SDK互換性・未確認事項

[文書一覧](README.md)

第2〜6節は2026-11-10時点の公開資料・固定コミットに基づく調査記録です。2026-11-11のU02 M0では配布SDK・runtime `0.167.0` を再調査し、SDK方式の不成立を確認しました。製品にはruntime `openai-codex-cli-bin==0.167.0` と公開app-server直接接続を採用しています。

## U02の採用判断

`openai-codex==0.167.0`（対応runtime `openai-codex-cli-bin==0.167.0`）は、要求handlerを唯一のreader内で同期実行するため製品接続に不採用です。模擬peerを使い、回答保留中は後続通知と別要求の応答が読めず、回答後に双方届くことを再現しました。公開の保留・後送APIは確認できません。現在の開発コミット`039d0f27d56c31913ffc0f2055a3432b1bcb0b06`も同じclient.pyで、より新しい公開prereleaseはありません。

Windows x64/Python 3.13.5の隔離環境で、固定runtimeのinitialize、未認証account/read、model/list、close呼出しと調査Pythonの正常終了を確認しました。これは認証・モデル利用・通常ターン・異常時の全資源終了の保証ではありません。closeはkill後のwaitや内部スレッド生存の確認結果を公開しないため、終了契約も別途解決が必要です。

詳細な配布物hash、API一覧、再現コード、GUI・サービス所有設計は[M0子仕様](../complete/unit_016/CODEX_SDK_SELECTION.md)にあります。2026-11-11にユーザーが公開app-server直接接続を承認しました。[M1](../complete/unit_017/CODEX_DIRECT_CONNECTION.md)で単一stdio接続を実装し、既存ChatGPT認証で通常応答、質問への回答・中断、command承認の取消し、file承認の拒否を確認しました。SDKは製品依存に追加していません。後続のGoal・MCP・旧書込終了は入口の存在確認に留まり、第7節の担当工程で実行確認します。

採用版のGUI結合・全体品質ゲートは[U02親仕様](../complete/unit_007/CODEX_CONNECTION.md)に記録します。新規ブラウザ認証、APIキー、承認accept後の実行、別版・別環境は、上記の受入結果に含みません。

## U04 M0のMCP公開経路（2026-11-12）

[M0子仕様](../complete/unit_031/RECORDED_OBSERVATION_DESIGN.md)で、同梱runtime `0.167.0`の公開schemaを生成し、公開tag `rust-v0.167.0`（`086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`）の設定・通知処理を読んだ。製品MCP、認証付きHTTP、画像の実モデル配送は実行していない。

| 根拠 | 確認した事実・設計への適用 |
|---|---|
| 同梱実行ファイルの`app-server generate-json-schema --experimental` | `ThreadStartParams.config`、`mcpServerStatus/list`のthreadId/serverName、`mcpServer/tool/call`のthreadId/server/tool/argumentsを確認。後二つは限定接続検証に利用できるが、専用の製品操作経路は作らない |
| [採用tagの設定schema](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/config.schema.json#L4208)・[公式MCP設定](https://learn.chatgpt.com/docs/extend/mcp) | URL、http_headers、enabled_tools、required、startup_timeout_sec、tool_timeout_secが公開されている。現製品の外部MCP無効化とthread/start.configへ、アプリ専有サーバを追加する方針 |
| [MCPのimage content](https://modelcontextprotocol.io/specification/2025-06-18/server/tools) | 同じPNG bytesをbase64のimage contentで返し、観測ID・取得UTC・analysisを短いJSON textで返せる。structuredContentとの二重配送を必須にしない |
| [採用tagのモデル配送と通知](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/mcp_tool_call.rs#L695) | 通知用には切詰め結果、モデル側には元の結果を渡す。Gigasの保存PNGとMCP応答の同一性を、モデル内部の画像前処理まで拡張しない |
| [通知結果上限](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/utils/pty/src/lib.rs#L25)・[切詰め処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/utils/output-truncation/src/lib.rs#L69) | 結果全体を最大1 MiBのpreviewへ制限する。外側JSONが加わるためGigas現行の受信1 MiBでは不足し得る。M2で2 MiBの有界受信と実通知サイズを確認する |
| [通知画像省略feature](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/features/src/lib.rs#L1922) | 開発中・既定false。切詰め後のtextに残る画像断片を消す保証もないため、U04の必須設定にしない |
| [公式Python MCP SDK v2.3.0](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0)・[ASGI組込み](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/run/asgi.md)・[実行設定](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/run/index.md) | Python >=3.10。`MCPServer.streamable_http_app(json_response=True, stateless_http=True)`を既存サービス用ループ上でホストする候補。lifespanによるsession manager起動・終了が必要。依存導入・Windows実行は未実施 |
| [Streamable HTTPの寿命](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports) | 接続／session IDを操作許可と同一視しない。U04はサーバ寿命に固定したAuthorization値を使い、切断後は再利用しない。入力世代との対応はU05で設計 |

**採用する設計**: loopbackのOS割当ポートと接続ごとの不透明なBearer値をthread/start.configへ渡す。ハンドラがreader/writerを固定参照する構成とし、token履歴表や新たな権限管理をU04で先行実装しない。設定ファイル書換えやreload、別の受信所有者はU04に不要。既存read-only threadとdeveloperInstructionsを使い、保存はアプリが行う。

**未確認と期限**: M2でSDK/ASGIのWindows起動終了、required・認証ヘッダーの実適用、本MCP画像のモデル受信、原寸PNGの容量、通知の実寸とGUI応答性を確認する。M0で公開経路は確認したが、製品接続の成功は未確認である。経路が失敗したら原因を記録し、方式・版・必要な固定縮小の再設計前に画像結合を受入しない。保存・観測の独立部分は進められる。

U05の停止・次実行では旧tokenの失効と新tokenの公開が必要。保持threadへの設定反映／MCP再読込またはthread・接続再作成の効果は未検証で、物理入力公開前に確認する。新threadへの設定注入を確認したことは、U06の同一セッションGoal再開を確認したことではない。

# 1. この文書の位置づけ

**公開app-server v2プロトコルへの単一接続を採用します。** Codex固有処理と通信管理は一つのCodex接続実装へ集約します。SDKの内部クラスや非公開メソッドへの依存、非公開ハンドラの差し替え、同じ通信をSDKと独自処理が二重に受信する構成は採りません。runtimeは0.167.0へ固定しています。製品経路の必要APIの実行確認はM1/M2で行います。

通信の管理主体は一つのCodex接続部に保ちます。asyncio subprocessのreaderで受信を振り分け、回答待ちはreaderの外で扱います。SDK改修・非公開API差替え・二重受信は行いません。以下のSDK調査記述は不採用判断の根拠であり、製品方式の現行契約ではありません。

次の三つは別の根拠として扱います。

| 根拠 | 確認できること | 確認できないこと |
| --- | --- | --- |
| 公式文書 | 提供方針、公開 API、接続方式 | 採用する版の不具合や個別環境での動作 |
| GitHub の確認時点のソース | そのコミットの具体的な実装 | 公開済みパッケージへの収録、安定 API としての保証 |
| 採用版での実行 | 固定した SDK・ランタイム・Windows の組み合わせの動作 | 未検証の版や環境の動作 |

以下の初回調査対象は `main` のコミット `27fcd7cbedd7efd63fd704bd01a0cfe1d7b85a26` です。この時点のPythonパッケージ定義は `0.0.0-dev` であり、その依存バージョンを「最新安定版」として転記しません。配布版0.167.0については冒頭の採用判断を優先します。

# 2. プロセスと接続

公式 Python SDK は `openai-codex` として案内され、Python 3.10 以上を対象としています。同期・非同期の API があります。標準のインストールには固定された Codex ランタイムへの依存が含まれ、独自の実行ファイルの指定もできます。

確認した SDK ソースは `codex app-server --listen stdio://` を子プロセスとして起動し、標準入出力の行単位 JSON メッセージで通信します。Python SDK を制御アプリへ組み込んでも、Codex ランタイム自体が Python プロセス内で動くわけではありません。非同期クライアントは内部で同期クライアントをスレッド経由で呼ぶ実装です。

ゲーム MCP の Streamable HTTP は別の接続です。ゲーム I/O と MCP サーバは制御アプリ内に置き、Codex app-server が HTTP クライアントとして接続します。公式文書には HTTP 型 MCP の URL、認証ヘッダー、接続・ツール実行タイムアウトの設定があります。長い入力列を許す前に、アプリ側の操作時間上限と MCP のツールタイムアウトを整合させます。

実行世代を MCP の接続コンテキストに紐付け、停止後の再開では旧世代の呼び出しを拒否する設計です。SDK からその接続設定を渡せるか、再開時に更新・再接続できるかは未検証です。採用版で必要な更新ができない場合の接続再作成も、このCodex接続部内で確認します。

Windows のネイティブ実行は公式に案内されています。ただし、本構成の子プロセス起動、ローカル HTTP 接続、プロジェクトへの書き込み、GUI との並行動作を Windows で確認済みとはしません。

# 3. 通常ターンと Goal の継続

公式の高水準 API にはスレッドの作成・再開、ターンの実行・ストリーム取得、実行中ターンへの `steer`、`interrupt`、明示的なコンテキスト圧縮があります。スレッドはコンテキストを継続する単位であり、ゲームの進行状況やセーブデータそのものではありません。

Goals は公開 app-server の `thread/goal/set`・`get`・`clear` と更新通知で扱えます。確認した Python ソースにも `thread_goal_set`、`pause_goal`、`start_goal_operation`、`next_goal_notification` といった低水準処理があり、複数の物理ターンを一つの Goal の活動として扱う実装が存在します。ただし、確認した高水準 API リファレンスには同等の入口が掲載されていません。低水準実装の存在だけを根拠に、高水準 SDK で安定して利用できると断定しません。

Codex接続部は通常ターンのイベントと、複数ターンにまたがる Goal の更新を扱います。単一の `run()` が返ったことや `turn/completed` を、依頼全体の達成とみなしません。アプリは Goal 状態、実行中ターン、利用者の停止意図を別々に保持します。

Goal の継続は実行状態、予算、保留入力などの条件を受けます。Goal が active であれば無条件に永久実行するという保証はありません。公式 Cookbook はツール呼び出しを含まない継続ターンで、自動継続が抑制される条件も説明しています。アプリ側で独立した無限再投入ループを重ねません。

確認した低水準の Goal 開始処理は、待機中の永続スレッドを要求します。再開時は既存 Goal の状態を読み、必要なら同じ Goal を再開します。新規 Goal の開始処理を毎回流用すると、利用量などの履歴をリセットする場合があります。即時停止では先にゲーム入力を閉じ、その後 Goal の一時停止と実行中ターンの中断を行います。Goal の pause だけでコントローラーの押下が解除されるわけではありません。

# 4. 介入・表示・利用者への要求

公開プロトコルには `turn/steer`、`turn/interrupt`、`turn/plan/updated` があります。計画通知はエージェントが公開した手順の更新として UI に表示できますが、思考の全内容や常時最新の中期計画を保証するものではありません。アプリは自身でゲーム攻略計画を生成しません。

利用者への質問は、取得できる場合は `item/tool/requestUserInput` などの要求を画面へ渡します。要求の種類・有効性は採用版のスキーマで確認します。質問、コマンド実行承認、ファイル変更承認、MCP の追加情報要求は、それぞれ別の要求です。ターンの中断で失効した要求へ、後から回答を送信しません。

承認については、そのまま採用できない実装上の注意があります。確認した低水準 `CodexClient` の標準ハンドラは、コマンド実行とファイル変更の承認要求に `accept` を返します。高水準 API の既定の `approval_mode` は `auto_review` です。`on-request` という設定名だけで「許可範囲外の操作は GUI で人間に確認できる」と判断できません。

同ソースでは、標準出力を読む一つのスレッド内で要求ハンドラを同期呼び出ししています。そこで GUI の回答を待つと、後続通知や中断要求への応答の読み取りまで止めるおそれがあります。これはソースからの設計上の懸念であり、実機での再現確認ではありません。

Codex接続部は、受信した質問・承認要求を要求IDとともにGUIへ渡し、有効な回答だけをSDKの公開経路で返します。回答待ちでイベント受信と停止受付を塞がず、停止・取消しで失効した要求へ遅い回答を返さないことを、採用版で確認します。ゲーム入力の即時停止は、この応答処理と独立して成立させます。

上記のソース調査で分かった制約が、採用版の公開機能で解消できるかは未確認です。要件を満たせない場合に要求処理だけを別の受信経路へ逃がすのではなく、SDK接続の未対応事項として扱います。SDK優先の責務分担を採用しますが、具体的な公開APIと要求の保留・取消しの実装方式は、確認が終わるまで未検証として残します。

# 5. 認証・モデル・利用制限

高水準 API には ChatGPT ログイン、デバイスコードによるログイン、API キーログイン、アカウント状態取得があります。モデル一覧から、そのアカウントで利用できるモデルと推論設定を取得する方針とし、特定モデル名や effort の全種類を固定しません。

app-server の利用枠情報は ChatGPT 利用枠が対象です。取得できた使用率や回復予定時刻を表示します。API キー利用時の課金残高やクレジット上限を、同じ通知から必ず取得できるとはしません。

利用制限や課金関連エラーは、得られたエラーと利用枠情報を添えて停止理由にします。ランタイム自身が再試行中の通知と、最終的な実行失敗は分けます。初期版の再開は利用者の明示操作に限り、回復時刻到来だけで入力を再開しません。

# 6. 継続制御への委任と制約

計画的終了の旧案は元Goalを休止し、同じスレッドの通常ターンでプレイ終了処理を行う方式でした。[10のUI合意](10-ui-interaction.md)では追加指示を入口とし、共通の追加指示・公開通知・停止で成立するかをU07 M0で確認します。専用ターン切替は採用せず、未成立時は必要な設計差分を記録します。通常攻略ではCodexの判断と公開状態を正本とし、アプリ側で実行中の状態整合性検査・停滞推定・自動修復を行いません。以下の調査結果はCodexへ任せる範囲と制約の根拠であり、外側に同等の監視・復旧機構を実装する要求ではありません。処理フローは[09](09-control-flows.md)を参照します。

継続制御のソース調査はコミット`0811df39768427445b9730c039d6c04020670e04`を参照します。これは採用リリースの確定ではありません。公式資料の継続方針、当該コミットの具体的なコード、実行未確認の設計上の推論を区別します。

| 確認対象 | 確認した根拠 | 採用方針への含意 |
| --- | --- | --- |
| 一時的エラー | [app-server文書](https://learn.chatgpt.com/docs/app-server)には内部再試行と最終失敗を扱う通知があります | 再試行はCodexへ任せ、アプリは受け取った通知を表示します。別の開始や操作再送を重ねません |
| ターン間継続 | [Goals文書](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)はイベント駆動の継続と抑制条件を説明しています | activeと実ターン進行を同一視せず、無操作・無通知から異常を推定しません |
| ランタイムの継続処理 | [extension.rs](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/codex-rs/ext/goal/src/extension.rs)のidleイベントから継続処理へ進み、[runtime.rs](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/codex-rs/ext/goal/src/runtime.rs)は保留・ライブスレッド・Goal状態・開始受付を確認します | この内部処理をアプリで再実装しません。Codexが解消できない停滞はユーザーが停止・再開します |
| 最終的なターンエラー | `on_turn_error`とruntime側にはusage_limitedやblockedへ移る経路があります | 通知を理由として停止します。アプリから自動でactiveへ戻しません |
| Goal更新と開始の差 | [api.rs](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/codex-rs/ext/goal/src/api.rs)では状態保存とランタイムへの反映が分かれています | ユーザー操作への受付結果と実際の開始通知を区別して表示します。開始通知が来ないことを補う自動診断・再設定は行いません |
| 継続保留 | [goals.rs](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/codex-rs/state/src/runtime/goals.rs)には保存済みの継続保留があります | 内部DBを編集したり、状態を再設定し続けたりして保留を解除しません |
| 接続の実体 | [Python client.py](https://github.com/openai/codex/blob/0811df39768427445b9730c039d6c04020670e04/sdk/python/src/openai_codex/client.py)はローカルapp-serverを子プロセスとして起動しSTDIOを読み取ります | 明示的な通信故障では入力を止めます。アプリが自動再接続・再起動して攻略を復帰させる機構は設けません |

### 明示的な操作に必要な状態取得

`thread/goal/get`や`thread/read`などの状態取得は、開始・明示的再開・計画的終了の手順で必要なときに利用します。通常実行中の表示は公開通知に従い、整合性検査のための定期取得や、通知の取りこぼしを推定して状態を再構築する機構は作りません。

`thread/resume`はスレッドを再開する操作であり、生存確認としては使いません。明示的再開でも、スレッドの読込だけでゲーム入力を許可せず、開始手順に従います。質問・承認は実際に受信した要求とIDを扱い、欠けた要求本文を推測して回答しません。要求の受付・応答・失効は、一つのCodex接続実装がSDKの公開機能を使って扱います。

### ユーザーの明示的再開と、その限界

同じGoalを明示的に再開する候補は、`objective`を送らず`status=active`へ更新する公開経路です。目的の置き換え・Goalの再作成とは分け、予算や利用量を維持します。受付と実際の開始は受け取った通知どおりに扱い、開始が起きなくても自動再設定しません。通信失敗で要求結果が不明なら成功扱いや自動再送をせず、入力を閉じてエラーを表示します。

ユーザーが停止・アプリ再起動・再開を行う場合も、旧入力の失効と旧実行の終了確認は必要です。Python側の非同期待機をキャンセルしただけで、同期処理のワーカーや実機入力まで終了したとは扱いません。これはアプリ自身の起動・終了処理の責任であり、Codex内部の自動修復ではありません。SDK受信処理の所有者と中断時の解放は、Codex接続部の確認事項です。

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

# 7. 採用版で行う確認

U02の冒頭で採用版候補を選び、以下の公開経路と未成立時の影響を調べます。過去のソース調査は探索の入口であり、採用版での成立を保証しません。実行確認は本実装の接続部と継続利用する試験へ残します。

| 対象 | 先に調べること | 成立を確認する期限と結果 |
| --- | --- | --- |
| 起動・版・通常ターン | SDKとランタイムの組合せ、Windows配布物、公開API、受信と子プロセス終了の所有者 | U02完了まで。版を固定し、通常ターンの開始・応答・中断・終了をWindowsで確認 |
| 質問・承認 | 第4節の標準ハンドラの懸念が採用版にあるか、公開経路で保留・回答・失効を扱えるか | U02完了まで。単一接続で回答待ち中も受信・停止し、失効後の回答を破棄する。黙って許可しない |
| 認証・モデル | ChatGPT/APIキーの経路、資格情報の保管責任、利用可能モデルと設定値の検証 | U02はChatGPTで最小接続を確認。APIキーを含む初期版の認証範囲はU06完了までに確認。設定文字列の保存で代用しない |
| MCPと画像 | U02で設定経路を調査し、U04冒頭でサーバと画像応答形式を決定 | U04完了まで。画像をモデルが受け取り、保存した画像・観測IDと対応することを確認 |
| MCP接続と実行世代 | U02〜U04で公開された設定・更新または接続再作成を選定 | U05の製品経路で実機入力を許可する前。停止後の旧接続・遅延要求を拒否できることを確認 |
| Goalの継続 | U02で公開経路と制約を調べ、U06冒頭で採用版による複数ターンの通知・休止・完了を確認 | U06の継続制御実装へ進む前に接続方式の成立を判断し、完了までに実行フローを検証 |
| 追加指示・計画・制限 | U06 M0で配送、公開表示と完了の競合、利用枠情報を設計 | 公開表示と通常ターンの共通入力はM1、Goalとの結合はM2、利用制限・全体の結合はM3。未配送指示を新Goalへ転用せず、受付不明を再送しない |
| 追加指示による終了作業と休止 | U07 M0で共通配送から終了作業・休止・公開結果を確認 | 必要な差分の実装前。元Goalを保持し、作業報告とアプリが確認できた停止・結果を分ける。専用切替が必要とは仮定しない |
| 同じGoalと新規スレッドでの再開 | U06 M0で同一セッションの保持Goal、U08 M0で保存参照・再起動後の復帰経路を確認 | 同一セッション再開はU06 M2の実装前。保存スレッド読込・新規スレッドの再構成はU08の実装前。どちらも旧処理の収束・世代更新と現在画面を確認 |
| 整理スレッドと書き込み終了 | U02で実行終了の観測方法を調べ、U09冒頭で整理の権限・終了・ファイル更新の扱いを確認 | U09の復元実装へ進む前。非同期待機の取消しだけを終了とみなさず、復元を開始できる条件を決定 |

「公開経路の調査」「採用版による限定的な接続確認」「製品フロー全体の検証」は別々に記録します。調査段階で必要経路が見つからなければ、期限まで隠して進めず、影響する要件と方式をその時点で見直します。未成立の機能を前提とする子工程は開始しませんが、U03の映像取得など独立した作業は進められます。SDKの不足を理由にアプリ独自の攻略継続ループへ変更する場合は、別の設計変更として扱います。

確認結果の詳細は各子仕様、確認済みの版と残る未確認事項は本書へ反映します。工程の依存関係と親仕様への入口は[05](05-implementation.md)を参照します。

<a id="ui-api-open"></a>

## UI合意に対応する確認済み範囲と未確認事項（2026-11-12）

画面と操作の採用要求は[10-ui-interaction.md](10-ui-interaction.md)に集約します。以下はU06・U07の依存する実装前に確認する事項であり、画面モックの成功で成立済みにはしません。

**確認済みの形式**: 同梱`codex-cli 0.167.0`の`app-server generate-json-schema`から、`TurnPlanUpdatedNotification`の必須項目が`threadId`・`turnId`・`plan`であること、`explanation`が省略可能かつstring/nullであることを確認しました。各計画項目は`step`と`status`を持ち、状態は`pending`・`inProgress`・`completed`です。項目IDはありません。[公式app-server資料](https://learn.chatgpt.com/docs/app-server#turn-events)でもエージェントが計画を公開・変更する通知として案内されています。形式の確認は、Goal継続中の通知頻度・順序・再開後の取得を実行確認した意味ではありません。

**現在の製品との隔たり**: 接続部は通常ターン用であり、GUIは質問カードと通常入力を分けています。回答検証は質問IDごとの値を要求し、選択式で自由回答不可の要求も扱います。通常送信の有効化だけでは共通入力や実行中の介入は成立しません。plan・reasoningの表示、Goal操作を既存GUIが実装済みとは扱いません。

**pause／clear／interruptの形式確認**: 0.167.0の実行ファイルで`app-server generate-json-schema --experimental`を実行し、`ThreadGoalSetParams`のstatusに`paused`、`ThreadGoalClearParams`のthreadIdとorigin、clear応答の`cleared`、`TurnInterruptParams`のthreadId・turnIdを確認しました。Goal変更のoriginには`user`／`automatic`があり、明示操作の出所を正しく伝える条件もU06で確認します。[Goals公式資料](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)はpause／resume／clearを区別し、[app-server公式資料](https://learn.chatgpt.com/docs/app-server#interrupt-a-turn)は進行中ターンの中断を別要求として定義しています。Codexデスクトップの各ボタンが内部で何を組み合わせるか、およびpause／clear単独が進行中推論を中断するかは未検証です。GIGASのpause／stopはGoal状態の変更と推論中断・実機入力遮断を合わせた製品操作であり、一つのAPI呼出しと同義にはしません。

| ID | 未確認事項と確認する契約 | 担当・判断期限 |
| --- | --- | --- |
| UI-A1 | `thread/goal/set/get/clear`等で、作成・既存目的の変更・一時停止・同一セッション再開・破棄をどう行うか。pause／clear／再開と実行中ターン、自律継続、未回答要求、未配送指示の関係。旧入力の終了・失効と現画面の配送。Goalなしの停止、pause中のメッセージも操作契約と対応付ける | U06 M0、M2のGoal制御実装前。保存参照からの復帰はU08 |
| UI-A2 | 実行中の`turn/steer`、質問要求への自由文回答、回答と追加指示を含む一文の配送。複数質問・自由回答不可・無関係な追加指示・承認待ちでの受理条件。ターン／Goal終了との競合、二重配送・意図しない次ターン開始を避ける条件 | U06 M0、M1の共通入力実装前に通常ターン経路を確認。Goal固有の条件はM2前に確認 |
| UI-A3 | `turn/plan/updated`の実通知順、空配列、説明文、ターン切替・停止・目的変更後の遅い通知、再接続時の取得可能範囲。前ターンの計画を現在扱いしないために必要な識別を確認する。Goal全体のTODOとの一致は要求しない | U06 M0で契約化、M1の表示結合までに確認。Goalとの対応はM2 |
| UI-A4 | `item/reasoning/summaryTextDelta`等の公開要約の有無、断片境界・完了・履歴取得、通常応答との順序。認証・モデル・設定による差。未提供なら空欄を埋めず、生の内部推論や別モデルによる生成で代替しない | U06 M0、M1のreasoning表示実装前 |
| UI-A5 | 実際のMCP等のツール通知からactivityとして表示できる名前・対象・結果と関連ID、開始／完了／失敗。まとめる場合の単位と根拠。固定の「移動・画面確認」や推測の操作回数で補わない | U04/U05の通知・記録を材料にU06 M0で選定、M1で実通知確認 |
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

