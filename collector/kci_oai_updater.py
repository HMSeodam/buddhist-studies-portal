#!/usr/bin/env python3
# kci_oai_updater.py — KCI(한국학술지인용색인) 공식 OAI-PMH 로 신규 논문 수집
#
# 왜 필요한가
#   RISS 는 화면을 긁어 오는 방식이라 봇 차단·지연·허위 응답에 취약하다.
#   KCI 는 기관이 공식 제공하는 OAI-PMH(메타데이터 수확 표준 프로토콜)가 있어
#   인증키 없이도 '최근 등록·수정된 논문'을 표준 XML 로 받아올 수 있다.
#   → 등재·등재후보 학술지는 KCI 에서 먼저 받고, RISS 는 KCI 에 없는 학술지와
#     RISS 링크 연결만 맡기면 RISS 요청 수가 크게 줄어든다.
#
# 동작
#   1) https://open.kci.go.kr/oai/request?verb=ListRecords&set=ARTI&metadataPrefix=oai_kci
#      &from=YYYY-MM-DD&until=YYYY-MM-DD 로 기간 내 변경된 논문 전체를 100건씩 수확
#   2) 학술지명이 포털의 학술지와 일치하는 것만 골라
#   3) 기존 데이터(riss_*.json)와 KCI ID·DOI·정규화 제목으로 대조
#        - 있으면: 비어 있는 칸(KCI 링크, DOI, 영문 초록, 쪽수, 소속 등)만 채움 (기존 값은 덮어쓰지 않음)
#        - 없으면: 새 논문으로 추가 + 공지(updates.json) 기록
#   4) (선택) 환경변수 KCI_API_KEY 가 있으면 REST articleDetail 로 키워드까지 보충
#   5) 마지막 수확일을 collector/state/kci_state.json 에 저장 → 다음엔 그 이후만
#
# 사용법
#   python kci_oai_updater.py                    # 지난 수확일(없으면 21일 전)부터 오늘까지
#   python kci_oai_updater.py --from 2026-01-01  # 기간 지정 (초기 적재·점검용)
#   python kci_oai_updater.py --dry-run          # 저장하지 않고 결과만 출력
#   python kci_oai_updater.py --discover         # 학술지명이 안 맞는 불교 관련 학술지 이름을 보고
#
# 참고: KCI 방화벽은 User-Agent 로 걸러낸다(curl 기본 UA 차단). requests + 브라우저형 UA 사용.

import argparse, json, os, re, sys, time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_log import record_update, atomic_write_json, today_kst   # noqa: E402

OUTPUT_DIR = "../output"
STATE_FILE = "state/kci_state.json"
OAI_URL    = "https://open.kci.go.kr/oai/request"
REST_URL   = "https://open.kci.go.kr/po/openapi/openApiSearch.kci"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 BuddhistStudiesPortal/1.0")
THROTTLE   = 1.0           # 요청 간 간격(초) — 공공 API 에 대한 예의
DEFAULT_LOOKBACK_DAYS = 21
OVERLAP_DAYS = 3           # 지난 수확일과 겹치게 조금 앞에서 시작 (누락 방지)

# 포털 학술지명 → KCI 에서 쓰일 수 있는 학술지명(별칭). 비교는 공백·기호 제거 후 '완전 일치'.
# 첫 실행 뒤 --discover 결과를 보고 필요한 별칭을 추가하면 된다.
KCI_JOURNALS = {
    "불교미술사학":   ["불교미술사학"],
    "동악미술사학":   ["동악미술사학"],
    "강좌미술사":     ["강좌미술사", "강좌 미술사"],
    "정토학연구":     ["정토학연구"],
    "선문화연구":     ["선문화연구"],
    "불교문예연구":   ["불교문예연구"],
    "불교학보":       ["불교학보"],
    "불교학연구":     ["불교학연구"],
    "한국불교학":     ["한국불교학"],
    "선학":           ["선학", "禪學"],
    "불교연구":       ["불교연구"],
    "동아시아불교문화": ["동아시아불교문화"],
    "불교철학":       ["불교철학"],
    "대각사상":       ["대각사상"],
    "보조사상":       ["보조사상", "보조사상 : 보조사상연구원 논문집"],
    "한국교수불자연합학회지": ["한국교수불자연합학회지"],
    "불교학리뷰":     ["불교학리뷰", "불교학 리뷰"],
    "불교학밀교학연구": ["불교학밀교학연구"],
    "인도철학":       ["인도철학"],
    "명상심리상담":   ["명상심리상담"],
    "불교와 사회":    ["불교와 사회", "불교와사회"],
    "한국불교사연구": ["한국불교사연구"],
    "한마음연구":     ["한마음연구"],
    "IJBTC":          ["International Journal of Buddhist Thought & Culture",
                       "International Journal of Buddhist Thought and Culture", "IJBTC"],
    "종학연구":       ["종학연구"],
    "무형문화연구":   ["무형문화연구"],
    "세화불학":       ["세계불학", "세화불학"],
    "전자불전":       ["전자불전"],
    "원불교사상과 종교문화": ["원불교사상과 종교문화", "원불교사상과종교문화"],
}
# --discover 때 '혹시 우리 학술지인데 이름이 달라서 놓친 것'을 찾는 단서
DISCOVER_HINTS = ["불교", "불학", "선학", "禪", "佛", "Buddh", "정토", "미술사", "원불교", "명상", "인도철학", "대각", "보조"]


def _norm(s: str) -> str:
    return re.sub(r"[\s\-_:·ㆍ&,.()（）\[\]]", "", (s or "")).lower().replace("and", "")

KCI_NAME_MAP = {_norm(alias): ours for ours, aliases in KCI_JOURNALS.items() for alias in aliases}


def norm_title(t: str) -> str:
    t = (t or "").lower()
    return re.sub(r"[\s\-‐‑–—_:;,.·ㆍ\"'“”‘’`「」『』《》〈〉()\[\]{}!?/\\]+", "", t)


# ════════════════════════════════════════
#  OAI-PMH 수확
# ════════════════════════════════════════

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def _find(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return c
    return None

def _findall(el, name):
    return [c for c in el.iter() if _local(c.tag) == name]

def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_oai_kci(xml_text: str):
    """ListRecords(oai_kci) 응답 → (레코드 dict 목록, resumptionToken, 오류코드)"""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    err = _find(root, "error")
    if err is not None:
        return [], "", err.get("code", "error")
    recs = []
    for rec in _findall(root, "record"):
        header = _find(rec, "header")
        if header is not None and header.get("status") == "deleted":
            continue
        ji, ai = _find(rec, "journalInfo"), _find(rec, "articleInfo")
        if ji is None or ai is None:
            continue
        titles = {t.get("lang", "original"): _text(t) for t in _findall(ai, "article-title")}
        abstracts = {a.get("lang", "original"): _text(a) for a in _findall(ai, "abstract")}
        authors = []
        an = _find(ai, "author-name")
        if an is not None:
            for a in [x for x in an if _local(x.tag) == "author"]:
                nm = _text(_find(a, "name"))
                if nm:
                    authors.append({"name": nm, "affiliation": _text(_find(a, "affiliation")),
                                    "order": str(len(authors) + 1)})
        if not authors:
            ag = _find(ai, "author-group")
            for a in ([x for x in ag if _local(x.tag) == "author"] if ag is not None else []):
                raw = _text(a)
                m = re.match(r"^(.*?)\((.*)\)\s*$", raw)
                nm, aff = (m.group(1).strip(), m.group(2).strip()) if m else (raw, "")
                if nm:
                    authors.append({"name": nm, "affiliation": aff, "order": str(len(authors) + 1)})
        doi = _text(_find(ai, "doi"))
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
        recs.append({
            "kci_id": ai.get("article-id", ""),
            "journal": _text(_find(ji, "journal-name")),
            "publisher": _text(_find(ji, "publisher-name")),
            "year": _text(_find(ji, "pub-year")),
            "volume": _text(_find(ji, "volume")),
            "issue": _text(_find(ji, "issue")),
            "title_orig": titles.get("original", ""),
            "title_en": titles.get("english", ""),
            "abstract_orig": abstracts.get("original", ""),
            "abstract_en": abstracts.get("english", ""),
            "authors": authors,
            "fpage": _text(_find(ai, "fpage")),
            "lpage": _text(_find(ai, "lpage")),
            "doi": doi,
            "url": _text(_find(ai, "url")),
            "language": _text(_find(ai, "language")),
        })
    tok = _find(root, "resumptionToken")
    return recs, (_text(tok) if tok is not None else ""), ""


def parse_oai_dc(xml_text: str):
    """ListRecords(oai_dc) 응답 → parse_oai_kci 와 같은 형태. (oai_kci 가 막힐 때 대체용)"""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    err = _find(root, "error")
    if err is not None:
        return [], "", err.get("code", "error")
    recs = []
    for rec in _findall(root, "record"):
        header = _find(rec, "header")
        if header is not None and header.get("status") == "deleted":
            continue
        md = _find(rec, "metadata")
        if md is None:
            continue
        titles = {t.get("lang", "original"): _text(t) for t in _findall(md, "title")}
        descs = {d.get("lang", "original"): _text(d) for d in _findall(md, "description")}
        ids = {}
        for i in _findall(md, "identifier"):
            ids.setdefault(i.get("type", ""), _text(i))
        # "대중서사연구, 21(1), 34, pp.227-260"
        ji = ids.get("journalInfo", "")
        parts = [x.strip() for x in ji.split(",")]
        journal = parts[0] if parts else ""
        vol = iss = fp = lp = ""
        for x in parts[1:]:
            m = re.match(r"^(\d+)?\s*\((\d+)\)$", x)
            if m: vol, iss = m.group(1) or "", m.group(2)
            elif re.match(r"^\d+$", x) and not vol: vol = x
            m2 = re.search(r"pp?\.\s*(\d+)\s*-\s*(\d+)", x)
            if m2: fp, lp = m2.group(1), m2.group(2)
        authors = []
        for c in _findall(md, "creator"):
            raw = _text(c); m = re.match(r"^(.*?)\((.*)\)\s*$", raw)
            nm, aff = (m.group(1).strip(), m.group(2).strip()) if m else (raw, "")
            if nm: authors.append({"name": nm, "affiliation": aff, "order": str(len(authors) + 1)})
        date = _text(_find(md, "date"))
        recs.append({
            "kci_id": ids.get("artiId", ""), "journal": journal,
            "publisher": _text(_find(md, "publisher")), "year": date[:4],
            "volume": vol, "issue": iss,
            "title_orig": titles.get("original", ""), "title_en": titles.get("english", ""),
            "abstract_orig": descs.get("original", ""), "abstract_en": descs.get("english", ""),
            "authors": authors, "fpage": fp, "lpage": lp,
            "doi": re.sub(r"^https?://(dx\.)?doi\.org/", "", ids.get("doi", "")),
            "url": _text(_find(md, "url")), "language": _text(_find(md, "language")),
        })
    tok = _find(root, "resumptionToken")
    return recs, (_text(tok) if tok is not None else ""), ""


def http_get(session, url, params, tries=4, stats=None):
    for i in range(tries):
        try:
            r = session.get(url, params=params, timeout=60)
            if r.status_code == 200:
                return r.text
            print(f"  ⚠ HTTP {r.status_code} (시도 {i+1}/{tries}) {r.text[:120]!r}")
            if stats is not None: stats["http_errors"].append(r.status_code)
        except requests.RequestException as e:
            print(f"  ⚠ 네트워크 오류 (시도 {i+1}/{tries}): {type(e).__name__}")
        time.sleep(5 * (2 ** i))
    return None


def _harvest_window(s, date_from, date_until, deadline, stats, matched, seen, unmatched, discover, fmt_state):
    """한 기간(보통 7일)을 끝까지 수확. 반환: (완료 여부, 사유)"""
    fmt = fmt_state["fmt"]
    parser = parse_oai_kci if fmt == "oai_kci" else parse_oai_dc
    params = {"verb": "ListRecords", "set": "ARTI", "metadataPrefix": fmt, "from": date_from, "until": date_until}
    page_in_win = 0
    while True:
        if time.time() > deadline:
            return False, "시간 예산 소진"
        text = http_get(s, OAI_URL, params, stats=stats)
        if text is None and fmt == "oai_kci" and stats["pages"] == 0:
            print("  ↪ 상세 형식(oai_kci) 응답 실패 → 간략 형식(oai_dc)으로 다시 시도")
            fmt = fmt_state["fmt"] = stats["format"] = "oai_dc"; parser = parse_oai_dc
            params = {"verb": "ListRecords", "set": "ARTI", "metadataPrefix": fmt, "from": date_from, "until": date_until}
            continue
        if text is None:
            return False, "KCI 서버 응답 없음"
        try:
            recs, token, err = parser(text)
        except ET.ParseError:
            return False, "XML 아님(차단 안내 페이지일 수 있음): " + re.sub(r"\s+", " ", text[:150])
        if err == "noRecordsMatch":
            return True, ""
        if err:
            # 토큰과 함께 보낸 추가 인자를 거부하면(badArgument) 표준 방식으로 전환
            if err == "badArgument" and "resumptionToken" in params and fmt_state["page_style"] == "kci":
                fmt_state["page_style"] = "std"
                params = {"verb": "ListRecords", "resumptionToken": params["resumptionToken"]}
                stats["notes"].append("다음 쪽 요청 방식: 표준(토큰만)으로 전환")
                continue
            return False, f"OAI 오류 {err}"
        stats["pages"] += 1; page_in_win += 1
        fresh = 0
        for r in recs:
            if r["kci_id"] in seen:
                continue
            seen.add(r["kci_id"]); fresh += 1; stats["total"] += 1
            ours = KCI_NAME_MAP.get(_norm(r["journal"]))
            if ours:
                r["journal_ours"] = ours
                matched.append(r)
                stats["per_journal"][ours] = stats["per_journal"].get(ours, 0) + 1
            elif discover and any(h.lower() in r["journal"].lower() for h in DISCOVER_HINTS):
                unmatched[r["journal"]] = unmatched.get(r["journal"], 0) + 1
        if stats["pages"] <= 3:
            print(f"    쪽{stats['pages']}: 레코드 {len(recs)}건(새 {fresh}) · 다음 토큰 {token!r}")
        if stats["pages"] % 20 == 0:
            print(f"  … {stats['pages']}쪽 / {stats['total']:,}건 확인, 대상 학술지 {len(matched)}건")
        if not token:
            return True, ""
        if not fresh and page_in_win > 1:
            # 같은 목록이 되풀이됨 → 다른 방식으로 다음 쪽 요청 시도
            if fmt_state["page_style"] == "kci":
                fmt_state["page_style"] = "std"
                stats["notes"].append("같은 쪽이 반복되어 다음 쪽 요청 방식을 표준(토큰만)으로 전환")
            elif fmt_state["page_style"] == "std":
                fmt_state["page_style"] = "full"
                stats["notes"].append("같은 쪽이 반복되어 다음 쪽 요청에 set 까지 포함")
            else:
                return False, "다음 쪽으로 넘어가지 않음(같은 목록 반복)"
        style = fmt_state["page_style"]
        if style == "kci":    # KCI 자체 링크 방식: 토큰 + metadataPrefix
            params = {"verb": "ListRecords", "metadataPrefix": fmt, "resumptionToken": token}
        elif style == "std":  # OAI-PMH 표준: 토큰만
            params = {"verb": "ListRecords", "resumptionToken": token}
        else:
            params = {"verb": "ListRecords", "metadataPrefix": fmt, "set": "ARTI", "resumptionToken": token}
        time.sleep(THROTTLE)


def harvest(date_from: str, date_until: str, deadline: float, discover: bool = True, on_window_done=None):
    """
    기간을 7일 단위로 나눠 수확 (긴 기간도 중간까지 저장·이어받기 가능).
    반환: (대상 레코드, 이름이 안 맞은 불교 관련 학술지, 끝까지 받았는지, 통계, 완료된 마지막 날짜)
    """
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "text/xml,application/xml;q=0.9,*/*;q=0.8",
                      "Accept-Language": "ko-KR,ko;q=0.9"})
    stats = {"pages": 0, "total": 0, "format": "oai_kci", "http_errors": [], "error": "",
             "per_journal": {}, "notes": [], "windows": 0}
    matched, seen, unmatched = [], set(), {}
    fmt_state = {"fmt": "oai_kci", "page_style": "kci"}
    d0 = datetime.strptime(date_from, "%Y-%m-%d"); dend = datetime.strptime(date_until, "%Y-%m-%d")
    done_until = ""
    while d0 <= dend:
        d1 = min(d0 + timedelta(days=6), dend)
        a, b = d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")
        before = stats["total"]
        ok, why = _harvest_window(s, a, b, deadline, stats, matched, seen, unmatched, discover, fmt_state)
        if not ok:
            stats["error"] = f"{a}~{b}: {why}"
            print(f"  ✗ {stats['error']}")
            return matched, unmatched, False, stats, done_until
        stats["windows"] += 1
        done_until = b
        print(f"  ✓ {a}~{b}: KCI 레코드 {stats['total']-before:,}건 (누적 {stats['total']:,}건, 대상 {len(matched)}건)")
        if on_window_done:
            on_window_done(b)
        d0 = d1 + timedelta(days=1)
    print(f"  수확 완료({stats['format']}): {stats['pages']}쪽 · KCI 레코드 {stats['total']:,}건 중 대상 학술지 {len(matched)}건")
    return matched, unmatched, True, stats, done_until


# ════════════════════════════════════════
#  (선택) REST articleDetail — 키워드 보충
# ════════════════════════════════════════

def fetch_keywords(kci_id: str, key: str) -> list:
    try:
        r = requests.get(REST_URL, params={"apiCode": "articleDetail", "key": key, "id": kci_id},
                         headers={"User-Agent": UA}, timeout=30)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        return [_text(k) for k in _findall(root, "keyword") if _text(k)]
    except Exception:
        return []


# ════════════════════════════════════════
#  병합
# ════════════════════════════════════════

def to_article(r: dict) -> dict:
    orig_is_en = bool(re.match(r"^[\x00-\x7F]+$", r["title_orig"] or "")) and r["language"] in ("영어", "English")
    return {
        "article_id": r["kci_id"], "id": r["kci_id"], "kci_id": r["kci_id"],
        "title_kr": r["title_orig"],
        "title_en": r["title_en"] if not orig_is_en else "",
        "authors": r["authors"],
        "abstract_kr": r["abstract_orig"] if not orig_is_en else "",
        "abstract_en": r["abstract_en"] or (r["abstract_orig"] if orig_is_en else ""),
        "journal_name": r["journal_ours"],
        "year": r["year"], "volume": r["volume"] if r["volume"] not in ("0",) else "",
        "issue": r["issue"],
        "start_page": r["fpage"], "end_page": r["lpage"],
        "doi": r["doi"], "riss_url": "", "kci_url": r["url"],
        "keywords_kr": [], "keywords_en": [], "ai_keywords": [],
        "source": "KCI",
    }


FILL_FIELDS = ["title_en", "abstract_kr", "abstract_en", "doi", "start_page", "end_page", "kci_url", "kci_id", "year"]

def merge(records: list, dry: bool, api_key: str) -> dict:
    by_journal = {}
    for r in records:
        by_journal.setdefault(r["journal_ours"], []).append(r)
    summary = {}
    for jname, recs in by_journal.items():
        path = Path(OUTPUT_DIR) / f"riss_{jname}.json"
        data = json.load(open(path, encoding="utf-8")) if path.exists() else {"info": {"name": jname}, "articles": []}
        if isinstance(data, list):
            data = {"info": {"name": jname}, "articles": data}
        arts = data["articles"]
        idx_id, idx_doi, idx_title = {}, {}, {}
        for a in arts:
            if a.get("kci_id"): idx_id[a["kci_id"]] = a
            m = re.search(r"ART\d+", a.get("kci_url", "") or "")
            if m: idx_id.setdefault(m.group(), a)
            if a.get("doi"): idx_doi[a["doi"].lower()] = a
            nt = norm_title(a.get("title_kr", ""))
            if nt: idx_title.setdefault(nt, a)
        added, filled = [], 0
        for r in recs:
            new = to_article(r)
            ex = (idx_id.get(r["kci_id"]) or (idx_doi.get(r["doi"].lower()) if r["doi"] else None)
                  or idx_title.get(norm_title(r["title_orig"])))
            if ex is not None:
                changed = False
                for k in FILL_FIELDS:
                    if new.get(k) and not ex.get(k):
                        ex[k] = new[k]; changed = True
                # 저자는 있고 소속만 비어 있으면 이름이 같은 경우에 소속만 채움
                kmap = {x["name"]: x.get("affiliation", "") for x in new["authors"]}
                for au in ex.get("authors", []) or []:
                    if not au.get("affiliation") and kmap.get(au.get("name")):
                        au["affiliation"] = kmap[au["name"]]; changed = True
                if not ex.get("authors") and new["authors"]:
                    ex["authors"] = new["authors"]; changed = True
                filled += changed
                continue
            if api_key:
                kws = fetch_keywords(r["kci_id"], api_key)
                ko = [k for k in kws if not re.match(r"^[\x00-\x7F]+$", k)]
                en = [k for k in kws if re.match(r"^[\x00-\x7F]+$", k)]
                new["keywords_kr"], new["keywords_en"] = ko, en
                time.sleep(THROTTLE)
            arts.append(new); added.append(new)
            idx_id[r["kci_id"]] = new
            idx_title[norm_title(new["title_kr"])] = new
        summary[jname] = (len(added), filled)
        print(f"  [{jname}] 새 논문 {len(added)}편 · 기존 레코드 보완 {filled}건")
        if dry or (not added and not filled):
            continue
        data.setdefault("info", {"name": jname})["last_updated"] = today_kst()
        atomic_write_json(path, data)
        groups = {}
        for a in added:
            k = (a["year"], a["volume"], a["issue"])
            groups[k] = groups.get(k, 0) + 1
        for (y, v, i), n in groups.items():
            record_update(OUTPUT_DIR, jname, n, volume=v, issue=i, year=y, source="KCI")
    return summary


def main():
    ap = argparse.ArgumentParser(description="KCI OAI-PMH 신규 논문 수집")
    ap.add_argument("--from", dest="date_from", default="")
    ap.add_argument("--until", dest="date_until", default="")
    ap.add_argument("--deadline-min", type=float, default=18)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--discover", action="store_true", help="(항상 켜져 있음, 호환용)")
    args = ap.parse_args()

    deadline = time.time() + args.deadline_min * 60
    state = {}
    if Path(STATE_FILE).exists():
        try: state = json.load(open(STATE_FILE, encoding="utf-8"))
        except Exception: state = {}
    today = datetime.strptime(today_kst(), "%Y-%m-%d")
    bf = state.get("backfill") or {}
    if args.date_from and bf.get("from") == args.date_from and bf.get("done_until"):
        # 같은 시작일로 다시 실행하면 지난번에 끝낸 날짜 다음부터 이어받기
        dfrom = (datetime.strptime(bf["done_until"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        print(f"  (지난 {args.date_from} 부터의 점검을 {dfrom} 부터 이어받습니다)")
    elif args.date_from:
        dfrom = args.date_from
    elif state.get("last_until"):
        dfrom = (datetime.strptime(state["last_until"], "%Y-%m-%d") - timedelta(days=OVERLAP_DAYS)).strftime("%Y-%m-%d")
    else:
        dfrom = (today - timedelta(days=DEFAULT_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    duntil = args.date_until or today.strftime("%Y-%m-%d")
    print(f"▶ KCI OAI-PMH 수확: {dfrom} ~ {duntil} (시간 예산 {args.deadline_min:.0f}분)")

    if dfrom > duntil:
        print("  이미 끝까지 받았습니다."); dfrom = duntil
    matched, unmatched, complete, stats, done_until = harvest(dfrom, duntil, deadline, True)
    summary = merge(matched, args.dry_run, os.environ.get("KCI_API_KEY", ""))
    # 진행 상황 저장 (병합이 끝난 뒤에만 — 받은 논문이 저장되기 전에 날짜만 앞서가지 않도록)
    if not args.dry_run and done_until:
        Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
        if args.date_from:
            state["backfill"] = {"from": args.date_from, "done_until": done_until, "updated": today_kst()}
        else:
            state["last_until"] = done_until
        state["updated"] = today_kst()
        atomic_write_json(Path(STATE_FILE), state)
    if unmatched:
        print("\n  🔎 이름이 맞지 않아 건너뛴 불교 관련 학술지 (KCI_JOURNALS 별칭 후보):")
        for n, c in sorted(unmatched.items(), key=lambda x: -x[1])[:40]:
            print(f"     {c:4}건  {n}")
    total_new = sum(v[0] for v in summary.values())
    print(f"\n✅ KCI 완료: 새 논문 {total_new}편 · 보완 {sum(v[1] for v in summary.values())}건"
          + ("" if complete else " (일부만 처리 — 다음 실행에서 같은 기간 재시도)"))
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        filled = sum(v[1] for v in summary.values())
        L = [f"### KCI OAI {dfrom}~{duntil}: 새 논문 {total_new}편 · 기존 보완 {filled}건",
             f"- 확인한 KCI 레코드: **{stats['total']:,}건** ({stats['pages']}쪽, 형식 {stats['format']}, "
             f"완료 구간 {stats['windows']}주{', ~'+done_until+' 까지' if done_until else ''})",
             f"- 우리 학술지로 연결된 레코드: **{len(matched)}건**"]
        if stats["per_journal"]:
            L.append("- 학술지별: " + ", ".join(f"{k} {v}건(새 {summary.get(k,(0,0))[0]}·보완 {summary.get(k,(0,0))[1]})"
                                              for k, v in sorted(stats["per_journal"].items(), key=lambda x: -x[1])))
        if unmatched:
            L.append("- 이름이 안 맞아 건너뛴 불교 관련 학술지: " + ", ".join(f"{n}({c})" for n, c in
                     sorted(unmatched.items(), key=lambda x: -x[1])[:15]))
        if stats["http_errors"]:
            L.append(f"- HTTP 오류: {stats['http_errors'][:10]}")
        for n in stats["notes"]:
            L.append(f"- 참고: {n}")
        if stats["error"] or not complete:
            L.append(f"- ⚠ 미완료: {stats['error'] or '중단'} → "
                     + (f"같은 시작일({args.date_from})로 다시 실행하면 {done_until or dfrom} 이후부터 이어받습니다" if args.date_from
                        else "다음 실행에서 이어받습니다"))
        with open(summ, "a", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
