# U08 M4 会話選択・既存会話の再接続

## 1. 目的・状態

状態: **wip**。[U08親仕様](../unit_013/RESUME.md)のM4を担当する。[M3](../../complete/unit_071/EXECUTION_SETTINGS_AND_INSTRUCTIONS.md)は製品実装・実接続受入まで完了した。次は、公開履歴を読む選択対象と実行資源を持つ対象を分け、既存会話への明示送信・Goal操作をサービス経由で成立させる。

起点は[M0](../../complete/unit_068/MANAGEMENT_STORAGE_DESIGN.md)のD5、[02](../../initial/02-runtime.md)の所有、[08](../../initial/08-sdk-compatibility.md)の公開契約、[09](../../initial/09-control-flows.md)のS06。画面の配置・下書き・履歴表示範囲は[10](../../initial/10-ui-interaction.md)を維持し、UIの結合はM5が担当する。

## 2. 対象範囲・対象外

サービスの所属別新規・既存選択・履歴取得、旧購読と要求・資源の回収、保存cwdと同じrecordsへの再接続、現在のMCP・モデル・実行設定によるresume、閲覧中のモデル選択とGoal削除を対象とする。既存の新規開始・共通停止・機器寿命を維持し、公開API文書と試験を同時に更新する。

サイドバー・履歴描画・下書き確認はM5、状態を跨ぐUI障害検証はM6、実機の通し受入はM7。独自の会話台帳・履歴キャッシュ・会話別モデル台帳、旧互換、自動再接続・再試行・再送、Goalの先行実行を抑える順序保証、ゲーム内状態の復元は追加しない。

## 3. 前提と冒頭確認

M1のCodexManagementがProject・thread・履歴ページ・Goalの公開値を返し、M2のResourcesが作業場所とMCP・writerを所有する。M3の不変なExecutionSettingsと設定変換をstart/resumeで共用する。

採用版0.167.0のthread/resumeにはprojectId・effortの直接指定がない。保存所属を維持し、modelとconfig.model_reasoning_effort、cwd・runtimeWorkspaceRoots・developerInstructions・権限・MCP設定を渡す。履歴は別の公開ページAPIで取得し、resumeではexcludeTurns=trueを使う。unsubscribeの各結果は購読解除の事実であり、停止・アンロードの保証ではない。ロード済みthreadの設定を更新するには、購読者なし・idle・非実行の条件を満たして再構築させる。根拠は08の採用版ソースと公開schemaで照合する。

resume応答ではthread.id・projectId、cwd・runtimeWorkspaceRoots、model・reasoningEffort、承認・sandboxの実効値を確認する。configまたはdeveloperInstructionsの指定はロード済みthreadでは差分として扱われ、再構築条件を満たさない場合は上書きが無視され得る。RPC成功だけで現在設定への更新成功としない。unsubscribeのnotLoaded・notSubscribed・unsubscribedを区別し、その結果から停止済みとは推定しない。

この静的確認を製品の再接続成功とは扱わない。K5では、同一プロセス内のロード済みidle会話と、プロセスを起動し直した保存会話を区別して確認する。

## 4. 振る舞い仕様

| 操作・状態 | 観測する結果 |
|---|---|
| Project・未所属の一覧と既存会話選択 | 公開metadata・履歴ページ・現在Goalを返す。resume・MCP起動・writer作成・ゲーム操作はしない。cwdが欠けていても閲覧できる |
| 所属別の新規会話 | 所属だけを保持し、明示実行で新しいthreadを作る。選択だけで未所属フォルダーを作らない |
| 実行中・送信中・停止中の切替 | 拒否し、利用者の共通停止後の操作を待つ。自動停止・切替予約はしない。機器の未回収操作も残して切り替えない |
| 停止済み対象からの切替 | 候補情報の取得後、旧threadのunsubscribe・要求失効・MCP閉鎖・保存drainを完了して対象を変更する。旧通知・古い回答・遅い取得結果を新対象へ反映しない |
| 閲覧中のモデル・推論レベル変更 | 公開候補で検証した値だけを保持し、settings/update・resumeを送らない。ロード済み対象は既存のsettings/update受理後に確定する |
| 閲覧中のGoal削除 | 選択threadへgoal/clearを送り、公開結果・通知に従う。モデル・sandbox ready・cwd存在・MCP準備を要求せず、resumeしない |
| 既存会話への通常送信・Goal作成/目的編集/再開 | 保存cwdの存在確認→現在MCP・同じrecordsの結合→resume→指定操作の順。Project所属・未所属とも同じ構成とし、欠けたcwdを再作成しない。recordsだけの欠落は明示実行で作成する |
| resume応答前の通知・ゲーム呼出し | 既知thread IDを受信対象にし、writer・MCPを先に結合する。active Goalの先行実行を許容し、その観測・本文を当該会話へ保存する |
| resume待機中の停止・切断 | 送信済みresumeを所有して結果と早期turnを回収する。未送信本文の取消しと、再開済みかもしれないGoal・turnの停止を混同しない。停止は既存の共通処理を使う |
| 失敗 | 選択・取得済み履歴と入力を維持し、実行準備の失敗を未受付・停止済みと断定しない。回収済みの旧資源をロード済みとして扱わず、自動回復・再送はしない。停止・明示切断を妨げない |

初回履歴取得・切替の要求は同時に一件だけとする。既存の接続世代、対象thread ID、所有taskの同一性で遅い結果を識別し、並行切替用の予約機構を作らない。履歴の全件保持・記録からの補完は行わない。

閲覧中のGoal削除でも、採用版は未購読の要求元へgoal/clearedを通知する。cleared=trueは対象IDの通知と結果を相関し、falseは通知を待たない。既存のGoal変更契約を共用し、成功ごとのgoal/getやresumeを追加しない。

新規・再接続の要求に選択effortを含め、Goal開始前の設定を確定する。毎turnのunsubscribe/resumeや、正確な保存先パスを伝えるための指示差替えは追加しない。

## 5. 設計・責務と実施順

選択の所有者は公開metadata・取得中taskと表示対象を扱い、全文履歴・実行状態の正本を持たない。Executionはロードした対象のturn・Goal・要求・送信と共通停止を担当する。Resourcesは解決された作業場所、MCP・観測・writerの取得と回収を担当し、履歴・会話選択の判断をしない。ServiceはQt配送・接続世代・部品の組立と接続全体の終了を維持する。

選択先は新規の所属、または既存のthread ID・保存cwd・所属を表す不変値として保持する。新規startの検査直後、records結合前に実IDと解決済みcwdを選択へ渡し、追加readを行わない。初回結合失敗・明示切断・通信断でも既知の選択先を維持し、接続し直しただけでは実行せず、次の明示操作を既存IDのresumeへ進める。閲覧Goalがactiveでもロード済み実行へ合成せず、閲覧だけの会話を停止・終了でpausedへ変えない。

選択と実行準備を既存の大きなclassへ継ぎ足さず、同じ情報を複数の所有者で更新しない。通知は現在の実行対象へ配送し、接続開始時の古いExecutionをclosureに固定しない。Goal変更の通知相関・停止は既存の契約を再利用する。新しい部品は具体的な所有境界に限り、単なる転送層を増やさない。

各itemをred→green→refactor-after-greenによる構造確認→同じ試験の再実行→独立レビュー→commitで進める。K3aで選択部品、K3bで既存所有者とサービスへ結合する。K3aだけでは製品の会話選択対応とは扱わず、未結合の入口をGUIへ出さない。選択から既存会話の明示実行までの結合はK4完了をゲートにする。TDD itemは同じM4の中で順に完了させ、M5のUIを先行させない。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | K1: start/resumeが選択モデル・推論レベル・最新設定を渡し、ID・所属・作業場所・権限の不一致を拒否する | new/regression | unit/integration | 共通設定変換、resume固有項目、Goal開始前のeffort、余分な設定更新の整理 |
| refactor-done | K2: 既存作業場所と同じrecordsを開き、欠損・失敗・取消しでも取得資源を回収する | new/edge | integration | 未所属/Project、欠けたcwd非再作成、recordsのみ再作成、追記と旧資源のdrain |
| refactor-done | K3a: 公開履歴と所属別新規の選択候補を取得し、失敗時に以前の対象を保持する | new/edge | unit/integration | 新規/既存target・閲覧/ページ結果、取得は一件、履歴を蓄積せず、実行資源を作らない |
| todo | K3b: 選択部品をサービスへ結合し、停止後だけ切替え、閲覧モデル選択・Goal削除を実行せず扱う | new/edge | integration | unsubscribeと旧要求・資源の回収、遅い結果の失効、start後・切断後の対象保持、Goal相関共用 |
| todo | K4: 既存会話への明示送信・Goal操作が現在資源でresumeし、早期通知と停止・切断を回収する | new/edge | integration | resume前のID/records結合、成功状態とIDの区別、失敗後の明示操作、保存設定snapshot、通常新規の回帰 |
| todo | K5: 実app-serverのロード済みidle・再起動後の会話で閲覧と明示再開・同一記録先追記が成立する | acceptance | integration | 公開ID/cwd/Goal、現在MCP・設定・モデル、active Goalと追加指示、実機は使わない |

## 7. 対象ファイル

runtime/codexの要求組立・管理/表示型、servicesの会話選択・実行・資源とQt配送、対応するunit/integration tests、docs/codex-gui.md・codex.md・codex-verification.md。所有契約が具体化された箇所は初期設計02・09へ反映し、工程進捗は05と親仕様へ記す。

## 8. 検証計画・現在の結果

| command / 確認 | 状態 | 対象 |
|---|---|---|
| 採用版公開schema・ソース照合 | 確認済み（静的） | resumeの要求・実効応答、unsubscribeの3結果、設定再構築条件、Goal通知と再開順序。根拠は08と第5節の採用版ソース |
| 各itemのpytest | not run | 要求、選択/実行境界、資源と通知・停止の順序 |
| uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m4-root -q | not run | 既存の通常送信・Goal・機器・GUIを含む回帰 |
| uv run ruff format --check . / uv run ruff check . / uv run ty check --no-progress | not run | 形式・lint・公開型/所有境界 |
| uv lock --check / uv build | not run | 公開API・依存・配布契約 |
| 隔離homeでの実接続 | not run | K5の両再開経路。モデルへの明示送信回数と実機未使用を記録する |
| git diff --check、文書内容・参照検査 | 着手仕様で成功 | 仕様・公開APIと実装範囲の一致。実装後は更新箇所を再確認する |

公開型・docstringは対応skillで確認する。実接続ではM1の認証済み隔離homeを使い、資格情報を複製しない。自動試験の模擬MCP・入力と実モデルの結果を区別し、途中失敗や未観測範囲を残す。

K5は履歴・Goalの閲覧が実行を始めないことを先に確認し、同一プロセス内の停止・切替後とプロセス再起動後で、現在のMCPによる観測と同じthreadのrecordsへの追記を確認する。active Goalへの追加指示はresume→turn/startで確認し、先行するGoal turnの有無を成功条件にはしない。公開APIだけの照合を、この製品経路の受入に代用しない。

### K1 実装・検証結果

start/resumeは共通の設定組立を使い、config.model_reasoning_effortを開始前に渡す。resume固有のthreadId・excludeTurnsを組み立て、保存所属は変更しない。返却ID・所属・選択推論レベルと既存の権限条件を検査する。初回Goal直前の補助settings/updateを撤去し、ロード済み対象のモデル変更時の更新は維持した。既存サービスへのresume結合はK4で行う。

`uv run pytest tests/unit/test_conversation_protocol.py tests/unit/test_execution_configuration.py tests/integration/test_conversation_control.py --basetemp=tmp/pytest-m4-k1 -q`は未実装resume・推論レベル欠落等で20 failed/75 passed、実装後95 passed（7.56秒）、構造確認後95 passed（7.46秒）。

`uv run pytest tests/unit/test_codex_connection.py tests/integration/test_codex_transport.py tests/integration/test_conversation_execution.py tests/integration/test_conversation_notifications.py tests/integration/test_codex_service.py --basetemp=tmp/pytest-m4-k1-regression -q`は122 passed（12.88秒）。全体ty、対象7ファイルのruff・format、差分検査は成功した。

refactor-skipped: 共通設定を一か所へ集約し、start/resume固有値だけの入口とした。追加状態・転送層は不要と判断した。独立レビューの修正必須指摘なし。公開文書は新規開始時の推論レベル伝達・返却検査を更新し、再接続サービスを実装済みとして記していない。

### K2 実装・検証結果

Resources.prepare_existingは公開metadataの保存cwdを変更せず検査し、現在のMCPを用意する。既存bind_recordsで同じ記録へ追記し、欠けたrecordsだけを明示結合時に作成する。別cwdはclose前に拒否し、保持writer.pathと異なるthread IDも拒否する。新規・既存のMCP組立を共用し、Project選択の所有移動はK3で呼出し元とまとめる。

`uv run pytest tests/integration/test_conversation_resources.py --basetemp=tmp/pytest-m4-resources -q --tb=short`は未実装API・別ID未拒否により15 failed/6 passed、実装後21 passed（5.62秒）、構造確認後21 passed（5.48秒）。既存本文・画像の保持と追記、cwd欠落、MCP部分起動の失敗/取消し、writer取得取消し後の所有、close時の保存drainを確認した。

`uv run pytest tests/integration/test_conversation_resources.py tests/integration/test_conversation_execution.py tests/integration/test_codex_service.py tests/integration/test_workspaces.py --basetemp=tmp/pytest-m4-resources -q --tb=short`は82 passed（15.17秒）。変更2ファイルのruff format/check・tyと差分検査は成功した。

refactor-done: 試験の初期writer命名を整理し、同じ試験が成功した。MCP組立は実装時に共用済みで、追加fixture層やthread台帳は不要と判断した。独立レビューの修正必須指摘なし。同じcwdの別threadへの切替でも旧endpointの入力呼出し先を残さないため、K3では必ずResources.closeを経由する。実app-server・実機はこのitemでは未使用。

### K3a 実装・検証結果

新規/既存を分ける不変targetとBrowseResult・HistoryResult、privateな選択所有者を追加した。metadata・Goal・初回履歴を取得し、呼出し元による旧実行の解放成功後に候補を採用する。取得/解放の失敗・取消しでは以前の選択を維持する。追加ページは初回選択を妨げず、要求時のtargetを付けて返す。保存cwdが欠けていても閲覧でき、選択部品は実行資源を作らない。

`uv run pytest tests/integration/test_conversation_selection.py --basetemp=tmp/pytest-m4-k3a -q --tb=short`は未実装moduleでcollection 1 error、実装後11 passed（0.08秒）、構造確認後11 passed（0.09秒）。`uv run pytest tests/integration/test_conversation_selection.py tests/unit/test_codex_history.py tests/unit/test_codex_management_goals.py --basetemp=tmp/pytest-m4-k3a-regression -q --tb=short`は39 passed（0.14秒）。変更3ファイルのruff・format、全体ty、差分検査は成功した。

refactor-done: 試験のpoll待ちを明示Event付きRPC gateへ置換し、async context managerの型をAsyncGeneratorで表した。同じ試験は成功し、Any・cast・型抑制を追加していない。独立レビューの修正必須指摘なし。履歴結果はtargetの値等価ではなく同じ選択instanceで照合することをdocstring・公開文書に明記した。A→B→Aへの切替後に古いAのページが届く場合も、K3bで採用しないことを確認する。サービス・GUIへの結合、再接続の製品利用はまだ未実装である。

## 9. 先送り事項

M5はサイドバー・履歴描画・下書きと既存UIへの接続、M6は新規/閲覧/実行を跨ぐ操作・設定・障害の全体確認、M7はUSB Video Device・COM8での通し受入を担当する。M3で未観測の会話からの記憶抽出とゲームMCP後の生成対象維持はM6・M7へ引継ぎ、M4へ新しい記憶機構を追加しない。

## 10. 完了チェックリスト

- [x] M3完了とM4の範囲・所有を確認した
- [ ] 採用版の公開契約と詳細なサービス境界を照合した
- [ ] K1〜K5と構造確認・独立レビューを記録した
- [ ] 公開型・docstring・利用手順と実装を整合させた
- [ ] 品質ゲートと実接続の両再開経路を確認した
- [ ] 親・初期設計へ完了範囲とM5以降の引継ぎを反映した
