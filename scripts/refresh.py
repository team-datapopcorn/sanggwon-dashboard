"""상권 대시보드 데이터 갱신 파이프라인.

  1) 최신 분기 ZIP 다운로드 (이미 있으면 재사용)
  2) CSV 압축 해제 (파일명 CP949 처리)
  3) 주민등록 인구 확보 (실패해도 계속)
  4) DuckDB 집계 → data/dashboard.json

인구가 없으면 밀도 지표만 빠져나가고 나머지는 정상 생성된다.
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_aggregates import build  # noqa: E402
from download import (  # noqa: E402
    download_population,
    download_zip,
    extract_version,
    safe_unzip,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"          # 작업 공간(다운로드 원본) — 커밋 안 함
RAW = DATA / "raw"
CSV_DIR = RAW / "csv"
POP_CSV = RAW / "population_sgg.csv"

# GitHub Pages 가 site/ 를 루트로 서비스하므로, 대시보드가 읽을 JSON 은
# site/data/ 아래에 있어야 한다. 루트 밖(../data)으로 두면 404가 난다.
OUT = ROOT / "site" / "data"


def convert_population_xls(xls_path: Path, out_csv: Path) -> Path | None:
    """행정안전부 시군구 인구 Excel 을 CSV 로 바꾼다.

    확장자는 .xls 지만 실제로는 xlsx 라 openpyxl 을 쓴다.
    행정기관코드는 10자리인데, 시군구 행은 뒷 5자리가 00000 이다
    (예: 1111000000 = 서울특별시 종로구). 이걸로 시군구만 기계적으로 걸러낸다.
    """
    import pandas as pd

    df = pd.read_excel(xls_path, sheet_name=0, header=None)

    header_row = None
    for i in range(min(10, len(df))):
        if str(df.iat[i, 0]).strip() == "행정기관코드":
            header_row = i
            break
    if header_row is None:
        print("[인구] 헤더행(행정기관코드)을 못 찾았다", file=sys.stderr, flush=True)
        print(f"  첫 열 값들: {[str(x)[:20] for x in df.iloc[:8, 0]]}", file=sys.stderr, flush=True)
        return None

    name_col, code_col, pop_col = 1, None, None
    for c in range(df.shape[1]):
        head = str(df.iat[header_row, c]).strip()
        if head == "행정기관코드":
            code_col = c
        elif "총인구수" in head or head in ("인구수", "총 인구"):
            pop_col = c
    if code_col is None or pop_col is None:
        print(f"[인구] 열 매칭 실패 code={code_col} pop={pop_col}", file=sys.stderr, flush=True)
        return None

    body = df.iloc[header_row + 1 :, [code_col, name_col, pop_col]].copy()
    body.columns = ["code", "label", "인구"]
    body["code"] = body["code"].astype(str).str.extract(r"(\d{10})", expand=False)
    body["label"] = body["label"].astype(str).str.strip()
    body = body.dropna(subset=["code"])

    # 행정기관코드는 10자리 = 시도(2) + 시군구(3) + 행정동(5).
    # 시군구 행은 행정동 자리가 모두 0 이고, 시도 행은 시군구 자리가 000 이다.
    #   시도: 1100000000 (서울) / 시군구: 1111000000 (서울 종로구)
    body = body[body["code"].str[5:10] == "00000"].copy().reset_index(drop=True)
    is_sido = body["code"].str[2:5] == "000"

    # 라벨은 "서울특별시 종로구" 형태. 공백으로 나눈다.
    body["시도명"] = body["label"].str.split().str[0]
    rest = body["label"].str.split().str[1:]
    body["시군구명"] = rest.str.join(" ").str.strip()

    # 시도 행은 '전체'로 표기해 시도 합계와 시군구明细을 한 파일에 담는다.
    # (세종은 시도이자 시군구인데, 집계 쪽에서 '세종특별자치시'로 되돌린다)
    body.loc[is_sido, "시군구명"] = "전체"
    # 라벨이 시도 하나뿐인데 코드가 시군구 행인 경우(세종 등)도 안전하게 처리
    body.loc[body["시군구명"] == "", "시군구명"] = body.loc[body["시군구명"] == "", "시도명"]
    body.loc[is_sido & (body["시군구명"] == "전체"), "시군구명"] = "전체"

    body["인구"] = body["인구"].astype(str).str.replace(",", "", regex=False).str.strip()
    body = body[body["인구"].str.match(r"^\d+$", na=False)]
    body["인구"] = body["인구"].astype(int)
    # 관할 출장소는 인구가 수백 명 수준이라 제외
    body = body[body["인구"] >= 5000]

    stamp = re.sub(r"[^0-9]", "", str(df.iat[header_row - 1, 2])) if header_row >= 1 else ""
    out = body[["시도명", "시군구명", "인구"]].copy()
    out["기준연월"] = (
        f"{stamp[:4]}-{stamp[4:6]}" if len(stamp) >= 6 else time.strftime("%Y-%m")
    )

    out.to_csv(out_csv, index=False, encoding="utf-8-sig")
    n_sgg = int((out["시군구명"] != "전체").sum())
    print(
        f"[인구] CSV 변환 완료: 시군구 {n_sgg}개 + 시도 {len(out) - n_sgg}개 "
        f"(기준 {out['기준연월'].iloc[0]}) -> {out_csv.name}",
        flush=True,
    )
    print(f"  예: {out.head(3).to_dict('records')}", flush=True)
    return out_csv


def main() -> int:
    t0 = time.time()
    RAW.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("[1/4] 상가 데이터 다운로드")
    print("=" * 60)
    data_nm, zip_path = download_zip(RAW / "zip")
    version = extract_version(data_nm)

    print("\n" + "=" * 60)
    print("[2/4] 압축 해제")
    print("=" * 60)
    if not any(CSV_DIR.glob("*.csv")):
        safe_unzip(zip_path, CSV_DIR)
    csvs = list(CSV_DIR.glob("*.csv"))
    if not csvs:
        print("[error] CSV 가 없다. ZIP 이 비어있거나 풀기 실패.", file=sys.stderr)
        return 1
    print(f"CSV {len(csvs)}개 확인")

    print("\n" + "=" * 60)
    print("[3/4] 주민등록 인구")
    print("=" * 60)
    if POP_CSV.exists():
        print(f"기존 사용: {POP_CSV.name}")
    else:
        xls = download_population(RAW)
        if xls:
            convert_population_xls(xls, POP_CSV)

    print("\n" + "=" * 60)
    print(f"[4/4] 집계 (버전 {version})")
    print("=" * 60)
    build(CSV_DIR, POP_CSV if POP_CSV.exists() else None, OUT, version)

    print(f"\n완료: {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())