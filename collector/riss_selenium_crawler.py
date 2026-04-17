#!/usr/bin/env python3
# riss_selenium_crawler.py v2 — 정확한 셀렉터 적용
#
# 사용법:
#   python riss_selenium_crawler.py --journal 선학 --no-detail
#   python riss_selenium_crawler.py --all

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import json, time, re, hashlib
from pathlib import Path
from datetime import datetime
import argparse

OUTPUT_DIR    = "./output"
REQUEST_DELAY = 2.0
RISS_BASE     = "https://www.riss.kr"

JOURNALS = [
    {"name":"불교미술사학",          "category":"불교미술사학","impact_factor":1.88,"control_no":"a57013b634c75673"},
    {"name":"동악미술사학",          "category":"불교미술사학","impact_factor":1.55,"control_no":"58ad7d9d54d869b8ffe0bdc3ef48d419"},
    {"name":"강좌미술사",            "category":"불교미술사학","impact_factor":1.34,"control_no":"b03a1d4832ed4c7a"},
    {"name":"정토학연구",            "category":"불교학",      "impact_factor":1.04,"control_no":"20c4186b3804871f"},
    {"name":"선문화연구",            "category":"불교학",      "impact_factor":1.00,"control_no":"7f818b2e8e8dcabd"},
    {"name":"불교문예연구",          "category":"불교학",      "impact_factor":0.91,"control_no":"54b47b13bc649e27ffe0bdc3ef48d419"},
    {"name":"불교학보",              "category":"불교학",      "impact_factor":0.90,"control_no":"27eeee1a652c6cf1"},
    {"name":"불교학연구",            "category":"불교학",      "impact_factor":0.88,"control_no":"74eb06313eaadb56ffe0bdc3ef48d419"},
    {"name":"한국불교학",            "category":"불교학",      "impact_factor":0.85,"control_no":"cb236634237a7a74"},
    {"name":"선학",                  "category":"불교학",      "impact_factor":0.65,"control_no":"d18b923635d64155ffe0bdc3ef48d419"},
    {"name":"불교연구",              "category":"불교학",      "impact_factor":0.56,"control_no":"a7943149367c4574ffe0bdc3ef48d419"},
    {"name":"동아시아불교문화",      "category":"불교학",      "impact_factor":0.74,"control_no":"89d7868617dd0940"},
    {"name":"불교철학",              "category":"불교학",      "impact_factor":0.72,"control_no":"0c20a8836b6e2010ffe0bdc3ef48d419"},
    {"name":"대각사상",              "category":"불교학",      "impact_factor":0.65,"control_no":"609d6ddc429d15c5"},
    {"name":"보조사상",              "category":"불교학",      "impact_factor":0.63,"control_no":"51ee8e4df59bd23effe0bdc3ef48d419"},
    {"name":"한국교수불자연합학회지","category":"불교학",      "impact_factor":0.61,"control_no":"90157c433708510fffe0bdc3ef48d419"},
    {"name":"불교학리뷰",            "category":"불교학",      "impact_factor":0.61,"control_no":"b4d6ff724148c295ffe0bdc3ef48d419"},
    {"name":"불교학밀교학연구",      "category":"불교학",      "impact_factor":0.50,"control_no":"adca842359598bb5ffe0bdc3ef48d419"},
    {"name":"인도철학",              "category":"불교학",      "impact_factor":0.48,"control_no":"6c3aaa42b0296663ffe0bdc3ef48d419"},
    {"name":"명상심리상담",          "category":"불교학",      "impact_factor":0.48,"control_no":"2158cb1ffaedc442ffe0bdc3ef48d419"},
    {"name":"불교와 사회",           "category":"불교학",      "impact_factor":0.43,"control_no":"ac622b8ba4ebe87affe0bdc3ef48d419"},
    {"name":"한국불교사연구",        "category":"불교사학",    "impact_factor":0.33,"control_no":"864d8da7fde953e0ffe0bdc3ef48d419"},
    {"name":"한마음연구",            "category":"불교학",      "impact_factor":0.31,"control_no":"5324a18d726261b4ffe0bdc3ef48d419"},
    {"name":"IJBTC",                 "category":"불교학",      "impact_factor":0.00,"control_no":"b44e9e4716ca7ae7ffe0bdc3ef48d419"},
    {"name":"종학연구",              "category":"불교학",      "impact_factor":0.00,"control_no":"d6dbf60f1a65bfc4ffe0bdc3ef48d419"},
    {"name":"무형문화연구",          "category":"불교학",      "impact_factor":0.00,"control_no":"e92ddca29a0f1d20ffe0bdc3ef48d419"},
    # display_name: RISS 표시명(세계불학)과 포털 표시명(세화불학) 분리
    {"name":"세계불학", "display_name":"세화불학",  "category":"불교학",  "impact_factor":0.00,"control_no":"b0e2ccd5057ccc6bffe0bdc3ef48d419"},
    {"name":"전자불전",                              "category":"불교학",  "impact_factor":0.00,"control_no":"4ed0c31dbf9d9728ffe0bdc3ef48d419"},
]


try:
    import undetected_chromedriver as uc
    _UC_AVAILABLE = True
except ImportError:
    _UC_AVAILABLE = False

def init_driver(headless=True):
    """
    undetected_chromedriver(UC)가 설치된 경우 우선 사용.
    UC는 RISS 등 봇 감지 사이트에서 headless 차단을 우회한다.
    미설치 시 표준 selenium으로 fallback.
    """
    if _UC_AVAILABLE:
        opts = uc.ChromeOptions()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,900")
        driver = uc.Chrome(options=opts, use_subprocess=True)
        driver.implicitly_wait(5)
        print(f"  드라이버: undetected_chromedriver ({'headless' if headless else 'show'})")
        return driver

    # ── fallback: 표준 selenium ────────────────────────────
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    driver.implicitly_wait(5)
    print("  드라이버: selenium (UC 미설치)")
    return driver


def load_page(driver, url, wait_sec=4):
    driver.get(url)
    time.sleep(wait_sec)
    # 리다이렉트 감지 경고 (홈으로 튕길 때 원인 파악용)
    landed = driver.current_url
    if landed.rstrip("/") != url.rstrip("/") and "DetailView" in url and "DetailView" not in landed:
        print(f"  ⚠ 리다이렉트 감지: 목표={url[:70]}")
        print(f"           실착={landed[:70]}")
    return BeautifulSoup(driver.page_source, "html.parser")


def warmup_session(driver):
    """RISS 홈을 먼저 방문해 세션 쿠키를 확보한다."""
    print("  RISS 세션 워밍업 중...")
    driver.get(RISS_BASE)
    time.sleep(4)
    print(f"  세션 확보 완료 (현재: {driver.current_url[:60]})")


# ── 호수 수집 내부 함수 ──
def _collect_issues_from_page(driver, control_no, seen):
    """현재 페이지 HTML에서 v_control_no 링크를 수집."""
    found = []
    soup  = BeautifulSoup(driver.page_source, "html.parser")
    for a in soup.find_all("a"):
        href    = a.get("href",    "") or ""
        onclick = a.get("onclick", "") or ""
        source  = href if "v_control_no" in href else onclick if "v_control_no" in onclick else ""
        if not source:
            continue
        m = re.search(r"v_control_no=([a-f0-9]+)", source)
        if not m or m.group(1) in seen:
            continue
        v_id  = m.group(1)
        seen.add(v_id)
        txt   = a.get_text(strip=True)
        no_m  = re.search(r"No\.(\d+)", txt)
        vol_m = re.search(r"Vol\.(\d+)", txt)
        if href.startswith("/"):
            full_url = RISS_BASE + href
        elif href.startswith("http"):
            full_url = href
        else:
            full_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                        f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}"
                        f"&v_control_no={v_id}&inside_outside=1")
        found.append({
            "label":        txt,
            "v_control_no": v_id,
            "issue":        no_m.group(1)  if no_m  else "",
            "volume":       vol_m.group(1) if vol_m else "",
            "url":          full_url,
        })
    return found


# ── 호수 목록 ──
def get_issue_list(driver, control_no: str) -> list:
    url = (f"{RISS_BASE}/search/detail/DetailView.do"
           f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}")
    driver.get(url)
    time.sleep(5)

    issues = []
    seen   = set()

    # ── 1차: 기본 페이지에서 바로 수집 (대부분의 학술지) ──
    issues += _collect_issues_from_page(driver, control_no, seen)
    if issues:
        return issues

    # ── 2차: 연도 클릭 후 호수 수집 ──────────────────────────────────────
    # 세계불학처럼 연도를 클릭해야 Vol/No 링크가 펼쳐지는 구조
    print("  연도 클릭 방식으로 호수 탐색 중...")
    year_els = [
        el for el in driver.find_elements(By.TAG_NAME, "a")
        if re.match(r"^(19|20)\d{2}", el.text.strip())
    ]
    if year_els:
        for el in year_els:
            try:
                year_txt = el.text.strip()
                print(f"    [{year_txt}] 클릭...", end=" ", flush=True)
                driver.execute_script("arguments[0].click();", el)
                time.sleep(2)
                before = len(issues)
                issues += _collect_issues_from_page(driver, control_no, seen)
                print(f"{len(issues) - before}개 발견")
            except Exception as e:
                print(f"클릭 실패: {e}")

    # ── 2-1차: 사이드바에 없는 이전 연도 추가 탐색 ───────────────────────
    # 사이드바가 특정 연도 이전을 표시하지 않는 경우(예: 전자불전 1999년)
    # 찾은 연도 중 가장 이른 연도 이전을 v_year URL로 추가 시도
    if issues:
        found_years = []
        for el in year_els:
            m = re.match(r"(19|20)\d{2}", el.text.strip())
            if m:
                found_years.append(int(m.group(1)))
        if found_years:
            earliest = min(found_years)
            print(f"  사이드바 최초 연도 {earliest}년 이전 추가 탐색...")
            consecutive_empty = 0
            for year in range(earliest - 1, earliest - 10, -1):
                year_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                            f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}"
                            f"&inside_outside=0&v_year={year}")
                driver.get(year_url)
                time.sleep(3)
                before  = len(issues)
                issues += _collect_issues_from_page(driver, control_no, seen)
                added   = len(issues) - before
                if added:
                    print(f"    {year}년: {added}개 발견")
                    consecutive_empty = 0
                else:
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        break
        return issues

    # ── 3차: 연도별 URL 직접 순회 (fallback) ─────────────────────────────
    print("  연도별 URL 방식으로 호수 탐색 중...")
    cur = datetime.now().year
    consecutive_empty = 0
    for year in range(cur, cur - 6, -1):
        year_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                    f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}"
                    f"&inside_outside=0&v_year={year}")
        driver.get(year_url)
        time.sleep(3)
        before  = len(issues)
        issues += _collect_issues_from_page(driver, control_no, seen)
        added   = len(issues) - before
        if added:
            print(f"  {year}년: {added}개 발견")
            consecutive_empty = 0
        else:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break

    # ── 디버그: 여전히 0개 ──
    if not issues:
        landed = driver.current_url
        print(f"\n  [DEBUG] v_control_no 링크 없음. (현재 URL: {landed[:80]})")
        soup = BeautifulSoup(driver.page_source, "html.parser")
        print("  <a> 태그 샘플 (최대 10개):")
        for tag in soup.find_all("a")[:10]:
            h = (tag.get("href") or "")[:80]
            t = tag.get_text(strip=True)[:30]
            print(f"    {t!r:30s}  href={h!r}")

    return issues


# ── 호수별 논문 목록 ──
def get_articles_by_issue(driver, issue: dict, control_no: str,
                           journal_name: str) -> list:
    base_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                f"?p_mat_type=3a11008f85f7c51d"
                f"&control_no={control_no}"
                f"&v_control_no={issue['v_control_no']}"
                f"&inside_outside=1")
    articles = []
    seen_ids = set()
    page     = 1

    while True:
        url  = f"{base_url}&currentPage={page}&rowPerPage=100"
        soup = load_page(driver, url, wait_sec=3)

        art_links = soup.find_all(
            "a",
            href=lambda h: h and "p_mat_type=1a0202" in str(h) and "control_no" in str(h)
        )
        if not art_links:
            break

        new = 0
        for lk in art_links:
            href  = lk.get("href","")
            m     = re.search(r"control_no=([a-f0-9]+)", href)
            if not m:
                continue
            art_id = m.group(1)
            if art_id in seen_ids:
                continue
            seen_ids.add(art_id)

            title   = lk.get_text(strip=True)
            if not title or len(title) < 2:
                continue

            full_url = RISS_BASE + href if href.startswith("/") else href
            articles.append({
                "article_id":  art_id,
                "title_kr":    title,
                "title_en":    "",
                "authors":     [],
                "abstract_kr": "",
                "abstract_en": "",
                "journal_name":journal_name,
                "year":        "",
                "volume":      issue.get("volume",""),
                "issue":       issue.get("issue",""),
                "start_page":  "",
                "end_page":    "",
                "doi":         "",
                "riss_url":    full_url,
                "kci_url":     "",
                "keywords_kr": [],
                "keywords_en": [],
                "ai_keywords": [],
                "source":      "RISS",
            })
            new += 1

        if new == 0 or len(art_links) < 10:
            break
        page += 1
        time.sleep(REQUEST_DELAY)

    return articles


# ── 논문 상세 ──
def fetch_detail(driver, article_id: str) -> dict:
    url  = (f"{RISS_BASE}/search/detail/DetailView.do"
            f"?p_mat_type=1a0202e37d52c72d&control_no={article_id}")
    soup = load_page(driver, url, wait_sec=5)
    r    = {}

    # 제목
    ti = soup.select_one(".thesisInfo h3.title")
    if ti:
        # "= 영문제목" 패턴 분리
        full = ti.get_text(separator="\n", strip=True)
        parts = re.split(r"\n\s*=\s*", full)
        r["title_kr"] = parts[0].strip()
        if len(parts) > 1:
            r["title_en"] = parts[1].strip()

    # infoDetailL 파싱 (저자·연도·권호·페이지·키워드)
    for li in soup.select(".infoDetailL li"):
        label_el = li.find("span", class_="strong")
        if not label_el:
            continue
        label = label_el.get_text(strip=True)
        div   = li.find("div")
        if not div:
            continue

        if label == "저자":
            authors = []
            for a in div.find_all("a"):
                nm = a.get_text(strip=True)
                # 소속 (a 다음 텍스트)
                affil = ""
                next_txt = a.next_sibling
                if next_txt and isinstance(next_txt, str):
                    affil = re.sub(r"[()]","", next_txt).strip()
                if nm and len(nm) >= 2:
                    authors.append({
                        "name":        nm,
                        "affiliation": affil,
                        "order":       str(len(authors)+1)
                    })
            if authors:
                r["authors"] = authors

        elif label == "발행연도":
            yr = div.get_text(strip=True)
            if re.match(r"^\d{4}$", yr):
                r["year"] = yr

        elif label == "권호사항":
            txt = div.get_text(separator=" ", strip=True)
            vol_m = re.search(r"Vol\.(\d+)", txt)
            no_m  = re.search(r"No\.(\d+)", txt)
            yr_m  = re.search(r"\[(\d{4})\]", txt)
            if vol_m: r["volume"] = vol_m.group(1)
            if no_m:  r["issue"]  = no_m.group(1)
            if yr_m and not r.get("year"): r["year"] = yr_m.group(1)

        elif label == "수록면":
            txt = div.get_text(strip=True)
            pg  = re.search(r"(\d+)\s*[-~]\s*(\d+)", txt)
            if pg:
                r["start_page"] = pg.group(1)
                r["end_page"]   = pg.group(2)

        elif label == "주제어":
            kk, ke = [], []
            for a in div.find_all("a"):
                kw = a.get_text(strip=True)
                if not kw or len(kw) < 2:
                    continue
                ratio = sum(c.isascii() for c in kw) / len(kw)
                (ke if ratio > 0.7 else kk).append(kw)
            if kk: r["keywords_kr"] = kk
            if ke: r["keywords_en"] = ke

    # 초록 — #abs1 .textWrap
    abs_el = soup.select_one("#abs1.textWrap, #abs1 .textWrap")
    if abs_el:
        t = abs_el.get_text(strip=True)
        if t and len(t) > 20:
            r["abstract_kr"] = t

    # 영문 초록 — #abs2 .textWrap
    abs_en = soup.select_one("#abs2.textWrap, #abs2 .textWrap")
    if abs_en:
        t = abs_en.get_text(strip=True)
        if t and len(t) > 20:
            r["abstract_en"] = t

    # KCI 링크
    kci_el = soup.select_one("a[href*='kci.go.kr']")
    if kci_el:
        r["kci_url"] = kci_el.get("href","")

    return r


# ── 학술지 크롤링 ──
def crawl_journal(driver, journal: dict, fetch_detail_flag=True) -> list:
    # display_name이 있으면 포털 표시명 사용 (예: 세계불학→세화불학)
    name       = journal.get("display_name") or journal["name"]
    control_no = journal["control_no"]

    print(f"\n{'='*55}")
    print(f"[{name}]")
    print(f"{'='*55}")

    # 1) 호수 목록
    issues = get_issue_list(driver, control_no)
    if not issues:
        print("  호수 없음 → 건너뜀")
        return []
    print(f"  호수: {len(issues)}개")
    time.sleep(REQUEST_DELAY)

    # 2) 호수별 논문 목록
    all_articles = []
    seen_ids     = set()

    for iss in issues:
        label = f"Vol.{iss['volume']} No.{iss['issue']}" if iss['volume'] else iss['label']
        print(f"  [{label}] ", end="", flush=True)

        arts = get_articles_by_issue(driver, iss, control_no, name)
        new  = 0
        for a in arts:
            aid = a["article_id"]
            if aid and aid not in seen_ids:
                seen_ids.add(aid)
                all_articles.append(a)
                new += 1
        print(f"{new}편")
        time.sleep(REQUEST_DELAY)

    print(f"\n  목록 수집: {len(all_articles)}편")

    # 3) 상세 수집
    if fetch_detail_flag and all_articles:
        print(f"  상세 수집 중...")
        for i, art in enumerate(all_articles):
            if not art.get("article_id"):
                continue
            d = fetch_detail(driver, art["article_id"])
            for k, v in d.items():
                if v: art[k] = v
            if (i+1) % 10 == 0:
                print(f"    {i+1}/{len(all_articles)} 완료")
            time.sleep(REQUEST_DELAY)

    return all_articles


def crawl_all(skip_detail=False):
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    print("Chrome 초기화...")
    driver = init_driver(headless=True)

    try:
        warmup_session(driver)   # 세션 쿠키 확보
        result = {}
        for journal in JOURNALS:
            arts = crawl_journal(driver, journal, fetch_detail_flag=not skip_detail)
            arts.sort(key=lambda x:(x.get("year",""), x.get("volume",""), _pg(x)))
            # 저장 키와 파일명 모두 display_name 우선 사용
            jkey = journal.get("display_name") or journal["name"]
            result[jkey] = {
                "info":{**journal, "name": jkey},"articles":arts,
                "by_year":_by_year(arts),"total_collected":len(arts),
            }
            sp = Path(OUTPUT_DIR) / f"riss_{jkey}.json"
            with open(sp,"w",encoding="utf-8") as f:
                json.dump(result[journal["name"]], f, ensure_ascii=False, indent=2)
            print(f"  → 저장: {sp.name}")
            time.sleep(REQUEST_DELAY*2)

        index = _build_index(result)
        final = {
            "meta":{
                "generated_at":datetime.now().isoformat(),
                "last_updated":datetime.now().strftime("%Y-%m-%d"),
                "total_articles":len(index),"journals":len(result),
                "version":"1.0","source":"RISS",
            },
            "journals":result,"index":index,
        }
        out = Path(OUTPUT_DIR)/"papers.json"
        with open(out,"w",encoding="utf-8") as f:
            json.dump(final, f, ensure_ascii=False, indent=2)
        sz = out.stat().st_size/1024/1024
        print(f"\n✅ 완료: {len(index)}편 → {out} ({sz:.1f}MB)")
    finally:
        driver.quit()


def _build_index(result):
    idx=[]
    for jname,jdata in result.items():
        info=jdata.get("info",{})
        for art in jdata.get("articles",[]):
            au=" ".join(a.get("name","") for a in art.get("authors",[]))
            kws=art.get("keywords_kr",[])+art.get("keywords_en",[])+art.get("ai_keywords",[])
            idx.append({
                "id":art.get("article_id") or _gid(art),
                "journal_name":jname,"category":info.get("category","불교학"),
                "impact_factor":info.get("impact_factor",0),
                "title_kr":art.get("title_kr",""),"title_en":art.get("title_en",""),
                "author_names":au,"authors":art.get("authors",[]),
                "year":art.get("year",""),"volume":art.get("volume",""),
                "issue":art.get("issue",""),"start_page":art.get("start_page",""),
                "abstract_kr":art.get("abstract_kr",""),"keywords":kws,
                "keywords_kr":art.get("keywords_kr",[]),"ai_keywords":art.get("ai_keywords",[]),
                "riss_url":art.get("riss_url",""),"kci_url":art.get("kci_url",""),
                "doi":art.get("doi",""),"source":"RISS",
            })
    return idx


def _pg(a):
    try: return int(a.get("start_page") or 0)
    except: return 0

def _by_year(arts):
    t={}
    for a in arts:
        y=a.get("year","미상"); v=a.get("volume",""); n=a.get("issue","미상")
        k=f"{v}권 {n}호" if v else f"{n}호"
        t.setdefault(y,{}).setdefault(k,[]).append(a)
    for y in t:
        for k in t[y]: t[y][k].sort(key=_pg)
    return t

def _gid(a):
    return "R"+hashlib.md5(f"{a.get('title_kr','')}{a.get('year','')}".encode()).hexdigest()[:11]


if __name__=="__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal",  default=None, help="단일 학술지")
    parser.add_argument("--journals", nargs="+", default=None, help="여러 학술지 (예: --journals 선학 불교학보)")
    parser.add_argument("--no-detail", action="store_true")
    parser.add_argument("--all",       action="store_true")
    parser.add_argument("--show",      action="store_true")
    args = parser.parse_args()

    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    if args.journals:
        targets = [j for j in JOURNALS if j["name"] in args.journals]
        missing = [n for n in args.journals if n not in [j["name"] for j in JOURNALS]]
        if missing:
            print(f"목록에 없는 학술지: {missing}")
        if not targets:
            exit(1)
        print("Chrome 초기화...")
        driver = init_driver(headless=not args.show)
        try:
            warmup_session(driver)   # 세션 쿠키 확보
            for target in targets:
                arts = crawl_journal(driver, target, fetch_detail_flag=not args.no_detail)
                jkey = target.get("display_name") or target["name"]
                print(f"\n{jkey}: {len(arts)}편")
                sp = Path(OUTPUT_DIR)/f"riss_{jkey}.json"
                with open(sp,"w",encoding="utf-8") as f:
                    json.dump(arts, f, ensure_ascii=False, indent=2)
                print(f"저장: {sp}")
        finally:
            driver.quit()

    elif args.journal:
        target = next((j for j in JOURNALS if j["name"]==args.journal), None)
        if not target:
            print(f"'{args.journal}' 없음"); exit(1)

        print("Chrome 초기화...")
        driver = init_driver(headless=not args.show)
        try:
            warmup_session(driver)   # 세션 쿠키 확보
            arts = crawl_journal(driver, target, fetch_detail_flag=not args.no_detail)
            print(f"\n최종 수집: {len(arts)}편")
            if arts:
                print("\n[연도별 분포]")
                stats={}
                for a in arts:
                    y=a.get("year","?")
                    stats[y]=stats.get(y,0)+1
                for y in sorted(stats):
                    print(f"  {y}년: {stats[y]}편")

                a=arts[0]
                print(f"\n[첫 번째 논문]")
                print(f"  제목:   {a.get('title_kr','')}")
                print(f"  저자:   {[x['name'] for x in a.get('authors',[])]}")
                print(f"  연도:   {a.get('year','')}  {a.get('issue','')}호")
                print(f"  초록:   {a.get('abstract_kr','')[:100]}")
                print(f"  키워드: {a.get('keywords_kr','')}")

                sp=Path(OUTPUT_DIR)/f"riss_{args.journal}.json"
                # display_name이 있으면 그 이름으로 저장
                jkey = target.get("display_name") or args.journal
                sp=Path(OUTPUT_DIR)/f"riss_{jkey}.json"
                with open(sp,"w",encoding="utf-8") as f:
                    json.dump(arts, f, ensure_ascii=False, indent=2)
                print(f"\n저장: {sp}")
        finally:
            driver.quit()

    elif args.all:
        crawl_all(skip_detail=args.no_detail)
    else:
        print("사용법:")
        print("  python riss_selenium_crawler.py --journal 선학 --no-detail")
        print("  python riss_selenium_crawler.py --all")

