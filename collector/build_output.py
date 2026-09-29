#!/usr/bin/env python3
# build_output.py — 출처별 원본(data/riss, data/kci)을 합쳐 앱이 읽는 output/ 을 새로 만든다
#
# ■ 왜 이렇게 바꿨나 (2026-09 구조 개편)
#   예전에는 RISS 수집기와 KCI 수집기가 같은 output 파일에 번갈아 써 넣고,
#   겹친 것을 나중에 찾아 지우는 방식이었다. 그러면
#     · 어느 쪽이 먼저 오느냐(KCI 가 먼저, RISS 가 나중)에 따라 결과가 달라지고
#     · 한 번 잘못 합치거나 못 합친 것이 파일에 계속 남아 누적된다.
#   이제는
#     1) 각 수집기는 자기 원본만 쌓는다   data/riss/riss_<학술지>.json · data/kci/kci_<학술지>.json
#     2) 이 스크립트가 매번 두 원본을 처음부터 대조해 output/riss_<학술지>.json 을 다시 만든다
#   → 순서와 상관없이 항상 같은 결과가 나오고, 대조 규칙을 고치면 과거 데이터에도 즉시 반영된다.
#
# ■ 합치는 규칙 (collector/paper_match.py)
#   같은 학술지 · 같은 호(권/호 숫자가 같고 연도 ±1) 안에서
#   한자를 한글 독음으로 바꾼 제목 유사도 + 시작 쪽수 근접도로 1:1 짝짓기.
#   짝이 되면 RISS 레코드를 기준으로 삼고 KCI 의 논문번호·DOI·영문 초록 등 빈칸만 채운다.
#   KCI 에만 있는 논문은 같은 호의 RISS 표기(권/호·연도)에 맞춰 넣는다.
#
# ■ 공지(updates.json)
#   이전 output 과 비교해 '어느 출처의 번호로도 처음 보는 논문'만 새 논문으로 친다.
#   (KCI 로 먼저 들어온 논문이 나중에 RISS 에도 올라와도 공지가 두 번 나가지 않음)
#   오래된 호를 뒤늦게 채운 것(발행 2년 이전)은 공지하지 않는다.
#
# 사용법:  python build_output.py            # 빌드
#          python build_output.py --check    # 저장하지 않고 대조 결과만 출력

import argparse, json, os, re, sys, collections
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from update_log import record_update, load_log, atomic_write_json, today_kst   # noqa: E402
from paper_match import match_journal, nums, year_of                          # noqa: E402

OUT_DIR  = Path("../output")
RISS_DIR = Path("../data/riss")
KCI_DIR  = Path("../data/kci")
CAND_FILE = KCI_DIR / "_candidates.json"      # 이름이 다른 KCI 학술지 후보 (제목 대조로 검증)
REPORT_FILE = Path("state/journal_check.json")

KCI_ART = "https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId={}"
FILL = ["title_en", "abstract_kr", "abstract_en", "doi", "start_page", "end_page", "keywords_kr", "keywords_en", "year"]
NOTICE_MIN_AGE = 1          # 올해·작년 발행분만 공지


def load_arts(path: Path):
    if not path.exists():
        return {}, []
    d = json.load(open(path, encoding="utf-8"))
    if isinstance(d, list):
        return {}, d
    return d.get("info", {}), d.get("articles", [])


def is_recommended(a):
    return "/recommender/" in (a.get("riss_url") or "")


def kci_id_of(a):
    if a.get("kci_id"):
        return a["kci_id"]
    m = re.search(r"ART\d{6,}", a.get("kci_url") or "")
    return m.group() if m else ""


def real_kci_url(a):
    kid = kci_id_of(a)
    return KCI_ART.format(kid) if kid else ""


# ════════════════════════════════════════
#  1회용: 예전 output(RISS+KCI 섞임) → 출처별 원본으로 나누기
# ════════════════════════════════════════
def migrate():
    RISS_DIR.mkdir(parents=True, exist_ok=True)
    KCI_DIR.mkdir(parents=True, exist_ok=True)
    n_r = n_k = n_l = 0
    for p in sorted(OUT_DIR.glob("riss_*.json")):
        info, arts = load_arts(p)
        riss = [a for a in arts if a.get("source") != "KCI" and not is_recommended(a)]
        kci = [a for a in arts if a.get("source") == "KCI"]
        links = []
        for a in riss:
            kid = kci_id_of(a)
            if kid:
                links.append({"kci_id": kid, "riss_hint": a.get("article_id"), "link_only": True, "source": "KCI",
                              "year": a.get("year", ""), "volume": a.get("volume", ""), "issue": a.get("issue", "")})
        name = p.stem[5:]
        atomic_write_json(RISS_DIR / p.name, {"info": info, "articles": riss})
        if kci or links:
            atomic_write_json(KCI_DIR / f"kci_{name}.json", {"articles": kci + links})
        n_r += len(riss); n_k += len(kci); n_l += len(links)
    # 예전 KCI 수집분이 만든 공지는 대부분 옛 호의 중복이라 정리
    log = load_log(str(OUT_DIR))
    before = len(log["updates"])
    log["updates"] = [u for u in log["updates"] if "KCI" not in (u.get("source") or "")]
    atomic_write_json(OUT_DIR / "updates.json", log)
    print(f"▶ 원본 분리 완료: RISS {n_r}편 · KCI {n_k}편 · KCI 연결 {n_l}건 · 공지 {before}→{len(log['updates'])}건")


# ════════════════════════════════════════
#  권/호 표기 맞추기
# ════════════════════════════════════════
def learn_scheme(pairs, riss):
    """짝지은 쌍에서 KCI 숫자를 RISS 의 어느 칸에 두는지 학습. 없으면 RISS 다수 표기."""
    c = collections.Counter()
    for r, k in pairs:
        rv, ri = str(r.get("volume") or "").strip("0") and r.get("volume"), str(r.get("issue") or "").strip("0") and r.get("issue")
        kv, ki = str(k.get("volume") or "").strip("0") and k.get("volume"), str(k.get("issue") or "").strip("0") and k.get("issue")
        if ki and not kv and rv and not ri: c["i2v"] += 1
        elif kv and not ki and ri and not rv: c["v2i"] += 1
        else: c["same"] += 1
    if c:
        return c.most_common(1)[0][0]
    recent = [a for a in riss if year_of(a) >= datetime.now().year - 10] or riss
    vo = sum(1 for a in recent if str(a.get("volume") or "").strip("0") and not str(a.get("issue") or "").strip("0"))
    io = sum(1 for a in recent if str(a.get("issue") or "").strip("0") and not str(a.get("volume") or "").strip("0"))
    return "i2v" if vo > io * 2 else ("v2i" if io > vo * 2 else "same")


def canon_numbers(k, riss_by_nums, scheme):
    """KCI 에만 있는 논문의 권/호/연도를 같은 호의 RISS 표기에 맞춤."""
    rec = dict(k)
    key = nums(k)
    if key:
        same = [r for r in riss_by_nums.get(key, [])
                if not year_of(k) or not year_of(r) or abs(year_of(r) - year_of(k)) <= 1]
        if same:
            vi = collections.Counter((r.get("volume", ""), r.get("issue", ""), r.get("year", "")) for r in same).most_common(1)[0][0]
            rec["volume"], rec["issue"], rec["year"] = vi
            return rec
    v, i = str(k.get("volume") or ""), str(k.get("issue") or "")
    v = "" if v.strip("0") == "" else v
    i = "" if i.strip("0") == "" else i
    if scheme == "i2v" and i and not v:
        v, i = i, ""
    elif scheme == "v2i" and v and not i:
        v, i = "", v
    rec["volume"], rec["issue"] = v, i
    return rec


# ════════════════════════════════════════
#  합치기
# ════════════════════════════════════════
def merge_pair(r, k):
    rec = dict(r)
    for f in FILL:
        if k.get(f) and not rec.get(f):
            rec[f] = k[f]
    kid = k.get("kci_id") or kci_id_of(r)
    if kid:
        rec["kci_id"] = kid
    kmap = {x.get("name"): x.get("affiliation", "") for x in (k.get("authors") or [])}
    if rec.get("authors"):
        rec["authors"] = [dict(a, affiliation=a.get("affiliation") or kmap.get(a.get("name"), "")) for a in rec["authors"]]
    elif k.get("authors"):
        rec["authors"] = k["authors"]
    if not rec.get("title_kr") and k.get("title_kr"):
        rec["title_kr"] = k["title_kr"]
    return rec


_FRONT = re.compile(r"^(?:목차|차례|目次|편집후기|編輯後記|간행사|발간사|권두언|卷頭言|투고규정|논문투고|연구윤리|학회소식|회칙|"
                    r"편집위원|편집규정|표지|판권|contents|editorial)", re.I)


def is_front_matter(k, name):
    """논문이 아닌 KCI 레코드(목차·편집후기, '佛敎硏究26' 처럼 학술지 이름+호수만 있는 것)."""
    t = (k.get("title_kr") or "").strip()
    if not t or _FRONT.match(t):
        return True
    # KCI 쪽 시험용 레코드 (예: IJBTC 'The essence' — 저자 'asdf')
    names = {(a.get("name") or "").strip().lower() for a in (k.get("authors") or [])}
    if names & {"asdf", "test", "테스트", "qwer", "aaa"} or t.lower() in ("test", "테스트"):
        return True
    from paper_match import mtitle as _mt
    strip = lambda x: re.sub(r"(?:제|vol|no)?\d+(?:집|호|권)?", "", _mt(x))
    core = strip(t)
    return bool(core) and core == strip(name)


def build_journal(name, riss, kci, extra_kci=()):
    riss = [a for a in riss if not is_recommended(a)]
    kci_all = [k for k in list(kci) + list(extra_kci)]
    pairs_map = match_journal(riss, kci_all)
    pairs = [(pairs_map[id(k)], k) for k in kci_all if id(k) in pairs_map]
    scheme = learn_scheme([(r, k) for r, k in pairs if not k.get("link_only")], riss)
    by_r = {}
    for r, k in pairs:
        by_r.setdefault(id(r), []).append(k)
    out = []
    for r in riss:
        rec = dict(r)
        for f in ("title_kr", "title_en"):          # RISS 제목 안의 줄바꿈·탭 정리 ('Double Tragedy\n\t\t : …')
            if rec.get(f):
                rec[f] = re.sub(r"\s+", " ", rec[f]).replace(" :", ":").strip()
        ks = by_r.get(id(r), [])
        for k in sorted(ks, key=lambda x: bool(x.get("link_only"))):
            rec = merge_pair(rec, k)
        if not rec.get("kci_id") and kci_id_of(r):
            rec["kci_id"] = kci_id_of(r)
        rec["kci_url"] = real_kci_url(rec)
        rec["source"] = "RISS"
        rec["sources"] = ["RISS", "KCI"] if rec.get("kci_id") else ["RISS"]
        rec["riss_id"] = r.get("article_id", "")
        out.append(rec)
    riss_by_nums = collections.defaultdict(list)
    for r in riss:
        if nums(r):
            riss_by_nums[nums(r)].append(r)
    # ── 학술지 대응 검증 (제목 대조) ──
    # KCI 에 같은 이름의 다른 학술지가 있으면 제목이 우리 RISS 목록과 맞지 않는다.
    # 발행기관별로, 같은 호가 RISS 에도 있는 KCI 레코드 중 짝이 된 비율을 본다.
    pub_cmp, pub_ok = collections.Counter(), collections.Counter()
    for k in kci_all:
        if k.get("link_only") or not nums(k) or not riss_by_nums.get(nums(k)):
            continue
        pub = k.get("publisher") or "?"
        pub_cmp[pub] += 1
        if id(k) in pairs_map:
            pub_ok[pub] += 1
    bad_pubs = {p for p, n in pub_cmp.items() if n >= 3 and pub_ok[p] / n < 0.5}
    trusted = len(pairs) >= 3       # 짝이 3건 이상 확인된 학술지만 KCI 단독 논문을 받아들임
    kci_only = held = 0
    for k in kci_all:
        if id(k) in pairs_map or k.get("link_only"):
            continue
        if is_front_matter(k, name):
            continue
        if not trusted or (k.get("publisher") or "?") in bad_pubs:
            held += 1           # 검증될 때까지 보류 (원본에는 남아 있음)
            continue
        rec = canon_numbers(k, riss_by_nums, scheme)
        rec["kci_url"] = real_kci_url(rec)
        rec["source"] = "KCI"
        rec["sources"] = ["KCI"]
        rec["riss_id"] = ""
        rec["journal_name"] = name
        out.append(rec)
        kci_only += 1
    # 대조 검증 통계: KCI 레코드 중 RISS 에 같은 호가 있는 것들의 짝 비율 (학술지 대응이 맞는지)
    real_k = [k for k in kci_all if not k.get("link_only")]
    comparable = [k for k in real_k if nums(k) and riss_by_nums.get(nums(k))]
    matched_c = sum(1 for k in comparable if id(k) in pairs_map)
    stats = {"riss": len(riss), "kci": len(real_k), "kci_links": len(kci_all) - len(real_k),
             "paired": sum(1 for k in real_k if id(k) in pairs_map), "kci_only": kci_only, "scheme": scheme,
             "held": held, "bad_publishers": sorted(bad_pubs),
             "comparable": len(comparable), "comparable_matched": matched_c,
             "match_rate": round(matched_c / len(comparable), 3) if comparable else None}
    return out, stats


def sort_key(a):
    try: v = int(re.search(r"\d+", str(a.get("volume") or "0")).group())
    except Exception: v = 0
    try: i = int(re.search(r"\d+", str(a.get("issue") or "0")).group())
    except Exception: i = 0
    try: p = int(re.search(r"\d+", str(a.get("start_page") or "0")).group())
    except Exception: p = 0
    return (year_of(a), v, i, p)


def verify_candidates(riss_by_journal):
    """이름이 다른 KCI 학술지 후보를 우리 학술지들과 제목 대조 → 같은 학술지면 자동 연결."""
    if not CAND_FILE.exists():
        return {}, []
    cands = json.load(open(CAND_FILE, encoding="utf-8"))
    accepted, report = {}, []
    for cname, recs in cands.items():
        if len(recs) < 3:
            continue
        best, rate, cnt = None, 0.0, 0
        for j, riss in riss_by_journal.items():
            m = match_journal(riss, recs)
            r = len(m) / len(recs)
            if r > rate:
                best, rate, cnt = j, r, len(m)
        ok = best is not None and len(recs) >= 5 and rate >= 0.6
        report.append({"kci_name": cname, "records": len(recs), "best": best, "matched": cnt,
                       "rate": round(rate, 2), "accepted": ok})
        if ok:
            accepted.setdefault(best, []).extend(recs)
    return accepted, report


def build(check=False):
    if not RISS_DIR.exists():
        if check:
            print("data/riss 가 없어 --check 를 할 수 없습니다 (먼저 빌드 1회 필요)"); return
        migrate()
    prev_primary, prev_ids = {}, set()
    for p in OUT_DIR.glob("riss_*.json"):
        _, arts = load_arts(p)
        for a in arts:
            prim = a.get("id") or a.get("article_id")
            for k in (a.get("article_id"), a.get("id"), a.get("riss_id"), a.get("kci_id"), kci_id_of(a)):
                if k:
                    prev_ids.add(k); prev_primary.setdefault(k, prim)
    riss_files = {p.stem[5:]: p for p in RISS_DIR.glob("riss_*.json")}
    riss_by_journal = {j: [a for a in load_arts(p)[1] if not is_recommended(a)] for j, p in riss_files.items()}
    accepted, cand_report = verify_candidates(riss_by_journal)

    cy = datetime.now().year
    all_stats, new_total = {}, 0
    for j, p in sorted(riss_files.items()):
        info, _ = load_arts(p)
        riss = riss_by_journal[j]
        _, kci = load_arts(KCI_DIR / f"kci_{j}.json")
        out, st = build_journal(j, riss, kci, accepted.get(j, []))
        # 대표 번호(id) 유지: 이전에 쓰던 번호가 있으면 그대로 (공유된 링크가 깨지지 않게)
        new_recs = []
        for rec in out:
            ids = [x for x in (rec.get("riss_id"), rec.get("kci_id")) if x]
            prim = next((prev_primary[x] for x in ids if x in prev_primary), None) or (ids[0] if ids else rec.get("id"))
            rec["id"] = rec["article_id"] = prim
            if not any(x in prev_ids for x in ids):
                new_recs.append(rec)
        out.sort(key=sort_key)
        st["new"] = len(new_recs)
        all_stats[j] = st
        if check:
            continue
        old_info, old_arts = load_arts(OUT_DIR / p.name)
        info = dict(old_info or {}, **info)
        if new_recs:
            info["last_updated"] = today_kst()
        before = json.dumps(old_arts, ensure_ascii=False, sort_keys=True)
        after = json.dumps(out, ensure_ascii=False, sort_keys=True)
        if before != after or not (OUT_DIR / p.name).exists():
            atomic_write_json(OUT_DIR / p.name, {"info": info, "articles": out})
        # 공지: 최근 발행분만, 호별로 묶어서
        groups = collections.Counter()
        srcs = {}
        for rec in new_recs:
            if year_of(rec) and year_of(rec) < cy - NOTICE_MIN_AGE:
                continue
            key = (rec.get("year", ""), rec.get("volume", ""), rec.get("issue", ""))
            groups[key] += 1
            srcs.setdefault(key, set()).update(rec.get("sources") or [rec.get("source", "RISS")])
        for (y, v, i), n in groups.items():
            record_update(str(OUT_DIR), j, n, volume=v, issue=i, year=y, source="+".join(sorted(srcs[(y, v, i)])))
        new_total += len(new_recs)

    # ── 보고 ──
    print(f"\n{'학술지':16}{'RISS':>6}{'KCI':>6}{'KCI연결':>7}{'짝':>6}{'KCI만':>6}{'대응률':>7}{'신규':>6}  표기")
    for j, st in all_stats.items():
        mr = f"{st['match_rate']*100:.0f}%" if st["match_rate"] is not None else "-"
        print(f"{j[:16]:16}{st['riss']:6}{st['kci']:6}{st['kci_links']:7}{st['paired']:6}{st['kci_only']:6}{mr:>7}{st['new']:6}  {st['scheme']}")
    warn = [j for j, st in all_stats.items() if (st["match_rate"] is not None and st["comparable"] >= 5 and st["match_rate"] < 0.5) or st["bad_publishers"]]
    held = {j: st["held"] for j, st in all_stats.items() if st["held"]}
    if held:
        print("⏸ 대응이 아직 확인되지 않아 보류한 KCI 단독 논문:", ", ".join(f"{j} {n}편" for j, n in held.items()))
    no_kci = [j for j, st in all_stats.items() if st["kci"] == 0 and st["kci_links"] == 0]
    if warn:
        print("\n⚠ KCI 학술지 대응 의심 (같은 호인데 제목이 절반 이상 안 맞음):", ", ".join(warn))
    if no_kci:
        print("ℹ KCI 에서 아직 논문을 받지 못한 학술지:", ", ".join(no_kci))
    for c in cand_report:
        print(f"🔎 KCI '{c['kci_name']}' {c['records']}건 → 가장 비슷한 학술지 {c['best']} ({c['matched']}건 일치, {c['rate']*100:.0f}%)"
              + (" → 자동 연결" if c["accepted"] else ""))
    if not check:
        REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(REPORT_FILE, {"updated": today_kst(), "journals": all_stats, "candidates": cand_report})
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ and not check:
        with open(summ, "a", encoding="utf-8") as f:
            f.write(f"### 출력 빌드: 새 논문 {new_total}편\n")
            f.write("| 학술지 | RISS | KCI | 짝 | KCI만 | 대응률 | 신규 |\n|---|---|---|---|---|---|---|\n")
            for j, st in all_stats.items():
                mr = f"{st['match_rate']*100:.0f}%" if st["match_rate"] is not None else "-"
                f.write(f"| {j} | {st['riss']} | {st['kci']} | {st['paired']} | {st['kci_only']} | {mr} | {st['new']} |\n")
            if warn:
                f.write(f"\n⚠ KCI 대응 의심: {', '.join(warn)}\n")
            if held:
                f.write("\n⏸ 대응 확인 전이라 보류한 KCI 단독 논문: " + ", ".join(f"{j} {n}편" for j, n in held.items()) + "\n")
            if no_kci:
                f.write(f"\nℹ KCI 에서 아직 못 받은 학술지: {', '.join(no_kci)}\n")
            for c in cand_report:
                if c["accepted"]:
                    f.write(f"\n🔎 KCI '{c['kci_name']}' {c['records']}건 → 우리 '{c['best']}'와 제목 {c['rate']*100:.0f}% 일치 → 자동 연결\n")
                else:
                    f.write(f"\n🔎 KCI '{c['kci_name']}' {c['records']}건 → 우리 학술지와 제목이 맞지 않아 연결하지 않음"
                            f" (이름만 비슷한 다른 학술지)\n")
    print(f"\n✅ 빌드 완료: 새 논문 {new_total}편")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    build(check=a.check)
