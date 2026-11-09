# Changelog

## 0.1.18
- Updates the plugin author display to GISPHN (Ryo Horiike).

## 0.1.17
- Uses scoped Qt and QGIS enums for QGIS 4 / Qt 6 compatibility.
- Documents and suppresses Bandit false positives for output-only XML construction and seeded Monte Carlo pseudorandomness.

## 0.1.16
- Adds QGIS repository metadata, including the Japanese listing description, repository URL and issue tracker.
- Adds a PNG listing icon while retaining the SVG action icon.
- Includes the full GPL-3.0-or-later license text.
- Adds an automated package validation script and CI workflow for syntax, metadata, license and ZIP structure checks.


## 0.1.15
- Adds a separate GeoPackage layer containing only clusters with Monte Carlo p-values at or below the selected alpha.
- Adds an Excel cluster summary workbook with cluster statistics and analysis settings.
- Removes the cluster summary table from the GeoPackage and does not add a statistics table to the QGIS layer list.

## 0.1.14
- Replaces the generic grid icon with a flexible connected-cluster scan icon.
- Displays the icon in the Plugins menu and adds the action to the QGIS Plugins toolbar.


## 0.1.13
- Uses categorized symbology for cluster rank, because cluster rank is an identifier rather than a continuous measurement.
- Clarifies that `fs_cluster_n` is the number of regions in a cluster, not the number of detected clusters.


## 0.1.12
- Adds sequential, non-overlapping secondary cluster detection.
- Tests each selected secondary cluster against the simulated distribution of maximum scan statistics and stops automatically when p reaches 1 by default.
- Adds a setting to cap the number of secondary clusters; 0 uses the rflexscan-style automatic stopping rule.
- Saves cluster-specific statistics in the spatial result layers and adds a cluster summary table to the GeoPackage.

## 0.1.11
- Adds the action directly to `iface.pluginMenu()` instead of using `addPluginToMenu()` with an empty group name.
- Removes an empty leftover G-CHAM Flexible Scan submenu from earlier plugin versions when found.

## 0.1.10
- Renames the plugin to G-CHAM Flexible Scan in metadata, menus and dialogs.
- Adds the action directly to the Plugins menu without a GISPHN analysis submenu.
- Renames output layer labels to match the plugin name.

## 0.1.9
- Fixes QGIS 4 renderer-range argument order that prevented saved result layers from being added to the project.
- Adds valid GeoPackage layers before applying styles, so a styling failure does not block layer loading.

## 0.1.8
- Raises the default candidate-zone limit from 200,000 to 2,000,000 and makes it configurable in the parameter dialog.
- Packs candidate memberships into a fixed-width array, removes duplicate generation without a global candidate set, and evaluates candidate statistics in NumPy batches.
- Applies a K-dependent memory guard and records the effective candidate limit in the progress log and output attributes.
- Adds the result GeoPackage path to the parameter dialog and preserves both output layers in the selected file.

## 0.1.7
- Added a progress dialog and persistent log for input validation, adjacency creation, candidate search, Monte Carlo simulation and output writing.
- Saves the full regional result and the most likely cluster as separate GeoPackage layers.
- Writes to a temporary GeoPackage and moves it to the selected path only after both layers are written.
- Adds the saved layers to the current project after successful file creation.

## 0.1.6
- Replaced the unavailable `QgsGeometry.boundary()` call with `QgsGeometry.touches()` for Queen-contiguity detection in QGIS 4.

## 0.1.5
- Fixed coordinate selector references in the analysis action (`dlg.x_field` / `dlg.y_field`).

## 0.1.4
- Restricts candidate zones for each seed to the seed plus its K-1 nearest centroid regions, following the 2005 paper's search definition.
- Defaults maximum cluster size to 15 and Monte Carlo replications to 999, matching the `rflexscan` defaults and the paper's simulation count.
- Allows optional X/Y centroid fields, so supplied population-centroid coordinates can be used instead of geometric centroids.
- Reports cluster relative risk as the inside rate divided by the outside rate.
- Builds `QgsSpatialIndex` by adding features individually to avoid the QGIS 4 Python-list constructor error.
- Documents that numerical equivalence and research validity still require direct comparison with `rflexscan`/FleXScan.

## 0.1.3
- Added automatic Poisson expected counts from observed cases and population at risk.
- Added manual expected-count field option, explicit field placeholders and result expected_n field.
- Restricted observed/expected/population selectors to numeric fields and improved default field hints.

## 0.1.2
- Removed the unavailable `QGIS_VERSION_INT` import reported on QGIS 4.2.1.
- Selects Qt 5 `QVariant` field types or Qt 6 `QMetaType` field types by capability.

## 0.1.1
- Fixed Qt 6 scoped button enums used by QGIS 4.
- Added Qt 5/Qt 6 compatibility for dialog execution, QAction imports and field types.
- Optimized feature ID lookup while building polygon adjacency.

## 0.1.0
- Initial experimental plugin scaffold.
- Python-only graph-based scan prototype with Poisson likelihood ratio and multinomial Monte Carlo.
- QGIS dialog, background task, input validation and result layer.
