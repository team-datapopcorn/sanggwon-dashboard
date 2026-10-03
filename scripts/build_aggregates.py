"""전국 상가 CSV 를 DuckDB 로 집계해 대시보드용 JSON 산출물을 만든다.

메모리 Consideration: 277만 행 × 39컬럼을 pandas 로 올리면 1GB 넘게 든다.
DuckDB 는 CSV 를 직접 스트리밍해 그룹별 집계만 하므로 메모리를 거의 쓰지 않는다
(로컬 검증: 16개 CSV 47초).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb

# 브랜드 매칭은 상호명 LIKE 규칙 기반 "추정"이다. 실제 점포 수와 다를 수 있다.
BRAND_RULES: dict[str, list[tuple[str, list[str]]]] = {
    "cvs": [
        ("GS25", ["%GS25%", "%지에스25%"]),
        ("CU", ["%CU%", "%씨유%"]),
        ("세븐일레븐", ["%세븐일레븐%", "%7-ELEVEN%", "%7ELEVEN%"]),
        ("이마트24", ["%이마트24%", "%emart24%"]),
        ("미니스톱", ["%미니스톱%"]),
    ],
    "cafe": [
        ("메가커피", ["%메가커피%", "%메가엠지씨%", "%메가MGC%"]),
        ("컴포즈", ["%컴포즈%"]),
        ("이디야", ["%이디야%"]),
        ("스타벅스", ["%스타벅스%"]),
        ("빽다방", ["%빽다방%"]),
        ("투썸플레이스", ["%투썸%"]),
        ("더벤티", ["%더벤티%"]),
        ("공차", ["%공차%"]),
        ("할리스", ["%할리스%"]),
        ("파스쿠찌", ["%파스쿠찌%"]),
        ("커피빈", ["%커피빈%"]),
        ("엔제리너스", ["%엔제리너스%"]),
        ("폴바셋", ["%폴바셋%"]),
    ],
    "chicken": [
        ("BBQ", ["%BBQ%", "%비비큐%"]),
        ("bhc", ["%BHC%", "%bhc%", "%비에이치씨%"]),
        ("교촌", ["%교촌%"]),
        ("처갓집", ["%처갓집%"]),
        ("굽네", ["%굽네%"]),
        ("페리카나", ["%페리카나%"]),
        ("네네", ["%네네치킨%"]),
        ("노랑통닭", ["%노랑통닭%"]),
        ("푸라닭", ["%푸라닭%"]),
        ("자담치킨", ["%자담%"]),
        ("멕시카나", ["%멕시카나%"]),
        ("60계", ["%60계%"]),
    ],
}

def _rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def brand_sql(category_key: str, small_name: str) -> str:
    """브랜드별 CASE WHEN 식을 만든다. 규칙이 없으면 전체 0."""
    rules = BRAND_RULES.get(category_key, [])
    if not rules:
        return "0"
    whens = []
    for brand, patterns in rules:
        cond = " or ".join(f"상호명 like '{p}'" for p in patterns)
        whens.append(f"when {cond} then '{brand}'")
    return "case " + " ".join(whens) + " else '기타/개인' end"


def build(csv_dir: Path, pop_csv: Path | None, out_dir: Path, version: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    t0 = time.time()

    glob = str(csv_dir / "*.csv")
    con.execute(
        f"""
        create or replace table shop as
        select * from read_csv('{glob}', header=true, union_by_name=true,
                               ignore_errors=true, filename=true)
        """
    )
    total = con.execute("select count(*) from shop").fetchone()[0]
    print(f"[duckdb] {total:,}행 로드 ({time.time() - t0:.1f}s)", flush=True)

    out: dict = {
        "meta": {
            "version": version,
            "total": total,
            "source": "소상공인시장진흥공단_상가(상권)정보",
            "license": "이용허락범위 제한 없음 (공공데이터포털)",
            "updated": time.strftime("%Y-%m-%d"),
            "note": "전국 영업 중 상가업소. 분기별 스냅샷이므로 폐업률은 이 데이터만으로 계산할 수 없다.",
        }
    }

    # ---- 1. 대분류/중분류/소분류 구성 -------------------------------------------------
    out["major"] = _rows(
        con,
        "select 상권업종대분류명 k, count(*) n from shop group by 1 order by 2 desc",
    )
    out["middle"] = _rows(
        con,
        """select 상권업종대분류명 major, 상권업종중분류명 k, count(*) n
           from shop group by 1,2 order by 3 desc""",
    )
    out["small"] = _rows(
        con,
        "select 상권업종소분류명 k, count(*) n from shop group by 1 order by 2 desc",
    )

    # ---- 2. 시도 --------------------------------------------------------------------
    out["sido"] = _rows(
        con,
        """select 시도명 sido, count(*) n,
             round(100.0*count(*) filter(where 상권업종대분류명='음식')/count(*),1) food,
             round(100.0*count(*) filter(where 상권업종소분류명='카페')/count(*),1) cafe,
             round(100.0*count(*) filter(where 상권업종소분류명='편의점')/count(*),1) cvs,
             round(100.0*count(*) filter(where 상권업종대분류명='교육')/count(*),1) edu,
             round(100.0*count(*) filter(where 상권업종대분류명='부동산')/count(*),1) realty,
             round(100.0*count(*) filter(where 상권업종대분류명='숙박')/count(*),1) lodging,
             round(100.0*count(*) filter(where 상권업종대분류명='과학·기술')/count(*),1) sci
           from shop group by 1 order by 2 desc""",
    )

    # ---- 3. 시군구 ------------------------------------------------------------------
    out["sgg"] = _rows(
        con,
        """select 시도명 sido, 시군구명 sgg, count(*) n,
             round(100.0*count(*) filter(where 상권업종대분류명='음식')/count(*),1) food,
             round(100.0*count(*) filter(where 상권업종대분류명='과학·기술')/count(*),1) sci,
             round(100.0*count(*) filter(where 상권업종대분류명='교육')/count(*),1) edu,
             round(100.0*count(*) filter(where 상권업종대분류명='숙박')/count(*),1) lodging
           from shop group by 1,2 order by 3 desc""",
    )

    # ---- 4. 행정동 상위 (전국 상위 N개만, 지도 라벨용) --------------------------------
    out["dong"] = _rows(
        con,
        """select 시도명 sido, 시군구명 sgg, 행정동명 dong, count(*) n,
             round(avg(위도),5) lat, round(avg(경도),5) lon
           from shop group by 1,2,3 order by 4 desc limit 300""",
    )

    # ---- 5. 층 ------------------------------------------------------------------------
    out["floor"] = _rows(
        con,
        """select 상권업종대분류명 k, count(*) n,
             round(100.0*count(*) filter(where 층정보='1')/count(*),1) f1,
             round(100.0*count(*) filter(where 층정보 in ('2','3'))/count(*),1) f23,
             round(100.0*count(*) filter(where 층정보 not in ('1','2','3','지') or 층정보 like 'B%' or 층정보 like '%지하%')/count(*),1) f4p,
             round(100.0*count(*) filter(where 층정보='지')/count(*),1) base
           from shop where 층정보 is not null group by 1 order by 3 desc""",
    )

    # ---- 6. 브랜드 (상호명 LIKE 기반 추정) ----------------------------------------------
    for key, small_name in [("cvs", "편의점"), ("cafe", "카페"), ("chicken", "치킨")]:
        case = brand_sql(key, small_name)
        rows = _rows(
            con,
            f"""select {case} k, count(*) n from shop
                where 상권업종소분류명='{small_name}' group by 1 order by 2 desc""",
        )
        rows.append({"k": "__total__", "n": sum(r["n"] for r in rows)})
        out[f"brand_{key}"] = rows

    # ---- 7. 경쟁 미집약 지역 찾기 ------------------------------------------------------
    # 시군구 × 주요 업종별 1만명당 점포수. 인구가 없으면 시군구 전체 대비 비율로 대체.
    focus = [
        "카페",
        "편의점",
        "치킨",
        "미용실",
        "피부 관리실",
        "부동산 중개/대리업",
        "입시·교과학원",
        "빵/도넛",
        "노래방",
        "약국",
        "세탁소",
        "안경",
    ]
    sel = ",\n".join(
        f"""round(sum(case when 상권업종소분류명='{c}' then 1 else 0 end),0) AS "{c}\""""
        for c in focus
    )
    out["sgg_focus"] = _rows(
        con,
        f"""select 시도명 sido, 시군구명 sgg, count(*) n, {sel}
            from shop group by 1,2 order by 3 desc""",
    )

    # ---- 8. 서울 구별 성격 -----------------------------------------------------------
    out["seoul_gu"] = _rows(
        con,
        """select 시군구명 gu, count(*) n,
             round(100.0*count(*) filter(where 상권업종대분류명='음식')/count(*),1) food,
             round(100.0*count(*) filter(where 상권업종소분류명='카페')/count(*),1) cafe,
             round(100.0*count(*) filter(where 상권업종대분류명='과학·기술')/count(*),1) sci,
             round(100.0*count(*) filter(where 상권업종대분류명='교육')/count(*),1) edu,
             round(100.0*count(*) filter(where 상권업종대분류명='숙박')/count(*),1) lodging
           from shop where 시도명='서울특별시' group by 1 order by 2 desc""",
    )

    # ---- 9. 좌표 기반 지도용 (격자 집계로 압축) ----------------------------------------
    # 원본 277만 점을 그대로 JSON 에 넣으면 수백 MB 가 된다.
    # 0.02도(약 2km) 격자로 뭉치고, 주요 업종별 개수만 남긴다.
    out["grid"] = _rows(
        con,
        f"""select floor(위도/0.02)*0.02 lat, floor(경도/0.02)*0.02 lon,
             count(*) n, {sel}
           from shop group by 1,2 order by 3 desc""",
    )
    print(f"[duckdb] grid 셀 {len(out['grid']):,}개", flush=True)

    # ---- 10. 인구 결합 ---------------------------------------------------------------
    if pop_csv and pop_csv.exists():
        con.execute(
            f"""create or replace table pop as
                select trim(시도명) as 시도명, trim(시군구명) as 시군구명,
                       try_cast(인구 as bigint) as 인구, 기준연월
                from read_csv('{pop_csv}', header=true, all_varchar=true)"""
        )
        # 세종은 시도이자 단일 시군구다. 시도 합계용 '전체' 행을 그대로 두고,
        # 시군구 조인용으로 같은 값을 한 행 더 넣는다.
        con.execute(
            """insert into pop (시도명, 시군구명, 인구, 기준연월)
               select 시도명, '세종특별자치시', 인구, 기준연월
               from pop where 시군구명='전체' and 시도명='세종특별자치시'
                 and not exists (select 1 from pop p2
                                  where p2.시도명='세종특별자치시'
                                    and p2.시군구명='세종특별자치시')"""
        )
        out["pop_meta"] = _rows(con, "select distinct 기준연월 from pop")[:1]
        joined = """
            from shop s join pop p on p.시도명=s.시도명 and p.시군구명=s.시군구명
            where p.인구 > 0
        """
        out["sgg_density"] = _rows(
            con,
            f"""select s.시도명 sido, s.시군구명 sgg, count(*) n, p.인구 pop,
                   round(1000.0*count(*)/p.인구,1) per1k,
                   round(10000.0*sum(case when 상권업종소분류명='카페' then 1 else 0 end)/p.인구,1) cafe10k,
                   round(10000.0*sum(case when 상권업종소분류명='편의점' then 1 else 0 end)/p.인구,1) cvs10k,
                   round(10000.0*sum(case when 상권업종소분류명='미용실' then 1 else 0 end)/p.인구,1) hair10k,
                   round(10000.0*sum(case when 상권업종소분류명='입시·교과학원' then 1 else 0 end)/p.인구,1) hagwon10k,
                   round(10000.0*sum(case when 상권업종소분류명='부동산 중개/대리업' then 1 else 0 end)/p.인구,1) realty10k
                {joined} group by 1,2,4 order by 5 desc""",
        )
        out["sido_density"] = _rows(
            con,
            f"""select s.시도명 sido, count(*) n, p.인구 pop,
                   round(1000.0*count(*)/p.인구,1) per1k
                from shop s join pop p on p.시도명=s.시도명 and p.시군구명='전체'
                where p.인구 > 0 group by 1,3 order by 4 desc""",
        )
        nat = con.execute(
            f"""select round(1000.0*count(*) / (select sum(인구) from pop where 시군구명='전체'), 1)
                from shop s join pop p on p.시도명=s.시도명 and p.시군구명=s.시군구명
                where p.인구 > 0"""
        ).fetchone()
        out["national_per1k"] = nat[0] if nat else None
        print(f"[duckdb] 인구 결합 완료 (기준 {out.get('pop_meta')})", flush=True)
    else:
        print("[duckdb] 인구 CSV 없음 → 밀도 지표 스킵", flush=True)

    # ---- 11. 시계열 ------------------------------------------------------------------
    # 분기별 전체 업소수/대분류만 남긴다. 첫 실행 이후 분기마다 1줄씩 쌓인다.
    history = []
    hist_path = out_dir / "history.json"
    if hist_path.exists():
        try:
            history = json.loads(hist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            history = []
    entry = {
        "version": version,
        "date": out["meta"]["updated"],
        "total": total,
        "major": {r["k"]: r["n"] for r in out["major"]},
        "sido": {r["sido"]: r["n"] for r in out["sido"]},
    }
    history = [h for h in history if h.get("version") != version]
    history.append(entry)
    history.sort(key=lambda h: h["version"])
    hist_path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")
    out["history"] = history

    # ---- 저장 -------------------------------------------------------------------------
    payload = json.dumps(out, ensure_ascii=False, default=float, separators=(",", ":"))
    (out_dir / "dashboard.json").write_text(payload, encoding="utf-8")
    size_mb = len(payload.encode("utf-8")) / 1e6
    print(f"[done] dashboard.json {size_mb:.2f} MB (총 {time.time() - t0:.1f}s)", flush=True)
    if size_mb > 8:
        print(f"[warn] JSON 이 {size_mb:.1f}MB 다. grid 해상도를 0.02 → 0.04 로 키우거나 "
              f"dong/small 항목을 줄이자.", flush=True)

    con.close()
    return out


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--csv-dir", default="data/raw/csv")
    p.add_argument("--pop", default="data/raw/population_sgg.csv")
    p.add_argument("--out", default="data")
    p.add_argument("--version", required=True)
    a = p.parse_args()

    build(
        Path(a.csv_dir),
        Path(a.pop) if Path(a.pop).exists() else None,
        Path(a.out),
        a.version,
    )