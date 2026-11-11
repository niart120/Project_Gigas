# Codex接続とGUI寿命の結合 仕様書

## 1. 概要

U02のM2子仕様。公開app-server接続をGUIから明示的に開始し、通常ターン、質問・承認、中断、切断を操作する。状態はwip。接続部の実装とGUI受入を混同しない。

起点は[親仕様](../unit_007/CODEX_CONNECTION.md)、[M1](../unit_017/CODEX_DIRECT_CONNECTION.md)、[実行基盤](../../initial/02-runtime.md)、[制御フロー](../../initial/09-control-flows.md)。既存の設定保存・終了契約は[U01](../../complete/unit_004/APPLICATION_SHELL.md)に従う。

| 利用者の入力・状態 | 期待する観測結果 | 制約 |
|---|---|---|
| 接続を押す | 保存成功済みの設定で接続し、認証・モデルの結果を表示する | 起動時の自動接続なし |
| 質問・承認待ち | Codex由来の内容を読み、明示回答する。待機中も中断できる | 自動承認・古い要求への再回答なし |
| アプリ終了 | ターンを止め、設定I/Oと接続サービスの終了を待つ | GUIを停止せず、終了取消しでもターンを再開しない |

## 2. 対象範囲

専用QThread内のasyncioループ、接続の生成・呼出し・終了、型付きイベントのGUI配送、ConnectionPanel、ApplicationShellの終了合流、利用手順を実装する。

## 3. 対象外

Goal、実機操作、プロジェクト記録、実行中の追加指示、APIキー認証、資格情報の独自保存は含めない。M1の公開接続APIやtransportはこの子仕様で変更しない。

## 4. 前提・設計判断

M1 C01〜C03の型付きイベントと寿命APIは実装済み。実runtime受入は親で並行して行う。M2の模擬試験だけで実接続を成功扱いにしない。

接続factoryと全async操作をサービススレッドで実行する。GUIは不変な設定snapshotと操作を渡し、queued signalで結果を受け取る。長いopen/sendを待つ間もstop/closeを別taskで受け付ける。QThread.quitではasyncioは停止しないため、明示shutdownで所有task・接続を回収してrunを終える。

## 5. 振る舞い仕様

| 境界 | 契約 |
|---|---|
| 設定 | 接続時点のApplicationShell.baselineを固定する。未保存編集は使わず、接続中の保存は次回接続へ適用する |
| 作業場所 | 設定ファイルの親のruntimeディレクトリをサービス側で作成する。開発repoを暗黙のcwdにしない |
| 認証 | 接続時は既存認証readのみ。ログイン操作でURLを表示し、別の明示操作でブラウザを開く。認証待ち終了でURLを消す |
| 本文 | item_idごとにdelta追記、replace=Trueで確定置換する。Codexとユーザーの本文はplain text表示する |
| 人間要求 | tokenでカードを識別し、全質問・選択肢・許可された承認判断を表示する。回答直後に再操作を閉じ、RequestResolved、停止、切断で失効する |
| 接続世代 | 古い接続のqueued通知・操作結果は現在の画面へ適用しない。RPC IDのstring/int変換はGUIで行わない |
| 終了 | アプリclose要求で先に中断する。設定確認の取消し・保存失敗でも再開しない。終了確定後は設定QThreadとサービスQThreadの両wait(0)が成功して一度だけstoppedを通知する |
| 途中終了 | サービスだけのfinishedはアプリ終了を意味しない。committed guardで保護する。待機理由を表示し、Qtを動かしたまま待つ |

GUIには項目名・短い状態・エラー理由とCodex/ユーザー本文だけを置く。アプリ独自の説明本文・説明ツールチップはdocsへ置く。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | G01 接続生成・操作を専用ループで行い、待機中の中断と終了で所有処理を回収する | new / edge | integration | 起動時未接続、GUI pulse、部分失敗、shutdown先着 |
| refactor-skipped | G02 保存済み設定で接続し、本文・認証・通常送信・中断・切断を画面から扱う | new | integration | URLは明示open、delta/replace、失敗時入力保持 |
| refactor-skipped | G03 質問・承認をtokenの有効期間に一度だけ回答する | new / edge | integration | 複数質問、秘密入力、選択肢、承認、失効と旧世代 |
| refactor-done | G04 設定と接続の両終了までGUIを動かし、取消し後もターンを再開しない | regression / edge | integration | 両完了順、設定失敗、早期finished、wait(0) |

## 7. 設計メモ

CodexServiceThreadは接続所有とasyncio寿命、ConnectionPanelは表示・入力、ApplicationShellは設定snapshotとアプリ寿命を所有する。サービス結果には接続世代・操作名・秘匿化済みエラーを付ける。予期しない例外本文や認証内容をGUIへ転記しない。

## 8. 対象ファイル

`src/gigas/services/`、`src/gigas/ui/connection_panel.py`、`src/gigas/ui/main_window.py`、`src/gigas/app/shell.py`、対応する結合試験、`docs/codex-gui.md`、`README.md`、本書と親仕様。

## 9. 検証

計画: 各itemのred/green、対象ruff/ty、既存U01回帰を行う。公開API変更のためuv lock --check・uv build、最終のunit/integration試験を実行する。GUI実画面と実runtimeでの接続・送信・質問回答・中断・終了は親の受入で確認する。

実績: 仕様具体化のみ。製品実装・自動試験・GUI実runtime確認はnot run。

### G01 実行履歴

redは`gigas.services`の未実装によるImportError。greenは専用loopでfactoryを実行し、送信待機と並行してstopを処理し、shutdownで接続・task・loopを回収して2 passed。shutdown先着時はfactoryを呼ばず、factory失敗の生例外本文をGUIへ出さないことも確認した。対象ruff format/check・tyが成功。refactor-skipped: command配送とasync操作を分離済みで、追加の構造変更は不要。公開型境界とGoogle style docstringを照合し、サービス契約をdocsへ記載した。

### G02 実行履歴

redはConnectionPanel未実装によるImportError。greenは接続操作・認証URLの明示openと破棄・本文delta/replace・送信失敗時の入力保持・保存済みsnapshotを実装して新規2件と既存終了11件がpass。アプリへのservice組込に伴い、基本の両スレッド終了待ちを先行実装した。取消し時の中断契約と終了競合はG04で検証する。対象ruff/ty成功。refactor-skipped: 表示・操作・shellの設定snapshotを分離済み。実画面の読み取り確認と実runtimeは親の受入で行う。

### G03 実行履歴

redはanswer_requested未実装により新規6件が失敗。greenは質問ごとの入力、許可された承認判断、token単位のカードと不可逆失効を実装しGUI8件がpass。回答の重複、旧世代通知、RequestResolved・停止・切断・終端後の遅いクリックを確認した。秘密入力はマスクし、提出・失効時に消去する。対象ruff/ty成功。refactor-skipped: QuestionInputとRequestCardを分離し、ConnectionPanelは世代・失効のみを管理するため追加整理は不要。実runtimeでの要求提示・回答は親の受入へ残す。

### G04 実行履歴

redは終了要求が設定保存・未保存確認の前に中断しない3件と、両終了の待機対象を区別しない2件が失敗。greenは中断をclose受付時へ移し、終了取消し・保存失敗後の再開を防いだ。両QThreadのwait(0)、stoppedの一回通知、GUI pulse、service早期終了、stop例外時にもcloseする経路を確認した。

独立レビューの「操作失敗が次の再描画で接続中に戻る」を採用し、factory失敗相当とopen失敗相当の2件をredから修正した。エラーは保持し、明示切断の成功まで再接続を許可しない。対象30件pass、ruff/ty成功。refactor-done: 予期しないservice終了の表示と、終了確定後の合流を別slotへ分離した。設定の既存11件を維持した。

## 10. 先送り事項

Goal・プロジェクト・実機入力は親仕様のU04〜U06へ引き継ぐ。実行中の追加指示はU06。APIキー認証は親の期限を維持する。

## 11. チェックリスト

- [x] 対象範囲と対象外を確認した
- [ ] TDD Test Listを完了した
- [ ] 型・docstring・既存回帰・配布gateを記録した
- [ ] 実画面と実runtime受入の結果・未検証範囲を親へ引き継いだ
