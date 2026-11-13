# サービス操作配送の整理 仕様書

## 1. 概要

### 1.1 目的

着手中の子仕様。unit054 M1 のサービス操作受付を、操作文脈と束縛した関数へ整理する。任意 payload を持つ `_Command` と `_perform` の操作名分岐を取り除く。

### 1.2 起点 / source

| source | 内容 | path |
|---|---|---|
| user request | KISS に沿った契約削減と責務整理 | [親仕様](../unit_054/KISS_SIMPLIFICATION.md) |
| preceding unit | 停止の受付結果、終了の単一所有 | [unit057](../../complete/unit_057/TURN_END_OWNERSHIP.md) |

### 1.3 use case

| actor / boundary | 入力または状態 | 期待する観測結果 | 制約 |
|---|---|---|---|
| GUI | 接続・送信・回答・停止 | 世代に対応する結果を通知する | 接続取得と coroutine 生成はサービス loop 上 |
| GUI | 入力設定・プロジェクト・解像度・ポート列挙 | 対応する資源または専用 signal を更新する | runtime 操作として偽装しない |
| GUI | 終了中の送信 | 未送信の結果を一度通知する | GUI 上で保存 task を開始しない |

## 2. 対象範囲

- サービス操作文脈、束縛関数、待ち行列と task 回収。
- ローカル資源操作の専用メソッド化、内部配送直呼び試験の公開入口への変更。
- 終了受付後の GUI 拒否通知と保存処理の loop 境界。

## 3. 対象外

- 公開 DTO の別ファイル移動、GUI shell の分割。
- runtime の停止・通知寿命・入力権限仕様の再変更。
- 実 runtime のモデル実行と物理入力。unit057 の受入結果を本単位の実測とは扱わない。

## 4. 関連 docs

- [親仕様](../unit_054/KISS_SIMPLIFICATION.md)
- [Codex 利用手順](../../../docs/codex.md)
- [品質規約](../../initial/README.md)

### 4.1 前提・着手条件

unit057 は実接続・実機受入を終え、`7a9aeb8` で complete へ移動済み。開始時の作業ツリーは clean、3 状態の最大番号は 057 だった。

### 4.2 冒頭の調査・設計

既存 309 試験を基準として、取消し前の not_sent、実行中の unknown、世代、回答辞書のコピー、ContextVar による記録相関を維持する。回答の配送不明時は入力保留を解除しない。構造変更と境界不具合修正を別の論理変更に分ける。

### 4.3 工程内の分割

1. D01: 既存 green を基準に操作配送を整理する。
2. D02: 終了中の GUI 拒否境界を再現し、必要な修正を行う。
3. 関連 gate と独立レビュー後、親の最終レビューへ引き渡す。

## 5. 振る舞い仕様

| 振る舞い | 入力・状態 | 期待結果 | 備考 |
|---|---|---|---|
| runtime 配送 | 公開操作を提出 | loop 上で現在接続を取得し実行する | 小さい文脈は世代・操作・回答 token・記録 ID のみ |
| 待ち行列と取消し | 開始前 / 実行中に取り消す | not_sent / unknown を一度通知する | 自動再送しない |
| 回答 | 呼出元が mapping を後で変更 | 提出時のコピーを使う | unknown の入力保留を維持 |
| 記録 | 送信・Goal・停止 | 同じ文脈の request_id と操作を保存する | stop / goal_clear 成功は accepted |
| ローカル資源 | 設定・project 操作 | 専用の処理と通知を使う | ServiceResult 除外リストを廃止 |
| shutdown | 接続保持中に終了 | stop を追加せず close を直接待つ | unit057 の所有を維持 |
| 終了中の拒否 | GUI から提出 | 例外なく not_sent を一度通知する | 保存は所有 loop のみで行う |

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-done | D01 既存配送・記録・資源・取消しを維持して構造を整理 | characterization | integration | baseline 309 passed → 構造変更後 309 passed。回答コピーの 1 件を加え service 64 passed |
| todo | D02 close 待機中の GUI 提出を未送信として拒否 | regression | integration | 生存中の recording へ GUI から入る経路を再現する |

## 7. 設計メモ

公開 API は操作別の関数へ引数を束縛し、待ち行列は開始関数を保持する。任意 payload 辞書・動的 getattr・handler registry・新しい manager は導入しない。接続操作だけが共通結果処理を通り、ローカル操作は専用通知を持つ。公開 DTO は本ファイルへ残す。

D01 では `_Command` の 14 フィールドを `_OperationContext` の 4 フィールドへ減らし、ローカル操作の union、結果通知の除外リスト、`_perform` の match、欠損 path / options / resolution の分岐を削除した。専用メソッド化に伴ってソース行数はほぼ同じであり、責務と任意値の組合せを減らした構造変更である。Goal の 2 試験は公開操作を提出して結果を待つ形に改訂し、取消し前の注入点だけを task 起動直後へ移した。

root と connection_audit の D01 独立レビューでは、loop 上の生成・接続取得、世代、配送結果、ContextVar、回答不明時の入力保留、ローカル資源回収、停止受付記録、直接 close に阻害指摘なし。既知の GUI 拒否時の保存経路は D02 に分離した。

## 8. 対象ファイル

| path | change | 内容 |
|---|---|---|
| `src/gigas/services/codex.py` | modify | 操作配送と loop 境界 |
| `tests/integration/test_codex_service.py` | modify | 公開入口と境界回帰 |
| `spec/wip/unit_058/SERVICE_OPERATION_DISPATCH.md` | new | 契約・検証・判断 |

## 9. 検証

実装後に service / GUI / shutdown / answer / transport / connection の関連試験、ruff、ty、lock、build、文書参照を検査する。

| command | result | notes |
|---|---|---|
| `uv run pytest tests/integration/test_codex_service.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_transport.py tests/unit/test_codex_connection.py --basetemp=tmp/pytest-u058-baseline` | 309 passed | 変更前の動作基準 |
| 初回 baseline invocation | path error、収集 0 件 | answer lifecycle のファイル名を訂正して上記を実行。TDD red ではない |
| root 実行: `uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u057-full --tb=short` | 845 passed / 60.53s | unit057 時点の全体基準。製品編集前に完了通知を受領 |
| baseline と同じ 6 ファイル、`--basetemp=tmp/pytest-u058-structure --tb=short` | 309 passed / 14.63s | D01 構造変更後 |
| `uv run pytest tests/integration/test_codex_service.py -q --basetemp=tmp/pytest-u058-characterization --tb=short` | 64 passed | コピー確認を追加したサービス境界 |
| `uv run ruff format --check src/gigas/services/codex.py tests/integration/test_codex_service.py` / `uv run ruff check src/gigas/services/codex.py tests/integration/test_codex_service.py` | passed | 変更した Python の形式・静的検査 |
| `uv run ty check --no-progress` / `git diff --check` | passed | 型境界・差分空白 |
| `uv run ruff format --check .` / `uv run ruff check .` | passed | 220 Python ファイル、D / ANN を含む repo 設定 |
| `uv lock --check` / `uv build` | passed | lock 整合、sdist / wheel の生成 |

## 10. 先送り事項

公開 DTO の移動は後続の構造整理単位へ残す。

## 11. チェックリスト

- [x] 対象範囲と対象外を確認した
- [ ] TDD Test List を更新した
- [ ] 検証結果または未実行理由を記録した
- [ ] package / release / public API に触れる場合の gate を記録した
- [ ] 親の最終レビューを受けた
