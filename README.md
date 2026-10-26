# 修士論文の実験コード

## 難易度のカウンターバランス表

[`counterbalance.csv`](counterbalance.csv) は、実験協力者10人分のIDと、N-back・暗算・PVSATの難易度の実施順序を固定した割り当て表です。1人につきN-backを4回、暗算とPVSATを各3回実施し、それぞれの難易度を1回ずつ経験します。

### 列と難易度番号

| 列 | 内容 |
| --- | --- |
| `participant_id` | 実験協力者のID。重複しない英大文字3文字 |
| `nback_1` ～ `nback_4` | N-backの1回目～4回目に実施する難易度番号（1～4） |
| `arithmetic_1` ～ `arithmetic_3` | 暗算の1回目～3回目に実施する難易度番号（1～3） |
| `pvsat_1` ～ `pvsat_3` | PVSATの1回目～3回目に実施する難易度番号（1～3） |

実験回数は課題ごとに独立して1から数えます。例えば、N-backを4回終えた後でも、初めて行う暗算は `arithmetic_1` を参照します。1回は1つの難易度で行う実験を意味し、課題中の個々の試行数とは異なります。

CSVの値は難易度を区別する番号です。**N-backの番号は実際のN値ではありません。** N値、暗算の計算条件、PVSATの提示間隔などとの対応は、各課題プログラムで別途定義します。

### 割り当て方法と均等化の範囲

10人では提示位置を完全には均等にできないため、提示位置の均等さを優先し、その制約内で隣接する難易度の移行回数の偏りを小さくしています。

| 課題 | 順序別の人数 | 各提示位置での各難易度の人数 | 各移行の出現回数 |
| --- | --- | --- | --- |
| N-back | `1→2→4→3`・`3→4→2→1`を各3人、`2→3→1→4`・`4→1→3→2`を各2人 | 2～3人 | 2～3回 |
| 暗算 | 全6順序を各1人に割り当て、`1→3→2`・`2→1→3`・`3→1→2`・`3→2→1`を各1人追加 | 3～4人 | 2～4回 |
| PVSAT | 暗算と同じ順序別人数で、IDへの割り当てを別に行う | 3～4人 | 2～4回 |

「移行」は、同じ協力者の同じ課題内で隣接する難易度の組み合わせを方向付きで数えたものです。例えば `1→2` と `2→1` は別に数えます。N-backでは12種類、暗算・PVSATでは各6種類の移行を対象にしています。

N-backの基本4順序は、提示位置と直前条件の両方を均等化する [Williamsデザイン](https://search.r-project.org/CRAN/refmans/crossdes/help/williams.html) に基づきます。10人分では完全な均等化にはならず、順序効果の除去を保証するものではありません。3課題自体の実施順序や、課題をまたぐ影響のカウンターバランスはこの表では扱いません。

再現手順は次のとおりです。

1. IDを `AFX, BKR, CMT, DNV, EPQ, FRS, GLT, HUW, JXZ, KYV` の順に並べます。
2. 各課題について、上表の人数分の順序リストを作り、難易度番号のタプルとして辞書順に並べます。
3. Python標準ライブラリで単一の `random.Random(20261026)` を作成します。
4. 同じ乱数生成器でN-back、暗算、PVSATの各リストに順に `shuffle` を1回ずつ適用し、各リストをID順に割り当てます。

実験では保存済みのCSVを参照し、起動のたびに順序を生成し直さないでください。

### 将来の自動実行プログラムでの読み取り例

CSVはUTF-8 BOM付きです。Python 3.8の標準ライブラリのみで読み取れます。課題の種類は起動する課題プログラム側で決め、協力者IDとその課題の実験回数を入力する想定です。

```python
import csv
from pathlib import Path


def get_difficulty(participant_id, task, session):
    task_sessions = {"nback": 4, "arithmetic": 3, "pvsat": 3}
    if task not in task_sessions:
        raise ValueError("不明な課題です: {}".format(task))
    if type(session) is not int or not 1 <= session <= task_sessions[task]:
        raise ValueError("実験回数が課題の範囲外です")

    csv_path = Path(__file__).resolve().parent / "counterbalance.csv"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    matches = [row for row in rows if row["participant_id"] == participant_id]
    if len(matches) != 1:
        raise ValueError("IDが未登録、または重複しています: {}".format(participant_id))
    return int(matches[0]["{}_{}".format(task, session)])


# AFXの暗算2回目は、難易度番号2。
difficulty = get_difficulty("AFX", "arithmetic", 2)
```

上記はCSV参照方法の例です。既存の課題コードへの接続や、難易度番号から実際の課題設定への変換は今後実装します。追加ライブラリは不要です。
