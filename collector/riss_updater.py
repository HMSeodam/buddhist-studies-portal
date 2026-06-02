#!/usr/bin/env python3
# riss_updater.py — 요일별 분산 업데이트 버전
#
# 동작:
#   월~토: 지정된 5개 학술지, 최신 2호수 확인
#   일:    전체 29개 학술지, 최신 1호수 확인 (전체 점검)
#
# 사용법:
#   python riss_updater.py              # 오늘 요일 자동 판별
#   python riss_updater.py --day mon    # 요일 직접 지정
#   python riss_updater.py --day sun    # 일요일 전체 점검
#   python riss_updater.py --day all    # 전체 강제 실행 (수동용)

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import json, time, re, hashlib
from pathlib import Path
from datetime import datetime
import argparse

OUTPUT_DIR = "../output"
RISS_BASE  = "https://www.riss.kr"

# ── 딜레이 설정 ──────────────────────────────────────────────
WARMUP_DELAY  = 6      # 세션 워밍업 대기
PAGE_LOAD     = 6      # 페이지 로드 후 기본 대기
CLICK_WAIT    = 5      # 연도 탭 클릭 후 대기 (조회중... 로딩 대기)
CLICK_RETRY   = 5      # 클릭 후 0개일 때 재대기
ISSUE_DELAY   = 6      # 호수 페이지 로드 대기
DETAIL_LOAD   = 8      # 논문 상세 페이지 로드 대기
DETAIL_DELAY  = 10     # 논문 상세 수집 후 대기 (빈 데이터 방지)
JOURNAL_DELAY = 40     # 학술지 간 대기 (누적 차단 방지 핵심)
RETRY_WAIT    = [15, 30, 60]  # 재시도 대기 (지수 백오프)

# ── 요일별 학술지 배정 (월~토: 5개씩, depth=2) ───────────────
SCHEDULE = {
    "mon": ["불교미술사학", "동악미술사학", "강좌미술사", "정토학연구", "선문화연구"],
    "tue": ["불교문예연구", "불교학보", "불교학연구", "한국불교학", "선학"],
    "wed": ["불교연구", "동아시아불교문화", "불교철학", "대각사상", "보조사상"],
    "thu": ["한국교수불자연합학회지", "불교학리뷰", "불교학밀교학연구", "인도철학", "명상심리상담"],
    "fri": ["불교와 사회", "한국불교사연구", "한마음연구", "IJBTC", "종학연구"],
    "sat": ["무형문화연구", "세화불학", "전자불전", "원불교사상과 종교문화", "불교미술사학"],
    # 일요일: 전체 학술지 1호수 점검 (아래 JOURNALS 전체 사용)
}

DAY_MAP = {0:"mon", 1:"tue", 2:"wed", 3:"thu", 4:"fri", 5:"sat", 6:"sun"}

# ── 전체 학술지 목록 ─────────────────────────────────────────
JOURNALS = [
    {"name":"불교미술사학",           "category":"불교미술사학", "control_no":"a57013b634c75673"},
    {"name":"동악미술사학",           "category":"불교미술사학", "control_no":"58ad7d9d54d869b8ffe0bdc3ef48d419"},
    {"name":"강좌미술사",             "category":"불교미술사학", "control_no":"b03a1d4832ed4c7a"},
    {"name":"정토학연구",             "category":"불교학",       "control_no":"20c4186b3804871f"},
    {"name":"선문화연구",             "category":"불교학",       "control_no":"7f818b2e8e8dcabd"},
    {"name":"불교문예연구",           "category":"불교학",       "control_no":"54b47b13bc649e27ffe0bdc3ef48d419"},
    {"name":"불교학보",               "category":"불교학",       "control_no":"27eeee1a652c6cf1"},
    {"name":"불교학연구",             "category":"불교학",       "control_no":"74eb06313eaadb56ffe0bdc3ef48d419"},
    {"name":"한국불교학",             "category":"불교학",       "control_no":"cb236634237a7a74"},
    {"name":"선학",                   "category":"불교학",       "control_no":"d18b923635d64155ffe0bdc3ef48d419"},
    {"name":"불교연구",               "category":"불교학",       "control_no":"a7943149367c4574ffe0bdc3ef48d419"},
    {"name":"동아시아불교문화",       "category":"불교학",       "control_no":"89d7868617dd0940"},
    {"name":"불교철학",               "category":"불교학",       "control_no":"0c20a8836b6e2010ffe0bdc3ef48d419"},
    {"name":"대각사상",               "category":"불교학",       "control_no":"609d6ddc429d15c5"},
    {"name":"보조사상",               "category":"불교학",       "control_no":"51ee8e4df59bd23effe0bdc3ef48d419"},
    {"name":"한국교수불자연합학회지", "category":"불교학",       "control_no":"90157c433708510fffe0bdc3ef48d419"},
    {"name":"불교학리뷰",             "category":"불교학",       "control_no":"b4d6ff724148c295ffe0bdc3ef48d419"},
    {"name":"불교학밀교학연구",       "category":"불교학",       "control_no":"adca842359598bb5ffe0bdc3ef48d419"},
    {"name":"인도철학",               "category":"불교학",       "control_no":"6c3aaa42b0296663ffe0bdc3ef48d419"},
    {"name":"명상심리상담",           "category":"불교학",       "control_no":"2158cb1ffaedc442ffe0bdc3ef48d419"},
    {"name":"불교와 사회",            "category":"불교학",       "control_no":"ac622b8ba4ebe87affe0bdc3ef48d419"},
    {"name":"한국불교사연구",         "category":"불교사학",     "control_no":"864d8da7fde953e0ffe0bdc3ef48d419"},
    {"name":"한마음연구",             "category":"불교학",       "control_no":"5324a18d726261b4ffe0bdc3ef48d419"},
    {"name":"IJBTC",                  "category":"불교학",       "control_no":"b44e9e4716ca7ae7ffe0bdc3ef48d419"},
    {"name":"종학연구",               "category":"불교학",       "control_no":"d6dbf60f1a65bfc4ffe0bdc3ef48d419"},
    {"name":"무형문화연구",           "category":"불교학",       "control_no":"e92ddca29a0f1d20ffe0bdc3ef48d419"},
    {"name":"세계불학", "display_name":"세화불학", "category":"불교학", "control_no":"b0e2ccd5057ccc6bffe0bdc3ef48d419"},
    {"name":"전자불전",               "category":"불교학",       "control_no":"4ed0c31dbf9d9728ffe0bdc3ef48d419"},
    {"name":"원불교사상과 종교문화",  "category":"불교학",       "control_no":"5c0f0b74c7717105"},
]

# name → control_no 빠른 조회용
NAME_TO_JOURNAL = {
    (j.get("display_name") or j["name"]): j for j in JOURNALS
}


# ════════════════════════════════════════
#  유틸리티
# ════════════════════════════════════════

def _gid(art: dict) -> str:
    s = f"{art.get('title_kr','')}{art.get('journal_name','')}{art.get('year','')}"
    return hashlib.md5(s.encode()).hexdigest()[:16]

def journal_key(j: dict) -> str:
    return j.get("display_name") or j["name"]


# ════════════════════════════════════════
#  드라이버 초기화
# ════════════════════════════════════════

try:
    import undetected_chromedriver as uc
    _UC_AVAILABLE = True
except ImportError:
    _UC_AVAILABLE = False

def init_driver():
    if _UC_AVAILABLE:
        opts = uc.ChromeOptions()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,900")
        try:
            import subprocess as _sp
            _major = None
            # Windows: 레지스트리에서 Chrome 버전 읽기
            for reg_cmd in [
                r'reg query "HKCU\Software\Google\Chrome\BLBeacon" /v version',
                r'reg query "HKLM\SOFTWARE\Google\Chrome\BLBeacon" /v version',
                r'reg query "HKLM\SOFTWARE\WOW6432Node\Google\Chrome\BLBeacon" /v version',
            ]:
                try:
                    _v = _sp.run(reg_cmd, capture_output=True, text=True,
                                 shell=True, timeout=5)
                    _m = re.search(r'(\d+)\.\d+\.\d+', _v.stdout)
                    if _m:
                        _major = int(_m.group(1))
                        print(f"  Chrome 버전 감지: {_major}")
                        break
                except Exception:
                    continue
            # Linux/Mac fallback
            if _major is None:
                for cmd in [['google-chrome', '--version'],
                            ['chromium-browser', '--version'],
                            ['chromium', '--version']]:
                    try:
                        _v = _sp.run(cmd, capture_output=True, text=True, timeout=5)
                        _m = re.search(r'(\d+)\.', _v.stdout)
                        if _m:
                            _major = int(_m.group(1))
                            break
                    except Exception:
                        continue
        except Exception:
            _major = None
        driver = uc.Chrome(options=opts, use_subprocess=True, version_main=_major)
        driver.implicitly_wait(8)
        print("  드라이버: undetected_chromedriver (headless)")
        return driver

    opts = Options()
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
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=opts
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    driver.implicitly_wait(8)
    print("  드라이버: selenium fallback (headless)")
    return driver


# ════════════════════════════════════════
#  페이지 로드 (재시도 포함)
# ════════════════════════════════════════

def load_page(driver, url, wait_sec=PAGE_LOAD, retries=3):
    for attempt in range(retries):
        try:
            driver.get(url)
            time.sleep(wait_sec)
            if len(driver.page_source) < 500:
                raise Exception("페이지 내용 없음 (빈 응답)")
            if "DetailView" in url and "DetailView" not in driver.current_url:
                print(f"  ⚠ 리다이렉트 감지: {url[:60]}")
            return BeautifulSoup(driver.page_source, "html.parser")
        except Exception as e:
            wait = RETRY_WAIT[min(attempt, len(RETRY_WAIT)-1)]
            print(f"  ⚠ 로드 실패 (시도 {attempt+1}/{retries}): {e} → {wait}초 대기")
            time.sleep(wait)
    print(f"  ✗ 로드 포기: {url[:60]}")
    return BeautifulSoup("", "html.parser")


# ════════════════════════════════════════
#  JSON 로드 / 저장
# ════════════════════════════════════════

def journal_path(name: str) -> Path:
    return Path(OUTPUT_DIR) / f"riss_{name}.json"

def load_journal(name: str) -> dict:
    path = journal_path(name)
    if not path.exists():
        print(f"  ⚠ {path.name} 없음 → 빈 데이터로 시작")
        return {"info": {"name": name}, "articles": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"info": {"name": name}, "articles": data}
    return data

def save_journal(name: str, data: dict):
    path = journal_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    sz = path.stat().st_size / 1024 / 1024
    print(f"  저장: {path.name} ({len(data['articles'])}편, {sz:.1f}MB)")

def load_all_existing_ids() -> set:
    ids = set()
    for path in Path(OUTPUT_DIR).glob("riss_*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            arts = data.get("articles", data) if isinstance(data, dict) else data
            for a in arts:
                aid = a.get("article_id") or a.get("id", "")
                if aid:
                    ids.add(aid)
        except Exception as e:
            print(f"  ⚠ {path.name} 로드 실패: {e}")
    print(f"기존 전체 데이터: {len(ids)}편")
    return ids


# ════════════════════════════════════════
#  호수 수집
# ════════════════════════════════════════

def _collect_issues_from_page(driver, control_no: str, seen: set) -> list:
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
        no_m  = re.search(r"No\.(\d+)",  txt)
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
            "v_control_no": v_id,
            "issue":  no_m.group(1)  if no_m  else "",
            "volume": vol_m.group(1) if vol_m else "",
            "label":  txt,
        })
    return found


def get_recent_issues(driver, control_no: str, depth: int) -> list:
    url = (f"{RISS_BASE}/search/detail/DetailView.do"
           f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}")
    driver.get(url)
    time.sleep(PAGE_LOAD)

    issues = []
    seen   = set()

    # ── 1차: 기본 HTML에서 바로 수집 ──────────────────────────
    issues += _collect_issues_from_page(driver, control_no, seen)
    if issues:
        return issues[:depth]

    # ── 2차: 연도 탭 클릭 ─────────────────────────────────────
    print("  연도 클릭 방식으로 호수 탐색 중...")
    year_els = [
        el for el in driver.find_elements(By.TAG_NAME, "a")
        if re.match(r"^(19|20)\d{2}", el.text.strip())
    ]
    if year_els:
        for el in year_els:
            if len(issues) >= depth:
                break
            try:
                year_txt = el.text.strip()
                print(f"    [{year_txt}] 클릭...", end=" ", flush=True)
                driver.execute_script("arguments[0].click();", el)
                time.sleep(CLICK_WAIT)   # 조회중... 로딩 대기
                before  = len(issues)
                issues += _collect_issues_from_page(driver, control_no, seen)
                # 0개면 한 번 더 대기 후 재시도
                if len(issues) - before == 0:
                    time.sleep(CLICK_RETRY)
                    issues += _collect_issues_from_page(driver, control_no, seen)
                print(f"{len(issues) - before}개 발견")
            except Exception as e:
                print(f"클릭 실패: {e}")
        if issues:
            return issues[:depth]

    # ── 3차: 연도별 URL 직접 순회 (fallback) ──────────────────
    print("  연도별 URL 방식으로 호수 탐색 중...")
    cur = datetime.now().year
    consecutive_empty = 0
    for year in range(cur, cur - 6, -1):
        if len(issues) >= depth:
            break
        year_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                    f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}"
                    f"&inside_outside=0&v_year={year}")
        driver.get(year_url)
        time.sleep(PAGE_LOAD)
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

    if not issues:
        print(f"  ⚠ 호수 없음 (URL: {driver.current_url[:80]})")

    return issues[:depth]


# ════════════════════════════════════════
#  호수별 논문 목록 수집
# ════════════════════════════════════════

def get_articles_by_issue(driver, issue: dict, control_no: str,
                           journal_name: str, existing_ids: set) -> list:
    base_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                f"?p_mat_type=3a11008f85f7c51d"
                f"&control_no={control_no}"
                f"&v_control_no={issue['v_control_no']}"
                f"&inside_outside=1&currentPage=1&rowPerPage=100")
    soup = load_page(driver, base_url, wait_sec=ISSUE_DELAY)

    new_arts = []
    for lk in soup.find_all("a", href=lambda h: h and "p_mat_type=1a0202" in str(h)):
        href   = lk.get("href", "")
        m      = re.search(r"control_no=([a-f0-9]+)", href)
        if not m:
            continue
        art_id = m.group(1)
        if art_id in existing_ids:
            continue
        title = lk.get_text(strip=True)
        if not title or len(title) < 2:
            continue
        full_url = RISS_BASE + href if href.startswith("/") else href
        new_arts.append({
            "article_id":  art_id,
            "title_kr":    title,
            "title_en":    "",
            "authors":     [],
            "abstract_kr": "",
            "abstract_en": "",
            "journal_name": journal_name,
            "year":        "",
            "volume":      issue.get("volume", ""),
            "issue":       issue.get("issue", ""),
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
    return new_arts


# ════════════════════════════════════════
#  논문 상세 수집 (재시도 + 빈 데이터 방지)
# ════════════════════════════════════════

def fetch_detail(driver, article_id: str, retries=3) -> dict:
    url = (f"{RISS_BASE}/search/detail/DetailView.do"
           f"?p_mat_type=1a0202e37d52c72d&control_no={article_id}")

    for attempt in range(retries):
        soup = load_page(driver, url, wait_sec=DETAIL_LOAD)
        r    = {}

        # 핵심 영역 없으면 빈 페이지 → 재시도
        if not soup.select_one(".thesisInfo") and attempt < retries - 1:
            wait = RETRY_WAIT[min(attempt, len(RETRY_WAIT)-1)]
            print(f"  ⚠ 상세 페이지 비어있음 (시도 {attempt+1}/{retries}) → {wait}초 대기")
            time.sleep(wait)
            continue

        # ── 제목 ──────────────────────────────────────────────
        ti = soup.select_one(".thesisInfo h3.title")
        if ti:
            full  = ti.get_text(separator="\n", strip=True)
            parts = re.split(r"\n\s*=\s*", full)
            r["title_kr"] = parts[0].strip()
            if len(parts) > 1:
                r["title_en"] = parts[1].strip()

        # ── infoDetailL 항목 ───────────────────────────────────
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
                    affil = ""
                    nxt = a.next_sibling
                    if nxt and isinstance(nxt, str):
                        affil = nxt.strip().strip("()")
                    if nm and len(nm) >= 2:
                        authors.append({
                            "name": nm, "affiliation": affil,
                            "order": str(len(authors)+1)
                        })
                if authors:
                    r["authors"] = authors

            elif label == "발행연도":
                yr = div.get_text(strip=True)
                if re.match(r"^\d{4}$", yr):
                    r["year"] = yr

            elif label == "권호사항":
                txt   = div.get_text(separator=" ", strip=True)
                vol_m = re.search(r"Vol\.(\d+(?:[-·~]\d+)?)", txt)
                no_m  = re.search(r"No\.(\d+(?:[-·~]\d+)?)",  txt)
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

        # ── 초록 ──────────────────────────────────────────────
        abs_el = soup.select_one("#abs1.textWrap, #abs1 .textWrap")
        if abs_el:
            t = abs_el.get_text(strip=True)
            if t and len(t) > 20:
                r["abstract_kr"] = t

        abs_en = soup.select_one("#abs2.textWrap, #abs2 .textWrap")
        if abs_en:
            t = abs_en.get_text(strip=True)
            if t and len(t) > 20:
                r["abstract_en"] = t

        # ── KCI 링크 ──────────────────────────────────────────
        kci_el = soup.select_one("a[href*='kci.go.kr']")
        if kci_el:
            r["kci_url"] = kci_el.get("href", "")

        break  # 정상 파싱 완료

    return r


# ════════════════════════════════════════
#  병합 저장
# ════════════════════════════════════════

def merge_and_save_journal(name: str, new_articles: list, existing_ids: set) -> int:
    if not new_articles:
        return 0
    data = load_journal(name)
    cur  = data.get("articles", [])
    file_ids = {a.get("article_id") or a.get("id", "") for a in cur}
    added = 0
    for art in new_articles:
        aid = art.get("article_id") or _gid(art)
        art["id"] = aid
        if aid in file_ids:
            continue
        cur.append(art)
        file_ids.add(aid)
        existing_ids.add(aid)
        added += 1
    if added == 0:
        return 0
    if "info" not in data:
        data["info"] = {"name": name}
    data["info"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    data["articles"] = cur
    save_journal(name, data)
    return added


# ════════════════════════════════════════
#  메인
# ════════════════════════════════════════

def get_today_journals(day_key: str):
    """요일에 해당하는 학술지 목록과 depth 반환."""
    if day_key == "sun":
        # 일요일: 전체 학술지, depth=1
        return JOURNALS, 1
    if day_key == "all":
        # 수동 전체 실행: 전체 학술지, depth=2
        return JOURNALS, 2
    names = SCHEDULE.get(day_key, [])
    target = [NAME_TO_JOURNAL[n] for n in names if n in NAME_TO_JOURNAL]
    return target, 2


def run(day_key: str, depth_override: int = None):
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    existing_ids = load_all_existing_ids()

    target_journals, depth = get_today_journals(day_key)
    if depth_override is not None:
        depth = depth_override
        print(f"  depth override: {depth}")
    if not target_journals:
        print(f"⚠ [{day_key}] 처리할 학술지 없음")
        return

    print(f"\n▶ 오늘 요일: {day_key.upper()} | 대상: {len(target_journals)}개 학술지 | depth={depth}")
    for j in target_journals:
        print(f"   - {journal_key(j)}")

    print(f"\nChrome 초기화...")
    driver = init_driver()

    print("  RISS 세션 워밍업 중...")
    driver.get(RISS_BASE)
    time.sleep(WARMUP_DELAY)
    print("  세션 확보 완료")

    total_new = 0

    try:
        for journal in target_journals:
            name       = journal_key(journal)
            control_no = journal["control_no"]
            print(f"\n[{name}] 최신 {depth}호수 확인 중...")

            issues = get_recent_issues(driver, control_no, depth)
            if not issues:
                time.sleep(JOURNAL_DELAY)
                continue

            journal_new = []
            for iss in issues:
                label = (f"Vol.{iss['volume']} No.{iss['issue']}"
                         if iss.get("volume") else iss.get("label", "?"))
                arts = get_articles_by_issue(driver, iss, control_no, name, existing_ids)

                if not arts:
                    print(f"  [{label}] 새 논문 없음")
                    continue

                print(f"  [{label}] 새 논문 {len(arts)}편 발견 → 상세 수집 중...")
                for art in arts:
                    d = fetch_detail(driver, art["article_id"])
                    for k, v in d.items():
                        if v:
                            art[k] = v
                    existing_ids.add(art["article_id"])
                    time.sleep(DETAIL_DELAY)   # 상세 수집 후 충분한 대기

                journal_new.extend(arts)
                time.sleep(DETAIL_DELAY)

            if journal_new:
                added = merge_and_save_journal(name, journal_new, existing_ids)
                total_new += added
                print(f"  → {name}: {added}편 추가")
            else:
                print(f"  → 새 논문 없음")

            time.sleep(JOURNAL_DELAY)   # 학술지 간 충분한 대기

    finally:
        driver.quit()

    print(f"\n{'='*50}")
    print(f"✅ 업데이트 완료 [{day_key.upper()}]")
    print(f"   새로 추가된 논문: {total_new}편")
    print(f"   업데이트 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RISS 요일별 분산 업데이트")
    parser.add_argument(
        "--day",
        choices=["mon","tue","wed","thu","fri","sat","sun","all"],
        default=None,
        help="요일 지정 (기본: 오늘 자동 판별). all=전체 강제 실행"
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=None,
        help="최신 호수 확인 개수 (1 또는 2). 미지정 시 요일 기본값 사용"
    )
    args = parser.parse_args()

    if args.day:
        day_key = args.day
    else:
        day_key = DAY_MAP[datetime.now().weekday()]

    print(f"실행 요일: {day_key.upper()}")
    run(day_key, depth_override=args.depth)
