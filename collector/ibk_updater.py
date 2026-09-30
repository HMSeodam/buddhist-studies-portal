#!/usr/bin/env python3
# ibk_updater.py — 印度學佛教學研究 증분 업데이트
#
# 동작:
#   1. 기존 ibk_印度學佛教學研究.json 로드
#   2. J-Stage 최신 2호 확인 → 새 논문만 상세 수집
#   3. 기존 논문 중 초록/저자 없는 것 보충 (J-Stage에 데이터 추가된 경우)
#   4. 저장
#
# 사용법:
#   python ibk_updater.py
#   python ibk_updater.py --depth 3  # 최신 3호 확인

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import json, time, re
from pathlib import Path
from datetime import datetime
import argparse, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_log import record_update   # noqa: E402

OUTPUT_DIR  = "../output"
OUTPUT_FILE = "ibk_印度學佛教學研究.json"
JSTAGE_BASE = "https://www.jstage.jst.go.jp"
JSTAGE_LIST = f"{JSTAGE_BASE}/browse/ibk/list/-char/ja"
DELAY       = 2.5


def init_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--lang=ja")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=opts
    )


def get_soup(driver, url, wait_sec=4):
    driver.get(url)
    time.sleep(wait_sec)
    return BeautifulSoup(driver.page_source, "html.parser")


# ── 부제가 저자 칸에 들어간 경우 (J-Stage 목록 화면의 구조 때문) ──────────────
#   예) 제목 '起信論研究の問題点' · 저자 '実叉難陀訳の性格をめぐって'  (실제 저자: 高崎直道)
_SUB_PART = re.compile(r"[のをにてと「」『』〔〕]|より|から|など|おける|於ける|ついて|として|める|みる|考察|研究|試論|序説|一考|中心|論$")
_NAME_WITH_ALIAS = re.compile(r"^[一-鿿〓]{2,5}[（(][^)）]{1,20}[)）]$")        # 金錫岩(花郎), 韓普光(泰植)


def looks_like_subtitle(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    if re.match(r"^[―─\-－]{1,2}", n):                  # '――チベット撰述文献の理解から――'
        return True
    if re.search(r"[A-Za-z]", n) or re.search(r"\s", n) or _NAME_WITH_ALIAS.match(n):
        return False                                    # 'Richard A. Gard', '井ノ口 泰淳', '白土 わか'
    return len(n) >= 5 and bool(_SUB_PART.search(n))


def fix_subtitle_authors(arts) -> int:
    """저자 칸의 부제를 제목 뒤로 옮기고, 진짜 저자는 상세 페이지에서 다시 받도록 표시. 반환: 고친 수"""
    fixed = 0
    for a in arts:
        aus = a.get("authors") or []
        subs = [x for x in aus if looks_like_subtitle(x.get("name", ""))]
        if not subs:
            continue
        sub = re.sub(r"^[―─\-－\s]+|[―─\-－\s]+$", "", subs[0]["name"])
        t = a.get("title_ja") or a.get("title_kr") or ""
        if sub and sub not in t:
            t = f"{t} ―{sub}―"
            a["title_ja"] = a["title_kr"] = t
        keep = [x for x in aus if x not in subs]
        for i, x in enumerate(keep):
            x["order"] = str(i + 1)
        a["authors"] = keep
        a["needs_author"] = True
        fixed += 1
    return fixed


def parse_page_authors(soup):
    """J-Stage 논문 페이지의 저자(일본어 표기 우선).
    메타데이터(citation_author)는 로마자('Jikido Takasaki')라서, 본문의 저자 검색 링크(item=8)에서 일본어 이름을 읽고
    메타데이터의 저자 수만큼만 앞에서부터 취한다 (관련 논문 목록의 다른 저자를 섞지 않도록)."""
    meta = [re.sub(r"\s+", " ", (m.get("content") or "")).strip()
            for m in soup.find_all("meta", attrs={"name": "citation_author"})]
    meta = [x for x in meta if x]
    links = []
    for a in soup.select("a[href*='item=8']"):
        nm = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        if nm and 2 <= len(nm) <= 30 and not looks_like_subtitle(nm) and nm not in links:
            links.append(nm)
    n = len(meta) or 1
    names = links[:n] if links else meta
    return names


def fetch_meta_authors(url: str):
    """J-Stage 논문 페이지의 서지 메타데이터(citation_author 등)에서 저자·부제를 읽음 (브라우저 없이)."""
    import requests
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                                   "Accept-Language": "ja,en;q=0.8"})
        if r.status_code != 200:
            return None
    except Exception:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    names = parse_page_authors(soup)
    title = ""
    m = soup.find("meta", attrs={"name": "citation_title"})
    if m:
        title = (m.get("content") or "").strip()
    return {"authors": names, "title": title}


def repair_authors(arts, minutes: float) -> int:
    """저자가 비었거나 부제로 표시됐던 논문의 저자를 J-Stage 메타데이터로 채움 (시간 예산 안에서)."""
    todo = [a for a in arts if a.get("jstage_url") and (a.get("needs_author") or not a.get("authors"))]
    if not todo or minutes <= 0:
        return 0
    print(f"\n저자 보충 대상 {len(todo)}편 (최대 {minutes:.0f}분)")
    end = time.time() + minutes * 60
    done = 0
    for a in todo:
        if time.time() > end:
            break
        info = fetch_meta_authors(a["jstage_url"])
        time.sleep(1.2)
        if not info:
            continue
        if info["authors"]:
            a["authors"] = [{"name": n, "affiliation": "", "order": str(i + 1)} for i, n in enumerate(info["authors"])]
            a.pop("needs_author", None)
            done += 1
            if done <= 10:
                print(f"  ✓ {a.get('title_ja','')[:30]} → {', '.join(info['authors'])}")
    print(f"  → {done}편 저자 보충 (남은 대상 {len(todo) - done}편은 다음 실행에서)")
    return done


def infer_year(vol):
    try: return str(1952 + int(vol) - 1)
    except: return ""


def load_existing():
    path = Path(OUTPUT_DIR) / OUTPUT_FILE
    if not path.exists():
        print(f"⚠ {OUTPUT_FILE} 없음 → ibk_crawler.py 먼저 실행하세요")
        return None, set(), set()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    arts = data.get("articles", [])
    existing_ids     = {a["article_id"] for a in arts}
    incomplete_ids   = {a["article_id"] for a in arts
                        if not a.get("abstract_ja") and a.get("jstage_url")}
    print(f"기존: {len(arts)}편 (초록 없음: {len(incomplete_ids)}편)")
    return data, existing_ids, incomplete_ids


def get_recent_issues(driver, depth=2):
    soup = get_soup(driver, JSTAGE_LIST, wait_sec=5)
    issues = []
    seen   = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/browse/ibk/(\d+)/(\d+)/_contents", href)
        if not m: continue
        key = f"{m.group(1)}_{m.group(2)}"
        if key in seen: continue
        seen.add(key)
        vol, iss = m.group(1), m.group(2)
        base_url = f"{JSTAGE_BASE}/browse/ibk/{vol}/{iss}/_contents/-char/ja"
        issues.append({
            "volume": vol, "issue": iss,
            "base_url": base_url,
            "year":   infer_year(vol),
        })
    issues.sort(key=lambda x: (int(x["volume"]), int(x["issue"])))
    return issues[-depth:]  # 최신 N호


def _parse_jstage_page(soup, vol, iss, year, seen: set) -> list[dict]:
    """crawler v3와 동일한 셀렉터 기반 파싱"""
    arts = []
    for li in soup.find_all("li"):
        title_div = li.select_one(".searchlist-title")
        if not title_div:
            continue
        title_a = title_div.select_one("a[href*='/article/ibk']")
        if not title_a:
            continue
        href = title_a.get("href", "")
        m = re.search(r"/article/(ibk\w*)/(\d+)/(\d+)/([\w_]+)/_article", href)
        if not m:
            continue
        page_id = m.group(4)
        if page_id in seen:
            continue
        seen.add(page_id)
        title = title_a.get_text(strip=True)
        if not title or len(title) < 2:
            continue

        # 저자
        authors = []
        au_div = li.select_one(".searchlist-authortags")
        if au_div:
            au_raw = au_div.get("title", "") or au_div.get_text(strip=True)
            for nm in re.split(r"[,、;]", au_raw):
                nm = nm.strip()
                if nm and 2 <= len(nm) <= 20 and not looks_like_subtitle(nm):
                    authors.append({"name": nm, "affiliation": "", "order": str(len(authors) + 1)})

        # 권호·페이지
        info_div = li.select_one(".searchlist-additional-info")
        info_txt = info_div.get_text(separator=" ", strip=True) if info_div else ""
        yr_m = re.search(r"(\d{4})\s*年", info_txt)
        art_year = yr_m.group(1) if yr_m else year
        pg_m = re.search(r"p\.\s*(\d+)\s*[-–]\s*(\d+)", info_txt)
        start_page = pg_m.group(1) if pg_m else ""
        end_page   = pg_m.group(2) if pg_m else ""

        # DOI
        doi = ""
        doi_a = li.select_one(".searchlist-doi a")
        if doi_a:
            doi_m = re.search(r"10\.\d+/\S+", doi_a.get("href", ""))
            if doi_m:
                doi = doi_m.group(0)
        if not doi and start_page:
            doi = f"10.4259/{m.group(1)}.{vol}.{iss}_{start_page}"

        # 목록 초록
        abs_txt = ""
        abs_div = li.select_one(".showabstractbox .inner-content p, .showabstractbox p")
        if abs_div:
            abs_txt = abs_div.get_text(strip=True)

        full_url = href if href.startswith("http") else JSTAGE_BASE + href
        arts.append({
            "article_id":  f"ibk_{vol}_{iss}_{page_id}",
            "title_ja":    title, "title_kr": title, "title_en": "",
            "authors":     authors,
            "abstract_ja": abs_txt, "abstract_en": "", "abstract_kr": abs_txt,
            "journal_name": "印度學佛教學研究",
            "year":        art_year, "volume": vol, "issue": iss,
            "start_page":  start_page, "end_page": end_page, "doi": doi,
            "jstage_url":  full_url, "ndl_url": "",
            "keywords_ja": [], "keywords_en": [], "keywords_kr": [],
            "ai_keywords": [], "source": "J-Stage",
        })
    return arts


def get_articles_list(driver, issue):
    """페이지네이션을 포함한 권호별 논문 수집 (crawler v3 방식)"""
    vol, iss, year = issue["volume"], issue["issue"], issue["year"]
    base_url = issue.get("base_url") or issue.get("url","")
    arts = []
    seen = set()
    page = 0

    while True:
        url  = f"{base_url}?from={page}"
        soup = get_soup(driver, url, wait_sec=3)
        new  = _parse_jstage_page(soup, vol, iss, year, seen)
        if not new:
            break
        arts.extend(new)
        has_next = any(
            f"?from={page + 1}" in (a.get("href", "") or "")
            for a in soup.find_all("a", href=True)
        )
        if not has_next:
            break
        page += 1
        time.sleep(2.5)

    return arts


def fetch_detail(driver, art):
    soup = get_soup(driver, art["jstage_url"], wait_sec=4)
    r = {}
    # 제목
    for sel in [".article-title", "h1.title", ".itemTitle"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) > 2:
                r["title_ja"] = t; r["title_kr"] = t; break
    # 영문 제목
    for sel in [".trans-title", ".title_en"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t: r["title_en"] = t; break
    # 저자 (서지 메타데이터가 가장 정확 — 없을 때만 화면 요소에서)
    authors = [{"name": nm, "affiliation": "", "order": str(i + 1)} for i, nm in enumerate(parse_page_authors(soup))]
    for sel in ([] if authors else [".contrib", ".author-list li", "[class*='author']"]):
        for el in soup.select(sel):
            nm = el.get_text(strip=True)
            nm = re.sub(r"\s+", " ", nm).strip()
            if nm and 2 <= len(nm) <= 30 and not looks_like_subtitle(nm) and nm not in [a["name"] for a in authors]:
                authors.append({"name": nm, "affiliation": "", "order": str(len(authors)+1)})
        if authors: break
    if authors: r["authors"] = authors
    # 초록
    for sel in ["#ja", ".abstract-ja", "[class*='abstract']"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) > 30:
                r["abstract_ja"] = t; r["abstract_kr"] = t; break
    for sel in ["#en", ".abstract-en", ".trans-abstract"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) > 30: r["abstract_en"] = t; break
    # 키워드
    kj, ke = [], []
    for sel in [".kwd-group", ".keywords", "[class*='keyword']"]:
        for el in soup.select(sel):
            for kw_el in el.find_all(["span", "li", "a"]):
                kw = kw_el.get_text(strip=True)
                if 2 <= len(kw) <= 40:
                    (ke if sum(c.isascii() for c in kw)/len(kw) > 0.7 else kj).append(kw)
        if kj or ke: break
    if kj: r["keywords_ja"] = kj; r["keywords_kr"] = kj
    if ke: r["keywords_en"] = ke
    return r


def save(data, articles):
    by_year = {}
    for a in articles:
        y = a.get("year","미상"); v = a.get("volume",""); n = a.get("issue","미상")
        k = f"{v}권 {n}호" if v else f"{n}호"
        by_year.setdefault(y,{}).setdefault(k,[]).append(a)

    data["articles"]        = articles
    data["by_year"]         = by_year
    data["total_collected"] = len(articles)
    data.setdefault("meta",{})["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    data["meta"]["total_articles"] = len(articles)

    out = Path(OUTPUT_DIR) / OUTPUT_FILE
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    sz = out.stat().st_size / 1024 / 1024
    print(f"저장: {out} ({sz:.1f}MB)")


def _normalize_title(t):
    """제목 정규화 — 공백·괄호·부호 제거 후 소문자"""
    import unicodedata
    t = unicodedata.normalize("NFKC", t or "")
    t = re.sub(r"[\s\u3000\u300c-\u300f『』「」【】（）()\[\]\-―:：・]", "", t)
    return t.lower()


def _remove_ndl_duplicates(arts: list) -> int:
    """
    J-Stage 논문과 제목이 같은 NDL 논문을 삭제하고 J-Stage로 대체.
    J-Stage가 더 풍부한 데이터(초록·키워드·DOI)를 가지므로 우선.
    반환: 삭제된 NDL 논문 수
    """
    # J-Stage 논문 제목 정규화 맵: norm_title → article
    jstage_titles = {}
    for a in arts:
        if a.get("source") == "J-Stage":
            nt = _normalize_title(a.get("title_ja",""))
            if nt:
                jstage_titles[nt] = a

    # NDL 논문 중 J-Stage와 제목 일치하는 것 찾기
    to_remove = []
    for a in arts:
        if a.get("source") != "NDL":
            continue
        nt = _normalize_title(a.get("title_ja",""))
        if nt and nt in jstage_titles:
            # J-Stage 논문에 NDL URL 이식 (NDL Search 링크 보존)
            jstage_art = jstage_titles[nt]
            if not jstage_art.get("ndl_url") and a.get("ndl_url"):
                jstage_art["ndl_url"] = a["ndl_url"]
            to_remove.append(a["article_id"])

    remove_set = set(to_remove)
    arts[:] = [a for a in arts if a["article_id"] not in remove_set]
    return len(to_remove)


def main(depth=2, repair_min=10.0):
    data, existing_ids, incomplete_ids = load_existing()
    if data is None:
        return
    n_fix = fix_subtitle_authors(data.get("articles", []))
    if n_fix:
        print(f"저자 칸의 부제를 제목으로 옮김: {n_fix}편")

    print("Chrome 초기화...")
    driver = init_driver()
    total_new = 0
    total_filled = 0

    try:
        arts = data.get("articles", [])
        arts_map = {a["article_id"]: a for a in arts}

        # 1) 최신 N호 확인 → 새 논문
        print(f"\n최신 {depth}호 확인 중...")
        recent = get_recent_issues(driver, depth)
        time.sleep(DELAY)

        for iss in recent:
            label = f"{iss['volume']}권 {iss['issue']}호"
            print(f"  [{label}] ", end="", flush=True)
            new_arts = get_articles_list(driver, iss)
            added = 0
            for a in new_arts:
                if a["article_id"] not in existing_ids:
                    # 상세 수집
                    d = fetch_detail(driver, a)
                    for k,v in d.items():
                        if v: a[k] = v
                    arts.append(a)
                    arts_map[a["article_id"]] = a
                    existing_ids.add(a["article_id"])
                    added += 1
                    time.sleep(DELAY)
            print(f"{added}편 신규")
            if added:
                try:
                    record_update(OUTPUT_DIR, "印度學佛教學研究", added,
                                  volume=iss.get("volume", ""), issue=iss.get("issue", ""),
                                  year=infer_year(iss.get("volume", "")), source="J-Stage")
                except Exception as e:
                    print(f"  ⚠ 공지 기록 실패: {e}")
            total_new += added
            time.sleep(DELAY)

        # 2) 기존 논문 중 초록 없는 것 보충
        if incomplete_ids:
            print(f"\n초록 보충 중 ({len(incomplete_ids)}편)...")
            filled = 0
            for art_id in list(incomplete_ids)[:50]:  # 한 번에 최대 50편
                art = arts_map.get(art_id)
                if not art or not art.get("jstage_url"):
                    continue
                d = fetch_detail(driver, art)
                if d.get("abstract_ja"):
                    for k,v in d.items():
                        if v: art[k] = v
                    filled += 1
                    print(f"  보충: {art.get('title_ja','')[:40]}")
                time.sleep(DELAY)
            total_filled = filled
            print(f"  → {filled}편 초록 보충 완료")

        # 3) 저자 보충 (부제가 저자로 들어갔던 논문, 저자 없는 논문)
        n_rep = repair_authors(arts, repair_min)

        # NDL→J-Stage 승격: 같은 논문이 NDL·J-Stage 양쪽에 있으면 NDL 삭제
        removed = _remove_ndl_duplicates(arts)
        if removed:
            print(f"\n  NDL 중복 제거: {removed}편 (J-Stage로 대체)")

        # 정렬 후 저장
        arts.sort(key=lambda x: (
            int(x.get("volume") or 0),
            int(x.get("issue") or 0),
            int(x.get("start_page") or 0)
        ))
        save(data, arts)

    finally:
        driver.quit()

    print(f"\n✅ 업데이트 완료")
    print(f"   신규: {total_new}편 / 초록 보충: {total_filled}편")
    if removed: print(f"   NDL→J-Stage 승격: {removed}편")
    print(f"   총: {len(arts)}편")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--depth", type=int, default=2)
    parser.add_argument("--repair-min", type=float, default=10, help="저자 보충에 쓸 시간(분)")
    args = parser.parse_args()
    main(depth=args.depth, repair_min=args.repair_min)
