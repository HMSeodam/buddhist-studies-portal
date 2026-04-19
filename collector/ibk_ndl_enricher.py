#!/usr/bin/env python3
# ibk_ndl_enricher.py
# ──────────────────────────────────────────────────────────────────────────────
# 목적: ibk_印度學佛教學研究.json 에서 ndl_url 이 없는 J-Stage 논문에
#       NDL Search URL을 추가한다.
#
# 동작:
#   1. DOI로 NDL OpenSearch API 검색
#   2. 결과가 있으면 직접 레코드 URL(ndl_url) 저장
#   3. 결과가 없으면 제목으로 재검색
#   4. 중간 결과를 OUTPUT_FILE에 주기적으로 저장 (중단 후 재시작 가능)
#
# 사용법:
#   python ibk_ndl_enricher.py              # 전체 실행
#   python ibk_ndl_enricher.py --limit 500  # 500건만 처리 (테스트용)
#   python ibk_ndl_enricher.py --year 2020  # 특정 연도만 처리
#
# 소요 시간 (약 14,700건 기준):
#   DELAY=0.8초 → 약 3시간
#   NDL이 색인하지 않은 논문(주로 1950~1990년대 일부)은 "없음" 처리
# ──────────────────────────────────────────────────────────────────────────────

import json, time, re, argparse
import urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime

OUTPUT_DIR  = "./output"
OUTPUT_FILE = "ibk_印度學佛教學研究.json"
DELAY       = 0.8   # API 호출 간격 (초) — NDL 서버 부하 배려
SAVE_EVERY  = 200   # N건마다 중간 저장
NOT_FOUND   = "__NOT_FOUND__"  # 검색 결과 없음 마커

# NDL OpenSearch API
# q 파라미터에 DOI 또는 제목을 넣으면 RSS XML 반환
# 참고: https://ndlsearch.ndl.go.jp/help/api/index.html
NDL_SEARCH = "https://ndlsearch.ndl.go.jp/api/opensearch"

# NDL SRU API (DOI 정밀 검색용 — 실패 시 OpenSearch로 fallback)
NDL_SRU    = "https://ndlsearch.ndl.go.jp/api/sru"


# ── HTTP 유틸 ──────────────────────────────────────────────────────────────────

def _get(url, timeout=10):
    """GET 요청 → (status, text) / 오류 시 (None, None)"""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; IBK-NDL-Enricher/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None, str(e)


# ── NDL 검색 함수 ──────────────────────────────────────────────────────────────

def _parse_opensearch(xml_text):
    """
    NDL OpenSearch RSS 응답에서 첫 번째 결과 URL 추출
    반환: ndl_url (str) 또는 None
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    # totalResults 확인
    ns_os = "http://a9.com/-/spec/opensearch/1.1/"
    total_el = root.find(f".//{{{ns_os}}}totalResults")
    if total_el is not None and total_el.text == "0":
        return None

    # 첫 번째 item의 link 추출
    for item in root.findall(".//item"):
        link_el = item.find("link")
        if link_el is not None and link_el.text:
            url = link_el.text.strip()
            if "ndlsearch.ndl.go.jp" in url:
                return url
    return None


def search_by_doi(doi):
    """DOI로 NDL 검색 → ndl_url 또는 None"""
    params = urllib.parse.urlencode({"q": doi, "media": "1", "cnt": "1"})
    status, text = _get(f"{NDL_SEARCH}?{params}")
    if not text or status not in (200, None):
        return None
    return _parse_opensearch(text)


def search_by_title(title_ja):
    """일본어 제목으로 NDL 검색 → ndl_url 또는 None"""
    if not title_ja or len(title_ja) < 4:
        return None
    # 제목 + 학술논문 필터
    params = urllib.parse.urlencode({
        "q": title_ja,
        "media": "1",
        "cnt": "1",
        "f-dn": "article"  # 논문류로 범위 한정
    })
    status, text = _get(f"{NDL_SEARCH}?{params}")
    if not text or status not in (200, None):
        return None
    return _parse_opensearch(text)


def fetch_ndl_url(art):
    """
    논문 1건에 대해 NDL URL을 가져온다.
    우선순위: DOI 검색 → 제목 검색
    반환: ndl_url (str), NOT_FOUND ("__NOT_FOUND__"), 또는 None (API 오류)
    """
    doi = art.get("doi", "")
    title = art.get("title_ja", "")

    # 1) DOI 검색
    if doi:
        result = search_by_doi(doi)
        if result:
            return result
        # DOI 검색에서 결과가 없으면 제목으로 재시도
        time.sleep(DELAY * 0.5)

    # 2) 제목 검색
    if title:
        result = search_by_title(title)
        if result:
            return result

    return NOT_FOUND


# ── 메인 ──────────────────────────────────────────────────────────────────────

def load_data():
    path = Path(OUTPUT_DIR) / OUTPUT_FILE
    if not path.exists():
        print(f"❌ {OUTPUT_FILE} 없음")
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_data(data, articles):
    """by_year 재구성 후 저장"""
    by_year = {}
    for a in articles:
        y = a.get("year", "미상")
        v = a.get("volume", "")
        n = a.get("issue", "미상")
        k = f"{v}권 {n}호" if v else f"{n}호"
        by_year.setdefault(y, {}).setdefault(k, []).append(a)

    data["articles"]        = articles
    data["by_year"]         = by_year
    data["total_collected"] = len(articles)
    data.setdefault("meta", {})["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    data["meta"]["total_articles"] = len(articles)

    out = Path(OUTPUT_DIR) / OUTPUT_FILE
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    sz = out.stat().st_size / 1024 / 1024
    print(f"  💾 저장: {out} ({sz:.1f}MB)")


def main(limit=None, year_filter=None):
    data = load_data()
    if not data:
        return

    articles = data.get("articles", [])

    # 처리 대상: jstage_url 있고 ndl_url 없는 것
    # (NOT_FOUND 마커가 있는 것도 재처리하려면 아래 조건 수정)
    targets = [
        a for a in articles
        if a.get("jstage_url")
        and not a.get("ndl_url")
        # 이미 검색 완료된 것은 건너뜀
        # NOT_FOUND 재시도하려면 아래 줄 주석 처리
        and a.get("ndl_url") != NOT_FOUND
    ]

    # 연도 필터
    if year_filter:
        targets = [a for a in targets if a.get("year") == str(year_filter)]

    # 건수 제한 (테스트용)
    if limit:
        targets = targets[:limit]

    total = len(targets)
    print(f"처리 대상: {total}건")
    if total == 0:
        print("추가할 항목 없음.")
        return

    # 예상 소요 시간
    est_min = total * DELAY / 60
    print(f"예상 소요 시간: 약 {est_min:.0f}분 (딜레이 {DELAY}초 기준)")
    print()

    found = 0
    not_found = 0
    errors = 0

    # article_id → 인덱스 맵 (빠른 업데이트용)
    art_map = {a["article_id"]: a for a in articles}

    for i, art in enumerate(targets, 1):
        art_id = art["article_id"]
        doi    = art.get("doi", "")
        title  = art.get("title_ja", "")[:30]

        print(f"[{i}/{total}] {title}… ", end="", flush=True)

        result = fetch_ndl_url(art)

        if result and result != NOT_FOUND:
            art_map[art_id]["ndl_url"] = result
            found += 1
            print(f"✅ {result}")
        elif result == NOT_FOUND:
            # NDL에 없는 논문 — 빈 문자열 유지 (마커 저장 원하면 NOT_FOUND 대입)
            not_found += 1
            print("—")
        else:
            errors += 1
            print("⚠ API 오류")

        # 중간 저장
        if i % SAVE_EVERY == 0:
            save_data(data, articles)
            print(f"  진행: {i}/{total} | 발견 {found} | 없음 {not_found} | 오류 {errors}")

        time.sleep(DELAY)

    # 최종 저장
    save_data(data, articles)

    print()
    print("=" * 50)
    print(f"완료: 총 {total}건 처리")
    print(f"  NDL URL 추가:   {found}건")
    print(f"  NDL 미색인:     {not_found}건")
    print(f"  API 오류:       {errors}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IBK 논문 NDL URL 일괄 추가")
    parser.add_argument("--limit", type=int, default=None,
                        help="처리 건수 제한 (테스트용, 예: 50)")
    parser.add_argument("--year",  type=int, default=None,
                        help="특정 연도만 처리 (예: 2020)")
    parser.add_argument("--delay", type=float, default=DELAY,
                        help=f"API 호출 간격 초 (기본값: {DELAY})")
    args = parser.parse_args()

    DELAY = args.delay
    main(limit=args.limit, year_filter=args.year)
