# Codex実接続の確認

`scripts/check_codex.py` は製品の通信接続・会話実行・記録資源を組み合わせ、認証済みのCodexランタイムへ短い依頼を送ります。モデルの利用量が発生するため、通常のpytest・CIには含めません。先にChatGPT認証を完了してください。APIキー方式には対応していません。

認証済みでWindows実行環境がreadyのCodex homeを使い、開発リポジトリとは別の作業フォルダーを指定します。次のコマンドはフォルダーがなければ作成し、各試行の記録先を`<cwd>/checks/<UUID>`へ残します。threadの書込先はその`memory/`に限定します。

```powershell
uv run python scripts/check_codex.py ordinary --cwd "$env:TEMP\gigas-codex-check"
uv run python scripts/check_codex.py question --cwd "$env:TEMP\gigas-codex-check"
uv run python scripts/check_codex.py interrupt --cwd "$env:TEMP\gigas-codex-check"
uv run python scripts/check_codex.py approval --cwd "$env:TEMP\gigas-codex-check"
uv run python scripts/check_codex.py file-approval --cwd "$env:TEMP\gigas-codex-check"
```

| シナリオ | 確認すること |
|---|---|
| `ordinary` | 指定した応答文字列を受信し、通常ターンが完了する |
| `question` | モデルからの質問に回答し、その後の応答と要求の失効を受け取る |
| `interrupt` | 質問を保留したまま中断し、停止処理の完了とターン終端を確認し、遅い回答を拒否する |
| `approval` | 無害な出力コマンドの承認要求を受け取り、提示された判断に従って拒否または取消しを送る。拒否後の応答、または取消し後のターン終了を受け取り、許可は送信しない |
| `file-approval` | 検証ファイルの追加承認を受け取り、拒否または取消しを送る。検証ファイルが作成されていないことを確認する |

質問・承認は2秒保留してから回答・中断します。すべてのシナリオで最後に接続を閉じ、接続用asyncioタスクが残っていないことを確認します。結果は本文や資格情報を含めないJSONで出力します。共通指示・記録先・MCPの準備と終了も通りますが、これだけでゲーム操作の受入とはしません。要求されたツールをモデルが使わなかった場合や、応答が期限内に届かない場合は失敗になります。自動再送はしません。

標準のCodex homeを使わずに確認する場合は、認証済みの別homeを `--codex-home` で指定します。このスクリプトは資格情報をコピーせず、ログイン操作も実行しません。

この確認は通常指示の製品経路を対象とします。結果JSONの`turn_status`は公開終端、`stop_completed`は停止APIの完了、`archive`は残した記録先です。GUIを使わず、映像・入力機器も接続しません。ブラウザでの新規ログイン、APIキー、Goalsの動作も確認しません。

## 確認済みの範囲

2026年11月14日、app-server `0.167.0`、`gpt-6.1-sol`・effort `low`、既存のChatGPT認証で、通常応答、元要求IDへの質問回答、質問保留中の共通停止・古い回答の拒否・接続回収を確認しました。新しい通常送信・停止経路の検証であり、過去版の結果を転用したものではありません。

別の検証プログラムでは、Goal作成、目的を保持したpausedへの停止、pausedのまま通常指示、目的を変更しないactiveへの再開、turnを止めないGoal削除、その後の共通停止を確認しました。終了時の残存task・記録エラーはありませんでした。

実機ではCOM8のCH552・9600 baudとUSB Video Deviceを使い、実モデルから同じstateless MCPを経由したゲーム操作と停止を確認しました。RIGHTの500 ms要求を265 msの押下で中断し、neutral送信から約47 ms後に停止結果が返りました。停止後の映像で右方向への移動を確認し、その後の観測でも位置を維持しました。同じMCP入口で次のobserveを受け付け、画像・操作結果をarchiveへ保存できています。一回のRIGHTだけを許す制限は、この実機検証プログラムに限ったものです。

この実機確認は製品のサービス部品を組み合わせたPythonプログラムで行い、GUIから実機までを通した操作ではありません。GUIから公開stdioへの配送、Qtから実HTTP MCP・模擬入力への接続は自動試験で確認しています。最新の自動試験は802件成功。検証の詳細と境界は[unit_061](../spec/complete/unit_061/CONVERSATION_EXECUTION_MODEL.md)・[unit_062](../spec/complete/unit_062/GOAL_EXECUTION_MODEL.md)を参照してください。

今回の新構成では、新規ログイン、他の入力機器・OS、長時間運転を確認していません。APIキー認証は対象外です。
