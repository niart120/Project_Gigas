# turn通知の受付・保持寿命 仕様書

## 1. 目的・状態・起点

状態: **complete（実装・局所検証・独立レビュー完了）**。[整理親仕様](../../wip/unit_054/KISS_SIMPLIFICATION.md)のM1第二子として、現在turnの公開通知だけを表示・記録し、終端後の差分保持をなくす。2026-11-13のM0承認とM1実行指示に基づく。構造整理のcommitは`939724d`。M1全体の実接続受入は親へ引き渡した。

Intent Delta: 完了済みturnへの表示・記録更新と、任意のitem・質問・終端通知からのturn採用を廃止する。開始通知が開始RPC応答より先に届く経路と、受信済み履歴・部分記録を維持する。

利用場面は通常会話とGoal自律turnの本文・Plan・公開要約・活動・質問・承認の受信。生の推論、MCP引数・画像・メタデータは表示・記録へ取り込まない。

## 2. 対象範囲

接続の通知入口、表示・記録の変換と蓄積、turn採用元、現在turnの要求ID重複抑止、対応するunit/GUI fixture、API docstringと公開説明を変更する。

## 3. 対象外

停止・close・Goal終了の本体、未解決startと現在turnの構造置換、`_completed_turns`・`_interrupted_turns`の削除、サービス操作受付、保存・入力のM2は扱わない。開始相関を二度作り替えないため、二集合は終了所有の後続子で除く。実runtime・実機の新規受入とM1全体の完了は親が所有する。branch変更・PR・push・mergeを行わない。

## 4. 関連文書・着手条件

- [親の採用設計 5.3・5.4](../../wip/unit_054/KISS_SIMPLIFICATION.md)
- [実行管理](../../initial/02-runtime.md)、[記録](../../initial/04-agent-memory.md)、[UI](../../initial/10-ui-interaction.md)
- [接続API](../../../docs/codex.md)
- [前提となる製品専用組立](../../complete/unit_055/PRODUCT_CONNECTION_ASSEMBLY.md)

unit_055の実装commit `6d10407`、完了移動commit `d950569`とclean treeを確認した。採用順序の根拠と、終端後に活動完了が届く過去実測の限界は親5.4へ記録済み。終端payloadからの再構成・再照会・猶予待ちを追加しない。

## 5. 振る舞い仕様

| 入力・状態 | 結果 |
|---|---|
| 採用済み現在turnの本文・Plan・公開要約・活動 | 既存の公開情報だけを表示し、項目完了時に確定記録を発行する |
| 現在turnのturn/completed | 未確定項目をpartialとして一度記録し、蓄積を破棄する。以後の同turn表示・記録通知は無視し、表示済み履歴は残す |
| 切断時に未確定項目がある | 受信済み部分を一度記録する。終端で確定した記録を繰り返さない |
| turn/startedまたは自分のturn/start成功応答 | 既存の実行条件に従ってturnを採用する。started→質問/本文/終端→開始応答を扱う |
| 未採用turnのitem・質問・turn/completed | turnを採用せず、表示・要求を発行しない。並べ替えキューや再送を追加しない |
| 現在turn内で同じ要求IDを再受信 | 回答・失効後も同turn内では再提示しない。終端時に重複記録を破棄し、未知turnの要求IDは保持しない |
| file patchが変化 | 提示済み承認を失効させる既存挙動を維持する。表示変換へ承認判断・RPCを移さない |

## 6. TDD Test List

| status | item | type / layer | 既存試験の扱い |
|---|---|---|---|
| refactor-done | D01 終端で表示・記録を締め、受信済み部分を一度確定し、次turnへ要約を混ぜない | behavior / unit・integration | 公開通知試験の旧turn更新期待を撤去。途中本文とGUI履歴保持の試験を改訂する |
| refactor-done | D02 started/start応答だけを採用元とし、先行通知を保持し、要求重複抑止を現在turnへ限定する | behavior / unit | started無し質問からの採用期待を撤去。未知item・質問・終端と、同turn内重複・次turnでの要求受付を確認する |

各itemはred→greenの検証後に別commitとする。両itemのgreen後に、通知変換・小さい蓄積担当の抽出を振る舞い不変の別commitで行う。UIへ二重の締切状態を追加せず、終端後イベントを直接注入して更新を保証するfixtureを撤去する。

## 7. 設計メモ

接続がturnと要求の寿命を判断する。新しい通知moduleは純粋な表示変換と現在turnの要約・未確定対話だけを持ち、transport・Goal・承認・全履歴を所有しない。file差分の変換不成立は局所的に表現し、共有例外のための汎用moduleやevent busは作らない。

## 8. 対象ファイル

| path | 内容 |
|---|---|
| `src/gigas/runtime/codex/connection.py`、通知module | 受付元・締切・要求ID寿命・変換抽出 |
| `src/gigas/runtime/codex/models.py`、`docs/codex.md` | 公開表示契約 |
| `tests/unit/test_codex_connection.py`、`tests/integration/test_codex_gui.py` | 既存期待・fixture・回帰検証 |

## 9. 検証

局所pytestは`--basetemp=tmp/pytest-u056`で実行する。各itemのred/green、通知・要求・承認とGUIの回帰、ruff・ty、公開API契約に対応するlock check・build、差分と文書の意味確認を行う。実runtimeと実機は親M1の結合受入へ残し、模擬試験を代用にしない。

### D01実行結果（commit `d1321aa`）

- `uv run pytest tests/unit/test_codex_connection.py -q -k 'public_progress or dialogue_snapshots' --basetemp=tmp/pytest-u056`: redは2 failed・1 passed。終端後の旧turnから5件の表示・記録が発行されるため失敗した。同じcommandのgreenは3 passed・161 deselected。
- `uv run pytest tests/unit/test_codex_connection.py tests/integration/test_codex_gui.py -q --basetemp=tmp/pytest-u056 --tb=short`: 213 passed。秘密値除外、途中本文一回確定、同item IDの次turn、受信済みGUI履歴を確認した。
- 対象4 Pythonファイルの`ruff check`・`ruff format --check`、`git diff --check`: 成功。
- 構造抽出はD02 green後の別commitへ残す。ty・lock・buildと全体のsource/docs reviewは子の最終gateで行う。

### D02実行結果（commit `9d09ba1`）

- `uv run pytest tests/unit/test_codex_connection.py -q -k 'unrecognized_turn or notifications_before_start_response or question_is_answered_once' --basetemp=tmp/pytest-u056 --tb=short`: redは2 failed・1 passed。未知質問からTurnStartedが発行されることと、次turnの質問が前turnの要求IDで抑止されることで失敗した。greenは3 passed・161 deselected。
- connection・GUIの同じ回帰command: 213 passed。開始通知→質問/本文/終端→開始応答と、同turn内で回答済みの質問を再提示しないことを確認した。
- 対象Pythonファイルのruff check・format: 成功。test_answer_lifecycle.pyのunit側への誤指定は0件実行で、検証成功に数えない。実際のintegration側を最終gateへ含める。

### green後の構造整理・最終gate

`_notifications.py`へPlan・活動・MCP text・file差分の純粋変換と`TurnContent`を抽出した。接続は現在turnに一つだけcontentを持ち、終端・切断で手放す。蓄積キーからturn IDを除き、現在turnの項目だけを保持する。未知turnの受付、終了処理、承認失効・RPCは接続が引き続き所有する。汎用manager・共有例外module・event busは追加していない。

| command / review | result | 検査範囲 |
|---|---|---|
| `uv run pytest tests/unit/test_codex_connection.py tests/integration/test_codex_gui.py -q --basetemp=tmp/pytest-u056 --tb=short` | 213 passed（抽出前後・最終1.90秒） | 通知・質問・承認・Goal・GUIの既存回帰 |
| `uv run pytest tests/integration/test_answer_lifecycle.py tests/integration/test_codex_service.py tests/integration/test_codex_shutdown.py -q --basetemp=tmp/pytest-u056 --tb=short` | 77 passed（12.32秒） | 回答配送・世代・権限・設定変更・終了の既存回帰 |
| `uv run ruff check .` / `uv run ruff format --check .` | 成功・218 files formatted | D lintを含むstyleとformat |
| `uv run ty check --no-progress` | 成功 | 新moduleと公開eventの境界。追加fixtureの曖昧な辞書型をJsonObjectへ明示し、ignore/cast/Anyは追加しない |
| `uv lock --check` / `uv build` | 成功 | 依存整合、sdist・wheel生成 |
| `git diff --check` | 成功 | 空白・patch整合 |
| `uv run python -X utf8 tmp/u054/validate_docs.py d950569` | 3 documents・25 local_links・56 units、errors=0 | Markdown参照先・仮テキスト・unit重複 |
| source / docstring / docs review | 確認済み | turn採用・終端締切・要求ID寿命・秘密値除外・file承認失効と公開説明を照合。UIに二重の締切状態を加えず、旧turn更新を保証するfixtureだけ撤去 |
| 独立review | 修正必須指摘なし | optional表示のValueError抑止、本文envelopeの接続エラー変換、file承認失効の順序、MCP text-only、content破棄と要求ID寿命を確認 |
| 実runtime・実機、全unit/integrationの再実行 | not run | 本子は局所契約を模擬境界で検証。終了所有変更後のM1全体gateと実接続受入は親が実施する |

## 10. 先送り事項

全完了/中断turn集合と開始相関recordは、親M1の終了所有の子で一緒に置換する。今回残した二集合をM1完了の設計として扱わない。実runtimeでの終端後通知・開始通知順と停止の結合確認も親が所有する。

## 11. 完了チェックリスト

- [x] 採用設計と対象外・前提を確認した
- [x] D01・D02を別々にred→greenとしcommitした
- [x] green後の通知責務抽出と回帰検証を完了した
- [x] 公開説明・docstring・型・package gateを確認した
- [x] 未検証・先送りを親へ引き渡した
