# U08 M0 責務・保存境界の設計

## 1. 目的・状態

状態: **complete（調査・設計・初期設計改訂・後続への引継ぎを完了）**。2026-11-14。[U08親仕様](../../wip/unit_013/RESUME.md)のM0を担当する。製品実装・製品経路の実接続・GUI実機受入は未着手であり、親U08はwipを維持する。

管理機能をapp-serverへ寄せ、GIGASはゲームI/Oと人間の介入を担う。攻略補助は実プレイで不足を確認してから検討する。対話で確定したD1〜D5を採用版の公開契約へ対応させ、初期設計を正本として整理した。議論の経緯や同じ契約を本書へ重複蓄積しない。

## 2. 対象範囲・対象外・前提

対象は責務・保存先・記録形式・指示・設定・再接続・会話管理UIの設計、公開APIと設定の成立条件、初期設計改訂、マイルストーンと受入条件の引継ぎである。U06・unit_061/062・unit_054の会話実行・共通停止・契約整理を前提とする。作業規約は[AGENTS.md](../../../AGENTS.md)に従う。

製品コード・依存・利用者の認証や開発用設定は変更しない。実データの移行・削除、後方互換層、独立MCPサーバーの起動機能、攻略Skills・独自記憶整理・監督エージェントは対象外。方式確認の隔離プローブと製品実装を区別する。後続子仕様を一括生成しない。

## 3. 確定した責務と契約の正本

| 判断 | 確定内容 | 正本 |
|---|---|---|
| D1 home・設定 | 全Project共通の専用CODEX_HOMEは%LOCALAPPDATA%/GIGAS/codex_home。GIGAS側でログインし、開発用認証・設定・記憶を同期しない。GIGAS設定は同GIGAS配下のsettings.toml。独自診断保存先は追加しない | [04 保存先・設定](../../initial/04-agent-memory.md)、[08 設定経路](../../initial/08-sdk-compatibility.md) |
| D2 Project・作業場所 | 任意のProject、1 Project＝1フォルダー。新規配置先は起動時cwdを初期位置とするピッカーで選び、既存フォルダーも登録可能。位置の正本はapp-serverのroots。未所属は`unassigned/<workspace-id>/`。既定projectsルート・位置台帳・複数roots・Project昇格・自動移動なし | [04](../../initial/04-agent-memory.md) |
| D3 記録 | Project・未所属とも作業フォルダーの`records/<thread-id>/`。再接続でも分割しない。schema_version=1、5種JSONLとPNG、限定した提出・確定本文。単純追記、保持期限・自動削除・修復・reload待ち・旧互換なし。原記録は編集しない運用、権限による強制保護は必須にしない | [04 JSONL・保存契約](../../initial/04-agent-memory.md) |
| D4 指示・Skills・記憶 | 最小限の共通指示、標準AGENTS/Skills探索、自由な攻略資料。Web live／disabledと標準記憶の生成・参照一括切替は既定ON、OFFでも既存記憶を削除しない。Apps・Plugins・Hooks無効、追加外部MCPなし | [04](../../initial/04-agent-memory.md)、[08](../../initial/08-sdk-compatibility.md) |
| D5 閲覧・実行・権限 | 会話選択は履歴表示だけ。明示送信・Goal作成・目的編集・再開で実行し、Goal削除はresumeなしで行う。実行中の切替・新規は停止後に行う。開閉式サイドバー、所属別＋、下書き破棄確認。workspace-write・シェル通信制限、承認条件on-request、承認者auto_review既定／user切替 | [02 資源所有](../../initial/02-runtime.md)、[09 操作順](../../initial/09-control-flows.md)、[10 UI](../../initial/10-ui-interaction.md) |

Project・thread・Goal・標準記憶はapp-serverが永続管理する。GIGASは公開APIから読み、独自ログによる会話復元やGoalの階層管理を持たない。外部Codexで攻略相談・資料整理をする場合は作業フォルダーを渡せるが、両アプリの会話共有・相互実行は必須にしない。

限定した本文は攻略資料の材料であり、会話全体の複製ではない。ユーザーは既存の提出イベント、Codexは進捗報告を含む確定本文から保存し、受付照合・最終回答の選別・新しい時刻観測点を設けない。保存部品の呼出し側が保存先と寿命を管理し、単独Python利用に架空のProject・thread・共通保存先を要求しない。

## 4. 公開契約による実現方式

### 作業場所・thread ID・記録先

新規Projectは選択先に作業フォルダーを作ってproject/createへ唯一のrootを渡す。既存フォルダー登録では内容を移動・初期化しない。GIGASの前回選択はUIの補助情報としてsettings.tomlへ残せるが、位置の正本にはしない。

新規未所属会話では最初の明示送信時にworkspace-id（UUID）を作り、その作業フォルダーをthread/startのcwd・runtimeWorkspaceRootsへ指定する。thread IDはまだ不要である。既存会話はthread/readの保存cwdを使い、独自対応表やフォルダー走査で場所を推定しない。既存作業フォルダーが欠けていれば勝手に再作成せず、履歴閲覧を許し実行時に通知する。recordsだけの欠落は明示実行時に作成する。

thread/startの応答で実際のthread IDを実行管理が保持し、実行前に`records/<thread-id>/`を保存部品へ渡す。UIの選択対象IDと現在の実行対象IDを区別し、永続的な会話ID台帳を別に作らない。MCPは開始時に接続先を公開するが、保存先の結合前にはturn/start・thread/goal/setを送らない。

### Codexへの案内

保存に関するdeveloperInstructionsでは、作業フォルダーと`records/<thread-id>/`配下のevents.jsonl・`observations/<observation-id>.png`という構造、原記録を編集しないことだけを案内する。作業フォルダーはcwdで設定する。固定の攻略資料名・様式は指定しない。

当該会話の正確な記録先パスを最初から渡すことは要件にしない。GIGAS内部での保存先確定とは別の処理であり、パス案内目的の追加API呼出し・指示差替え・MCP結果への保存先追加は行わない。通常のプレイでは観測結果の画像を使い、保存画像が必要ならobservation_idを手掛かりに探す。直接のパス案内は実プレイで不足が確認された場合だけ再検討する。

### 設定・指示の適用

GIGAS所有の設定はsettings.tomlから起動時と新規thread/start・既存thread/resumeへ上書きする。実行中への即時反映やconfig.tomlとの双方向同期は追加しない。モデル・推論強度はロード済み実行対象にはthread/settings/updateで次ターンへ反映する。新規入力・履歴閲覧中は現在の入力欄に選択を保持し、明示実行時のstart/resumeへ渡す。閲覧だけの選択変更でresumeやsettings/updateを送らない。

標準AGENTSとSkillsの読込み抑制を外す。$HOME/.agents/skillsも標準探索対象であり、専用homeで全Skillsまで隔離する意味ではない。記憶はfeatures.memories・generate_memories・use_memoriesを一括切替し、disable_on_external_context=falseでゲームMCP利用だけによる生成対象除外を避ける。候補source・経過時間・利用枠等は標準処理に任せ、OFFで作った過去threadをON時に一括再抽出しない。根拠と設定値は08に集約した。

### 閲覧・再接続

閲覧はthread/read・thread/turns/list・thread/items/list・thread/goal/getを用い、resumeしない。公開本文・reasoning要約・activity（Web検索を含む）を既存表示へ対応させ、Planと質問・承認カードの完全再現は求めない。古い要求を再び回答可能にせず、recordsから履歴を補完しない。Goal再開の活性条件にresumeや資源準備の完了を含めず、押下後に準備する。Goal削除は選択thread IDへclearを直接送り、実行環境・作業場所・MCP・writerを要求しない。

切替では共通停止後、旧threadのunsubscribe・要求失効・MCP閉鎖・保存drainを行う。unsubscribeだけでは停止・アンロードを保証しない。採用版の購読者なし・idle・非実行の条件下では、変更設定付きresumeで既存threadが再構築される経路を使う。再起動後も現在のMCP・記録先・指示・設定をresume前に準備する。

active Goalへの追加指示はresume→turn/startとし、追加指示受付前のGoal進行を許容する。順序保証のためのGoal休止・再有効化・本文事前注入は設けない。失敗時は選択・履歴・本文を保持し、自動再送せず、未受付・停止済みと断定しない。既存通知・詳細と停止・切断を使い、段階別の細部は実装担当へ渡す。

## 5. 成立確認の根拠と限界

採用版はcodex-cli 0.167.0、公開ソースcommitは086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a。最新版への更新は行っていない。公開schema・実装の参照先と採用設定は[08](../../initial/08-sdk-compatibility.md)に記載した。以下の限定接続は製品コードへの組込み、認証・モデル・Windows権限の成功証明ではない。

| 対象 | 確認したこと | 製品側に残る検証 |
|---|---|---|
| Project・作業場所 | 1 rootのProject作成・read・再起動後の保持。未所属一覧、保存cwdのread・再起動後の再解決 | 製品の所属一覧・フォルダーピッカー・新規と既存登録・欠損、M1・M2・M5 |
| 指示・設定 | Web live、記憶ON/OFF、標準文書上限32768を設定結果で確認。隔離作業場所のSkill発見、AGENTS.mdのinstructionSources掲載 | 実モデルでの読込み・Web利用・記憶対象と参照、M3・M6。抽出・統合は今回実行していない |
| 再接続 | 停止済み・購読解除後のresumeで承認者auto_review→user、MCP URL・認証値・ツール一覧の差替え。プロセス再起動後もresume | 製品の資源回収・実I/O・通常送信・Goal操作、M4・M6 |
| 履歴・Goal | 公開履歴ページとGoalなしの取得。active Goalの再開順序はソースで確認 | 全履歴項目の描画・新着との結合・active Goalの実再開、M4・M5・M6 |
| 閲覧中の設定・Goal削除 | 採用版のソースでsettings/updateはロード済みthreadが必要、goal/clearは保存済みthreadを直接操作可能と確認。根拠は08 | 閲覧中のモデル選択で通信しないこと、選択threadへのclearがresumeや実行準備を伴わないこと、M1・M4・M5・M6 |
| 空thread | 未送信threadのunsubscribe後の指示差替えresumeはno rollout found。公開APIによる試行結果 | 正確なパス案内は要件から外したため解決機構は不要 |
| 権限 | reviewer指定の受理を確認。未準備homeのworkspace-write要求にreadOnlyが返ることを観測 | readiness・実効設定の検査と実書込み、別会話資料参照、シェル通信制限、審査・人への要求、M2・M3・M6 |

認証は既存account/login/start・account/read・model/listを専用homeで使う。Windows準備も既存readiness・明示setupを使い、権限低下を黙って採用しない。専用homeは設定・記憶の保存分離であり、認証やOS権限の完全隔離を保証するものではない。

## 6. 初期設計改訂とM1以降への引継ぎ

[親仕様第6節](../../wip/unit_013/RESUME.md#6-マイルストーン作業単位実施順)を工程の正本とする。M1からM7まで依存順に進める。マイルストーンとunitは一対一を強制せず、次の範囲だけ子仕様化する。各段階で変更する部品と呼出し元を同時に更新し、次段階を待たず動作する経路を受け入れる。

| 担当 | 主な対象と引継ぎ | 担当段階の終了までに確認すること |
|---|---|---|
| M1 専用home・設定・管理API | app・storage/settings・runtime/codexの起動・認証・公開管理API。設定の正本と前回選択を分ける | 分離homeのログイン・再認証、設定解決、Project/会話情報・履歴・Goal取得、保存済みGoalのclear。既存の新規実行・停止も維持 |
| M2 記録と新規開始 | storage/project.pyのProjectWriterをGameIoRecordWriterへ整理。_conversation_execution・_conversation_resources・_conversation_protocolの新規開始、指示内の保存先案内、全呼出し元・試験・docstring・公開docsも更新 | 04の保存契約と単独利用、project.json・固定memory依存の撤去、cwd→start→ID→records結合→通常送信/Goal。作業場所の権限と構造案内も同時に変更し、Projectあり／なしの新規実行・観測保存・停止を確認。正確なパス案内用API・MCP結果拡張なし |
| M3 実行設定・標準指示 | M2の開始経路へlaunch.py・prompts/play.md・Python内埋込み指示の残る制限と設定を結合 | 最小共通指示とAGENTS/Skills、Web・記憶・承認等の設定、実効権限・実モデルI/O。保存部品の置換に必要な呼出し元改修はM2から持ち越さない |
| M4 切替・既存会話の再接続 | 選択/実行対象の分離、停止後のunsubscribe・要求失効・MCP閉鎖・保存drain、現在資源・設定によるresume | サービス経由で通常送信・Goal目的編集/再開、閲覧中のモデル選択・resumeなしのGoal削除を確認。同一プロセスと再起動後も同じcwd・recordsで操作でき、過去操作・古い要求を再送しない |
| M5 会話管理UI | 既存Qtと[配置モック](assets/conversation-sidebar-proposal.png)、履歴・新規・既存再開・モデル/Goal操作をM4の経路へ結合 | 停止前の切替禁止、新規/未所属/Project、Web activity、停止後の閲覧・新規開始・再開、資源回収をUIで確認。閉状態・空・読込中・失敗も実画面で確認 |
| M6 再開・設定・障害の結合検証 | 新規・閲覧・実行を跨ぐモデル/Goal操作・履歴新着・権限・通知詳細 | active Goalの先行継続を許容。欠損・拒否・結果不明でも履歴・本文を守り停止可能。auto_review/user・シェル制限を実経路で確認。基本経路はM5までに完了 |
| M7 通し受入 | GUI・実機・README/docs・配布資材 | 新規/既存・途中介入・停止・再起動・代表障害を通す。製品経路の実測と未検証を区別する |

改訂した正本は[01 概要](../../initial/01-overview.md)、[02 実行](../../initial/02-runtime.md)、[03 ゲームI/O](../../initial/03-game-io.md)、[04 保存](../../initial/04-agent-memory.md)、[05 工程](../../initial/05-implementation.md)、[06 配置](../../initial/06-repository.md)、[07 検証](../../initial/07-verification.md)、[08 外部契約](../../initial/08-sdk-compatibility.md)、[09 フロー](../../initial/09-control-flows.md)、[10 UI](../../initial/10-ui-interaction.md)。公開利用文書は現行製品の説明を保ち、実装変更時に新契約へ更新する。完了済み仕様は当時の実績として保持する。

## 7. 振る舞い仕様・TDD Test List

本M0の製品TDDはnot applicable。合意・公開契約・根拠・初期設計・担当と期限を照合する文書作業である。[親仕様第7節](../../wip/unit_013/RESUME.md#7-振る舞い仕様tdd-test-listの受入観点)と[07のU08受入](../../initial/07-verification.md#u08で追加置換する受入)を後続TDDの起点とする。詳細項目・故障注入点は着手する子仕様で定め、文章の固定文字列や見出しの一致を製品テストにしない。

## 8. 対象ファイル・検証

対象は本書・親仕様・関連初期設計と索引、採用モック、[調査コード](assets/probe_m0.py)、[限定接続の結果](assets/probe-result.json)。src/・tests/・依存・公開利用文書は変更していない。

実行した限定接続のコマンドは次のとおり。出力先は新しい隔離ディレクトリを指定し、以後の試行でも再利用しない。

```powershell
uv run python -X utf8 spec/wip/unit_068/assets/probe_m0.py --output C:/Users/train/.codex/visualizations/2026/11/14/01a1d926-9c19-7a03-80bc-b38940aa864b/research/m0-final-probe-9
```

2026-11-14に終了コード0。上のコマンドは実行時の配置で、現在の再現コードは本書と同じunitのassets配下にある。出力JSONは隔離ルートだけを`<probe-root>`へ置換して保存した。調査コードは独立したCODEX_HOMEとローカルのMCP検査用サーバーを使い、既存認証をコピーせず、モデル・ゲーム機器を使わない。履歴永続化のため利用不能なlocalhostのモデル接続先へ1 turnを提出し、中断または既終了を確認する。1秒待機は調査用の永続化待ちで、製品の同期契約・テスト基盤へ持ち込まない。

文書は合意・差分・責務境界・担当段階の整合を読んで確認した。レビュー指摘に対応し、閲覧中のモデル選択、Goal再開の活性条件とresumeしない削除、呼出し元を含む工程境界、初期設計からの過去の実測・経緯の削除を反映した。`uv run python -X utf8 -`による検査で、対象13文書の相対参照・アンカー・JSONL例5行、調査コードのPython構文と結果JSON、68 unitの番号一意性を確認し、欠落・仮テキストは0件だった。`git diff --check`は成功。調査コードのみ`uv run ruff format`で整形し、製品コードは変更していない。製品のunit/integration・型検査・package buildはnot applicable、製品経路・実モデル・GUI・実機はnot runとしてM1〜M7へ割り当てた。

M1着手時の全体lintで本調査コードの53件を検出したため、型注釈・docstring・JSON境界の検査・CLI出力を整えた。動作条件や公開要求の順序は維持し、全体除外は設けていない。2026-11-14に`uv run ruff format --check spec/complete/unit_068/assets/probe_m0.py`、`uv run ruff check spec/complete/unit_068/assets/probe_m0.py`、`uv run ty check --no-progress spec/complete/unit_068/assets/probe_m0.py`が成功した。次の再実行も終了コード0で、Project/cwd永続化、記憶設定、MCP差替え、履歴・Goal取得を再確認した。

```powershell
uv run python -X utf8 spec/complete/unit_068/assets/probe_m0.py --output tmp/u08-m0-probe-lint-20261114-02
```

再実行結果は指定先のresult.jsonに保存し、当初のassets/probe-result.jsonは変更していない。repo配下の隔離作業場所のためinstructionSourcesには祖先のrepo AGENTS.mdも含まれる。未準備homeの実効sandboxはreadOnlyであり、権限・モデル推論・本人認証・実機の受入は依然として未実施である。

## 9. 先送り事項

製品実装・製品経路の検証は第6節の担当と完了条件へ引き継いだ。M0内の未決事項はない。数時間の運転・記録量はU10、正確な保存先パスの案内や攻略補助・資料規約は実プレイで不足が確認された後の作業選定とする。固定の攻略様式や独自記憶整理を先行追加しない。

次の候補はM1の専用home・設定・管理APIの子仕様作成。本M0完了から製品実装へ自動着手しない。

## 10. 完了チェックリスト

- [x] D1〜D5の合意を確定し、最新の記録先案内方針を反映した
- [x] 採用版の公開API・設定・制約、限定接続と未検証範囲を記録した
- [x] 初期設計を改訂し、M0を議論の経緯から確定契約・根拠・引継ぎへ整理した
- [x] M1〜M7の依存・受入条件を親仕様と整合させ、製品実装には進んでいない

完了に伴いunit_068をspec/wipからspec/completeへ移動し、参照元を更新した。親unit_013はwipのままとする。
