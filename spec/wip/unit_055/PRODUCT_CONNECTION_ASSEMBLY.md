# 製品専用Codex接続組立 仕様書

## 1. 目的・状態・起点

状態: **wip（製品実装・局所検証済み、親review・commit待ち）**。[整理親仕様](../unit_054/KISS_SIMPLIFICATION.md)のM1最初の子作業として、製品で使用していない単独read-only接続の保証を廃止する。2026-11-13にユーザーがM0案とM1の実行を承認した。既存文書差分を保持した`codex/kiss-simplification-spec`で作業する。

Intent Delta: `prepare_turn`・`on_thread_started`を必須にし、準備した原記録先の`memory/`だけをCodexの書込対象にする。コールバック省略時の起動cwd利用、memoryなしのread-only送信、二回目の準備省略は提供しない。

利用場面はアプリの通常送信・Goal開始・同一Goal再開。接続・ログイン時には記録先を作らず、明示開始時にサービスが記録先・memory・MCPを準備する。単独API利用者の互換性は維持しない。

## 2. 対象範囲

接続・起動関数の準備コールバック、`ThreadConfiguration`、サービスの準備結果、対応する既存unit/integration fixtureと公開API説明を変更する。readinessの確認、memoryだけのsandbox要求と応答検証、同threadへのunsubscribe/resume、準備・記録失敗時の扱いを残す。

## 3. 対象外

停止・自然完了・通知寿命・サービス操作配送の整理、保存保証・入力検証、依存更新、認証方式追加、実runtime・実機の新規検証、PR・mergeを含めない。commitと完了判定は親担当が行う。停止と通知の整理は親の後続子へ分ける。

## 4. 関連文書・着手条件

- [親の採用設計](../unit_054/KISS_SIMPLIFICATION.md)
- [実行管理](../../initial/02-runtime.md)
- [記録とmemory](../../initial/04-agent-memory.md)
- [接続API](../../../docs/codex.md)
- [開発規約](../../../AGENTS.md)

親担当による関連正本仕様への採用内容の反映と実装開始通知を、製品・test変更の開始条件とする。子仕様と既存コード・試験の調査は先行できる。新しい外部APIへ依存せず、採用済みのworkspace-writeとreadiness経路へ統一する。

## 5. 振る舞い仕様

| 場面 | 結果 |
|---|---|
| 接続を組み立てる | `prepare_turn`と`on_thread_started`を必須のkeyword引数とする。作成・open・loginでは準備コールバックを呼ばない |
| 通常送信・新規Goal・明示再開 | 準備コールバックが返す`ThreadConfiguration(cwd, mcp_config)`を使う。cwdは準備済みの絶対パス、`cwd / "memory"`も準備済みである |
| threadの開始・再開 | threadのcwdとruntimeWorkspaceRootsを`cwd / "memory"`へ固定しworkspace-writeを要求する。追加書込先・一時領域・networkを許可しない |
| 初回thread確定 | 必須のon_thread_startedを呼び、成功後にthreadを採用する。以後は同じthreadへunsubscribe/resumeし、その都度準備設定を読み込む |
| readiness未完了・未準備 | 初回取得中はその結果を待つ。ready以外ならthread/Goalを送信せず、接続を維持して失敗を伝える。停止中なら送信しない |
| thread応答の設定不一致 | ID・model・承認設定・sandbox・memory cwdの不一致を拒否し、turn開始を行わない |
| 準備または初回対応記録の失敗 | 認証を失わず、明示再試行できる既存契約を残す |

## 6. TDD Test List

| status | item | type / layer | 既存試験の扱い |
|---|---|---|---|
| green | D01 必須の準備結果からmemoryだけを書込対象にし、次の通常送信も同じthreadで再準備する | behavior / unit | `test_open_validates_catalog_and_delivers_packaged_instructions`で、準備結果のmemoryを使わず原記録先をcwdにする失敗を観測後、製品変更で成功。既存fixtureも必須準備を渡す形へ揃えた |

回帰検証では、未準備・準備待ち停止・準備失敗・権限不一致で実行を開始しないことと、製品サービス・GUIの通常会話・Goalが必須準備を通ることを確認する。readiness・準備失敗・thread設定・サービス・GUI・shutdownの既存試験を使い、新しい振る舞いitemや防御経路は増やさない。

callback省略の失敗を同じ内容で大量に列挙しない。型検査と関数signatureが表す必須引数を、自然言語や内部構造の一致試験へ置き換えない。旧試験が本来確認していた認証・質問・停止は、小さい試験用準備を渡して継続確認する。

refactor review: 今回の枝削除と型縮小はD01の振る舞い変更に含まれる。green後に追加する構造変更は見送った。試験だけの小さい組立関数で準備済みmemoryを提供し、製品に代替モードを残していない。commitを含む最終状態への更新は親担当が行う。

## 7. 設計メモ

`ThreadConfiguration.memory_dir`を削除し、pathの二重指定と一致検査をなくす。準備・sandbox・権限応答の境界はそのまま使い、新しい設定型・準備manager・互換経路を作らない。製品側に試験専用read-onlyモードを残さない。

## 8. 対象ファイル

| path | 内容 |
|---|---|
| `src/gigas/runtime/codex/{connection,models,launch}.py` | 必須準備と固定memory書込先、API docstring |
| `src/gigas/services/codex.py` | ThreadConfigurationの準備結果だけを変更 |
| `tests/unit/test_codex_connection.py` | 準備fixtureと既存期待 |
| `tests/integration/test_codex_{service,gui,shutdown}.py`、`tests/integration/peers/codex_transport_peer.py` | 必要な組立・模擬応答の更新 |
| `docs/codex.md` | API signatureと準備・書込先の説明 |

## 9. 検証

局所pytestは`--basetemp=tmp/pytest-u055`を指定して他作業との一時領域共有を避ける。公開API変更のため型・docstring review、lock check、buildを含める。正本仕様は親担当が反映する。

| command / review | result | 範囲 |
|---|---|---|
| `uv run pytest tests/unit/test_codex_connection.py -q -k test_open_validates_catalog_and_delivers_packaged_instructions --basetemp=tmp/pytest-u055` | red: 1 failed / green: 1 passed（各162 deselected） | 期待するcwdがmemoryであるのに原記録先を返す失敗を先に確認 |
| `uv run pytest tests/unit/test_codex_connection.py -q --basetemp=tmp/pytest-u055` | 164 passed / 0.59s | 新旧接続契約の回帰。設定不一致fixtureをworkspace-writeの正常応答へ更新し、cwd不一致も検証 |
| `uv run pytest tests/integration/test_codex_service.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py -q --basetemp=tmp/pytest-u055` | 119 passed / 13.26s | サービス・GUI・終了の組立。実接続を作るfixtureも製品サービスの必須準備を使用 |
| `uv run ruff format --check .` / `uv run ruff check .` | passed（216 files） / passed | 変更後の形式・lint |
| `uv run ty check --no-progress` | passed | 必須コールバックと削除属性の型境界。互換用Optionalや型抑制の追加なし |
| `uv lock --check` / `uv build` | passed / passed | lock整合とsdist・wheelの生成 |
| `git diff --check` / 文書・docstring読解 | passed / 確認済み | 必須callback・固定memory・readiness・準備タイミングの説明と実装の整合。独立reviewで阻害指摘なし。旧read-onlyを示す試験promptの残存は修正 |
| 実runtime・実機 | not run | 本子では既存採用経路を固定試験。親の終了整理後に必要な実接続を行う |

## 10. 先送り事項

停止の単一所有と通知寿命は親の後続子が担当する。本子でread-only分岐を削ったことは、それらの整理・実接続検証の完了を意味しない。

## 11. チェックリスト

- [x] 対象範囲・対象外と既存呼出元を確認した
- [x] 最初の振る舞いとTDD Test Listを具体化した
- [x] 実装開始条件を確認した
- [x] 対象試験と型・docstring・配布gateを記録した
- [x] 親担当のreview・commitへ引き渡した
