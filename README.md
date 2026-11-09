# G-CHAM Flexible Scan

QGIS上でポリゴン集計データに対する柔軟な空間スキャン統計量を実行する研究用プラグインです。TangoとTakahashiのFlexible Spatial Scan Statisticを参考に、地域の隣接関係と重心距離から柔軟な形状の候補領域を作成します。

## 主な機能

- 症例数と期待数を使ったPoissonモデル
- 背景人口からの期待数自動計算、または計算済み期待数の指定
- Queen隣接と重心距離に基づく候補領域の生成
- ホットスポット、コールドスポットの検出
- モンテカルロ検定によるp値の算出
- 重ならない二次クラスターの逐次検出
- 有意水準を指定した有意クラスター専用レイヤの出力
- GeoPackageとExcelによる結果保存
- 進捗ログとキャンセル操作

## 出力

指定したGeoPackageには次の空間レイヤを保存し、QGISプロジェクトにも追加します。

- `scan_result`：全地域と解析属性
- `detected_clusters`：検出されたすべての一次・二次クラスター
- `significant_clusters`：指定した有意水準を満たすクラスターの地域のみ

クラスター統計はGeoPackage内のテーブルレイヤではなく、GeoPackageと同じフォルダに`*_cluster_summary.xlsx`として別途保存します。Excelには「クラスター一覧」と「解析設定」の2シートが含まれます。

有意判定はモンテカルロp値が設定した有意水準α以下であることを基準とします。シミュレーション回数を0にした場合はp値が算出されず、有意クラスター専用レイヤは空になります。

## インストール

1. [Releases / ZIP package](dist/GISPHN_Flexible_Scan_QGIS_0.1.16.zip)をダウンロードします。
2. QGISの「プラグイン」→「プラグインの管理とインストール」→「ZIPからインストール」を選びます。
3. ZIPを指定してインストールし、「プラグイン」メニューの「G-CHAM Flexible Scan」を起動します。

## 開発・パッケージ検証

プラグイン本体は`FlexScanQGIS/`にあります。Python 3.12での構文検査と、メタデータ、LICENSE、アイコン、ZIP内のディレクトリ構造を確認して配布ZIPを作成します。

```bash
python -m py_compile FlexScanQGIS/__init__.py FlexScanQGIS/plugin.py FlexScanQGIS/scan_engine.py
python scripts/package_plugin.py
```

## 方法上の注意

このプラグインはrflexscanを呼び出さずに動作する独立したPython実装です。候補集合、統計量、乱数生成方法などの違いにより、rflexscan/FleXScanと同じ結果になる保証はありません。研究で使用する前に、公開サンプルデータを用いて候補領域、統計量、クラスター構成、p値を照合してください。共変量調整、時間解析、Bernoulliモデル、FleXScanのRESTRICTED統計量には対応していません。

## 参照文献

- Tango T, Takahashi K. A flexibly shaped spatial scan statistic for detecting clusters. *International Journal of Health Geographics*. 2005;4:11. https://doi.org/10.1186/1476-072X-4-11
- Otani T, Takahashi K. Flexible scan statistics for detecting spatial disease clusters: The rflexscan R package. *Journal of Statistical Software*. 2021;99(13). https://doi.org/10.18637/jss.v099.i13

## ライセンス

GNU General Public License version 3 or later. 詳細は[`FlexScanQGIS/LICENSE`](FlexScanQGIS/LICENSE)を参照してください。

## 引用・参考文献

論文や報告書では、使用したプラグインのバージョンを明記し、ソフトウェアと統計手法の原著論文をそれぞれ引用してください。出力された解析設定（K、シミュレーション回数、乱数種、有意水準、検出方向など）も方法に記載してください。

### プラグインの引用形式

APA第7版：

> GISPHN. (2026). *G-CHAM Flexible Scan* (Version 0.1.16) [Computer software]. GitHub. https://github.com/GISPHN/GISPHN_Flexible_Scan

Vancouver形式：

> GISPHN. G-CHAM Flexible Scan [computer software]. Version 0.1.16. GitHub; 2026 [cited 2026 Nov 9]. Available from: https://github.com/GISPHN/GISPHN_Flexible_Scan

BibTeX：

```bibtex
@software{GISPHN2026FlexibleScan,
  author  = {{GISPHN}},
  title   = {G-CHAM Flexible Scan},
  version = {0.1.16},
  year    = {2026},
  url     = {https://github.com/GISPHN/GISPHN_Flexible_Scan},
  note    = {QGIS plugin}
}
```

### 手法の参考文献（APA第7版）

Tango, T., & Takahashi, K. (2005). A flexibly shaped spatial scan statistic for detecting clusters. *International Journal of Health Geographics, 4*, Article 11. https://doi.org/10.1186/1476-072X-4-11

Otani, T., & Takahashi, K. (2021). Flexible scan statistics for detecting spatial disease clusters: The rflexscan R package. *Journal of Statistical Software, 99*(13), 1–29. https://doi.org/10.18637/jss.v099.i13

このプラグインは`rflexscan`を内部で実行するものではなく、独立した試作実装です。ソフトウェアの引用は、`rflexscan`との数値同等性や検証済みであることを意味しません。
