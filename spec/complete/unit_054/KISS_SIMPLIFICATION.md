# U06後の契約・状態・分岐の整理 親仕様

## 1. 目的・状態・起点

状態: **complete**。M1はunit_055〜060、会話再編はunit_061・062で完了した。M2は[unit_063](../../complete/unit_063/STORAGE_INPUT_SIMPLIFICATION_DESIGN.md)の保存・入力整理をunit_064〜066、接続・取得境界の小整理を[unit_067](../unit_067/M2_BOUNDARY_CLEANUP.md)で完了し、全体803試験と静的・配布確認を通過した。最終製品commitは`6fa7e8d`。未検証の長時間運転・再起動後復帰は第10節へ引き継ぐ。

目的は、個人用のゲーム操作アプリに必要な利用場面へ契約を絞り、独立に管理する状態と判断箇所を減らすこと。単なるファイル分割、既存分岐の移設、コード行数・テスト件数の削減だけでは完了しない。既存仕様・公開API・テストの期待も見直し対象とする。

| 起点 | 内容・参照 |
|---|---|
| ユーザー要求 | connection.pyを中心にKISSで整理し、過剰な契約・防御・到達不能経路を洗い出す。実測自体と実測から採った対処も再評価する |
| M0要求と確認 | 残す契約・削る契約、状態の持ち方、仕様・テスト変更を具体化。2026-11-13に「その方針でM0を確定」「関連仕様へ反映したうえでM1を遂行・完遂」の指示を受けた |
| repo | 基準commit `6794752`。文書作業branch `codex/kiss-simplification-spec`。M0開始時は前段の仕様追加・文書訂正だけが未commit |
| 現行の正本 | [実行管理](../../initial/02-runtime.md)、[入力・観測](../../initial/03-game-io.md)、[保存・記憶](../../initial/04-agent-memory.md)、[制御フロー](../../initial/09-control-flows.md)、[UI](../../initial/10-ui-interaction.md) |
| 既存の根拠 | [U06 M2](../../complete/unit_049/GOAL_MEMORY_INTEGRATION.md)、[M3](../../complete/unit_050/GOAL_ACCEPTANCE.md)、[最新の認証行変更](../../complete/unit_053/AUTH_ROW_CONSOLIDATION.md) |

Intent Delta: 全既存期待を維持するリファクタリングから、利用範囲と保証の縮小を含む整理へ変える。過去の「新phaseを作らない」「既存flagだけで対応する」等の実装上の制約は継承しない。公開状態と内部構造は別に判断する。

利用場面は、通常会話・Goalによる操作、途中の回答・介入、明示停止、自然完了、同一接続再開、記録参照。現在の利用場面に寄与しない単独APIや将来用途の保証を残すために、本体を複雑にしない。

## 2. 対象範囲

接続・サービスの責務と状態所有、停止・完了・回収、表示通知の変換と保持寿命、製品から使わない公開利用形態、保存の保証と検証、入力の重複検証を扱う。関連するinitial仕様、公開docs、未着手のU07〜U10親仕様、既存unit/integration試験も見直す。

入力の停止・解除、明示的な承認、memoryと原記録の書込範囲、秘密値の除外、受付不明の操作を自動再送しないことは残す。その実現に使う現在の分岐・照合回数・task構成まで維持する前提にはしない。過去のcomplete仕様は当時の記録として保持する。

## 3. 対象外

U07〜U10の新機能実装、Bluetooth、認証方式追加、UIの見た目の再設計、依存更新、公開・配布作業は含めない。U09は初期版から外し、代わりの整理機構は実装しない。

汎用状態機械、イベントバス、ハンドラ登録基盤、プラグイン機構、自動再試行・修復、互換用の二重実装を追加しない。クラス・ファイル・テスト数の目標値を置かない。

## 4. 着手条件と工程

M1後の追加再編は[unit_061 通常指示の送信・ゲーム操作・停止](../../complete/unit_061/CONVERSATION_EXECUTION_MODEL.md)と[unit_062 Goal操作](../../complete/unit_062/GOAL_EXECUTION_MODEL.md)で扱う。通常指示・Goalの実行判断をapp-serverへ委ね、共通停止、入力許可契約の撤去、通信・会話・機器・記録の内部再編を実装した。共通の内部構成・実装順はunit_061第6節、Goal差分はunit_062第7節を正本とする。呼出しと状態寿命、取消し回収、通知待ちを再レビューし、全体試験と実接続・短い実機入力を確認した。新構成の根拠・限界は両unitへ記録し、以下のM1実績と混同しない。U07は初期版対象外とし、U08/U10の必須依存から外した。

両unitは、本書M0の包括的終了・端末回収・ローカル停止受付・run/Grant保持と、新規記録payloadのrun契約を置換する。関連正本も両unitへ合わせた。記録のrun撤去に必要な過去payload再検証の縮小はM2と共通の方針として実装済みだが、M2全体の完了とは数えない。共通envelopeの形式と過去archiveを維持し、移行処理・新旧の二重書込みは設けない。

**M1は完了した**。第5節以降のM0確定設計を関連initial・planned仕様へ反映し、製品専用の接続組立から順に実装した。公開docsとdocstringも製品変更へ合わせた。M2の現在の範囲・進捗は第4.2節に記す。

| 段階 | 到達点・開始条件 |
|---|---|
| M0 契約と所有の確定 | 完了。残す保証、失う挙動、終了遷移、状態寿命、仕様・試験の改訂対応を2026-11-13にユーザーと確認した |
| M1 接続・サービスの整理 | 完了。製品専用の組立、通知寿命、終了所有、操作配送、責務と状態の所有を整理し、全847試験と必要な実接続・実機受入を確認した |
| M2 保存・入力・残件の整理 | 完了。保存保証・入力の検査所有をunit_063〜066、接続・取得境界をunit_067で整理した。全体803試験と静的・配布確認を通過。独立したUI分割は含めない |

### 4.1 M1の完了実績

M1では依存する前段の完了後に次の子だけを作り、製品準備、通知、終了、サービス、接続担当の順に分けた。

M1の最初の子は[unit_055 製品専用Codex接続組立](../../complete/unit_055/PRODUCT_CONNECTION_ASSEMBLY.md)で完了した。単独read-only経路と準備省略を廃止した。

[unit_056 通知の受付と寿命](../../complete/unit_056/TURN_NOTIFICATION_LIFETIME.md)を完了した。表示蓄積と要求IDの寿命、turn採用元を先に絞った。開始応答の相関に使う完了turn集合と中断turn集合は、続くunit_057で除去した。

[unit_057 終了要求の単一所有](../../complete/unit_057/TURN_END_OWNERSHIP.md)を完了した。停止・自然完了・切断の所有、開始受付との相関、サービスの停止受付記録とshutdownを変更した。実機入力は同単位で確認し、非入力の実接続判定に見つかった検証器の不備はunit_060で訂正・再検証した。

[unit_058 サービス操作配送の整理](../../complete/unit_058/SERVICE_OPERATION_DISPATCH.md)を完了した。引数を束縛した実行関数と小さい操作文脈へ置き換え、配送とローカル資源操作を分けた。この段階では資源・入力runは未分離だったため、unit_059で所有者を分けた。

[unit_059 サービス責務と状態所有](../../complete/unit_059/SERVICE_RESPONSIBILITY_OWNERSHIP.md)を完了した。保存・観測・MCPの資源管理と、入力runの許可・質問待ち・終了を、それぞれ操作と寿命ごと分離した。サービスの独立thread ID、GoalState全体の保持、run終端taskの二重所有と不要な例外callbackを撤去した。全846試験と実runtime・短い実機入力で停止・回収・同接続再開まで確認した。

[unit_060 接続の責務と状態所有](../../complete/unit_060/CONNECTION_RESPONSIBILITY_OWNERSHIP.md)を完了した。質問・承認、モデル候補、Windows環境準備、認証・利用枠をそれぞれの担当へ移し、会話・Goal・開始/終了と唯一の公開phaseは接続に残した。起動中の利用枠通知による早すぎる停止も代表試験で修正し、最終全847試験と実runtime受入を確認した。

2026-11-13の追伸で、ユーザーは振る舞いと状態の整理後には責務に沿うファイル分割も進めるよう指示した。その後、両クラスが依然として巨大で多数の状態を保持する点を指摘し、責務と状態の双方から帰属を見直す方針を示した。現在turnの質問・承認と公開境界型だけの分割案を見直し、第5.9節の責務分離をM1へ含める。汎用managerやmixinを作らず、外部契約を維持して状態の所有と操作の境界を改める。

仕様変更を伴う作業と純粋な構造整理は別の作業・コミットにする。先に構造を整える場合も、その後に減らす契約との対応を示す。必要な実接続・実機検証は変更した子作業で行い、工程末まで欠陥を持ち越さない。

### 4.2 M2の範囲と進捗

[unit_063 保存・入力検証の整理](../../complete/unit_063/STORAGE_INPUT_SIMPLIFICATION_DESIGN.md)で第5.6・5.7節を具体化し、保存内容の再検査と明示同期を削った。入力はparserのJSON読取、InputState/InputStepの生成時保証、サービスの開始前の機器能力検査を分け、生成後の重複検査を削除した。仕様・公開API・試験を揃え、全体803試験と静的・package gateを確認した。

過去payloadの意味検査撤去、サービス操作整理、run/Grant撤去は完了済みであり、M2の残件に重ねない。実装順はunit_063第6節の、保存検査の縮小、保存の同期保証縮小、操作列の生成保証と検査所有、結合確認とする。保存と入力は独立した変更として扱い、各段階で仕様・公開API・試験を揃える。

SerialConfigの再解決、CaptureServiceのpending分岐、stdio readerの未設定分岐は[unit_067](../unit_067/M2_BOUNDARY_CLEANUP.md)で別々の変更として削除した。解決済み設定、lock内の要求取出し、起動時に得たprotocolをそのまま使い、新しい検証状態やassert/cast/ignoreを追加していない。対応する既存試験と型検査、M2全体の受入を確認した。

M2の受入は、通常の記録・後参照、原本保全、入力開始前の全列検査、停止・解除を保ったうえで、不要な検査と保証を撤去し、仕様・公開API・試験を揃えること。unit_063の設計完了だけではM2完了にしない。

## 5. M0の確定設計

第5節の会話・終了部分はM0からM1への判断記録である。後にunit_061・062が置き換えた契約は第4節と両unitを優先し、旧run/Grant・端末回収・停止受付の保証を現行へ戻さない。保存・入力はunit_063で具体化した採用契約に従う。

### 5.1 契約の採否と失う挙動

以下をすべて**採用**する。実装・検証の完了とは区別し、M1/M2それぞれの子仕様に実績を記録する。

| 領域 | 残す契約 | 削る契約・失う挙動 |
|---|---|---|
| 停止・自然完了 | 入力権限を即時に閉じる。自然完了の最終応答を受け取る。対象turn終端と公開端末回収を確認してから次を許可する | Goal complete直後とturn終端後の二段階回収。終端前に端末が止まる保証。stopの戻り値によるRPC受付・回収完了保証 |
| 公開範囲・通知 | アプリの記録先・memory・MCPを準備して実行する。開始応答より早い通知を扱う | 単独read-only接続、memoryなし実行、任意item/質問だけからのturn採用、完了済み全turnの表示更新 |
| 保存 | 原本・追記順・writer排他・上書き拒否・失敗診断。受付済み保存を終了時に回収する | 過去payloadの意味検査、UUID/時刻の厳密な再検査、型付き内部値の再解析、毎回の明示flush/fsync |
| 入力 | 外部JSONからの全列生成、InputState/InputStepの生成時の不変条件保証、選択機器での全列実行可否、停止・解除。Grant撤去はunit_061で完了 | parserと生成処理の重複する値域検査、生成後の共通条件の再検査、単独encoderでの機器能力の拒否保証 |
| 後続工程 | 通常Goalによるmemory更新、停止中の利用者による編集、U08の再開、U10の連続運転確認 | 初期版の専用記憶整理、自動起動、退避・自動復元・未完整理の復旧。U10のU09完了待ち |

### 5.2 終了処理の所有と遷移

**現状:** [connection.py](../../../src/gigas/runtime/codex/connection.py)の`_stopping`は手動中断、自然完了待ち、完了後の遅いturn拒否を兼ねる。`_finishing_goal_turn`が最終応答の例外を補い、`_terminal_cleanup`の履歴も判断に使われる。`_accept_goal`・`_stop`・`_complete_turn`・回収callback・通知・`_status`に終了判断が分散している。

**採用設計:** 接続が終了理由・対象turn・Goal操作を一つの終了要求として所有し、一つの非同期処理で終了まで進める。優先順位は切断、中断、自然完了。中断には明示停止と異常終端を含み、失敗理由は診断として残す。理由の変更は同じ要求へ反映し、自然完了通知で中断へ移った要求を戻さない。状態通知関数は決定済み状態を発行するだけにする。

| 契機 | 即時に行うこと | 終了処理と完了条件 |
|---|---|---|
| Goal complete、turn実行中 | 入力・追加指示・回答を閉じる。公開phaseはstopping、内部理由は自然完了 | 最終応答と対象turn終端を待つ。その後に一回の端末回収を行いreadyへ |
| 明示stop/pause/clear | 同じ受付を閉じ、stoppingを発行 | Goal pause/clearと既知turnのinterruptを独立に試み、対象turn終端後に一回回収してstoppedへ。Goal操作のlock待ちでinterruptを遅らせない |
| 自然完了待ちへの明示停止 | 既存の終了理由を中断へ変更 | 終了処理を起こして対象turnをinterruptする。自然待機用・中断用の二つの回収を作らない |
| complete以外へのGoal非active化 | 入力・追加指示・回答を閉じる | 中断として扱い、対象turn終端後に一回回収してstoppedへ。すでに非activeのGoalを再度pauseしない |
| 準備中・開始RPC未応答の停止 | 停止を先に記録。未送信なら送信を抑止 | 送信済みなら開始通知/応答で実IDを得て中断する。未送信ならturn終端を待たない。threadがある場合の端末回収は行う |
| 停止後の正当なturn/started | 入力許可を戻さず、新しい実行中表示にしない | そのturnを中断し、終端後に一回回収する。終了済み要求への追加対象であり、新しい利用者runは作らない |
| Goal操作・中断・回収の失敗 | errorを通知し、次の実行を拒否する | 独立に可能な中断は試みる。自動再送や終わりのない追跡は行わず、明示切断・再接続で復旧する |
| 明示切断・アプリ終了 | 受付を閉じ、通常終了要求を切断へ切り替える | turn終端待ちを打ち切る。可能な中断・端末回収を試み、必ず所有transportを閉じる。最終応答・Goal休止の永続化・任意の子孫プロセス終了は保証しない |

終了要求のない通常会話のcompletedは本文を確定してreadyへ戻り、追加の端末回収は行わない。active Goalの成功turn終端はturn間として入力を保留し、runを維持する。いずれも表示・要求のturn単位のデータは締める。failed/interruptedは中断の終了要求へ入り、Goalがactiveならpauseし、一回回収後にstoppedへ進む。終端済みturnへ再度interruptしない。自然完了待ちの対象turnが失敗・中断した場合もreadyにはしない。停止処理自体の失敗は上表のerrorとして区別する。

一回の端末回収とは、公開一覧の全ページ取得、列挙した端末のterminate、一覧が空かの再確認を一組として行うこと。通知のたびにやり直すのではない。確認時に残存していればerrorとし、空になるまで繰り返さない。対象turnがなく、開始待ちもなければturn終端待ちを省く。thread自体がなければ端末RPCも不要。

終了操作の最初の失敗を要求に保持する。以後の遅いturn通知・同じ停止要求で処理やRPCを再試行せず、開始済みの独立I/Oだけを回収する。Goal操作と既知turn中断は先に独立開始するため、Goal操作の失敗でもその中断を試みる。失敗後に正当な開始通知で判明したturnは現在の一件としてID・表示・終端を受信するが、新たな中断・回収を自動開始しない。errorと入力閉鎖を維持し、明示切断で現在対象の未試行処理を一回試みてtransportを閉じる。同対象の失敗済み中断・回収を切断時に再試行しない。

**公開stop/clear_goalの完了を軽くする:** Goal pauseを含むstopとclear_goalは、終了要求のローカル受付で戻る。戻りはRPC受付・Goal変更・実停止完了を意味しない。完了・失敗は既存の`SessionStatus`、Goalの実状態は`GoalEvent`で通知する。サービスの操作結果も「停止完了」として表示・記録しない。新しい待機APIや完了通知型を追加しない。同じ終了要求への再操作は合流し、clearへの変更はその明示要求として扱う。

**待ち関係:** 通知消費処理はturn終端や終了理由を記録して戻り、終了処理をawaitしない。終了処理が待つのはturn終端と開始受付の結果だけで、send/set_goalの公開操作全体ではない。send/set_goal側も終了処理を待たない。理由変更の通知は処理を起こすためだけに使い、別の業務状態を持たせない。

**closeの収束:** 切断へ変更した終了処理は、turn終端・開始準備の成立・開始受付・Goal操作lockの待機を打ち切る。開始済みI/Oの所有・回収まで放棄する意味ではない。現在の対象に発行済みのinterruptと進行中の端末回収は共有し、closeを理由に同じ操作を追加しない。以前の対象の回収結果を遅い別turnへ流用せず、新しい対象が判明したら先行対象の回収完了だけでstoppedを発行しない。RPCは既存の期限を使い、新しい停止用タイマーは設けない。

終わりに所有操作・通知消費・transportを回収する。その際、終了処理自身とclose呼出元を待機対象から除き、終了処理から公開closeをawaitして自身の回収を待つ循環を作らない。サービスのshutdownは入力を閉じた後にconnection.closeへ進み、stopとcloseを重ねない。通信断時は回収未確認を診断に残す。

**失う保証:** 最終応答待ちやturn終端待ちの間、端末の書込が続く場合を許容する。入力停止は待たない。終端通知が来なければ通常停止はstoppingのままであり、明示切断を収束経路とする。新規実行を許可するために未確認の停止を成功扱いにはしない。

### 5.3 状態の持ち方と寿命

公開Goalはruntimeを正本とし、アプリ側に攻略継続を判断する第二のGoal状態機械は作らない。公開phaseは表示用の結果であり、taskの有無や古い通知で補正しない。

| 所有者・値 | 用途・寿命 | 減らすもの |
|---|---|---|
| 接続: 現在turnの記録 | 実ID、終端結果、中断発行の有無を一か所に保持。終了待ちが終わり、未解決startからも参照されなくなれば破棄 | 全完了turn・全中断turnの集合、複数箇所のturn状態 |
| 接続: 未解決start一件 | 開始RPCの受付結果と、先行通知で得たturn記録。応答前に終端しても受付結果を正しく返し、応答解決後に破棄 | 過去turn集合で通知先行を補う経路 |
| 接続: 終了要求一件 | 理由、対象、Goal操作、処理結果。実行taskは動作中だけ保持。要求は次の明示開始または切断まで保持し、遅いturnへ権限を戻さない | `_stopping`・`_finishing_goal_turn`・回収task履歴による別々の意味付け |
| 表示蓄積: 現在turnの項目 | 本文・推論要約・活動を結合し、終端時に確定して破棄。画面の確定履歴は残す | 無期限の`_summaries`、完了済み全turnの差分結合用データ |
| 要求受付: 現在turnの要求 | 回答tokenは回答・失効時に解放。同turn内の重複抑止に使うIDはturn終端まで保持 | 接続全期間の`_seen_requests`。過去ID再利用への無期限保険 |
| サービス/入力/保存 | サービスは接続世代と操作task、InputServiceはGrant・入力列・解除、RecordingServiceは受付済み保存、ProjectWriterはwriter排他と次sequenceを所有 | 接続のGoal・終了状態をサービスが独自に修復する経路、入力停止に保存完了を条件付ける経路 |

終了要求がある場合、次の明示開始は終了成功後だけ許可し、終了要求と過去turn用データを破棄して新しい準備へ進む。終了処理の失敗状態からの新規実行は拒否する。保留と失効は異なるため、質問待ちの一時保留・停止済みGrantを区別する状態は残す。操作取消し・回答配送・接続世代も異なる結果を表すため、一つの汎用フラグへ押し込まない。

### 5.4 通知の受付と表示の締切

turnの採用元は`turn/started`と自分が送った`turn/start`の応答に限定する。任意のitem・質問に未知turn IDがあっても、新しいturnとして採用しない。開始応答より先に開始通知・質問・本文・終端が来る場合は、未解決start一件で扱う。終端済みturnの`turn/started`を任意順で再送する模擬入力まで支える保証は廃止する。

表示・記録の締切は`turn/completed`受信時とする。受信済み本文は最終または途中本文として一度確定し、それ以降の同turnの本文・Plan・推論要約・Activity更新を捨てる。表示済み履歴や原記録自体は消さない。状態を持たない通知変換は関数へ、現在turnの蓄積・確定だけは小さい担当へ分ける。

**事実と限界:** 採用版0.167.0に対応するcommit `086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a`では、[通常turn処理](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/core/src/tasks/regular.rs#L60)が開始通知を送ってから本体を実行し、[app-server](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/request_processors/thread_lifecycle.rs#L532)はイベントを逐次処理する。将来版を含む全通知の順序保証には一般化しない。

M2の`tmp/u06-m2-runtime/memory-product-20261113-170826/report.json`には、turn終端後に同turnのcommand item完了が届いた記録がある。遅延経路は実在し、今回捨てるのはその活動の最終状態を表示へ反映する保証である。[終端通知の構成](https://github.com/openai/codex/blob/086ccc833bc9cf01024ad2dd6af7dbd421b6bf9a/codex-rs/app-server/src/bespoke_event_handling.rs#L1402)も全項目の履歴を保証しない。終端payloadからの再構成、thread再読込、待機タイマー、猶予キューは追加しない。

秘密値・raw RPC・画像本体の除外は通知変換の境界で維持する。表示締切による破棄と、停止後の旧入力・回答拒否は別に検証する。

### 5.5 製品専用の組立へ統一する

[CodexServiceThread](../../../src/gigas/services/codex.py)はM0時点でもprepare/on_thread_startedを渡し、記録先配下のmemoryを準備していた。[公開API](../../../docs/codex.md)の単独read-only経路は製品経路ではなかったが、公開済み契約である。以下は承認済みの破壊的変更として扱う。

`CodexConnection`/`create_connection`の`prepare_turn`・`on_thread_started`を必須にする。`ThreadConfiguration.memory_dir`を削除し、書込先は準備済みの`cwd / "memory"`に固定する。起動用cwdへのfallback、memoryなしのread-only分岐、二つのpathの一致検査を削る。

準備は実行開始時に行い、接続・ログインだけでは記録先を作らない。書込先の準備とsandboxの利用可否・要求した書込範囲の応答確認は残す。原記録全体への書込許可には広げない。模擬peerと小さい準備fixtureは試験側に置き、製品へ試験専用モードや互換分岐を追加しない。外部の単独API利用者の有無は未確認であり、互換維持は本案の対象外とする。

### 5.6 保存の保証と検証を縮小する

生成するJSONL・画像・metadataの形式は変えず、schema更新・移行・セッション別ログ・索引・新しい書込バッファは導入しない。全件走査は採番に必要な範囲で残す。以下の境界だけで検査する。

| 境界 | 残す検査 | 削る検査 |
|---|---|---|
| projectを開く | metadataがJSON object、対応schema、空でないproject ID、必要な保存ディレクトリの存在 | project IDのUUID4表記、作成時刻の字句形式 |
| JSONLを読む | UTF-8/JSON object、改行終端、対応schema、同じproject ID、正整数で単調増加するsequence、空でないtype、payloadの存在 | event IDと時刻の表記、既知payloadの入力・対話・観測としての再解析。未知payloadと同様に内容を保持 |
| 新しいイベントを書く | 型付き値を一度JSON化。JSON直列化に失敗する値と非有限数を既存の`json.dumps(..., allow_nan=False)`で拒否 | JSON化前の二重シリアライズ、内部のID/時刻/寸法/入力値の再検証。別のdataclassへ検査を移して残さない |
| 新しい画像を書く | 保存先に使うobservation IDの`obs-UUID4`制約、同じ場所の一時ファイル、既存画像を上書きしない確定 | 新規Observationの画像・元Frameの寸法整合、生成元・日時などの保存時の再検査 |

`project.py`の`_validate_dialogue`・`_validate_observation`・時刻検査・既知payload分岐と、[input_events.py](../../../src/gigas/storage/input_events.py)の`validate_input_payload`を削る。`_identifier`相当は新規画像の保存先を決める境界へ限定する。

**同期保証:** metadata・画像・JSONLの明示flush/fsyncを外し、通常のwrite/closeへ揃える。正常終了では新しい要求を閉じ、符号化、受付済み保存、writer closeの順に回収する。closeに伴うバッファ排出は行われるが、安定記憶への同期完了は保証しない。異常終了・電源断では直近の画像や記録が欠け得る。これは保証の縮小であり、性能改善を測定済みとは記さない。

**残す失敗処理:** 画像保存前の失敗ではログを進めない。ログ追記結果が不明なら`_next_sequence=None`として以後の追記を拒否し、明示reloadの検査成功後だけ再開する。自動切詰め・修復・再送はしない。writerのmutex・OS lockと原本保持は残す。保存失敗は診断へ残し、入力結果・観測成功を取り消さない。

### 5.7 入力検証とサービス操作を一か所ずつにする

**入力（unit_063で改訂）:** `parse_sequence(value)`がJSONの形式を読み、InputState/InputStepの生成処理が共通の値域・重複・反対方向・press/waitの整合を保証する。`__post_init__`を全廃する旧案は、生成時の不変条件保証を維持するユーザー指示により置き換える。直接構築も同じ生成処理を通し、InputStepは生成済みInputStateの内部を再検査しない。詳細条件とparserの型確認との分担はunit_063第5.2節を正本とする。

parserの`protocol`引数と重複する値域検査、`encode_state`の能力再検査を削る。選択機器での能力検査は`InputService.start`が列全体へ一度行い、生成済みの共通条件は検査し直さない。全列が有効になる前に一部の物理入力を送らない。個数・時間に固定上限を設けない契約は残す。非空列はparserとサービス呼出側の契約とし、InputStep単体の生成保証とは区別する。単独encoder・低水準送信は機器適合済みの状態を渡す前提とする。新しいValidatedSequenceや第二のencoderは作らない。

**サービス:** 任意のpayload欄を多数持つ`_Command`を、接続世代・操作名・request ID/tokenの小さい操作文脈と、引数を束縛した実行関数へ替える。Qt側では引数を固定し、回答mappingはコピーする。現在の接続の取得とcoroutineの生成はサービスloop上で行う。`_perform`の操作名matchとpayloadのNone分岐を削り、動的getattrや登録表には置き換えない。

接続・プロジェクト・入力設定・解像度・port列挙などローカル資源操作は専用メソッドで受ける。接続へ転送する操作として偽装せず、taskの回収と結果通知だけを共用する。これにより`_complete`のローカル操作除外リストも不要にする。接続世代による失効、配送前のnot_sent、配送後のunknown、入力権限の即時失効、操作文脈の伝播は残す。ファイルを分けるだけの新しいmanager群は作らない。

**先送り・小さい削除:** ApplicationShellの設定分割は、この契約縮小に必須でないため本工程では見送る。CaptureServiceの同一Condition内の重複pending確認、起動後しか呼ばないStdioTransport readerのprotocol未設定分岐は、呼出条件を維持して削る。ignore/castへの機械置換はしない。

### 5.8 既存仕様・後続工程への反映

**U09の確定方針:** 専用の記憶整理機能を初期版から外す。自動整理、別整理スレッド、全memory退避、差分復元、未完整理の永続状態、全開始経路での復旧待ちを作らない。候補生成用API・採用操作も先行追加しない。通常Goalのmemory更新と停止中の利用者編集は残すが、自動backup/rollbackは保証しない。

[U09親仕様](../../planned/unit_014/MEMORY_MAINTENANCE.md)は削除・complete扱いにせず、「初期版対象外・必要時に再設計」として範囲と着手条件を改訂する。[U10](../../planned/unit_015/CONTINUOUS_OPERATION.md)はU02〜U08の必要な確認を前提にし、整理の成功・復元を受入から外す。U08の通常の記録参照・再開は残す。将来、整理の実需が出たときは明示生成だけの方式から再検討し、本工程で実装を予約しない。

| 正本・文書 | 採用設計の反映箇所 |
|---|---|
| initial 02・09・10、docs/codex.mdとcodex-gui.md | 終了要求の受付と実終了の区別、自然完了、終端後一回の回収、切断、表示締切、製品専用の準備 |
| initial 03・04、docs/input-api.md・recorded-observation.md、関連docstring | 検証の所有、入力値の生成時保証と単独符号化の前提、読込条件、write/closeと異常終了時の損失 |
| initial 01・02・04・05・07・08・09、planned U08〜U10、READMEの機能予告 | 依頼完了後の自動整理、退避・復元、未完整理の復旧とU09必須依存を撤去。通常memory更新と再開は維持 |
| complete仕様 | 当時の判断・試験記録として保持。本案の成功を示す記録へ書き換えない |

M0案の作成時には、09/S01の「自然完了でも残るturnを一律中断」、recorded-observationの「対話記録・memory編集未対応」「開始保存をawait」「保存失敗で入力停止」「観測drainが共通保存も回収」、U10の「保存失敗で入力を止める」という古い説明も訂正した。M0確定後は採用設計を正本へ反映する。M2の新しい保存保証を公開docsへ適用するのはM2実装時とする。

### 5.9 巨大クラスの責務・状態所有を分ける

**unit_058時点の再評価:** `6794752`と`df794a7`をASTで比較すると、CodexConnectionはclass本体1,704→1,524行・初期属性48→43、CodexServiceThreadは879→886行・39→39だった。初期属性は依存参照を含み、子記録の内部状態を含まない。操作文脈の14→4はサービス本体の改善ではなく、子記録への移動も状態そのものの消滅ではない。終了契約の縮小に実益はあるが、この時点では巨大クラスの改善は未達と判断した。

この再評価から、操作・寿命・必要状態を往復して境界を決めた。各担当の引受け、結果、終了条件を定め、他の担当のprivate属性を読む必要が残るなら境界を再検討した。以下の所有分離では外部の停止・配送・保存・入力契約を変えていない。

| 所有者 | 引き受ける操作と寿命 | 境界に残すもの |
|---|---|---|
| ConversationResources | project/writer、recording、observation、MCPの取得、記録、受付閉鎖、符号化・保存・writerの回収 | 接続の有無とproject切替の排他はサービスが調整。入力許可・Goal判断を持たない |
| ConversationRun | run開始・Grant、質問待ち・回答配送中、Goal/turnの事実に基づく入力許可、同期失効、非同期run終了 | InputServiceは既存どおり機器と入力実行を所有。writer・MCP・接続・QtをRunへ渡さない |
| CodexServiceThread | Qt受付、接続世代、操作配送、入力設定の集約、所有者同士の短い組立と終了順 | 準備済みobservation/recordingやGrantをサービス属性へ複製しない。別async coreへ本体を丸ごと移さない |
| HumanRequests | 現在turnの質問・承認の検証、登録、回答、失効と保持寿命 | 接続側に所属・終了条件の照合、RPC、phaseを残す。要求とitemを一記録にまとめ、逆引き辞書2個を除く |
| ModelCatalog / WindowsSandbox / AccountSession | モデル候補と選択、環境準備一件、認証・login相関・利用枠をそれぞれの操作と一緒に所有 | 会話・Goal・開始/終了・接続寿命と唯一のphase決定はCodexConnectionに残す。子へ接続本体や状態参照callback群を渡さない |

ResourcesとRunは相互参照しない。準備済みobservation/recordingを値としてRun.beginへ渡し、返ったGrantをResourcesのMCPへbindする。run終了taskはRunだけが所有する。切断は同期入力失効→入力/観測操作回収→接続とendpointの終了→保存・archive回収の順で進める。新しいclose task・停止期限を増やさない。

unit_059のレビューでは、run終了taskへ故障を注入する追加試験と例外専用の回収拡張を撤回した。入力・観測の例外はInputServiceで回収され、保存例外はRecordingServiceの別taskで通知される。単独所有になったrun終了taskを外から取り消す経路もなく、通常の製品障害からその例外経路へ到達する根拠がなかった。専用の例外報告callbackも外し、予期せぬプログラム上の例外は必要なdrainで顕在化させる。実機器のclose失敗に対応する既存の回収は残す。

**削除と保持の判断:** serviceのthread_idは入力許可には使われず記録対応だけだった。Resourcesで既存のObservationService.thread_idを使い、独立保持を撤去した。Goalは入力側ではstatusしか読まないため、全GoalStateの複製をやめた。質問待ち集合は、非blocking質問でもゲーム入力を保留する製品契約に必要で、running phaseや回答配送中集合から導出できない。条件を外したプロセス内実験では実CodexConnection＋HTTPの既存試験が入力を再許可して失敗した。発行前の停止意思、質問待ち、配送不明は異なるため、数合わせで一つにしていない。

unit_059でサービスのResources/Runを分け、unit_060で接続側の担当を切り出した。公開境界型はRpcPeerだけを自然なtransport境界へ移し、再公開を維持した。単なる行数・属性数ではなく、状態への書込主体、所有期間、分岐の判断箇所、依存の方向、検証結果を完了根拠とする。

## 6. TDD Test Listと既存試験の変更方針

親では受入観点と既存期待の扱いを決め、詳細なred/green項目と進捗は各子で所有する。M1はunit_055〜060、M2の保存・入力はunit_064〜066、接続・取得境界はunit_067で完了した。次の表はM0時点からの改訂対応であり、その後の会話・停止の受入はunit_061・062を優先する。内部flag・task名・呼出回数の単純置換をテストの目的にしない。

| 受入観点 | 残す・変更する試験 | 削る・統合する期待 |
|---|---|---|
| 明示停止で入力を閉じ、自然完了の最終応答を受け取り、必要な終端・回収まで次を始めない | connectionの`test_completed_goal_preserves_final_reply_or_accepts_manual_stop`、`test_goal_stop_waits_for_terminal_cleanup_before_new_execution`を新しい順序へ改訂。終端か回収を保留した代表例で、stop返却が完了を意味せずstoppingを保つことを確認。結果は状態通知で確認 | complete直後の回収、同じ対象の多重回収、stop返却＝RPC/回収完了の期待 |
| 準備中停止・開始通知先行・遅い正当turn・通信不明を扱い、切断で所有処理を回収できる | 既存preparation・answer lifecycle・service/transportの試験へ分担。started→質問/本文/終端→開始応答、自然待ちから手動停止、turn終端なしのcloseを代表例にする | 開始通知なしの質問から未知turnを採用する`started_notice=False`の経路、全組合せの機械的追加 |
| 表示締切と秘密値除外を守る | `test_public_progress_preserves_turn_identity_and_excludes_private_payloads`の旧turn追加表示を「更新なし」へ改訂。中断時の途中本文一回確定は残す | 終端後のPlan・要約・MCP完了の反映、全履歴からの再構成 |
| 製品準備でmemoryだけを書込対象にする | connectionのreadiness/準備失敗/書込範囲検査、GUI・transport peerの正常経路を同じ準備fixtureへ揃える | callback省略・read-only・二回目の準備省略を製品保証とする期待。peerの二種類sandbox許容 |
| 原本を保持し、採番・排他・保存失敗を扱う | observation storageの正常保存・再読込・未知イベント保持・上書き拒否を残す。`test_invalid_event_envelope_is_rejected`はschema/project/sequence/type等の残す条件だけへ。追記失敗は不完全行が残ってreloadを拒否する例と、全行書込後のfile close失敗からreloadで再開する例を残す | 種別×同じ追記失敗の総当たり、fsync専用ケース、時刻形式だけの拒否 |
| 過去payloadと型付き内部値を再検証しない | `test_input_record_read_rejects_invalid_known_payload`はpayloadを不透明な値として保持する代表例へ変更 | `test_invalid_input_record_is_rejected_before_append`、`test_invalid_recorded_observation_is_rejected_without_rewriting`、`test_invalid_source_identifiers_cannot_create_unreadable_records`の内部不正値・旧payload検査。ただし新規画像IDの保存先検査は残す |
| 不正な全入力列は物理入力前に拒否する | parserのJSON形式、InputState/InputStepの生成時保証、serviceの機器能力、MCPの`test_http_invalid_sequence_sends_no_input`を分担。後方要素が不正でも送信0回、有効な記録との対応を残す | parserのprotocol引数、単独encoderによる機器非対応の拒否期待。直接構築の不変条件と正常なプロトコルbyte変換試験は残す |
| サービスの配送結果と世代失効を守る | GUI公開メソッドとsignalによる既存試験、not_sent/unknown、質問待ちと停止後の旧Grant拒否を残す | `_Command`を直接作る試験は公開入口へ変更。新しい内部クラスの形を固定する代替assertは作らない |

テスト削除は、廃止する契約と対応させて子仕様へ記録する。故障・取消し・通信結果不明を「個人用だから」の理由だけで除外しない。固定された自然言語、文書見出し、内部構造だけの試験を新設しない。テスト数・網羅率を削減成果の目標にしない。

## 7. 設計の確定記録

2026-11-13、ユーザーは第5節の契約縮小と失う挙動を含む方針を承認した。端末回収の一本化、停止APIのローカル受付化、終端後の表示更新廃止、fsyncと意味検査の撤去、単独APIの縮小、U09の初期版除外を採用する。当時の実装対象はM1であり、M2は未着手としていた。現在の具体化状況は第4.2節に記す。

| 比較した案 | M0の判断・理由 |
|---|---|
| 二段階の端末回収を維持 | 不採用案。早い端末停止保証より、終端後一回という開始条件・所有の単純化を選ぶ |
| stopがturn終端まで必ずawaitする | 不採用案。外部自動化向けの強い完了契約を増やさず、既存状態通知へ統一する |
| 古い通知を後から復元する | 不採用案。表示の完全性を縮小し、期限・再照会・全履歴保持を作らない |
| 保存形式更新・索引・同期スケジューラ | 不採用案。今回必要なのは保証・検証の縮小で、形式や運用の追加ではない |
| U09の候補生成・明示採用だけを作る | 今回は先送り案。初期版で必要な根拠がなく、別の採用状態・UI/APIを増やさない |
| ApplicationShellの分割 | 本工程では見送り案。契約・状態削減との直接の対応が薄く、独立したUI変更を増やす |

子の完了時は、なくした契約、統合・削除した状態、判断箇所と所有者、改訂・削除した試験、残る保証の検証結果を示す。必要な小さい内部値は追加してよいが、旧flagとの二重管理は残さない。

## 8. 対象ファイル

| 対象 | 変更の目的 |
|---|---|
| `src/gigas/runtime/codex/{connection,models,launch,transport}.py` | 終了所有、製品専用の準備、通知変換・保持、内部の不要経路 |
| `src/gigas/services/{codex,input,recording}.py`、`mcp/game_io.py`、`game_io/input/` | 組立・操作受付・入力権限・保存・検証の分担 |
| `src/gigas/storage/{project,input_events}.py` | 保存保証、読込・内部生成の検証を縮小 |
| `src/gigas/game_io/capture/service.py` | 呼出条件から不要と確認した分岐だけを削除 |
| initial・planned・docs・関連docstring | 第5.8節の対応。関係しない節は変更しない |
| `tests/unit/test_codex_connection.py`、input系、対応integration試験 | 第6節の残す・統合・変更・削除を実施 |

## 9. 検証計画と現在の結果

### M0設計案作成時に確認した根拠と限界

ソース・仕様・既存試験と、M2/M3の資料を読み直した。M2の中断後3→30 bytesの追記継続、M3の製品停止後の565 bytes不変は、それぞれの条件での現象・回収成立の証拠である。前者は検証側の手動terminateを含み、後者は現行の二段階回収を含む。新しい一回回収や全分岐の必要性を証明したものではない。

採用版では非active Goalへの変更とturn中断・端末終了は別であり、interrupt応答だけでもturn終端は確定しない。実測からは「turn中断だけで全端末が止まる」とは置けず、公開端末の回収は残す。ただし、同じFakePeer通知の再投入は実runtimeでの重複発生の証拠にはせず、回収時期の選択を実測で必然と装わない。

| 検査 | 結果・範囲 |
|---|---|
| branch・status、unit配置 | `codex/kiss-simplification-spec`で前段の文書差分を保持。unit_054だけをwipへ移動 |
| 内容レビュー | 独立レビュー3件。通常turnとGoal turn間の区別、切断時の待機打切り、対象ごとの回収、停止試験の過剰なタイミング期待、保存検査の表現、既存文書との差を修正した。停止戻り値の強化案は退け、状態通知への一本化を推奨 |
| `uv run python -X utf8 tmp/u054/validate_docs.py` | 変更Markdown 5件、ローカル参照先86件、unit 54件の一意性、コード囲み・仮テキスト・空白を検査しerrors=0。リンクのanchorと本文の意味は自動検査の対象外 |
| `git diff --check` | 成功。追跡済み差分の空白エラーなし。新規仕様の空白は上記スクリプトでも確認 |
| 製品・テスト差分 | `git diff --name-only -- src tests`は空。statusでも変更は文書5件だけ |
| pytest・ruff・ty・build | not applicable。今回の差分は文書のみで、製品・テスト・依存・配布を変えない |
| 新設計の実接続・実機・性能 | not run。設計確認前・製品未実装。過去の成功を代用しない |

### M1の実行記録

**検証器の判定訂正:** 最終確認でneutral/close-waitのreportにerrorとpassedが併存すると判明した。非入力modeへの余分な中立送出assertと、例外時のstatus戻し漏れが原因だった。unit_057時点とunit_060の初回再検証の合格表示は最終受入の根拠に使わず、原観測を残して検証器修正後に再実行した。input実行はerrorなしで押下後の中立送出を観測済み。過去記録への訂正はunit_057の9.2、errorなし・終了コード0まで確認した最終結果はunit_060の9.1へ記録した。

unit_055を実装commit `6d10407`で完了した。製品のcallback省略・memory_dir・read-only分岐を削除し、接続unit 164件、サービス/GUI/終了integration 119件とruff・ty・lock・buildを通過した。公開API・docstring・試験の失敗原因も独立レビューで確認した。

unit_056は`d1321aa`・`9d09ba1`で通知の契約を縮小し、`939724d`で変換と現在turnの蓄積を分離した。接続/GUI 213件、回答/サービス/終了77件とruff・ty・lock・buildを通過し、独立レビューでも修正必須の指摘はなかった。

unit_057は`de3956e`〜`f886a97`で終了要求を一元化した。関連309件・ruff・ty・lock・buildを通過。独立レビューで見つけた処理完了時の起床漏れと切断理由の降格を修正した。失敗後の開始通知の禁止案は、最新IDを明示closeに使い、errorと入力閉鎖・RPC非再送を維持するため採用しなかった。採用runtimeでの明示停止・自然最終応答・同一接続再開・終端前切断と、CH552の短い右入力・中立送出・映像での停止維持を確認した。実測条件、検証器側の初回失敗、局所gateは子9.1に記録した。

unit_058は`df794a7`で操作配送を整理し、`f18124f`でshutdown中のGUI側拒否がloop外から保存taskを作る不具合を修正した。関連311件・ruff・ty・lock・buildと独立レビューを通過した。

unit_059は`7b04abf`・`f063856`でサービスのResources/Runを分離した。全846件（60.91秒）とruff・ty・lock・buildを通過。到達根拠のないrun終了例外の追加試験と回収拡張は撤回し、独立レビューで阻害指摘なし。実機LEFT一回の途中停止、中立送出、136行での書込停止、同接続再開、自然最終応答、終了時の全資源回収を確認した。局所条件・映像確認の範囲は子9.1を正本とし、接続側分離後の検証へ流用しない。

サービス配送の変更前に、`uv run pytest tests/unit tests/integration -q --basetemp=tmp/pytest-u057-full --tb=short`を実行し、845 passed（60.53秒）を確認した。これはunit_057完了時点の全体回帰と後段の構造変更の基準であり、unit_058以降の検証結果には数えない。

実機受入の準備として`tmp/u054/m1_acceptance.py --execute --mode capture`を実行し、現行設定のUSB映像を2秒後・5秒後に取得、停止まで確認した。結果は`tmp/u054/m1-capture-20261113-215939-477655/report.json`。runtimeとserialは開いておらず、ゲーム入力は送信していない。これは映像の前提確認であり、新しい終了処理の検証ではない。

M0確定後、製品変更前の基準として`uv run pytest tests/unit tests/integration --basetemp tmp/pytest-u054-baseline -q`を実行し、847 passed（61.00秒）を確認した。この結果は既存実装の基準であり、新設計の検証結果には数えない。unit_055の仕様と最初のTDD項目を作成し、関連正本の反映後に製品実装を開始した。

M0の採用内容をinitial・後続親仕様・READMEへ反映した。専用整理の開始・復旧・受入条件を撤去し、M2未実装を明記した。`uv run python -X utf8 tmp/u054/validate_docs.py 6794752`は変更文書19件・ローカル参照先334件・unit 55件を検査してerrors=0、`git diff --check`も成功。unit_055へ実装開始を通知した。

実接続の前提確認として、既存の隔離homeを現行製品のcreate_connectionで開き、認証済み・sandbox ready・catalog 8件、connecting→ready→closedを確認した。thread/turn開始と準備callbackは0回で、モデル実行・capture/serialのopenは行っていない。結果は`tmp/u054/open-20261113-213548-838906/report.json`。この確認もM1変更前であり、新しい停止・回収設計の成立確認ではない。

### 実装時の計画

各子では該当pytestを改訂し、変更範囲に対応する試験と`uv run ruff format --check .`、`uv run ruff check .`、`uv run ty check --no-progress`、`git diff --check`から必要な検査を選ぶ。横断変更と工程全体の統合確認では`uv run pytest tests/unit`・`uv run pytest tests/integration`を実行する。公開API変更では型境界・docstringのskillと`uv lock --check`・`uv build`も適用する。依存変更があれば`uv sync --dev`等を[AGENTS.md](../../../AGENTS.md)に従って追加する。

終了所有の変更では、採用runtimeで自然最終応答、明示停止、有限の端末書込、回収後の同一接続再開、終端待ち中の切断を確認する。書込条件・採用版・操作順・検証側の介入を記録し、新案の成立を現行実装のログで代用しない。開始通知順の前提もこの接続確認で観測する。

入力・停止の配線変更では短い実機入力と中立送出を確認し、模擬試験と区別する。保存変更は一時領域で正常追記・再読込・部分失敗を確認する。性能を主張する場合だけ同条件で比較する。未実施の受入は完了扱いにしない。

### M1の最終確認

最終製品commitは`a40edfa`。全unit/integrationは847 passed（62.78秒）、ruff format/check・ty・lock・buildも成功した。unit_059で入力所有変更後のCH552短入力と中立送出・映像上の停止維持、unit_060で接続所有変更後の実認証・準備状態・停止回収・同接続再開・自然最終応答・終端前切断を確認した。新規login・sandbox setup・実質問の再受信、他機器、長時間運転は今回の実測へ含めない。

| class本体（AST比較） | 基準`6794752` | 最終製品 |
|---|---|---|
| CodexConnection | 1,704行、初期属性48 | 1,215行、初期属性29 |
| CodexServiceThread | 879行、初期属性39 | 673行、初期属性22 |

初期属性は依存参照を含む直接保持数で、移した担当の内部状態を含まない。総状態がこの差分だけ消えたという意味ではない。削除したものは独立thread ID・要求の逆引き辞書2個・task二重登録・不要な例外callback、縮小したものはサービスのGoal全体→status、モデル選択→既存snapshot一件、ログイン先行通知全体→成否である。残る接続本体は会話・Goal・開始/終了の調整を担い、依然として大きい。数値を下げるための別coreや親状態を照会するcallback網は作らなかった。

最終文書確認ではinitial・公開docs・後続plannedのM1完了/M2未実装を読み合わせた。`uv run python -X utf8 tmp/u054/validate_docs.py 6794752`は変更文書26件・ローカル参照先380件・unit 60件の一意性を検査してerrors=0、`git diff --check`も成功した。anchorは自動検査の対象外。本文の意味はソース・受入資料と独立レビューで確認した。

### M2の最終確認

保存検査をunit_064、通常write/closeへの縮小をunit_065、入力の生成時保証と全列能力検査をunit_066、残る境界の整理をunit_067で完了した。新しい検証フラグ・ラッパー・復旧状態は作らず、仕様・公開API・試験を各変更と一緒に改訂した。

| 受入条件 | 最終状態と根拠 |
|---|---|
| 記録・後参照と原本保全 | 共通形式・採番・排他・画像保存先を残し、過去payloadを再解釈しない。内容再検査・二重JSON化・明示flush/fsyncを撤去。unit_064・065の一時保存先と模擬I/O失敗で確認 |
| 入力開始前の全列拒否 | InputState/InputStepの生成時保証、parserの非空列、サービスの全列能力検査へ分担。encoderへ再検査を残さず、後方要素の不正・機器非対応でも当該操作を送らないことをunit_066のunit・HTTP試験で確認 |
| 停止・解除・保存の独立 | 入力・観測・記録の所有を変えず、部分送出・取消し回収・BUSY・保存失敗後の操作を全体integrationで確認 |
| 接続・取得境界の小整理 | `c2cabaa`で設定再解決、`6ffd9f8`で取出し後のNone分岐、`6fa7e8d`でreaderの未設定分岐を削除。既存serial/settings 37件、capture 47件、transport 22件とtyを確認 |

最終製品commit `6fa7e8d`で、`uv sync --dev`・`uv lock --check`、`uv run ruff format --check .`（243 files）・`uv run ruff check .`・`uv run ty check --no-progress`、`uv run pytest tests/unit tests/integration -q`（803 passed、66.37秒）、`uv build`が成功した。

`uv run python scripts/check_package.py`も成功し、独立環境へwheelを導入して同梱資材、機器を開かない取得workerの起動・終了、module/console入口のQt起動・終了を確認した。初回はsandboxの`#!sandbox`を含む一時パスをuvが誤解釈して導入に失敗したため、通常の一時環境で再実行した。製品の修正は不要だった。

保存・入力の契約と、残す境界・削る検査をソースと試験で照合した。公開APIの前提・例外は型注釈・docstring・docsへ反映し、機器適合と非空列を型だけで保証したとは扱わない。今回の変更は送出byte列・停止配線を変えず、実機・実app-server・電源断・性能を再実測していない。M1やunit_061の実測をM2の実測として数えていない。

完了移動後、`uv run python -X utf8 tmp/u054/validate_docs.py main`で変更文書25件・ローカル参照343件・unit番号67件の一意性を確認し、errors=0だった。`git diff main --check`も成功した。anchorは自動検査に含めず、移動先の第5.9節など参照見出しを読み合わせた。initial・U08/U10の未実装表記を更新し、過去のcomplete仕様の受入記録は移動リンク以外を変更していない。

## 10. 先送り事項

長時間の資源・記録量評価は[U10](../../planned/unit_015/CONTINUOUS_OPERATION.md)、アプリ再起動後の通常復帰は[U08](../../planned/unit_013/RESUME.md)が所有する。保存の通常write/close、過去payloadの不透明な保持、入力の生成時保証と開始前の機器適合を引き継いだ。U07・U09は初期版対象外であり、専用終了や退避・復元エンジンを後で必ず作る約束にはしない。ApplicationShellの分割は、別のUI変更で必要になった時点で再評価する。公開一覧に属さない任意の子孫プロセスの終了保証は追加しない。

## 11. 完了チェックリスト

- [x] M0へ着手し、残す契約・削る契約・失う挙動・状態所有を具体化した
- [x] 既存仕様・テストの変更方針と、実測から結論できる範囲を記録した
- [x] ユーザーと設計案を確認し、採否を記録してM0を完了した
- [x] M1の子作業で契約・状態・分岐を削減し、正本と公開説明を更新した
- [x] M1で改訂・削除した試験の理由と、必要な製品・実接続・実機検証を記録した
- [x] M2の保存保証・入力検証・残件を実装し、対応する受入を確認した
- [x] 先送りを後続仕様へ接続し、工程全体の受入を満たした
