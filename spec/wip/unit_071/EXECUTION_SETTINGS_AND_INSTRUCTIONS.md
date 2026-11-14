# U08 M3 実行設定・標準指示の結合

## 1. 目的・状態

状態: **wip（J1完了、次はJ2）**。[U08親仕様](../unit_013/RESUME.md)のM3を担当する。[M2](../../complete/unit_070/RECORDING_AND_NEW_CONVERSATION.md)の新規開始経路へ、保存済みのWeb・自動記憶・承認方法とCodex標準の指示読込を結合する。既定でWebと自動記憶を使い、承認は「代わりに承認」とする。

起点は[M0](../../complete/unit_068/MANAGEMENT_STORAGE_DESIGN.md)のD4・D5と、[08の設定契約](../../initial/08-sdk-compatibility.md#設定の所有と適用)。M2は製品実装・実接続受入まで完了した。利用者はGIGASの設定から実行環境を選び、攻略資料やSkillsは作業フォルダーと標準の探索先で管理できる。

## 2. 対象範囲・対象外

起動設定・thread/startの設定変換、実効権限の検査、保存後の設定値の受渡し、設定画面の3項目、AGENTS/Skills読込み抑制の撤去、共通指示と公開文書・試験を対象とする。M1のsettings.toml保存契約は再実装しない。

既存会話のresumeと履歴・会話選択はM4以降。独自の指示探索・記憶生成・資料様式・記憶削除、外部MCPの導入UI、config.tomlとの同期、実行中threadへの設定即時反映、設定反映のためだけの自動再接続は追加しない。

## 3. 関連文書・着手時の確認

[04](../../initial/04-agent-memory.md)の自由な攻略資料と記憶、[02](../../initial/02-runtime.md)の所有、[09](../../initial/09-control-flows.md)の明示開始、[10](../../initial/10-ui-interaction.md)とAGENTS.mdのGUI表記、[07](../../initial/07-verification.md)の受入観点に従う。

現状はSettingsに3値の保存があり、launch.pyはWeb・記憶をOFF、AGENTS/Skillsを抑制し、thread要求はreviewer=userに固定している。M2でPythonの固定資料指示とguide/current資材は撤去済み。Shellの保存成功経路は再利用するが、今回の3値だけの変更を映像設定変更とみなさないようにする。

採用版の公開設定名と要求上書きの順序は08の根拠へ照合する。標準Skillsは専用homeだけでなく$HOME/.agents/skillsも探索する。記憶の候補選択・抽出にはsource・経過時間・残量等の条件があり、ON直後の生成を開始条件にしない。実効設定・実モデル動作はJ4で確認し、静的な成立確認と区別する。

## 4. 振る舞い仕様

| 境界 | 入力・状態 | 結果 |
|---|---|---|
| Web・記憶 | 保存済みのON/OFF、既定ON | Webはlive/disabled。記憶のfeatures.memories・generate_memories・use_memoriesを一括切替し、disable_on_external_context=false。OFFでも既存記憶は保持する |
| 承認・sandbox | auto_review既定／user、workspace-write | on-requestと選択したreviewerを要求し返却値を検査する。作業場所を通常の書込先、追加rootsなし、シェル通信禁止、一時領域除外を維持する |
| 指示・機能範囲 | 新規開始のcwd | project_doc_max_bytes=0とskills.include_instructions=falseを撤去し標準探索へ任せる。共通指示は既存の最小情報と記録構造。Apps/Plugins/Hooksは無効、notifyなし、質問・Planは有効、ゲームMCP以外の導入経路を増やさない |
| 保存と反映 | 設定保存成功後／失敗・キャンセル | 成功した値だけを次の接続・新規thread開始へ使う。開始要求中の値を固定し、応答を同じ値で検査する。既存threadを切替えず、失敗・キャンセルは以前の値を維持する |
| GUIと機器 | 設定3項目だけを変更 | 既存の適用・OK・キャンセルを使い、映像・入力への不要な再接続を行わない。設定画面へ説明文や反映時期の新たな案内を追加しない |

設定更新はGIGASの保存値を渡す処理に限り、Codexのconfig.tomlを編集しない。モデル選択の既存経路に3値の再適用を混ぜない。M4は同じ設定変換と最新値の取得経路をresumeに使う。

## 5. 設計・責務と実施順

runtime境界へ渡す値は3項目だけの不変値とし、storage.Settings全体への依存を作らない。起動とthread要求で設定名の変換を共用する。変換部品は値の組立のみで、保存・接続・UI・状態管理を持たない。

Shellは保存成功を確定し、既存のサービス境界へ値を渡す。Executionは新規開始時に設定を固定して要求と検査に用いる。Resourcesは作業場所・MCP・writerの所有を維持し、設定同期や指示探索を追加しない。設定専用のmanager・worker・台帳は設けない。

J1でruntime契約、J2で保存から開始まで、J3で設定UI、J4で実接続と実画面を確認する。各itemをred→green→構造確認→同じ試験の再実行→commitで進める。UIはProduct Designで既存画面を確認してから変更し、合意済みの画面配置を維持する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| done | J1: 起動・新規threadの設定がWeb/記憶/承認の値を使い、標準指示を抑制せず、実効権限の不一致を拒否する | new/regression | unit/integration | 不変値・共通変換、全8組合せ、固定制約・MCP上書きの維持。公開型・docstring・呼出し元も更新 |
| todo | J2: 保存成功した最新設定だけを次の新規開始へ反映し、開始中・既存threadと機器接続を不用意に変更しない | new/edge | integration | 保存失敗・キャンセル、開始準備中の変更、変更前後の新規開始、映像・入力非再接続 |
| todo | J3: 設定画面から3値を編集し、適用・OK・キャンセルで保存値と一致する | new/regression | integration/GUI | 既存値の表示と操作結果を検証。固定文言だけのassertを追加しない |
| todo | J4: 実app-serverで標準指示・設定・権限と実モデルI/Oを確認する | acceptance | integration/GUI | AGENTS/Skills、Web/記憶のON/OFF、通常資料編集・別会話資料参照、シェル通信制限、両承認方法。記憶の標準処理と即時生成保証を区別する |

## 7. 対象ファイル

runtime/codexのモデル・設定組立・launch・thread protocol、servicesの開始設定受渡し、app/shellの保存成功と変更判定、ui/settings_dialog、prompts/play.md、対応するtests、docs。初期設計と親仕様には進捗と後続の参照を反映する。

## 8. 検証

公開型はtype-boundary-review、docstringと利用例はdocstring-style、各green後はrefactor-after-greenを用いる。独立レビューとagentic-self-reviewでM0合意・工程範囲を照合する。

| command / 確認 | result | 検査対象 |
|---|---|---|
| 対象itemのpytest | not run | 設定伝達・要求/応答・GUI操作と副作用 |
| uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m3-root -q | not run | 新規開始・停止・機器・GUIの回帰 |
| uv run ruff format --check . / uv run ruff check . / uv run ty check --no-progress | not run | 形式・lint・型境界 |
| uv lock --check / uv build | not run | 依存・公開API・配布指示 |
| 実app-server・モデル・設定画面 | not run | 公開実効値と製品経路、標準読込と権限 |
| git diff --check、文書内容・リンク確認 | not run | 設定の所有・反映単位と公開説明 |

### J1 実装・検証結果

ExecutionSettingsの3値を起動とthread要求へ渡し、純粋な共通変換で公開設定を組み立てた。Executionは準備の最初のawaitより前に値を固定し、要求と返却値の検査へ使う。Resourcesは変更していない。直接利用する検証スクリプトは人間承認の検証を保つためuserを明示する。

`uv run pytest tests/unit/test_execution_configuration.py tests/unit/test_conversation_protocol.py tests/unit/test_codex_connection.py tests/integration/test_codex_service.py tests/integration/test_conversation_execution.py --basetemp=tmp/pytest-m3-j1 -q` は、未実装ExecutionSettingsのImportError 2件でred、実装後148 passed、構造確認後も148 passed。`test_codex_transport.py --basetemp=tmp/pytest-m3-j1-transport -q` は22 passed。

refactor-skipped: 設定値、変換、開始時の所有を分け、保存やResourcesへ依存を追加していない。重複した変換や不要な管理部品がないことを見直し、追加の構造変更は行わなかった。独立レビューの修正必須指摘なし。全体ruff・format（262ファイル）・ty、スクリプトのty、git diff --checkは成功した。実app-server・モデル・保存後の更新・UIはこの結果に含めない。

Product Designによる変更前確認では、通常権限の実画面で設定→Codexを開き、接続・利用状況・モデル既定値の配置と、スクロール外のOK・キャンセル・適用を確認した。未変更で閉じて正常終了した。記録は`tmp/u08-m3-ui/audit.md`と同ディレクトリのscreenshotsに置いた。

## 9. 先送り事項

既存threadの購読解除後の設定適用・resumeはM4、再開を跨ぐ設定・承認の結合はM6、実機I/Oとの通し受入はM7。記憶の標準生成は候補条件に従い、即時生成やOFF時の過去threadの一括再抽出を追加しない。

## 10. 完了チェックリスト

- [x] M2完了とM3の範囲・所有を確認した
- [ ] J1〜J4と各cycleの構造確認を記録した
- [ ] 3値の保存・起動・新規開始・UIを結合した
- [ ] 標準指示・設定・権限の実接続受入を完了した
- [ ] 公開型・docstring・docs・配布資材を整合させた
- [ ] 品質ゲートと独立レビュー、親・初期設計の進捗更新を終えた
