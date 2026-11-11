# Codex実接続の確認

`scripts/check_codex.py` は製品の接続部を使い、認証済みのCodexランタイムへ短い依頼を送ります。モデルの利用量が発生するため、通常のpytest・CIには含めません。先にChatGPT認証を完了してください。APIキー方式には対応していません。

開発リポジトリとは別の作業フォルダーを指定します。次のコマンドはフォルダーがなければ作成します。

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
| `interrupt` | 質問を保留したまま中断し、遅い回答を拒否してターンの終了を受け取る |
| `approval` | 無害な出力コマンドの承認要求を受け取り、提示された判断に従って拒否または取消しを送る。拒否後の応答、または取消し後のターン終了を受け取り、許可は送信しない |
| `file-approval` | 検証ファイルの追加承認を受け取り、拒否または取消しを送る。検証ファイルが作成されていないことを確認する |

質問・承認は2秒保留してから回答・中断します。すべてのシナリオで最後に接続を閉じ、接続用asyncioタスクが残っていないことを確認します。結果は本文や資格情報を含めないJSONで出力します。要求されたツールをモデルが使わなかった場合や、応答が期限内に届かない場合は失敗になります。自動再送はしません。

標準のCodex homeを使わずに確認する場合は、認証済みの別homeを `--codex-home` で指定します。このスクリプトは資格情報をコピーせず、ログイン操作も実行しません。

この確認は接続部を対象とします。GUIの表示・操作、ブラウザでの新規ログイン、ゲーム機器、APIキー、Goalsの動作は確認しません。
