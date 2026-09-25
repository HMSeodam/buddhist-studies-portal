#!/usr/bin/env python3
# update_log.py — 공지사항(업데이트 소식) 기록 모듈
#
# 수집기(riss_updater / ibk_updater / kci_oai_updater)가 새 논문을 추가할 때마다
# output/updates.json 에 한 줄씩 기록한다. 웹 앱은 이 파일을 읽어
# 우측 상단 '공지' 패널에 "2026-09-26 동아시아불교문화 76호 20편 업데이트" 형태로 보여 주고,
# 항목을 누르면 해당 학술지·호수로 바로 이동한다.
#
# 파일 형식:
# {
#   "notices": [                      # 사람이 직접 쓰는 공지 (선택)
#     {"date":"2026-09-26", "text":"KCI 연동 시험 중입니다.", "pinned":true}
#   ],
#   "updates": [                      # 수집기가 자동으로 쓰는 업데이트 기록 (최신이 앞)
#     {"date":"2026-09-26", "journal":"동아시아불교문화", "year":"2026",
#      "volume":"", "issue":"76", "count":20, "source":"RISS"}
#   ]
# }

import json, os, tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
MAX_UPDATES = 300          # 파일이 끝없이 커지지 않도록 최근 300건만 보관


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _path(output_dir: str) -> Path:
    return Path(output_dir) / "updates.json"


def load_log(output_dir: str) -> dict:
    p = _path(output_dir)
    if not p.exists():
        return {"notices": [], "updates": []}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("notices", [])
        d.setdefault("updates", [])
        return d
    except Exception as e:
        print(f"  ⚠ updates.json 읽기 실패 → 새로 만듦: {e}")
        return {"notices": [], "updates": []}


def atomic_write_json(path: Path, data, indent=2):
    """중간에 프로세스가 죽어도 JSON 이 반쯤 쓰인 채 남지 않도록 임시파일→교체."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def record_update(output_dir: str, journal: str, count: int, *, volume="", issue="",
                  year="", source="RISS", date=None):
    """같은 날·같은 학술지·같은 호수 기록이 이미 있으면 편수를 더하고, 없으면 새로 추가."""
    if not count:
        return
    date = date or today_kst()
    log = load_log(output_dir)
    ups = log["updates"]
    key = (date, journal, str(volume or ""), str(issue or ""))
    for u in ups:
        if (u.get("date"), u.get("journal"), str(u.get("volume") or ""), str(u.get("issue") or "")) == key:
            u["count"] = int(u.get("count", 0)) + int(count)
            if source and source not in (u.get("source") or ""):
                u["source"] = f"{u.get('source')}+{source}" if u.get("source") else source
            break
    else:
        ups.insert(0, {
            "date": date, "journal": journal, "year": str(year or ""),
            "volume": str(volume or ""), "issue": str(issue or ""),
            "count": int(count), "source": source,
        })
    ups.sort(key=lambda u: u.get("date", ""), reverse=True)
    log["updates"] = ups[:MAX_UPDATES]
    atomic_write_json(_path(output_dir), log)
