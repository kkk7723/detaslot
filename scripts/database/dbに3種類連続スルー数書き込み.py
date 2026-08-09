#!/usr/bin/env python
# coding: utf-8

# In[2]:


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
    # scripts/database/*.py から実行する場合
    PROJECT_ROOT = find_project_root(
        Path(__file__)
    )
else:
    # scripts/database/*.ipynb から実行する場合
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

MAX_BACK = 10

STATUS_COLUMNS = [
    f"ステータス{i}回前"
    for i in range(1, MAX_BACK + 1)
]

GAME_COLUMNS = [
    f"ゲーム{i}回前"
    for i in range(1, MAX_BACK + 1)
]

SOURCE_COLUMNS = [
    "実行日",
    "台番号",
    "最終ゲーム",
    *STATUS_COLUMNS,
    *GAME_COLUMNS,
]

DESTINATION_COLUMNS = [
    "BIGスルー数",
    "REGスルー数",
    "ATARTスルー数",
    "BIGスルー間ゲーム数",
    "REGスルー間ゲーム数",
    "ATARTスルー間ゲーム数",
    "BIGスルー間累計ゲーム数",
    "REGスルー間累計ゲーム数",
    "ATARTスルー間累計ゲーム数",
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


# =========================================================
# 値の正規化
# =========================================================

def normalize_status(
    value: Any,
) -> str:
    """
    ステータスを完全一致比較用に正規化する。

    処理:
    - None、NaNを空文字へ変換
    - 前後空白を除去
    - 大文字化

    表記揺れの置換は行わない。
    """
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (
        TypeError,
        ValueError,
    ):
        pass

    return str(value).strip().upper()


def clean_integer(
    value: Any,
    *,
    default: int | None = None,
) -> int | None:
    """
    数値を整数へ変換する。

    カンマやG表記を除去する。
    空欄・NaN・変換不能値はdefaultを返す。
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
# スルー数計算
# =========================================================

def count_until(
    row: pd.Series,
    *,
    target: str,
    stops: set[str],
) -> int:
    """
    ステータス1回前からMAX_BACK回前まで順番に走査する。

    - stopsに一致した時点で終了
    - targetに一致したときだけカウントを増やす

    例:
        BIGスルー数
        target="BIG"
        stops={"REG", "AT/ART"}
    """
    count = 0

    normalized_target = normalize_status(
        target
    )

    normalized_stops = {
        normalize_status(status)
        for status in stops
    }

    for index in range(
        1,
        MAX_BACK + 1,
    ):
        status = normalize_status(
            row.get(
                f"ステータス{index}回前",
                "",
            )
        )

        if status in normalized_stops:
            break

        if status == normalized_target:
            count += 1

    return count


def sum_first_games(
    row: pd.Series,
    count: int,
) -> int:
    """
    ゲーム1回前から、指定件数分のゲーム数を合計する。

    空欄や変換不能値は0として扱う。
    """
    if count <= 0:
        return 0

    total = 0

    end_index = min(
        count,
        MAX_BACK,
    )

    for index in range(
        1,
        end_index + 1,
    ):
        game_value = clean_integer(
            row.get(
                f"ゲーム{index}回前",
                None,
            ),
            default=0,
        )

        total += int(
            game_value or 0
        )

    return total


def calculate_total_games(
    row: pd.Series,
    through_count: int,
    span_game_sum: int,
) -> int:
    """
    スルー数が1以上の場合に、

        最終ゲーム + スルー間ゲーム数

    を返す。

    スルー数が0の場合は0を返す。
    """
    if through_count <= 0:
        return 0

    final_game = clean_integer(
        row.get(
            "最終ゲーム",
            None,
        ),
        default=0,
    )

    return int(
        final_game or 0
    ) + int(
        span_game_sum
    )


# =========================================================
# 1行分の計算
# =========================================================

def calculate_row_values(
    row: pd.Series,
) -> dict[str, int]:
    """
    1レコード分のスルー数関連値を計算する。
    """
    # BIGが連続している数。
    # REGまたはAT/ARTが出た時点で終了。
    big_through_count = count_until(
        row,
        target="BIG",
        stops={
            "REG",
            "AT/ART",
        },
    )

    # REGが連続している数。
    # BIGまたはAT/ARTが出た時点で終了。
    reg_through_count = count_until(
        row,
        target="REG",
        stops={
            "BIG",
            "AT/ART",
        },
    )

    # AT/ARTが連続している数。
    # BIGまたはREGが出た時点で終了。
    atart_through_count = count_until(
        row,
        target="AT/ART",
        stops={
            "BIG",
            "REG",
        },
    )

    big_span_games = sum_first_games(
        row,
        big_through_count,
    )

    reg_span_games = sum_first_games(
        row,
        reg_through_count,
    )

    atart_span_games = sum_first_games(
        row,
        atart_through_count,
    )

    big_total_games = calculate_total_games(
        row,
        big_through_count,
        big_span_games,
    )

    reg_total_games = calculate_total_games(
        row,
        reg_through_count,
        reg_span_games,
    )

    atart_total_games = calculate_total_games(
        row,
        atart_through_count,
        atart_span_games,
    )

    return {
        "BIGスルー数": big_through_count,
        "REGスルー数": reg_through_count,
        "ATARTスルー数": atart_through_count,
        "BIGスルー間ゲーム数": big_span_games,
        "REGスルー間ゲーム数": reg_span_games,
        "ATARTスルー間ゲーム数": atart_span_games,
        "BIGスルー間累計ゲーム数": big_total_games,
        "REGスルー間累計ゲーム数": reg_total_games,
        "ATARTスルー間累計ゲーム数": atart_total_games,
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

    print(
        "[INFO] 最新日データ読込開始"
    )

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
        f"[DB] 最新日レコード数: "
        f"{len(dataframe)}件"
    )

    # -----------------------------------------------------
    # スルー数・ゲーム数計算
    # -----------------------------------------------------

    print(
        "[CALC] スルー数および"
        "ゲーム数の集計開始"
    )

    calculated_rows: list[
        dict[str, int]
    ] = []

    for _, row in dataframe.iterrows():
        calculated_rows.append(
            calculate_row_values(
                row
            )
        )

    calculated_dataframe = pd.DataFrame(
        calculated_rows,
        index=dataframe.index,
    )

    for column in DESTINATION_COLUMNS:
        dataframe[
            f"calculated_{column}"
        ] = calculated_dataframe[
            column
        ].astype(int)

    print(
        f"[CALC] 集計完了: "
        f"{len(dataframe)}件"
    )

    # -----------------------------------------------------
    # 計算内容を表示
    # -----------------------------------------------------

    for _, row in dataframe.iterrows():
        print(
            f"[CALC] 台番号={row['台番号']}, "
            f"ROWID={row['_rowid']}, "
            f"BIG={row['calculated_BIGスルー数']}, "
            f"REG={row['calculated_REGスルー数']}, "
            f"ATART={row['calculated_ATARTスルー数']}, "
            f"BIG間={row['calculated_BIGスルー間ゲーム数']}, "
            f"REG間={row['calculated_REGスルー間ゲーム数']}, "
            f"ATART間={row['calculated_ATARTスルー間ゲーム数']}, "
            f"BIG累計={row['calculated_BIGスルー間累計ゲーム数']}, "
            f"REG累計={row['calculated_REGスルー間累計ゲーム数']}, "
            f"ATART累計="
            f"{row['calculated_ATARTスルー間累計ゲーム数']}"
        )

    # -----------------------------------------------------
    # 変更が必要な行だけ更新対象にする
    # -----------------------------------------------------

    update_parameters: list[
        tuple[Any, ...]
    ] = []

    unchanged_count = 0

    for _, row in dataframe.iterrows():
        calculated_values = [
            int(
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
                ],
                default=None,
            )
            for column in DESTINATION_COLUMNS
        ]

        if all(
            current_value
            == calculated_value
            for (
                current_value,
                calculated_value,
            ) in zip(
                current_values,
                calculated_values,
            )
        ):
            unchanged_count += 1
            continue

        update_parameters.append(
            tuple(
                calculated_values
                + [
                    int(row["_rowid"])
                ]
            )
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

    set_clause = ",\n            ".join(
        (
            f"{quote_identifier(column)} = ?"
        )
        for column in DESTINATION_COLUMNS
    )

    update_sql = f"""
        UPDATE {quote_identifier(TABLE_NAME)}
        SET
            {set_clause}
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
        "✅ スルー数関連更新完了: "
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




