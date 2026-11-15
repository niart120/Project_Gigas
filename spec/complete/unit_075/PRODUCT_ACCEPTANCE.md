# U08 M7 製品全体の受入

## 1. 目的・状態・起点

状態: **complete**。[U08親仕様](../unit_013/RESUME.md)のM7を担当する。Intent Delta: none。M1〜M6で完成した管理・記録・再接続を、実画面から実機まで通して確認した。利用者が新しい依頼を始め、途中で止め、ゲーム状態を変更した後に同じ会話へ戻る場面を対象とする。

## 2. 対象範囲・対象外

Project所属・未所属の新規開始、実映像・有限入力・共通停止、アプリ再起動後の明示再開、同じrecordsへの追記、映像未接続時の観測失敗と明示復帰を確認する。README/docs・配布指示資材を現行製品と照合し、U08全体の確認済み範囲を確定する。

数時間の攻略、ゲームリセットやセーブ復元、物理抜線・プロセス強制終了時の解除保証、新しい手動入力UIは対象外。M3〜M6で確認済みの指示・Skills・権限・承認・Goal・故障組合せを実機試験へ重複させない。標準記憶の抽出を強制せず、生成待ちを完了条件にしない。

## 3. 関連文書・着手条件

[02 実行](../../initial/02-runtime.md)、[03 ゲームI/O](../../initial/03-game-io.md)、[04 記録](../../initial/04-agent-memory.md)、[07 検証](../../initial/07-verification.md)、[09 操作](../../initial/09-control-flows.md)、[10 UI](../../initial/10-ui-interaction.md)を正本とする。

着手時点で、[M5](../../complete/unit_073/CONVERSATION_MANAGEMENT_UI.md)はnative画面と実app-server、[M6](../../complete/unit_074/RESUME_SETTINGS_AND_FAILURES.md)は設定・故障と実resumeを確認済みだった。全体1015件・静的検査・lock/buildが成功し、現行U08構成の実機通し受入を本作業へ引き継いだ。

本人認証・Windows sandbox準備済みの検証用専用homeを使う。機器は利用許可済みのUSB Video DeviceとCOM8（CH552、9600 baud）。検証用設定・作業場所を分け、通常利用の設定・既存記録を初期化しない。

## 4. 振る舞い仕様と実施順

| 段階 | 操作と期待する観測結果 |
|---|---|
| 新規・Project | GUIで機器を接続して現在画面を確認する。Projectで新しい会話を開始し、observeの後に安全な有限入力を1回だけ依頼する。入力完了・解除・実画像と、実thread ID配下の操作/観測/PNGを照合する |
| 新規・未所属と停止 | 未所属の新しい会話で現在画面を観測し、有限入力を依頼して共通停止する。入力処理の終端・解除後に切替が可能となり、記録先はその会話の作業場所配下となる。会話切替だけでは機器を再接続しない |
| 介入・再起動・再開 | 停止・入力idle後に、同じInputServiceへモデル外から有限入力を1回渡す。通常終了・再起動後に先のProject会話を選び、観測だけを依頼する。選択だけでは実行せず、明示resume後は現在画面を返し、古い操作を再送せず同じrecordsへ追記する |
| 代表障害・復帰 | idleで映像を「未接続」にし、同じ会話でobserveを1回依頼する。DEVICE_UNAVAILABLEを返し、成功観測・PNGを作らず、自動再試行しない。GUIで映像を明示再接続し、プレビュー復帰と通常終了を確認する |

入力内容は現在のゲーム画面を確認して決める。検証補助は要求と結果・PNG bytes・入力結果を受動的に記録し、観測のための追加入力はしない。入力はアプリ所有のInputServiceだけを通し、COMポートを二重に開かない。

observeとact_and_observeの両方で、返却画像のbytes・observation_id・captured_atを保存PNG/JSONLへ対応させる。operation_idで操作の開始・終端・解除結果を照合し、再起動後も既存JSONL prefixとPNGを保持することを確認する。

モデル外入力は手動介入後の状態変化を検証するための公開API利用であり、物理コントローラーや製品GUIの手動入力機能の検証とはしない。偽の会話イベントを記録へ足さない。共通停止が押下中か待機中かを実測で区別し、確認した解除範囲だけを報告する。映像未接続は要求前の利用不能であり、物理抜線や転送途中の故障と区別する。

## 5. TDD Test List・作業境界

| status | item | type | layer | notes |
|---|---|---|---|---|
| verified | P1: GUIから新規・停止・介入後の再開・観測失敗を実機へ通し、画像と記録を照合する | acceptance | GUI/runtime/hardware | 通常送信5回。復帰後のMCP入力確認を1回追加。自動再送なし、成功PNG6枚を照合 |
| verified | P2: 現行の公開手順・配布指示・全受入範囲を照合し、U08を完了整理する | review | docs/package | M1〜M7の証拠を参照し、公開文書・初期設計・親仕様の確認範囲を更新 |
| refactor-done | P3: 旧APIキー設定の移行経路を撤去し、対応する認証設定だけを受け付ける | regression | settings/GUI | M0合意の撤去漏れを修正。red 4件→green 63件、構造整理後も63件、関連173件成功 |
| refactor-skipped | P4: MCP終了中の追加要求でHTTP応答前に切断された場合も拒否として検証する | regression | integration | 全体gateでReadErrorを観測。元observeの成功・PNG・drain条件は維持し、追加POSTの期待だけを修正 |

製品不具合を検出した場合は受入を止め、再現試験のred、修正、green後の責務確認、同じ試験、独立レビューを経て別コミットにする。既存動作の実測を新しいred/greenと偽らない。試験補助はtmpに隔離し、画面操作はComputer Useで行う。

P3は10の「旧APIキー設定からの移行UIは設けない」への適合修正である。旧設定の移行ボタン・専用分岐・利用手順を除き、設定は対応するchatgptだけを受け付ける。実ファイルの変換・削除は行わない。既存の設定保存失敗・未編集値保持の検証は、現行の設定項目を使って維持する。

## 6. 対象ファイル

本仕様、U08親仕様、関連初期設計、README、`docs/codex-gui.md`・`docs/codex-verification.md`などの公開手順を対象とする。製品コード・テストは、実測で見つかった問題の再現と修正が必要な場合だけ変更する。

## 7. 検証計画と実績

| gate | 状態・範囲 |
|---|---|
| 実画面・実モデル・実機 | 成功。Project・未所属の新規、共通停止、モデル外介入、再起動・履歴閲覧・明示再開、映像未接続時の失敗・明示復帰を確認。画像・入力結果・記録を照合。解除の確認範囲は下記参照 |
| 配布指示・利用手順 | P3・P4反映後の`uv run python scripts/check_package.py`成功。wheel/sdist同梱指示の一致、隔離環境からの資材読取・起動終了を確認。P1後に実機操作の確認範囲を公開文書へ反映 |
| 回帰・静的・build | P3・P4反映後の全体1018件、ruff/format・ty・lock/build成功。下記にcommandと範囲を記録 |
| 文書レビュー・リンク・git diff --check | 実測・記録を独立監査し、公開文書・初期設計・親仕様を更新。完了移動後の参照と差分を検査。範囲と結果は下記の完了整理に記録 |

### P3の修正・検証

M0で撤去対象とした旧認証設定の移行UIが、M1以降も残っていた。旧値の読込・保存・既存ファイル保全・GUI初期読込失敗を試験し、4 failed / 59 passedを確認した。設定はchatgptだけを受理し、移行ボタン・編集処理・Shellの表示分岐と公開手順を除去した。runtimeの未対応認証拒否は維持した。

修正後は63 passed。green後、移行ボタンのためだけに残った認証行の特別layoutを通常のボタン配置へ揃え、同じ試験で63 passedを確認した（refactor-done）。設定のroundtrip・未編集値保持・保存失敗は現行項目で引き続き検証する。関連する設定・I/O・GUI・接続・実行設定の回帰173件、対象ruff/format、全体ty、diffチェックも成功した。実データの変換・削除はしていない。

63件のcommandは`uv run pytest tests/unit/test_settings.py tests/integration/test_settings_io.py tests/integration/test_app_settings.py --basetemp=tmp/pytest-m7-auth-fix -q`。関連173件は同3ファイルに`tests/unit/test_codex_connection.py tests/integration/test_codex_gui.py tests/integration/test_execution_settings_flow.py`を加え、basetempを`tmp/pytest-m7-auth-regression`として実行した。独立レビューでも必須修正なし。P1起動時の実画面では通常の認証行とログインボタンを確認し、未接続で無効、移行UIなしだった。

### P4の試験修正

`uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m7-final -q`は1 failed / 1017 passed（849.98秒）。唯一の失敗はMCP終了中の追加POSTに対するhttpx.ReadErrorで、処理中の元observeを検証する前に試験が止まった。uvicornはlistenerを閉じ、HTTP応答前の接続も閉じるため、追加要求は503だけでなく読取時の切断でも拒否される。

修正前の単独再実行は1 passed（1.24秒）で、同じ競合は再現しなかった。追加の拒否確認POSTだけReadErrorを許容し、先行observe成功・記録1件・PNG存在・close/drainの必須条件は維持した。製品コードは変更していない。

`uv run pytest tests/integration/test_game_io_mcp.py::test_cancelled_close_keeps_http_and_observation_work_owned --basetemp=tmp/pytest-m7-mcp-close -q`は修正後1 passed（1.39秒）、構造確認後も1 passed（1.29秒）。`uv run pytest tests/integration/test_game_io_mcp.py --basetemp=tmp/pytest-m7-mcp -q`は16 passed（6.44秒）。局所の例外条件だけで済み、helper追加は不要と判断した（refactor-skipped）。対象ruff/format・ty・diff、公開docsとローカルuvicorn実装の照合、独立レビューも成功した。

### P2の配布確認

READMEの再開予定・旧設定ボタンの記載と、映像API文書のJSONL保存項目を現行契約へ修正した。配布smokeのアイコン検査を表示中のsidebarへ合わせた。`uv run python scripts/check_package.py`は、wheel/sdistの共通指示一致、隔離環境からの資材読取、取得worker、module/consoleの両起動・通常終了に成功した。P3の製品変更前（2abe0a3＋当該3ファイル）の確認と、下記の最終製品のgateを分けて記録する。

関連GUI15件は構造確認の前後とも成功。検査対象1箇所だけの変更で追加の抽象化は不要と判断した（refactor-skipped）。README/API文書のローカルリンク12件、ruff/format・ty・diffも成功した。

### 最終製品の自動検査

P3・P4反映後（17c969a）に、次のgateが成功した。初期設計04・06・09・10の実装予定表現も現行契約へ揃え、独立レビューで実装済み範囲との整合と要件追加がないことを確認した。

| command | 結果 |
|---|---|
| `uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m7-final -q` | 1018 passed、115.22秒 |
| `uv run ruff format --check .` | 289ファイル成功 |
| `uv run ruff check .` | 成功 |
| `uv run ty check --no-progress` | 成功 |
| `uv lock --check` | 83パッケージ成功 |
| `uv build` | wheel・sdist生成成功 |
| `uv run python scripts/check_package.py` | 26.332秒で成功。隔離インストール、同梱play.md一致、資材読取、存在しない取得元の拒否・worker終了、module/console両入口のoffscreen起動・sidebarアイコン・通常終了を確認 |
| `git diff --check` | 成功 |

配布検査では当該プロセスだけ`UV_CACHE_DIR`を検証用領域、`TEMP/TMP`を通常のTempへ指定した。先行試行ではinterpreter cacheへの一時ファイル書込み権限エラー、sandboxのTempパス解釈エラー、GUI起動検査の15秒タイムアウトが発生した。最後の失敗時には全体pytest等も大幅に遅延していたが、原因は特定していない。単独再実行の成功と区別する。

更新した7文書のローカルリンク119件・unit番号75件の一意性を`uv run python tmp/validate_m2_docs.py`で確認した。独立レビューで最終gate実績と失敗記録を照合し、キャッシュ書込みエラーの表記と取得worker試験の範囲を明確にした。文書のみの追記に対して製品試験は繰り返していない。

### P1の実画面・実モデル・実機受入

2026-11-15（JST）、利用者がゲーム映像を復帰させた後に再開した。USB Video Device、COM8（CH552、9600 baud）、準備済みの専用home、gpt-6.1-sol／lowを使用した。ゲームは屋内で、セーブ・リセット・場面退出は行っていない。2回のnativeプロセスで通常送信を合計5回行い、両方とも通常終了・exit 0・window消滅を確認した。

| 操作 | 確認結果 |
|---|---|
| Project新規 | GUIで作業フォルダーを選び、実Project・threadを作成。observe 1回とUP 100ms＋wait 250msのact 1回が成功し、入力・中立送信とPNG2枚を記録した。ただし主人公の位置変化は見えなかった |
| 未所属新規・共通停止 | 別の作業フォルダー・実threadでobserve 1回。UP 30000msの押下処理中に「中断」し、受付から15.172秒でINTERRUPTED／completed_steps=0／input_sent=sent／neutral=sentを回収した。turnもinterruptedとなり、入力idle・停止完了後に切替可能。中断したactの成功観測・PNGはない |
| モデル外介入 | 利用者の追加入力許可後、同じInputServiceへUP 500ms・RIGHT 500ms・LEFT 500ms（各入力後wait 1000ms）の有限列を1回渡した。6 step成功・中立送信を確認し、主人公が入口から室内へ移動した。会話イベントは生成していない |
| 再起動・履歴閲覧・再開 | 先のProject会話を選ぶとread・goal/get・turns/listだけで履歴を表示し、resume・turn/start・MCPは発生しなかった。明示送信後はresume 1回→turn/start 1回→observe 1回となり、介入後の現在画面を返した。古い入力の再送なし、同じrecordsへ追記 |
| 映像未接続・明示復帰 | GUIで映像を未接続にしてobserve 1回を依頼。DEVICE_UNAVAILABLEを返し、成功観測・PNGを追加せず、再試行なしでturnが終了した。映像とCOM8を明示接続し、プレビューを復帰させた |
| 復帰後のMCP入力 | 初回に見えなかった実反応を補うため、observe 1回→RIGHT 300ms＋wait 250msのact 1回を追加した。2 step成功・中立送信、返却画像とプレビューで右への移動、後続プレビューで静止を確認した。追加の再試行はない |

最初の2回のUP入力で実機の反応が見えなかった原因は特定していない。その後の有限入力で移動した事実だけを確認し、コントローラー認識の遅れが原因だったとは断定しない。押下中の共通停止では入力処理の取消しと中立フレーム送信を確認したが、当該入力に対する機器側解除そのものは観測できていない。30000msを実際に押し続けた試験や機器ACKの確認として扱わない。

実記録先はProjectが`tmp/u08-m7-project-native/records/01a1dc71-6305-7210-ba67-c2b97acf4eab/`、未所属が`tmp/u08-m7-acceptance/unassigned/a811aadb-0a15-431a-8447-42d08e540702/records/01a1dc72-cd77-75f1-9412-4a4379592bd2/`。成功PNG6枚について、MCPへ返したbytesと保存ファイルのSHA-256、observation_id、captured_at、JSONL内の相対パスが一致した。操作の開始・終端はoperation_idで対応し、再起動前のJSONL prefixとPNGを保持した。検証前から存在した4会話の記録も変更していない。

独立監査でも同じ結果を確認した。2プロセスの管理・実行RPCは27件成功、guardによる拒否と記録失敗は0件。全体のMCPはobserve 5回（成功4・DEVICE_UNAVAILABLE 1）、act 3回（成功2・中断1）で、成功画像は合計6枚だった。ProjectのJSONLは2,761→8,199 bytesへ追記され、未所属の1,919 bytesと既存4会話は不変。失敗observeの提出区間には提出本文1件・確定本文2件だけが残り、余分な観測・PNGはなかった。

証拠は`tmp/u08-m7-ui/l4-20261114T220541Z-f77005a0-`と`l4-20261114T234755Z-c2adc170-`のevents.jsonl／report.json、同ディレクトリの画面画像に保存した。`08-stopped-before-intervention.jpeg`と`09-after-intervention.jpeg`が介入前後、`10-restored-history-only.jpeg`が閲覧のみ、`11-resumed-observation.jpeg`と`12-observe-unavailable.jpeg`が再開・失敗、`13-recovered-before-mcp.jpeg`〜`15-recovered-stable.jpeg`が復帰後入力と静止の証拠である。`07-unassigned-stopped.jpeg`は別アプリが写ったため根拠から除外した。

Project・未所属の両threadは、ゲームMCP利用後もstate DBの読取専用metadata確認で`memory_mode=enabled`・`source=vscode`だった。生成対象維持の証拠であり、標準記憶の実抽出や攻略上の有用性を確認したものではない。P1は既存製品の受入、P2は文書・配布の照合であり、新しいred/greenや製品リファクタリングは必要なかった。P3・P4の修正と検証は上記の別記録・別コミットに分離した。

### P2の完了整理

公開docs 5件の実機未確認表記を更新し、画像対応・明示再開・映像復帰の実績と、機器側解除・標準記憶抽出・長時間運転の未検証範囲を照合した。初期設計の現在地・生成対象維持、親仕様のM7受入を更新し、本仕様と親仕様をcompleteへ移した。既存仕様・開発ジャーナルは移動先への参照だけを更新した。

独立レビューでは実測・証拠・完了条件に不整合はなく、残っていた予定表現3箇所を整理した。`uv run python tmp/validate_m2_docs.py`で変更33文書・ローカルリンク481件・unit番号75件の一意性と仮テキスト残りを検査し、成功した。`git diff --check`も成功し、旧wip参照の残置はない。製品コードは実機受入後に変更しておらず、製品試験・型検査・buildの再実行はnot applicableとした。上記17c969aの最終製品gateと、文書内容のレビューを区別する。

## 8. 先送り事項

長時間攻略・資源・記録量は[U10](../../planned/unit_015/CONTINUOUS_OPERATION.md)。他の入力機器・OS、物理障害、強制終了時の機器側解除保証は本受入の範囲外として残す。標準記憶の実抽出・有用性は通常プレイで観測し、独自整理を追加しない。

## 9. 完了チェックリスト

- [x] M1〜M6の完了、実機利用範囲と現在の未検証範囲を確認した
- [x] P1の実測と画像・入力・記録の対応を確認した
- [x] P2の公開手順・配布資材とU08全体の整合を確認した
- [x] 独立レビュー、変更に対応するgate、未検証範囲を記録した
- [x] 本仕様とU08親仕様を完了へ移し、参照を更新した
