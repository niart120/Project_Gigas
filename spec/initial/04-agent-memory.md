# 04-agent-memory.md — Project・作業資料・ゲームI/O記録

[文書一覧](README.md)

本書はU08で採用する管理・保存・指示の契約の正本です。責務と引継ぎは[M0](../complete/unit_068/MANAGEMENT_STORAGE_DESIGN.md)、公開契約の根拠は[08](08-sdk-compatibility.md)に集約します。製品反映はM1〜M6、GUI・実機の通し受入はM7です。実装・受入状況は[U08親仕様](../complete/unit_013/RESUME.md)に集約します。

## 責務と保存先

app-serverがProject情報・会話所属・thread履歴・Goal・標準自動記憶を保持します。Codexが依頼の解釈、長期目標と途中指示の関係、攻略資料の内容・構成を判断します。GIGASはゲームI/O、停止、公開状態の表示、作業場所と記録先の接続を担当します。

```text
%LOCALAPPDATA%/GIGAS/
  settings.toml
  codex_home/                    Codex管理領域
  unassigned/
    <workspace-id>/              未所属会話ごとの作業場所
      攻略資料（構成自由）
      records/<thread-id>/
        events.jsonl
        observations/<observation-id>.png
利用者が指定したProjectフォルダー/
  攻略資料（構成自由）
  records/<thread-id>/
    events.jsonl
    observations/<observation-id>.png
```

専用homeは全Project・未所属会話に共通の一つとし、app-serverのCODEX_HOMEへ渡します。GIGAS側でChatGPTへログインし、開発用homeの認証・設定・記憶をコピーしません。独自の診断ログ保存先は設けず、既存の通知詳細・標準エラー出力・loggingを使います。Codexの内部ファイルは標準管理に任せます。

Projectは任意で、1 Project＝1作業フォルダーです。複数rootsは対象外です。新規作成では名前と親フォルダーを指定し、起動時cwdをピッカーの初期位置に使います。既存フォルダーの登録も可能とし、内容を移動・初期化しません。新規作成先が既存フォルダーと衝突した場合は上書きしません。ProjectのID・名前・実パスはapp-serverのProject情報を正本とし、独自の位置台帳や既定projectsルートは設けません。settings.tomlの前回選択はUIの補助情報です。配置先を変えても既存データを自動移動せず、初回cwdを既定保存ルートとして保存する仕組みは設けません。

未所属では初回の明示送信時にランダムなworkspace-id（UUID）を採番して作業フォルダーを作り、その絶対パスをthread/startのcwd・runtimeWorkspaceRootsへ渡します。workspace-idは会話作成前の配置名で、threadの別正本ではありません。以後はthread/readのcwdから場所を解決し、パス対応表・親フォルダー走査・作業領域のProject昇格を追加しません。Project所属ならProjectの唯一のrootを使います。別会話の資料を自動共用させず、必要なときに通常のファイル参照で読める配置とします。

記録先は実際のthread IDが得られてから`records/<thread-id>/`に確定します。同じ会話は再接続・再起動で分割せず追記し、Project所属・未所属で内部構成を変えません。recordsだけで会話・資料・ゲーム状態を復元できるとは扱いません。バックアップ対象は作業フォルダー、専用home、settings.tomlを区別して案内し、バックアップ機能・稼働中の整合スナップショット保証は追加しません。

## 指示・Skills・設定

GIGAS共通指示はゲームI/Oの利用に必要な最小限の内容を同梱します。Python内の追記文も整理対象とし、共通の文面と会話ごとの値を分けます。攻略手順・資料様式・設計方針の説明をプレイ用指示へ入れません。guide.md・memory/current.md・notes/の雛形生成と固定入口は撤去します。

保存に関するdeveloperInstructionsは、作業フォルダーと、その下の`records/<thread-id>/`にevents.jsonl・`observations/<observation-id>.png`を置く構造、原記録を編集しないことだけを案内します。作業フォルダーはcwdで設定し、固定の攻略資料名・様式を指定しません。当該会話の正確な記録先パスを最初から伝えることは要件にしません。

GIGASはthread/startの応答でthread IDを保持し、実行前に確定した`records/<thread-id>/`を保存部品へ渡します。これはCodexへの案内とは別の処理です。パス案内のための追加API呼出し・指示差替え・MCP結果への保存先追加は行いません。通常のプレイでは観測結果の画像を利用し、保存画像が必要ならobservation_idを手掛かりに通常のファイル探索で探せます。直接のパス案内は実プレイで不足が確認された場合に検討します。非同期保存のためIDがあっても保存成功・即時参照を保証せず、画像索引や参照専用APIは追加しません。

Project指示はCodex標準のAGENTS.md等の探索に任せます。Skillsは作業場所・祖先側の.agents/skills、ユーザー共通の$HOME/.agents/skillsなど標準経路を使います。専用homeでもユーザー共通Skillsは共有されます。独自探索・複製・同期・許可台帳は設けません。現行のskills.include_instructions=falseとproject_doc_max_bytes=0を撤去し、標準読込みへ戻します。

| 設定 | 正本と採用値 |
|---|---|
| Web検索 | settings.toml。既定ON。ON=live、OFF=disabled |
| 標準自動記憶 | settings.toml。既定ON。生成・参照を一括切替し、OFFでも保存済み記憶を削除しない |
| 承認方法 | settings.toml。既定「代わりに承認」=auto_review、「自分で承認」=user。条件はon-request |
| モデル・推論強度 | 保存既定値と入力欄の選択を区別。新規・閲覧中は保持して明示実行のstart/resumeへ適用し、ロード済み実行対象はsettings/update受理後に次ターンへ反映 |

切替の具体的な適用経路は[08](08-sdk-compatibility.md)に定めます。進行中の会話への即時反映は要求せず、設定の双方向同期は設けません。標準自動記憶は攻略資料の編集とは別です。生成対象条件・アイドル時間・利用枠などはCodexに任せ、毎回の生成成功・即時生成・Project別の記憶分離を保証せず、生成待ちを開始条件にしません。

Apps・Plugins・Hooksは無効、notify=[]を維持します。質問・計画は有効、追加の外部MCP導入経路は対象外です。圧縮・出力量・処理優先度などの独自調整は追加しません。

作業フォルダーをworkspace-writeの通常書込先とし、Codex標準の保護対象は尊重します。別会話資料の読取りは標準の読取範囲で行い、独自ACLを設けません。シェルは有効、シェルのnetwork_access=falseを基本として例外は標準承認へ渡します。Web検索・ゲームMCPの通信は別に扱います。原記録を編集しない運用としますが、権限による強制保護は必須にしません。

## 保存部品と書込み

保存部品は指定先へのゲームI/O記録・PNG保存と書込み整合性を担当します。アプリが作業場所とthreadから記録先を決めて渡し、単独Python利用では呼出し側が保存先と開始・終了を決めます。架空のProject・thread・単独利用専用の共通保存先は設けません。

GameIoRecordWriterは指定された記録先を作成・開きます。独自Project metadataや固定の攻略資料を生成しません。単独利用でも同じ保存APIを使い、独立MCPサーバーの起動機能は提供しません。

JSONLは標準ライブラリで一行ずつ追記します。イベント連番、過去ログの全件検査、改行補完、破損行の修復、reloadによる復旧待ち、別記録先への自動切替は設けません。末尾破損で次の行も読めなくなる可能性を許容し、復旧は利用者が行います。今回の保存失敗を既存通知で伝え、次の新しい記録は通常どおり受け付けます。失敗した記録の自動再送はしません。

JSONL追記の直列化・writerの排他、PNGの上書き拒否、終了時のdrainを維持します。保存完了をMCP応答・次の入力や観測・停止の前提にしません。画像とイベントの部分失敗を隠さず、対応の自動補完や余剰画像削除を行いません。元のPNG bytesをMCPと保存で共用し、追加のサムネイル・ハッシュ・画像索引は作りません。

保持期限・容量上限・自動削除・ローテーション・記録削除UIは設けません。Projectや会話の管理操作と実ファイル削除を連動させません。利用者はGIGAS終了後に`records/<thread-id>/`を手動削除できます。画像だけの削除も禁止しませんが、参照検査・リンク修復は行いません。

作業フォルダーがありrecordsだけがなければ、次の明示実行で必要なものを作成します。閲覧だけでは作成しません。Project所属・未所属とも既存の作業フォルダー自体がなければ再作成せず、履歴閲覧は可能、実行時には見つからないパスを伝えて開始しません。利用者による元の場所への復元などで解決します。新規作成とは区別します。

旧形式・旧API・旧homeからの移行と互換層は設けません。これは設計上の制約撤回で、既存データの自動削除を意味しません。

## JSONL形式

新形式は`schema_version: 1`から開始する。旧形式との互換・変換・判別処理は設けず、版番号だけで旧記録と区別できるとは扱わない。版番号の採用を、過去ログの検査・移行機構を追加する理由にしない。

各イベントはUTF-8のJSON object一つを1行で保存し、末尾に改行を付ける。本文中の改行はJSON文字列内でエスケープする。共通項目は次の4項目だけとする。

| 項目 | 型・意味 |
|---|---|
| `schema_version` | 整数`1` |
| `recorded_at` | 行を組み立てた時刻。UTCのISO 8601文字列で末尾は`Z`。入力実行・保存完了時刻ではない |
| `type` | 次表の現行イベント名を使う文字列 |
| `payload` | 次表の項目を持つJSON object |

| `type` | `payload`の項目 |
|---|---|
| `dialogue.submitted` | `text`: ユーザーの提出本文（文字列） |
| `dialogue.message` | `text`: Codexの確定本文（文字列） |
| `input.operation_started` | `operation_id`: 操作ID（文字列）、`sequence`: 要求された入力操作の配列 |
| `input.operation_finished` | `operation_id`: 操作ID、`result`: 結果、`completed_steps`: 完了ステップ数（整数）、`input_sent`: 入力送信結果、`neutral`: 解除結果。結果に含まれる場合は`observation_id`、空でない場合は`phase`・`reason`（各文字列） |
| `observation.recorded` | `observation_id`: 観測ID、`captured_at`: 元フレームのPC取得時刻（UTC文字列）、`image_path`: 記録先からの相対パス、`image`: 出力寸法、`source_frame`: 元寸法 |

`dialogue.submitted`は後述の本文取得元に対応するユーザー本文だけに限定し、同名の現行イベントに含まれるモデル変更・停止・質問フォーム回答・承認操作などは残さない。`dialogue.message`は進捗報告を含む確定本文だけとする。両者とも`payload.text`へ本文を直接置き、従来の`content`の包みや`role`・`kind`・`partial`・`received_at`、request・item等の管理用IDは残さない。受付成功の照合や最終回答の選別は追加しない。

入力結果の値は現行の表現を使う。`result`は`completed`／`interrupted`／`failed`、`input_sent`は`none`／`sent`／`unknown`、`neutral`は`not_needed`／`sent`／`unconfirmed`とする。`completed_steps`は解除送信または待機を完了したステップ数であり、ゲーム内の成功数ではない。送信結果を機器への物理的到達の保証にはしない。任意項目がない場合はキーを省略し、保存側で`phase`・`reason`の分類・推定を追加しない。入力完了後の観測に失敗・取消しが生じた場合、`result: "completed"`と`phase: "observe"`・理由が併存し得る。

入力操作列`sequence`は現行の`InputStep.to_json()`の表現を使う。各操作は`kind`（`press`／`wait`）と`duration_ms`（正整数）を持つ。`press`では`buttons`（文字列配列）を残し、中立でない`left_stick`・`right_stick`、押下中の`touch`は`x`・`y`の整数を持つobjectとして残す。`wait`は種別と時間だけとする。これはイベント共通項目から削除する連番`sequence`とは別である。

観測の`image`・`source_frame`はそれぞれ整数の`width`・`height`だけを持つ。`image_path`は`observations/<observation-id>.png`とし、区切りには`/`を使う。同じPNG bytes・IDによる対応を維持する。MCP側のMIME指定や取得サービス内部の状態管理の変更は本JSONL形式の変更に含めない。

全イベントで独自`project_id`・`event_id`、行ごとの`thread_id`・`turn_id`、イベント連番を保存しない。行順は追記順であり、観測行が入力結果の行より先に残る場合もある。操作・観測はIDで対応付け、行の隣接関係や時刻から推定しない。通常記録に評価用のモデル・開始条件・到達点などの項目を追加しない。

以下は形式例であり、IDを説明用に短縮している。実記録では既存の生成済みIDを使う。

```jsonl
{"schema_version":1,"recorded_at":"2026-11-14T03:00:00Z","type":"dialogue.submitted","payload":{"text":"サファリゾーンでラッキーを捕獲してください"}}
{"schema_version":1,"recorded_at":"2026-11-14T03:00:01Z","type":"input.operation_started","payload":{"operation_id":"op-1","sequence":[{"kind":"press","duration_ms":100,"buttons":["A"]},{"kind":"wait","duration_ms":500}]}}
{"schema_version":1,"recorded_at":"2026-11-14T03:00:02Z","type":"observation.recorded","payload":{"observation_id":"obs-1","captured_at":"2026-11-14T03:00:01.900Z","image_path":"observations/obs-1.png","image":{"width":1280,"height":720},"source_frame":{"width":1920,"height":1080}}}
{"schema_version":1,"recorded_at":"2026-11-14T03:00:02.100Z","type":"input.operation_finished","payload":{"operation_id":"op-1","result":"completed","completed_steps":2,"input_sent":"sent","neutral":"sent","observation_id":"obs-1"}}
{"schema_version":1,"recorded_at":"2026-11-14T03:00:03Z","type":"dialogue.message","payload":{"text":"現在の画面を確認しました。サファリゾーンへ向かいます。"}}
```

## 本文取得元と利用の限界

ユーザー本文は現行ConversationExecution.send・set_goalの提出イベントから保存します。RPC受付結果との照合は追加せず、提出を受理成功とは扱いません。Codex本文はDialogueEvent(kind="message", partial=False)から、進捗報告を含めて保存します。ターン最後の応答の選別、delta・未完本文の再構成はしません。

質問フォーム回答・承認操作、公開推論要約・Plan・activity・Goal状態・接続状態などの管理イベントはJSONLへ残しません。履歴表示はapp-serverを正本とし、この限定本文から会話を再構成しません。記録の用途は攻略資料の材料、画像の参照、入力結果の確認です。Codexの文章をゲーム内成否の確認済み事実に置き換えません。

recorded_atは行の組立時刻、captured_atは元フレームのPC取得時刻です。入力受付・結果確定など新しい観測点は追加しません。captured_atをゲーム内部やカメラの撮影時刻とは扱いません。
