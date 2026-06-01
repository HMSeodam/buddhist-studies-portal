#!/usr/bin/env python3
# riss_updater.py — 증분 업데이트 (매주 자동 실행용)
#
# 동작:
#   1. output/ 폴더의 학술지별 개별 JSON 파일 로드 (riss_학술지명.json)
#   2. 각 학술지 최신 호수 1~2개만 확인
#   3. 새 논문 ID 발견 시에만 상세 수집
#   4. 해당 학술지 JSON 파일에만 업데이트 저장 (papers.json 사용 안 함)
#
# 사용법:
#   python riss_updater.py           # 최신 1호수씩 확인
#   python riss_updater.py --depth 3 # 최신 3호수씩 확인

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    UnexpectedAlertPresentException,
    NoAlertPresentException,
)
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import json, time, re, hashlib
from pathlib import Path
from datetime import datetime
import argparse

OUTPUT_DIR    = "../output"
REQUEST_DELAY = 2.0
PAGE_LOAD_TIMEOUT = 60
PAGE_LOAD_RETRIES = 3
RETRY_DELAY = 5.0
ALLOW_REFRESH_ON_BLOCK = True
RISS_BASE     = "https://www.riss.kr"

JOURNALS = [
    {"name":"불교미술사학",          "category":"불교미술사학","control_no":"a57013b634c75673"},
    {"name":"동악미술사학",          "category":"불교미술사학","control_no":"58ad7d9d54d869b8ffe0bdc3ef48d419"},
    {"name":"강좌미술사",            "category":"불교미술사학","control_no":"b03a1d4832ed4c7a"},
    {"name":"정토학연구",            "category":"불교학",      "control_no":"20c4186b3804871f"},
    {"name":"선문화연구",            "category":"불교학",      "control_no":"7f818b2e8e8dcabd"},
    {"name":"불교문예연구",          "category":"불교학",      "control_no":"54b47b13bc649e27ffe0bdc3ef48d419"},
    {"name":"불교학보",              "category":"불교학",      "control_no":"27eeee1a652c6cf1"},
    {"name":"불교학연구",            "category":"불교학",      "control_no":"74eb06313eaadb56ffe0bdc3ef48d419"},
    {"name":"한국불교학",            "category":"불교학",      "control_no":"cb236634237a7a74"},
    {"name":"선학",                  "category":"불교학",      "control_no":"d18b923635d64155ffe0bdc3ef48d419"},
    {"name":"불교연구",              "category":"불교학",      "control_no":"a7943149367c4574ffe0bdc3ef48d419"},
    {"name":"동아시아불교문화",      "category":"불교학",      "control_no":"89d7868617dd0940"},
    {"name":"불교철학",              "category":"불교학",      "control_no":"0c20a8836b6e2010ffe0bdc3ef48d419"},
    {"name":"대각사상",              "category":"불교학",      "control_no":"609d6ddc429d15c5"},
    {"name":"보조사상",              "category":"불교학",      "control_no":"51ee8e4df59bd23effe0bdc3ef48d419"},
    {"name":"한국교수불자연합학회지","category":"불교학",      "control_no":"90157c433708510fffe0bdc3ef48d419"},
    {"name":"불교학리뷰",            "category":"불교학",      "control_no":"b4d6ff724148c295ffe0bdc3ef48d419"},
    {"name":"불교학밀교학연구",      "category":"불교학",      "control_no":"adca842359598bb5ffe0bdc3ef48d419"},
    {"name":"인도철학",              "category":"불교학",      "control_no":"6c3aaa42b0296663ffe0bdc3ef48d419"},
    {"name":"명상심리상담",          "category":"불교학",      "control_no":"2158cb1ffaedc442ffe0bdc3ef48d419"},
    {"name":"불교와 사회",           "category":"불교학",      "control_no":"ac622b8ba4ebe87affe0bdc3ef48d419"},
    {"name":"한국불교사연구",        "category":"불교사학",    "control_no":"864d8da7fde953e0ffe0bdc3ef48d419"},
    {"name":"한마음연구",            "category":"불교학",      "control_no":"5324a18d726261b4ffe0bdc3ef48d419"},
    {"name":"IJBTC",                 "category":"불교학",      "control_no":"b44e9e4716ca7ae7ffe0bdc3ef48d419"},
    {"name":"종학연구",              "category":"불교학",      "control_no":"d6dbf60f1a65bfc4ffe0bdc3ef48d419"},
    {"name":"무형문화연구",          "category":"불교학",      "control_no":"e92ddca29a0f1d20ffe0bdc3ef48d419"},
    # display_name: RISS 표시명(세계불학)과 포털 표시명(세화불학) 분리
    {"name":"세계불학", "display_name":"세화불학",  "category":"불교학",  "control_no":"b0e2ccd5057ccc6bffe0bdc3ef48d419"},
    {"name":"전자불전",                              "category":"불교학",  "control_no":"4ed0c31dbf9d9728ffe0bdc3ef48d419"},
    {"name":"원불교사상과 종교문화",                  "category":"불교학",  "control_no":"5c0f0b74c7717105"},
]


# ════════════════════════════════════════
#  드라이버
# ════════════════════════════════════════

try:
    import undetected_chromedriver as uc
    _UC_AVAILABLE = True
except ImportError:
    _UC_AVAILABLE = False

def init_driver(headless=True):
    if _UC_AVAILABLE:
        opts = uc.ChromeOptions()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,900")
        try:
            import subprocess as _sp
            _v = _sp.run(['google-chrome','--version'], capture_output=True, text=True, timeout=5)
            _major = int(re.search(r'(\d+)\.', _v.stdout).group(1))
        except Exception:
            _major = None
        driver = uc.Chrome(options=opts, use_subprocess=True, version_main=_major)
        driver.implicitly_wait(5)
        driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        driver.set_script_timeout(PAGE_LOAD_TIMEOUT)
        print(f"  드라이버: undetected_chromedriver ({'headless' if headless else 'GUI'})")
        return driver

    # ── fallback: 표준 selenium ────────────────────────────
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
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    driver.set_script_timeout(PAGE_LOAD_TIMEOUT)
    print(f"  드라이버: selenium ({'headless' if headless else 'GUI'}, UC 미설치)")
    return driver


def dismiss_alert(driver):
    try:
        alert = driver.switch_to.alert
        text = alert.text
        alert.accept()
        print(f"  ⚠ 알림 닫음: {text[:60]}")
    except NoAlertPresentException:
        pass
    except Exception as e:
        print(f"  ⚠ 알림 처리 실패: {e}")


def page_is_blocked(soup):
    text = soup.get_text(separator=" ").strip()
    if not text:
        return False
    blocked_tokens = [
        "조회중",
        "조회 중",
        "자동화",
        "로봇",
        "bot",
        "차단",
        "차단되었습니다",
        "접근이 제한",
    ]
    return any(token in text for token in blocked_tokens)


def navigate_home_then_url(driver, url):
    try:
        load_page(driver, RISS_BASE, wait_sec=2)
    except Exception:
        pass
    return load_page(driver, url, wait_sec=5)


def load_page(driver, url, wait_sec=4, retries=PAGE_LOAD_RETRIES):
    for attempt in range(1, retries + 1):
        try:
            driver.get(url)
            time.sleep(1)
            page = driver.page_source
            soup = BeautifulSoup(page, "html.parser")
            if page_is_blocked(soup) and attempt < retries and ALLOW_REFRESH_ON_BLOCK:
                print(f"  ⚠ 페이지 차단 의심 감지, 재시도 ({attempt}/{retries})")
                dismiss_alert(driver)
                try:
                    driver.refresh()
                except Exception as e:
                    print(f"  ⚠ 새로고침 실패: {e}")
                time.sleep(RETRY_DELAY)
                continue
            break
        except UnexpectedAlertPresentException:
            dismiss_alert(driver)
            print(f"  ⚠ 알림 때문에 로드 재시도 ({attempt}/{retries})")
        except TimeoutException as e:
            print(f"  ⚠ 페이지 로드 실패 ({attempt}/{retries}): {e}")
            dismiss_alert(driver)
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
        except WebDriverException as e:
            print(f"  ⚠ WebDriver 오류 ({attempt}/{retries}): {e}")
            dismiss_alert(driver)
        if attempt >= retries:
            print(f"  ❌ 최종 실패: {url[:70]}")
            return BeautifulSoup(driver.page_source, "html.parser")
        time.sleep(RETRY_DELAY)
    time.sleep(wait_sec)
    dismiss_alert(driver)
    landed = driver.current_url
    if landed.rstrip("/") != url.rstrip("/") and "DetailView" in url and "DetailView" not in landed:
        print(f"  ⚠ 리다이렉트 감지: {url[:70]}")
    return BeautifulSoup(driver.page_source, "html.parser")


def wait_for_issue_list(driver, timeout=20, allow_refresh=True):
    def is_loaded(d):
        page = d.page_source
        return all(token not in page for token in [
            "조회중",
            "조회 중",
            "조회중 오류",
            "조회 중 오류"
        ])

    for attempt in range(1, 3):
        try:
            WebDriverWait(driver, timeout).until(is_loaded)
            return True
        except TimeoutException:
            print("  ⚠ 조회중 상태가 사라지지 않음")
            dismiss_alert(driver)
            if attempt == 1 and allow_refresh:
                print("  ⟳ 새로고침 후 재시도")
                try:
                    driver.refresh()
                except Exception as e:
                    print(f"  ⚠ 새로고침 실패: {e}")
                time.sleep(RETRY_DELAY)
                continue
            return False
    return False


# ════════════════════════════════════════
#  개별 학술지 JSON 로드 / 저장
# ════════════════════════════════════════

def journal_path(journal_name: str) -> Path:
    return Path(OUTPUT_DIR) / f"riss_{journal_name}.json"

def journal_key(journal: dict) -> str:
    """파일명·표시에 쓰이는 이름 반환 (display_name 우선)."""
    return journal.get("display_name") or journal["name"]

def load_journal(journal_name: str) -> dict:
    """개별 학술지 JSON 파일 로드. 없으면 빈 구조 반환."""
    path = journal_path(journal_name)
    if not path.exists():
        print(f"  ⚠ {path.name} 없음 → 빈 데이터로 시작")
        return {"info": {"name": journal_name}, "articles": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    # 구형 포맷(plain list) 호환 처리
    if isinstance(data, list):
        return {"info": {"name": journal_name}, "articles": data}
    return data

def save_journal(journal_name: str, data: dict):
    """개별 학술지 JSON 파일 저장."""
    path = journal_path(journal_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    sz = path.stat().st_size / 1024 / 1024
    print(f"  저장: {path.name} ({len(data['articles'])}편, {sz:.1f}MB)")

def load_all_existing_ids() -> set:
    """모든 학술지 JSON에서 기존 article_id 전부 수집 (중복 방지용)."""
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
#  최신 호수 목록 (상위 N개만)
# ════════════════════════════════════════

def _collect_issues_from_page(driver, control_no: str, seen: set) -> list:
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
        v_id = m.group(1)
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
            "v_control_no": v_id,
            "issue":        no_m.group(1)  if no_m  else "",
            "volume":       vol_m.group(1) if vol_m else "",
            "label":        txt,
        })
    return found


def get_recent_issues(driver, control_no: str, depth: int) -> list[dict] | None:
    """최신 호수 depth개 수집. riss_selenium_crawler.py의 3단계 클릭 방식 사용."""
    url = (f"{RISS_BASE}/search/detail/DetailView.do"
           f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}")
    navigate_home_then_url(driver, url)
    if not wait_for_issue_list(driver, timeout=20):
        print("  ⚠ 호수 목록 로딩 실패: 차단 또는 로딩 지연")
        return None

    issues = []
    seen   = set()

    # ── 1차: 기본 페이지 HTML에서 바로 수집 ──────────────────────────────
    issues += _collect_issues_from_page(driver, control_no, seen)
    if issues:
        return issues[:depth]

    # ── 2차: 연도 탭 클릭 → JS 렌더링 후 수집 ────────────────────────────
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
                if not wait_for_issue_list(driver, timeout=20):
                    print("    ⚠ 연도 클릭 후 로딩 실패")
                    continue
                before = len(issues)
                issues += _collect_issues_from_page(driver, control_no, seen)
                print(f"{len(issues) - before}개 발견")
            except Exception as e:
                print(f"클릭 실패: {e}")
        if issues:
            return issues[:depth]

    # ── 3차: 연도별 URL 직접 순회 (fallback) ─────────────────────────────
    print("  연도별 URL 방식으로 호수 탐색 중...")
    cur = datetime.now().year
    consecutive_empty = 0
    for year in range(cur, cur - 6, -1):
        if len(issues) >= depth:
            break
        year_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                    f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}"
                    f"&inside_outside=0&v_year={year}")
        navigate_home_then_url(driver, year_url)
        if not wait_for_issue_list(driver, timeout=20):
            print(f"  ⚠ {year}년 페이지 로딩 실패")
            continue
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

    # ── 디버그: 여전히 0개이면 페이지 상태 출력 ──────────────────────────
    if not issues:
        landed = driver.current_url
        print(f"  호수 없음 (현재 URL: {landed[:80]})")
        soup = BeautifulSoup(driver.page_source, "html.parser")
        print("  <a> 태그 샘플:")
        for tag in soup.find_all("a")[:8]:
            h = (tag.get("href") or "")[:60]
            t = tag.get_text(strip=True)[:30]
            print(f"    {t!r:30s}  href={h!r}")

    return issues[:depth]


# ════════════════════════════════════════
#  호수별 논문 수집
# ════════════════════════════════════════

def get_articles_by_issue(driver, issue: dict, control_no: str,
                           journal_name: str, existing_ids: set) -> list[dict]:
    """호수 페이지에서 기존에 없는 논문만 수집"""
    base_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                f"?p_mat_type=3a11008f85f7c51d"
                f"&control_no={control_no}"
                f"&v_control_no={issue['v_control_no']}"
                f"&inside_outside=1")
    navigate_home_then_url(driver, f"{base_url}&currentPage=1&rowPerPage=100")
    if not wait_for_issue_list(driver, timeout=30):
        print("  ⚠ 호수 목록 로딩 실패: 새로고침 후 다시 시도")
        try:
            driver.refresh()
        except Exception as e:
            print(f"  ⚠ 새로고침 실패: {e}")
        time.sleep(RETRY_DELAY)
        navigate_home_then_url(driver, f"{base_url}&currentPage=1&rowPerPage=100")
        if not wait_for_issue_list(driver, timeout=30):
            print("  ⚠ 호수 목록 재로딩에서도 실패")
            return None
    soup = BeautifulSoup(driver.page_source, "html.parser")
    if page_is_blocked(soup):
        print("  ⚠ 차단/로딩 실패 감지: 이 호수의 논문 목록을 가져오지 못함")
        return None

    new_arts = []
    for lk in soup.find_all("a", href=lambda h: h and "p_mat_type=1a0202" in str(h)):
        href  = lk.get("href", "")
        m     = re.search(r"control_no=([a-f0-9]+)", href)
        if not m:
            continue
        art_id = m.group(1)
        if art_id in existing_ids:
            continue  # 이미 있는 논문 → 스킵

        title   = lk.get_text(strip=True)
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
            "journal_name":journal_name,
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
#  논문 상세 수집
# ════════════════════════════════════════

def fetch_detail(driver, article_id: str) -> dict | None:
    url  = (f"{RISS_BASE}/search/detail/DetailView.do"
            f"?p_mat_type=1a0202e37d52c72d&control_no={article_id}")
    soup = load_page(driver, url, wait_sec=5)
    if page_is_blocked(soup):
        print(f"  ⚠ 상세 페이지 차단/로딩 실패: article_id={article_id}")
        return None
    r    = {}

    # 제목
    ti = soup.select_one(".thesisInfo h3.title")
    if ti:
        full = ti.get_text(separator="\n", strip=True)
        parts = re.split(r"\n\s*=\s*", full)
        r["title_kr"] = parts[0].strip()
        if len(parts) > 1:
            r["title_en"] = parts[1].strip()

    # infoDetailL 항목
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
                next_txt = a.next_sibling
                if next_txt and isinstance(next_txt, str):
                    affil = next_txt.strip().strip("()")
                if nm and len(nm) >= 2:
                    authors.append({"name":nm,"affiliation":affil,"order":str(len(authors)+1)})
            if authors:
                r["authors"] = authors

        elif label == "발행연도":
            yr = div.get_text(strip=True)
            if re.match(r"^\d{4}$", yr):
                r["year"] = yr

        elif label == "권호사항":
            txt = div.get_text(separator=" ", strip=True)
            vol_m = re.search(r"Vol\.(\d+(?:[-·~]\d+)?)", txt)
            no_m  = re.search(r"No\.(\d+(?:[-·~]\d+)?)", txt)
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

    # 초록
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

    # KCI 링크
    kci_el = soup.select_one("a[href*='kci.go.kr']")
    if kci_el:
        r["kci_url"] = kci_el.get("href", "")

    return r


# ════════════════════════════════════════
#  학술지별 병합 및 저장
# ════════════════════════════════════════

def merge_and_save_journal(journal_name: str, new_articles: list[dict],
                            existing_ids: set) -> int:
    """새 논문을 해당 학술지 JSON 파일에 병합하고 저장."""
    if not new_articles:
        return 0

    data = load_journal(journal_name)
    current_articles = data.get("articles", [])

    # 기존 article_id 집합 (이 파일 내)
    file_ids = {a.get("article_id") or a.get("id", "") for a in current_articles}

    added = 0
    for art in new_articles:
        aid = art.get("article_id") or _gid(art)
        art["id"] = aid
        if aid in file_ids:
            continue
        current_articles.append(art)
        file_ids.add(aid)
        existing_ids.add(aid)   # 전역 중복 방지 집합도 업데이트
        added += 1

    if added == 0:
        return 0

    # info 보존 (기존 것 우선)
    if "info" not in data:
        data["info"] = {"name": journal_name}
    data["info"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    data["articles"] = current_articles

    save_journal(journal_name, data)
    return added


# ════════════════════════════════════════
#  메인
# ════════════════════════════════════════

def filter_journals(journal_names: list[str] | None):
    if not journal_names:
        return JOURNALS
    names = {name.strip().lower() for name in journal_names}
    selected = []
    for journal in JOURNALS:
        if journal_key(journal).lower() in names or journal["name"].lower() in names:
            selected.append(journal)
    if not selected:
        print(f"  ⚠ 지정한 학회지를 찾을 수 없습니다: {journal_names}")
        return JOURNALS
    return selected


def run(depth=1, headless=True, journal_names=None):
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # 모든 학술지 파일에서 기존 ID 수집
    existing_ids = load_all_existing_ids()

    print(f"\nChrome 초기화...")
    driver = init_driver(headless=headless)

    # RISS 세션 쿠키 확보 (직접 상세 URL 접근 시 홈으로 튕기는 현상 방지)
    print("  RISS 세션 워밍업 중...")
    driver.get(RISS_BASE)
    time.sleep(4)
    print(f"  세션 확보 완료")

    total_new = 0
    pending_journals = list(filter_journals(journal_names))
    retry_journals = []
    second_pass = False

    try:
        while pending_journals:
            journal = pending_journals.pop(0)
            name       = journal_key(journal)   # display_name 우선
            control_no = journal["control_no"]

            print(f"\n[{name}] 최신 {depth}호수 확인 중...")

            # 최신 호수 목록
            try:
                issues = get_recent_issues(driver, control_no, depth)
            except Exception as e:
                print(f"  ⚠ {name} 최신 호수 수집 중 예외: {e}")
                if not second_pass:
                    retry_journals.append(journal)
                continue
            if issues is None:
                print(f"  ⚠ {name} 처리 실패, 다음 학회지로 우선 이동")
                if not second_pass:
                    retry_journals.append(journal)
                continue
            if not issues:
                continue

            journal_new = []
            blocked_issue_encountered = False
            for iss in issues:
                label = f"Vol.{iss['volume']} No.{iss['issue']}" if iss['volume'] else iss['label']
                arts  = get_articles_by_issue(driver, iss, control_no, name, existing_ids)

                if arts is None:
                    print(f"  [{label}] 차단/로딩 실패로 인해 건너뜀")
                    blocked_issue_encountered = True
                    continue
                if not arts:
                    print(f"  [{label}] 새 논문 없음")
                    continue

                print(f"  [{label}] 새 논문 {len(arts)}편 발견 → 상세 수집 중...")

                # 상세 수집
                for i, art in enumerate(arts):
                    try:
                        d = fetch_detail(driver, art["article_id"])
                        if d is None:
                            print(f"    ⚠ 상세 수집 실패: {art['article_id']}")
                            continue
                        for k, v in d.items():
                            if v: art[k] = v
                        existing_ids.add(art["article_id"])  # 중복 방지
                    except Exception as e:
                        print(f"    ⚠ 상세 수집 예외: {art['article_id']} / {e}")
                        continue
                    time.sleep(REQUEST_DELAY)

                journal_new.extend(arts)
                time.sleep(REQUEST_DELAY)

            if journal_new:
                added = merge_and_save_journal(name, journal_new, existing_ids)
                total_new += added
                print(f"  → {name}: {added}편 추가")
            else:
                print(f"  → 새 논문 없음")

            if blocked_issue_encountered and not second_pass:
                retry_journals.append(journal)

            time.sleep(REQUEST_DELAY)

            if not pending_journals and retry_journals and not second_pass:
                print("\n차단/로딩 실패한 학회지를 다시 시도합니다...")
                pending_journals = retry_journals
                retry_journals = []
                second_pass = True

        if retry_journals:
            print("\n다시 시도 후에도 실패한 학회지: ")
            for journal in retry_journals:
                print(f"  - {journal_key(journal)}")

    finally:
        driver.quit()

    print(f"\n{'='*50}")
    print(f"✅ 증분 업데이트 완료")
    print(f"   새로 추가된 논문: {total_new}편")
    print(f"   업데이트 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RISS 증분 업데이트")
    parser.add_argument("--depth", type=int, default=1,
                        help="학술지당 확인할 최신 호수 개수 (기본: 1)")
    parser.add_argument("--journal", nargs="+",
                        help="업데이트할 학회지 이름을 하나 이상 지정")
    parser.add_argument("--no-headless", dest="headless", action="store_false",
                        help="GUI 브라우저로 실행")
    parser.set_defaults(headless=True)
    args = parser.parse_args()

    run(depth=args.depth, headless=args.headless, journal_names=args.journal)
