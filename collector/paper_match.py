#!/usr/bin/env python3
# paper_match.py — RISS 레코드와 KCI 레코드가 '같은 논문'인지 판정하는 규칙 (호 단위 대조)
#
# 실제 데이터에서 확인된 차이 (2026-09 점검)
#   · 표기 문자:  RISS '홍각선사비문을 통해 본 선림원'  ↔  KCI '弘覺禪師碑文을 통해 본 禪林院'
#   · 머리말:     RISS '<특집논문1> 불교와 심리치료-…: 군 생활 적응…' / '일반논문 : …'  ↔  KCI 본제목만
#   · 권·호:      RISS '46권'  ↔  KCI '46호'   (숫자 자리가 서로 바뀜)
#   · 연도:       RISS 2011  ↔  KCI 2010 (같은 호인데 발행연도 표기가 1년 차이)
#   · 쪽수:       대개 같거나 1~3쪽 차이
#
# 그래서 제목만으로 판단하지 않고,
#   ① 같은 학술지 · 같은 호(권/호 숫자 집합이 같고 연도 ±1) 안에서
#   ② 한자를 한글 독음으로 바꾸고 머리말·한자 병기를 걷어낸 제목 유사도
#   ③ 시작 쪽수의 근접도
# 를 합쳐 점수가 높은 쌍부터 1:1로 짝짓는다. 호 정보가 맞지 않는 경우에만 제목이 매우 비슷할 때 보조로 짝짓는다.

import re
import unicodedata
from difflib import SequenceMatcher

try:
    import hanja as _hanja
    def _to_hangul(s: str) -> str:
        try:
            return _hanja.translate(s, "substitution")
        except Exception:
            return s
except ImportError:          # hanja 패키지가 없으면 한자는 그대로 (정확도만 조금 떨어짐)
    def _to_hangul(s: str) -> str:
        return s

_CJK = r"㐀-䶿一-鿿豈-﫿"
_PREFIX_RE = re.compile(
    r"^\s*(?:[<〈《\[【][^>〉》\]】]{0,40}[>〉》\]】]\s*)+"          # <특집논문1> [기획] 등
    r"|^\s*(?:일반|특집|기획|연구|투고|발표|초청|학술|번역|자료)?\s*논문\s*\d*\s*[:：]\s*"   # 일반논문 :
    r"|^\s*특집\s*\d*\s*[:：]\s*"
)
_ROMAN = {"ⅰ": 1, "ⅱ": 2, "ⅲ": 3, "ⅳ": 4, "ⅴ": 5, "ⅵ": 6, "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6}
_PART_RE = re.compile(r"(?:\(\s*|⑴|⑵|⑶|⑷|⑸)?\b(ⅰ|ⅱ|ⅲ|ⅳ|ⅴ|ⅵ|i{1,3}|iv|vi?|\d{1,2}|上|中|下|상|하)\s*\)?\s*$")
REVIEW_WORDS = ("논평", "토론", "서평", "비평", "반론", "답변", "답론")


def mtitle(t: str) -> str:
    """대조용 제목: 머리말 제거 → 한자 병기 괄호 제거 → 한자를 한글 독음으로 → 글자·숫자만."""
    s = unicodedata.normalize("NFKC", t or "")
    for _ in range(3):
        s2 = _PREFIX_RE.sub("", s)
        if s2 == s:
            break
        s = s2
    s = re.sub(r"\(\s*[" + _CJK + r"\s·ㆍ,]+\s*\)", "", s)        # 원효(元曉) → 원효
    s = re.sub(r"[" + _CJK + r"]+", lambda m: _to_hangul(m.group(0)), s)
    s = s.lower()
    s = re.sub(r"[^\w]+", "", s).replace("_", "")
    n = len(s)
    if n >= 8 and n % 2 == 0 and s[: n // 2] == s[n // 2:]:
        s = s[: n // 2]                                           # 제목이 두 번 겹쳐 적힌 경우
    return s


def part_no(t: str):
    """연재 번호 (Ⅱ)·(2)·⑵·上 → 숫자/문자 하나로. 없으면 None."""
    s = unicodedata.normalize("NFKC", t or "").strip().lower()
    s = re.sub(r"[\s\-–—―:：]+$", "", s)
    s = re.sub(r"[-–—―]\s*[^-–—―]{0,40}[-–—―]?$", "", s).strip() or s   # 끝의 부제 '-…-' 제거 후 검사
    m = _PART_RE.search(s)
    if not m:
        return None
    v = m.group(1)
    if v in _ROMAN:
        return _ROMAN[v]
    if v.isdigit():
        return int(v)
    return {"上": 1, "상": 1, "中": 2, "下": 3, "하": 3}.get(v, v)


def title_sim(t1: str, t2: str, m1: str = None, m2: str = None) -> float:
    a = m1 if m1 is not None else mtitle(t1)
    b = m2 if m2 is not None else mtitle(t2)
    if not a or not b:
        return 0.0
    if a == b:
        sim = 1.0
    else:
        sm = SequenceMatcher(None, a, b, autojunk=False)
        ratio = sm.ratio()
        lcs = sm.find_longest_match(0, len(a), 0, len(b)).size
        short = min(len(a), len(b))
        contain = lcs / short if short >= 6 else 0.0
        sim = max(ratio, contain * 0.95)
    # 연재물의 다른 편이면 크게 깎음
    p1, p2 = part_no(t1), part_no(t2)
    if p1 is not None and p2 is not None and p1 != p2:
        sim *= 0.4
    # 한쪽이 '「다른쪽 제목」에 대한 논평/토론' 꼴(원제 + 짧은 꼬리말)이면 다른 글
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if short and short in long_:
        extra = long_.replace(short, "", 1)
        if len(extra) <= 12 and any(w in extra for w in REVIEW_WORDS):
            sim *= 0.3
    return sim


def nums(a: dict) -> frozenset:
    out = set()
    for k in ("volume", "issue"):
        for m in re.findall(r"\d+", str(a.get(k) or "")):
            if int(m) > 0:
                out.add(int(m))
    return frozenset(out)


def year_of(a: dict) -> int:
    try:
        return int(str(a.get("year") or "0")[:4])
    except ValueError:
        return 0


def page_of(a: dict):
    m = re.search(r"\d+", str(a.get("start_page") or ""))
    return int(m.group()) if m else None


def _tail(t: str) -> str:
    """'특집명 : 논문제목' 꼴이면 마지막 콜론 뒤(실제 논문 제목)."""
    parts = re.split(r"\s[:：;]\s", t or "")
    return parts[-1] if len(parts) > 1 else ""


def _pair_score(r: dict, k: dict, mr: str, mk: str):
    sim = title_sim(r.get("title_kr", ""), k.get("title_kr", ""), mr, mk)
    for tr, tk in ((_tail(r.get("title_kr", "")), k.get("title_kr", "")),
                   (r.get("title_kr", ""), _tail(k.get("title_kr", "")))):
        if tr and tk and len(mtitle(tr)) >= 6 and len(mtitle(tk)) >= 6:
            sim = max(sim, title_sim(tr, tk))
    pr, pk = page_of(r), page_of(k)
    bonus = 0.0
    if pr is not None and pk is not None:
        d = abs(pr - pk)
        bonus = 0.25 if d == 0 else 0.15 if d <= 3 else 0.05 if d <= 8 else -0.25 if d > 20 else 0.0
    return sim, bonus


def match_issue(riss_recs: list, kci_recs: list):
    """같은 호로 판정된 두 목록을 1:1로 짝지음. 반환: [(riss, kci, 점수)]"""
    mr = [mtitle(r.get("title_kr", "")) for r in riss_recs]
    mk = [mtitle(k.get("title_kr", "")) for k in kci_recs]
    cands = []
    for i, r in enumerate(riss_recs):
        for j, k in enumerate(kci_recs):
            sim, bonus = _pair_score(r, k, mr[i], mk[j])
            ok = sim >= 0.62 or (sim >= 0.38 and bonus >= 0.15)
            if ok:
                cands.append((sim + bonus, i, j))
    cands.sort(reverse=True)
    used_r, used_k, out = set(), set(), []
    for sc, i, j in cands:
        if i in used_r or j in used_k:
            continue
        used_r.add(i); used_k.add(j)
        out.append((riss_recs[i], kci_recs[j], sc))
    return out


def match_journal(riss_recs: list, kci_recs: list):
    """
    한 학술지 안에서 KCI 레코드 → RISS 레코드 짝짓기.
    반환: {id(kci_rec): riss_rec}
    """
    result = {}
    # 0) 이미 알려진 연결 (KCI 논문번호, RISS 번호, DOI)
    by_kid = {(r.get("kci_id") or r.get("kci_hint")): r for r in riss_recs if r.get("kci_id") or r.get("kci_hint")}
    by_rid = {r.get("article_id"): r for r in riss_recs}
    by_doi = {r["doi"].lower(): r for r in riss_recs if r.get("doi")}
    taken = set()
    rest_k = []
    for k in kci_recs:
        r = (by_kid.get(k.get("kci_id")) or by_rid.get(k.get("riss_hint"))
             or (by_doi.get(k["doi"].lower()) if k.get("doi") else None))
        if r is not None and id(r) not in taken:
            result[id(k)] = r; taken.add(id(r))
        else:
            rest_k.append(k)
    free_r = [r for r in riss_recs if id(r) not in taken]
    # 1) 호 단위 대조: 숫자 집합이 같고 연도 ±1
    groups_r = {}
    for r in free_r:
        groups_r.setdefault(nums(r), []).append(r)
    groups_k = {}
    for k in rest_k:
        groups_k.setdefault(nums(k), []).append(k)
    leftover = []
    for key, ks in groups_k.items():
        if not key:
            leftover.extend(ks); continue
        rs = [r for r in groups_r.get(key, []) if id(r) not in taken]
        if not rs:
            leftover.extend(ks); continue
        # 연도별로 나눠 ±1 허용
        for y in sorted({year_of(k) for k in ks}):
            kk = [k for k in ks if year_of(k) == y and id(k) not in result]
            rr = [r for r in rs if id(r) not in taken and (not y or not year_of(r) or abs(year_of(r) - y) <= 1)]
            for r, k, sc in match_issue(rr, kk):
                result[id(k)] = r; taken.add(id(r))
        leftover.extend(k for k in ks if id(k) not in result)
    # 2) 보조: 호 정보가 어긋나도 제목이 매우 비슷하고 연도 ±1
    if leftover:
        pool = [r for r in riss_recs if id(r) not in taken]
        mp = [(r, mtitle(r.get("title_kr", ""))) for r in pool]
        for k in leftover:
            mk = mtitle(k.get("title_kr", ""))
            if len(mk) < 8:
                continue
            best, bs = None, 0.0
            for r, mr in mp:
                if id(r) in taken:
                    continue
                if year_of(k) and year_of(r) and abs(year_of(k) - year_of(r)) > 1:
                    continue
                s = title_sim(k.get("title_kr", ""), r.get("title_kr", ""), mk, mr)
                if s > bs:
                    best, bs = r, s
            if best is not None and bs >= 0.9:
                result[id(k)] = best; taken.add(id(best))
    return result


def find_in_issue(title: str, issue: dict, kci_recs: list, min_sim: float = 0.62):
    """RISS 호 목록의 제목 하나가 같은 호의 KCI 레코드 중 어느 것과 같은 논문인지 (없으면 None)."""
    key = nums(issue)
    if not key:
        return None
    y = year_of(issue)
    mt = mtitle(title)
    best, bs = None, 0.0
    for k in kci_recs:
        if k.get("link_only") or nums(k) != key:
            continue
        if y and year_of(k) and abs(y - year_of(k)) > 1:
            continue
        s = title_sim(title, k.get("title_kr", ""), mt, None)
        t = _tail(title)
        if t and len(mtitle(t)) >= 6:
            s = max(s, title_sim(t, k.get("title_kr", "")))
        if s > bs:
            best, bs = k, s
    return best if bs >= min_sim else None
