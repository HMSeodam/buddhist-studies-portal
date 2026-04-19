#!/usr/bin/env python3
# ibk_crawler.py v3 — 정확한 셀렉터 적용
#
# 사용법:
#   python ibk_crawler.py --no-detail   # 목록만 (빠름)
#   python ibk_crawler.py               # 초록·키워드 포함
#   python ibk_crawler.py --test        # 최신 1호만 테스트

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import json, time, re
from pathlib import Path
from datetime import datetime
import argparse

OUTPUT_DIR  = "./output"
OUTPUT_FILE = "ibk_印度學佛教學研究.json"
JSTAGE_BASE = "https://www.jstage.jst.go.jp"
NDL_BASE    = "https://ndlsearch.ndl.go.jp"
DELAY       = 2.5

JSTAGE_LIST = f"{JSTAGE_BASE}/browse/ibk/list/-char/ja"
NDL_JOURNAL = (f"{NDL_BASE}/search?cs=bib&display=panel&from=0"
               f"&size=200&f-tid=R100000002-I000000001758&sort=published_dt")


def init_driver(headless=True):
    opts = Options()
    if headless:
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
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=opts
    )
    driver.implicitly_wait(5)
    return driver


def get_soup(driver, url, wait_sec=3):
    driver.get(url)
    time.sleep(wait_sec)
    return BeautifulSoup(driver.page_source, "html.parser")


# ════════════════════════════════════════
#  J-Stage: 권호 목록
# ════════════════════════════════════════

def jstage_get_issues(driver) -> list[dict]:
    print("  [J-Stage] 권호 목록 수집...")
    soup = get_soup(driver, JSTAGE_LIST, wait_sec=5)

    issues = []
    seen   = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/browse/ibk/(\d+)/(\d+)/_contents", href)
        if not m:
            continue
        vol, iss = m.group(1), m.group(2)
        if iss == "0":   # 권의 마지막 호로 리다이렉트되는 더미 URL
            continue
        key = f"{vol}_{iss}"
        if key in seen:
            continue
        seen.add(key)
        issues.append({
            "volume":   vol,
            "issue":    iss,
            "year":     str(1952 + int(vol) - 1),
            "base_url": f"{JSTAGE_BASE}/browse/ibk/{vol}/{iss}/_contents/-char/ja",
        })

    issues.sort(key=lambda x: (int(x["volume"]), int(x["issue"])))
    print(f"  → {len(issues)}개 권호")
    return issues


# ════════════════════════════════════════
#  J-Stage: 호수별 논문 수집 (페이지네이션)
# ════════════════════════════════════════

def jstage_get_articles(driver, issue: dict) -> list[dict]:
    """
    ?from=0, ?from=1, ... 순서로 전체 페이지 수집
    구조: <li> > .searchlist-title + .searchlist-authortags
               + .searchlist-additional-info + .searchlist-doi
    """
    vol, iss, year = issue["volume"], issue["issue"], issue["year"]
    arts = []
    seen = set()
    page = 0

    while True:
        url  = f"{issue['base_url']}?from={page}"
        soup = get_soup(driver, url, wait_sec=3)

        new = _parse_jstage_page(soup, vol, iss, year, seen)
        if not new:
            break

        arts.extend(new)

        # 다음 페이지 존재 확인
        has_next = any(
            f"?from={page+1}" in (a.get("href","") or "")
            for a in soup.find_all("a", href=True)
        )
        if not has_next:
            break

        page += 1
        time.sleep(DELAY)

    return arts


def _parse_jstage_page(soup, vol, iss, year, seen: set) -> list[dict]:
    """
    실제 J-Stage 구조:
    <li>
      <div class="searchlist-title"><a href="...">제목</a></div>
      <div class="searchlist-authortags" title="저자명">저자명</div>
      <div class="searchlist-additional-info">2023 年72 巻1 号 p. 1-11</div>
      <div class="searchlist-doi"><a href="https://doi.org/...">DOI</a></div>
      <div class="showabstractbox ..."><p>초록</p></div>
    </li>
    """
    arts = []

    for li in soup.find_all("li"):
        title_div = li.select_one(".searchlist-title")
        if not title_div:
            continue

        title_a = title_div.select_one("a[href*='/article/ibk']")
        if not title_a:
            continue

        href = title_a.get("href","")
        m    = re.search(r"/article/(ibk\w*)/(\d+)/(\d+)/([\w_]+)/_article", href)
        if not m:
            continue

        page_id = m.group(4)
        if page_id in seen:
            continue
        seen.add(page_id)

        title = title_a.get_text(strip=True)
        if not title or len(title) < 2:
            continue

        # 저자 (.searchlist-authortags title 속성이 가장 정확)
        authors = []
        au_div  = li.select_one(".searchlist-authortags")
        if au_div:
            au_raw = au_div.get("title","") or au_div.get_text(strip=True)
            for nm in re.split(r"[,、;]", au_raw):
                nm = nm.strip()
                if nm and 2 <= len(nm) <= 20:
                    authors.append({"name":nm,"affiliation":"","order":str(len(authors)+1)})

        # 권호·페이지 (.searchlist-additional-info)
        # 예: "2023 年72 巻1 号 p.\n\t\t\t1-11\n 発行日: 2023/12/20"
        info_div = li.select_one(".searchlist-additional-info")
        info_txt = info_div.get_text(separator=" ", strip=True) if info_div else ""

        # 연도
        yr_m = re.search(r"(\d{4})\s*年", info_txt)
        art_year = yr_m.group(1) if yr_m else year

        # 페이지: "p. 1-11" 패턴
        pg_m = re.search(r"p\.\s*(\d+)\s*[-–]\s*(\d+)", info_txt)
        start_page = pg_m.group(1) if pg_m else ""
        end_page   = pg_m.group(2) if pg_m else ""

        # DOI
        doi = ""
        doi_a = li.select_one(".searchlist-doi a")
        if doi_a:
            doi_m = re.search(r"10\.\d+/\S+", doi_a.get("href",""))
            if doi_m:
                doi = doi_m.group(0)
        if not doi and start_page:
            doi = f"10.4259/{m.group(1)}.{vol}.{iss}_{start_page}"

        # 목록에서 바로 보이는 초록 (.showabstractbox)
        abs_txt = ""
        abs_div = li.select_one(".showabstractbox .inner-content p, .showabstractbox p")
        if abs_div:
            abs_txt = abs_div.get_text(strip=True)

        full_url = href if href.startswith("http") else JSTAGE_BASE + href

        arts.append({
            "article_id":  f"ibk_{vol}_{iss}_{page_id}",
            "title_ja":    title,
            "title_kr":    title,
            "title_en":    "",
            "authors":     authors,
            "abstract_ja": abs_txt,
            "abstract_en": "",
            "abstract_kr": abs_txt,
            "journal_name":"印度學佛教學研究",
            "year":        art_year,
            "volume":      vol,
            "issue":       iss,
            "start_page":  start_page,
            "end_page":    end_page,
            "doi":         doi,
            "jstage_url":  full_url,
            "ndl_url":     "",
            "keywords_ja": [],
            "keywords_en": [],
            "keywords_kr": [],
            "ai_keywords": [],
            "source":      "J-Stage",
        })

    return arts


# ════════════════════════════════════════
#  J-Stage: 논문 상세 (초록·키워드·저자 보완)
# ════════════════════════════════════════

def jstage_fetch_detail(driver, art: dict) -> dict:
    soup = get_soup(driver, art["jstage_url"], wait_sec=5)
    r    = {}

    # 영문 제목
    for sel in [".global-article-title-en", ".trans-title", "[lang='en'].article-title"]:
        el = soup.select_one(sel)
        if el:
            t = el.get_text(strip=True)
            if t and len(t) > 2:
                r["title_en"] = t; break

    # 저자 보완 (소속 포함)
    if not art.get("authors") or not art["authors"][0].get("affiliation"):
        authors = []
        for contrib in soup.select(".contrib-group .contrib, .author-name, .contrib"):
            nm = contrib.get_text(strip=True)
            nm = re.sub(r"\s+"," ",nm).strip()
            if nm and 2 <= len(nm) <= 20 and nm not in [a["name"] for a in authors]:
                authors.append({"name":nm,"affiliation":"","order":str(len(authors)+1)})
        if authors:
            r["authors"] = authors

    # 초록 (일본어) — 상세 페이지
    for sel in ["section#ja p", ".abstract-ja p", "#ja p",
                ".abstract p", "section.abstract p"]:
        els = soup.select(sel)
        for el in els:
            t = el.get_text(strip=True)
            if t and len(t) > 30:
                r["abstract_ja"] = t; r["abstract_kr"] = t; break
        if r.get("abstract_ja"):
            break

    # 초록 (영어)
    for sel in ["section#en p", ".abstract-en p", "#en p"]:
        els = soup.select(sel)
        for el in els:
            t = el.get_text(strip=True)
            if t and len(t) > 30:
                r["abstract_en"] = t; break
        if r.get("abstract_en"):
            break

    # 키워드
    kj, ke = [], []
    for sel in [".kwd-group", ".keywords", "[class*='keyword']"]:
        for container in soup.select(sel):
            for kw_el in container.find_all(["span","li","p","a"]):
                kw = kw_el.get_text(strip=True).strip(",.;、。")
                if not kw or len(kw) < 2 or len(kw) > 50:
                    continue
                ratio = sum(c.isascii() for c in kw)/len(kw)
                (ke if ratio > 0.7 else kj).append(kw)
        if kj or ke:
            break

    if kj: r["keywords_ja"] = list(dict.fromkeys(kj)); r["keywords_kr"] = r["keywords_ja"]
    if ke: r["keywords_en"] = list(dict.fromkeys(ke))

    return r


# ════════════════════════════════════════
#  NDL: 권호 목록
# ════════════════════════════════════════

def ndl_get_issues(driver) -> list[dict]:
    print("  [NDL] 권호 목록 수집...")
    soup  = get_soup(driver, NDL_JOURNAL, wait_sec=6)
    issues = []
    seen   = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m    = re.search(r"/books/(R100000002-I000000001758-i\w+)", href)
        if not m:
            continue
        item_id = m.group(1)
        if item_id in seen:
            continue
        seen.add(item_id)
        full_url = NDL_BASE + href if href.startswith("/") else href
        issues.append({
            "item_id": item_id,
            "label":   a.get_text(strip=True),
            "url":     full_url,
        })

    print(f"  → NDL {len(issues)}개 권호")
    return issues


# ════════════════════════════════════════
#  NDL: 호수별 논문 수집 (버튼 클릭 페이지네이션)
# ════════════════════════════════════════

def _parse_ndl_items(soup, seen: set, meta: dict) -> list[dict]:
    """NDL 논문 항목 파싱"""
    arts = []
    for item in soup.select("div.pages-books-section-index-item-split"):
        a_el  = item.select_one("a.lang-link")
        if not a_el:
            continue
        href  = a_el.get("href","")
        ndl_m = re.search(r"/(R000000004-I\w+)", href)
        if not ndl_m:
            continue
        ndl_id = ndl_m.group(1)
        if ndl_id in seen:
            continue
        seen.add(ndl_id)

        title = a_el.get_text(strip=True)
        if not title or len(title) < 2:
            continue

        item_txt = item.get_text(separator="|", strip=True)
        parts    = [p.strip() for p in item_txt.split("|") if p.strip()]
        authors, pg_start, pg_end = [], "", ""

        for part in parts:
            if part == title:
                continue
            pg_m = re.search(r"p\.?\s*(\d+)(?:\s*[-–]\s*(\d+))?", part)
            if pg_m:
                pg_start = pg_m.group(1); pg_end = pg_m.group(2) or ""; continue
            if (re.search(r"[一-鿿゠-ヿ]", part)
                    and 2 <= len(part) <= 15
                    and not re.search(r"[（(]|年|巻|号|通号", part)):
                nm = part.strip()
                if nm and nm not in [a["name"] for a in authors]:
                    authors.append({"name":nm,"affiliation":"","order":str(len(authors)+1)})

        full_url = NDL_BASE + href if href.startswith("/") else href
        arts.append({
            "article_id":  f"ndl_{ndl_id}",
            "title_ja":    title, "title_kr": title, "title_en": "",
            "authors":     authors,
            "abstract_ja": "", "abstract_en": "", "abstract_kr": "",
            "journal_name":"印度學佛教學研究",
            "year":        meta.get("year",""), "volume": meta.get("volume",""),
            "issue":       meta.get("issue",""),
            "start_page":  pg_start, "end_page": pg_end, "doi": "",
            "jstage_url":  "", "ndl_url": full_url,
            "keywords_ja": [], "keywords_en": [], "keywords_kr": [],
            "ai_keywords": [], "source": "NDL",
        })
    return arts


def ndl_get_articles(driver, issue: dict) -> tuple[dict, list[dict]]:
    """
    1. もっと見る 클릭 → 5편→50편 확장
    2. 1페이지 파싱
    3. 페이지 2,3... 버튼 클릭 → 각 페이지 파싱 후 합산
    ※ 페이지 버튼 클릭 시 기존 내용이 교체되므로 페이지별로 따로 수집
    """
    driver.get(issue["url"])
    time.sleep(4)

    # 권호 메타 파싱 (페이지 공통)
    def get_meta(soup):
        txt   = soup.get_text(separator="\n", strip=True)
        meta  = {}
        vol_m = re.search(r"(\d+)[巻（(](\d+)[)）号]", txt)
        yr_m  = re.search(r"(\d{4})[\s年]", txt)
        if vol_m: meta["volume"] = vol_m.group(1); meta["issue"] = vol_m.group(2)
        if yr_m:  meta["year"]   = yr_m.group(1)
        return meta

    # もっと見る 클릭 (5→50 확장)
    for _ in range(3):
        try:
            btn = WebDriverWait(driver, 4).until(
                EC.element_to_be_clickable(
                    (By.XPATH,
                     "//span[contains(text(),'もっと見る')]"
                     " | //button[contains(text(),'もっと見る')]")
                )
            )
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(2.5)
        except:
            break

    # 1페이지 수집
    soup1 = BeautifulSoup(driver.page_source, "html.parser")
    meta  = get_meta(soup1)
    seen  = set()
    all_arts = _parse_ndl_items(soup1, seen, meta)

    # 총 페이지 수 파악
    page_nums = set()
    for btn in driver.find_elements(By.CSS_SELECTOR, "button.ui-parts-pagination-button"):
        txt = btn.text.strip()
        if txt.isdigit():
            page_nums.add(int(txt))
    max_page = max(page_nums) if page_nums else 1

    # 2페이지 이상 수집
    for pg in range(2, max_page + 1):
        try:
            # 해당 페이지 버튼 클릭
            pg_btn = driver.find_element(
                By.XPATH,
                f"//button[contains(@class,'ui-parts-pagination-button') and text()='{pg}']"
            )
            driver.execute_script("arguments[0].click();", pg_btn)
            time.sleep(3)

            # もっと見る 다시 클릭
            for _ in range(3):
                try:
                    mb = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable(
                            (By.XPATH, "//span[contains(text(),'もっと見る')]"
                             " | //button[contains(text(),'もっと見る')]")
                        )
                    )
                    driver.execute_script("arguments[0].click();", mb)
                    time.sleep(2)
                except:
                    break

            soup_pg = BeautifulSoup(driver.page_source, "html.parser")
            page_arts = _parse_ndl_items(soup_pg, seen, meta)
            all_arts.extend(page_arts)
        except Exception as e:
            break

    return meta, all_arts


# ════════════════════════════════════════
#  병합
# ════════════════════════════════════════

def _norm_title(t):
    """제목 정규화 — 공백·괄호·기호 제거 후 소문자"""
    import unicodedata
    t = unicodedata.normalize("NFKC", t or "")
    t = re.sub(r"[\s　「-』『』『』「」【】（）()\[\]\-―:：・]", "", t)
    return t.lower()


def merge_sources(jstage_arts, ndl_arts):
    """
    J-Stage 우선. NDL은 J-Stage에 없는 권호만 보충.
    제목 기반 중복 체크로 동일 논문 중복 방지.
    J-Stage가 있으면 NDL URL만 이식 후 NDL 항목 버림.
    """
    # J-Stage 제목 맵
    jstage_map = {}
    for a in jstage_arts:
        nt = _norm_title(a.get("title_ja",""))
        if nt:
            jstage_map[nt] = a

    merged = list(jstage_arts)

    for art in ndl_arts:
        nt = _norm_title(art.get("title_ja",""))
        if nt in jstage_map:
            # 이미 J-Stage에 있음 → NDL URL만 이식
            js_art = jstage_map[nt]
            if not js_art.get("ndl_url") and art.get("ndl_url"):
                js_art["ndl_url"] = art["ndl_url"]
        else:
            # J-Stage에 없는 논문 → NDL로 보충
            merged.append(art)

    merged.sort(key=lambda x: (
        int(x.get("volume") or 0),
        int(x.get("issue") or 0),
        int(x.get("start_page") or 0),
    ))
    return merged


# ════════════════════════════════════════
#  저장
# ════════════════════════════════════════

def save(articles):
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    by_year = {}
    for a in articles:
        y = a.get("year","미상")
        v = a.get("volume",""); n = a.get("issue","미상")
        k = f"{v}권 {n}호" if v else f"{n}호"
        by_year.setdefault(y,{}).setdefault(k,[]).append(a)

    data = {
        "info": {
            "name":      "印度學佛教學研究",
            "name_en":   "Journal of Indian and Buddhist Studies",
            "publisher": "日本印度学仏教学会",
            "category":  "일본불교학",
            "country":   "Japan",
            "issn":      "0019-4344",
            "homepage":  "https://www.inbuds.net",
        },
        "meta": {
            "generated_at":   datetime.now().isoformat(),
            "last_updated":   datetime.now().strftime("%Y-%m-%d"),
            "total_articles": len(articles),
            "source":         "J-Stage + NDL",
        },
        "articles":        articles,
        "by_year":         by_year,
        "total_collected": len(articles),
    }

    out = Path(OUTPUT_DIR) / OUTPUT_FILE
    with open(out,"w",encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    sz = out.stat().st_size/1024/1024
    print(f"저장: {out} ({sz:.1f}MB)")
    return len(articles)


# ════════════════════════════════════════
#  메인
# ════════════════════════════════════════

def main(no_detail=False, test=False):
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    print("Chrome 초기화...")
    driver = init_driver(headless=True)

    try:
        # 1) J-Stage 권호 목록
        jstage_issues = jstage_get_issues(driver)
        if test:
            jstage_issues = jstage_issues[-1:]
            print(f"  [테스트] 최신 1호만: {jstage_issues[0]['volume']}권 {jstage_issues[0]['issue']}호")
        time.sleep(DELAY)

        # 2) J-Stage 논문 수집
        all_jstage = []
        for iss in jstage_issues:
            label = f"{iss['volume']}권 {iss['issue']}호"
            print(f"  [J-Stage] {label} ", end="", flush=True)
            arts = jstage_get_articles(driver, iss)
            print(f"→ {len(arts)}편")
            all_jstage.extend(arts)
            time.sleep(DELAY)

        print(f"\n  J-Stage 총 {len(all_jstage)}편")

        # 3) J-Stage 상세 (초록·키워드)
        if not no_detail and all_jstage:
            # 이미 완료된 것 건너뜀 (재시작 지원)
            need_detail = [a for a in all_jstage
                           if a.get("jstage_url") and not a.get("abstract_ja")
                           and not a.get("title_en")]
            already = len(all_jstage) - len(need_detail)
            print(f"  상세 수집: {len(need_detail)}편 (완료 {already}편 건너뜀)")
            filled = 0
            for i, art in enumerate(need_detail):
                retry = 0
                while retry < 3:
                    try:
                        d = jstage_fetch_detail(driver, art)
                        for k,v in d.items():
                            if v: art[k] = v
                        if d.get("abstract_ja"): filled += 1
                        break
                    except Exception as e:
                        retry += 1
                        print(f"    재시도 {retry}/3: {e}")
                        time.sleep(DELAY * 3)
                # 100편마다 중간 저장
                if (i+1) % 100 == 0:
                    save(all_jstage)
                    print(f"    {i+1}/{len(need_detail)} 중간저장 (초록 {filled}편)")
                elif (i+1) % 20 == 0:
                    print(f"    {i+1}/{len(need_detail)} (초록 {filled}편)")
                time.sleep(DELAY)

        # 4) NDL 보충
        jstage_keys = {f"{a['volume']}_{a['issue']}" for a in all_jstage}
        print(f"\n  [NDL] 보충 수집...")
        ndl_issues  = ndl_get_issues(driver)
        time.sleep(DELAY)

        all_ndl = []
        for iss in ndl_issues:
            if test and len(all_ndl) >= 100: break
            meta, arts = ndl_get_articles(driver, iss)
            vol   = meta.get("volume","")
            iss_n = meta.get("issue","")
            if vol and iss_n and f"{vol}_{iss_n}" in jstage_keys:
                continue
            if arts:
                label = f"{vol}권 {iss_n}호" if vol else iss.get("label","")
                print(f"  [NDL] {label} → {len(arts)}편")
                all_ndl.extend(arts)
            time.sleep(DELAY)

        print(f"  NDL 보충: {len(all_ndl)}편")

        # 5) 병합·저장
        merged = merge_sources(all_jstage, all_ndl)
        print(f"\n수집 완료: {len(merged)}편 (J-Stage {len(all_jstage)} + NDL 보충 {len(all_ndl)})")

        # 샘플 출력
        if merged:
            samples = [merged[0], merged[len(merged)//2], merged[-1]]
            print("\n[샘플 확인]")
            for a in samples:
                print(f"  {a.get('volume','')}권 {a.get('issue','')}호 {a.get('year','')}년")
                print(f"  제목: {a.get('title_ja','')[:50]}")
                print(f"  저자: {[x['name'] for x in a.get('authors',[])]}")
                print(f"  페이지: {a.get('start_page','')}-{a.get('end_page','')}")
                print(f"  초록: {'있음' if a.get('abstract_ja') else '없음'}")
                print()

        save(merged)

    finally:
        driver.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--test",      action="store_true")
    args = parser.parse_args()
    main(no_detail=args.no_detail, test=args.test)
