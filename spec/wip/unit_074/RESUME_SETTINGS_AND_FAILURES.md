# U08 M6 再開・設定・障害の結合検証

## 1. 目的・状態・起点

状態: **wip**。[U08親仕様](../unit_013/RESUME.md)のM6を担当する。Intent Delta: none。[M5](../../complete/unit_073/CONVERSATION_MANAGEMENT_UI.md)までに完成した画面と実行経路について、状態を跨ぐ設定反映と失敗後の保持・再操作を検証し、見つかった不具合を修正する。

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
| todo | N1: 設定・モデルを変更し、新規/閲覧から明示実行、ロード後変更へ進める | characterization/regression | integration/GUI | 本サービスと公開peerを使い、保存・実要求・確定表示を結ぶ。履歴と新着、active Goalへの追加指示も同じ境界で確認 |
| todo | N2: 追加履歴・準備・resume・Goal・通信の失敗後に内容を保持して明示再操作できる | characterization/edge | integration/GUI | 追加履歴、cwd欠落、resume送信後失敗、Goal拒否、通信断の代表経路。下位層の全故障組合せは複製しない |
| todo | N3: 実resumeへ現在の設定・権限・承認方法を適用し、標準記憶の残条件を確認する | acceptance | integration/runtime | 既存専用home、ON/auto_reviewとOFF/user。模擬FrameのMCP観測を併設。実機はM7 |

## 7. 対象ファイル

不足する結合試験は`tests/integration/`の会話・設定経路と既存peerを使う。製品の修正候補は`app/conversations.py`、`app/shell.py`、`ui/connection_panel.py`、`services/`、`runtime/codex/`だが、検出した振る舞いに必要な箇所だけを変更する。公開契約を変更した場合は型・docstring・利用文書も同時に更新する。

## 8. 検証

| gate | 状態・検査範囲 |
|---|---|
| itemごとのpytest・構造確認・独立レビュー | not run。状態を跨ぐ製品境界と故障後の操作 |
| 全unit/integration・ruff format/check・ty | not run。追加/修正後の全体回帰と静的検査 |
| uv lock --check / uv build | not run。依存整合・配布資材 |
| 実app-server・モデル | not run。実resume・権限・承認・標準記憶。認証資材と本文を検証reportへ転載しない |
| 文書レビュー・リンク・git diff --check | 着手時に実施。仕様・既存証拠・後続境界の整合 |

## 9. 先送り事項

M7はUSB Video Device・COM8を使い、GUIから新規/再開・再起動・途中介入・共通停止・代表的な機器障害を通す。実画像と記録の対応、利用文書と配布資材を確定する。標準記憶の即時抽出や攻略への有用性は保証せず、実プレイの長期評価はU10へ残す。

## 10. 完了チェックリスト

- [x] M5完了・対象範囲・既存根拠と不足を確認した
- [ ] N1〜N3の検証・必要な修正・構造確認・独立レビューを記録した
- [ ] 公開文書・品質ゲート・実接続の結果と限界を整合させた
- [ ] 親・初期設計を更新し、M7の実機受入へ引き継いだ
