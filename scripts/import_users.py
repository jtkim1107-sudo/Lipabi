"""직원 명단 일괄 등록: CSV 한 장으로 계정을 만든다.

CSV 형식 (첫 줄은 헤더, 인코딩 UTF-8):
    이름,아이디,팀,역할,이메일
    김민수,minsu.kim,마케팅팀,직원,minsu@lipaco.co.kr
    박지영,jiyoung.park,국내BM팀,팀장,jiyoung@lipaco.co.kr

- 역할: 직원 / 팀장 / 관리자 (비우면 직원)
- 이메일: 비워도 됨 (브리핑 메일 발송용)
- 비밀번호는 자동 생성되어 결과 CSV(초기비밀번호 포함)로 저장됨
  → 각자에게 전달하고 첫 로그인 후 변경하도록 안내

사용법:
    python scripts/import_users.py 명단.csv
    → 결과: 명단_결과.csv (아이디/초기 비밀번호)
"""
import csv
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import auth, database  # noqa: E402

ROLE_MAP = {"관리자": "admin", "팀장": "leader", "직원": "member", "": "member"}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    if not src.exists():
        print(f"파일을 찾을 수 없습니다: {src}")
        return 1

    database.init_db()
    results = []
    created = skipped = 0

    with open(src, encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            name = (row.get("이름") or "").strip()
            username = (row.get("아이디") or "").strip().lower()
            team = (row.get("팀") or "").strip()
            role_kr = (row.get("역할") or "").strip()
            email = (row.get("이메일") or "").strip()

            if not name or not username:
                print(f"  {i}행 건너뜀: 이름/아이디 누락")
                skipped += 1
                continue
            if role_kr not in ROLE_MAP:
                print(f"  {i}행 건너뜀: 알 수 없는 역할 '{role_kr}' (직원/팀장/관리자)")
                skipped += 1
                continue
            if database.get_user_by_username(username):
                print(f"  {i}행 건너뜀: 아이디 '{username}' 이미 존재")
                skipped += 1
                continue

            password = secrets.token_urlsafe(8)
            database.create_user(
                username=username,
                name=name,
                password_hash=auth.hash_password(password),
                role=ROLE_MAP[role_kr],
                team=team,
                email=email,
            )
            results.append(
                {"이름": name, "아이디": username, "팀": team,
                 "역할": role_kr or "직원", "이메일": email, "초기비밀번호": password}
            )
            created += 1

    out = src.with_name(src.stem + "_결과.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["이름", "아이디", "팀", "역할", "이메일", "초기비밀번호"]
        )
        writer.writeheader()
        writer.writerows(results)

    print(f"\n완료: {created}명 생성, {skipped}건 건너뜀")
    print(f"초기 비밀번호 목록: {out}")
    print("⚠️ 이 파일은 각자에게 전달한 뒤 삭제하세요. 첫 로그인 후 비밀번호 변경을 안내하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
