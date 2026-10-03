"""공공데이터포털에서 상가(상권)정보 최신 분기 파일과 행정안전부 주민등록 인구를 내려받는다.

data.go.kr 파일데이터 다운로드는 2단계다.
  1) POST /tcs/dss/selectFileDataDownload.do -> atchFileId, fileDetailSn 확보
  2) GET  /cmm/cmm/fileDownload.do?...&dataNm=<데이터명> -> ZIP 바이너리

dataNm 파라미터가 없으면 0바이트가 돌아온다. 반드시 붙일 것.
"""

from __future__ import annotations

import re
import sys
import time
import zipfile
from pathlib import Path

import requests

BASE = "https://www.data.go.kr"
DETAIL_PAGE = f"{BASE}/data/15083033/fileData.do"
MOIS = "https://jumin.mois.go.kr/statMonth.do"
# 엑셀 내보내기 전용 엔드포인트. statMonth.do 에는 시도 수준만 나온다.
# xlsStats: 1=시도, 2=시군구, 3=읍면동
MOIS_XLS = "https://jumin.mois.go.kr/downloadExcel2.do?searchYearMonth=month&xlsStats=2"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 파일데이터 PK는 변하지 않고, publicDataDetailPk 는 분기마다 바뀐다.
PUBLIC_DATA_PK = "15083033"


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
    return s


def fetch_detail_page(session: requests.Session) -> tuple[str, str]:
    """최신 분기의 (publicDataDetailPk, dataNm) 을 상세 페이지에서 파싱한다."""
    r = session.get(DETAIL_PAGE, timeout=60)
    r.raise_for_status()
    html = r.text

    # 다운로드 버튼 onclick: fn_fileDataDown('<pk>', '<detailPk>', '<dataNm>', ...)
    calls = re.findall(
        r"fn_fileDataDown\(\s*'%s'\s*,\s*'([^']*)'\s*,\s*'([^']*)'" % PUBLIC_DATA_PK, html
    )
    detail_pk = next((p for p, _ in calls if p.strip()), None)
    if not detail_pk:
        raise RuntimeError("상세 페이지에서 publicDataDetailPk 를 찾지 못했다. 사이트 구조 확인 필요.")

    # 데이터명은 onclick 세 번째 인자 우선, 없으면 페이지 제목에서 분기 추출
    name = next((n for _, n in calls if n.strip()), None)
    if not name:
        m3 = re.search(r"소상공인시장진흥공단_상가\(상권\)정보_(\d{8})", html)
        if m3:
            name = f"소상공인시장진흥공단_상가(상권)정보_{m3.group(1)}"

    if not name:
        raise RuntimeError("데이터명을 찾지 못했다.")

    return detail_pk, name


def resolve_download(session: requests.Session, detail_pk: str) -> tuple[str, str, str]:
    """1단계: atchFileId / fileDetailSn 확보. (atchFileId, fileDetailSn, dataNm) 반환."""
    r = session.post(
        f"{BASE}/tcs/dss/selectFileDataDownload.do?recommendDataYn=Y",
        data={"publicDataPk": PUBLIC_DATA_PK, "publicDataDetailPk": detail_pk},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": DETAIL_PAGE,
        },
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    if not payload.get("status"):
        raise RuntimeError(f"다운로드 준비 실패: {payload}")

    info = payload.get("dataSetFileDetailInfo") or {}
    data_nm = info.get("dataNm") or ""
    return payload["atchFileId"], str(payload["fileDetailSn"]), data_nm


def download_zip(dest: Path, session: requests.Session | None = None) -> tuple[str, Path]:
    """최신 분기 ZIP 을 dest 로 내려받고 (데이터명, 경로) 을 반환한다.

    이미 dest 에 같은 버전의 파일이 있으면 네트워크를 쓰지 않는다.
    """
    dest.mkdir(parents=True, exist_ok=True)
    session = session or make_session()

    detail_pk, page_name = fetch_detail_page(session)
    atch_id, detail_sn, api_name = resolve_download(session, detail_pk)
    data_nm = api_name or page_name

    version = extract_version(data_nm)
    target = dest / f"상가(상권)정보_{version}.zip"
    if target.exists() and target.stat().st_size > 100_000_000:
        print(f"[상가] 이미 받음: {target.name} ({target.stat().st_size:,} bytes)", flush=True)
        return data_nm, target

    url = (
        f"{BASE}/cmm/cmm/fileDownload.do"
        f"?atchFileId={atch_id}&fileDetailSn={detail_sn}&dataNm={requests.utils.quote(data_nm)}"
    )
    print(f"[상가] 다운로드 시작: {data_nm}", flush=True)
    t0 = time.time()

    # 353MB 이므로 스트리밍으로 받는다.
    with session.get(url, headers={"Referer": DETAIL_PAGE}, stream=True, timeout=900) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        got = 0
        last_report = 0.0
        with open(target, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                now = time.time()
                if now - last_report > 10:
                    pct = f"{got / total * 100:5.1f}%" if total else "  ?  "
                    speed = got / max(now - t0, 1) / 1e6
                    print(f"  {pct} {got:,} bytes  {speed:.1f} MB/s", flush=True)
                    last_report = now

    if got < 100_000_000:
        raise RuntimeError(
            f"다운로드가 너무 작다 ({got:,} bytes). dataNm 파라미터 또는 봇 차단 여부를 확인하자."
        )

    with open(target, "rb") as f:
        if f.read(4) != b"PK\x03\x04":
            raise RuntimeError("ZIP 시그니처가 아니다. HTML이 내려왔을 수 있다.")

    print(f"[상가] 완료: {target.name} ({got:,} bytes, {time.time() - t0:.0f}s)", flush=True)
    return data_nm, target


def download_population(dest: Path, as_of: str | None = None) -> Path | None:
    """행정안전부 주민등록 인구(시군구) Excel 을 내려받는다.

    as_of 는 'YYYY-MM'. None 이면 서버 기본값(최신 월)을 쓴다.
    실패해도 파이프라인이 죽지 않게 None 을 반환한다(인구 결합은 선택 사항).
    """
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / "population_sgg.xlsx"
    session = make_session()

    session.get(MOIS, timeout=60)  # 세션 쿠키 확보

    # 연월을 안 보내면 서버가 축약된 응답(1KB 안팎)을 돌려준다.
    # 또, 매월 말일자 통계를 집계하므로 이번 달 값은 아직 없을 수 있다.
    # 그래서 최근 달부터 거꾸로 시도해 실제 행이 있는 첫 달을 쓴다.
    candidates = []
    if as_of:
        candidates = [as_of]
    else:
        today = time.localtime()
        y, m = today.tm_year, today.tm_mon
        for back in range(0, 4):
            mm = m - back
            yy = y
            while mm <= 0:
                mm += 12
                yy -= 1
            candidates.append(f"{yy:04d}-{mm:02d}")

    last_err = ""
    for as_of_try in candidates:
        year, month = as_of_try.split("-")
        data = {
            "sltOrgType": "1",
            "sltOrgLvl1": "A",
            "sltOrgLvl2": "",
            "gender": "gender",
            "genderPer": "genderPer",
            "generation": "generation",
            "sltUndefType": "",
            "sltOrderType": "1",
            "sltOrderValue": "ASC",
            "category": "month",
            "state": "2",  # 전체시군구현황
            "searchYearStart": year,
            "searchMonthStart": month,
            "searchYearEnd": year,
            "searchMonthEnd": month,
        }

        try:
            r = session.post(MOIS_XLS, data=data, timeout=300)
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            continue

        # 헤더만 있는 파일(아직 미발행)이면 다음 달로 넘어간다
        if len(r.content) < 8000:
            last_err = f"{as_of_try}: {len(r.content)} bytes (데이터 미발행)"
            print(f"[인구] {as_of_try} 은 아직 없음, 이전 달 시도", flush=True)
            continue

        out.write_bytes(r.content)
        print(f"[인구] 받음: {as_of_try} / {len(r.content):,} bytes -> {out.name}", flush=True)
        return out

    print(
        f"[인구] 최근 4개월 중 사용 가능한 통계가 없다(파이프라인 계속): {last_err}",
        file=sys.stderr,
        flush=True,
    )
    return None


def extract_version(data_nm: str) -> str:
    m = re.search(r"(\d{8})", data_nm)
    if not m:
        raise RuntimeError(f"데이터명에서 분기(YYYYMMDD)를 못 찾았다: {data_nm}")
    return m.group(1)


def safe_unzip(zip_path: Path, dest: Path) -> list[Path]:
    """파일명이 CP949 로 저장된 ZIP 을 UTF-8 이름으로 풀어낸다."""
    dest.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            # zipfile 은 한글이 CP437 로 잘못 해석된다. 원래 CP949 로 되돌린다.
            try:
                name = name.encode("cp437").decode("cp949")
            except (UnicodeEncodeError, UnicodeDecodeError):
                try:
                    name = name.encode("cp437").decode("utf-8")
                except Exception:  # noqa: BLE001
                    pass
            info.filename = name
            target = dest / Path(name).name
            with zf.open(info) as src, open(target, "wb") as dst:
                dst.write(src.read())
            out.append(target)
            print(f"  unpack: {target.name}", flush=True)
    return out


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    data_dir = root / "data"
    raw = data_dir / "raw"

    nm, zp = download_zip(raw / "zip")
    print(f"version = {extract_version(nm)}")
    csvs = safe_unzip(zp, raw / "csv")
    print(f"\nCSV {len(csvs)}개 준비 완료: {raw / 'csv'}")