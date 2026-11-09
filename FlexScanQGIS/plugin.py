import math
import os
import queue
import tempfile
import zipfile
from datetime import datetime
from xml.etree import ElementTree as ET

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import (QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QSpinBox, QDoubleSpinBox, QTextEdit, QVBoxLayout, QApplication,
    QWidget)
try:
    from qgis.PyQt.QtGui import QAction, QIcon
except ImportError:  # Compatibility with QGIS builds exposing QAction in QtWidgets
    from qgis.PyQt.QtWidgets import QAction
    from qgis.PyQt.QtGui import QIcon
from qgis.core import (QgsProject, QgsVectorLayer, QgsFeature, QgsField, QgsTask,
    QgsWkbTypes, QgsSpatialIndex, QgsApplication, QgsVectorFileWriter,
    QgsGeometry, QgsPointXY)
from .scan_engine import expected_from_population, scan


try:
    BUTTON_OK = QDialogButtonBox.Ok
    BUTTON_CANCEL = QDialogButtonBox.Cancel
except AttributeError:  # Qt 6 scoped enum names
    BUTTON_OK = QDialogButtonBox.StandardButton.Ok
    BUTTON_CANCEL = QDialogButtonBox.StandardButton.Cancel

try:
    # PyQt5/QGIS 3 exposes QVariant; PyQt6/QGIS 4 removed it.
    from qgis.PyQt.QtCore import QVariant
    FIELD_INT, FIELD_DOUBLE, FIELD_STRING = QVariant.Int, QVariant.Double, QVariant.String
except (ImportError, AttributeError):
    from qgis.PyQt.QtCore import QMetaType
    FIELD_INT = QMetaType.Type.Int
    FIELD_DOUBLE = QMetaType.Type.Double
    FIELD_STRING = QMetaType.Type.QString


class ScanTask(QgsTask):
    def __init__(self, title, obs, exp, adjacency, coordinates, max_size, sims,
                 direction, seed, nearest_neighbors, candidate_limit,
                 max_secondary_clusters, event_queue, callback):
        super().__init__(title, QgsTask.CanCancel)
        self.obs, self.exp, self.adj = obs, exp, adjacency
        self.coordinates = coordinates
        self.nearest_neighbors = nearest_neighbors
        self.candidate_limit = candidate_limit
        self.max_secondary_clusters = max_secondary_clusters
        self.max_size, self.sims, self.direction, self.seed = max_size, sims, direction, seed
        self.event_queue = event_queue
        self.callback = callback
        self.result = None
        self.error = None

    def run(self):
        try:
            self.event_queue.put(("log", "統計解析を開始"))
            self.result = scan(self.obs, self.exp, self.adj, self.coordinates,
                self.max_size, self.sims,
                self.direction, self.seed,
                progress=self._progress,
                status=lambda message: self.event_queue.put(("log", message)),
                cancel=self.isCanceled, nearest_neighbors=self.nearest_neighbors,
                max_candidates=self.candidate_limit,
                max_secondary_clusters=self.max_secondary_clusters)
            self.event_queue.put(("log", "統計解析が完了。結果をGeoPackageに保存します"))
            return True
        except Exception as exc:
            self.error = str(exc)
            self.event_queue.put(("log", f"解析エラー: {self.error}"))
            return False

    def _progress(self, done, total):
        value = 40 + int(45 * done / total) if total else 85
        self.setProgress(value)
        self.event_queue.put(("progress", value, f"モンテカルロ検定 {done:,}/{total:,}"))

    def finished(self, success):
        try:
            self.callback(self.result if success else None, self.error)
        except Exception as exc:
            try:
                self.callback(None, f"解析後処理エラー: {exc}")
            except Exception as final_exc:
                self.event_queue.put(("log", f"解析後処理エラー: {final_exc}"))


class ScanProgressDialog(QDialog):
    """Visible progress and persistent log for preprocessing, scan and save."""

    def __init__(self, parent, event_queue, log_path):
        super().__init__(parent)
        self.event_queue = event_queue
        self.log_path = log_path
        self.task = None
        self.done = False
        self.setWindowTitle("G-CHAM Flexible Scan 進捗")
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self.status = QLabel("準備中")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_view, 1)
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("解析をキャンセル")
        self.close_button = QPushButton("処理中…")
        self.close_button.setEnabled(False)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.cancel_task)
        self.close_button.clicked.connect(self.close)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start(150)

    def add_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log_view.append(line)
        try:
            with open(self.log_path, "a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        except OSError as exc:
            self.log_view.append(f"ログファイルへの書き込み失敗: {exc}")

    def set_progress(self, value, label=None):
        self.progress.setValue(max(0, min(100, int(value))))
        if label:
            self.status.setText(label)

    def _drain_events(self):
        while True:
            try:
                event = self.event_queue.get_nowait()
            except queue.Empty:
                break
            if event[0] == "log":
                self.add_log(event[1])
                self.status.setText(event[1])
            elif event[0] == "progress":
                self.set_progress(event[1], event[2])

    def cancel_task(self):
        if self.task is not None and not self.done:
            self.task.cancel()
            self.cancel_button.setEnabled(False)
            self.add_log("キャンセル要求を送信しました。処理の区切りで停止します")

    def finish(self, success, message):
        self._drain_events()
        self.add_log(message)
        self.done = True
        self.cancel_button.setEnabled(False)
        self.close_button.setText("閉じる")
        self.close_button.setEnabled(True)
        if success:
            self.set_progress(100, "保存完了")
        else:
            self.status.setText("処理終了（エラーあり）")

    def closeEvent(self, event):
        if not self.done:
            event.ignore()
            return
        self.timer.stop()
        event.accept()


class ScanDialog(QDialog):
    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface = iface
        self.setWindowTitle("G-CHAM Flexible Scan")
        self.resize(500, 420)
        form = QFormLayout()
        self.layer = QComboBox()
        for lyr in QgsProject.instance().mapLayers().values():
            if isinstance(lyr, QgsVectorLayer) and lyr.isValid() and lyr.geometryType() == QgsWkbTypes.PolygonGeometry:
                self.layer.addItem(lyr.name(), lyr.id())
        self.id_field, self.obs_field = QComboBox(), QComboBox()
        self.x_field, self.y_field = QComboBox(), QComboBox()
        self.expected_mode = QComboBox()
        self.expected_mode.addItem("背景人口から自動計算", "population")
        self.expected_mode.addItem("期待数フィールドを指定", "field")
        self.exp_field, self.population_field = QComboBox(), QComboBox()
        self.layer.currentIndexChanged.connect(self._fields)
        self.expected_mode.currentIndexChanged.connect(self._update_expected_controls)
        self.max_size = QSpinBox(); self.max_size.setRange(1,100); self.max_size.setValue(15)
        self.candidate_limit = QSpinBox()
        self.candidate_limit.setRange(100_000, 10_000_000)
        self.candidate_limit.setSingleStep(100_000)
        self.candidate_limit.setValue(2_000_000)
        self.candidate_limit.setToolTip(
            "候補が多い解析では上限を引き上げられます。候補数はKや隣接構造により急増します。"
            "メモリ保護のため、実際の上限はKに応じて自動的に下げる場合があります。"
        )
        self.max_secondary_clusters = QSpinBox()
        self.max_secondary_clusters.setRange(0, 10_000)
        self.max_secondary_clusters.setValue(0)
        self.max_secondary_clusters.setToolTip(
            "0はrflexscanの既定に合わせ、互いに重ならない二次クラスターをp値1に達するまで探索します。"
            "1以上は二次クラスターの最大出力数です。各二次クラスターは最尤クラスターと同じ"
            "モンテカルロ最大統計量分布で個別に検定します。"
        )
        self.sims = QSpinBox(); self.sims.setRange(0,99999); self.sims.setValue(999)
        self.alpha = QDoubleSpinBox()
        self.alpha.setRange(0.001, 0.2)
        self.alpha.setDecimals(3)
        self.alpha.setSingleStep(0.01)
        self.alpha.setValue(0.05)
        self.alpha.setToolTip(
            "この値以下のモンテカルロp値を有意クラスターと判定します。"
            "シミュレーション回数を0にするとp値が得られず、有意クラスターは出力されません。"
        )
        self.seed = QSpinBox(); self.seed.setRange(0,2147483647); self.seed.setValue(12345)
        self.seed.setToolTip(
            "このPython実装内でモンテカルロ標本を再現するための値です。"
            "乱数種を変えても観測クラスターと統計量は変わらず、p値のシミュレーション結果が変わります。"
            "rflexscan/FleXScanと同じ乱数列になる設定ではありません。"
        )
        coordinate_tip = "人口重心などのX/Y座標を指定できます。レイヤと同じ投影座標系の座標値を使用してください。"
        self.x_field.setToolTip(coordinate_tip)
        self.y_field.setToolTip(coordinate_tip)
        self.direction = QComboBox(); self.direction.addItems(["HOT", "COLD", "BOTH"])
        self.output_path = QLineEdit()
        default_name = "FlexibleScan_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".gpkg"
        self.output_path.setText(os.path.join(os.path.expanduser("~"), default_name))
        self.summary_path_label = QLabel()
        self.output_path.textChanged.connect(self._update_summary_path)
        self._update_summary_path()
        self.output_browse = QPushButton("参照…")
        self.output_browse.clicked.connect(self._browse_output)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_path, 1)
        output_row.addWidget(self.output_browse)
        output_container = QWidget()
        output_container.setLayout(output_row)
        form.addRow("地域ポリゴン", self.layer)
        form.addRow("地域ID", self.id_field)
        form.addRow("症例数（整数）", self.obs_field)
        form.addRow("重心X座標（任意）", self.x_field)
        form.addRow("重心Y座標（任意）", self.y_field)
        form.addRow("期待数の求め方", self.expected_mode)
        form.addRow("期待数フィールド", self.exp_field)
        form.addRow("リスク人口", self.population_field)
        self.expected_field_label = form.labelForField(self.exp_field)
        self.population_field_label = form.labelForField(self.population_field)
        self.expected_help = QLabel()
        self.expected_help.setWordWrap(True)
        self._fields()
        form.addRow("期待数の説明", self.expected_help)
        form.addRow("最大クラスター地域数", self.max_size)
        form.addRow("候補領域の上限", self.candidate_limit)
        form.addRow("二次クラスター数（0=rflexscan方式）", self.max_secondary_clusters)
        form.addRow("シミュレーション回数（0でp値なし）", self.sims)
        form.addRow("有意水準（α）", self.alpha)
        form.addRow("乱数種", self.seed)
        form.addRow("検出方向", self.direction)
        form.addRow("結果GeoPackage", output_container)
        form.addRow("クラスターサマリーExcel", self.summary_path_label)
        note = QLabel(
            "候補領域は起点地域と重心距離が近い最大K地域の範囲で作成します。"
            "X・Y座標フィールドを指定すると、その座標で近傍を判定します。"
            "未指定の場合はポリゴンの幾何学的重心を使います。原著は人口重心です。"
            "統計量・乱数生成・結果はrflexscan/FleXScanとの照合前のため、"
            "学術研究の確定結果として使用する前に検証が必要です。"
        )
        note.setWordWrap(True)
        buttons = QDialogButtonBox(BUTTON_OK | BUTTON_CANCEL)
        buttons.button(BUTTON_OK).setText("解析開始")
        buttons.button(BUTTON_CANCEL).setText("閉じる")
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self); layout.addLayout(form); layout.addWidget(note); layout.addWidget(buttons)

    def _browse_output(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "結果GeoPackageの保存先", self.output_path.text(),
            "GeoPackage (*.gpkg)")
        if path:
            if not path.lower().endswith(".gpkg"):
                path += ".gpkg"
            self.output_path.setText(path)

    def _update_summary_path(self):
        gpkg_path = self.output_path.text().strip()
        if gpkg_path:
            base_path = os.path.splitext(gpkg_path)[0]
            self.summary_path_label.setText(base_path + "_cluster_summary.xlsx")
        else:
            self.summary_path_label.setText("GeoPackageの保存先から自動設定")

    def _fields(self):
        self.id_field.clear(); self.obs_field.clear(); self.exp_field.clear(); self.population_field.clear()
        self.x_field.clear(); self.y_field.clear()
        self.x_field.addItem("ポリゴンの幾何学的重心を使用", None)
        self.y_field.addItem("ポリゴンの幾何学的重心を使用", None)
        self.id_field.addItem("-- 地域IDを選択 --", None)
        for combo in (self.obs_field, self.exp_field, self.population_field):
            combo.addItem("-- 項目を選択 --", None)
        if not self.layer.count():
            self._update_expected_controls()
            return
        lyr = QgsProject.instance().mapLayer(self.layer.currentData())
        if not lyr: return
        for f in lyr.fields():
            self.id_field.addItem(f.name(), f.name())
            if f.isNumeric():
                for combo in (self.obs_field, self.exp_field, self.population_field):
                    combo.addItem(f.name(), f.name())
                self.x_field.addItem(f.name(), f.name())
                self.y_field.addItem(f.name(), f.name())
        self._choose_by_hint(self.obs_field, ("cases", "case", "observed", "症例", "患者数", "発生数", "罹患数", "件数"))
        self._choose_by_hint(self.exp_field, ("expected", "期待数", "期待症例"))
        self._choose_by_hint(self.population_field, ("population", "人口", "リスク人口", "対象人口"))
        self._choose_by_hint(self.x_field, ("centroid_x", "重心x", "中心x", "coord_x", "x座標", "x_coord"))
        self._choose_by_hint(self.y_field, ("centroid_y", "重心y", "中心y", "coord_y", "y座標", "y_coord"))
        self._update_expected_controls()

    @staticmethod
    def _choose_by_hint(combo, hints):
        for i in range(1, combo.count()):
            name = combo.itemText(i).lower()
            if any(hint.lower() in name for hint in hints):
                combo.setCurrentIndex(i)
                return

    def _update_expected_controls(self, *_args):
        auto = self.expected_mode.currentData() == "population"
        self.exp_field.setEnabled(not auto)
        self.population_field.setEnabled(auto)
        if hasattr(self, "expected_field_label"):
            self.expected_field_label.setVisible(not auto)
            self.population_field_label.setVisible(auto)
        if auto:
            self.expected_help.setText(
                "自動計算：期待数＝全地域の症例数合計×各地域のリスク人口÷全地域のリスク人口合計。"
                "地域間で発生率が等しいという帰無仮説を置く単純なPoisson期待数です。"
                "年齢などで標準化した期待数が必要な解析には、層別データが別途必要です。"
            )
        else:
            self.expected_help.setText("各地域について事前に計算した期待症例数の数値フィールドを指定してください。")


class FlexibleScanPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.plugin_menu = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        self.action = QAction(QIcon(icon_path), "G-CHAM Flexible Scan",
                              self.iface.mainWindow())
        self.action.setObjectName("GCHAMFlexibleScanAction")
        set_icon_visible = getattr(self.action, "setIconVisibleInMenu", None)
        if set_icon_visible is not None:
            set_icon_visible(True)
        self.action.triggered.connect(self.run)
        self.plugin_menu = self.iface.pluginMenu()
        # Remove a leftover submenu from versions that registered an empty
        # group name through addPluginToMenu().
        if self.plugin_menu is not None:
            for menu_action in list(self.plugin_menu.actions()):
                submenu = menu_action.menu()
                title = menu_action.text().replace("&", "").strip()
                if submenu is None or title != "G-CHAM Flexible Scan":
                    continue
                for child in list(submenu.actions()):
                    if child.text().replace("&", "").strip() == "G-CHAM Flexible Scan":
                        submenu.removeAction(child)
                        child.deleteLater()
                if not submenu.actions():
                    self.plugin_menu.removeAction(menu_action)
                    submenu.deleteLater()
        if self.plugin_menu is None:
            raise RuntimeError("QGISのプラグインメニューを取得できません")
        self.plugin_menu.addAction(self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            if self.plugin_menu is not None:
                self.plugin_menu.removeAction(self.action)
        self.plugin_menu = None
        self.action = None

    def run(self):
        dlg = ScanDialog(self.iface)
        # QDialog.exec() is the Qt 6 name; exec_() is retained on many Qt 5 builds.
        execute_dialog = getattr(dlg, "exec", None) or getattr(dlg, "exec_")
        if not execute_dialog() or not dlg.layer.count(): return
        lyr = QgsProject.instance().mapLayer(dlg.layer.currentData())
        if dlg.id_field.currentData() is None or dlg.obs_field.currentData() is None:
            QMessageBox.warning(dlg, "入力エラー", "地域IDと症例数の数値フィールドを選択してください。"); return
        auto_expected = dlg.expected_mode.currentData() == "population"
        selected_expected = dlg.population_field.currentData() if auto_expected else dlg.exp_field.currentData()
        if selected_expected is None:
            label = "リスク人口" if auto_expected else "期待数"
            QMessageBox.warning(dlg, "入力エラー", f"{label}の数値フィールドを選択してください。"); return
        use_coordinate_fields = (dlg.x_field.currentData() is not None or
                                 dlg.y_field.currentData() is not None)
        if (dlg.x_field.currentData() is None) != (dlg.y_field.currentData() is None):
            QMessageBox.warning(dlg, "入力エラー", "重心X座標とY座標は両方指定するか、両方とも未指定にしてください。")
            return
        output_path = dlg.output_path.text().strip()
        if not output_path:
            QMessageBox.warning(dlg, "保存先エラー", "結果GeoPackageの保存先を指定してください。")
            return
        if not output_path.lower().endswith(".gpkg"):
            output_path += ".gpkg"
        summary_xlsx_path = os.path.splitext(output_path)[0] + "_cluster_summary.xlsx"
        if not os.path.isdir(os.path.dirname(os.path.abspath(output_path))):
            QMessageBox.warning(dlg, "保存先エラー", "保存先フォルダが存在しません。")
            return
        if os.path.exists(output_path) or os.path.exists(summary_xlsx_path):
            QMessageBox.warning(dlg, "保存先を変更してください",
                                "GeoPackageまたはExcelの出力先に既存ファイルがあります。"
                                "別のファイル名を指定してください。")
            return
        log_path = os.path.splitext(output_path)[0] + "_progress.log"
        event_queue = queue.SimpleQueue()
        progress_dialog = ScanProgressDialog(self.iface.mainWindow(), event_queue, log_path)
        self._progress_dialog = progress_dialog
        progress_dialog.show()
        QApplication.processEvents()
        progress_dialog.add_log(f"入力レイヤ: {lyr.name()}")
        progress_dialog.add_log(f"出力GeoPackage: {output_path}")
        progress_dialog.add_log(f"クラスターサマリーExcel: {summary_xlsx_path}")
        progress_dialog.add_log(f"進捗ログ: {log_path}")
        progress_dialog.cancel_button.setEnabled(False)

        def main_progress(percent, label):
            progress_dialog.set_progress(percent, label)
            QApplication.processEvents()

        try:
            idx_id = lyr.fields().indexFromName(dlg.id_field.currentData())
            idx_obs = lyr.fields().indexFromName(dlg.obs_field.currentData())
            idx_expected = lyr.fields().indexFromName(selected_expected)
            idx_x = lyr.fields().indexFromName(dlg.x_field.currentData()) if use_coordinate_fields else -1
            idx_y = lyr.fields().indexFromName(dlg.y_field.currentData()) if use_coordinate_fields else -1
            total_features = lyr.featureCount()
            if total_features < 2:
                raise ValueError("有効なポリゴンが2件以上必要です。")
            ids, obs, expected_input, geoms, coordinates, feats = [], [], [], [], [], []
            seen_ids = set()
            progress_dialog.add_log(f"入力確認を開始: {total_features:,}地域")
            step = max(1, total_features // 20)
            for row, ft in enumerate(lyr.getFeatures(), start=1):
                if ft.geometry().isEmpty() or not ft.geometry().isGeosValid():
                    raise ValueError("空または無効なポリゴンがあります。先に形状を修復してください。")
                idv, ov, expected_value = ft[idx_id], ft[idx_obs], ft[idx_expected]
                if idv is None or str(idv) in seen_ids:
                    raise ValueError("地域IDに欠損または重複があります。")
                seen_ids.add(str(idv))
                o, val = float(ov), float(expected_value)
                if not math.isfinite(o) or o < 0 or o != int(o):
                    raise ValueError("症例数は0以上の整数で指定してください。")
                if not math.isfinite(val) or val < 0:
                    raise ValueError("期待数またはリスク人口は0以上の有限値で指定してください。")
                geom = ft.geometry()
                if use_coordinate_fields:
                    x, y = float(ft[idx_x]), float(ft[idx_y])
                    if not (math.isfinite(x) and math.isfinite(y)):
                        raise ValueError("重心座標フィールドに欠損または無限値があります。")
                else:
                    point = geom.centroid().asPoint()
                    if point.isEmpty():
                        raise ValueError("ポリゴンの重心を計算できません。形状を確認してください。")
                    x, y = point.x(), point.y()
                feats.append(ft)
                ids.append(str(idv)); obs.append(int(o)); expected_input.append(val)
                geoms.append(geom); coordinates.append((x, y))
                if row % step == 0 or row == total_features:
                    main_progress(5 + int(10 * row / total_features),
                                  f"入力確認 {row:,}/{total_features:,}")
                    progress_dialog.add_log(f"入力確認: {row:,}/{total_features:,}地域")
            if sum(obs) <= 0:
                raise ValueError("全地域の症例数合計が0です。")
            if auto_expected:
                exp = expected_from_population(obs, expected_input)
                progress_dialog.add_log("期待数をリスク人口から自動計算")
            else:
                exp = expected_input
                progress_dialog.add_log("指定された期待数フィールドを使用")
            if sum(exp) <= 0:
                raise ValueError("期待数の合計が0です。")
            if not lyr.crs().isValid() or lyr.crs().isGeographic():
                raise ValueError("入力レイヤを投影座標系に変換してください。")

            max_size = min(dlg.max_size.value(), len(feats))
            progress_dialog.add_log("重心点の近傍インデックスを作成中")
            point_index = QgsSpatialIndex()
            point_fid_to_row = {}
            for row, (x, y) in enumerate(coordinates):
                point_feature = QgsFeature()
                point_feature.setId(row + 1)
                point_feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                point_index.addFeature(point_feature)
                point_fid_to_row[row + 1] = row
                if (row + 1) % step == 0 or row + 1 == total_features:
                    main_progress(15 + int(2 * (row + 1) / total_features),
                                  f"重心インデックス {row + 1:,}/{total_features:,}")
            nearest_neighbors = []
            for row, (x, y) in enumerate(coordinates):
                query = QgsPointXY(x, y)
                nearest_fids = point_index.nearestNeighbor(query, min(len(feats), max_size + 1))
                candidate_rows = {point_fid_to_row[fid] for fid in nearest_fids
                                  if fid in point_fid_to_row and point_fid_to_row[fid] != row}
                ranked = sorted(candidate_rows,
                                key=lambda j: ((coordinates[j][0] - x) ** 2 +
                                               (coordinates[j][1] - y) ** 2, j))
                if len(ranked) < max_size - 1:
                    ranked = sorted((j for j in range(len(feats)) if j != row),
                                    key=lambda j: ((coordinates[j][0] - x) ** 2 +
                                                   (coordinates[j][1] - y) ** 2, j))
                nearest_neighbors.append(ranked[:max_size - 1])
                check_row = row + 1
                if check_row % step == 0 or check_row == total_features:
                    main_progress(17 + int(5 * check_row / total_features),
                                  f"重心近傍検索 {check_row:,}/{total_features:,}")
                    progress_dialog.add_log(f"重心近傍を検索: {check_row:,}/{total_features:,}地域")

            progress_dialog.add_log("隣接インデックスを作成中")
            idx = QgsSpatialIndex()
            for row, feature in enumerate(feats, start=1):
                idx.addFeature(feature)
                if row % step == 0 or row == total_features:
                    main_progress(22 + int(8 * row / total_features),
                                  f"空間インデックス {row:,}/{total_features:,}")
            feature_id_to_row = {ft.id(): row for row, ft in enumerate(feats)}
            adjacency = [set() for _ in feats]
            progress_dialog.add_log("Queen隣接（辺または点接触）を計算中")
            for i, geom in enumerate(geoms):
                for fid in idx.intersects(geom.boundingBox()):
                    j = feature_id_to_row.get(fid)
                    if j is None or j <= i:
                        continue
                    if geom.touches(geoms[j]):
                        adjacency[i].add(j); adjacency[j].add(i)
                row = i + 1
                if row % step == 0 or row == total_features:
                    main_progress(30 + int(15 * row / total_features),
                                  f"隣接行列 {row:,}/{total_features:,}地域")
                    progress_dialog.add_log(f"隣接関係を確認: {row:,}/{total_features:,}地域")
            edge_count = sum(len(x) for x in adjacency) // 2
            progress_dialog.add_log(f"隣接行列の作成完了: {edge_count:,}接続")
            sims = dlg.sims.value()
            seed = dlg.seed.value()
            candidate_limit = dlg.candidate_limit.value()
            max_secondary_clusters = dlg.max_secondary_clusters.value()
            direction = dlg.direction.currentText()
            alpha = dlg.alpha.value()
            progress_dialog.add_log(
                f"解析設定: K={max_size}, simulations={sims}, seed={seed}, "
                f"candidate_limit={candidate_limit:,}, "
                f"max_secondary_clusters={max_secondary_clusters} "
                f"(0=rflexscan方式), direction={direction}, alpha={alpha:g}")
        except Exception as exc:
            progress_dialog.finish(False, f"前処理エラー: {exc}\n解析結果ファイルは作成していません")
            return

        task = ScanTask(
            "G-CHAM Flexible Scan", obs, exp, adjacency, coordinates, max_size, sims,
            direction, seed, nearest_neighbors, candidate_limit,
            max_secondary_clusters, event_queue,
            lambda result, error: self._finish(
                lyr, feats, ids, exp, result, error, output_path,
                summary_xlsx_path, progress_dialog, max_size, sims, seed, alpha))
        progress_dialog.task = task
        progress_dialog.cancel_button.setEnabled(True)
        progress_dialog.add_log("統計解析をバックグラウンドで開始")
        if not QgsApplication.taskManager().addTask(task):
            progress_dialog.finish(False, "QGISタスクマネージャーが解析タスクを開始できませんでした")

    @staticmethod
    def _result_fields(source_fields):
        specs = [
            ("fs_expected_n", FIELD_DOUBLE), ("fs_cluster", FIELD_INT),
            ("fs_p_value", FIELD_DOUBLE), ("fs_llr", FIELD_DOUBLE),
            ("fs_rel_risk", FIELD_DOUBLE), ("fs_cluster_ids", FIELD_STRING),
            ("fs_candidate_n", FIELD_INT), ("fs_max_size", FIELD_INT),
            ("fs_sim_count", FIELD_INT), ("fs_seed", FIELD_INT),
            ("fs_candidate_limit", FIELD_INT),
            ("fs_cluster_rank", FIELD_INT), ("fs_cluster_n", FIELD_INT),
            ("fs_cluster_cases", FIELD_DOUBLE),
            ("fs_cluster_expected", FIELD_DOUBLE),
            ("fs_secondary_limit", FIELD_INT),
            ("fs_alpha", FIELD_DOUBLE), ("fs_significant", FIELD_INT),
        ]
        used = {field.name().lower() for field in source_fields}
        output = []
        for base_name, field_type in specs:
            name, suffix = base_name, 2
            while name.lower() in used:
                name = f"{base_name}_{suffix}"
                suffix += 1
            used.add(name.lower())
            output.append((name, field_type))
        return output

    @staticmethod
    def _build_result_layer(source_layer, feats, ids, expected_counts, result,
                            max_size, simulations, seed, layer_name,
                            result_fields, selected_rows, alpha):
        uri = f"{QgsWkbTypes.displayString(source_layer.wkbType())}?crs={source_layer.crs().authid()}"
        output = QgsVectorLayer(uri, layer_name, "memory")
        if not output.isValid():
            raise RuntimeError("結果用メモリレイヤを作成できません")
        provider = output.dataProvider()
        if not provider.addAttributes(list(source_layer.fields()) +
                                      [QgsField(name, kind) for name, kind in result_fields]):
            raise RuntimeError("結果レイヤの属性項目を作成できません")
        output.updateFields()
        cluster_by_row = {}
        for cluster in result["clusters"]:
            for row in cluster["members"]:
                cluster_by_row[row] = cluster
        additions = []
        for i in selected_rows:
            feature = QgsFeature(output.fields())
            feature.setGeometry(feats[i].geometry())
            cluster = cluster_by_row.get(i)
            cluster_ids = (",".join(ids[j] for j in sorted(cluster["members"]))
                           if cluster else None)
            feature.setAttributes(feats[i].attributes() + [
                expected_counts[i], 1 if cluster else 0,
                cluster["p_value"] if cluster else None,
                cluster["statistic"] if cluster else None,
                cluster["relative_risk"] if cluster else None, cluster_ids,
                result["candidate_count"], max_size, simulations, seed,
                result["candidate_limit"],
                cluster["rank"] if cluster else 0,
                len(cluster["members"]) if cluster else 0,
                cluster["cases"] if cluster else None,
                cluster["expected"] if cluster else None,
                result["max_secondary_clusters"],
                alpha,
                int(cluster is not None and cluster["p_value"] is not None and
                    cluster["p_value"] <= alpha),
            ])
            additions.append(feature)
        ok, _ = provider.addFeatures(additions) if additions else (True, [])
        if not ok:
            raise RuntimeError("結果レイヤへ地物を書き込めません")
        output.updateExtents()
        return output

    @staticmethod
    def _xlsx_column_name(index):
        name = ""
        while index:
            index, remainder = divmod(index - 1, 26)
            name = chr(65 + remainder) + name
        return name

    @classmethod
    def _xlsx_sheet_xml(cls, rows, add_filter=True):
        main_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        ET.register_namespace("", main_ns)
        root = ET.Element(f"{{{main_ns}}}worksheet")
        views = ET.SubElement(root, f"{{{main_ns}}}sheetViews")
        view = ET.SubElement(views, f"{{{main_ns}}}sheetView", {"workbookViewId": "0"})
        ET.SubElement(view, f"{{{main_ns}}}pane", {
            "ySplit": "1", "topLeftCell": "A2", "activePane": "bottomLeft",
            "state": "frozen"})
        sheet_data = ET.SubElement(root, f"{{{main_ns}}}sheetData")
        max_columns = max((len(row) for row in rows), default=1)
        for row_number, values in enumerate(rows, start=1):
            row = ET.SubElement(sheet_data, f"{{{main_ns}}}row", {"r": str(row_number)})
            for column_number, value in enumerate(values, start=1):
                if value is None:
                    continue
                cell_ref = f"{cls._xlsx_column_name(column_number)}{row_number}"
                if isinstance(value, bool):
                    cell = ET.SubElement(row, f"{{{main_ns}}}c", {"r": cell_ref, "t": "inlineStr"})
                    inline = ET.SubElement(cell, f"{{{main_ns}}}is")
                    ET.SubElement(inline, f"{{{main_ns}}}t").text = "はい" if value else "いいえ"
                elif isinstance(value, (int, float)) and math.isfinite(float(value)):
                    cell = ET.SubElement(row, f"{{{main_ns}}}c", {"r": cell_ref, "t": "n"})
                    ET.SubElement(cell, f"{{{main_ns}}}v").text = str(value)
                else:
                    text = str(value)
                    if isinstance(value, float) and math.isinf(value):
                        text = "∞" if value > 0 else "−∞"
                    cell = ET.SubElement(row, f"{{{main_ns}}}c", {"r": cell_ref, "t": "inlineStr"})
                    inline = ET.SubElement(cell, f"{{{main_ns}}}is")
                    ET.SubElement(inline, f"{{{main_ns}}}t", {
                        "{http://www.w3.org/XML/1998/namespace}space": "preserve"}).text = text
        if add_filter and rows and len(rows) > 1:
            last_column = cls._xlsx_column_name(max_columns)
            ET.SubElement(root, f"{{{main_ns}}}autoFilter", {
                "ref": f"A1:{last_column}{len(rows)}"})
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    @classmethod
    def _write_cluster_summary_xlsx(cls, path, clusters, ids, alpha, settings):
        cluster_rows = [[
            "クラスター順位", "種別", "構成地域数", "観測症例数", "期待症例数",
            "相対リスク", "対数尤度比", "モンテカルロp値", "有意判定",
            "有意水準α", "地域ID一覧",
        ]]
        for cluster in clusters:
            p_value = cluster["p_value"]
            significant = p_value is not None and p_value <= alpha
            cluster_rows.append([
                cluster["rank"],
                "最尤クラスター" if cluster["rank"] == 1 else f"二次クラスター {cluster['rank'] - 1}",
                len(cluster["members"]), cluster["cases"], cluster["expected"],
                cluster["relative_risk"], cluster["statistic"], p_value,
                "有意" if significant else ("非有意" if p_value is not None else "判定不可"),
                alpha, ", ".join(str(ids[i]) for i in sorted(cluster["members"])),
            ])
        significant_count = sum(
            cluster["p_value"] is not None and cluster["p_value"] <= alpha
            for cluster in clusters)
        setting_rows = [
            ["項目", "値"],
            ["入力レイヤ", settings["input_layer"]],
            ["有意水準α", alpha],
            ["判定基準", "モンテカルロp値 <= α"],
            ["有意クラスター数", significant_count],
            ["検出クラスター数", len(clusters)],
            ["最大クラスター地域数K", settings["max_size"]],
            ["シミュレーション回数", settings["simulations"]],
            ["乱数種", settings["seed"]],
            ["検出方向", settings["direction"]],
            ["最大二次クラスター数設定", settings["max_secondary_clusters"]],
            ["候補領域数", settings["candidate_count"]],
            ["候補領域上限", settings["candidate_limit"]],
            ["GeoPackage", settings["gpkg_path"]],
        ]
        spreadsheet_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        rel_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        pkg_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
        content_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
        ET.register_namespace("", spreadsheet_ns)
        ET.register_namespace("r", rel_ns)
        workbook = ET.Element(f"{{{spreadsheet_ns}}}workbook")
        sheets = ET.SubElement(workbook, f"{{{spreadsheet_ns}}}sheets")
        ET.SubElement(sheets, f"{{{spreadsheet_ns}}}sheet", {
            "name": "クラスター一覧", "sheetId": "1", f"{{{rel_ns}}}id": "rId1"})
        ET.SubElement(sheets, f"{{{spreadsheet_ns}}}sheet", {
            "name": "解析設定", "sheetId": "2", f"{{{rel_ns}}}id": "rId2"})
        workbook_xml = ET.tostring(workbook, encoding="utf-8", xml_declaration=True)
        rel_root = ET.Element(f"{{{pkg_rel_ns}}}Relationships")
        for number in (1, 2):
            ET.SubElement(rel_root, f"{{{pkg_rel_ns}}}Relationship", {
                "Id": f"rId{number}",
                "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet",
                "Target": f"worksheets/sheet{number}.xml"})
        rel_xml = ET.tostring(rel_root, encoding="utf-8", xml_declaration=True)
        root_rels = ET.Element(f"{{{pkg_rel_ns}}}Relationships")
        ET.SubElement(root_rels, f"{{{pkg_rel_ns}}}Relationship", {
            "Id": "rId1",
            "Type": "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument",
            "Target": "xl/workbook.xml"})
        root_rels_xml = ET.tostring(root_rels, encoding="utf-8", xml_declaration=True)
        types = ET.Element(f"{{{content_ns}}}Types")
        ET.SubElement(types, f"{{{content_ns}}}Default", {
            "Extension": "rels", "ContentType": "application/vnd.openxmlformats-package.relationships+xml"})
        ET.SubElement(types, f"{{{content_ns}}}Default", {
            "Extension": "xml", "ContentType": "application/xml"})
        ET.SubElement(types, f"{{{content_ns}}}Override", {
            "PartName": "/xl/workbook.xml",
            "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"})
        for number in (1, 2):
            ET.SubElement(types, f"{{{content_ns}}}Override", {
                "PartName": f"/xl/worksheets/sheet{number}.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"})
        content_xml = ET.tostring(types, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as workbook_zip:
            workbook_zip.writestr("[Content_Types].xml", content_xml)
            workbook_zip.writestr("_rels/.rels", root_rels_xml)
            workbook_zip.writestr("xl/workbook.xml", workbook_xml)
            workbook_zip.writestr("xl/_rels/workbook.xml.rels", rel_xml)
            workbook_zip.writestr("xl/worksheets/sheet1.xml",
                                  cls._xlsx_sheet_xml(cluster_rows))
            workbook_zip.writestr("xl/worksheets/sheet2.xml",
                                  cls._xlsx_sheet_xml(setting_rows, add_filter=False))

    @staticmethod
    def _write_gpkg_layer(layer, path, layer_name, action):
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "GPKG"
        options.layerName = layer_name
        options.fileEncoding = "UTF-8"
        try:
            options.actionOnExistingFile = getattr(QgsVectorFileWriter, action)
        except AttributeError:
            options.actionOnExistingFile = getattr(
                QgsVectorFileWriter.ActionOnExistingFile, action)
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsProject.instance().transformContext(), options)
        if not result or result[0] != 0:
            detail = result[1] if result and len(result) > 1 else "不明な書き込みエラー"
            raise RuntimeError(f"GeoPackageレイヤ '{layer_name}' の保存に失敗: {detail}")

    def _finish(self, layer, feats, ids, expected_counts, result, error,
                output_path, summary_xlsx_path, progress_dialog,
                max_size, simulations, seed, alpha):
        if result is None:
            progress_dialog.finish(False, f"解析を完了できません: {error or '原因不明'}\n結果ファイルは作成していません")
            return
        clusters = result.get("clusters", [])
        if not clusters:
            progress_dialog.finish(False, "検出クラスターが空のため、結果ファイルを作成できません")
            return
        selected_rows = sorted({i for cluster in clusters for i in cluster["members"]})
        significant_clusters = [cluster for cluster in clusters
                                if cluster["p_value"] is not None and
                                cluster["p_value"] <= alpha]
        significant_rows = sorted({i for cluster in significant_clusters
                                   for i in cluster["members"]})
        progress_dialog.add_log(
            f"有意判定: p <= {alpha:g}、有意クラスター={len(significant_clusters)}件")
        progress_dialog.add_log("全地域の結果レイヤを作成中")
        progress_dialog.set_progress(88, "結果レイヤ作成中")
        QApplication.processEvents()
        tmp_path = None
        tmp_xlsx_path = None
        xlsx_committed = False
        try:
            result_fields = self._result_fields(layer.fields())
            all_layer = self._build_result_layer(
                layer, feats, ids, expected_counts, result, max_size, simulations,
                seed, "G-CHAM Flexible Scan 全地域", result_fields,
                range(len(feats)), alpha)
            cluster_layer = self._build_result_layer(
                layer, feats, ids, expected_counts, result, max_size, simulations,
                seed, "G-CHAM Flexible Scan 検出クラスター", result_fields,
                selected_rows, alpha)
            significant_layer = self._build_result_layer(
                layer, feats, ids, expected_counts, result, max_size, simulations,
                seed, "G-CHAM Flexible Scan 有意クラスター", result_fields,
                significant_rows, alpha)
            progress_dialog.add_log("GeoPackageへ保存中（全地域レイヤ）")
            progress_dialog.set_progress(92, "GeoPackage保存中: 全地域")
            QApplication.processEvents()
            file_handle, tmp_path = tempfile.mkstemp(
                prefix=".flexscan_", suffix=".gpkg", dir=os.path.dirname(output_path) or ".")
            os.close(file_handle)
            os.remove(tmp_path)
            self._write_gpkg_layer(all_layer, tmp_path, "scan_result", "CreateOrOverwriteFile")
            progress_dialog.add_log("GeoPackageへ保存中（一次・二次クラスター）")
            progress_dialog.set_progress(94, "GeoPackage保存中: クラスター")
            QApplication.processEvents()
            self._write_gpkg_layer(cluster_layer, tmp_path, "detected_clusters", "CreateOrOverwriteLayer")
            progress_dialog.add_log("GeoPackageへ保存中（有意クラスターのみ）")
            progress_dialog.set_progress(95, "GeoPackage保存中: 有意クラスター")
            QApplication.processEvents()
            self._write_gpkg_layer(significant_layer, tmp_path, "significant_clusters", "CreateOrOverwriteLayer")
            progress_dialog.add_log("Excelへクラスターサマリーを保存中")
            progress_dialog.set_progress(97, "Excel保存中")
            QApplication.processEvents()
            file_handle, tmp_xlsx_path = tempfile.mkstemp(
                prefix=".flexscan_summary_", suffix=".xlsx",
                dir=os.path.dirname(summary_xlsx_path) or ".")
            os.close(file_handle)
            self._write_cluster_summary_xlsx(tmp_xlsx_path, clusters, ids, alpha, {
                "input_layer": layer.name(), "max_size": max_size,
                "simulations": simulations, "seed": seed,
                "direction": result.get("direction", ""),
                "max_secondary_clusters": result.get("max_secondary_clusters", 0),
                "candidate_count": result.get("candidate_count", 0),
                "candidate_limit": result.get("candidate_limit", 0),
                "gpkg_path": output_path,
            })
            if os.path.exists(output_path) or os.path.exists(summary_xlsx_path):
                raise RuntimeError("保存先に同名ファイルが作成されたため、安全のため保存を中止しました")
            os.replace(tmp_xlsx_path, summary_xlsx_path)
            tmp_xlsx_path = None
            xlsx_committed = True
            os.replace(tmp_path, output_path)
            tmp_path = None
        except Exception as exc:
            if xlsx_committed and os.path.exists(summary_xlsx_path):
                try:
                    os.remove(summary_xlsx_path)
                except OSError:
                    pass
            if tmp_xlsx_path and os.path.exists(tmp_xlsx_path):
                try:
                    os.remove(tmp_xlsx_path)
                except OSError:
                    pass
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            if tmp_path:
                for suffix in ("-wal", "-shm", "-journal"):
                    sidecar = tmp_path + suffix
                    if os.path.exists(sidecar):
                        try:
                            os.remove(sidecar)
                        except OSError:
                            pass
            progress_dialog.finish(False, f"保存エラー: {exc}\n不完全な一時ファイルは削除しました")
            return

        try:
            full = QgsVectorLayer(f"{output_path}|layername=scan_result",
                                  "G-CHAM Flexible Scan 全地域", "ogr")
            cluster = QgsVectorLayer(f"{output_path}|layername=detected_clusters",
                                     "G-CHAM Flexible Scan 検出クラスター", "ogr")
            significant = QgsVectorLayer(
                f"{output_path}|layername=significant_clusters",
                f"G-CHAM Flexible Scan 有意クラスター (p≤{alpha:g})", "ogr")
            if not full.isValid() or not cluster.isValid() or not significant.isValid():
                raise RuntimeError("保存済みGeoPackageをQGISレイヤとして再読込できません")
            QgsProject.instance().addMapLayer(full)
            QgsProject.instance().addMapLayer(cluster)
            QgsProject.instance().addMapLayer(significant)
        except Exception as exc:
            progress_dialog.finish(False,
                f"GeoPackageとExcelは保存済みですが、QGISへの追加に失敗しました: {exc}\n"
                f"GeoPackage: {output_path}\nExcel: {summary_xlsx_path}")
            return

        try:
            from qgis.core import (QgsCategorizedSymbolRenderer,
                                  QgsRendererCategory, QgsFillSymbol)
            cluster_field = result_fields[11][0]
            palette = ["215,70,65,255", "55,126,184,255", "77,175,74,255",
                       "152,78,163,255", "255,127,0,255", "166,86,40,255",
                       "247,129,191,255", "153,153,153,255"]
            cluster_categories = []
            for cluster_info in clusters:
                rank = cluster_info["rank"]
                color = palette[(rank - 1) % len(palette)]
                label = "最尤クラスター" if rank == 1 else f"二次クラスター {rank - 1}"
                symbol = QgsFillSymbol.createSimple(
                    {"color": color, "outline_color": "65,65,65,255"})
                cluster_categories.append(QgsRendererCategory(rank, symbol, label))
            full_categories = [QgsRendererCategory(
                0, QgsFillSymbol.createSimple(
                    {"color": "230,230,230,255", "outline_color": "80,80,80,255"}),
                "クラスター外")]
            full_categories.extend(cluster_categories)
            full.setRenderer(QgsCategorizedSymbolRenderer(cluster_field, full_categories))
            cluster.setRenderer(QgsCategorizedSymbolRenderer(cluster_field, cluster_categories))
            significant_categories = [
                category for category, cluster_info in zip(
                    cluster_categories, clusters)
                if cluster_info in significant_clusters]
            significant.setRenderer(QgsCategorizedSymbolRenderer(
                cluster_field, significant_categories))
        except Exception as exc:
            progress_dialog.add_log(f"レイヤは追加済みですが、スタイル設定に失敗しました: {exc}")
        cluster_lines = []
        for cluster_info in clusters:
            ptext = ("未算出" if cluster_info["p_value"] is None
                     else f"{cluster_info['p_value']:.6g}")
            rank_label = "最尤" if cluster_info["rank"] == 1 else f"二次{cluster_info['rank'] - 1}"
            cluster_lines.append(
                f"{rank_label}: 地域数={len(cluster_info['members'])}, "
                f"LLR={cluster_info['statistic']:.8g}, "
                f"相対リスク={cluster_info['relative_risk']:.8g}, p={ptext}")
        progress_dialog.finish(True,
            f"保存完了: {output_path}\n"
            f"Excelサマリー: {summary_xlsx_path}\n"
            f"レイヤ: scan_result（全地域）, detected_clusters（全検出）, "
            f"significant_clusters（p≤{alpha:g}のみ）\n"
            f"候補領域数={result['candidate_count']:,}, 検出クラスター数={len(clusters)}, "
            f"有意クラスター数={len(significant_clusters)}\n"
            + "\n".join(cluster_lines) + "\n"
            f"進捗ログ: {progress_dialog.log_path}\n研究利用前にrflexscan/FleXScanとの照合が必要です")
