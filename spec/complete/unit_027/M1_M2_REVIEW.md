# M1・M2 実装／テストレビュー

2026-11-11。対象は `d88d608` → `9eb7b6d`。M1の終点は `6dfbb28`、M2の終点は `9eb7b6d`。レビュー開始時の作業ツリーはcleanだった。

後続対応: 本書のR1〜R3は[unit_030](../unit_030/REVIEW_FIXES_AND_TEST_REDUCTION.md)で修正済み。ユーザーの追加指示により「中」107件も一律削除し、最終362件となった。以下の未修正表示・評価・件数はレビュー時点の記録であり、現在の状態は後続仕様を参照する。

本書は[親仕様](IMPLEMENTATION_SIMPLIFICATION.md)の追補であり、新しい実装仕様ではない。製品コードと既存テストは変更していない。全体UI/UXの設計は対象外。

## 結論と指摘

主要な撤去対象は整理されている。独立した映像接続／停止、設定再読込、初回画像を待ってOKを閉じる予約、モデルレス設定との同期、映像情報専用の計測、カードの手動折りたたみを戻す必要はない。

ただし、取得サービスとGUIの状態解釈に1件、不要な候補再構築に1件、操作上の問題が残る。どちらも再現した。M1以前の実装にも同じ判定・更新経路があり、M1・M2が新たに導入した退行とは断定しない。今回整理した操作仕様を満たすうえで残る不整合として扱う。

| ID | 優先度 | 指摘 | 状態 |
|---|---|---|---|
| R1 | P2 | worker生成前の取消を解放失敗と誤認し、最新の機器選択への接続を捨てる | 実CaptureServiceの処理順を制御して再現、未修正 |
| R2 | P2 | 初回画像の到着で機器候補を再構築し、選択中の候補を元へ戻す | QtとApplicationShell、模擬取得で再現、未修正 |
| R3 | P3 | エラー文が、撤去した設定内の接続状態表示へ案内している | コードと画面構成の照合、未修正 |

### R1: 未開始の要求を取り消した時のcleanupの解釈

根拠: [VideoController._advance_application](../../../src/gigas/app/video.py)、[CaptureService._run_request](../../../src/gigas/game_io/capture/service.py)。レビュー対象の行はそれぞれ348〜352、249〜254。

再現手順:

1. 実CaptureServiceに列挙またはAへの取得要求を渡す。
2. `_run_request`の入口をEventで待たせ、子プロセスをまだ生成させない。
3. VideoControllerへBの適用を渡す。旧要求の取消が要求される。
4. Eventを解放する。実サービスは資源を作っていないため `phase=idle, cleanup=none` へ戻る。

列挙／取得の両方で `spawned=0, apply_state=failed`、通知1件、「旧映像の終了を確認できない」となった。Bは開始されない。GUIはidleのときもgraceful/forcedを要求するが、サービス自身はidle/noneから次の要求を受け付けられる。資源未作成と解放未確認が同じ扱いになっている。

既存の[切替失敗試験](../../../tests/integration/test_video_application.py)の `failure=unreleased` はidle/noneを一律に失敗扱いする。一方、[ControlledCapture.complete_disconnect](../../../tests/integration/peers/controlled_capture.py)は常にgracefulにするため、実サービスの未生成取消と一致しない。`test_latest_target_replaces_unstarted_switch`もA取得後のB待ちを検証しており、最初のworker生成前を扱っていない。

修正案: 資源所有者側の「資源なし」と「解放未確認」を基準に契約を揃える。GUIへ新しい取消フラグを足さない。既存のunreleased期待を見直し、未生成取消の順序を実サービスとの境界試験で扱う。unconfirmed、異常終了、強制終了後の解放確認を削ってよいという意味ではない。

### R2: 状態更新に候補再構築が混在する

根拠: [ApplicationShell._video_changed](../../../src/gigas/app/shell.py)の169〜183行、[SettingsDialog.set_capture_devices](../../../src/gigas/ui/settings_dialog.py)の224〜240行。

再現手順:

1. 保存機器Aへの接続中に設定を開き、機器候補A/Bを表示する。
2. コンボのポップアップでBへ移動し、まだEnterで確定しない。
3. Aの初回フレームを到着させる。機器一覧のrevisionは変えない。
4. Enterを押す。

結果は `before=B, after_first_frame=A, selected_after_enter=path-a`。初回フレームによるchangedが `_update_capture_devices` を呼び、同じ一覧でもclear/addItemを実行する。確定済みcurrentDataは保持するが、選択途中の候補は失われる。

この経路では `_update_capture_devices` と直後の `_render` が同じメニュー描画も重ねて行う。単なるキャッシュ削減の問題ではなく、利用者の操作に割り込む再構築である。

修正案: 一覧・表示する選択値の変更と、検索／取得状態の更新を分ける。同じ一覧を状態変化のたびに再構築しない。追加の「選択保護中」フラグや後回しキューは不要。既存の `test_device_selection_and_rates_preserve_unsaved_values` または機器発見GUI試験を、確定前のキー選択を含む操作へ書き換える。異なる一覧が実際に届いた場合の選択値保持は引き続き必要。

### R3: 失敗時の案内先

[VideoController._notify_failure](../../../src/gigas/app/video.py)の382・394行は「設定で機器と接続状態を確認してください」と案内する。現在の設定ダイアログが示すのは保存／編集対象で、取得状態は主画面、失敗理由は通知詳細にある。

機器の再選択／再適用と通知詳細へ案内する文言に合わせる。固定文章の一致テストを追加する必要はない。

## 残る状態・経路の評価

状態変数の数そのものを削減目標にはしない。現在の操作に対応する情報を消すと、画面・保存・非同期結果の区別が崩れる。

| 状態・経路 | 判断 | 理由 |
|---|---|---|
| baseline・フォーム編集値・提出済み `_io_request` | 維持 | 適用前の編集、保存中に閉じた画面、完了した保存を区別するために必要 |
| `_replacement` 一件・`apply_state`・取得generation | 維持、R1の契約を修正 | A→B→Cで最新の未開始要求だけを反映し、旧画像を採用しないために必要。操作キューや別の接続意図はない |
| `_enumerating`・取得serviceのphase・一覧revision | 維持 | 列挙試行の完了・失敗と取得状態を区別する。R2は状態の追加ではなく更新先の分離で直す |
| `_exit_requested` と `committed` | 維持 | 未保存確認で取り消せる終了要求と、資源解放を開始した終了確定は異なる |
| `_settings_error_notice` | 維持 | フォルダーを開く失敗など別の通知が来ても、設定保存の失敗詳細を保持するために必要 |
| `_loaded_text` | 維持 | QLineEditで正規化された表示から、未編集のCRLF等を原文のまま保存するために必要 |
| `_last_state`・`_last_failure` | 維持 | 通知・描画の重複抑止。別の利用者向け状態や復旧予約ではない |
| カードの `_result_received` | 維持 | 中断時の暫定unknownと、実際に受領した送信結果を区別する。前者を次ターンで消すと遅延結果を失う |
| TurnStarted | 維持 | 次ターンの開始要求と開始確認を分ける最小の境界。既存のturn ID受理時に発行し、別の履歴状態を追加していない |
| 状態変更からの無条件候補再構築・二重メニュー描画 | 整理 | R2。モーダル化後も残る不要な更新経路 |

取得時刻・連番・世代・資源解放結果は、撤去した映像情報画面専用のデータではない。画像の鮮度、旧世代の失効、プロセス所有権の検証に使われており、削除対象にしない。

## 472件の分類と価値

[全ケースの棚卸しCSV](M1_M2_TEST_INVENTORY.csv)に、nodeidごとの機能、配置、実行境界、価値、扱い、理由、M1/M2変更ファイルかどうか、今回のJUnit所要秒を記録した。472行・重複なし。43ファイル、282個のテスト関数からパラメーター展開されている。配置上はunit 185件、integration 287件。

分類は全472件を対象とする。価値は契約と故障時の影響に基づく一次評価であり、検出能力の点数や網羅率ではない。詳細なassertと旧版の比較はM1/M2変更箇所、撤去対象の受け皿、重複候補に集中した。差分外の全故障条件を独立に再現したわけではなく、mutation testingや全組合せの保証も行っていない。

| 機能 | ケース | 判断 |
|---|---:|---|
| 映像取得基盤 | 105 | 期限、共有メモリ、API切替、資源解放を含む。似た形でも異なる故障地点なので一括削減しない |
| Codex通信・実行 | 92 | 応答順序、回答の送達証拠、取消・プロセス終了。模擬接続と実パイプは別の責務 |
| 設定データ・保存 | 74 | 型・境界値・原本保護・未知項目保持。loadとsaveは侵入経路が違うため片方へ寄せない |
| 回答・対話UI | 59 | 送信の一回性、失効、秘密入力、Unicodeと選択保持。UI全面再設計は別途判断 |
| 設定操作・終了 | 54 | 保存の受理／完了、OK／適用／取消、終了時の後始末 |
| 映像操作・描画 | 46 | 設定と実取得の結合、描画。R1/R2の見落としと一部の重複がある |
| 共通UI | 33 | メニュー、通知、寸法。操作契約と内部配置の固定を分けて整理する |
| 起動・配布・基盤 | 9 | 起動・終了のsmokeとメタデータ、製品外のtmpパス確認1件 |

一次評価は「高」363件、「中」107件、「低」1件、「製品外」1件。高は誤接続、データ損失、回答の二重送信、資源残留などを防ぐ契約、またはそれらの主要操作を検証するもの。中は主に入力・表示・閲覧の使いやすさと小さなsmoke。高だからassertがすべて適切とは限らず、R1のように期待が誤ったものも含む。

### 具体的な整理候補

| テスト関数／ケース | 評価と次の扱い |
|---|---|
| `test_failed_switch_stays_stopped_without_rollback_or_retry[unreleased]` | R1。期待の見直しと実サービス境界への置換。終了未確認を拒否する他ケースは維持 |
| `test_device_selection_and_rates_preserve_unsaved_values` | R2。確定値だけでなく選択中の操作を扱うよう既存試験を補強 |
| `test_unselecting_camera_discards_unsaved_device_choice` | 低。選択欄の往復と空値確認だけ。未接続の保存・停止を確認する試験へ寄せて削除可能 |
| `test_tmp_path_stays_in_workspace` | 製品外。作業領域の安全規約ならpytest設定・基盤として管理する。製品受入件数には含めない整理が自然 |
| `test_settings_fields_are_grouped_by_display_io_and_codex` | タブと項目の到達性は残し、QScrollArea等の内部親子構造へのassertを減らす |
| `test_file_and_codex_menus_use_live_actions_and_exit_lifetime` | action配列の完全一致を減らし、コマンドの接続と終了寿命を維持 |
| `test_preview_preserves_pixels_aspect_and_clears_stopped_image` | 部分重複。video_gui側へ整理する余地はあるが、リサイズ直後の境界画素、最新画像への置換、元画像の不変性を移さず丸ごと落とさない |
| `test_auto_resolution_waits_for_old_cleanup_and_first_new_frame` | 内容はOKが先に閉じる現契約。名前だけ旧仕様を連想させるため変更候補 |

CSVでは464件を維持、上記8件を見直し候補とした。8件すべての削除を勧めているわけではない。追加で大量に削る根拠は今回得られていない。

`test_video_settings`の54件は多く見えるが、入力型・有効値・既存ファイルの保全を分けている。`test_codex_connection`の49件も、開始前後・取消前後・回答書込前後で効果が異なる。件数を減らすために一つのループへ隠したり、複数の故障条件を一つの長い試験へまとめたりしない。

### 配置と実行コスト

integration配下にはQtウィジェットだけの試験もあり、unit配下にはファイルI/Oや共有メモリ・スレッドを使う試験がある。ディレクトリ名だけで「重い結合テスト287件」とは判断できない。CSVと末尾の一覧では実際の境界を別に示した。

全472件は今回35.319秒で成功した。所要時間はこの1回の実測で、性能保証やflaky不在の証拠ではない。資源・プロセス試験の期限は遅いCIでの余裕も必要だが、今回の実行では失敗しなかった。CSVの秒数はJUnitのtestcase値であり、処理本体だけを精密に計測した値ではない。

## M1・M2で減らした検証の評価

556→512→472、合計84件減。件数の変化だけでは検証の維持を証明できないため、撤去した経路と残る契約を対応させた。

| 旧試験／整理対象 | 現在の受け皿・判断 |
|---|---|
| 設定再読込、読込待ち後の列挙予約、モデルレスなメニューとフォーム同期 | 操作自体を撤去。起動時読込失敗の原本保護はapp_settings、保存中の画面終了はapp_lifetime、モーダル中のメニュー遮断はvideo_applicationに残る |
| 一時停止・保存済み機器の記憶・専用接続ボタン | 未接続の保存成功後に停止する契約へ変更。settings_apply_contract、settings_source_menu、video_application、実spawnのvideo_guiで確認。保存失敗時に取得を継続する検証も残る |
| OKが初回画像を待つ／途中停止で自動閉鎖を取り消す | 旧仕様は不要。settings_acceptが保存後・初回画像前の閉鎖、無編集の再試行、適用後のキャンセルを確認 |
| 保存／読込失敗中の旧停止ボタン | 映像停止ボタンの検証は削除が妥当。Codex停止はui_control_lifetimeで保存中に設定を閉じて操作、notificationsで保存失敗後の操作を確認。再読込失敗中の停止は再読込機能撤去で不要 |
| 映像情報4件・FPS計測assert | 情報画面と専用計測が撤去対象なので削除が妥当。画像の寸法・画素・時刻・世代・停止失効はcapture系に残る |
| 旧select_deviceの6ケース | 公開関数自体を撤去。現経路select_candidatesの識別子一致、名前／indexへ代替しないこと、同一API内の重複拒否はcapture_platforms、API再列挙はcapture_fallbackに残る |
| カードの手動折りたたみ・再展開 | 操作を撤去。request_layoutをコンパクトな結果と原稿保持に書換え。answer_lifecycleが開始失敗時の保持、次ターン確認後の撤去、遅延結果・旧世代を確認 |
| 旧カードのスクロール試験 | request_layoutに新要求の見出し、長文・狭幅時の選択肢・決定ボタンの到達確認がある。秘密入力消去はcodex_guiに残り、M2で消去assertを追加 |
| 設定キー21→9、解像度×表示方式59→54、閲覧23→19、寸法17→11 | 各値・各キー・選択方向・幅／高さの制約・端数を残した代表化。全組合せは保証しなくなったが、個別の不正型・原本保護・Unicode境界は落としていない |

レビューした変更範囲で、必要な検証を削除したことに起因すると確認できた退行はなかった。ただしR1/R2のような既存の境界の見落としはある。「472件成功」を操作仕様全体の適合証明とは扱わない。

回答のsent/not_sent/unknownは、接続層・ワーカー・GUIで似た試験がある。しかし接続層は書込の証拠、ワーカーは取消と結果配送、GUIは表示・カード寿命を検証している。これらを同一の重複として削るべきではない。

## 検証記録・未検証

| 確認 | 結果 |
|---|---|
| `uv run pytest tests/unit tests/integration -q --tb=short --junitxml=tmp/m1-m2-review/results.xml` | 472 passed、errors/failures/skipped各0、35.319秒 |
| R1の局所プローブ | 実CaptureService、Eventで未spawn位置を制御。列挙／取得の両方で同じ誤失敗、spawn 0回。機器アクセスなし |
| R2の局所プローブ | Qt offscreen、ApplicationShell、ControlledCapture。初回フレームの前後で候補B→A、Enter後A。正常終了まで確認 |
| ソース・旧版比較 | 親仕様とM1/M2子仕様、`git diff d88d608 9eb7b6d`、撤去名の参照検索、対応する既存試験を確認 |
| 一覧・文書 | 収集nodeid/JUnit/CSVの472件一致、重複なし、43ファイル・282関数、リンク、`git diff --check`を確認 |
| ruff・ty・build | not run。今回はレビュー文書とCSVのみの変更。前回M2の結果は子仕様にあり、今回の新しい実行結果としては扱わない |
| Windowsの実表示・実機映像・外部Codex | not run。今回の再現はoffscreen・模擬入力。OSの実操作、実機の受入を再実行したとは扱わない |
| 全体UI/UX設計 | not applicable。別議論のまま |

局所プローブとJUnitは作業領域 `tmp/m1-m2-review/` に置いた。恒久的な回帰テストは追加していない。次の実装でR1/R2を直す際に既存試験を優先して書き換え、撤去済み機能の不在だけを検証する試験は作らない。

## ファイル別の内訳

| テストファイル | 関数 | ケース | 主な境界 |
|---|---:|---:|---|
| [integration/test_answer_lifecycle.py](../../../tests/integration/test_answer_lifecycle.py) | 7 | 7 | Qt・模擬サービスイベント |
| [integration/test_app_entry.py](../../../tests/integration/test_app_entry.py) | 2 | 6 | 独立Pythonプロセス・Qt起動 |
| `integration/test_app_import.py`（後続で削除） | 1 | 1 | 独立Pythonプロセス |
| [integration/test_app_lifetime.py](../../../tests/integration/test_app_lifetime.py) | 7 | 11 | Qt・設定I/Oワーカー・模擬サービス |
| [integration/test_app_settings.py](../../../tests/integration/test_app_settings.py) | 11 | 16 | Qt・設定I/Oワーカー・模擬サービス |
| `integration/test_auth_choices.py`（後続で削除） | 2 | 2 | Qtウィジェット |
| [integration/test_capture_configuration.py](../../../tests/integration/test_capture_configuration.py) | 1 | 5 | Qt・模擬取得サービス |
| [integration/test_capture_failures.py](../../../tests/integration/test_capture_failures.py) | 12 | 20 | 取得サービス・プロセス／資源境界 |
| [integration/test_capture_fallback.py](../../../tests/integration/test_capture_fallback.py) | 8 | 13 | 取得サービス・プロセス／資源境界 |
| [integration/test_capture_service.py](../../../tests/integration/test_capture_service.py) | 7 | 9 | 取得サービス・プロセス／資源境界 |
| [integration/test_codex_gui.py](../../../tests/integration/test_codex_gui.py) | 6 | 10 | Qt・模擬サービスイベント |
| [integration/test_codex_service.py](../../../tests/integration/test_codex_service.py) | 12 | 17 | Qt／asyncioワーカー・模擬接続 |
| [integration/test_codex_shutdown.py](../../../tests/integration/test_codex_shutdown.py) | 4 | 5 | Qt／asyncioワーカー・模擬接続 |
| [integration/test_codex_transport.py](../../../tests/integration/test_codex_transport.py) | 11 | 21 | stdio子プロセス・障害注入 |
| [integration/test_device_discovery_gui.py](../../../tests/integration/test_device_discovery_gui.py) | 5 | 5 | Qt・模擬取得サービス / Qt・取得サービス・実spawn（模擬backend） |
| `integration/test_keyboard_workflow.py`（後続で削除） | 8 | 14 | Qtウィジェット・キー／文字列操作 |
| `integration/test_main_menus.py`（後続で削除） | 9 | 11 | Qt・模擬サービス／設定ワーカー |
| `integration/test_notifications.py`（後続で削除） | 11 | 11 | Qt・模擬サービス／設定ワーカー |
| `integration/test_request_layout.py`（後続で削除） | 8 | 9 | Qtウィジェット・キー／文字列操作 |
| [integration/test_resolution_application.py](../../../tests/integration/test_resolution_application.py) | 2 | 2 | Qt・模擬取得サービス |
| [integration/test_settings_accept.py](../../../tests/integration/test_settings_accept.py) | 4 | 4 | Qt・設定I/Oワーカー・模擬サービス |
| [integration/test_settings_apply_contract.py](../../../tests/integration/test_settings_apply_contract.py) | 4 | 4 | Qt・設定I/Oワーカー・模擬サービス |
| [integration/test_settings_io.py](../../../tests/integration/test_settings_io.py) | 6 | 9 | ファイルI/O・入力検証 |
| `integration/test_settings_keyboard.py`（後続で削除） | 3 | 9 | Qtウィジェット |
| [integration/test_settings_recovery.py](../../../tests/integration/test_settings_recovery.py) | 4 | 5 | Qt・設定I/Oワーカー・模擬サービス |
| [integration/test_settings_source_menu.py](../../../tests/integration/test_settings_source_menu.py) | 1 | 2 | Qt・設定I/Oワーカー・模擬サービス |
| `integration/test_transcript_reading.py`（後続で削除） | 9 | 19 | Qtウィジェット・キー／文字列操作 |
| [integration/test_ui_control_lifetime.py](../../../tests/integration/test_ui_control_lifetime.py) | 1 | 1 | Qt・設定I/Oワーカー・模擬サービス |
| [integration/test_video_application.py](../../../tests/integration/test_video_application.py) | 8 | 13 | Qt・模擬取得サービス / Qt・取得サービス・実spawn（模擬backend） |
| [integration/test_video_gui.py](../../../tests/integration/test_video_gui.py) | 9 | 12 | Qt描画・模擬取得サービス / Qt・取得サービス・実spawn（模擬backend） |
| `integration/test_video_preview.py`（後続で削除） | 1 | 1 | Qtウィジェット |
| `integration/test_video_settings_gui.py`（後続で削除） | 7 | 8 | Qtウィジェット |
| [integration/test_window_presets.py](../../../tests/integration/test_window_presets.py) | 4 | 5 | Qt・模擬取得サービス / Qt・取得サービス・実spawn（模擬backend） |
| [unit/test_capture_backend.py](../../../tests/unit/test_capture_backend.py) | 8 | 19 | 取得モデル・模擬backend／OS境界 |
| [unit/test_capture_frames.py](../../../tests/unit/test_capture_frames.py) | 4 | 5 | 取得モデル・模擬backend／OS境界 |
| [unit/test_capture_models.py](../../../tests/unit/test_capture_models.py) | 5 | 13 | 取得モデル・模擬backend／OS境界 |
| [unit/test_capture_platforms.py](../../../tests/unit/test_capture_platforms.py) | 7 | 13 | 取得モデル・模擬backend／OS境界 |
| [unit/test_capture_transport.py](../../../tests/unit/test_capture_transport.py) | 4 | 8 | 共有メモリ・スレッド |
| [unit/test_codex_connection.py](../../../tests/unit/test_codex_connection.py) | 30 | 49 | asyncio・模擬peer |
| `unit/test_package.py`（後続で削除） | 2 | 2 | 導入済みメタデータ／pytest基盤 |
| [unit/test_settings.py](../../../tests/unit/test_settings.py) | 6 | 11 | ファイルI/O・入力検証 |
| [unit/test_video_settings.py](../../../tests/unit/test_video_settings.py) | 15 | 54 | ファイルI/O・入力検証 |
| `unit/test_window_layout.py`（後続で削除） | 6 | 11 | 純粋な寸法計算 |
