#!/usr/bin/env python3
# merge_journals.py
# 개별 수집된 학술지 JSON을 papers.json에 병합
#
# 사용법: python merge_journals.py

import json, hashlib
from pathlib import Path
from datetime import datetime

OUTPUT_DIR  = "./output"
PAPERS_JSON = f"{OUTPUT_DIR}/papers.json"

# 병합할 파일 목록
MERGE_FILES = [
    "riss_IJBTC.json",
    "riss_종학연구.json",
    "riss_무형문화연구.json",
]

JOURNAL_INFO = {
    "IJBTC":    {"category":"불교학", "control_no":"b44e9e4716ca7ae7ffe0bdc3ef48d419"},
    "종학연구":  {"category":"불교학", "control_no":"d6dbf60f1a65bfc4ffe0bdc3ef48d419"},
    "무형문화연구":{"category":"불교학","control_no":"e92ddca29a0f1d20ffe0bdc3ef48d419"},
}

def _gid(a):
    return "R"+hashlib.md5(f"{a.get('title_kr','')}{a.get('year','')}".encode()).hexdigest()[:11]

def _pg(a):
    try: return int(a.get("start_page") or 0)
    except: return 0

def _by_year(arts):
    t={}
    for a in arts:
        y=a.get("year","미상"); v=a.get("volume",""); n=a.get("issue","미상")
        k=f"{v}권 {n}호" if v else f"{n}호"
        t.setdefault(y,{}).setdefault(k,[]).append(a)
    for y in t:
        for k in t[y]: t[y][k].sort(key=_pg)
    return t

def main():
    # papers.json 로드
    if not Path(PAPERS_JSON).exists():
        print(f"❌ {PAPERS_JSON} 없음")
        return

    with open(PAPERS_JSON, encoding="utf-8") as f:
        db = json.load(f)

    existing_ids = {a["id"] for a in db.get("index", [])}
    print(f"기존 papers.json: {len(existing_ids)}편")

    total_added = 0

    for fname in MERGE_FILES:
        fpath = Path(OUTPUT_DIR) / fname
        if not fpath.exists():
            print(f"⚠ {fname} 없음 → 건너뜀")
            continue

        with open(fpath, encoding="utf-8") as f:
            data = json.load(f)

        # 리스트 또는 dict 형태 모두 처리
        if isinstance(data, list):
            arts = data
            jname = arts[0]["journal_name"] if arts else fname.replace("riss_","").replace(".json","")
        else:
            arts  = data.get("articles", [])
            jname = data.get("info", {}).get("name", fname.replace("riss_","").replace(".json",""))

        if not arts:
            print(f"  [{jname}] 논문 없음")
            continue

        info = JOURNAL_INFO.get(jname, {"category":"불교학"})

        added = 0
        new_arts = []
        for art in arts:
            aid = art.get("article_id") or _gid(art)
            art["id"] = aid
            if aid in existing_ids:
                continue
            existing_ids.add(aid)
            new_arts.append(art)
            added += 1

        # index에 추가
        db["index"].extend(new_arts)

        # journals에 추가
        db.setdefault("journals", {})[jname] = {
            "info": {"name":jname, **info},
            "articles": arts,
            "by_year": _by_year(arts),
            "total_collected": len(arts),
        }

        total_added += added
        print(f"  [{jname}] {len(arts)}편 중 {added}편 신규 추가")

    # meta 업데이트
    db["meta"]["total_articles"] = len(db["index"])
    db["meta"]["last_updated"]   = datetime.now().strftime("%Y-%m-%d")
    db["meta"]["journals"]       = len(db.get("journals", {}))

    # 저장
    with open(PAPERS_JSON, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)

    sz = Path(PAPERS_JSON).stat().st_size / 1024 / 1024
    print(f"\n✅ 병합 완료!")
    print(f"   총 {len(db['index'])}편 ({sz:.1f}MB)")
    print(f"   신규 추가: {total_added}편")

if __name__ == "__main__":
    main()
