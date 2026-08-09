#!/usr/bin/env python
# coding: utf-8

from __future__ import annotations

import argparse
import importlib
import sys
import time
from pathlib import Path

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
    # .py実行時
    PROJECT_ROOT = find_project_root(
        Path(__file__)
    )
else:
    # Notebook実行時
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
    GOOGLE_CREDENTIALS_FILE,
    GOOGLE_SCOPES,
    require_file,
)

from config.juglist import (
    EXPORT_FILE_NAME,
)

from utils.sheet_utils import (
    open_worksheet,
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
    # .py実行時
    args = parse_args()
else:
    # Notebook実行時
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


# =========================================================
# 店舗別必須設定
# =========================================================

required_site_settings = (
    "GSHEET_NAME",
)


for setting_name in required_site_settings:
    if not hasattr(
        site_config,
        setting_name,
    ):
        raise AttributeError(
            f"config/{args.site}.py に "
            f"{setting_name} が設定されていません。"
        )


spreadsheet_name = (
    site_config.GSHEET_NAME
)


# =========================================================
# jugシート設定
# =========================================================

WORKSHEET_NAME = "jug"


# =========================================================
# CSVパス
# =========================================================
#
# detaslot/export/<site>/jug.csv
#
# =========================================================

csv_path = (
    PROJECT_ROOT
    / "export"
    / args.site
    / EXPORT_FILE_NAME
)


print(
    f"[INFO] 対象店舗: "
    f"{args.site}"
)

print(
    f"[INFO] CSV: "
    f"{csv_path}"
)

print(
    f"[INFO] スプレッドシート: "
    f"{spreadsheet_name}"
)

print(
    f"[INFO] シート名: "
    f"{WORKSHEET_NAME}"
)


# =========================================================
# CSV読み込み
# =========================================================

def load_csv(
    path: Path,
) -> pd.DataFrame:
    """
    jug.csvを読み込む。
    """

    require_file(
        path,
        "ジャグラーCSV",
    )


    dataframe = pd.read_csv(
        path,
        encoding="utf-8-sig",
    )


    if dataframe.empty:
        raise RuntimeError(
            f"CSVにデータがありません: "
            f"{path}"
        )


    return dataframe


# =========================================================
# Google Sheets書き込み用に変換
# =========================================================

def dataframe_to_sheet_values(
    dataframe: pd.DataFrame,
) -> list[list]:
    """
    DataFrameをGoogle Sheetsの
    worksheet.update() 用二次元listへ変換する。

    1行目にはヘッダーを含める。

    NaN / NaT は空文字に変換する。
    """

    output_dataframe = (
        dataframe.copy()
    )


    # -----------------------------------------------------
    # NaN / NaTなどを空欄にする
    # -----------------------------------------------------

    output_dataframe = (
        output_dataframe
        .astype(object)
        .where(
            pd.notna(
                output_dataframe
            ),
            "",
        )
    )


    # -----------------------------------------------------
    # ヘッダー
    # -----------------------------------------------------

    headers = [
        str(column)
        for column in output_dataframe.columns
    ]


    # -----------------------------------------------------
    # データ
    # -----------------------------------------------------

    rows = (
        output_dataframe
        .values
        .tolist()
    )


    return [
        headers,
        *rows,
    ]


# =========================================================
# Excel列番号 → アルファベット変換
# =========================================================

def column_number_to_letter(
    column_number: int,
) -> str:
    """
    1 -> A
    2 -> B
    26 -> Z
    27 -> AA
    """

    if column_number < 1:
        raise ValueError(
            "column_number は1以上にしてください。"
        )


    letters = ""


    while column_number:

        column_number, remainder = divmod(
            column_number - 1,
            26,
        )

        letters = (
            chr(
                65 + remainder
            )
            + letters
        )


    return letters


# =========================================================
# メイン処理
# =========================================================

def main() -> None:
    start_time = time.time()


    # =====================================================
    # Google認証ファイル確認
    # =====================================================

    require_file(
        GOOGLE_CREDENTIALS_FILE,
        "GoogleサービスアカウントJSON",
    )


    # =====================================================
    # CSV読み込み
    # =====================================================

    dataframe = load_csv(
        csv_path
    )


    print(
        f"[CSV] 読み込み完了: "
        f"{len(dataframe)}件"
    )

    print(
        f"[CSV] カラム数: "
        f"{len(dataframe.columns)}列"
    )

    print(
        f"[CSV] カラム: "
        f"{list(dataframe.columns)}"
    )


    # =====================================================
    # Google Sheets接続
    # =====================================================

    worksheet = open_worksheet(
        credentials_file=GOOGLE_CREDENTIALS_FILE,
        scopes=GOOGLE_SCOPES,
        spreadsheet_name=spreadsheet_name,
        worksheet_name=WORKSHEET_NAME,
    )


    print(
        "[SHEET] Googleスプレッドシート認証・"
        "jugシート取得完了"
    )


    # =====================================================
    # Sheets書き込み用データ作成
    # =====================================================

    values = dataframe_to_sheet_values(
        dataframe
    )


    if not values:
        raise RuntimeError(
            "スプレッドシートへ"
            "書き込むデータがありません。"
        )


    row_count = len(
        values
    )

    column_count = len(
        values[0]
    )


    end_column = (
        column_number_to_letter(
            column_count
        )
    )


    target_range = (
        f"A1:"
        f"{end_column}"
        f"{row_count}"
    )


    print(
        f"[SHEET] 書き込み範囲: "
        f"{target_range}"
    )


    # =====================================================
    # 既存jugシートをクリア
    # =====================================================

    try:
        worksheet.clear()

    except Exception as exc:
        raise RuntimeError(
            "[SHEET] jugシートの"
            "クリアに失敗しました: "
            f"{exc}"
        ) from exc


    print(
        "[SHEET] 既存データクリア完了"
    )


    # =====================================================
    # CSV全体を一括書き込み
    # =====================================================

    try:
        worksheet.update(
            range_name=target_range,
            values=values,
        )

    except Exception as exc:
        raise RuntimeError(
            "[SHEET] jug.csvの"
            "一括書き込みに失敗しました: "
            f"{exc}"
        ) from exc


    # =====================================================
    # 完了
    # =====================================================

    print()

    print(
        "========================================"
    )

    print(
        "✅ jugシート一括書き込み完了"
    )

    print(
        f"[SITE] "
        f"{args.site}"
    )

    print(
        f"[CSV] "
        f"{csv_path}"
    )

    print(
        f"[SHEET] "
        f"{spreadsheet_name}"
    )

    print(
        f"[WORKSHEET] "
        f"{WORKSHEET_NAME}"
    )

    print(
        f"[RANGE] "
        f"{target_range}"
    )

    print(
        f"[DATA] "
        f"{len(dataframe)}件"
    )

    print(
        f"[HEADER] "
        f"{column_count}列"
    )

    print(
        f"[TOTAL ROWS] "
        f"{row_count}行"
    )

    print(
        f"[INFO] 所要時間: "
        f"{time.time() - start_time:.2f}秒"
    )

    print(
        "========================================"
    )


# =========================================================
# 実行
# =========================================================

if __name__ == "__main__":
    main()
