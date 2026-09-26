#!/usr/bin/env python3
# match_util.py — RISS·KCI 에서 온 같은 논문을 알아보는 공통 규칙
#
# 같은 논문인데도 출처마다 제목이 조금씩 다르게 적힌다.
#   · KCI 는 원제 뒤에 한국어 번역 제목을 덧붙이기도 함
#       "中国禅茶文化研究轨迹与 AI 时代走向 중국 선차 문화 연구의 궤적과…"
#   · 각주 표시 "*", 특수 괄호 문자(󰡔), 띄어쓰기·문장부호 차이
#   · 제목이 두 번 겹쳐 적힌 경우 "조주선사…해석조주선사…해석"
#   · 권/호가 서로 바뀌어 적힌 경우 (RISS 28권 ↔ KCI 28호)
# 그래서 '같은 학술지 + 같은 연도 + 권호 숫자 겹침' 안에서
# 정규화한 제목이 같거나, 한쪽이 다른 쪽을 포함하거나, 매우 비슷하면 같은 논문으로 본다.

import re
import unicodedata
from difflib import SequenceMatcher

MIN_CONTAIN = 8      # 포함 관계로 볼 최소 글자 수 (짧은 '발간사' 같은 제목 오판 방지)
MIN_RATIO   = 0.88   # 유사도 기준


def ntitle(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "").lower()
    t = re.sub(r"[\W_]+", "", t)          # 공백·문장부호·*·사용자정의문자 제거 (한자·한글·영문·숫자만 남김)
    n = len(t)
    if n >= 8 and n % 2 == 0 and t[: n // 2] == t[n // 2:]:
        t = t[: n // 2]                    # 제목이 두 번 겹쳐 적힌 경우
    return t


def clean_title(t: str) -> str:
    """표시용 제목 정리: 두 번 겹친 제목·끝의 각주 표시·겹친 공백."""
    s = re.sub(r"\s+", " ", (t or "")).strip()
    s = re.sub(r"\s*[*＊]+$", "", s)
    half = len(s) // 2
    for cut in (half, half + 1, half - 1):
        a, b = s[:cut].strip(), s[cut:].strip()
        if len(a) >= 6 and ntitle(a) == ntitle(b):
            return a
    return s


def nums(a: dict) -> set:
    out = set()
    for k in ("volume", "issue"):
        for m in re.findall(r"\d+", str(a.get(k) or "")):
            if int(m) > 0:
                out.add(int(m))
    return out


REVIEW_WORDS = ("논평", "토론", "서평", "비평", "반론", "답변", "답론", "comment", "review", "response", "reply")
PART_RE = re.compile(r"(Ⅰ|Ⅱ|Ⅲ|Ⅳ|Ⅴ|Ⅵ|⑴|⑵|⑶|⑷|\(\s*(?:I{1,3}|IV|V|VI|\d{1,2}|上|中|下|상|중|하)\s*\)|\b(?:I{1,3}|IV|VI?)\s*$|[上中下]\s*$|\d+\s*$)")


def _parts(t: str) -> set:
    """제목 속 연재 번호 (I)(Ⅱ)⑴ 上下 등."""
    t = unicodedata.normalize("NFKC", t or "")
    return {re.sub(r"[\s()]", "", m.group(0)).lower() for m in PART_RE.finditer(t)}


SEPARATORS = set("-—―－–:：(（[「『<〈《~～·,/|")   # ";" 는 특집 목차 구분이라 제외


def _after_prefix(orig: str, n: int) -> str:
    """원래 제목에서 정규화 기준 n 글자 다음에 오는 첫 글자(공백 제외)."""
    t = unicodedata.normalize("NFKC", orig or "")
    cnt = 0
    for i, ch in enumerate(t):
        if cnt >= n:
            rest = t[i:].lstrip()
            return rest[:1]
        if re.match(r"\w", ch) and ch != "_":
            cnt += 1
    return ""


def _is_hangul(ch: str) -> bool:
    return "\uac00" <= ch <= "\ud7a3"


def same_title(t1: str, t2: str) -> bool:
    if _parts(t1) != _parts(t2) and _parts(t1) and _parts(t2):
        return False            # 연재물의 서로 다른 편
    a, b = ntitle(t1), ntitle(t2)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    # 앞부분이 통째로 같을 때만 (KCI 가 원제 뒤에 번역 제목을 덧붙인 경우).
    # '홍성기의 「원제」' 같은 논평·서평은 앞이 달라서 다른 논문으로 본다.
    if len(short) >= MIN_CONTAIN and long_.startswith(short):
        rest = long_[len(short):]
        if any(w in rest for w in REVIEW_WORDS):
            return False        # '「원제」에 대한 논평' 같은 토론·서평
        if _parts(t1) != _parts(t2):
            return False        # 한쪽만 (1)·(Ⅱ) 등 편 번호가 있음
        # 긴 제목에서 짧은 제목 다음이 부제 구분자(- : ( 「 등)이거나,
        # 한자·영문 원제 뒤에 한글 번역이 붙은 경우(KCI)만 같은 논문으로 본다.
        # '…전산화' vs '…전산화를 위한 …' 처럼 문장이 이어지면 다른 논문.
        lt = t1 if len(a) > len(b) else t2
        nxt = _after_prefix(lt, len(short))
        if nxt in SEPARATORS:
            return True
        cjk_short = sum(1 for c in short if not _is_hangul(c)) >= len(short) * 0.6
        return cjk_short and _is_hangul(nxt)
    if len(short) >= MIN_CONTAIN and short in long_:
        return False            # 앞에 다른 말이 붙은 경우(논평·서평 등) → 다른 논문
    if len(short) / len(long_) < 0.85:
        return False            # 길이가 크게 다르면 유사도만으로 합치지 않음
    if a[:4] != b[:4] or a[-2:] != b[-2:]:
        return False            # 첫머리·끝이 다르면('한국…'/'일본…', '…조각'/'…건축') 다른 논문
    # '비슷하다'만으로 합칠 때는 한글과 숫자가 완전히 같아야 함
    # (로마자 표기·한자 병기·특수문자 차이만 허용 / '화쟁'≠'화엄', '제1호'≠'제2호')
    hg = lambda x: "".join(c for c in x if _is_hangul(c))
    dg = lambda x: re.findall(r"\d+", x)
    if hg(a) != hg(b) or dg(a) != dg(b):
        return False
    if any((w in a) != (w in b) for w in REVIEW_WORDS):
        return False
    return SequenceMatcher(None, a, b).ratio() >= MIN_RATIO


def compatible(a: dict, b: dict) -> bool:
    ya, yb = str(a.get("year") or ""), str(b.get("year") or "")
    if ya and yb and ya != yb:
        return False
    na, nb = nums(a), nums(b)
    if na and nb and not (na & nb):
        return False
    return True


def find_same(rec: dict, candidates, title_key="title_kr"):
    """candidates 중 rec 과 같은 논문을 찾아 돌려준다 (없으면 None)."""
    t = rec.get(title_key) or rec.get("title_orig") or ""
    for c in candidates:
        if compatible(rec, c) and same_title(t, c.get("title_kr") or c.get("title_ja") or ""):
            return c
    return None
