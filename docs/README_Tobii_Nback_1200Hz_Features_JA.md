# Tobii Pro Spectrum 1200 Hz：機械学習・深層学習用の視線時系列特徴量

**対象環境**：PsychoPy 2022.2.4（Coder）、Tobii Pro Spectrum、1200 Hz。実験後に計算するスクリプトです。Python 3.8以降の標準ライブラリのみを使用し、PsychoPyやTobii SDKへの接続は不要です。

## 1. ファイル

- `tools/tobii_nback_1200hz_features.py`：解析本体
- `tests/`：保存先の検索・試行時刻ラベルなどの自動テスト

本体は以前の軽量版 `psychopy2022_spectrum1200_nback_lightweight.py` が生成する `*_gaze.csv` の**全行を保持**し、1200 Hz相当の時系列特徴量を右側に追加します。サンプリング周波数を補間で増やすことはありません。元ファイルは編集しません。

## 2. 使い方

1. プロジェクト内の `tools/tobii_nback_1200hz_features.py` をPsychoPy Coderで開きます。
2. 新しい実験結果は `data/nback/<ID>/session_<回数>/<日時>/` に保存されます。従来の `nback_data/` もそのまま使用できます。
3. 「Run」を押します。`RUN_PREFIX = ""` なら `data/nback/` 配下を再帰検索して**最新の** `*_gaze.csv` を選択します。新形式の結果がない場合だけ `nback_data/` を検索します。パスはスクリプトの場所を基準に解決します。
4. 特定の実験を解析する場合は冒頭の `RUN_PREFIX` にファイル名のプレフィックス、`DATA_DIR` からの相対パス、または絶対パスを指定します（`_gaze.csv` は省略可能）。ファイル名だけの指定は配下を再帰検索し、複数一致時はフルパスの指定を求めます。暗算を含む任意の視線CSVは `--gaze` にパスを渡しても選択できます。
5. 出力された `*_features_1200Hz.csv` と `*_features_quality.json` を確認します。

あるいは別のPython環境から実行する場合：

```bash
python tools/tobii_nback_1200hz_features.py --gaze "data/nback/AFX/session_01/<日時>/<プレフィックス>_gaze.csv"
```

### 期待する入力

```text
実験フォルダー/
  psychopy2022_spectrum1200_nback_lightweight.py
  tools/
    tobii_nback_1200hz_features.py
  data/nback/AFX/session_01/<日時>/
    <プレフィックス>_gaze.csv                  # 必須
    <プレフィックス>_eye_openness.jsonl        # 開眼度を保存していれば統合
    <プレフィックス>_metadata.json             # 画面の3D幾何情報
    <プレフィックス>_trials.csv                # 試行時刻（任意）
    <プレフィックス>_events.csv                # 課題終了時刻（任意）
```

`eye_openness.jsonl` が存在しなければ開眼度に関する列は空欄になります。**後処理によって未計測の開眼度を復元することはできません。** 現在の課題では `RECORD_EYE_OPENNESS = True` が初期設定です。変更した場合は実行環境で1200 Hz収録の安定性を確認してください。眼球画像ファイルはこの特徴量スクリプトでは使用しません。

解析結果は入力した視線CSVと同じフォルダーへ保存します。`--output` で出力CSVのパスを指定することもできます。新しい保存先・従来の保存先の両方を明示的な `--gaze` 指定で読み込めます。

## 3. 各時点（同一サンプル）で計算する特徴量

以下は各視線サンプルに1行ずつ対応します。左右眼の有効性フラグがFalseの値は**算出値では欠損**にします。生の入力列自体は保持します。

| 分類 | 代表的な出力列 | 説明・単位 |
|---|---|---|
| 瞳孔径 | `left_pupil_mm_clean`, `right_pupil_mm_clean` | 有効性チェック済み左右瞳孔径、mm |
| 平均瞳孔径 | `pupil_mean_mm` | 有効な眼の平均、mm |
| 瞳孔径左右差 | `pupil_left_minus_right_mm` | 左－右、mm（両眼有効時のみ） |
| 瞳孔径ベースライン差 | `pupil_left_baseline_delta_mm` など3列 | 基準値との差、mm |
| 開眼度 | `left_openness_mm`, `right_openness_mm` | 有効性チェック済み左右開眼度、mm |
| 開眼度平均・左右差 | `openness_mean_mm`, `openness_left_minus_right_mm` | mm |
| 視線方向角 | `left_gaze_yaw_deg`, `left_gaze_pitch_deg`, `left_gaze_forward_angle_deg` など | 左眼・右眼・両眼平均方向（°）。UCSの−Z軸を「前方」と定義。水平右＋、垂直上＋ |
| 刺激中心からの距離 | `left_gaze_to_stim_center_deg`, `right_gaze_to_stim_center_deg`, `gaze_to_stim_center_deg` | 各眼の3D視線ベクトルと各眼→刺激中心ベクトルのなす角（°） |
| 画面中心からの距離（正規化） | `left_screen_center_offset_norm` など | 画面の幅・高さそれぞれが1の座標系での距離。**角度ではない** |
| 左右眼の視線座標差 | `left_right_gaze_dx_norm`, `left_right_gaze_dy_norm`, `left_right_gaze_distance_norm`, `left_right_gaze_distance_deg` | 左右の画面上の視線点の差。`_deg`は両眼中点から左右視線点を見た角距離 |
| 輻輳角 | `vergence_angle_deg` | 左右の独立した3D視線方向ベクトルのなす角（°） |
| 眼球位置 | `left_eye_position_x_mm`～`right_eye_position_z_mm` | 左右の3D gaze origin（mm）。元のCSVにも含まれる情報の別名 |
| 両眼間距離 | `interocular_distance_mm` | 2つのgaze origin間のユークリッド距離（mm） |

**注意1**：刺激中心は正規化画面座標 `(0.5, 0.5)` としています。中央に文字を提示する以前のN-back実験用の設定です。異なる刺激位置の実験ではコード先頭の `SCREEN_CENTER_X_NORM` と `SCREEN_CENTER_Y_NORM` を変更するか、刺激位置を試行別に拡張する必要があります。

**注意2**：角度で表した「刺激中心からの距離」には、`*_metadata.json` に `tracker.display_area.top_left/top_right/bottom_left` の3D座標が必要です。以前の軽量版はメタデータへの記録を試みます。存在しない場合は勝手に視距離を仮定せず、該当列を空欄にします。

**注意3**：瞳孔径ベースライン差の**初期設定は各眼の最初の有効サンプルとの差**です。これは刺激提示前ベースラインやタスク前安静ベースラインを意味しません。事前測定した固定値がある場合は `PUPIL_BASELINE_LEFT_MM` と `PUPIL_BASELINE_RIGHT_MM` にそれぞれmmで設定してください。

## 4. 隣接2〜3サンプルで計算する特徴量

微分に使用する時間差は1200分の1秒と固定せず、Tobiiの`device_time_stamp_us` の実差を使います。差分値は、原則として後側のサンプル時刻に記録します。

| 分類 | 出力列 | 単位 |
|---|---|---|
| サンプル間視線移動角 | `gaze_sample_displacement_deg` | ° |
| 視線角速度 | `gaze_angular_speed_deg_s` | °/s、正の速度の大きさ |
| 視線角速度の変化率 | `gaze_angular_acceleration_deg_s2` | °/s²、**速度の大きさの差分**（符号付き）、3時点必要 |
| 水平・垂直視線速度 | `gaze_horizontal_velocity_deg_s`, `gaze_vertical_velocity_deg_s` | °/s、符号付き |
| 瞳孔径変化速度 | `left_pupil_velocity_mm_s`, `right_pupil_velocity_mm_s`, `pupil_mean_velocity_mm_s` | mm/s |
| 開眼度変化速度 | `left_openness_velocity_mm_s`, `right_openness_velocity_mm_s`, `openness_mean_velocity_mm_s` | mm/s |
| 瞳孔径変化加速度 | `left_pupil_acceleration_mm_s2`, `right_pupil_acceleration_mm_s2`, `pupil_mean_acceleration_mm_s2` | mm/s²、3時点必要 |
| 開眼度変化加速度 | `left_openness_acceleration_mm_s2`, `right_openness_acceleration_mm_s2`, `openness_mean_acceleration_mm_s2` | mm/s²、3時点必要 |
| 輻輳角変化速度 | `vergence_velocity_deg_s` | °/s |

`gaze_angular_speed_deg_s` は有効な左右3D視線単位ベクトルの平均方向（片眼のみ有効な場合はその眼）から計算します。微小角の数値誤差を抑えるため `atan2(|a×b|, a·b)` を使います。加速度は「視線速度の大きさ」の1階微分であり、3D角加速度ベクトルそのものではありません。

初回、必要な直前サンプルが欠損している場合、タイムスタンプが戻る／重複する場合、間隔が3 msを超えた場合には、**差分は空欄**にします。`sample_dt_ms`と`derivative_dt_usable`（0/1）を併せて保存します。データの補間、平滑化、ダウンサンプリングはしません。

## 5. 出力・品質管理

- `*_features_1200Hz.csv`：**入力視線CSVと同じ行数とタイムスタンプ**。元の全列＋追加特徴量＋任意の試行時刻列。欠損は空欄（pandasで読み込むとNaN）。
- `*_features_quality.json`：処理した視線行数、開眼度が一致した行数／比率、3Dディスプレイ幾何情報の有無、長いサンプル間隔の回数、欠損率、基準瞳孔径など。

試行情報 `*_trials.csv` があれば `trial_index`（試行番号）、`trial_phase`（`stimulus` / `isi`）、`time_since_trial_on_ms` を追加します。試行の正誤や反応時間は**モデルの入力特徴量として混ぜません**。ラベルとして使用するときは、別の `*_trials.csv` と紐付けてください。データの取得時刻にはPC側の `system_time_stamp_us` を用います。

6ブロック形式では `response_window_end_us` を試行区間の終了に使います。30秒休憩と休憩中の同期カウントダウンの視線行は保持しますが、`trial_index`・`trial_phase`・`time_since_trial_on_ms` は空欄です。従来CSVでは次試行の開始、刺激終了＋ISI、対応する課題終了のうち最も早い時刻で試行を閉じます。各ブロックの所属は試行CSVの `block` とセット内で一意な `trial` を使って紐付けてください。

機械学習入力の例：

```python
import pandas as pd

df = pd.read_csv('P001_日時_features_1200Hz.csv')
X = df[['pupil_mean_mm', 'gaze_angular_speed_deg_s',
        'gaze_to_stim_center_deg', 'vergence_angle_deg',
        'openness_mean_mm']]
# XにNaNがある場合は、学習方針に合わせてマスク/補完する。
# 課題成績などの正解ラベルは別ファイルで管理する。
```

## 6. 機械学習・深層学習での注意

1. **1200 Hzの列＝1200 Hzの独立した生理情報、ではありません。** 特に瞳孔径の速度・加速度はノイズに弱いです。平滑化やフィルターを試す場合は、元の時系列との違いを検証します。
2. **データ漏洩に注意。** 同じ被験者・同じ連続収録をランダムな行単位で訓練とテストへ分割すると、強い時間的相関のため性能を過大評価しやすいです。原則として被験者単位、少なくともセッション単位で分割してください。
3. 標準化・欠損補完・特徴選択などのパラメーターは、**訓練データだけ**で推定し、テストデータに適用します。
4. 目の有効性フラグを残し、瞳孔・開眼度・注視点の無効値と、タイムスタンプの欠損をマスクしてください。片眼だけ有効な平均は1眼で定義しますが、`valid_*_eye_count`から区別できます。
5. 刺激が中央に固定されたN-backでは、刺激中心への視線角は有用ですが、視点角が小さいという理由だけで「高認知負荷」とは推定できません。
6. 3D眼球位置と輻輳角はTobiiの測定系に依存します。視距離、頭部位置、キャリブレーション誤差による変動に注意してください。

## 7. 単体テスト

```bash
python -B -m unittest tests.test_experiment_paths tests.test_nback_trial_labels -v
```

プロジェクトのルートで実行します。新旧の保存先からの検索、ID・課題・回数別の保存、再実行時の分離、作業ディレクトリに依存しない起動、休憩を前問のISIに含めない試行時刻ラベルなどを検証します。全テストは `python -B -m unittest discover -v` で実行できます。

## 8. 技術上の定義（参照）

- Tobii Pro SDK：座標系（ADCSとUCS） https://devtobiipro.azurewebsites.net/commonconcepts/coordinatesystems.html
- Tobii Pro SDK：視線原点、視線点、方向 https://devtobiipro.azurewebsites.net/commonconcepts/gaze.html
- Tobii Pro SDK：開眼度・視線ストリームのタイムスタンプ https://www.developer.tobiipro.com/commonconcepts/eyeopenness.html
- Tobii Pro SDK：ディスプレイ3D角位置 https://developer.tobiipro.com/tobii.research/python/reference/1.9.0.68-alpha-g3b66b496/classtobii__research_1_1DisplayArea.html

**実機の研究データでの検証はまだ行っていません。** 本実験データを用いて、出力の欠損率、角度のレンジ、サンプリング間隔、符号、異常値をチェックしてください。
