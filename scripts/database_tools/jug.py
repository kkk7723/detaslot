#!/usr/bin/env python
# coding: utf-8

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
    PROJECT_ROOT = find_project_root(
        Path(__file__)
    )
else:
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


# =========================================================
# 共通設定
# =========================================================

from config.common import (
    DEFAULT_SITE,
    TABLE_NAME,
    require_file,
)

from config.juglist import (
    JUG_MACHINE_NAMES,
    MACHINE_NAME_COLUMN,
    EXPORT_COLUMNS,
    EXPORT_FILE_NAME,
)


# =========================================================
# 店舗選択
# =========================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--site",
        default=DEFAULT_SITE,
        help="config名",
    )

    return parser.parse_args()


if "__file__" in globals():
    args = parse_args()
else:
    args = argparse.Namespace(
        site=DEFAULT_SITE,
    )


# =========================================================
# 店舗別config読み込み
# =========================================================

config_file = (
    PROJECT_ROOT
    / "config"
    / f"{args.site}.py"
)


if not config_file.is_file():
    raise FileNotFoundError(
        f"店舗設定が見つかりません: "
        f"{config_file}"
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


if not hasattr(
    site_config,
    "DB_PATH",
):
    raise AttributeError(
        f"config/{args.site}.py に "
        "DB_PATH が設定されていません。"
    )


db_path = Path(
    site_config.DB_PATH
)


print(
    f"[INFO] 対象店舗: "
    f"{args.site}"
)

print(
    f"[INFO] 使用DB: "
    f"{db_path}"
)

print(
    f"[INFO] 対象テーブル: "
    f"{TABLE_NAME}"
)


# =========================================================
# SQLite共通
# =========================================================

def quote_identifier(
    identifier: str,
) -> str:
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

    cursor = connection.execute(
        f"""
        PRAGMA table_info(
            {quote_identifier(table_name)}
        )
        """
    )

    return {
        str(row[1])
        for row in cursor.fetchall()
    }


def require_columns(
    existing_columns: set[str],
    required_columns: list[str],
) -> None:

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
# 文字列正規化
# =========================================================

def normalize_text(
    value: Any,
) -> str:

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

    return str(value).strip()


# =========================================================
# 台番号正規化
# =========================================================

def normalize_machine_number(
    value: Any,
) -> str:
    """
    台番号を末尾取得用に正規化する。

    例:
        123      -> "123"
        "0123"   -> "123"
        123.0    -> "123"
        "123.0"  -> "123"
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

    text = str(value).strip()

    if not text:
        return ""

    try:
        number = float(text)

        if number.is_integer():
            return str(
                int(number)
            )
    except ValueError:
        pass

    if text.isdigit():
        return str(
            int(text)
        )

    return text


# =========================================================
# 対象機種取得
# =========================================================

def get_target_machine_names() -> set[str]:

    machine_names = {
        normalize_text(
            machine_name
        )
        for machine_name in JUG_MACHINE_NAMES
        if normalize_text(
            machine_name
        )
    }

    if not machine_names:
        raise RuntimeError(
            "config/juglist.py の "
            "JUG_MACHINE_NAMES が空です。"
        )

    return machine_names


# =========================================================
# DBから全データ取得
# =========================================================

def load_database_data(
    connection: sqlite3.Connection,
) -> pd.DataFrame:

    select_columns = [
        MACHINE_NAME_COLUMN,
        *EXPORT_COLUMNS,
    ]

    select_columns = list(
        dict.fromkeys(
            select_columns
        )
    )

    select_sql_columns = ",\n".join(
        quote_identifier(
            column
        )
        for column in select_columns
    )

    sql = f"""
        SELECT
            {select_sql_columns}
        FROM
            {quote_identifier(TABLE_NAME)}
        ORDER BY
            date({quote_identifier("実行日")}) ASC,
            {quote_identifier("台番号")} ASC
    """

    return pd.read_sql_query(
        sql,
        connection,
    )


# =========================================================
# CSV出力先
# =========================================================

def create_export_path(
    site: str,
) -> Path:

    export_dir = (
        PROJECT_ROOT
        / "export"
        / site
    )

    export_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return (
        export_dir
        / EXPORT_FILE_NAME
    )


# =========================================================
# 実際日作成
# =========================================================

def add_actual_date(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    DBの実行日からCSV用の「実際日」を作る。

    例:
        実行日:
        2025-09-06 05:58:15

        ↓ 1日前

        実際日:
        2025-09-05

    DB自体は変更しない。
    """

    result = dataframe.copy()

    converted_dates = pd.to_datetime(
        result["実行日"],
        errors="coerce",
    )

    invalid_count = int(
        converted_dates.isna().sum()
    )

    if invalid_count:
        print(
            f"[WARN] 実行日の変換失敗: "
            f"{invalid_count}件"
        )

    result["実際日"] = (
        converted_dates
        - pd.Timedelta(
            days=1
        )
    ).dt.strftime(
        "%Y-%m-%d"
    )

    return result


# =========================================================
# 実際日から分析用カラムを作成
# =========================================================

def add_analysis_columns(
    dataframe: pd.DataFrame,
) -> pd.DataFrame:
    """
    CSV専用の分析カラムを作成する。

    実際日基準:
        ・年月
        ・年
        ・月
        ・期間
        ・曜日
        ・平日休日
        ・日付
        ・日付末尾

    台番号基準:
        ・台末尾

    DB自体は変更しない。
    """

    result = dataframe.copy()


    # -----------------------------------------------------
    # 実際日をdatetimeへ変換
    # -----------------------------------------------------

    actual_dates = pd.to_datetime(
        result["実際日"],
        errors="coerce",
    )


    # =====================================================
    # 年月
    # =====================================================
    #
    # 例:
    # 2025-09-25
    # ↓
    # 2025-09
    # =====================================================

    result["年月"] = (
        actual_dates
        .dt
        .strftime(
            "%Y-%m"
        )
    )


    # =====================================================
    # 年
    # =====================================================

    result["年"] = (
        actual_dates
        .dt
        .year
        .astype(
            "Int64"
        )
    )


    # =====================================================
    # 月
    # =====================================================

    result["月"] = (
        actual_dates
        .dt
        .month
        .astype(
            "Int64"
        )
    )


    # =====================================================
    # 日付
    # =====================================================

    day_numbers = (
        actual_dates
        .dt
        .day
    )

    result["日付"] = (
        day_numbers
        .astype(
            "Int64"
        )
    )


    # =====================================================
    # 日付末尾
    # =====================================================
    #
    # 例:
    # 25日 → 5
    # 30日 → 0
    # =====================================================

    result["日付末尾"] = (
        day_numbers % 10
    ).astype(
        "Int64"
    )


    # =====================================================
    # 期間
    # =====================================================

    result["期間"] = pd.cut(
        day_numbers,
        bins=[
            0,
            10,
            20,
            31,
        ],
        labels=[
            "1-10日",
            "11-20日",
            "21-31日",
        ],
        include_lowest=True,
    )


    # =====================================================
    # 曜日
    # =====================================================

    weekday_map = {
        0: "月",
        1: "火",
        2: "水",
        3: "木",
        4: "金",
        5: "土",
        6: "日",
    }

    day_of_week = (
        actual_dates
        .dt
        .dayofweek
    )

    result["曜日"] = (
        day_of_week
        .map(
            weekday_map
        )
    )


    # =====================================================
    # 平日休日
    # =====================================================
    #
    # 月～金 → 平日
    # 土・日 → 休日
    #
    # 祝日は判定しない。
    # =====================================================

    result["平日休日"] = (
        day_of_week
        .map(
            lambda value: (
                "休日"
                if pd.notna(value)
                and int(value) >= 5
                else (
                    "平日"
                    if pd.notna(value)
                    else ""
                )
            )
        )
    )


    # =====================================================
    # 台末尾
    # =====================================================
    #
    # 例:
    # 台番号123 → 3
    # 台番号120 → 0
    # 台番号0032 → 2
    # =====================================================

    def get_machine_last_digit(
        value: Any,
    ) -> int | None:

        normalized = (
            normalize_machine_number(
                value
            )
        )

        if not normalized:
            return None

        if not normalized.isdigit():
            return None

        return int(
            normalized[-1]
        )


    result["台末尾"] = (
        result["台番号"]
        .map(
            get_machine_last_digit
        )
        .astype(
            "Int64"
        )
    )


    return result


# =========================================================
# メイン処理
# =========================================================

def main() -> None:
    start_time = time.time()


    # -----------------------------------------------------
    # DBファイル確認
    # -----------------------------------------------------

    require_file(
        db_path,
        "店舗別SQLiteデータベース",
    )


    # -----------------------------------------------------
    # 対象機種取得
    # -----------------------------------------------------

    machine_names = (
        get_target_machine_names()
    )


    print(
        f"[CONFIG] 対象機種数: "
        f"{len(machine_names)}件"
    )


    # -----------------------------------------------------
    # DB読み込み
    # -----------------------------------------------------

    with sqlite3.connect(
        db_path
    ) as connection:

        table_columns = get_table_columns(
            connection,
            TABLE_NAME,
        )

        required_columns = list(
            dict.fromkeys([
                MACHINE_NAME_COLUMN,
                *EXPORT_COLUMNS,
            ])
        )

        require_columns(
            table_columns,
            required_columns,
        )


        dataframe = load_database_data(
            connection,
        )


    # -----------------------------------------------------
    # DBデータなし
    # -----------------------------------------------------

    if dataframe.empty:
        print(
            "[INFO] DBに対象データがありません。"
        )
        return


    print(
        f"[DB] DB総レコード数: "
        f"{len(dataframe)}件"
    )


    # -----------------------------------------------------
    # 機種名比較用
    # -----------------------------------------------------

    dataframe[
        "_機種名比較用"
    ] = dataframe[
        MACHINE_NAME_COLUMN
    ].map(
        normalize_text
    )


    # -----------------------------------------------------
    # juglist.py と完全一致する機種だけ抽出
    # -----------------------------------------------------

    matched_dataframe = dataframe[
        dataframe[
            "_機種名比較用"
        ].isin(
            machine_names
        )
    ].copy()


    print(
        f"[FILTER] 一致レコード: "
        f"{len(matched_dataframe)}件"
    )


    # -----------------------------------------------------
    # DBに存在する出力カラムだけ取得
    # -----------------------------------------------------

    export_dataframe = (
        matched_dataframe[
            EXPORT_COLUMNS
        ]
        .copy()
    )


    # =====================================================
    # CSV専用「実際日」を追加
    # =====================================================

    export_dataframe = add_actual_date(
        export_dataframe
    )


    # =====================================================
    # 分析用カラムを追加
    # =====================================================

    export_dataframe = add_analysis_columns(
        export_dataframe
    )


    # =====================================================
    # CSVの最終カラム順
    # =====================================================

    final_columns = [
        "実際日",
        "年月",
        "年",
        "月",
        "期間",
        "曜日",
        "平日休日",
        "日付",
        "日付末尾",
        "台末尾",
        *EXPORT_COLUMNS,
    ]


    # -----------------------------------------------------
    # 重複カラムを除去
    # -----------------------------------------------------

    final_columns = list(
        dict.fromkeys(
            final_columns
        )
    )


    export_dataframe = (
        export_dataframe[
            final_columns
        ]
        .copy()
    )


    # -----------------------------------------------------
    # CSV出力先
    # -----------------------------------------------------

    csv_path = create_export_path(
        args.site,
    )


    # -----------------------------------------------------
    # CSV出力
    # -----------------------------------------------------

    export_dataframe.to_csv(
        csv_path,
        index=False,
        encoding="utf-8-sig",
    )


    # -----------------------------------------------------
    # 完了
    # -----------------------------------------------------

    print()

    print(
        "========================================"
    )

    print(
        "✅ CSV出力完了"
    )

    print(
        f"[CSV] {csv_path}"
    )

    print(
        f"[CSV] 出力件数: "
        f"{len(export_dataframe)}件"
    )

    print(
        f"[CSV] 出力カラム: "
        f"{final_columns}"
    )

    print(
        "[CSV] 実際日: "
        "実行日から-1日"
    )

    print(
        "[CSV] 実際日形式: "
        "YYYY-MM-DD"
    )

    print(
        "[CSV] 年月: "
        "YYYY-MM"
    )

    print(
        "[CSV] 年: "
        "実際日の年"
    )

    print(
        "[CSV] 月: "
        "実際日の月"
    )

    print(
        "[CSV] 期間: "
        "1-10日 / 11-20日 / 21-31日"
    )

    print(
        "[CSV] 曜日: "
        "月 / 火 / 水 / 木 / 金 / 土 / 日"
    )

    print(
        "[CSV] 平日休日: "
        "月～金=平日 / 土日=休日"
    )

    print(
        "[CSV] 日付: "
        "実際日の月内日付"
    )

    print(
        "[CSV] 日付末尾: "
        "実際日の日付1の位"
    )

    print(
        "[CSV] 台末尾: "
        "台番号の1の位"
    )

    print(
        "[DB] 追加カラムのDB保存: "
        "なし"
    )

    print(
        f"[INFO] 所要時間: "
        f"{time.time() - start_time:.1f}秒"
    )

    print(
        "========================================"
    )


# =========================================================
# 実行
# =========================================================

if __name__ == "__main__":
    main()

