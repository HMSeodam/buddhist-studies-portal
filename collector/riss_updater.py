#!/usr/bin/env python3
# riss_updater.py — RISS 증분 업데이트 안정화 버전 (GitHub Actions용)
#
# 목적:
#   - 공개 RISS 페이지에 과도한 요청을 보내지 않도록 조회량과 속도를 낮춤
#   - RISS 페이지의 Javascript alert / 로딩 지연 / 세션 불안정을 복구함
#   - 한 학술지 또는 한 논문의 실패가 전체 업데이트를 중단시키지 않도록 함
#   - 상세 수집에 실패한 신규 논문은 목록 메타데이터로 먼저 저장하고 다음 실행에서 보강함
#
# 주의:
#   - 접근 제한을 속이거나 인증을 회피하는 기능은 포함하지 않음
#   - 장기적으로는 RISS OpenAPI 발급 후 API 기반 수집으로 전환하는 편이 안정적임
#
# 사용법:
#   python riss_updater.py             # 최신 1호수씩 확인
#   python riss_updater.py --depth 2   # 최신 2호수씩 확인

from __future__ import annotations

import argparse
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    NoAlertPresentException,
    TimeoutException,
    UnexpectedAlertPresentException,
    WebDriverException,
)
from selenium.webdriver.chrome.options import Options

OUTPUT_DIR = "../output"
RISS_BASE = "https://www.riss.kr"

# RISS 페이지에 부담을 주지 않기 위한 보수적 설정
REQUEST_DELAY = 5.0          # 정상 페이지 이동 사이 기본 대기
RETRY_BASE_DELAY = 10.0      # 오류 재시도 시 대기
PAGE_LOAD_TIMEOUT = 30       # 한 요청이 장시간 멈추지 않도록 제한
MAX_PAGE_RETRIES = 3
ISSUE_PAGE_SIZE = 10         # RISS 기본 표시 수와 동일하게 요청
MAX_ISSUE_PAGES = 20
MAX_PENDING_ENRICH = 8       # 실행당 학술지별 보강 재시도 수

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
#  공통 유틸리티
# ════════════════════════════════════════

class PageLoadFailure(RuntimeError):
    """RISS 페이지를 반복 시도 후에도 읽지 못한 경우."""


def polite_sleep(seconds: float = REQUEST_DELAY) -> None:
    """동일한 간격의 기계적 연속 요청을 피하고 사이트 부하를 낮춘다."""
    time.sleep(seconds + random.uniform(0.4, 1.4))


def article_id_of(article: dict) -> str:
    return article.get("article_id") or article.get("id", "")


def parse_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# ════════════════════════════════════════
#  브라우저 세션: alert·timeout 복구 포함
# ════════════════════════════════════════

class BrowserSession:
    def __init__(self) -> None:
        self.driver: Optional[webdriver.Chrome] = None
        self.start()

    def start(self) -> None:
        opts = Options()
        opts.page_load_strategy = "eager"
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,1000")
        opts.add_argument("--lang=ko-KR")
        # 일반 브라우저로 공개 페이지를 저빈도로 읽는다. 자동화 은폐 스크립트는 사용하지 않는다.
        self.driver = webdriver.Chrome(options=opts)
        self.driver.implicitly_wait(3)
        self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        print("  드라이버: selenium Chrome (eager, headless)")

    def close(self) -> None:
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def dismiss_alert(self) -> str:
        if not self.driver:
            return ""
        try:
            alert = self.driver.switch_to.alert
            text = alert.text or "(내용 없음)"
            alert.accept()
            return text
        except NoAlertPresentException:
            return ""
        except Exception:
            return "(경고창 닫기 실패)"

    def warmup(self) -> None:
        assert self.driver is not None
        try:
            self.driver.get(RISS_BASE)
            polite_sleep(3.0)
            alert_text = self.dismiss_alert()
            if alert_text:
                print(f"  ⚠ 워밍업 중 RISS alert: {alert_text}")
        except Exception as e:
            print(f"  ⚠ RISS 세션 워밍업 실패(진행 계속): {type(e).__name__}: {e}")

    def reset(self, reason: str) -> None:
        print(f"    ↻ 브라우저 세션 재시작: {reason}")
        self.close()
        polite_sleep(RETRY_BASE_DELAY)
        self.start()
        self.warmup()

    def load_page(
        self,
        url: str,
        wait_sec: float = 2.0,
        validator: Optional[Callable[[BeautifulSoup], bool]] = None,
        purpose: str = "페이지",
        retries: int = MAX_PAGE_RETRIES,
    ) -> BeautifulSoup:
        """페이지를 로드하며 alert/timeout/세션 오류를 처리하고 제한적으로 재시도한다."""
        last_error: Optional[BaseException] = None

        for attempt in range(1, retries + 1):
            assert self.driver is not None
            try:
                self.driver.get(url)
                polite_sleep(wait_sec)

                alert_text = self.dismiss_alert()
                if alert_text:
                    raise RuntimeError(f"RISS alert: {alert_text}")

                soup = parse_soup(self.driver.page_source)
                if validator is not None and not validator(soup):
                    raise RuntimeError("필요한 본문 요소가 확인되지 않음")

                return soup

            except UnexpectedAlertPresentException as e:
                last_error = e
                alert_text = self.dismiss_alert()
                print(f"    ⚠ {purpose} alert ({attempt}/{retries}): {alert_text or str(e)[:120]}")
                if attempt < retries:
                    self.reset("RISS alert 발생")

            except TimeoutException as e:
                last_error = e
                print(f"    ⚠ {purpose} 로딩 시간 초과 ({attempt}/{retries})")
                try:
                    self.driver.execute_script("window.stop();")
                    soup = parse_soup(self.driver.page_source)
                    if validator is None or validator(soup):
                        print("    → 로딩은 지연되었으나 필요한 HTML은 확보됨")
                        return soup
                except Exception:
                    pass
                if attempt < retries:
                    self.reset("페이지 로딩 시간 초과")

            except (WebDriverException, RuntimeError) as e:
                last_error = e
                print(f"    ⚠ {purpose} 실패 ({attempt}/{retries}): {str(e)[:160]}")
                if attempt < retries:
                    self.reset(f"{purpose} 오류")

            if attempt < retries:
                polite_sleep(RETRY_BASE_DELAY * attempt)

        raise PageLoadFailure(f"{purpose} 최종 실패: {url}") from last_error


# ════════════════════════════════════════
#  개별 학술지 JSON 로드 / 저장
# ════════════════════════════════════════

def journal_path(journal_name: str) -> Path:
    return Path(OUTPUT_DIR) / f"riss_{journal_name}.json"


def journal_key(journal: dict) -> str:
    return journal.get("display_name") or journal["name"]


def load_journal(journal_name: str) -> dict:
    path = journal_path(journal_name)
    if not path.exists():
        print(f"  ⚠ {path.name} 없음 → 빈 데이터로 시작")
        return {"info": {"name": journal_name}, "articles": []}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return {"info": {"name": journal_name}, "articles": data}
    return data


def save_journal(journal_name: str, data: dict) -> None:
    path = journal_path(journal_name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    sz = path.stat().st_size / 1024 / 1024
    print(f"  저장: {path.name} ({len(data['articles'])}편, {sz:.1f}MB)")


def load_all_existing_ids() -> set[str]:
    ids: set[str] = set()
    for path in Path(OUTPUT_DIR).glob("riss_*.json"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            arts = data.get("articles", data) if isinstance(data, dict) else data
            for article in arts:
                aid = article_id_of(article)
                if aid:
                    ids.add(aid)
        except Exception as e:
            print(f"  ⚠ {path.name} 로드 실패: {e}")
    print(f"기존 전체 데이터: {len(ids)}편")
    return ids


# ════════════════════════════════════════
#  최신 호수 목록: 클릭 대신 연도 URL 조회
# ════════════════════════════════════════

def collect_issues_from_soup(soup: BeautifulSoup, control_no: str, seen: set[str], year: str = "") -> list[dict]:
    found: list[dict] = []
    for a in soup.find_all("a"):
        href = a.get("href", "") or ""
        onclick = a.get("onclick", "") or ""
        source = href if "v_control_no" in href else onclick if "v_control_no" in onclick else ""
        if not source:
            continue
        match = re.search(r"v_control_no=([a-f0-9]+)", source)
        if not match or match.group(1) in seen:
            continue
        v_id = match.group(1)
        seen.add(v_id)
        text = a.get_text(strip=True)
        no_m = re.search(r"No\.(\d+)", text)
        vol_m = re.search(r"Vol\.(\d+)", text)
        found.append({
            "v_control_no": v_id,
            "issue": no_m.group(1) if no_m else "",
            "volume": vol_m.group(1) if vol_m else "",
            "label": text,
            "year": year,
        })
    return found


def get_recent_issues(browser: BrowserSession, control_no: str, depth: int) -> list[dict]:
    """JS 연도 클릭을 반복하지 않고 연도별 공개 URL로 최신 호수를 찾는다."""
    issues: list[dict] = []
    seen: set[str] = set()
    current_year = datetime.now().year
    empty_years = 0

    print("  연도 URL 방식으로 호수 탐색 중...")
    for year in range(current_year, current_year - 6, -1):
        if len(issues) >= depth:
            break
        url = (
            f"{RISS_BASE}/search/detail/DetailView.do"
            f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}"
            f"&inside_outside=1&currentPage=1&rowPerPage={ISSUE_PAGE_SIZE}&v_year={year}"
        )
        try:
            soup = browser.load_page(url, wait_sec=2.0, purpose=f"{year}년 호수 목록")
        except PageLoadFailure as e:
            print(f"    ⚠ {year}년 조회 실패: {e}")
            continue
        added = collect_issues_from_soup(soup, control_no, seen, str(year))
        if added:
            issues.extend(added)
            print(f"    [{year}] {len(added)}개 발견")
            empty_years = 0
        else:
            empty_years += 1
            if empty_years >= 3 and not issues:
                break
        polite_sleep(REQUEST_DELAY)

    return issues[:depth]


# ════════════════════════════════════════
#  호수별 논문 목록 수집
# ════════════════════════════════════════

def article_links_from_soup(soup: BeautifulSoup) -> list[tuple[str, str, str]]:
    results: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for link in soup.find_all("a", href=lambda h: h and "p_mat_type=1a0202" in str(h)):
        href = link.get("href", "") or ""
        match = re.search(r"control_no=([a-f0-9]+)", href)
        title = link.get_text(strip=True)
        if not match or not title or len(title) < 2:
            continue
        aid = match.group(1)
        if aid in seen:
            continue
        seen.add(aid)
        full_url = RISS_BASE + href if href.startswith("/") else href
        results.append((aid, title, full_url))
    return results


def get_articles_by_issue(
    browser: BrowserSession,
    issue: dict,
    control_no: str,
    journal_name: str,
    existing_ids: set[str],
) -> list[dict]:
    """RISS 기본 페이지 크기로 조회하고, 신규 호수일 때만 다음 페이지까지 확인한다."""
    base_url = (
        f"{RISS_BASE}/search/detail/DetailView.do"
        f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}"
        f"&v_control_no={issue['v_control_no']}&inside_outside=1"
    )
    new_articles: list[dict] = []
    seen_page_ids: set[str] = set()

    for page in range(1, MAX_ISSUE_PAGES + 1):
        url = f"{base_url}&currentPage={page}&rowPerPage={ISSUE_PAGE_SIZE}"
        soup = browser.load_page(url, wait_sec=2.0, purpose=f"{journal_name} 논문 목록 {page}페이지")
        links = article_links_from_soup(soup)
        if not links:
            break

        page_ids = {aid for aid, _, _ in links}
        if page_ids.issubset(seen_page_ids):
            break
        seen_page_ids.update(page_ids)

        page_new = 0
        for aid, title, full_url in links:
            if aid in existing_ids or any(a["article_id"] == aid for a in new_articles):
                continue
            new_articles.append({
                "article_id": aid,
                "title_kr": title,
                "title_en": "",
                "authors": [],
                "abstract_kr": "",
                "abstract_en": "",
                "journal_name": journal_name,
                "year": issue.get("year", ""),
                "volume": issue.get("volume", ""),
                "issue": issue.get("issue", ""),
                "start_page": "",
                "end_page": "",
                "doi": "",
                "riss_url": full_url,
                "kci_url": "",
                "keywords_kr": [],
                "keywords_en": [],
                "ai_keywords": [],
                "source": "RISS",
                "metadata_status": "detail_pending",
            })
            page_new += 1

        # 첫 페이지가 전부 기존 논문이면 이미 수집된 호수로 보고 추가 호출을 피한다.
        if page == 1 and page_new == 0:
            break
        if len(links) < ISSUE_PAGE_SIZE:
            break
        polite_sleep(REQUEST_DELAY)

    return new_articles


# ════════════════════════════════════════
#  논문 상세 수집
# ════════════════════════════════════════

def detail_page_valid(soup: BeautifulSoup) -> bool:
    return soup.select_one(".thesisInfo h3.title") is not None


def fetch_detail(browser: BrowserSession, article_id: str) -> dict:
    url = (
        f"{RISS_BASE}/search/detail/DetailView.do"
        f"?p_mat_type=1a0202e37d52c72d&control_no={article_id}"
    )
    soup = browser.load_page(
        url,
        wait_sec=3.0,
        validator=detail_page_valid,
        purpose=f"논문 상세 {article_id}",
    )
    result: dict = {"metadata_status": "complete"}

    title_el = soup.select_one(".thesisInfo h3.title")
    if title_el:
        full = title_el.get_text(separator="\n", strip=True)
        parts = re.split(r"\n\s*=\s*", full)
        result["title_kr"] = parts[0].strip()
        if len(parts) > 1:
            result["title_en"] = parts[1].strip()

    for li in soup.select(".infoDetailL li"):
        label_el = li.find("span", class_="strong")
        div = li.find("div")
        if not label_el or not div:
            continue
        label = label_el.get_text(strip=True)

        if label == "저자":
            authors = []
            for a in div.find_all("a"):
                name = a.get_text(strip=True)
                affiliation = ""
                next_txt = a.next_sibling
                if next_txt and isinstance(next_txt, str):
                    affiliation = next_txt.strip().strip("()")
                if name and len(name) >= 2:
                    authors.append({"name": name, "affiliation": affiliation, "order": str(len(authors) + 1)})
            if authors:
                result["authors"] = authors

        elif label == "발행연도":
            year = div.get_text(strip=True)
            if re.match(r"^\d{4}$", year):
                result["year"] = year

        elif label == "권호사항":
            text = div.get_text(separator=" ", strip=True)
            vol_m = re.search(r"Vol\.(\d+(?:[-·~]\d+)?)", text)
            no_m = re.search(r"No\.(\d+(?:[-·~]\d+)?)", text)
            year_m = re.search(r"\[(\d{4})\]", text)
            if vol_m:
                result["volume"] = vol_m.group(1)
            if no_m:
                result["issue"] = no_m.group(1)
            if year_m and not result.get("year"):
                result["year"] = year_m.group(1)

        elif label == "수록면":
            text = div.get_text(strip=True)
            pages = re.search(r"(\d+)\s*[-~]\s*(\d+)", text)
            if pages:
                result["start_page"] = pages.group(1)
                result["end_page"] = pages.group(2)

        elif label == "주제어":
            korean, english = [], []
            for a in div.find_all("a"):
                keyword = a.get_text(strip=True)
                if not keyword or len(keyword) < 2:
                    continue
                ascii_ratio = sum(c.isascii() for c in keyword) / len(keyword)
                (english if ascii_ratio > 0.7 else korean).append(keyword)
            if korean:
                result["keywords_kr"] = korean
            if english:
                result["keywords_en"] = english

    abstract_kr = soup.select_one("#abs1.textWrap, #abs1 .textWrap")
    if abstract_kr:
        text = abstract_kr.get_text(strip=True)
        if len(text) > 20:
            result["abstract_kr"] = text

    abstract_en = soup.select_one("#abs2.textWrap, #abs2 .textWrap")
    if abstract_en:
        text = abstract_en.get_text(strip=True)
        if len(text) > 20:
            result["abstract_en"] = text

    kci_el = soup.select_one("a[href*='kci.go.kr']")
    if kci_el:
        result["kci_url"] = kci_el.get("href", "")

    return result


# ════════════════════════════════════════
#  병합·보강·저장
# ════════════════════════════════════════

def merge_and_save_journal(journal_name: str, new_articles: list[dict], existing_ids: set[str]) -> int:
    if not new_articles:
        return 0
    data = load_journal(journal_name)
    current = data.get("articles", [])
    file_ids = {article_id_of(a) for a in current}
    added = 0
    for article in new_articles:
        aid = article_id_of(article)
        article["id"] = aid
        if not aid or aid in file_ids:
            continue
        current.append(article)
        file_ids.add(aid)
        existing_ids.add(aid)
        added += 1
    if not added:
        return 0
    data.setdefault("info", {"name": journal_name})
    data["info"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
    data["articles"] = current
    save_journal(journal_name, data)
    return added


def enrich_pending_details(browser: BrowserSession, journal_name: str) -> int:
    """이전 실행에서 상세 페이지가 실패했던 신규 논문을 제한된 수만 다시 보강한다."""
    data = load_journal(journal_name)
    articles = data.get("articles", [])
    pending = [a for a in articles if a.get("metadata_status") == "detail_pending"][:MAX_PENDING_ENRICH]
    if not pending:
        return 0
    print(f"  보류 상세정보 {len(pending)}편 재시도 중...")
    updated = 0
    for index, article in enumerate(pending, start=1):
        aid = article_id_of(article)
        try:
            print(f"    [보강 {index}/{len(pending)}] {article.get('title_kr', '')[:38]} / {aid}")
            detail = fetch_detail(browser, aid)
            for key, value in detail.items():
                if value:
                    article[key] = value
            updated += 1
        except PageLoadFailure as e:
            print(f"    ⚠ 보강 실패, 다음 실행에서 재시도: {aid} / {e}")
            browser.reset("보류 상세정보 보강 실패 후 세션 정리")
        polite_sleep(REQUEST_DELAY)
    if updated:
        data.setdefault("info", {"name": journal_name})
        data["info"]["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        save_journal(journal_name, data)
    return updated


# ════════════════════════════════════════
#  메인
# ════════════════════════════════════════

def run(depth: int = 1) -> None:
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    existing_ids = load_all_existing_ids()

    print("\nChrome 초기화...")
    browser = BrowserSession()
    print("  RISS 세션 워밍업 중...")
    browser.warmup()
    print("  세션 확보 완료")

    total_new = 0
    total_enriched = 0
    failed_journals: list[str] = []

    try:
        for journal in JOURNALS:
            name = journal_key(journal)
            control_no = journal["control_no"]
            print(f"\n[{name}] 최신 {depth}호수 확인 중...")

            try:
                total_enriched += enrich_pending_details(browser, name)
                issues = get_recent_issues(browser, control_no, depth)
                if not issues:
                    print("  → 호수 확인 불가 또는 검색 결과 없음")
                    continue

                journal_new: list[dict] = []
                for issue in issues:
                    label = f"Vol.{issue['volume']} No.{issue['issue']}" if issue.get("volume") else issue.get("label", "")
                    arts = get_articles_by_issue(browser, issue, control_no, name, existing_ids)
                    if not arts:
                        print(f"  [{label}] 새 논문 없음")
                        continue

                    print(f"  [{label}] 새 논문 {len(arts)}편 발견 → 상세 수집 중...")
                    for idx, article in enumerate(arts, start=1):
                        aid = article["article_id"]
                        print(f"    [{idx}/{len(arts)}] {article['title_kr'][:38]} / {aid}")
                        try:
                            detail = fetch_detail(browser, aid)
                            for key, value in detail.items():
                                if value:
                                    article[key] = value
                            print("      → 상세정보 완료")
                        except PageLoadFailure as e:
                            # 목록에서 확인된 신규 논문은 누락시키지 않고, 다음 실행에서 보강한다.
                            article["metadata_status"] = "detail_pending"
                            print(f"      ⚠ 상세정보 보류 저장: {e}")
                            browser.reset("논문 상세 수집 실패 후 세션 정리")
                        journal_new.append(article)
                        polite_sleep(REQUEST_DELAY)

                if journal_new:
                    added = merge_and_save_journal(name, journal_new, existing_ids)
                    total_new += added
                    print(f"  → {name}: {added}편 추가")
                else:
                    print("  → 새 논문 없음")

            except PageLoadFailure as e:
                failed_journals.append(name)
                print(f"  ⚠ {name} 수집 보류: {e}")
                browser.reset(f"{name} 수집 실패 후 다음 학술지 진행")
            except Exception as e:
                failed_journals.append(name)
                print(f"  ⚠ {name} 예외 발생, 다음 학술지 진행: {type(e).__name__}: {e}")
                browser.reset(f"{name} 예외 후 다음 학술지 진행")

            polite_sleep(REQUEST_DELAY)

    finally:
        browser.close()

    print(f"\n{'=' * 50}")
    print("✅ 증분 업데이트 실행 완료")
    print(f"   새로 추가된 논문: {total_new}편")
    print(f"   상세정보 보강 완료: {total_enriched}편")
    if failed_journals:
        print(f"   이번 실행에서 보류된 학술지: {', '.join(failed_journals)}")
        print("   보류 항목은 다음 자동 실행에서 다시 시도됩니다.")
    print(f"   업데이트 시각: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RISS 증분 업데이트 안정화 버전")
    parser.add_argument("--depth", type=int, default=1, help="학술지당 확인할 최신 호수 개수 (기본: 1)")
    args = parser.parse_args()
    run(depth=args.depth)
