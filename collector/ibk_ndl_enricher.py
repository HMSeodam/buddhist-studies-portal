#!/usr/bin/env python3
# ibk_ndl_enricher.py  v3
# ──────────────────────────────────────────────────────────────────────────────
# 변경 내역 (v3):
#   - 쿼리 형식: anywhere = "{doi}"  (v2의 dc.identifier는 syntax error)
#   - recordSchema: dcndl (NDL 확장 스키마 → rdf:about에 URL 포함)
#   - URL 추출: HTML 이스케이프된 응답에서 정규식으로 직접 추출
#     패턴: https://ndlsearch.ndl.go.jp/books/R000000004-I{숫자}
#
# 사용법:
#   python ibk_ndl_enricher.py --rollback   # 잘못 저장된 ndl_url 제거
#   python ibk_ndl_enricher.py --limit 50   # 50건 테스트
#   python ibk_ndl_enricher.py --year 2020  # 특정 연도만
#   python ibk_ndl_enricher.py              # 전체 (~3~4시간)
# ──────────────────────────────────────────────────────────────────────────────

import json, time, re, argparse
import urllib.request, urllib.parse
from pathlib import Path
from datetime import datetime
from collections import Counter

OUTPUT_DIR  = "../output"   # collector/ 에서 실행 기준
OUTPUT_FILE = "ibk_印度學佛教學研究.json"
DELAY       = 1.0           # API 호출 간격(초)
SAVE_EVERY  = 200           # N건마다 중간 저장
DUP_THRESHOLD = 3           # 같은 URL 이 횟수 이상 → 오매칭 판단

NDL_SRU = "https://ndlsearch.ndl.go.jp/api/sru"

# ndlsearch URL 패턴 (HTML 이스케이프 응답에도 그대로 포함됨)
NDL_URL_RE = re.compile(r'https://ndlsearch\.ndl\.go\.jp/books/R\d+-I\d+')


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _get(url, timeout=12):
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (IBK-NDL-Enricher/3.0)"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except Exception as e:
        return None, str(e)


# ── NDL SRU 검색 ──────────────────────────────────────────────────────────────

def search_ndl(doi):
    """
    DOI → NDL SRU 검색 → ndl_url 반환 (없으면 None)

    작동 원리:
      1. anywhere = "{doi}" 쿼리 (유일하게 작동하는 형식)
      2. recordSchema=dcndl → 응답에 rdf:about URL 포함
      3. 응답 텍스트에서 정규식으로 ndlsearch URL 추출
    """
    if not doi:
        return None

    query = f'anywhere = "{doi}"'
    params = urllib.parse.urlencode({
        "operation":      "searchRetrieve",
        "version":        "1.2",
        "query":          query,
        "maximumRecords": "1",
        "recordSchema":   "dcndl",
    })
    _, text = _get(f"{NDL_SRU}?{params}")
    if not text:
        return None

    # numberOfRecords 확인
    m = re.search(r"<numberOfRecords>(\d+)</numberOfRecords>", text)
    if not m or m.group(1) == "0":
        return None

    # ndlsearch URL 추출 (HTML 이스케이프된 응답에서도 URL 자체는 그대로 있음)
    m2 = NDL_URL_RE.search(text)
    if m2:
        # #material, #item 같은 fragment 제거
        url = m2.group(0).split("#")[0]
        return url

    return None


# ── 저장 ──────────────────────────────────────────────────────────────────────

def _save(data, articles):
    by_year = {}
    for a in articles:
        y = a.get("year", "미상")
        v = a.get("volume", "")
        n = a.get("issue",  "미상")
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
    print(f"  💾 저장 ({out.stat().st_size / 1024 / 1024:.1f} MB)")


# ── 롤백 (v1/v2 오매칭 복구) ─────────────────────────────────────────────────

def rollback():
    path = Path(OUTPUT_DIR) / OUTPUT_FILE
    if not path.exists():
        print(f"❌ {OUTPUT_FILE} 없음"); return

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    arts = data.get("articles", [])

    url_count = Counter(
        a["ndl_url"] for a in arts
        if a.get("ndl_url") and a.get("source") != "NDL"
    )
    dup_urls = {u for u, c in url_count.items() if c >= DUP_THRESHOLD}

    if not dup_urls:
        print("중복 URL 없음 — 롤백 불필요"); return

    print(f"중복 URL {len(dup_urls)}개 발견:")
    for u in sorted(dup_urls):
        print(f"  {u}  ({url_count[u]}건)")

    removed = 0
    for a in arts:
        if a.get("ndl_url") in dup_urls and a.get("source") != "NDL":
            a["ndl_url"] = ""
            removed += 1

    _save(data, arts)
    print(f"\n✅ {removed}건 ndl_url 초기화 완료")


# ── 메인 ──────────────────────────────────────────────────────────────────────

def main(limit=None, year_filter=None):
    path = Path(OUTPUT_DIR) / OUTPUT_FILE
    if not path.exists():
        print(f"❌ {OUTPUT_FILE} 없음"); return

    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    articles = data.get("articles", [])

    # 처리 대상: jstage_url 있고 ndl_url 비어있는 것
    targets = [a for a in articles if a.get("jstage_url") and not a.get("ndl_url")]
    if year_filter:
        targets = [a for a in targets if a.get("year") == str(year_filter)]
    if limit:
        targets = targets[:limit]

    total = len(targets)
    if total == 0:
        print("처리할 항목 없음."); return

    est = total * DELAY / 60
    print(f"처리 대상: {total}건  (딜레이 {DELAY}초, 예상 {est:.0f}분)")
    print()

    art_map  = {a["article_id"]: a for a in articles}
    url_seen = Counter()
    found = not_found = errors = dup_skip = 0

    for i, art in enumerate(targets, 1):
        label = art.get("title_ja", "")[:28]
        doi   = art.get("doi", "")
        print(f"[{i}/{total}] {label}… ", end="", flush=True)

        try:
            url = search_ndl(doi)
        except Exception as e:
            errors += 1
            print(f"⚠ 오류: {e}")
            time.sleep(DELAY)
            continue

        if url:
            url_seen[url] += 1
            if url_seen[url] >= DUP_THRESHOLD:
                dup_skip += 1
                print(f"⚠ 중복 URL 감지 → 오매칭 가능성, 저장 안 함")
                # 이미 저장된 동일 URL 제거
                if url_seen[url] == DUP_THRESHOLD:
                    for a in articles:
                        if a.get("ndl_url") == url and a.get("source") != "NDL":
                            a["ndl_url"] = ""
            else:
                art_map[art["article_id"]]["ndl_url"] = url
                found += 1
                print(f"✅ {url}")
        else:
            not_found += 1
            print("—")

        # 중간 저장
        if i % SAVE_EVERY == 0:
            _save(data, articles)
            print(f"  진행 {i}/{total} | 발견 {found} | 없음 {not_found} | "
                  f"중복skip {dup_skip} | 오류 {errors}")

        time.sleep(DELAY)

    _save(data, articles)
    print()
    print("=" * 55)
    print(f"완료: {total}건 처리")
    print(f"  NDL URL 추가:      {found}건")
    print(f"  NDL 미색인:        {not_found}건")
    print(f"  중복 감지(오매칭): {dup_skip}건")
    print(f"  API 오류:          {errors}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="IBK 논문 NDL URL 추가 v3")
    parser.add_argument("--rollback", action="store_true",
                        help="오매칭 ndl_url 일괄 제거")
    parser.add_argument("--limit",   type=int,   default=None,
                        help="처리 건수 제한 (테스트용)")
    parser.add_argument("--year",    type=int,   default=None,
                        help="특정 연도만 처리")
    parser.add_argument("--delay",   type=float, default=DELAY,
                        help=f"API 호출 간격 초 (기본값: {DELAY})")
    args = parser.parse_args()

    if args.rollback:
        rollback()
    else:
        DELAY = args.delay
        main(limit=args.limit, year_filter=args.year)
