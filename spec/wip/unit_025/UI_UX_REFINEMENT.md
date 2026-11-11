# 主画面・設定・会話のUI/UX改善

## 1. 目的・状態・起点

状態: **wip**。U01〜U03の現行GUIを横断する独立した修正単位。[unit_024レビュー](../../complete/unit_024/UI_UX_REVIEW.md)のRV01〜RV11と、エラー説明の文章を許可する2026-11-11の追加指示を実装する。レビュー済みという状態を製品修正済みとは扱わない。

開始版は`fd99b84`、ブランチは`codex/uiux-review`。開始時clean、`origin/main`との差はレビュー記録の1 commitだけである。Constitutionは[AGENTS.md](../../../AGENTS.md)、[初期GUI方針](../../initial/01-overview.md)、[実行・停止の境界](../../initial/02-runtime.md)、[工程計画](../../initial/05-implementation.md)。Intent Deltaは、機器選択から接続までの一体化、画面の情報配置、再現不具合修正、エラー説明文の例外追加。

## 2. 対象範囲・対象外

対象は機器一覧・映像適用、主メニュー・ステータスバー、設定保存待ちの停止操作、通知と診断、応答閲覧、質問・承認の状態と配置、現在使える設定・キーボード操作。TDD、代表画面確認、Live Gamer 4K 2.1を使用した可能な実機検証、PRから安全な後片付けまで行う。

U04の観測保存・MCP、U05のゲーム入力・入力遮断、U06のAPIキー認証と接続後モデル選択は対象外。既存TOML値は保持し、未実装項目を利用可能に見せない。Codexの中断はゲーム入力遮断とは区別する。モデル・エフォート候補選択の機能実装はU06へ残す。

## 3. 調査・設計と前提

NyX `648f860e5afd582f80aa58645570a16bfc68a393`の比較結果はunit_024を参照する。ダイアログの適用、初回候補表示、接続メニュー、ステータスバーの役割を採用する。列挙時のGUIスレッド待ち、indexを永続IDにする方式、ダミー画像、固定寸法の強制は採用しない。

一つの映像所有者と独立した読み手、不変フレーム、旧取得プロセスの終了確認は維持する。取得中に設定を開くだけでは切断・再接続しない。保存・設定適用・接続成功を区別し、新接続失敗時は停止状態から明示的な再試行を待つ。旧機器へ自動復帰しない。

## 4. 振る舞い仕様

### 4.1 映像の選択と適用

設定画面を開くと、未接続なら非同期に機器を列挙する。取得中は接続時に得た候補を表示してプレビューを継続する。取得中の新しい機器の検出は現サービスの制約を確認し、明示切断後の再列挙で扱う。列挙のために取得元を二重に開かない。保存値と未保存の選択は列挙で上書きしない。未確認、検索中、未検出、失敗を区別する。

コンボ選択だけでは保存・接続しない。設定ダイアログの「適用」で値を保存し、成功後に映像機器・取得FPSを反映する。旧接続があれば終了確認を待って新接続へ進む。旧接続の終了未確認、保存失敗、新接続失敗を別結果として扱う。設定画面内でも切断できる。未選択の適用では保存値を空にして映像を切断する。

「適用」に接続の意図を付けるのは取得元・取得FPSが変わった場合、または取得中の値と反映対象が異なる場合とする。未接続のまま表示設定・モデルだけを適用しても取得を開始しない。保存済み同一機器へ接続／再試行するボタンを設定内にも置く。保存中の手動切断とアプリ終了要求は、保存結果待ちの未開始接続も取り消す。終了取消しで接続予定を復活させない。

表示FPS・表示サイズだけの変更で映像を再接続しない。設定読込・再読込は自動接続しない。未保存確認の「保存」は永続化だけ行い、アプリ／ダイアログ終了を理由に新接続を起動しない。接続メニューからの選択も同じ保存・適用処理を使う。設定に未保存の編集がある間はメニューから別設定を書き込まず、切断は受け付ける。

### 4.2 主画面・設定と操作可能状態

「ファイル」に設定・終了、「接続」に映像・Codex、「表示」に4プリセットを配置する。右上の独立した設定ボタン行とプレビュー上の接続ボタン・状態・機器名を除く。映像状態と現在の機器名はステータスバーへ置く。FPS・解像度を常設しない。Codexの送信・中断は会話の近くに残す。

映像メニューも、未接続で開いた際に一覧を非同期取得する。取得中は既存候補を使う。設定画面と同じ表示名・同名番号・保存値を参照し、更新ボタンを押すまで候補が出ない構成をメニュー側へ再導入しない。メニューの選択印は保存値、ステータスバーの名前は現在の取得先を表す。

保存状態は設定ダイアログへ置き、映像状態を上書きしない。設定保存中・ダイアログ終了待ちでも既存接続の中断・切断を受け付ける。アプリ終了の既存順序は維持し、終了取消しで自動再開しない。

設定は`Ctrl+,`、送信は`Ctrl+Enter`を提供する。Enter単独は入力・改行・IME確定のために残す。質問・承認を既定値で自動送信しない。現在未対応のAPIキーは選べる新規選択肢にせず、保存済みの値を読み込んだ場合は未対応として表示し、無関係な保存で値を消さない。

### 4.3 エラー・ログ・長い表示

エラー説明の文章をAGENTS.mdと初期GUI方針で許可する。失敗操作と確認できた影響・復旧手段を説明し、未確認の原因は断定しない。技術詳細は選択・コピー可能な専用の詳細領域へ置く。常設ログ欄を主画面に追加しない。

重要な失敗は消えるトーストだけに頼らず、通知を保持して詳細を再表示できるようにする。通知はGUIを待たせず、同じ失敗をポーリングごとに重複表示しない。通常の成功表示は短時間の状態表示で足りる。モーダルは未保存変更など判断が必要な確認に限り、エラー表示で中断・切断を塞がない。実行結果不明の回答・承認を自動再送しない。

通知は必要時の非モーダル表示とステータスバーの入口を使い、詳細はモデルレスの画面で開く。設定・映像・Codex・終了の発生元ごとに保持し、一方の失敗で他方を消さない。古い通知から旧接続世代へ操作を送る再試行callbackは保持せず、現在有効な設定・接続の操作へ戻す。NyXは常設ログとマクロ失敗時のRetryモーダルを持つが、Gigasではこの自動モーダル・再実行を移植しない。

通知は発生元ごとの「最近の失敗」であり、未解決件数や現在の接続状態ではない。後の成功でも履歴は保持し、現在の状態は接続サービスの値で別に表示する。通知を閉じても終了確認や復旧が成立した扱いにしない。詳細の選択・コピー中に別の通知で本文を置き換えず、再選択・再表示時に更新する。同じ失敗のポーリング通知はまとめるが、明示的な再試行が再び失敗した場合は新しい通知として表示する。

長い機器名・モデル名・診断が最小幅を押し広げない。短い状態表示と詳細を分け、800×600の作業領域でも操作部品へ到達できる。ログ保存の新しい仕組みや一般的な通知基盤を過剰に作らない。

### 4.4 会話と質問・承認

応答更新で過去の閲覧位置・文字選択を消さない。末尾追従中だけ新しい末尾へ進む。既存itemの置換と追加を区別して、別item更新でも現在の閲覧を維持する。

回答・承認は、回答待ち、送信中、送信済み、失効、送信結果不明を区別する。要求の解決通知だけで回答送信や許可対象の実行の成功を断定しない。結果はtokenと接続世代に対応させ、別要求へ取り違えない。失効後の再送を許さない。

要求中は読み取り専用の入力欄より質問・承認を優先し、下書きを保持する。新しい要求の見出しから読み始められ、判断ボタンへ到達できる。解決済みカードは短い完了表示へ畳み、必要なら内容を再表示できる。承認の自動選択・自動許可をしない。

## 5. TDD Test List

1 itemずつredの理由を確認し、green・必要な構造整理・記録とcommitを終えてから次へ進む。テストで文書の自然言語や見出しを固定しない。既存挙動を意図的に変更する試験は契約変更に合わせて更新する。

| status | item | type | layer | 対応 |
|---|---|---|---|---|
| refactor-skipped | T01: 設定画面の終了待ちでも中断・切断が可能 | regression | integration | RV04。中断無効でred→19 passed |
| refactor-done | T02: 応答更新で閲覧位置・選択を保持し、末尾追従を区別 | regression | integration | RV06。選択消失・折返し位置ずれでred→21 passed |
| refactor-skipped | T03: 要求解決と回答送信結果を区別し、tokenごとに状態を反映 | regression | unit / integration | RV07。5 failed / 4 passed→81 passed |
| refactor-skipped | T04: 小画面の要求を優先表示し、完了内容を折り畳んで下書きを保持 | regression | integration | RV08。初期4件と通知順競合のred→39 passed |
| refactor-skipped | T05: 設定を開くと候補を非同期取得し、取得中は映像と編集を保持 | regression | integration | RV02。5 failed / 1 passed→80 passed |
| refactor-skipped | T06: 設定適用で保存から安全な映像切替まで完結 | regression | integration | RV01。10 failed / 2 passed→97 passed |
| refactor-skipped | T07: 再確認可能なエラー通知と幅制限で操作・診断を維持 | regression | integration | RV05、RV09。GUI 7 failed→146 passed、core 10 failed→65 passed |
| todo | T08: メニューと設定で接続処理を共有し、主画面の状態を集約 | regression | integration | RV03 |
| todo | T09: 未対応認証を新規選択させず既存値は保持 | regression | integration | RV10 |
| todo | T10: 設定・送信のショートカットと終了取消し後の状態を確認 | regression | integration / manual | RV11 |
| refactor-skipped | T11: 本文置換範囲内の選択端点もUnicode文字境界へ収める | edge | integration | T02独立レビューで発見。4 failed / 9 passed→25 passed |
| refactor-skipped | T12: Qtが正規化する改行を含む本文更新でも選択・閲覧位置を保持 | regression | integration | 6 failed / 17 passed→35 passed |

## 6. 対象ファイルと責務

`app/shell.py`は設定保存と接続・終了調整、`app/video.py`は映像の状態遷移、`ui/`はメニュー・表示・通知・編集。`runtime/codex`と`services/codex.py`は回答結果と失敗通知の対応付けに必要な契約を変更する。同じ失敗を状態イベントと操作結果の両方で通知する場合は共通の識別子で対応付け、同じ文章の別操作を消さない。RPCや再試行機構は変更しない。テストは対応するunit/integrationへ追加する。利用手順は`docs/`、規約はAGENTS.mdと初期仕様へ反映する。

## 7. 検証計画・実績

実装前に失敗を確認し、各itemの実績を本節へ追記する。全体gateは`uv sync --dev`、`uv lock --check`、`uv run ruff format --check .`、`uv run ruff check .`、`uv run ty check --no-progress`、unit/integration、`uv build`、`git diff --check`。Python3.12/3.13、代表画面の描画、実機映像切替、終了後プロセスの状態を確認する。実機・IME・DPIの結果は自動試験と分ける。

画面・実機の詳細は[受入記録](UI_ACCEPTANCE.md)へ集約する。T06後の実機適用は確認済みだが、最終メニュー・通知の画面受入とは分ける。

T01: `uv run pytest tests/integration/test_ui_control_lifetime.py -q`で中断ボタン無効の期待したredを確認。設定画面終了待ちとアプリ終了を分け、設定画面終了待ちでは既存の中断・切断を維持した。新規接続は引き続き設定状態で制限する。`uv run pytest tests/integration/test_ui_control_lifetime.py tests/integration/test_app_lifetime.py tests/integration/test_codex_shutdown.py -q`は19 passed。対象ruff check成功、format適用後に対象1 passed。構造変更は不要と判断。

現時点: 製品修正を進行中。画面・実機確認、全体品質gate、PRは未完了。

実機の事前確認: `uv run python -X utf8 tmp/uiux-refinement/hardware_preflight.py`を実行。DirectShowの`USB Video Device`（VID07CA/PID0575、Live Gamer 4K 2.1）で1920×1080のファイアレッドのタイトル画面を目視確認した。約3.06秒で連番1→175、実測57.69 FPS、保持した別読み手の画像は不変。正常解放`graceful`、サービス停止、子プロセス残存なし。これは既存取得基盤の事前確認であり、改善後の設定適用・メニュー・画面の受入を完了した根拠にはしない。

T02: `uv run pytest tests/integration/test_transcript_reading.py -q --basetemp=tmp/pytest-review-t02`で選択消失・方向リセットの6 failed / 2 passedを確認し、変更範囲の編集で8 passed。折返し段落の前置追加で閲覧位置が588pxずれる回帰を追加してred、可視文字の折返し行を保持して9 passed。green後に`Transcript`へ責務を分離し、`uv run pytest tests/integration/test_transcript_reading.py tests/integration/test_codex_gui.py -q --basetemp=tmp/pytest-review-t02`で21 passed。対象ruff・format、全体ty、差分検査成功。QtのUTF-16位置とPython文字位置の違いをemojiで検証し、型ignore/castは追加していない。

T02の独立レビューで、変更範囲内の端点が新しいemojiのUTF-16途中へ入る候補を指摘された。実ウィジェットで`abcd`の先頭1文字選択→`🐉XY`への置換を調べ、位置0–1のまま選択文字が空になることを確認。進行中のT03へ混ぜず、T11として回帰試験と修正を追加する。

T03: 回答直後の誤った送信済み表示・解決通知での上書き・サービスの結果未通知を先にred（5 failed / 4 passed）で確認。`AnswerResult(token, outcome)`と送信失敗の`AnswerDeliveryError`を追加し、未実行の拒否・途中取消し・未接続・旧世代・サービス終了を区別した。確定結果を上書きせず、暫定unknownだけ遅延結果で確定できる。`uv run pytest tests/unit/test_codex_connection.py tests/integration/test_answer_lifecycle.py tests/integration/test_codex_service.py tests/integration/test_codex_gui.py tests/integration/test_codex_shutdown.py -q --basetemp=tmp/pytest-review-t03`は81 passed。対象ruff/format・全体ty・差分検査成功。独立レビューで必須指摘なし。task生成直後の取消しという追加観点も検証し、未送信の1回通知を確認した。結果生成・拒否処理を共通化し、公開説明と型・docstringの契約を揃えた。green後の追加構造変更は不要と判断。

T11: T03完了後に選択。`uv run pytest tests/integration/test_transcript_reading.py -q`でASCIIからemojiへの置換4例が選択文字を失うred（4 failed / 9 passed）を確認。置換範囲内の端点をUnicode文字境界へ進める最小修正により、同ファイルと`test_codex_gui.py`は25 passed。対象ruff/format・全体ty成功。変更範囲内の選択は元の文字を保持できないため、端点を収める契約をdocstringへ明記した。追加構造変更は不要。

T04: 要求中の領域不足、完了カードの未折り畳み、長い選択肢による横幅拡張など4件をredで確認。入力欄の同一インスタンスを隠して下書きと選択を保持し、要求の見出しから表示する。独立レビューで、回答送信中にターン完了が先着すると非表示の下書きが送信可能になる競合もredにし、要求中の送信を無効化した。関連GUI試験39 passed、対象ruff/format・全体ty・差分検査成功。追加構造変更は不要。模擬イベントによるoffscreen描画ではHD相当の要求欄153px（修正前68px）、Full HD相当710px。8画面で見出し・判断操作・折り返しを確認した。描画時だけMeiryo UI 9ptを指定しており、実Codex・実機・IME・複数DPIの検証とは区別する。

T05: `uv run --no-sync pytest tests/integration/test_device_discovery_gui.py --basetemp=tmp/pytest-t05-red -q -o faulthandler_timeout=30`で初回・再表示時の自動列挙と状態区別が未実装であるred（5 failed / 1 passed）を確認。表示時の列挙予定を設定読込後まで保持し、表示済みダイアログの再フォーカスや取得中の映像を邪魔しない。候補・未保存値を保持したまま検索中・未確認・未検出・列挙失敗を区別する。一覧と終了未確認が同時に届いた場合にも失敗を消さない回帰を追加した。関連9試験ファイル80 passed、対象ruff/format・全体ty・差分検査成功。画面試験の暗黙の取得サービスだけを無機器の制御用実装へ差し替え、明示的な実サービスと模擬backendによるプロセス試験は維持した。実spawn試験で列挙だけでは映像を開かず、接続後の設定再表示でも連番が進むことを確認した。共用fixtureの移動はred前の試験境界整理であり、green後の追加構造変更は不要。

T06: `uv run --no-sync pytest tests/integration/test_video_application.py --basetemp=tmp/pytest-t06-red -q --tb=short -o faulthandler_timeout=30`で10 failed / 2 passedのredを確認。適用の保存成功後だけ映像設定を渡し、旧処理の終了確認と新世代の最初のフレームを待つ。保存失敗は旧映像・入力を維持し、切断・アプリ終了で未開始の接続予定を取り消す。取得設定以外の適用と未保存確認の保存は接続を開始しない。共通の`apply_settings`はメニュー用snapshotも受け取り、未保存編集と競合する上書きを拒否する。

T06の追加redでは、未保存選択中の旧設定接続、未接続からの保存待ちを切断できない状態、未選択の明示接続、ポーリングによる切断の再無効化を確認・修正。独立レビューで旧終了未確認と新接続失敗の表示混同もredにして修正した。最終の同ファイル17 passed、関連試験97 passed、対象ruff/format・全体ty・差分検査成功。実CaptureServiceと模擬backendのspawn試験でFPS切替の解放順、新フレーム、別読み手が保持した画像の不変性を確認した。これは物理キャプチャーの実機試験とは区別する。追加構造整理は不要。

T12: 横断レビューで`before\nKEEP`の`KEEP`を選択した状態から`new\r\nbefore\nKEEP`へ更新すると選択が消えることを実ウィジェットで確認。T06のcommit後、`uv run pytest tests/integration/test_transcript_reading.py -q --basetemp=tmp/pytest-t12-red --tb=short`でCRLF・ソフト改行・非改行空白を含む6 failed / 17 passedのredを確認した。本文の比較元をQtの`toRawText()`へ変え、段落改行だけを1文字へ揃えて23 passed。通常空白への置換は行わず、非改行空白とソフト改行を保持する。同ファイルと`test_codex_gui.py`は35 passed、対象ruff/format・全体ty成功。正逆の選択、閲覧位置、更新後の再追記を検証し、追加構造変更は不要。

T07: `uv run pytest tests/integration/test_notifications.py -q --basetemp=tmp/pytest-review-t07`で初期7件のredを確認。発生元別の最近の失敗、非モーダル通知、モデルレスの詳細snapshot、選択・コピーの維持を実装した。追加redで詳細画面のEnter誤発火、旧失敗の取消し時再通知、成功した適用と後の取得失敗の混同、過去のローカルエラーがサービス終了を隠す経路を修正した。同ファイルは13 passed、関連GUI 13ファイル146 passed。

T07のCodex側は`uv run pytest tests/unit/test_codex_connection.py tests/integration/test_codex_service.py -q --basetemp=tmp/pytest-t07-correlation-red`で10 failed / 53 passed。省略可能な`failure_id`を状態・例外・結果へ渡し、同じ失敗だけを対応付けた。独立レビューで終了時の中断失敗にも相関が必要と分かり、追加red→修正後は65 passed。型/docstring・API docsを確認し、ignore/castは追加していない。担当ruff/format・全体ty・差分検査成功、独立レビューの必須指摘はすべて解消。追加構造整理は不要。

## 8. 先送り事項

APIキー認証とモデル候補の取得・接続後切替はU06、シリアル機器接続はU05。マルチモニターDPI、長時間運転、スクリーンリーダーは環境で確認できない場合に未検証として残す。RV01〜RV10の現行不具合を未実装のまま完了扱いにしない。

## 9. 完了条件

- [x] ユーザー指摘、現在のコード、NyXとの差を仕様化した
- [ ] 全TDD itemを検証・commitし、後続工程の未実装と分けた
- [ ] エラー説明文の例外を規約・製品・docsで整合させた
- [ ] 代表画面と可能な実機確認を記録した
- [ ] 品質gateと独立レビューを通過した
- [ ] PRへ渡す変更・検証結果・未検証事項を揃えた

このunitの`complete`への移動は、実装・受入・ローカルgateを終えたPR提出可能な状態を表す。依頼全体の完了には、その後のPR check・レビュー・merge、main同期、安全なローカル／リモートブランチ整理も必要である。将来のmergeを本書で完了済みとはせず、実際のPR状態・merge SHA・最終Git同期結果で確認する。
