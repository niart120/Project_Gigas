# Dev Journal

Project_Gigas の設計観測、未解決事項、先送り判断の記録。

仕様書へ昇格できる粒度になったら、未着手は `spec/planned/unit_XXX`、着手する作業は `spec/wip/unit_XXX` へ移す。

## 2026-11-11: U02完了後の接続・設定・操作レビュー

### 現状

U02完了時の実装（`79a7947`）を対象に、認証・設定と会話操作の結合をレビューした。製品コードは変更していない。ログイン・接続とモデル・推論レベルを分ける追加要求は[02の契約](initial/02-runtime.md)、[U06](planned/unit_011/GOAL_EXECUTION.md)、[T21](initial/07-verification.md)へ反映した。以下はそれとは別に再現した問題と設計候補で、修正・実接続検証は未実施。

### 観察

| ID・優先度 | 再現条件と観測結果 | 根拠 |
| --- | --- | --- |
| R01・P2 | 設定正常読込→Codex実行中→設定再読込失敗で、実行状態は`running`のまま中断・切断ボタンが有効から無効になる。アプリ終了経由の中断は残る | [shell.py:169](../src/gigas/app/shell.py#L169)の`ready=False`と[接続パネルの操作条件](../src/gigas/ui/connection_panel.py#L287)。設定結果を注入した実Qtパネルで確認 |
| R02・P2 | 初回送信の`thread/start`を保留して中断後に応答を返すと、`turn/start`未送信でも入力欄が空になる。処理終了が送信成功として扱われる | [connection.py:333](../src/gigas/runtime/codex/connection.py#L333)の早期return→[サービス完了通知](../src/gigas/services/codex.py#L241)→[入力消去](../src/gigas/ui/connection_panel.py#L225)。FakePeer・実サービス処理・実Qtパネルで確認 |
| R03・P2 | 未認証で一覧にないモデル、または未対応エフォートを指定すると、認証待ちより先に接続を閉じる。`login()`も拒否され、`account/login/start`は0回 | [connection.py:258](../src/gigas/runtime/codex/connection.py#L258)のモデル照合。FakePeerで両条件を確認。無効な実行設定からログインへ進めない |
| R04・P2（復帰導線） | `turn/completed(status=failed)`後、認証済みでpeerを閉じていないのに`phase=error`となり、明示的な次の送信も拒否する。GUIからは切断・再接続が必要 | [ターン終端処理](../src/gigas/runtime/codex/connection.py#L664)と[送信条件](../src/gigas/runtime/codex/connection.py#L319)。FakePeerで次送信拒否と`turn/start`が1回のままであることを確認 |
| R05・P3 | 100行の応答表示を先頭へ戻して文字選択中、応答断片が届くと末尾へ移動し、選択も空になる | [connection_panel.py:186](../src/gigas/ui/connection_panel.py#L186)の全文置換と末尾移動。実Qtでスクロール`0→81（末尾）`、選択`Codex→空`を確認 |

別の設計候補として、Codex homeの認証を共有する現方式では、GUIに使用アカウントの識別情報がない。[launch.py](../src/gigas/runtime/codex/launch.py)はランタイム既定のhomeを維持し（`CODEX_HOME`指定時はその環境を継承）、[account/read処理](../src/gigas/runtime/codex/connection.py#L538)は認証有無だけを保持する。これは静的確認した現仕様の制約であり、誤アカウント利用を再現したものではない。識別表示、共有範囲、切断とログアウトの違い、切替の提供範囲をU06 M0の判断事項とした。

### 方針

R01は設定の健全性と既存セッションを止める操作を分離する。[U05](planned/unit_010/INPUT_AND_STOP.md)の実機入力公開前に修正・確認する条件を追記した。R02は送信処理の終了と入力受付を区別し、受理前の中断で原稿を残す。R04は接続障害とターン結果を区別し、終端と通信継続を確認できた場合の明示的な次ターンを設計する。自動再送や受付不明の解消を推測で行わない。R02・R04は次の接続・会話画面拡張前の独立した修正候補とする。

R03はU06/T21の認証・設定分離へ含める。R05は末尾を見ている場合だけ追従し、読み返し位置と選択を保持する修正候補。U06の会話表示を拡張する前に扱う。今回はレビューと文書反映に限り、修正用の子仕様を先行作成しない。2担当の指摘5件と認証の設計候補を採用し、重複する「再接続で会話を作り直す」指摘はR04の影響としてまとめた。

検証は認証不要の模擬peerと画面表示なしの実Qtを使用した。`uv run python -X utf8 tmp/review_auth_settings.py`、`uv run python tmp/review_interaction_probe.py`が終了コード0で問題を再現。driverはローカルの無視対象`tmp/`に置き、上表に再現条件を残した。実ランタイム・新規ログイン・物理機器での再現は未実施。文書のみの変更のため製品全体のpytest・型検査・ビルドは対象外。`uv run python tmp/u02_check_links.py`でローカル参照333件・unit18件の一意性、`git diff --check`で空白不備なしを確認した。変更文面・未検証表示・仮テキストは読取確認し、独立した差分レビューで指摘されたhomeの環境継承も明記した。

## 2026-11-11: R01修正と残項目の担当確定

### 現状

前項のR01を[unit_019](complete/unit_019/SESSION_STOP_AVAILABILITY.md)で修正した。設定障害中の中断・切断と新規接続禁止をQtで確認し、全146テストが成功。

### 観察

前項のR02・R04・R05はジャーナル内の候補に留まり、親仕様への埋め込みが不足していた。

### 方針

R02・R04はU04の接続・会話画面拡張前、R03はU06/T21、R05はU06/M2へ親仕様で割り当てた。使用アカウントと認証共有の設計はU06/M0。R01の物理入力との結合確認はU05で行う。今回未修正の項目を修正済みとは扱わない。

