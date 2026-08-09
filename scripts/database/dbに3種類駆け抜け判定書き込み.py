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

def find_project_root(start_path: Path) -> Path:
    """
    config/ と utils/ が存在するディレクトリを
    detaslotのプロジェクトルートとして返す。
    """
    current = start_path.resolve()

    if current.is_file():
        current = current.parent

    for candidate in [current, *current.parents]:
        if (
            (candidate / "config").is_dir()
            and (candidate / "utils").is_dir()
        ):
            return candidate

    raise RuntimeError(
        "detaslotのプロジェクトルートを特定できません。"
        f" 開始位置: {start_path}"
    )


if "__file__" in globals():
    # scripts/database/*.py から実行
    PROJECT_ROOT = find_project_root(Path(__file__))
else:
    # scripts/database/*.ipynb から実行
    PROJECT_ROOT = find_project_root(Path.cwd())


if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


print(f"[INFO] PROJECT_ROOT: {PROJECT_ROOT}")
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
# 対象カラム
# =========================================================

SOURCE_COLUMNS = [
    "実行日",
    "台番号",
    "宵越し特賞履歴ステータス1回前",
    "宵越し特賞履歴ステータス2回前",
]

DESTINATION_COLUMNS = [
    "BIG駆け抜け判定",
    "REG駆け抜け判定",
    "ATART駆け抜け判定",
]


# =========================================================
# SQLite共通
# =========================================================

def quote_identifier(identifier: str) -> str:
    """
    SQLiteのテーブル名・カラム名を
    []で安全に囲む。
    """
    return (
        "["
        + str(identifier).replace("]", "]]")
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
        raise RuntimeError(
            f"{TABLE_NAME} に必要列がありません: "
            f"{missing_columns}"
        )


# =========================================================
# 値の正規化
# =========================================================

def normalize_status(value: Any) -> str:
    """
    ステータスを完全一致比較用に整形する。

    - None、NaNは空文字
    - 前後空白を除去
    - 大文字化
    - 表記置換は行わない
    """
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    return str(value).strip().upper()


def clean_integer(
    value: Any,
) -> int | None:
    """
    DBの現在値を整数またはNoneへ変換する。
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = str(value).strip()

    if not text:
        return None

    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


# =========================================================
# 駆け抜け判定
# =========================================================

def judge_pair(
    row: pd.Series,
    *,
    previous_status: str,
    current_statuses: set[str],
) -> int | None:
    """
    直近ペアだけを使って駆け抜け判定する。

    時系列:
        2回前 -> 1回前

    条件:
        2回前がprevious_statusと完全一致し、
        1回前がcurrent_statusesのいずれかなら1。

    不一致の場合はNone。
    """
    previous_value = normalize_status(
        row.get(
            "宵越し特賞履歴ステータス2回前",
            "",
        )
    )

    current_value = normalize_status(
        row.get(
            "宵越し特賞履歴ステータス1回前",
            "",
        )
    )

    normalized_previous = normalize_status(
        previous_status
    )

    normalized_current_statuses = {
        normalize_status(status)
        for status in current_statuses
    }

    if (
        previous_value == normalized_previous
        and current_value in normalized_current_statuses
    ):
        return 1

    return None


def calculate_run_through_values(
    row: pd.Series,
) -> dict[str, int | None]:
    """
    1レコード分の駆け抜け判定を計算する。
    """
    return {
        "BIG駆け抜け判定": judge_pair(
            row,
            previous_status="BIG",
            current_statuses={
                "REG",
                "AT/ART",
            },
        ),
        "REG駆け抜け判定": judge_pair(
            row,
            previous_status="REG",
            current_statuses={
                "BIG",
                "AT/ART",
            },
        ),
        "ATART駆け抜け判定": judge_pair(
            row,
            previous_status="AT/ART",
            current_statuses={
                "BIG",
                "REG",
            },
        ),
    }


# =========================================================
# メイン処理
# =========================================================

def main() -> None:
    start_time = time.time()

    require_file(
        db_path,
        "店舗別SQLiteデータベース",
    )

    # -----------------------------------------------------
    # DBカラム確認
    # -----------------------------------------------------

    with sqlite3.connect(db_path) as connection:
        table_columns = get_table_columns(
            connection,
            TABLE_NAME,
        )

    require_columns(
        table_columns,
        [
            *SOURCE_COLUMNS,
            *DESTINATION_COLUMNS,
        ],
    )

    print(
        f"[DB] 必須カラム確認完了: "
        f"{len(SOURCE_COLUMNS) + len(DESTINATION_COLUMNS)}列"
    )

    # -----------------------------------------------------
    # DB内の最新日を取得
    # -----------------------------------------------------

    latest_date_sql = f"""
        SELECT
            MAX(
                date(
                    {quote_identifier("実行日")}
                )
            )
        FROM {quote_identifier(TABLE_NAME)}
        WHERE
            {quote_identifier("実行日")} IS NOT NULL
            AND TRIM(
                CAST(
                    {quote_identifier("実行日")}
                    AS TEXT
                )
            ) <> ''
    """

    with sqlite3.connect(db_path) as connection:
        latest_date_row = connection.execute(
            latest_date_sql
        ).fetchone()

    latest_date = (
        latest_date_row[0]
        if latest_date_row
        else None
    )

    if not latest_date:
        print(
            "[INFO] 有効な実行日データがありません。"
        )
        return

    print(
        f"[DB] 最新実行日: "
        f"{latest_date}"
    )

    # -----------------------------------------------------
    # 最新日のデータだけ取得
    # -----------------------------------------------------

    source_select = ", ".join(
        quote_identifier(column)
        for column in SOURCE_COLUMNS
    )

    current_select = ", ".join(
        (
            f"{quote_identifier(column)} "
            f"AS {quote_identifier(f'current_{column}')}"
        )
        for column in DESTINATION_COLUMNS
    )

    select_sql = f"""
        SELECT
            ROWID AS _rowid,
            {source_select},
            {current_select}
        FROM {quote_identifier(TABLE_NAME)}
        WHERE
            date(
                {quote_identifier("実行日")}
            ) = ?
        ORDER BY ROWID ASC
    """

    with sqlite3.connect(db_path) as connection:
        dataframe = pd.read_sql_query(
            select_sql,
            connection,
            params=[latest_date],
        )

    if dataframe.empty:
        print(
            f"[INFO] 最新日の対象データなし: "
            f"{latest_date}"
        )
        return

    print(
        f"[DB] 最新日レコード数: "
        f"{len(dataframe)}件"
    )

    # -----------------------------------------------------
    # 判定計算
    # -----------------------------------------------------

    calculated_rows: list[
        dict[str, int | None]
    ] = []

    for _, row in dataframe.iterrows():
        calculated_rows.append(
            calculate_run_through_values(row)
        )

    calculated_dataframe = pd.DataFrame(
        calculated_rows,
        index=dataframe.index,
    )

    for column in DESTINATION_COLUMNS:
        dataframe[
            f"calculated_{column}"
        ] = calculated_dataframe[column]

    big_count = int(
        dataframe[
            "calculated_BIG駆け抜け判定"
        ]
        .notna()
        .sum()
    )

    reg_count = int(
        dataframe[
            "calculated_REG駆け抜け判定"
        ]
        .notna()
        .sum()
    )

    atart_count = int(
        dataframe[
            "calculated_ATART駆け抜け判定"
        ]
        .notna()
        .sum()
    )

    print(
        f"[CALC] BIG駆け抜け対象: "
        f"{big_count}件"
    )
    print(
        f"[CALC] REG駆け抜け対象: "
        f"{reg_count}件"
    )
    print(
        f"[CALC] ATART駆け抜け対象: "
        f"{atart_count}件"
    )

    # -----------------------------------------------------
    # 各レコードの判定内容を表示
    # -----------------------------------------------------

    for _, row in dataframe.iterrows():
        status_2 = normalize_status(
            row[
                "宵越し特賞履歴ステータス2回前"
            ]
        )

        status_1 = normalize_status(
            row[
                "宵越し特賞履歴ステータス1回前"
            ]
        )

        print(
            f"[CALC] 台番号={row['台番号']}, "
            f"ROWID={row['_rowid']}, "
            f"2回前={status_2!r}, "
            f"1回前={status_1!r}, "
            f"BIG={row['calculated_BIG駆け抜け判定']!r}, "
            f"REG={row['calculated_REG駆け抜け判定']!r}, "
            f"ATART="
            f"{row['calculated_ATART駆け抜け判定']!r}"
        )

    # -----------------------------------------------------
    # 変更が必要なレコードだけ抽出
    # -----------------------------------------------------

    update_parameters: list[
        tuple[
            int | None,
            int | None,
            int | None,
            int,
        ]
    ] = []

    unchanged_count = 0

    for _, row in dataframe.iterrows():
        calculated_values = [
            clean_integer(
                row[
                    f"calculated_{column}"
                ]
            )
            for column in DESTINATION_COLUMNS
        ]

        current_values = [
            clean_integer(
                row[
                    f"current_{column}"
                ]
            )
            for column in DESTINATION_COLUMNS
        ]

        if current_values == calculated_values:
            unchanged_count += 1
            continue

        update_parameters.append((
            calculated_values[0],
            calculated_values[1],
            calculated_values[2],
            int(row["_rowid"]),
        ))

        print(
            f"[UPDATE準備] "
            f"台番号={row['台番号']}, "
            f"ROWID={row['_rowid']}, "
            f"旧値={current_values}, "
            f"新値={calculated_values}"
        )

    print(
        f"[DB] 変更なし: "
        f"{unchanged_count}件"
    )
    print(
        f"[DB] 更新対象: "
        f"{len(update_parameters)}件"
    )

    # -----------------------------------------------------
    # ROWID単位で更新
    # -----------------------------------------------------

    update_sql = f"""
        UPDATE {quote_identifier(TABLE_NAME)}
        SET
            {quote_identifier("BIG駆け抜け判定")} = ?,
            {quote_identifier("REG駆け抜け判定")} = ?,
            {quote_identifier("ATART駆け抜け判定")} = ?
        WHERE
            ROWID = ?
    """

    updated_count = 0

    if update_parameters:
        with sqlite3.connect(db_path) as connection:
            cursor = connection.cursor()

            cursor.executemany(
                update_sql,
                update_parameters,
            )

            connection.commit()

            updated_count = len(
                update_parameters
            )

    # -----------------------------------------------------
    # 完了
    # -----------------------------------------------------

    print(
        f"✅ 駆け抜け判定 更新完了: "
        f"{updated_count}件"
    )
    print(
        f"[DB] 対象日: "
        f"{latest_date}"
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




