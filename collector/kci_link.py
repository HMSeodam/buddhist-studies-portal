#!/usr/bin/env python3
# kci_link.py — RISS 로 수집한 논문 하나하나에 대응하는 KCI 논문번호(ART…)를 찾아 1:1 로 연결한다
#
# 왜 필요한가
#   KCI 공식 수확 통로(OAI-PMH)는 '기간별 변경분 전체'만 주고 학술지별·논문별 요청이 안 된다.
#   그래서 오래된 논문은 그 논문의 KCI 기록이 수정된 날짜를 받을 때까지 번호를 알 수 없다.
#   KCI Open API(REST, 인증키 필요)의 논문 검색(articleSearch)은 '제목 + 학술지명 + 발행연도'로
#   찾을 수 있으므로, 우리가 이미 아는 논문을 하나씩 물어 정확한 번호를 받아 온다.
#
# 동작
#   1) data/riss/riss_<학술지>.json 중 아직 KCI 와 짝이 없는 논문을 고른다 (최신 논문부터)
#   2) KCI 에 제목(앞부분)·학술지명·발행연도±1 로 검색
#   3) 결과 중 같은 학술지 · 같은 호(권/호 숫자) · 제목 유사도(한자→한글 독음 포함) · 시작 쪽수로 검증
#      (collector/paper_match.py — 출력 빌드와 같은 규칙). 확실한 것만 받아들인다
#   4) 찾은 KCI 레코드를 data/kci/kci_<학술지>.json 에 riss_hint(=RISS 번호)와 함께 저장
#      → 출력 빌드가 이 표시로 두 레코드를 바로 짝지어 'KCI 원문' 버튼이 생긴다
#   5) 못 찾은 논문(KCI 수록 이전 호 등)은 state/kci_link_state.json 에 적어 90일 동안 다시 묻지 않는다
#
# 사용법
#   KCI_API_KEY=발급키 python kci_link.py --deadline-min 10 --max-calls 1000
#   python kci_link.py --dry-run --limit 20        # 저장하지 않고 결과만
#
# 인증키: https://www.kci.go.kr → Open API → 인증키 신청 (무료). 저장소 Settings → Secrets → Actions 에 KCI_API_KEY 로.

import argparse, json, os, re, sys, time
from datetime import datetime, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_log import atomic_write_json, today_kst                           # noqa: E402
from paper_match import match_journal, match_issue, nums, year_of, title_sim, mtitle, _PREFIX_RE   # noqa: E402
from kci_oai_updater import (parse_oai_kci, to_article, KCI_NAME_MAP, _norm, REST_URL, UA)  # noqa: E402

RISS_DIR = Path("../data/riss")
KCI_DIR = Path("../data/kci")
STATE = Path("state/kci_link_state.json")
THROTTLE = 0.6
RETRY_MISS_DAYS = 90
SKIP_JOURNALS = {"印度學佛教學研究"}          # KCI 비수록 (J-Stage)

# KCI 에서 쓰는 학술지명 (우리 이름과 다른 것만) — 2026-09 저자 확인
# KCI 검색의 학술지명 조건은 글자가 정확히 같아야 하므로 이체자(硏/研) 표기까지 후보로 둔다.
# 실제로 결과가 나온 이름은 state 에 기억해 두고 다음부터 그 이름만 쓴다.
KCI_QUERY_NAME = {
    "IJBTC": ["International Journal of Buddhist Thought and Culture",
              "International Journal of Buddhist Thought & Culture"],
    "선문화연구": ["禪文化硏究", "禪文化研究", "선문화연구"],
    "종학연구": ["宗學硏究", "宗學研究", "종학연구"],
    "정토학연구": ["정토학연구", "淨土學硏究"],
}


def qnames(ours, known=None):
    lst = ([known] if known else []) + list(KCI_QUERY_NAME.get(ours, [])) + [ours]
    out = []
    for n in lst:
        if n and n not in out:
            out.append(n)
    return out


class Stop(Exception):
    pass


def _ours(journal_name: str) -> str:
    return (KCI_NAME_MAP.get(_norm(journal_name))
            or KCI_NAME_MAP.get(_norm(re.sub(r"[\(（\[].*?[\)）\]]", "", journal_name))) or "")


_LABEL = re.compile(r"특집|특별|기획|논문|總說|特輯|論文|企劃")


def query_titles(title: str, limit: int = 4):
    """검색어 후보 (KCI 쪽 표기가 한자·한글 어느 쪽이든 걸리도록 여러 갈래로)
       ① 콜론 앞뒤 각 부분의 앞머리 (특집·기획 같은 머리말 부분은 제외)
       ② 제목 안의 한자 표기 (괄호 병기 포함: 영산회괘불도(靈山會掛佛圖) → 靈山會掛佛圖)
       ③ 가장 긴 한글 낱말들"""
    t = title or ""
    for _ in range(3):
        t2 = _PREFIX_RE.sub("", t)
        if t2 == t:
            break
        t = t2
    out = []

    def add(q):
        q = re.sub(r"\s+", " ", q or "").strip(" -–—―:：,.")
        if len(q) >= 2 and q not in out:
            out.append(q)

    plain = re.sub(r"\(\s*[㐀-䶿一-鿿豈-﫿\s·ㆍ,]+\s*\)", "", t)          # 한자 병기 괄호 제거
    plain = re.sub(r"[「」『』《》〈〉\"“”‘’'<>]", " ", plain)
    parts = [x for x in re.split(r"\s*[:：]\s*|\s[-–—―]\s?|\s?[-–—―]\s", plain) if x.strip()]
    parts.sort(key=lambda x: 1 if (_LABEL.search(x) and len(x.strip()) <= 14) else 0)   # 머리말은 뒤로
    for part in parts[:2]:
        if _LABEL.search(part) and len(part.strip()) <= 14:
            continue
        q = ""
        for w in part.split():
            if len(q) + len(w) + 1 > 25 and q:
                break
            q = (q + " " + w).strip()
        add(q)
    for h in sorted(re.findall(r"[㐀-䶿一-鿿豈-﫿]{3,}", t), key=len, reverse=True)[:1]:
        add(h[:12])
    for w in sorted(re.findall(r"[가-힣A-Za-z]{3,}", plain), key=len, reverse=True)[:2]:
        add(w)
    return out[:limit]


SWEEP_TOKENS = {"ko": ["의", "연구", "硏究", "研究"], "en": ["the", "of", "and"]}


class KCI:
    def __init__(self, key, max_calls, deadline):
        self.key, self.max_calls, self.deadline = key, max_calls, deadline
        self.calls = 0
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "text/xml,application/xml;q=0.9,*/*;q=0.8"})

    def search(self, title, journal="", year=0, page=1, exact_year=False):
        if self.calls >= self.max_calls:
            raise Stop(f"호출 한도 {self.max_calls}회")
        if time.time() > self.deadline:
            raise Stop("시간 예산 소진")
        p = {"key": self.key, "apiCode": "articleSearch", "title": title, "displayCount": 100, "page": page}
        if journal:
            p["journal"] = journal
        if year:
            p["dateFrom"], p["dateTo"] = (f"{year}01", f"{year}12") if exact_year else (f"{year - 1}01", f"{year + 1}12")
        text = None
        for i in range(4):
            try:
                self.calls += 1
                r = self.s.get(REST_URL, params=p, timeout=40)
                if r.status_code == 200:
                    text = r.text
                    break
                if r.status_code in (401, 403):
                    raise Stop(f"KCI 가 인증키를 거부함(HTTP {r.status_code})")
            except requests.RequestException:
                pass
            time.sleep(3 * (i + 1))
        time.sleep(THROTTLE)
        if text is None:
            raise Stop("KCI 서버 응답 없음")
        mt = re.search(r"<total>\s*(\d+)", text)
        total = int(mt.group(1)) if mt else 0
        if "<record" not in text:
            # 결과 없음: KCI 는 '<total>' 없이 'No Data' 로 답하기도 한다 (요청 내용을 되풀이한 inputData 는 빼고 판단)
            body = re.sub(r"<inputData>.*?</inputData>", " ", text, flags=re.S)
            if re.search(r"No\s*Data|검색\s*결과가\s*없|결과가 없습니다", body, re.I):
                return [], 0
            if not mt and re.search(r"인증키|유효하지|권한|한도|초과|limit|invalid", body, re.I):
                raise Stop("KCI 응답: " + re.sub(r"<[^>]+>|\s+", " ", body)[:120].strip())
            return [], total
        try:
            recs, _, _ = parse_oai_kci(text)
        except ET.ParseError:
            return [], total
        return recs, total


def pick(r, cands, ours):
    """같은 학술지 · 같은 호 · 제목·쪽수로 검증된 후보 하나 (없으면 None)."""
    arts = []
    for c in cands:
        if _ours(c.get("journal", "")) != ours or not c.get("kci_id"):
            continue
        c = dict(c, journal_ours=ours)
        a = to_article(c)
        a["_raw"] = c
        arts.append(a)
    if not arts:
        return None
    y = year_of(r)
    same = [a for a in arts if nums(a) and nums(a) == nums(r) and (not y or not year_of(a) or abs(year_of(a) - y) <= 1)]
    if same:
        m = match_issue([r], same)
        if m:
            return m[0][1]
    # 호 표기가 어긋나는 경우: 연도 ±1 이면서 제목이 거의 같을 때만
    best, bs = None, 0.0
    mr = mtitle(r.get("title_kr", ""))
    for a in arts:
        if y and year_of(a) and abs(year_of(a) - y) > 1:
            continue
        s = title_sim(r.get("title_kr", ""), a.get("title_kr", ""), mr, None)
        if s > bs:
            best, bs = a, s
    return best if bs >= 0.9 and len(mr) >= 8 else None


def main():
    ap = argparse.ArgumentParser(description="RISS 논문 ↔ KCI 논문번호 1:1 연결 (KCI Open API)")
    ap.add_argument("--deadline-min", type=float, default=10)
    ap.add_argument("--max-calls", type=int, default=1000)
    ap.add_argument("--limit", type=int, default=0, help="논문 수 제한(시험용)")
    ap.add_argument("--journal", default="", help="이 학술지만")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("KCI_API_KEY", "").strip()
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if not key:
        msg = ("KCI 논문번호 연결: 인증키(KCI_API_KEY)가 없어 건너뜀 — "
               "키를 넣으면 RISS 논문마다 KCI 원문 링크를 찾아 붙입니다")
        print("ℹ " + msg)
        if summ:
            open(summ, "a", encoding="utf-8").write(f"### {msg}\n")
        return
    if not RISS_DIR.exists():
        print("data/riss 없음 — 건너뜀"); return

    state = {}
    if STATE.exists():
        try: state = json.load(open(STATE, encoding="utf-8"))
        except Exception: state = {}
    miss = state.setdefault("miss", {})
    cutoff = (datetime.strptime(today_kst(), "%Y-%m-%d") - timedelta(days=RETRY_MISS_DAYS)).strftime("%Y-%m-%d")

    # 1) 짝이 없는 RISS 논문 모으기
    swept0 = state.setdefault("swept", {})
    qname = state.setdefault("qname", {})
    reset_j = set()
    by_j0 = {}
    for k_, v in swept0.items():
        by_j0.setdefault(k_.split("|")[0], []).append((v or {}).get("n", 0))
    tried_reset = state.setdefault("reset_tried", {})
    for j_, ns in by_j0.items():
        # 다시 시도는 학술지마다 90일에 한 번만 (전자불전처럼 KCI 에 정말 없는 학술지를 매일 되풀이하지 않도록)
        if len(ns) >= 3 and not any(ns) and j_ not in qname and tried_reset.get(j_, "") <= cutoff:
            reset_j.add(j_)
            tried_reset[j_] = today_kst()
        elif any(ns) and j_ not in qname:
            qname[j_] = qnames(j_)[0]           # 예전 실행에서 결과가 나왔던 이름
    for k_ in [k_ for k_ in swept0 if k_.split("|")[0] in reset_j]:
        del swept0[k_]
    if reset_j:
        print("  ↺ 학술지명 조건이 맞지 않았던 것으로 보여 다시 시도:", ", ".join(sorted(reset_j)))
    todo, stores = [], {}
    paired_k = set()             # 이미 RISS 와 짝이 된 KCI 번호 — 다른 RISS 레코드(중복 수집분 등)에 또 붙이지 않음
    probe = (0, "", 0)           # (KCI 레코드 수, 학술지, 연도) — 1단계 가능 여부를 시험할 곳
    for f in sorted(RISS_DIR.glob("riss_*.json")):
        ours = f.stem[5:]
        if ours in SKIP_JOURNALS or (args.journal and ours != args.journal):
            continue
        riss = json.load(open(f, encoding="utf-8")).get("articles", [])
        kp = KCI_DIR / f"kci_{ours}.json"
        kd = json.load(open(kp, encoding="utf-8")) if kp.exists() else {"articles": []}
        stores[ours] = (kp, kd)
        karts = kd.get("articles", [])
        mm = match_journal(riss, karts)
        paired = {id(r) for r in mm.values()}
        paired_k.update(a.get("kci_id") for a in karts if id(a) in mm)
        ycount = {}
        for a in karts:
            if not a.get("link_only") and year_of(a):
                ycount[year_of(a)] = ycount.get(year_of(a), 0) + 1
        if ycount:
            yb = max(ycount, key=ycount.get)
            if ycount[yb] > probe[0]:
                probe = (ycount[yb], ours, yb)
        for r in riss:
            if id(r) in paired or r.get("kci_id") or r.get("kci_hint") or not r.get("title_kr"):
                continue
            if "/recommender/" in (r.get("riss_url") or ""):
                continue
            rid = r.get("article_id", "")
            if ours in reset_j:
                miss.pop(rid, None)
            if miss.get(rid, "") > cutoff:
                continue
            todo.append((ours, r))
    todo.sort(key=lambda x: -year_of(x[1]))            # 최신 논문부터
    if args.limit:
        todo = todo[: args.limit]
    print(f"▶ KCI 번호 연결 대상 {len(todo):,}편 (시간 {args.deadline_min:.0f}분 · 호출 최대 {args.max_calls}회)")

    api = KCI(key, args.max_calls, time.time() + args.deadline_min * 60)
    found = {}
    tried = 0
    stop_reason = ""
    per_j = {}
    done = set()                 # 이번 실행에서 짝을 찾은 RISS 레코드 id()
    used_k = set(paired_k)       # 이미 쓰인 KCI 번호

    def accept(ours, r, art, how):
        raw = art.pop("_raw", None) or {}
        art["publisher"] = raw.get("publisher", "")
        art["kci_year"], art["kci_volume"], art["kci_issue"] = raw.get("year"), raw.get("volume"), raw.get("issue")
        art["riss_hint"] = r.get("article_id", "")
        found.setdefault(ours, []).append(art)
        done.add(id(r)); used_k.add(art["kci_id"])
        miss.pop(r.get("article_id", ""), None)
        per_j[ours] = per_j.get(ours, 0) + 1
        if sum(per_j.values()) <= 15:
            print(f"  ✓ [{ours}] {r['title_kr'][:40]} → {art['kci_id']} ({how})")

    def as_art(c, ours):
        a = to_article(dict(c, journal_ours=ours)); a["_raw"] = c
        return a

    tried_ids = set()
    swept = state.setdefault("swept", {})
    by_j = {}
    for ours, r in todo:
        by_j.setdefault(ours, []).append(r)
    try:
        # ── 1단계: 학술지·연도별 훑기 ─────────────────────────────
        # 흔한 낱말(의·연구·硏究…)로 한 해치 논문을 통째로 받아, 출력 빌드와 같은 호 단위 대조로 한꺼번에 짝짓는다.
        # (KCI 제목 검색이 이런 짧은 검색어를 받지 않으면 처음 몇 번 만에 알아채고 2단계로 넘어감)
        sweep_ok = False
        if probe[0]:
            # KCI 에 레코드가 많은 것으로 확인된 학술지·연도에 '의'로 물어 보아, 짧은 검색어가 통하는지 먼저 시험
            _, pj, py = probe
            for tok in ("의", "연구"):
                _, total = api.search(tok, qnames(pj, qname.get(pj))[0], py, exact_year=True)
                if total > 0:
                    sweep_ok = True
                    break
            print(f"  · 학술지·연도별 훑기: {'사용' if sweep_ok else '불가 — 논문별 검색만 사용'} (시험: {pj} {py})")
        jy = sorted({(ours, year_of(r)) for ours, r in todo if year_of(r)}, key=lambda x: (-x[1], x[0]))
        jpool = {}
        for ours, y in (jy if sweep_ok else []):
            k = f"{ours}|{y}"
            if (swept.get(k) or {}).get("d", "") > cutoff:
                continue
            pool = jpool.setdefault(ours, {})
            n_year = 0
            names = [qname[ours]] if ours in qname else qnames(ours)
            for tok in SWEEP_TOKENS["en" if ours == "IJBTC" else "ko"]:
                page = 1
                jq = names[0]
                while True:
                    recs, total = api.search(tok, jq, y, page=page, exact_year=True)
                    if total == 0 and page == 1 and len(names) > 1 and ours not in qname:
                        # 이 이름으로는 없음 → 다른 표기로 한 번씩
                        for alt in names[1:]:
                            recs, total = api.search(tok, alt, y, page=1, exact_year=True)
                            if total:
                                jq = alt; break
                    if total and ours not in qname:
                        qname[ours] = jq; names = [jq]
                    for c in recs:
                        if c.get("kci_id") and _ours(c.get("journal", "")) == ours:
                            n_year += c["kci_id"] not in pool
                            pool[c["kci_id"]] = c
                    if not recs or page * 100 >= total or page >= 5:
                        break
                    page += 1
            rs = [r for r in by_j[ours] if id(r) not in done and year_of(r) and abs(year_of(r) - y) <= 1]
            arts = [as_art(c, ours) for kid, c in pool.items() if kid not in used_k]
            m = match_journal(rs, arts)
            byid = {id(a): a for a in arts}
            for aid, r in m.items():
                if id(r) not in done:
                    accept(ours, r, byid[aid], "호 대조")
            swept[k] = {"d": today_kst(), "n": n_year}

        # ── 2단계: 남은 논문을 하나씩 검색 ─────────────────────────
        for ours, r in todo:
            if id(r) in done:
                continue
            y = year_of(r)
            jq = qnames(ours, qname.get(ours))[0]
            hit = None
            sw = [swept.get(f"{ours}|{yy}") for yy in (y - 1, y, y + 1)] if y else []
            # '그 해 KCI 에 없음'은 학술지명 조건이 맞는 것으로 확인된 학술지(qname)에만 적용
            if sweep_ok and ours in qname and sw and all(x and x.get("n", 1) == 0 for x in sw):
                # 그 해(±1년) KCI 에 이 학술지 논문이 한 편도 없음 → KCI 수록 이전 호. 묻지 않고 넘어감
                miss[r.get("article_id", "")] = today_kst(); tried_ids.add(id(r)); continue
            qs = query_titles(r["title_kr"], limit=2 if sweep_ok and swept.get(f"{ours}|{y}") else 4)
            for q in qs:
                recs, _ = api.search(q, jq, y)
                hit = pick(r, [c for c in recs if c.get("kci_id") not in used_k], ours)
                if hit:
                    break
            if not hit and qs:                       # 학술지명 표기가 다를 수 있어 학술지 조건 없이 한 번 더
                recs, _ = api.search(qs[0], "", y)
                hit = pick(r, [c for c in recs if c.get("kci_id") not in used_k], ours)
            tried += 1; tried_ids.add(id(r))
            if hit:
                accept(ours, r, hit, "제목 검색")
            else:
                miss[r.get("article_id", "")] = today_kst()
            if tried % 100 == 0:
                print(f"  … {tried}편 검색 · 연결 {sum(per_j.values())}편 · 호출 {api.calls}회")
    except Stop as e:
        stop_reason = str(e)
        print(f"  ⏹ 멈춤: {stop_reason} (다음 실행이 이어서)")

    # 2) 저장
    n_new = 0
    for ours, hits in found.items():
        kp, kd = stores[ours]
        arts = kd.setdefault("articles", [])
        idx = {a.get("kci_id"): a for a in arts if a.get("kci_id")}
        for h in hits:
            old = idx.get(h["kci_id"])
            if old is not None:                      # 이미 있던 KCI 레코드면 RISS 표시만 달아 줌
                old["riss_hint"] = h["riss_hint"]
                if old.get("link_only"):
                    old.clear(); old.update(h)
            else:
                arts.append(h); idx[h["kci_id"]] = h; n_new += 1
        if not args.dry_run:
            KCI_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_json(kp, kd)
    if not args.dry_run:
        state["updated"] = today_kst()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(STATE, state)

    linked = sum(per_j.values())
    left = sum(1 for _, r in todo if id(r) not in done and id(r) not in tried_ids)
    print(f"\n✅ KCI 번호 연결: {tried:,}편 확인 → {linked:,}편 연결 (호출 {api.calls}회, 남은 대상 {left:,}편)")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out and not args.dry_run:        # Actions 자동 반복(target=kcilink)용
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"remaining={left}\nprogress={'true' if (linked or tried) else 'false'}\n")
    if summ:
        L = [f"### KCI 논문번호 연결: {tried:,}편 확인 → **{linked:,}편 연결** (남은 대상 {left:,}편, API 호출 {api.calls}회)"]
        if per_j:
            L.append("- 학술지별: " + ", ".join(f"{k} {v}" for k, v in sorted(per_j.items(), key=lambda x: -x[1])))
        L.append(f"- 못 찾은 논문 누적 {len(miss):,}편 (KCI 수록 이전 호 등 — {RETRY_MISS_DAYS}일 뒤 다시 확인)")
        if stop_reason:
            L.append(f"- 멈춘 이유: {stop_reason} → 다음 실행에서 이어서")
        open(summ, "a", encoding="utf-8").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
