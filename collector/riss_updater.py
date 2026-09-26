#!/usr/bin/env python3
# riss_updater.py — 요일별 분산 업데이트 (차단·지연·허위응답 방어판)
#
# 동작:
#   월~토: 지정된 5개 학술지, 최신 2호수 확인
#   일:    전체 29개 학술지, 최신 1호수 확인 (전체 점검)
#   + 지난 실행에서 끝내지 못한 학술지(이월 목록)를 먼저 처리
#
# 2026-09 개정 — RISS 봇 차단 대응
#   1) 차단 감지   : 차단/점검 문구, 경고창(alert), 새 창(popup), 비정상적으로 짧은 응답,
#                    페이지 로드 시간 초과를 '차단 의심'으로 판정
#   2) 서킷브레이커 : 차단이 연속되면 점점 길게 쉬고(3→7→15→25분), 브라우저 세션을 새로 연다.
#                    그래도 계속되면 이번 실행을 멈추고 남은 학술지를 이월(pending) 목록에 저장
#   3) 느린 서버   : 로드가 느리면 모든 대기시간에 배수(최대 4배)를 걸어 속도를 스스로 낮춤
#   4) 허위 정보   : 상세 페이지의 제목·학술지명·연도·호수·URL 을 목록 정보와 대조 → 어긋나면 저장하지 않음
#                    (저장하지 않은 논문은 다음 실행에서 자동으로 다시 수집됨)
#   5) 시간 예산   : --deadline-min 안에서만 작업하고, 넘기 전에 스스로 멈춰 지금까지 수집분을 안전하게 저장
#   6) 원자적 저장 : 강제 종료되어도 JSON 이 깨지지 않게 임시파일→교체 방식으로 저장
#   7) 공지 기록   : 새 논문이 추가되면 output/updates.json 에 "날짜·학술지·호수·편수" 기록
#
# 종료 코드:
#   0  = 정상 완료
#   75 = 미완료(차단 또는 시간 부족으로 이월 목록이 남음) → 워크플로가 쉬었다가 재시도
#
# 사용법:
#   python riss_updater.py                       # 오늘 요일 자동 판별
#   python riss_updater.py --day mon             # 요일 직접 지정
#   python riss_updater.py --day all --depth 1   # 전체 강제 실행
#   python riss_updater.py --deadline-min 120    # 120분 안에 끝내기
#   python riss_updater.py --only 선학,불교학보   # 특정 학술지만

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, NoAlertPresentException,
)
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
import json, time, re, hashlib, os, sys, random, argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_log import record_update, retract_update, atomic_write_json, today_kst   # noqa: E402
from match_util import find_same   # noqa: E402

OUTPUT_DIR = "../output"
STATE_FILE = "state/riss_pending.json"     # collector/state/ — 이월 목록 (워크플로가 함께 커밋)
RISS_BASE  = "https://www.riss.kr"

# ── 딜레이 설정 (초) ──────────────────────────────────────────
WARMUP_DELAY  = 8      # 세션 워밍업 대기
PAGE_LOAD     = 6      # 페이지 로드 후 기본 대기
CLICK_WAIT    = 6      # 연도 탭 클릭 후 대기 (조회중... 로딩 대기)
CLICK_RETRY   = 6      # 클릭 후 0개일 때 재대기
ISSUE_DELAY   = 7      # 호수 페이지 로드 대기
DETAIL_LOAD   = 8      # 논문 상세 페이지 로드 대기
DETAIL_DELAY  = 10     # 논문 상세 수집 후 대기
JOURNAL_DELAY = 45     # 학술지 간 대기 (누적 차단 방지 핵심)

# ── 방어 설정 ────────────────────────────────────────────────
PAGE_LOAD_TIMEOUT = 120                  # driver.get 한 번의 최대 시간 (Selenium 기본 300초 → 명시)
SLOW_LOAD_SEC     = 25                   # 이보다 오래 걸리면 '서버 지연'으로 보고 속도를 낮춤
MAX_SLOW_FACTOR   = 4.0                  # 대기시간 최대 배수
LOAD_RETRIES      = 4                    # 한 페이지당 재시도 횟수 (기존 3 → 4)
RETRY_WAIT        = [20, 60, 120, 240]   # 일반 재시도 대기 (기존 15/30/60 → 넉넉하게)
BLOCK_COOLDOWN    = [180, 420, 900, 1500]  # 차단 의심 시 쉬는 시간: 3 → 7 → 15 → 25분
MAX_BLOCK_STREAK  = len(BLOCK_COOLDOWN)  # 연속 차단이 이 횟수를 넘으면 이번 실행 중단
MAX_DETAIL_FAILS  = 3                    # 상세 페이지 연속 실패가 이만큼이면 차단으로 간주
MIN_JOURNAL_MIN   = 12                   # 남은 시간이 이보다 적으면 새 학술지를 시작하지 않음

# 차단·점검 안내 문구 (페이지 본문에서 검사)
BLOCK_PATTERNS = [
    "비정상적인 접근", "비정상적인 요청", "비정상 접근", "비정상적인 트래픽",
    "과도한 접근", "과도한 요청", "과도한 트래픽", "접근이 차단", "접근이 제한",
    "접속이 차단", "접속이 제한", "일시적으로 차단", "일시적으로 제한", "이용이 제한",
    "자동화된 요청", "자동화 프로그램", "자동 수집", "매크로", "보안문자", "자동입력 방지",
    "captcha", "access denied", "too many requests", "request rejected",
    "the requested url was rejected", "service unavailable", "bad gateway",
    "gateway time-out", "gateway timeout",
]
# 짧은 페이지에서만 차단으로 보는 문구 (정상 페이지에도 나올 수 있어서)
SHORT_PAGE_PATTERNS = ["잠시 후 다시", "서비스 점검", "시스템 점검", "점검 중", "오류가 발생"]
SHORT_PAGE_LEN = 15000

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

# name → journal 빠른 조회용
NAME_TO_JOURNAL = {
    (j.get("display_name") or j["name"]): j for j in JOURNALS
}


# ════════════════════════════════════════
#  예외 / 유틸리티
# ════════════════════════════════════════

class RissBlocked(Exception):
    """연속 차단으로 이번 실행을 중단해야 함."""

class OutOfTime(Exception):
    """시간 예산 소진."""


def _gid(art: dict) -> str:
    s = f"{art.get('title_kr','')}{art.get('journal_name','')}{art.get('year','')}"
    return hashlib.md5(s.encode()).hexdigest()[:16]

def journal_key(j: dict) -> str:
    return j.get("display_name") or j["name"]

def norm_title(t: str) -> str:
    """제목 비교용 정규화: 공백·문장부호 제거, 소문자."""
    t = (t or "").lower()
    return re.sub(r"[\s\-‐‑–—_:;,.·ㆍ\"'“”‘’`「」『』《》〈〉()\[\]{}!?/\\]+", "", t)

def norm_journal(t: str) -> str:
    return re.sub(r"[\s\-_()（）\[\]·ㆍ]", "", (t or "")).lower()

def norm_num(x) -> str:
    m = re.search(r"\d+", str(x or ""))
    return str(int(m.group())) if m else ""

def clean_affil(s: str) -> str:
    """'동국대학교 불교학술원)\\n     ;' 같은 잔여 문자를 정리."""
    s = re.sub(r"\s+", " ", s or "").strip()
    s = s.strip(" ;,()")
    return "" if s in (";", ",") else s

def title_similar(a: str, b: str) -> float:
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return 1.0          # 비교할 수 없으면 통과
    if na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ════════════════════════════════════════
#  시간 예산 · 속도 조절 · 차단 카운터
# ════════════════════════════════════════

class Guard:
    def __init__(self, deadline_min: float):
        self.t0 = time.time()
        self.deadline = self.t0 + deadline_min * 60
        self.slow_factor = 1.0
        self.block_streak = 0
        self.detail_fail_streak = 0
        self.stats = {"blocks": 0, "slow": 0, "alerts": 0, "popups": 0,
                      "invalid_detail": 0, "skipped_articles": 0, "cooldown_sec": 0}

    def minutes_left(self) -> float:
        return (self.deadline - time.time()) / 60

    def check_time(self, need_sec: float = 0):
        if time.time() + need_sec > self.deadline:
            raise OutOfTime()

    def sleep(self, base: float):
        """속도 배수 + 무작위 흔들림(사람처럼 불규칙하게)."""
        sec = base * self.slow_factor * random.uniform(0.85, 1.35)
        self.check_time(sec)
        time.sleep(sec)

    def on_ok(self, load_sec: float):
        self.block_streak = 0
        if load_sec > SLOW_LOAD_SEC:
            self.stats["slow"] += 1
            self.slow_factor = min(MAX_SLOW_FACTOR, self.slow_factor * 1.5)
            print(f"    🐢 서버 응답 느림({load_sec:.0f}초) → 대기 배수 ×{self.slow_factor:.1f}")
        elif self.slow_factor > 1.0:
            self.slow_factor = max(1.0, self.slow_factor * 0.9)

    def cooldown(self, reason: str):
        """차단 의심 → 점점 길게 쉼. 한도를 넘으면 RissBlocked."""
        self.stats["blocks"] += 1
        self.block_streak += 1
        if self.block_streak > MAX_BLOCK_STREAK:
            raise RissBlocked(reason)
        wait = BLOCK_COOLDOWN[self.block_streak - 1]
        self.slow_factor = min(MAX_SLOW_FACTOR, max(self.slow_factor, 1.0 + 0.5 * self.block_streak))
        if time.time() + wait > self.deadline - 60:
            raise OutOfTime()
        print(f"  ⛔ [{ts()}] 차단 의심({reason}) → {wait//60}분 휴식 "
              f"(연속 {self.block_streak}/{MAX_BLOCK_STREAK}, 대기 배수 ×{self.slow_factor:.1f})")
        self.stats["cooldown_sec"] += wait
        time.sleep(wait)


# ════════════════════════════════════════
#  드라이버 초기화
# ════════════════════════════════════════

try:
    import undetected_chromedriver as uc
    _UC_AVAILABLE = True
except ImportError:
    _UC_AVAILABLE = False

def init_driver():
    # GitHub Actions 환경 감지 (CI=true 환경변수)
    is_ci = os.environ.get("CI", "false").lower() == "true"

    if _UC_AVAILABLE and not is_ci:
        # 로컬 환경: UC 사용 (봇 감지 우회)
        opts = uc.ChromeOptions()
        opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,900")
        opts.set_capability("unhandledPromptBehavior", "ignore")
        try:
            import subprocess as _sp
            _major = None
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

    # GitHub Actions 또는 UC 미설치: 표준 Selenium 사용
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # 경고창(alert)이 떠도 드라이버가 예외로 멈추지 않게 → 우리가 직접 읽고 닫는다
    opts.set_capability("unhandledPromptBehavior", "ignore")
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
#  RISS 세션 (차단 감지 + 재시도 + 세션 재시작)
# ════════════════════════════════════════

def detect_block_text(html: str, soup: BeautifulSoup) -> str:
    if not html or len(html) < 500:
        return "빈 응답"
    body = soup.body or soup
    for t in body(["script", "style", "noscript"]):
        t.decompose()
    text = body.get_text(" ", strip=True)
    title = (soup.title.get_text(strip=True) if soup.title else "")
    hay = (title + " " + text[:6000]).lower()
    for p in BLOCK_PATTERNS:
        if p.lower() in hay:
            return f"문구 '{p}'"
    if len(html) < SHORT_PAGE_LEN:
        for p in SHORT_PAGE_PATTERNS:
            if p in hay:
                return f"짧은 페이지+문구 '{p}'"
    return ""


class RissSession:
    def __init__(self, guard: Guard):
        self.guard = guard
        self.driver = None
        self.main_handle = None
        self.start()

    def start(self):
        self.driver = init_driver()
        self.driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
        self.main_handle = self.driver.current_window_handle
        print("  RISS 세션 워밍업 중...")
        try:
            self.driver.get(RISS_BASE)
        except TimeoutException:
            print("  ⚠ 워밍업 페이지 로드 시간 초과 (계속 진행)")
        except WebDriverException as e:
            print(f"  ⚠ 워밍업 실패: {str(e)[:80]}")
        self.guard.sleep(WARMUP_DELAY)
        self.clean_popups()
        print("  세션 확보 완료")

    def restart(self):
        print("  ↻ 브라우저 세션 재시작 (쿠키·세션 초기화)")
        self.quit()
        time.sleep(5)
        self.start()

    def quit(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None

    def clean_popups(self) -> str:
        """경고창(alert)·새 창(popup)을 닫고, 경고창 문구를 돌려준다."""
        note = ""
        d = self.driver
        try:
            al = d.switch_to.alert
            note = (al.text or "").strip()
            al.accept()
            self.guard.stats["alerts"] += 1
            print(f"    ⚠ 경고창 닫음: {note[:60]}")
        except NoAlertPresentException:
            pass
        except Exception:
            pass
        try:
            handles = d.window_handles
            if len(handles) > 1:
                for h in handles:
                    if h != self.main_handle:
                        d.switch_to.window(h)
                        d.close()
                d.switch_to.window(self.main_handle)
                self.guard.stats["popups"] += 1
                print(f"    ⚠ 팝업 창 {len(handles)-1}개 닫음")
        except Exception:
            try:
                self.main_handle = d.window_handles[0]
                d.switch_to.window(self.main_handle)
            except Exception:
                pass
        return note

    def check_current(self, validator=None, what="") -> tuple:
        """현재 화면 검사 → (soup, 문제사유, 구조문제여부)."""
        alert_note = self.clean_popups()
        html = self.driver.page_source or ""
        soup = BeautifulSoup(html, "html.parser")
        reason = ""
        if alert_note:
            low = alert_note.lower()
            if any(p.lower() in low for p in BLOCK_PATTERNS + SHORT_PAGE_PATTERNS):
                reason = f"경고창 '{alert_note[:30]}'"
        if not reason:
            reason = detect_block_text(html, BeautifulSoup(html, "html.parser"))
        structural = False
        if not reason and validator is not None and not validator(soup):
            reason, structural = f"예상 구조 없음({what})", True
        return soup, reason, structural

    def get(self, url: str, wait: float, validator=None, what: str = "",
            retries: int = LOAD_RETRIES):
        """
        페이지를 열고 검사한다.
          - 차단 문구/경고창/시간초과 → 서킷브레이커 휴식 + 세션 재시작 후 재시도
          - 구조만 이상(내용이 비어 보임) → 짧게 쉬고 재시도, 끝내 이상하면 None
        성공 시 BeautifulSoup, 실패 시 None.
        """
        g = self.guard
        for attempt in range(retries):
            g.check_time(PAGE_LOAD_TIMEOUT)
            t0 = time.time()
            reason, structural = "", False
            soup = None
            try:
                self.driver.get(url)
                g.sleep(wait)
                soup, reason, structural = self.check_current(validator, what)
            except TimeoutException:
                reason = f"로드 시간 초과({PAGE_LOAD_TIMEOUT}초)"
                g.slow_factor = min(MAX_SLOW_FACTOR, g.slow_factor * 1.5)
                try:
                    self.driver.execute_script("window.stop();")
                except Exception:
                    pass
            except (OutOfTime, RissBlocked):
                raise
            except WebDriverException as e:
                reason = f"브라우저 오류: {str(e).splitlines()[0][:60]}"

            elapsed = time.time() - t0
            if not reason:
                g.on_ok(elapsed)
                if "DetailView" in url and "DetailView" not in (self.driver.current_url or ""):
                    print(f"  ⚠ 리다이렉트 감지: {url[:60]}")
                return soup

            if structural:
                w = RETRY_WAIT[min(attempt, len(RETRY_WAIT) - 1)]
                print(f"  ⚠ [{ts()}] {reason} (시도 {attempt+1}/{retries}) → {w}초 후 재시도")
                if attempt == retries - 1:
                    return None
                g.sleep(w)
                continue

            # 차단 의심 → 길게 쉬고, 두 번째부터는 세션도 새로
            g.cooldown(reason)
            if g.block_streak >= 2:
                self.restart()
        print(f"  ✗ 로드 포기: {url[:70]}")
        return None


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
    atomic_write_json(path, data)
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
                for k in ("article_id", "id", "riss_id"):
                    if a.get(k):
                        ids.add(a[k])
        except Exception as e:
            print(f"  ⚠ {path.name} 로드 실패: {e}")
    print(f"기존 전체 데이터: {len(ids)}개 ID")
    return ids


# ── 이월(pending) 상태 ──────────────────────────────────────
def load_pending() -> list:
    p = Path(STATE_FILE)
    if not p.exists():
        return []
    try:
        d = json.load(open(p, encoding="utf-8"))
        return [n for n in d.get("journals", []) if n in NAME_TO_JOURNAL]
    except Exception:
        return []

def save_pending(names: list, note: str = ""):
    p = Path(STATE_FILE)
    names = list(dict.fromkeys(names))
    if not names:
        if p.exists():
            p.unlink()
        return
    atomic_write_json(p, {"journals": names, "updated": today_kst(), "note": note})


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
        yr_m  = re.search(r"((?:19|20)\d{2})", txt)
        found.append({
            "v_control_no": v_id,
            "issue":  no_m.group(1)  if no_m  else "",
            "volume": vol_m.group(1) if vol_m else "",
            "year":   yr_m.group(1)  if yr_m  else "",
            "label":  txt,
        })
    return found


def _journal_page_ok(soup) -> bool:
    html = str(soup)
    return ("v_control_no" in html) or bool(re.search(r">\s*(19|20)\d{2}\s*<", html))


def get_recent_issues(sess: RissSession, control_no: str, depth: int) -> list:
    url = (f"{RISS_BASE}/search/detail/DetailView.do"
           f"?p_mat_type=3a11008f85f7c51d&control_no={control_no}")
    soup = sess.get(url, PAGE_LOAD, validator=_journal_page_ok, what="학술지 호수 목록")
    driver = sess.driver

    issues = []
    seen   = set()

    # ── 1차: 기본 HTML에서 바로 수집 ──────────────────────────
    if soup is not None:
        issues += _collect_issues_from_page(driver, control_no, seen)
        if issues:
            return issues[:depth]

    # ── 2차: 연도 탭 클릭 ─────────────────────────────────────
    print("  연도 클릭 방식으로 호수 탐색 중...")
    try:
        year_els = [
            el for el in driver.find_elements(By.TAG_NAME, "a")
            if re.match(r"^(19|20)\d{2}", (el.text or "").strip())
        ]
    except WebDriverException:
        year_els = []
    for el in year_els:
        if len(issues) >= depth:
            break
        try:
            year_txt = el.text.strip()
            print(f"    [{year_txt}] 클릭...", end=" ", flush=True)
            driver.execute_script("arguments[0].click();", el)
            sess.guard.sleep(CLICK_WAIT)   # 조회중... 로딩 대기
            _, reason, _ = sess.check_current()
            if reason:
                print(f"차단 의심: {reason}")
                sess.guard.cooldown(reason)
                break
            before  = len(issues)
            issues += _collect_issues_from_page(driver, control_no, seen)
            if len(issues) - before == 0:
                sess.guard.sleep(CLICK_RETRY)
                issues += _collect_issues_from_page(driver, control_no, seen)
            print(f"{len(issues) - before}개 발견")
        except (OutOfTime, RissBlocked):
            raise
        except Exception as e:
            print(f"클릭 실패: {str(e)[:60]}")
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
        if sess.get(year_url, PAGE_LOAD, retries=2) is None:
            continue
        before  = len(issues)
        issues += _collect_issues_from_page(sess.driver, control_no, seen)
        added   = len(issues) - before
        if added:
            print(f"  {year}년: {added}개 발견")
            consecutive_empty = 0
        else:
            consecutive_empty += 1
            if consecutive_empty >= 3:
                break

    if not issues:
        print(f"  ⚠ 호수 없음 (URL: {(sess.driver.current_url or '')[:80]})")

    return issues[:depth]


# ════════════════════════════════════════
#  호수별 논문 목록 수집
# ════════════════════════════════════════

def _issue_page_ok(soup) -> bool:
    return bool(soup.find("a", href=lambda h: h and "p_mat_type=1a0202" in str(h)))


def get_articles_by_issue(sess: RissSession, issue: dict, control_no: str,
                          journal_name: str, existing_ids: set, title_index: dict):
    """
    반환: (새 논문 목록, 제목으로 기존 레코드에 RISS 링크만 붙인 건수, 목록이 비어 의심스러운지)
    title_index: {정규화 제목: 기존 레코드} — KCI 등 다른 경로로 이미 들어온 논문은
                 상세 페이지를 다시 열지 않고 RISS 링크만 붙인다 (RISS 요청 수 절감).
    """
    base_url = (f"{RISS_BASE}/search/detail/DetailView.do"
                f"?p_mat_type=3a11008f85f7c51d"
                f"&control_no={control_no}"
                f"&v_control_no={issue['v_control_no']}"
                f"&inside_outside=1&currentPage=1&rowPerPage=100")
    soup = sess.get(base_url, ISSUE_DELAY, validator=_issue_page_ok,
                    what="호수 논문 목록", retries=3)
    if soup is None:
        return [], 0, True

    new_arts, linked = [], 0
    for lk in soup.find_all("a", href=lambda h: h and "p_mat_type=1a0202" in str(h)):
        href   = lk.get("href", "")
        # RISS 가 목록 옆에 띄우는 '함께 본 논문' 추천(recommender/click.do)은 다른 학술지 논문 → 제외
        if "recommender" in href or "click.do" in href or "DetailView" not in href:
            continue
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
        # 같은 제목의 기존 레코드(KCI 수집분 등)가 있으면 링크만 연결
        ex = title_index.get(norm_title(title))
        if ex is None or ex.get("riss_url"):
            # 제목 표기가 조금 다른 KCI 수집분(번역 제목 병기·각주 표시 등)도 같은 논문으로 인식
            pool = [a for a in title_index.values() if not a.get("riss_url")]
            ex = find_same({"title_kr": title, "year": issue.get("year", ""),
                            "volume": issue.get("volume", ""), "issue": issue.get("issue", "")}, pool)
        if ex is not None and not ex.get("riss_url"):
            ex["riss_url"] = full_url
            ex["riss_id"] = art_id
            existing_ids.add(art_id)
            linked += 1
            continue
        new_arts.append({
            "article_id":  art_id,
            "title_kr":    title,
            "title_en":    "",
            "authors":     [],
            "abstract_kr": "",
            "abstract_en": "",
            "journal_name": journal_name,
            "year":        issue.get("year", ""),
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
    return new_arts, linked, False


# ════════════════════════════════════════
#  논문 상세 수집 + 검증
# ════════════════════════════════════════

def parse_detail(soup) -> dict:
    r = {}
    ti = soup.select_one(".thesisInfo h3.title")
    if ti:
        full  = ti.get_text(separator="\n", strip=True)
        parts = re.split(r"\n\s*=\s*", full)
        r["title_kr"] = parts[0].strip()
        if len(parts) > 1:
            r["title_en"] = parts[1].strip()

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
                    affil = clean_affil(nxt)
                if nm and len(nm) >= 2:
                    authors.append({
                        "name": nm, "affiliation": affil,
                        "order": str(len(authors)+1)
                    })
            if authors:
                r["authors"] = authors

        elif label in ("학술지명", "간행물명", "수록잡지명"):
            r["_journal_seen"] = div.get_text(" ", strip=True)

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

    kci_el = soup.select_one("a[href*='kci.go.kr']")
    if kci_el:
        r["kci_url"] = kci_el.get("href", "")
    return r


def validate_detail(d: dict, art: dict, expected_journal: str, current_url: str) -> str:
    """상세 정보가 요청한 논문과 맞는지 확인. 문제가 있으면 사유 문자열, 없으면 ''."""
    if art["article_id"] not in (current_url or ""):
        return "다른 논문 페이지로 이동됨"
    if not d.get("title_kr"):
        return "제목 없음"
    sim = title_similar(d["title_kr"], art.get("title_kr", ""))
    if sim < 0.45:
        return f"목록 제목과 불일치(유사도 {sim:.2f})"
    js = d.get("_journal_seen")
    if js:
        a, b = norm_journal(js), norm_journal(expected_journal)
        alt = norm_journal(NAME_TO_JOURNAL.get(expected_journal, {}).get("name", ""))
        if b and not (b in a or a in b or (alt and (alt in a or a in alt))):
            return f"학술지명 불일치('{js[:20]}')"
    y = d.get("year") or art.get("year")
    if y:
        try:
            yi = int(y)
            if not (1945 <= yi <= datetime.now().year + 1):
                return f"연도 이상({y})"
        except ValueError:
            return f"연도 형식 이상({y})"
    for k in ("issue", "volume"):
        a, b = norm_num(d.get(k)), norm_num(art.get(k))
        if a and b and a != b:
            return f"{'호' if k=='issue' else '권'} 불일치({b}→{a})"
    return ""


def fetch_detail(sess: RissSession, art: dict, expected_journal: str):
    """검증을 통과한 상세 정보 dict, 실패하면 None (→ 이번엔 저장하지 않고 다음 실행에서 재수집)."""
    g = sess.guard
    url = (f"{RISS_BASE}/search/detail/DetailView.do"
           f"?p_mat_type=1a0202e37d52c72d&control_no={art['article_id']}")
    last_reason = ""
    for attempt in range(3):
        soup = sess.get(url, DETAIL_LOAD,
                        validator=lambda s: s.select_one(".thesisInfo") is not None,
                        what="논문 상세", retries=3)
        if soup is None:
            last_reason = "상세 페이지 비어있음"
        else:
            d = parse_detail(soup)
            last_reason = validate_detail(d, art, expected_journal, sess.driver.current_url)
            if not last_reason:
                g.detail_fail_streak = 0
                d.pop("_journal_seen", None)
                return d
            g.stats["invalid_detail"] += 1
            print(f"  ⚠ 상세 검증 실패: {last_reason} (시도 {attempt+1}/3)")
        # 연속 실패가 쌓이면 '허위 응답/차단'으로 보고 길게 쉼
        g.detail_fail_streak += 1
        if g.detail_fail_streak >= MAX_DETAIL_FAILS:
            g.cooldown(f"상세 연속 실패 {g.detail_fail_streak}회: {last_reason}")
            sess.restart()
            g.detail_fail_streak = 0
        else:
            g.sleep(RETRY_WAIT[min(attempt, len(RETRY_WAIT) - 1)])
    print(f"  ✗ 저장 보류(다음 실행에서 재시도): {art.get('title_kr','')[:40]} — {last_reason}")
    g.stats["skipped_articles"] += 1
    return None


# ════════════════════════════════════════
#  병합 저장
# ════════════════════════════════════════

def merge_and_save_journal(name: str, data: dict, new_articles: list,
                           existing_ids: set, linked: int) -> list:
    """data(이미 로드된 학술지 JSON)에 새 논문 병합 후 저장. 실제로 추가된 논문 목록 반환."""
    cur  = data.get("articles", [])
    file_ids = {a.get("article_id") or a.get("id", "") for a in cur}
    added = []
    for art in new_articles:
        aid = art.get("article_id") or _gid(art)
        art["id"] = aid
        if aid in file_ids:
            continue
        cur.append(art)
        file_ids.add(aid)
        existing_ids.add(aid)
        added.append(art)
    if not added and not linked:
        return []
    if "info" not in data:
        data["info"] = {"name": name}
    data["info"]["last_updated"] = today_kst()
    data["articles"] = cur
    save_journal(name, data)
    return added


def log_added(name: str, added: list):
    """추가된 논문을 호수별로 묶어 공지(updates.json)에 기록."""
    groups = {}
    for a in added:
        k = (a.get("year", ""), a.get("volume", ""), a.get("issue", ""))
        groups[k] = groups.get(k, 0) + 1
    for (y, v, i), n in groups.items():
        record_update(OUTPUT_DIR, name, n, volume=v, issue=i, year=y, source="RISS")


# ════════════════════════════════════════
#  메인
# ════════════════════════════════════════

def get_today_journals(day_key: str):
    """요일에 해당하는 학술지 목록과 depth 반환."""
    if day_key == "sun":
        return JOURNALS, 1
    if day_key == "all":
        return JOURNALS, 2
    names = SCHEDULE.get(day_key, [])
    target = [NAME_TO_JOURNAL[n] for n in names if n in NAME_TO_JOURNAL]
    return target, 2


def process_journal(sess, journal, depth, existing_ids) -> tuple:
    """한 학술지 처리. 반환: (추가 편수, 완료 여부)"""
    g = sess.guard
    name       = journal_key(journal)
    control_no = journal["control_no"]
    print(f"\n[{name}] 최신 {depth}호수 확인 중... (남은 시간 {g.minutes_left():.0f}분)")

    data = load_journal(name)
    title_index = {norm_title(a.get("title_kr", "")): a
                   for a in data.get("articles", []) if a.get("title_kr")}

    issues = get_recent_issues(sess, control_no, depth)
    if not issues:
        print("  → 호수를 찾지 못함 (차단 의심 → 이월)")
        return 0, False

    journal_new, linked_total, complete = [], 0, True
    added = []
    try:
        for iss in issues:
            label = (f"Vol.{iss['volume']} No.{iss['issue']}"
                     if iss.get("volume") else iss.get("label", "?"))
            arts, linked, suspicious = get_articles_by_issue(
                sess, iss, control_no, name, existing_ids, title_index)
            linked_total += linked
            if suspicious:
                print(f"  [{label}] ⚠ 논문 목록을 읽지 못함 (차단 의심 → 이월)")
                complete = False
                continue
            if linked:
                print(f"  [{label}] 기존 레코드에 RISS 링크 {linked}건 연결")
            if not arts:
                print(f"  [{label}] 새 논문 없음")
                continue

            print(f"  [{label}] 새 논문 {len(arts)}편 발견 → 상세 수집 중...")
            for n, art in enumerate(arts, 1):
                d = fetch_detail(sess, art, name)
                if d is None:
                    complete = False          # 일부 보류 → 다음 실행에서 이어받기
                else:
                    for k, v in d.items():
                        if v:
                            art[k] = v
                    journal_new.append(art)
                    existing_ids.add(art["article_id"])
                    print(f"    ({n}/{len(arts)}) ✓ {art.get('title_kr','')[:36]}")
                g.sleep(DETAIL_DELAY)
            g.sleep(DETAIL_DELAY)
    finally:
        # 시간 초과·차단으로 중단되더라도 지금까지 검증된 논문은 저장
        added = merge_and_save_journal(name, data, journal_new, existing_ids, linked_total)
        if added:
            log_added(name, added)
            print(f"  → {name}: {len(added)}편 추가")
        elif not linked_total:
            print("  → 새 논문 없음")
    return len(added), complete


def purge_recommended() -> int:
    """
    예전 수집기가 '함께 본 논문' 추천 링크까지 긁어 와 다른 학술지 논문이 섞여 들어간 것을 정리.
    (riss_url 이 recommender/click.do 인 레코드) 관련 공지 편수도 되돌린다.
    """
    removed = 0
    for path in sorted(Path(OUTPUT_DIR).glob("riss_*.json")):
        try:
            data = json.load(open(path, encoding="utf-8"))
        except Exception:
            continue
        arts = data.get("articles", data) if isinstance(data, dict) else data
        bad = [a for a in arts if "/recommender/" in (a.get("riss_url") or "") and a.get("source", "RISS") == "RISS"]
        if not bad:
            continue
        keep = [a for a in arts if a not in bad]
        if isinstance(data, dict):
            data["articles"] = keep
        else:
            data = keep
        atomic_write_json(path, data)
        name = path.stem[5:]
        for a in bad:
            retract_update(OUTPUT_DIR, name, 1, volume=a.get("volume", ""), issue=a.get("issue", ""), source="RISS")
        print(f"  🧹 [{name}] 다른 학술지 논문(추천 링크) {len(bad)}편 삭제: " + " / ".join(a.get("title_kr", "")[:20] for a in bad[:3]))
        removed += len(bad)
    return removed


def run(day_key: str, depth_override: int = None, deadline_min: float = 300,
        only: list = None, pending_only: bool = False) -> int:
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    purged = purge_recommended()
    existing_ids = load_all_existing_ids()
    guard = Guard(deadline_min)

    today_journals, depth = get_today_journals(day_key)
    if depth_override is not None:
        depth = depth_override
        print(f"  depth override: {depth}")

    # 이월된 학술지를 먼저, 그다음 오늘 몫 (중복 제거)
    pending = load_pending()
    if only:
        targets = [NAME_TO_JOURNAL[n] for n in only if n in NAME_TO_JOURNAL]
    elif pending_only:
        # 워크플로 재시도용: 앞선 시도에서 못 끝낸 학술지만
        targets = [NAME_TO_JOURNAL[n] for n in pending]
    else:
        seen, targets = set(), []
        for j in [NAME_TO_JOURNAL[n] for n in pending] + list(today_journals):
            k = journal_key(j)
            if k not in seen:
                seen.add(k); targets.append(j)
    if not targets:
        print(f"⚠ [{day_key}] 처리할 학술지 없음")
        return 0

    print(f"\n▶ 요일: {day_key.upper()} | 대상 {len(targets)}개 | depth={depth} "
          f"| 시간 예산 {deadline_min:.0f}분")
    if pending:
        print(f"   (지난 실행 이월: {', '.join(pending)})")
    for j in targets:
        print(f"   - {journal_key(j)}")

    print("\nChrome 초기화...")
    sess = RissSession(guard)

    total_new = 0
    unfinished = [journal_key(j) for j in targets]
    stop_reason = ""
    try:
        for journal in targets:
            name = journal_key(journal)
            if guard.minutes_left() < MIN_JOURNAL_MIN:
                stop_reason = f"시간 예산 부족(남은 {guard.minutes_left():.0f}분)"
                break
            try:
                added, complete = process_journal(sess, journal, depth, existing_ids)
                total_new += added
                if complete:
                    unfinished.remove(name)
            except OutOfTime:
                stop_reason = "시간 예산 소진"
                break
            except RissBlocked as e:
                stop_reason = f"연속 차단으로 중단: {e}"
                break
            except Exception as e:
                # 한 학술지에서 예상치 못한 오류가 나도 나머지는 계속
                print(f"  ✗ [{name}] 오류: {type(e).__name__}: {str(e)[:100]}")
                try:
                    sess.restart()
                except Exception:
                    pass
            try:
                guard.sleep(JOURNAL_DELAY)
            except OutOfTime:
                stop_reason = "시간 예산 소진"
                break
    finally:
        sess.quit()
        if only:
            # --only 실행은 이월 목록에서 처리 완료분만 빼고 나머지는 보존
            done = {journal_key(j) for j in targets} - set(unfinished)
            save_pending([n for n in load_pending() if n not in done] + unfinished, stop_reason)
        else:
            save_pending(unfinished, stop_reason)

    s = guard.stats
    print(f"\n{'='*56}")
    print(f"{'✅' if not unfinished else '⏸'} 업데이트 {'완료' if not unfinished else '일부 완료'} [{day_key.upper()}]")
    print(f"   새로 추가된 논문: {total_new}편")
    print(f"   차단 의심 {s['blocks']}회 · 휴식 {s['cooldown_sec']//60}분 · 느린 응답 {s['slow']}회 "
          f"· 경고창 {s['alerts']} · 팝업 {s['popups']}")
    print(f"   상세 검증 실패 {s['invalid_detail']}회 · 저장 보류 {s['skipped_articles']}편")
    if unfinished:
        print(f"   이월(다음 실행에서 이어받기): {', '.join(unfinished)}")
        if stop_reason:
            print(f"   중단 사유: {stop_reason}")
    print(f"   소요: {(time.time()-guard.t0)/60:.0f}분 · 완료 시각 {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # GitHub Actions 요약(Summary)에 표시
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        try:
            with open(summ, "a", encoding="utf-8") as f:
                f.write(f"### RISS [{day_key}] 새 논문 {total_new}편\n"
                        f"- 소요 {(time.time()-guard.t0)/60:.0f}분, 차단 의심 {s['blocks']}회, "
                        f"휴식 {s['cooldown_sec']//60}분, 느린 응답 {s['slow']}회\n"
                        f"- 상세 검증 실패 {s['invalid_detail']}회, 저장 보류 {s['skipped_articles']}편\n"
                        + (f"- ⏸ 이월: {', '.join(unfinished)} ({stop_reason})\n" if unfinished else ""))
        except Exception:
            pass
    return 75 if unfinished else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RISS 요일별 분산 업데이트")
    parser.add_argument("--day", choices=["mon","tue","wed","thu","fri","sat","sun","all"],
                        default=None, help="요일 지정 (기본: 오늘 자동 판별). all=전체 강제 실행")
    parser.add_argument("--depth", type=int, default=None,
                        help="최신 호수 확인 개수 (1 또는 2). 미지정 시 요일 기본값 사용")
    parser.add_argument("--deadline-min", type=float,
                        default=float(os.environ.get("RISS_DEADLINE_MIN", 300)),
                        help="이 시간(분) 안에 스스로 멈추고 저장 (기본 300)")
    parser.add_argument("--only", default="", help="쉼표로 구분한 학술지만 처리")
    parser.add_argument("--pending-only", action="store_true",
                        help="이월 목록(state/riss_pending.json)에 있는 학술지만 처리 (재시도용)")
    args = parser.parse_args()

    day_key = args.day or DAY_MAP[datetime.now().weekday()]
    only = [s.strip() for s in args.only.split(",") if s.strip()] or None
    print(f"실행 요일: {day_key.upper()}")
    sys.exit(run(day_key, depth_override=args.depth, deadline_min=args.deadline_min,
             only=only, pending_only=args.pending_only))
