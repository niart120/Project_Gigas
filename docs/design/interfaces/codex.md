# Codex App Serverとの接続

## 接続の範囲

GIGASは`openai-codex-cli-bin`に同梱されたCodexを`app-server --listen stdio://`で起動し、一つの標準入出力接続で公開RPCを利用する。採用バージョンは[pyproject.toml](../../../pyproject.toml)に固定する。

GIGASアプリは専用のCodex homeを使う。認証情報・会話・Project・標準自動記憶はCodexが管理する。GIGASの設定には資格情報を保存しない。現在の認証方式はChatGPTログインである。

接続は`initialize`と`initialized`、アカウント確認、モデル一覧取得を行う。ログインとWindows実行環境の準備は別の操作で、接続しただけでは会話を開始しない。未認証や無効な既定モデルでも接続を維持し、利用者がログイン・選択し直せる。

## 使用する会話・管理操作

| 操作 | GIGASでの用途 |
|---|---|
| Projectとthreadの管理API | 所属、作業場所、会話一覧、保存履歴、保存Goalの取得 |
| `thread/start` | 明示実行に伴う永続会話の新規作成 |
| `thread/resume` | 保存されたID・作業場所で実行を再開。履歴の閲覧とは別 |
| `turn/start` | 通常依頼と実行中の追加指示を提出 |
| `thread/goal/set`・`thread/goal/clear` | 目的の作成・編集・再開・削除と、共通停止時の休止 |
| `thread/settings/update` | ロード済み会話の次回以降のモデル・推論レベルを更新 |
| `turn/interrupt` | 実行中turnを中断 |

操作結果と通知を照合して実行状態を反映する。送信の受付応答は、指示への遵守やゲームへの入力反映を示すものではない。Goal変更は対応する公開通知を確認し、遅い応答によって新しい通知の値を巻き戻さない。

通常依頼、Goal、停止・再開の利用上の意味は[会話機能](../functions/conversations.md)、公開Python APIは[Codex API](codex-api.md)にまとめる。

## threadの実行設定

新規開始と再開には、作業場所、現在のモデル・推論レベル、準備開始時の実行設定、GIGASが開いたゲームMCPの接続先を渡す。開始・再開の応答ではthread ID、Project所属、作業場所、モデル、承認方法、sandboxの実効値を照合する。

| 項目 | 現在の設定 |
|---|---|
| 作業領域 | `cwd`と`runtimeWorkspaceRoots`に会話の作業フォルダーを指定 |
| 書込みと通信 | `workspace-write`。追加書込先は空、シェルのネットワークは無効、一時領域は通常書込先から除外 |
| 承認 | `on-request`。審査者は`auto_review`または`user` |
| Web検索 | 設定に応じて`live`または`disabled` |
| 標準自動記憶 | 生成・利用をまとめて切替。OFFでも既存データは削除しない |
| MCP | 継承された外部MCP設定を無効化し、GIGASのゲームMCPを設定 |
| 指示 | 同梱`play.md`を`developerInstructions`へ渡す。標準のAGENTS・Skills探索も有効 |

Apps・Plugins・Hooksと外部通知は無効。Web・記憶・承認方法の変更は、次の新規開始・再開で適用する。同じロード済み会話のモデル・推論レベルは、設定更新の成功後に選択を確定する。

保存先は`records/<thread-id>/`の構造としてプレイ用指示に説明する。新規作成では実際のthread IDを得て記録先へ結び付けてから実行し、再開では要求前に既存IDと記録先を結び付ける。再開の応答前に届く通知やツール要求も扱う。

## 通知と回答要求

会話通知は対象threadを照合し、本文・活動は実行中turnに属するものを処理する。本文差分はitem単位で追記し、確定本文で置き換える。公開推論要約、Plan、コマンド・ファイル変更・MCP・Web活動は、それぞれ表示用の情報へ変換する。MCPのテキスト結果が`content`と`isError`を持つJSONで包まれている場合は、内側のテキスト内容を取り出す。

| 要求 | 回答の扱い |
|---|---|
| `item/tool/requestUserInput` | 質問IDに対応する回答を、元のRPC要求へ返す |
| `item/commandExecution/requestApproval` | コマンドの確認対象と提示された選択肢を表示し、判断を返す |
| `item/fileChange/requestApproval` | 変更対象と差分を表示し、判断を返す。確認対象の変更で要求を失効させる |

停止・切断中、別thread・別turnの要求、対応していない要求や形式は受け付けない。回答の配送は通常指示の送信とは独立し、停止・終端・解決通知で失効した要求への遅い回答は送らない。

## 失敗時の扱い

接続断では会話資源を閉じ、実行中の有限入力と保存処理を回収する。結果が不明な送信・再開・機器操作を自動再送しない。再開要求後に実効設定を確認できない場合は、その接続で実行を続けず、明示的な切断・再接続を求める。

利用枠の公開値は表示用であり、残量から実行可否を推定しない。利用制限・失敗はサーバーから受け取った状態として扱う。

実装: [起動](../../../src/gigas/runtime/codex/launch.py)、[会話要求と応答検査](../../../src/gigas/runtime/codex/_conversation_protocol.py)、[実行設定](../../../src/gigas/runtime/codex/_execution_settings.py)、[通知変換](../../../src/gigas/runtime/codex/_notifications.py)。
