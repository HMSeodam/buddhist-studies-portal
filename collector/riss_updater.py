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
]


# ════════════════════════════════════════
#  드라이버
# ════════════════════════════════════════

def init_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
    driver.implicitly_wait(5)
    return driver


def load_page(driver, url, wait_sec=4):
    driver.get(url)
    time.sleep(wait_sec)
    return BeautifulSoup(driver.page_source, "html.parser")


# ════════════════════════════════════════
#  개별 학술지 JSON 로드 / 저장
# ════════════════════════════════════════

def journal_path(journal_name: str) -> Path:
    return Path(OUTPUT_DIR) / f"riss_{journal_name}.json"

def load_journal(journal_name: str) -> dict:
    """개별 학술지 JSON 파일 로드. 없으면 빈 구조 반환."""
    path = journal_path(journal_name)
    if not path.exists():
        print(f"  ⚠ {path.name} 없음 → 빈 데이터로 시작")
        return {"info": {"name": journal_name}, "articles": []}
    with open(path, encoding="utf-8") as f:
        return json.load(f)

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

def get_recent_issues(driver, control_no: str, depth: int) -> list[dict]:
    """학술지 페이지에서 최신 호수 N개만 수집"""
    url  = (f"{RISS_BASE}/search/detail/DetailView.do"
            f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}")
    soup = load_page(driver, url, wait_sec=5)

    issues = []
    seen   = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "v_control_no" not in href:
            continue
        m = re.search(r"v_control_no=([a-f0-9]+)", href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))

        txt   = a.get_text(strip=True)
        no_m  = re.search(r"No\.(\d+)", txt)
        vol_m = re.search(r"Vol\.(\d+)", txt)
        issues.append({
            "v_control_no": m.group(1),
            "issue":        no_m.group(1)  if no_m  else "",
            "volume":       vol_m.group(1) if vol_m else "",
            "label":        txt,
        })

        if len(issues) >= depth:
            break  # 최신 N개만

    return issues


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
    soup = load_page(driver, f"{base_url}&currentPage=1&rowPerPage=100", wait_sec=3)

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

def fetch_detail(driver, article_id: str) -> dict:
    url  = (f"{RISS_BASE}/search/detail/DetailView.do"
            f"?p_mat_type=1a0202e37d52c72d&control_no={article_id}")
    soup = load_page(driver, url, wait_sec=5)
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
                    affil = re.sub(r"[()]", "", next_txt).strip()
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

def run(depth=1):
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # 모든 학술지 파일에서 기존 ID 수집
    existing_ids = load_all_existing_ids()

    print(f"\nChrome 초기화...")
    driver = init_driver()

    total_new = 0

    try:
        for journal in JOURNALS:
            name       = journal["name"]
            control_no = journal["control_no"]

            print(f"\n[{name}] 최신 {depth}호수 확인 중...")

            # 최신 호수 목록
            issues = get_recent_issues(driver, control_no, depth)
            if not issues:
                print(f"  호수 없음")
                continue

            journal_new = []
            for iss in issues:
                label = f"Vol.{iss['volume']} No.{iss['issue']}" if iss['volume'] else iss['label']
                arts  = get_articles_by_issue(driver, iss, control_no, name, existing_ids)

                if not arts:
                    print(f"  [{label}] 새 논문 없음")
                    continue

                print(f"  [{label}] 새 논문 {len(arts)}편 발견 → 상세 수집 중...")

                # 상세 수집
                for i, art in enumerate(arts):
                    d = fetch_detail(driver, art["article_id"])
                    for k, v in d.items():
                        if v: art[k] = v
                    existing_ids.add(art["article_id"])  # 중복 방지
                    time.sleep(REQUEST_DELAY)

                journal_new.extend(arts)
                time.sleep(REQUEST_DELAY)

            if journal_new:
                added = merge_and_save_journal(name, journal_new, existing_ids)
                total_new += added
                print(f"  → {name}: {added}편 추가")
            else:
                print(f"  → 새 논문 없음")

            time.sleep(REQUEST_DELAY)

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
    args = parser.parse_args()

    run(depth=args.depth)
