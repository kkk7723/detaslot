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
# 対象カラム
# =========================================================

SOURCE_COLUMNS = [
    "実行日",
    "台番号",
    "BIG",
    "REG",
    "BONUS",
    "ATART",
    "ステータス1回前",
    "ステータス2回前",
    "ステータス3回前",
]

DESTINATION_COLUMNS = [
    "宵越し特賞履歴ステータス1回前",
    "宵越し特賞履歴ステータス2回前",
    "宵越し特賞履歴ステータス3回前",
]


# =========================================================
# 共通関数
# =========================================================

def quote_identifier(
    identifier: str,
) -> str:
    """
    SQLiteのテーブル名・カラム名を
    []で囲む。
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


def normalize_machine_number(
    value: Any,
) -> str:
    """
    台番号をグループ化用の形式へ統一する。

    例:
        32     -> "32"
        "0032" -> "32"
        32.0   -> "32"
        空欄   -> ""
    """
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    text = str(value).strip()

    if not text:
        return ""

    try:
        number = float(text)

        if number.is_integer():
            return str(int(number))
    except ValueError:
        pass

    if text.isdigit():
        return str(int(text))

    return text


def clean_status(
    value: Any,
) -> str | None:
    """
    ステータス値を保存用に整形する。
    """
    if value is None:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    text = str(value).strip()

    return text if text else None


def pick_previous_statuses_at_first_hit(
    machine_dataframe: pd.DataFrame,
) -> tuple[
    str | None,
    str | None,
    str | None,
]:
    """
    1台分のデータを最新から過去へ並べる。

    BIG・REG・BONUS・ATARTのいずれかが
    1以上になった最初のレコードから、

    - ステータス1回前
    - ステータス2回前
    - ステータス3回前

    を取得して返す。

    ヒットがない場合は、
    (None, None, None) を返す。
    """
    working = (
        machine_dataframe
        .sort_values(
            [
                "実行日",
                "_rowid",
            ],
            ascending=[
                False,
                False,
            ],
        )
        .reset_index(drop=True)
        .copy()
    )

    hit_mask = (
        (working["BIG"] >= 1)
        | (working["REG"] >= 1)
        | (working["BONUS"] >= 1)
        | (working["ATART"] >= 1)
    )

    hit_positions = working.index[
        hit_mask
    ].tolist()

    if not hit_positions:
        return (
            None,
            None,
            None,
        )

    first_hit_position = hit_positions[0]

    hit_row = working.iloc[
        first_hit_position
    ]

    return (
        clean_status(
            hit_row[
                "ステータス1回前"
            ]
        ),
        clean_status(
            hit_row[
                "ステータス2回前"
            ]
        ),
        clean_status(
            hit_row[
                "ステータス3回前"
            ]
        ),
    )


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
    # DBデータ取得
    # -----------------------------------------------------

    select_columns = ", ".join(
        quote_identifier(column)
        for column in SOURCE_COLUMNS
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

    with sqlite3.connect(
        db_path
    ) as connection:
        dataframe = pd.read_sql_query(
            sql,
            connection,
        )

    if dataframe.empty:
        print(
            "[INFO] DBに対象データがありません。"
        )
        return

    print(
        f"[DB] 取得完了: "
        f"{len(dataframe)}件"
    )

    # -----------------------------------------------------
    # 型整備
    # -----------------------------------------------------

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

    dataframe[
        "照合用台番号"
    ] = dataframe[
        "台番号"
    ].map(
        normalize_machine_number
    )

    dataframe = dataframe[
        dataframe["照合用台番号"] != ""
    ].copy()

    if dataframe.empty:
        print(
            "[INFO] 有効な実行日・台番号を"
            "持つデータがありません。"
        )
        return

    numeric_columns = [
        "BIG",
        "REG",
        "BONUS",
        "ATART",
    ]

    for column in numeric_columns:
        dataframe[column] = pd.to_numeric(
            dataframe[column],
            errors="coerce",
        ).fillna(0).astype(int)

    # -----------------------------------------------------
    # DB内の最新日を取得
    # -----------------------------------------------------

    latest_date = (
        dataframe["実行日"]
        .dt.date
        .max()
    )

    latest_date_dataframe = dataframe[
        dataframe["実行日"].dt.date
        == latest_date
    ].copy()

    print(
        f"[DB] 最新日: "
        f"{latest_date}"
    )
    print(
        f"[DB] 最新日のレコード数: "
        f"{len(latest_date_dataframe)}件"
    )

    # -----------------------------------------------------
    # 最新日の各台について最新1レコードを取得
    # -----------------------------------------------------

    latest_rows = (
        latest_date_dataframe
        .sort_values(
            [
                "照合用台番号",
                "実行日",
                "_rowid",
            ],
            ascending=[
                True,
                False,
                False,
            ],
        )
        .drop_duplicates(
            subset=[
                "照合用台番号"
            ],
            keep="first",
        )
        .copy()
    )

    print(
        f"[DB] 最新日の対象台数: "
        f"{len(latest_rows)}台"
    )

    # -----------------------------------------------------
    # 台番号ごとの宵越しステータス計算
    # -----------------------------------------------------

    calculated_statuses: dict[
        str,
        tuple[
            str | None,
            str | None,
            str | None,
        ],
    ] = {}

    for (
        normalized_machine_number,
        machine_group,
    ) in dataframe.groupby(
        "照合用台番号",
        sort=False,
    ):
        statuses = (
            pick_previous_statuses_at_first_hit(
                machine_group
            )
        )

        calculated_statuses[
            normalized_machine_number
        ] = statuses

        print(
            f"[CALC] 台番号="
            f"{normalized_machine_number}, "
            f"1回前={statuses[0]!r}, "
            f"2回前={statuses[1]!r}, "
            f"3回前={statuses[2]!r}"
        )

    # -----------------------------------------------------
    # 最新日の各台の最新行だけ更新
    # -----------------------------------------------------

    update_sql = f"""
        UPDATE {quote_identifier(TABLE_NAME)}
        SET
            {
                quote_identifier(
                    DESTINATION_COLUMNS[0]
                )
            } = ?,
            {
                quote_identifier(
                    DESTINATION_COLUMNS[1]
                )
            } = ?,
            {
                quote_identifier(
                    DESTINATION_COLUMNS[2]
                )
            } = ?
        WHERE
            ROWID = ?
    """

    updates: list[
        tuple[
            str | None,
            str | None,
            str | None,
            int,
        ]
    ] = []

    for _, latest_row in latest_rows.iterrows():
        normalized_machine_number = str(
            latest_row["照合用台番号"]
        ).strip()

        statuses = calculated_statuses.get(
            normalized_machine_number,
            (
                None,
                None,
                None,
            ),
        )

        updates.append((
            statuses[0],
            statuses[1],
            statuses[2],
            int(latest_row["_rowid"]),
        ))

        print(
            f"[UPDATE準備] 台番号="
            f"{latest_row['台番号']}, "
            f"ROWID={latest_row['_rowid']}"
        )

    updated_count = 0

    with sqlite3.connect(
        db_path
    ) as connection:
        cursor = connection.cursor()

        for update_parameters in updates:
            cursor.execute(
                update_sql,
                update_parameters,
            )

            updated_count += max(
                cursor.rowcount,
                0,
            )

        connection.commit()

    # -----------------------------------------------------
    # 完了
    # -----------------------------------------------------

    print(
        "✅ 宵越し特賞履歴ステータス"
        "(1/2/3回前) 更新完了"
    )
    print(
        f"[DB] 更新行数: "
        f"{updated_count}行"
    )
    print(
        "[DB] 更新対象: "
        "最新日の各台の最新1レコードのみ"
    )
    print(
        f"[INFO] 所要時間: "
        f"{time.time() - start_time:.2f}秒"
    )


# =========================================================
# 実行
# =========================================================

if __name__ == "__main__":
    main()


# In[ ]:




