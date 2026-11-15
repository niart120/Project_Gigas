# M1描画試作

製品からは読み込まない検証用コード。app serverへの接続、機器操作、記録書き込みは行わない。採用判断と限界は[検証結果](../M1_VALIDATION.md)を参照。

リポジトリのルートで実行する。

```powershell
$env:QT_QPA_PLATFORM = 'windows'
uv run python spec/wip/unit_082/prototype/demo.py
```

「試作」メニューで短い／長いPlan、活動詳細、推論欄の表示を切り替える。F5でストリーミングを再生し、F6で再生を中断する。回答内の外部リンクはこの試作では開かない。

```powershell
# QWidgetの描画を保存し、終了する。--stayを付けると画面を残す。
uv run python spec/wip/unit_082/prototype/demo.py --capture tmp/activity-display-m1/render

# 単一のGUIスレッドで解析する比較方式
uv run python spec/wip/unit_082/prototype/benchmark.py --out tmp/activity-display-m1/sync.json

# 採用方式：解析ワーカーを使用。全12条件、各3秒の送信
uv run python spec/wip/unit_082/prototype/benchmark.py --worker --out tmp/activity-display-m1/worker.json

# 高負荷を6回繰り返し、待ちデータとメモリを確認
uv run python spec/wip/unit_082/prototype/benchmark.py --worker --history 1000 --length 100000 --interval 50 --duration 5 --repeat 6 --out tmp/activity-display-m1/repeated.json

# 描画・選択・非同期境界の自動試験
$env:QT_QPA_PLATFORM = 'offscreen'
uv run pytest spec/wip/unit_082/prototype -q
uv run ruff check spec/wip/unit_082/prototype
uv run ruff format --check spec/wip/unit_082/prototype
```

`windows`でも試験できるが、OSのクリップボードやUI Automationの影響を受ける。自動試験の再現用は`offscreen`、見た目と性能の確認は`windows`を使い分ける。ベンチマークの待機は送信スレッドとQtイベントループが終了条件を満たすまで続くため、試験中にウィンドウを閉じない。

`document.py`はQtのMarkdown解析・書式制御・文書置換・選択と閲覧位置・左線を扱う。`worker.py`は解析の配送と世代管理、`subtitle.py`は公開要約の切替、`demo.py`と`benchmark.py`は独立した実行入口。製品への移植時は既存の通知・履歴・終了処理へ統合する。特に試作の`shutdown()`は試験終了時の待機であり、製品のGUI終了待ちへそのまま移さない。

試作専用の`ruff.toml`では公開API用の型注釈・docstring規則と、検証用assert・結果出力・サンプル文字列の規則を除外する。構文・未使用コード・インポート・バグ検出などの検査は有効。製品実装は製品側の型・公開API規則に従う。
