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

GAME_COLUMNS = [
    f"ゲーム{i}回前"
    for i in range(1, 101)
]

SOURCE_COLUMNS = [
    "実行日",
    "台番号",
    *GAME_COLUMNS,
]

DESTINATION_COLUMN = (
    "初回当選ゲーム数"
)


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
    必須カラムの存在を確認する。
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


def clean_game_value(
    value: Any,
) -> int | None:
    """
    ゲーム数を整数へ変換する。

    空欄・NaN・変換不能値はNone。
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (
        TypeError,
        ValueError,
    ):
        pass

    text = str(value).strip()

    if not text:
        return None

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
        return None


def get_first_hit_game(
    row: pd.Series,
) -> int | None:
    """
    ゲーム100回前から1回前へ逆順に確認し、
    最後に入力されているゲーム値を返す。

    例:
        ゲーム1～4回前に値がある
        → ゲーム4回前を返す
    """
    for index in range(
        100,
        0,
        -1,
    ):
        column_name = (
            f"ゲーム{index}回前"
        )

        game_value = clean_game_value(
            row.get(column_name)
        )

        if game_value is not None:
            return game_value

    return None


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
        [
            *SOURCE_COLUMNS,
            DESTINATION_COLUMN,
        ],
    )

    print(
        f"[DB] 必須カラム確認完了: "
        f"{len(SOURCE_COLUMNS) + 1}列"
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
            {quote_identifier("実行日")}
            IS NOT NULL
            AND TRIM(
                CAST(
                    {quote_identifier("実行日")}
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

    select_columns = ", ".join(
        quote_identifier(column)
        for column in SOURCE_COLUMNS
    )

    select_sql = f"""
        SELECT
            ROWID AS _rowid,
            {select_columns},
            {quote_identifier(DESTINATION_COLUMN)}
                AS current_value
        FROM {quote_identifier(TABLE_NAME)}
        WHERE
            date(
                {quote_identifier("実行日")}
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
            f"[INFO] 最新日の対象データなし: "
            f"{latest_date}"
        )
        return

    print(
        f"[DB] 対象レコード: "
        f"{len(dataframe)}件"
    )

    # -----------------------------------------------------
    # 初回当選ゲーム数を計算
    # -----------------------------------------------------

    dataframe[
        "初回当選ゲーム数_計算値"
    ] = dataframe.apply(
        get_first_hit_game,
        axis=1,
    )

    calculated_count = int(
        dataframe[
            "初回当選ゲーム数_計算値"
        ]
        .notna()
        .sum()
    )

    empty_count = (
        len(dataframe)
        - calculated_count
    )

    print(
        f"[CALC] 計算成功: "
        f"{calculated_count}件"
    )
    print(
        f"[CALC] 履歴ゲームなし: "
        f"{empty_count}件"
    )

    # -----------------------------------------------------
    # 変更が必要な行だけ抽出
    # -----------------------------------------------------

    update_parameters: list[
        tuple[
            int | None,
            int,
        ]
    ] = []

    unchanged_count = 0

    for _, row in dataframe.iterrows():
        new_value = row[
            "初回当選ゲーム数_計算値"
        ]

        current_value = row[
            "current_value"
        ]

        new_clean = clean_game_value(
            new_value
        )

        current_clean = clean_game_value(
            current_value
        )

        if new_clean == current_clean:
            unchanged_count += 1
            continue

        update_parameters.append((
            new_clean,
            int(row["_rowid"]),
        ))

        print(
            f"[UPDATE準備] "
            f"台番号={row['台番号']}, "
            f"ROWID={row['_rowid']}, "
            f"旧値={current_clean!r}, "
            f"新値={new_clean!r}"
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
            {quote_identifier(DESTINATION_COLUMN)}
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

    print(
        f"✅ DB更新完了: "
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




