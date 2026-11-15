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

## ゲームを使った確認

上のスクリプトはGUI・映像・入力機器を使いません。ゲーム操作はGIGASを起動し、使用する映像と入力機器を設定して確認します。操作手順は[Codex利用ガイド](codex.md)、次に取り組む範囲は[開発計画](../roadmap.md)を参照してください。

[2026-11-15の確認記録](../../spec/archive/verification/codex-2026-11-15.md)には、実GUI・実モデル・CH552で行った短時間の確認と、その限界が残っています。同記録では`check_codex.py`自体の実モデル実行は未実施であり、別経路の製品受入結果と区別しています。
