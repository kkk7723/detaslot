#!/usr/bin/env python
# coding: utf-8

# In[1]:


from __future__ import annotations

import argparse
import importlib
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd


# =========================================================
# プロジェクトルート設定
# =========================================================

def find_project_root(
    start_path: Path,
) -> Path:
    """
    config/ と utils/ が存在するディレクトリを
    detaslotのプロジェクトルートとして返す。
    """
    current = start_path.resolve()

    if current.is_file():
        current = current.parent

    for candidate in [
        current,
        *current.parents,
    ]:
        if (
            (candidate / "config").is_dir()
            and (candidate / "utils").is_dir()
        ):
            return candidate

    raise RuntimeError(
        "detaslotのプロジェクトルートを"
        "特定できませんでした。"
        f" 開始位置: {start_path}"
    )


if "__file__" in globals():
    # scripts/database/*.py から実行
    PROJECT_ROOT = find_project_root(
        Path(__file__)
    )
else:
    # scripts/database/*.ipynb から実行
    PROJECT_ROOT = find_project_root(
        Path.cwd()
    )


if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )


print(
    f"[INFO] PROJECT_ROOT: "
    f"{PROJECT_ROOT}"
)
print(
    f"[INFO] config存在: "
    f"{(PROJECT_ROOT / 'config').is_dir()}"
)
print(
    f"[INFO] utils存在: "
    f"{(PROJECT_ROOT / 'utils').is_dir()}"
)


# =========================================================
# 共通設定
# =========================================================

from config.common import (
    DEFAULT_SITE,
    TABLE_NAME,
    require_file,
)


# ==================================================
# 店舗選択
# ==================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--site",
        default=DEFAULT_SITE,
        help="config名",
    )

    return parser.parse_args()


if "__file__" in globals():
    # .py実行時
    # --site指定があればそれを使用し、
    # 指定がなければDEFAULT_SITEを使用
    args = parse_args()
else:
    # Notebook実行時
    args = argparse.Namespace(
        site=DEFAULT_SITE,
    )


config_file = (
    PROJECT_ROOT
    / "config"
    / f"{args.site}.py"
)

if not config_file.is_file():
    raise FileNotFoundError(
        f"店舗設定が見つかりません: {config_file}"
    )



try:
    site_config = importlib.import_module(
        f"config.{args.site}"
    )
except ModuleNotFoundError as exc:
    raise SystemExit(
        f"[ERROR] 店舗設定が見つかりません: "
        f"config/{args.site}.py"
    ) from exc


if not hasattr(site_config, "DB_PATH"):
    raise AttributeError(
        f"config/{args.site}.py に "
        "DB_PATH が設定されていません。"
    )


db_path = Path(site_config.DB_PATH)

print(f"[INFO] 対象店舗: {args.site}")
print(f"[INFO] 使用DB: {db_path}")
print(f"[INFO] 対象テーブル: {TABLE_NAME}")


# =========================================================
# 固定設定
# =========================================================

STATUS_COLUMNS = [
    f"ステータス{i}回前"
    for i in range(1, 101)
]

CARRYOVER_SOURCE_COLUMNS = [
    "実行日",
    "台番号",
    "BIG",
    "REG",
    "BONUS",
    "ATART",
    "最終ゲーム",
]


# =========================================================
# 共通関数
# =========================================================

def quote_identifier(
    identifier: str,
) -> str:
    """
    SQLiteのテーブル名・カラム名を
    []で安全に囲む。
    """
    return (
        "["
        + str(identifier).replace(
            "]",
            "]]",
        )
        + "]"
    )


def get_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    """
    SQLiteテーブルに存在するカラム名を取得する。
    """
    cursor = connection.execute(
        f"PRAGMA table_info("
        f"{quote_identifier(table_name)}"
        f")"
    )

    return {
        str(row[1])
        for row in cursor.fetchall()
    }


def require_columns(
    existing_columns: set[str],
    required_columns: list[str],
    *,
    label: str,
) -> None:
    """
    必須カラムがDBに存在するか確認する。
    """
    missing_columns = [
        column
        for column in required_columns
        if column not in existing_columns
    ]

    if missing_columns:
        raise ValueError(
            f"[ERROR] {label}に必要なDBカラムが"
            f"存在しません: "
            f"{', '.join(missing_columns)}"
        )


def is_empty_value(
    value: Any,
) -> bool:
    """
    None、NaN、空文字を空欄として判定する。
    """
    if value is None:
        return True

    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass

    return str(value).strip() == ""


def normalize_status_value(
    value: Any,
) -> str:
    """
    ステータス照合用に文字列化して前後空白を除去する。
    """
    if is_empty_value(value):
        return ""

    return str(value).strip()


def count_atart_history(
    row: pd.Series,
    status_columns: list[str],
) -> int:
    """
    ステータス1～100回前のうち、
    完全一致で AT/ART の件数を数える。
    """
    return sum(
        normalize_status_value(
            row.get(column)
        ) == "AT/ART"
        for column in status_columns
    )


def calc_carryover_game_sum(
    machine_dataframe: pd.DataFrame,
) -> int:
    """
    1台分の履歴を新しい順に確認し、
    BIG・REG・BONUS・ATARTのいずれかが
    1以上となる最初のレコードまで、
    最終ゲームを合計する。

    当たりがある行自体も合計に含める。
    """
    working = (
        machine_dataframe
        .sort_values(
            ["実行日", "_rowid"],
            ascending=[
                False,
                False,
            ],
        )
        .reset_index(drop=True)
        .copy()
    )

    numeric_columns = [
        "BIG",
        "REG",
        "BONUS",
        "ATART",
        "最終ゲーム",
    ]

    for column in numeric_columns:
        working[column] = pd.to_numeric(
            working[column],
            errors="coerce",
        ).fillna(0)

    hit_mask = (
        (working["BIG"] >= 1)
        | (working["REG"] >= 1)
        | (working["BONUS"] >= 1)
        | (working["ATART"] >= 1)
    )

    hit_positions = working.index[
        hit_mask
    ].tolist()

    if hit_positions:
        first_hit_position = hit_positions[0]

        target_rows = working.iloc[
            : first_hit_position + 1
        ]
    else:
        target_rows = working

    return int(
        target_rows[
            "最終ゲーム"
        ].sum()
    )


# =========================================================
# ATART補完
# =========================================================

def update_missing_atart(
    connection: sqlite3.Connection,
    table_columns: set[str],
) -> tuple[int, Any | None]:
    """
    DB内の最新日について、ATARTが空欄の行だけを対象に、
    ステータス1～100回前の AT/ART 完全一致数を保存する。

    ROWID単位で更新するため、
    同じ台番号・同じ日の別レコードは巻き込まない。
    """
    required_columns = [
        "実行日",
        "台番号",
        "ATART",
        *STATUS_COLUMNS,
    ]

    require_columns(
        table_columns,
        required_columns,
        label="ATART補完",
    )

    select_columns = ", ".join(
        quote_identifier(column)
        for column in required_columns
    )

    sql = f"""
        SELECT
            ROWID AS _rowid,
            {select_columns}
        FROM {quote_identifier(TABLE_NAME)}
        ORDER BY
            datetime(
                {quote_identifier("実行日")}
            ) DESC,
            ROWID DESC
    """

    dataframe = pd.read_sql_query(
        sql,
        connection,
    )

    dataframe["実行日"] = pd.to_datetime(
        dataframe["実行日"],
        errors="coerce",
    )

    dataframe = dataframe.dropna(
        subset=["実行日"],
    ).copy()

    if dataframe.empty:
        print(
            "[ATART] 有効な実行日データなし。"
            "補完をスキップします。"
        )
        return 0, None

    latest_date = (
        dataframe["実行日"]
        .dt.date
        .max()
    )

    target_mask = (
        dataframe["実行日"].dt.date
        == latest_date
    ) & dataframe["ATART"].apply(
        is_empty_value
    )

    target_dataframe = dataframe.loc[
        target_mask
    ].copy()

    print(
        f"[ATART] 最新日: "
        f"{latest_date}"
    )
    print(
        f"[ATART] 補完対象: "
        f"{len(target_dataframe)}件"
    )

    if target_dataframe.empty:
        print(
            "[ATART] 補完対象なし"
        )
        return 0, latest_date

    updates: list[
        tuple[int, int]
    ] = []

    for _, row in target_dataframe.iterrows():
        atart_count = count_atart_history(
            row,
            STATUS_COLUMNS,
        )

        updates.append((
            atart_count,
            int(row["_rowid"]),
        ))

        print(
            f"[ATART] 台番号="
            f"{row['台番号']}, "
            f"ROWID={row['_rowid']}, "
            f"ATART={atart_count}"
        )

    update_sql = f"""
        UPDATE {quote_identifier(TABLE_NAME)}
        SET
            {quote_identifier("ATART")} = ?
        WHERE
            ROWID = ?
    """

    connection.executemany(
        update_sql,
        updates,
    )

    connection.commit()

    print(
        f"[ATART] 補完完了: "
        f"{len(updates)}件"
    )

    return len(updates), latest_date


# =========================================================
# 宵越し累計ゲーム数更新
# =========================================================

def update_carryover_game_sum(
    connection: sqlite3.Connection,
    table_columns: set[str],
) -> tuple[int, Any | None]:
    """
    台番号ごとに宵越し累計ゲーム数を計算し、
    DB内の最新日レコードへ書き込む。

    最新日に同じ台番号のレコードが複数あっても、
    各ROWIDを個別に更新する。
    """
    required_columns = [
        *CARRYOVER_SOURCE_COLUMNS,
        "宵越し累計ゲーム数",
    ]

    require_columns(
        table_columns,
        required_columns,
        label="宵越し累計ゲーム数更新",
    )

    select_columns = ", ".join(
        quote_identifier(column)
        for column in CARRYOVER_SOURCE_COLUMNS
    )

    sql = f"""
        SELECT
            ROWID AS _rowid,
            {select_columns}
        FROM {quote_identifier(TABLE_NAME)}
        ORDER BY
            {quote_identifier("台番号")} ASC,
            datetime(
                {quote_identifier("実行日")}
            ) DESC,
            ROWID DESC
    """

    dataframe = pd.read_sql_query(
        sql,
        connection,
    )

    dataframe["実行日"] = pd.to_datetime(
        dataframe["実行日"],
        errors="coerce",
    )

    dataframe = dataframe.dropna(
        subset=[
            "実行日",
            "台番号",
        ],
    ).copy()

    dataframe["台番号"] = (
        dataframe["台番号"]
        .astype(str)
        .str.strip()
    )

    dataframe = dataframe[
        dataframe["台番号"] != ""
    ].copy()

    if dataframe.empty:
        print(
            "[CARRYOVER] 計算対象データなし"
        )
        return 0, None

    latest_date = (
        dataframe["実行日"]
        .dt.date
        .max()
    )

    print(
        f"[CARRYOVER] 最新日: "
        f"{latest_date}"
    )

    # ATART補完後の値をDBから読み直しているため、
    # 補完値も宵越し計算へ反映される。
    machine_sum_map: dict[str, int] = {}

    for (
        machine_number,
        machine_group,
    ) in dataframe.groupby(
        "台番号",
        sort=False,
    ):
        carryover_sum = (
            calc_carryover_game_sum(
                machine_group
            )
        )

        machine_sum_map[
            machine_number
        ] = carryover_sum

        print(
            f"[CARRYOVER] 台番号="
            f"{machine_number}, "
            f"累計={carryover_sum}"
        )

    latest_dataframe = dataframe[
        dataframe["実行日"].dt.date
        == latest_date
    ].copy()

    updates: list[
        tuple[int, int]
    ] = []

    for _, row in latest_dataframe.iterrows():
        machine_number = str(
            row["台番号"]
        ).strip()

        carryover_sum = (
            machine_sum_map.get(
                machine_number,
                0,
            )
        )

        updates.append((
            int(carryover_sum),
            int(row["_rowid"]),
        ))

    update_sql = f"""
        UPDATE {quote_identifier(TABLE_NAME)}
        SET
            {quote_identifier("宵越し累計ゲーム数")} = ?
        WHERE
            ROWID = ?
    """

    connection.executemany(
        update_sql,
        updates,
    )

    connection.commit()

    print(
        f"[CARRYOVER] 更新完了: "
        f"{len(updates)}レコード"
    )

    return len(updates), latest_date


# =========================================================
# メイン処理
# =========================================================

def main() -> None:
    start_time = time.time()

    require_file(
        db_path,
        "店舗別SQLiteデータベース",
    )

    with sqlite3.connect(
        db_path
    ) as connection:
        table_columns = get_table_columns(
            connection,
            TABLE_NAME,
        )

        print(
            f"[DB] カラム数: "
            f"{len(table_columns)}"
        )

        atart_count, atart_date = (
            update_missing_atart(
                connection,
                table_columns,
            )
        )

        print()

        carryover_count, carryover_date = (
            update_carryover_game_sum(
                connection,
                table_columns,
            )
        )

    print()
    print(
        f"[RESULT] ATART補完: "
        f"{atart_count}件"
    )
    print(
        f"[RESULT] ATART対象日: "
        f"{atart_date}"
    )
    print(
        f"[RESULT] 宵越し更新: "
        f"{carryover_count}件"
    )
    print(
        f"[RESULT] 宵越し対象日: "
        f"{carryover_date}"
    )
    print(
        f"[INFO] 所要時間: "
        f"{time.time() - start_time:.1f}秒"
    )


# =========================================================
# 実行
# =========================================================

if __name__ == "__main__":
    main()


# In[ ]:




