# 05-implementation.md — 実装計画・工程の依存関係

[文書一覧](README.md)

初期版の製品範囲は[01](01-overview.md)に従います。U07・U09は初期版対象外です。本書は工程の入口、依存関係、実施順を所有します。各工程の到達点・分割・受入条件は親仕様、具体的なAPI・TDD・検証結果は着手時に作る子仕様へ置きます。文書の役割と移動規則は[06](06-repository.md)に従います。

U08は[M0](../complete/unit_068/MANAGEMENT_STORAGE_DESIGN.md)・[M1](../complete/unit_069/MANAGEMENT_API.md)・[M2](../complete/unit_070/RECORDING_AND_NEW_CONVERSATION.md)に加えて[M3の実行設定・標準指示の結合](../complete/unit_071/EXECUTION_SETTINGS_AND_INSTRUCTIONS.md)を完了しました。[M4の切替・既存会話の再接続](../wip/unit_072/CONVERSATION_SELECTION_AND_RESUME.md)に着手しています。各段階の進捗・受入状態は[親仕様](../wip/unit_013/RESUME.md)に集約します。

## 開発方針

各段階は後続でも使う本実装とその試験として残します。変更する部品と呼出し元を同じ工程で更新し、公開API文書・配布資材も合わせます。後続工程を待たなければ新規開始や停止が動かない区切りを設けません。

保存コードはstorageが所有し、その利用経路と一緒に変更します。保存部品をProject管理APIへ依存させず、呼出し側が記録先と寿命を決めます。専用の記憶整理・退避・復元、旧形式の移行・互換層は追加しません。

全クラス・全イベント詳細・将来の設定項目を先に作りません。模擬機器と固定通知は試験資産として使い、製品経路に未実装の成功応答や偽の実機画像を置きません。必要な接続調査の結果は本実装の契約と継続利用する試験へ引き継ぎます。

## 工程と前提

U番号は製品の到達点、unit_XXXは作業仕様の識別子です。完了工程の詳細な結果・実測・経緯は各作業仕様に集約し、本書へ複製しません。

| 工程・親仕様 | 状態・前提 | 所有する範囲 |
|---|---|---|
| [U01 起動・設定](../complete/unit_001/APP_STARTUP.md) | complete | 起動、設定、GUIと配布の基礎 |
| [U02 Codex接続](../complete/unit_007/CODEX_CONNECTION.md) | complete。U01に依存 | 公開app-server接続、認証、受信・要求応答・終了 |
| [U03 映像取得](../complete/unit_008/VIDEO_CAPTURE.md) | complete。U01に依存 | 取得元、プレビュー、最新フレーム、切断・終了 |
| [U04 記録付き観測](../complete/unit_009/RECORDED_OBSERVATION.md) | complete。U02・U03に依存 | 観測・PNG保存とMCPへの画像返却 |
| [U05 入力と即時停止](../complete/unit_010/INPUT_AND_STOP.md) | complete。U04に依存 | 有限の入力列、事後観測、停止・解除 |
| [U06 Goalによる継続](../complete/unit_011/GOAL_EXECUTION.md) | complete。U02・U05に依存 | 共通対話UI、Goalと同一セッション内の継続・再開 |
| [U07 計画的終了](../planned/unit_012/PLANNED_STOP.md) | 初期版対象外 | 具体的な要求が生じた時点で再検討 |
| [U08 Project・会話管理と再接続](../wip/unit_013/RESUME.md) | wip、M0〜M3完了、M4着手中。U06と会話実行・契約整理に依存 | 管理の委譲、記録形式、新規開始・履歴・既存会話の再接続 |
| [U09 記憶整理](../planned/unit_014/MEMORY_MAINTENANCE.md) | 初期版対象外 | 実需と再採用の判断後に再設計 |
| [U10 連続運転](../planned/unit_015/CONTINUOUS_OPERATION.md) | planned。U02〜U06・U08の必要な受入完了後 | Windows実機の数時間運転、遅延・資源・記録量 |

会話実行・共通停止は[unit_061](../complete/unit_061/CONVERSATION_EXECUTION_MODEL.md)、Goal操作は[unit_062](../complete/unit_062/GOAL_EXECUTION_MODEL.md)、契約整理は[unit_054](../complete/unit_054/KISS_SIMPLIFICATION.md)を前提とします。U08・U10の着手にU07・U09の完了は要求しません。

## U08の到達点と作業順

M1専用home・設定・管理API→M2記録と新規開始の結合→M3実行設定・標準指示の結合→M4切替・既存会話の再接続→M5会話管理UI→M6再開・設定・障害の結合検証→M7 GUI・実機の通し受入の順に進めます。開始条件と完了条件は[U08親仕様第6節](../wip/unit_013/RESUME.md#6-マイルストーン作業単位実施順)を正本とします。

M2はM1の管理APIを呼出し側から使い、固定memory・project.jsonの撤去とcwd・実thread ID・recordsの結合を同時に完了します。保存部品自体に管理API依存は置きません。Projectの画面操作を追加する前に、サービス経由のProjectあり／なしの開始と既存入力UIからの新規実行を確認します。

M4で停止後の旧資源回収と現在設定によるresume、通常送信・Goal操作を完成させ、M5で会話選択UIへ接続します。閲覧中のモデル選択・Goal削除はresumeから分離します。M6は基本経路を初めて実装する段階ではなく、状態を跨ぐ結合・障害検証を担います。各段階の試験・必要な実接続をM7まで持ち越しません。

マイルストーンは受入可能な到達点、unitはレビュー・実装を進める作業仕様であり、一対一を強制しません。次に着手する範囲だけを子仕様化し、必要に応じて一つの段階を複数unitへ分けます。unit番号の予約・未着手子仕様の一括生成は行いません。

## 外部契約と検証

採用runtimeと公開API・設定の成立条件は[08](08-sdk-compatibility.md)、受入観点は[07](07-verification.md)に従います。公開経路が成立しなければ影響範囲と変更判断を記録し、非公開API差替え、二重受信、攻略指示の自動再投入で補いません。

各工程で調査・設計の確定、製品実装、接続・実機検証を区別します。機器がなくても模擬境界の試験は進められますが、実機確認は未実施として残します。GUIは合意画面と説明文を置かない方針に従い、各機能の工程で実画面を確認します。

品質ゲートの選択は[AGENTS.md](../../AGENTS.md)に従います。同梱指示を変更する工程ではT20の配布確認、新規依存を変更する工程ではlock・build等を行います。文書整備に無関係な製品テスト・型検査・ビルドを文書品質の根拠にしません。
