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

2026年11月14日のU08以前の構成では、app-server `0.167.0`、`gpt-6.1-sol`・effort `low`、既存のChatGPT認証で、通常応答、元要求IDへの質問回答、質問保留中の共通停止・古い回答の拒否・接続回収を確認しました。以下のGoal・実機結果も同日の確認であり、公開Projectと会話別recordsへ変更した現在の保存経路の受入結果ではありません。

別の検証プログラムでは、Goal作成、目的を保持したpausedへの停止、pausedのまま通常指示、目的を変更しないactiveへの再開、turnを止めないGoal削除、その後の共通停止を確認しました。終了時の残存task・記録エラーはありませんでした。

実機ではCOM8のCH552・9600 baudとUSB Video Deviceを使い、実モデルから同じstateless MCPを経由したゲーム操作と停止を確認しました。RIGHTの500 ms要求を265 msの押下で中断し、neutral送信から約47 ms後に停止結果が返りました。停止後の映像で右方向への移動を確認し、その後の観測でも位置を維持しました。同じMCP入口で次のobserveを受け付け、画像・操作結果をarchiveへ保存できています。一回のRIGHTだけを許す制限は、この実機検証プログラムに限ったものです。

この実機確認は製品のサービス部品を組み合わせたPythonプログラムで行い、GUIから実機までを通した操作ではありません。GUIから公開stdioへの配送、Qtから実HTTP MCP・模擬入力への接続は同時点の自動試験で確認しました。検証の詳細と境界は[unit_061](../spec/complete/unit_061/CONVERSATION_EXECUTION_MODEL.md)・[unit_062](../spec/complete/unit_062/GOAL_EXECUTION_MODEL.md)を参照してください。

2026年11月15日の[M1](../spec/complete/unit_069/MANAGEMENT_API.md)では、専用homeでの本人ログイン、Windows sandboxの準備とApp Server再接続、GUIからの通常応答・生成中の停止・停止後の送信を確認しました。この時点では記録は旧形式です。

同日の[M2](../spec/complete/unit_070/RECORDING_AND_NEW_CONVERSATION.md)では、製品GUIから未所属の通常送信、Projectの新規作成、Goal開始・生成中停止・停止後の通常送信と正常終了を確認しました。公開Project root・会話cwd・recordsの実thread IDが一致し、Goalは目的を保持してpausedとなりました。提出本文と確定応答を保存し、中断した未完本文は補完保存していません。

実Codexと64×48の模擬Frameを使った製品サービスの確認では、observe 1回のMCP応答と保存PNGの168 bytes・観測IDが一致しました。進捗を含む確定本文2件、提出1件、観測1件が新形式で残り、保存エラーなく停止・回収・終了しました。これは実機映像・入力の確認とは区別します。

公開Projectと会話別recordsに対応させた本スクリプト自体は、CLI・静的検査までを確認し、実モデルでは未実行です。GUIから実機までの通し受入は[U08](../spec/wip/unit_013/RESUME.md)のM7で確認します。他の入力機器・OS、長時間運転は未検証です。APIキー認証は対象外です。
