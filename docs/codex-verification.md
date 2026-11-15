# Codex実接続の確認

`scripts/check_codex.py` は製品の通信接続・会話実行・記録資源を組み合わせ、認証済みのCodexランタイムへ短い依頼を送ります。モデルの利用量が発生するため、通常のpytest・CIには含めません。先にChatGPT認証を完了してください。APIキー方式には対応していません。

認証済みでWindows実行環境がreadyのCodex homeを使い、開発リポジトリとは別の作業フォルダーを指定します。コマンドは`--cwd`のフォルダーがなければ作成し、各試行で`<cwd>/checks/<UUID>`を新規作成して公開APIでProjectへ登録します。threadの作業場所・通常の書込先はそのフォルダー、ゲームI/Oの記録先は配下の`records/<thread-id>/`です。検証用Projectと作業フォルダーは終了後も残します。

```powershell
uv run python scripts/check_codex.py ordinary --cwd "$env:TEMP\gigas-codex-check" --codex-home "$env:LOCALAPPDATA\GIGAS\codex_home"
uv run python scripts/check_codex.py question --cwd "$env:TEMP\gigas-codex-check" --codex-home "$env:LOCALAPPDATA\GIGAS\codex_home"
uv run python scripts/check_codex.py interrupt --cwd "$env:TEMP\gigas-codex-check" --codex-home "$env:LOCALAPPDATA\GIGAS\codex_home"
uv run python scripts/check_codex.py approval --cwd "$env:TEMP\gigas-codex-check" --codex-home "$env:LOCALAPPDATA\GIGAS\codex_home"
uv run python scripts/check_codex.py file-approval --cwd "$env:TEMP\gigas-codex-check" --codex-home "$env:LOCALAPPDATA\GIGAS\codex_home"
```

| シナリオ | 確認すること |
|---|---|
| `ordinary` | 指定した応答文字列を受信し、通常ターンが完了する |
| `question` | モデルからの質問に回答し、その後の応答と要求の失効を受け取る |
| `interrupt` | 質問を保留したまま中断し、停止処理の完了とターン終端を確認し、遅い回答を拒否する |
| `approval` | 無害な出力コマンドの承認要求を受け取り、提示された判断に従って拒否または取消しを送る。拒否後の応答、または取消し後のターン終了を受け取り、許可は送信しない |
| `file-approval` | 作業場所の一つ上に`gigas-u02-approval-<UUID>.txt`を追加する承認を受け取り、拒否または取消しを送る。要求した同じ絶対パスにファイルが作成されていないことを確認する |

質問・承認は2秒保留してから回答・中断します。すべてのシナリオで最後に接続を閉じ、接続用asyncioタスクが残っていないことを確認します。結果は本文や資格情報を含めないJSONで出力します。共通指示・記録先・MCPの準備と終了も通りますが、これだけでゲーム操作の受入とはしません。要求されたツールをモデルが使わなかった場合や、応答が期限内に届かない場合は失敗になります。自動再送はしません。

上の例はGIGAS既定の専用homeを明示します。設定ファイルの配置を変えている場合は、その親のcodex_homeを指定してください。`--codex-home`を省略するとCodexランタイムの既定homeを使います。このスクリプトは資格情報をコピーせず、ログイン操作も実行しません。

この確認は通常指示の製品経路を対象とします。結果JSONの`turn_status`は公開終端、`stop_completed`は停止APIの完了、`archive`は残した記録先です。GUIを使わず、映像・入力機器も接続しません。ブラウザでの新規ログイン、APIキー、Goalsの動作も確認しません。

## 確認済みの範囲と未確認事項

2026年11月15日、app-server `0.167.0`、`gpt-6.1-sol`・effort `low`、GIGAS専用homeのChatGPT認証で、次の範囲を確認しています。

| 対象 | 確認済みの範囲 | 根拠と限界 |
|---|---|---|
| 認証と新規会話 | 本人ログイン、Windows sandbox準備後のApp Server再接続、Project/未所属の開始、共通停止、会話別records | [M1](../spec/complete/unit_069/MANAGEMENT_API.md)、[M2](../spec/complete/unit_070/RECORDING_AND_NEW_CONVERSATION.md)。観測保存は模擬Frameを使用 |
| 指示・資料・ツール | 標準AGENTS/Skills、作業資料の編集、別会話資料の参照、Web ON/OFF、通常シェルの通信拒否、両承認方法 | [M3](../spec/complete/unit_071/EXECUTION_SETTINGS_AND_INSTRUCTIONS.md)。ゲーム操作なし |
| 会話選択と再開 | 公開履歴・Web activity、Project/未所属の新規、既存再開、Goal再開と停止、停止後切替、再接続時の履歴・原稿保持 | [M4](../spec/complete/unit_072/CONVERSATION_SELECTION_AND_RESUME.md)、[M5](../spec/complete/unit_073/CONVERSATION_MANAGEMENT_UI.md)。実GUI・実モデル。実データの複数ページ履歴は未観測 |
| 設定と失敗後の操作 | 新規/閲覧時のモデル候補と実行後の変更、再開時の保存設定、追加履歴失敗、作業場所欠落、resume失敗、Goal拒否、通信断後の保持と明示再操作 | [M6](../spec/complete/unit_074/RESUME_SETTINGS_AND_FAILURES.md)。状態と故障はQt/模擬peer、設定保存から実resumeの権限・承認は実接続で確認 |
| 標準自動記憶 | ON時の公開参照、OFF時の参照停止と既存summary保持。Project所属・未所属とも実機MCP利用後の生成対象状態を維持 | [M6](../spec/complete/unit_074/RESUME_SETTINGS_AND_FAILURES.md)、[M7](../spec/complete/unit_075/PRODUCT_ACCEPTANCE.md)。実会話からの抽出は未観測。即時生成や有用性は保証しない |
| 実機と再起動 | Project/未所属の新規開始、実画像と有限入力、押下中の共通停止、モデル外入力後の再起動・履歴閲覧・明示再開、同じrecordsへの追記、映像未接続時の観測失敗と明示復帰 | [M7](../spec/complete/unit_075/PRODUCT_ACCEPTANCE.md)。USB Video Device、CH552・9600 baud。MCP入力と同じ入力サービスへのモデル外入力で画面上の移動を確認 |

再起動後の会話選択だけでは実行せず、明示再開後に現在のゲーム画面を観測しました。既存記録・画像を保持して同じ保存先へ追記し、映像未接続時の`DEVICE_UNAVAILABLE`では成功観測・PNGを追加しませんでした。

短い右方向入力で移動と後続の静止を確認しました。上方向入力では画面の反応を確認できない試行があり、原因は未特定です。押下中の共通停止では操作の中断と中立入力の送信を確認しましたが、機器側の受信確認や解除保証を示すものではありません。入力結果の`sent`だけでゲームへの反映を判定しません。

上記の製品受入は、このページ冒頭の`check_codex.py`の実行とは別経路です。現行スクリプト自体はCLI・静的検査まで確認し、実モデルでは未実行です。300秒の待機期限、解除ACK、writeの最悪時間、DTR/RTSによるreset、物理抜線・強制終了時の解除、他の入力機器・OS、長時間運転、モニター間DPI遷移は未検証です。APIキー認証は対象外です。
