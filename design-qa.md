# 仮想コントローラの画面照合

source visual truth: [採用画像](spec/wip/unit_078/assets/virtual-controller-proposal.png)

実装はPySide6のネイティブウィンドウ。通常状態はCH552、640×420論理px、Windows 150%（DPR 1.5）、Yu Gothic UI。画像のタイトルバーと背景余白を除いた1264×826を640×420へ縮小し、960×630の実装client画像も640×420へ正規化した。CSSは対象外。

## 比較履歴

1. [初回比較](spec/wip/unit_078/assets/controller-comparison-1.png) / [実装1](spec/wip/unit_078/assets/controller-native-1.png)
   - [P2] ABXYと中央の記号が採用画像より小さく、操作部の識別性が下がっている。操作部の高さに合わせて描画文字を拡大する。
   - [P2] 暗色QPaletteのMidでは外周と背景の差が小さく、stickとpadの境界が読みにくい。前景色から半透明の外周色を作り、padの中央境界も描く。
   - 配置、左右stickの色、上部・中央・下部の構成は維持。標準タイトルバー、単色背景、ネイティブパレットは意図した差分。
2. [修正後比較](spec/wip/unit_078/assets/controller-comparison-2.png)で外周とABXYの識別性を確認。中央±はまだ弱いため線描へ変更。独立レビューの縦長時の文字切れも採用し、描画fontを部品の幅・高さへ収めた。
3. [最終比較](spec/wip/unit_078/assets/controller-comparison-3.png) / [実装3](spec/wip/unit_078/assets/controller-native-3.png)で再確認。中央±・ABXY・外周のP2を解消。最小・無効・3DS・拡大font・縦長3DSを別撮影し、操作部の重なりや文字切れがないことを確認。

## 必須観点

文字: Yu Gothic UI。ABXYは高さへ合わせ、幅と高さに収まる範囲へ制限。中央±は線描。採用画像の字形・太さを完全一致させずWindowsのフォントを使用する差分は許容。説明文・状態バー・見えるstick名はない。

余白・配置: 上部6ボタン、左上stick、右上ABXY、中央2×2、左下pad、右下stick。画像はclient領域に正規化した。

色: 左シアン・右赤を維持。前景色から作った半透明の外周で背景との差を確保した。無効状態はパレットの無効前景と薄い軸色を使用。

画像品質: 操作部はQPainterによるベクトル描画。撮影・HOMEは標準機能を表す単純なアイコン。製品写真・ロゴ等の画像資産はない。

内容: 採用された操作ラベルのみ。3DSではSELECT/STARTへ置換し、LS/RS/撮影を無効表示。

## 追加状態・操作証跡

[最小サイズ](spec/wip/unit_078/assets/controller-minimum.png)、[3DS](spec/wip/unit_078/assets/controller-3ds.png)、[無効](spec/wip/unit_078/assets/controller-disabled.png)、[18pt font](spec/wip/unit_078/assets/controller-large-font.png)、[縦長3DS](spec/wip/unit_078/assets/controller-tall-3ds.png)。すべてDPR 1.5。最小寸法は520×350を下限とし、fontMetricsから文字高さ・SELECT幅に必要な分だけ増やす。

全景の640×420比較で全ラベル・外周が読めるため、部分切抜きは不要と判断した。追加状態はnative clientの原寸画像でも確認した。Qtのマウスイベントで保持・外部座標release・最小化・非アクティブ化・無効化を検査し、Windows上でも操作部外へのドラッグ→解放で解除通知と中央復帰を観測。CUAのdrag APIは窓境界外の座標を拒否するため、実際に窓の外で離す操作は未検証。非対応protocolの実機やクロスモニタDPI変更も未検証。ブラウザconsole検査はnative Qtのため対象外。

残るP3: 採用画像の背景質感・陰影・字の太さはネイティブパレットと標準フォントへ置換している。追加の装飾は行わない。

実施: 文字と外周の修正、修正後の同寸法再比較、追加状態の確認。未解決のP0/P1/P2はない。

final result: passed
