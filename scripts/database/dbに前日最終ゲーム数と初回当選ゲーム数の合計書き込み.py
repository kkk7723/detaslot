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


db_path = Path(
    site_config.DB_PATH
)


print(f"[INFO] 対象店舗: {args.site}")
print(f"[INFO] 使用DB: {db_path}")
print(f"[INFO] 対象テーブル: {TABLE_NAME}")


# =========================================================
# 対象カラム
# =========================================================

EXECUTION_DATE_COLUMN = "実行日"
MACHINE_NUMBER_COLUMN = "台番号"

FIRST_HIT_GAME_COLUMN = (
    "初回当選ゲーム数"
)

PREVIOUS_FINAL_GAME_COLUMN = (
    "前日最終ゲーム数"
)

TOTAL_COLUMN = (
    "前日最終ゲーム数と初回当選ゲーム数の合計"
)

REQUIRED_COLUMNS = [
    EXECUTION_DATE_COLUMN,
    MACHINE_NUMBER_COLUMN,
    FIRST_HIT_GAME_COLUMN,
    PREVIOUS_FINAL_GAME_COLUMN,
    TOTAL_COLUMN,
]


# =========================================================
# SQLite共通
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


def clean_integer(
    value: Any,
    *,
    default: int | None = None,
) -> int | None:
    """
    値を整数へ変換する。

    空欄・NaN・変換不能値の場合はdefaultを返す。
    """
    if value is None:
        return default

    try:
        if pd.isna(value):
            return default
    except (
        TypeError,
        ValueError,
    ):
        pass

    text = str(value).strip()

    if not text:
        return default

    text = (
        text
        .replace(",", "")
        .replace("Ｇ", "")
        .replace("G", "")
        .strip()
    )

    try:
        return int(
            float(text)
        )
    except (
        TypeError,
        ValueError,
    ):
        return default


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

    with sqlite3.connect(
        db_path
    ) as connection:
        table_columns = get_table_columns(
            connection,
            TABLE_NAME,
        )

    require_columns(
        table_columns,
        REQUIRED_COLUMNS,
    )

    print(
        f"[DB] 必須カラム確認完了: "
        f"{len(REQUIRED_COLUMNS)}列"
    )

    # -----------------------------------------------------
    # DB内の最新日を取得
    # -----------------------------------------------------

    latest_date_sql = f"""
        SELECT
            MAX(
                date(
                    {quote_identifier(EXECUTION_DATE_COLUMN)}
                )
            )
        FROM {quote_identifier(TABLE_NAME)}
        WHERE
            {quote_identifier(EXECUTION_DATE_COLUMN)}
            IS NOT NULL
            AND TRIM(
                CAST(
                    {quote_identifier(EXECUTION_DATE_COLUMN)}
                    AS TEXT
                )
            ) <> ''
    """

    with sqlite3.connect(
        db_path
    ) as connection:
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
        f"[DB] 最新日: "
        f"{latest_date}"
    )

    # -----------------------------------------------------
    # 最新日のデータだけ取得
    # -----------------------------------------------------

    select_sql = f"""
        SELECT
            ROWID AS _rowid,
            {quote_identifier(EXECUTION_DATE_COLUMN)}
                AS execution_datetime,
            {quote_identifier(MACHINE_NUMBER_COLUMN)}
                AS machine_number,
            {quote_identifier(FIRST_HIT_GAME_COLUMN)}
                AS first_hit_game,
            {quote_identifier(PREVIOUS_FINAL_GAME_COLUMN)}
                AS previous_final_game,
            {quote_identifier(TOTAL_COLUMN)}
                AS current_total
        FROM {quote_identifier(TABLE_NAME)}
        WHERE
            date(
                {quote_identifier(EXECUTION_DATE_COLUMN)}
            ) = ?
        ORDER BY ROWID ASC
    """

    with sqlite3.connect(
        db_path
    ) as connection:
        dataframe = pd.read_sql_query(
            select_sql,
            connection,
            params=[
                latest_date,
            ],
        )

    if dataframe.empty:
        print(
            f"[INFO] 最新日の対象データがありません: "
            f"{latest_date}"
        )
        return

    print(
        f"[DB] 対象レコード: "
        f"{len(dataframe)}件"
    )

    # -----------------------------------------------------
    # 合計値計算
    # -----------------------------------------------------

    dataframe[
        "first_hit_game_clean"
    ] = dataframe[
        "first_hit_game"
    ].map(
        lambda value: clean_integer(
            value,
            default=0,
        )
    )

    dataframe[
        "previous_final_game_clean"
    ] = dataframe[
        "previous_final_game"
    ].map(
        lambda value: clean_integer(
            value,
            default=0,
        )
    )

    dataframe[
        "calculated_total"
    ] = (
        dataframe[
            "first_hit_game_clean"
        ]
        + dataframe[
            "previous_final_game_clean"
        ]
    ).astype(int)

    print(
        f"[CALC] 合計計算完了: "
        f"{len(dataframe)}件"
    )

    # -----------------------------------------------------
    # 変更が必要な行だけ抽出
    # -----------------------------------------------------

    update_parameters: list[
        tuple[
            int,
            int,
        ]
    ] = []

    unchanged_count = 0

    for _, row in dataframe.iterrows():
        calculated_total = int(
            row["calculated_total"]
        )

        current_total = clean_integer(
            row["current_total"],
            default=None,
        )

        if (
            current_total is not None
            and current_total
            == calculated_total
        ):
            unchanged_count += 1
            continue

        update_parameters.append((
            calculated_total,
            int(row["_rowid"]),
        ))

        print(
            f"[UPDATE準備] "
            f"台番号={row['machine_number']}, "
            f"ROWID={row['_rowid']}, "
            f"初回={row['first_hit_game_clean']}, "
            f"前日最終="
            f"{row['previous_final_game_clean']}, "
            f"旧合計={current_total!r}, "
            f"新合計={calculated_total}"
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
            {quote_identifier(TOTAL_COLUMN)}
            = ?
        WHERE
            ROWID = ?
    """

    updated_count = 0

    if update_parameters:
        with sqlite3.connect(
            db_path
        ) as connection:
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
        f"✅ 合計更新完了: "
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




