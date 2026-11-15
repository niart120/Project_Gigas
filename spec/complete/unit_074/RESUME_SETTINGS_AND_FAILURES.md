# U08 M6 再開・設定・障害の結合検証

## 1. 目的・状態・起点

状態: **complete**。[U08親仕様](../../complete/unit_013/RESUME.md)のM6を担当する。Intent Delta: none。[M5](../../complete/unit_073/CONVERSATION_MANAGEMENT_UI.md)までに完成した画面と実行経路について、状態を跨ぐ設定反映と失敗後の保持・再操作を検証し、見つかった不具合を修正する。

利用者が保存会話を選び、モデルや設定を変更して続きを送る場合、履歴を遡る途中や実行準備で失敗する場合を扱う。新しい管理機能や画面構成を設計する工程にはしない。

## 2. 対象範囲・対象外

設定UIから保存・サービス・start/resume/settings/updateへの結合、保存履歴と新着、Goal操作、追加履歴・実行準備・通信失敗後の保持と明示再操作を対象とする。実resumeの権限・承認方法の再適用、標準記憶の生成対象・参照経路の残確認も含める。

自動再試行・再接続、追加指示とGoal自動継続の順序保証、独自記憶生成・強制抽出・待機、全故障組合せのGUIへの複製は対象外。M5で実証した所属別新規、通常再開、保存Goal再開・共通停止、停止後切替、下書き取消と再接続保持は参照する。AGENTS/SkillsやWeb検索の新規開始試験を繰り返さない。実機I/OはM7、数時間の運転はU10。

## 3. 関連文書・着手条件・冒頭確認

[02 実行](../../initial/02-runtime.md)、[04 記録・指示](../../initial/04-agent-memory.md)、[07 検証](../../initial/07-verification.md)、[08 公開契約](../../initial/08-sdk-compatibility.md)、[09 操作](../../initial/09-control-flows.md)、[10 UI](../../initial/10-ui-interaction.md)を正本とする。先行成果は[M3](../../complete/unit_071/EXECUTION_SETTINGS_AND_INSTRUCTIONS.md)、[M4](../../complete/unit_072/CONVERSATION_SELECTION_AND_RESUME.md)、[M5](../../complete/unit_073/CONVERSATION_MANAGEMENT_UI.md)。

M5は全体1007件、静的検査、配布build、実画面・実app-server受入を完了した。設定保存、Panelのモデル選択、サービスのresume故障は既存試験で確認済みだが、それぞれを画面まで通す境界に不足がある。既存peer・制御された待機を使い、実装構造の写しや固定文言の検査は増やさない。

M3の実会話からの標準記憶抽出は未観測。標準の候補条件、通常の起動時処理と出力metadataを確認する。即時生成は合格条件ではなく、抽出を観測できなければ理由・条件・範囲を記録する。製品の設定や起動に起因する不具合は修正し、候補時刻の操作や独自抽出で補わない。

## 4. 振る舞い仕様

| 対象 | 期待する観測結果 |
|---|---|
| 入力欄のモデル | 新規・閲覧中は候補選択だけでstart/resume/settings/updateを送らない。明示実行で選択値を使い、ロード後はsettings/updateの受理後に変更を確定する。保存既定値を上書きしない |
| 実行設定 | Web・記憶・承認方法は既存設定UIで保存し、次のstart/resumeへ適用する。ロード済み会話の設定をその場で差し替えない |
| 履歴とGoal | 保存本文に新着が続く。active Goalへの追加指示は標準のresume→turn/startまたは既存実行への配送を使い、Goalとの順序保証を追加しない。拒否されたGoal操作で保存表示を勝手に変更しない |
| 追加履歴の失敗 | 既存本文・閲覧位置・次cursorを保ち、明示操作で同じページを取得できる。自動取得を繰り返さない |
| 実行準備・通信失敗 | 選択・履歴・原稿を保持し、既存通知詳細へ失敗を残す。cwd欠落では再作成せず実行を拒否する。resume送信後に結果不明なら成功・停止済みと断定せず、明示的な再接続から再操作する。停止・切断と資源回収を妨げない |
| 実効権限・記憶 | 再開時にもworkspace-write・network_access=false・on-requestと現在の承認者を適用する。実要求と結果を確認し、拒否後の自動切替をしない。記憶OFFで既存資材を消さず、ONとゲームMCP利用後の生成対象状態を確認する |

## 5. 責務・実施順

N1→N2→N3の順とする。画面は表示と操作、サービスは選択・実行所有、app-serverは設定・Goal・承認・記憶を保持する。既存の所有境界を使い、故障対策用の状態台帳や復旧サービスを追加しない。

既存動作の確認はcharacterizationとして先に実行し、最初から成功したものをredと偽らない。不具合を検出した場合は失敗を固定してから修正し、green後の構造確認・同じ試験・独立レビューを行う。itemごとにコミットし、実測のためだけの補助はtmpへ隔離する。

## 6. TDD Test List

| status | item | type | layer | notes |
|---|---|---|---|---|
| refactor-skipped | N1: 設定・モデルを変更し、新規/閲覧から明示実行、ロード後変更へ進める | characterization/regression | integration/GUI | 本サービスと公開peerで3件成功、関連150件成功。製品変更なし |
| refactor-skipped | N2: 追加履歴・準備・resume・Goal・通信の失敗後に内容を保持して明示再操作できる | characterization/edge | integration/GUI | 5件成功、関連118件成功。製品変更なし |
| done | N3: 実resumeへ現在の設定・権限・承認方法を適用し、標準記憶の残条件を確認する | acceptance | integration/runtime | 実resume/通常送信各2回。ONの終了待ちは補助側のQt処理不足と特定。実機はM7 |

## 7. 対象ファイル

不足する結合試験は`tests/integration/`の会話・設定経路と既存peerを使う。製品の修正候補は`app/conversations.py`、`app/shell.py`、`ui/connection_panel.py`、`services/`、`runtime/codex/`だが、検出した振る舞いに必要な箇所だけを変更する。公開契約を変更した場合は型・docstring・利用文書も同時に更新する。

## 8. 検証

| gate | 状態・検査範囲 |
|---|---|
| itemごとのpytest・構造確認・独立レビュー | N1〜N3完了。N3の実測と補助の診断を下記へ記録 |
| `uv run pytest tests/unit tests/integration --basetemp=tmp/pytest-m6-final -q` | 1015 passed（89.77秒）。N1/N2追加後の全体回帰 |
| `uv run ruff format --check .` / `uv run ruff check .` / `uv run ty check --no-progress` | 成功。format対象288ファイル、全体の形式・lint・型 |
| `uv lock --check` / `uv build` | 成功。83依存、sdist/wheel。依存整合・配布資材 |
| 実app-server・モデル | ON/auto_reviewとOFF/userの実resume、画像保存、承認と取消し、記憶参照/生成対象/保持を確認。実抽出は未観測 |
| 文書レビュー・リンク・git diff --check | 着手時に実施。仕様・既存証拠・後続境界の整合 |

### N1の結果

設定UIの保存から本ApplicationShell・CodexServiceThreadを通し、新規/閲覧中のモデル候補がstart/resumeへ渡ること、ロード後のモデル変更はsettings/update受理まで確定せず拒否後も元の値を使うことを確認した。Web・記憶・承認方法の変更はロード中に差し替わらず、次の明示再開へ反映された。保存モデル既定値は不変、保存履歴に新着を加え、active Goalへの追加送信でも独自のGoal更新を送らない。製品コードの修正は不要だった。

| command | 結果・範囲 |
|---|---|
| `uv run pytest tests/integration/test_execution_settings_flow.py --basetemp=tmp/pytest-m6-settings -q --tb=short` | characterization 3 passed（2.14秒）。拒否後の次turn・Goal不変も確認して3 passed、構造確認後も3 passed（2.00秒） |
| `uv run pytest tests/integration/test_execution_settings_flow.py tests/integration/test_execution_settings_gui.py tests/integration/test_codex_gui.py tests/integration/test_conversation_switching.py tests/integration/test_conversation_controller.py tests/integration/test_settings_apply_contract.py tests/integration/test_settings_recovery.py tests/integration/test_settings_accept.py --basetemp=tmp/pytest-m6-settings-regression -q --tb=short` | 150 passed（13.28秒）。既存の設定・対話・切替・保存失敗との回帰 |
| 追加2ファイルのruff check/format、全体ty、git diff --check | 成功。実app-server・モデル・native画面・機器は未使用 |

初期の失敗は試験側のSettings属性名と閉じたpeerの再利用に起因し、製品のredとは扱わない。接続ごとに新peerを作り、公開の保存Goalだけを次接続へ持たせた。Refactor status: **refactor-skipped**。既存ResumePeerが公開RPC/通知、共通ConversationGuiがShell寿命と結果待ち、試験が操作と期待値を持ち、新しい状態管理はない。公開API・文書契約は変更していない。

独立レビューで公開要求・受理前後・拒否後の次送信・試験責務を確認し、必須修正なし。仕様のgate行はN1完了と後続未実行を分けて更新した。

### N2の結果

追加履歴失敗では同じ文書・選択・閲覧位置とcursorを保持し、明示クリックだけで同じ要求を再送できた。作業場所がない会話は履歴・Goal・原稿を表示したまま実行を拒否し、アプリは場所を再作成しなかった。試験側で場所を戻した後の送信は成功した。

送信済みresumeの失敗後は、その接続で再送してもresume/turn/startを増やさず、切断・再接続後の明示送信で再開した。本文のnot_sentはturn/start未送信を示し、resumeの成否が確認できたという意味ではない。Goal削除の拒否では目的と原稿を保持し、明示再操作でだけ削除した。通信断では履歴・次の原稿・通知詳細を保ち、writer所有権の解放後も自動再接続・再送はなかった。

| command | 結果・範囲 |
|---|---|
| `uv run pytest tests/integration/test_conversation_failures_gui.py --basetemp=tmp/pytest-m6-failures -q --tb=short` | characterization 5 passed（2.17秒）、構造・契約確認後も5 passed（2.14秒） |
| `uv run pytest tests/integration/test_conversation_failures_gui.py tests/integration/test_execution_settings_flow.py tests/integration/test_conversation_controller.py tests/integration/test_conversation_view.py tests/integration/test_conversation_resume.py tests/integration/test_conversation_switching.py tests/integration/test_codex_gui.py --basetemp=tmp/pytest-m6-failures-regression -q --tb=short` | 118 passed（13.64秒）。既存の選択・表示・再開・通常対話との回帰 |
| 追加1ファイルのruff check/format、全体ty、git diff --check | 成功。実app-server・モデル・native画面・機器は未使用 |

初回の1件失敗はGoal削除の試験期待から既存origin=userを落としていたためで、製品不具合ではない。Refactor status: **refactor-skipped**。N1のShell寿命・peerを再利用し、local helperは履歴入力・閲覧操作・実行副作用の期待だけに限定した。副作用のないconfig/readまで禁止する過剰な期待は外した。製品コード・公開API・文書契約に変更はない。

独立レビューで失敗境界・保持・明示再操作・writer所有権解放を確認し、必須修正なし。N1の設定成功経路とN2の故障経路を重複させていないことも確認した。

### N3の実接続結果

既存の専用home・採用版0.167.0・gpt-6.1-sol/lowを使い、専用settingsを実SettingsDialogのOK経路で保存してから接続した。公開サービスから保存会話を選び、明示的なresumeと通常送信を各ケース1回だけ実行した。Qtはoffscreenで、nativeの設定・承認カード操作や実機確認とは区別する。

| ケース | 観測した事実 |
|---|---|
| ON・auto_review | Web live、記憶生成/参照ON、外部MCP利用による無効化なしをresumeへ渡した。応答は同じcwd/Project、workspaceWrite、networkAccess=false、on-request、auto_review。模擬画像のobserve 1回と無害なWrite-Output 1回が完了し、自動審査approvedの対象IDが同じcommandに一致した |
| OFF・user | Web disabled、記憶生成/参照OFFとuserをresumeへ渡し、同じ実効sandboxを確認した。本人承認要求はaccept/cancelを提示し、公開回答経路でcancelを1回配送した。commandはdeclined、turnはinterruptedとなり、別権限での再試行はなかった |
| 記録 | ONは同じrecordsへ提出1・確定本文2・観測1を追記。MCP返却PNGの168 bytesと保存画像・observation_idが一致した。OFFは提出1・確定本文1だけ。各実行で監査した4会話の旧JSONL prefix/PNGを保持し、対象外3会話は不変だった |
| 標準記憶 | ONはゲームMCP観測後もmemory_mode=enabled、公開thread/memory/readで既存80 bytesのsummaryを参照できた。OFFはdisabled、公開参照なし、既存summaryのhashは不変。読取り専用監査では標準6時間のidle等の必要条件を満たす候補0、抽出結果0、既存統合jobにerrorなしだった |

実行コマンドは`$env:PYTHONPATH = (Get-Location).Path`と`$env:QT_QPA_PLATFORM = 'offscreen'`を設定し、`uv run python tmp/live_m6_acceptance.py --case on --run-dir tmp/u08-m6-acceptance/on --output tmp/u08-m6-on.json --execute-model`、続いて同じコマンドのcase/run-dir/outputをoffへ替えたもの。最初のimport失敗はmainより前でサーバー・モデル利用0回であり、検索パスを明示して解消した。

ONは送信・観測・承認・本文・記録の確認後、補助の終了待ちで40秒のTimeoutErrorとなった。元reportのall_checks_pass=falseとshutdown_completed=falseを保持し、全体成功へ書き換えていない。モデルなしの`tmp/repro_m6_qt_shutdown.py`で、設定/サービスのthread終了・映像回収済みでもSettingsWorkerの遅延削除通知が未配送であることを再現した。補助の手動Qt待機へDeferredDeleteの配送だけを加えると正常終了した（`tmp/u08-m6-qt-shutdown-repro.json`、`tmp/u08-m6-qt-shutdown-fixed.json`）。製品は変更せず、ONのモデル送信も繰り返していない。修正した補助によるOFFは終了コード0、all_checks_pass=true、shutdown_completed=true、記録失敗なしだった。

補助3ファイルのruff/format/ty・compile/importは成功した。Refactor status: **refactor-done（検証補助のみ）**。標準記憶のmetadata読取を分け、既存M4の待機・公開証跡・記録比較を再利用した。手動Qt pumpの遅延削除配送はM6補助へ閉じた。製品に新しい待機や自動復旧は追加していない。

抽出未観測は標準候補条件と合わせて記録し、即時生成・本文への注入・記憶の有用性を成功と主張しない。SQLiteは検証用の読取りであり製品経路には使用しない。シェルの通常編集・別会話参照・通信の実拒否はM3の実証を参照し、M6では実resume応答と承認配送を確認した。監査した4会話をhome内の全記録へ広げず、ONの終了完了未観測と補助修正後のOFF終了を区別する。

独立監査で、両ケース各10 RPCの成功対応、保存設定と実要求/応答、承認対象と実配送、実ファイルの旧prefix/PNG保持を確認し、必須修正なし。公開記憶の結果と、生成対象のDB補助監査・未観測の抽出を混同していないことも確認した。

## 9. 先送り事項

M7はUSB Video Device・COM8を使い、GUIから新規/再開・再起動・途中介入・共通停止・代表的な機器障害を通す。実画像と記録の対応、利用文書と配布資材を確定する。標準記憶の即時抽出や攻略への有用性は保証せず、実プレイの長期評価はU10へ残す。

## 10. 完了チェックリスト

- [x] M5完了・対象範囲・既存根拠と不足を確認した
- [x] N1〜N3の検証・必要な修正・構造確認・独立レビューを記録した
- [x] 公開文書・品質ゲート・実接続の結果と限界を整合させた
- [x] 親・初期設計を更新し、M7の実機受入へ引き継いだ
