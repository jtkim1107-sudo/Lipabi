"""Power BI REST API 연동 (서비스 주체 인증 + DAX executeQueries).

설정 없이 체험할 수 있도록 POWERBI_MOCK=true 이면 샘플 데이터를 반환한다.
"""
import json
import logging
import random
from datetime import date, timedelta

import msal
import requests

from . import config

logger = logging.getLogger(__name__)

AUTHORITY = "https://login.microsoftonline.com/{tenant}"
SCOPE = ["https://analysis.windows.net/powerbi/api/.default"]
API_BASE = "https://api.powerbi.com/v1.0/myorg"


class PowerBIError(Exception):
    pass


def _get_token() -> str:
    if not (config.POWERBI_TENANT_ID and config.POWERBI_CLIENT_ID and config.POWERBI_CLIENT_SECRET):
        raise PowerBIError(
            "Power BI 인증 정보가 없습니다. .env에 POWERBI_TENANT_ID / POWERBI_CLIENT_ID / "
            "POWERBI_CLIENT_SECRET을 설정하거나 POWERBI_MOCK=true로 샘플 데이터를 사용하세요."
        )
    app = msal.ConfidentialClientApplication(
        config.POWERBI_CLIENT_ID,
        authority=AUTHORITY.format(tenant=config.POWERBI_TENANT_ID),
        client_credential=config.POWERBI_CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=SCOPE)
    if "access_token" not in result:
        raise PowerBIError(f"토큰 발급 실패: {result.get('error_description', result)}")
    return result["access_token"]


def execute_dax(dataset_id: str, dax: str, workspace_id: str = "") -> list[dict]:
    """DAX 쿼리를 실행해 행 목록을 반환한다."""
    token = _get_token()
    if workspace_id:
        url = f"{API_BASE}/groups/{workspace_id}/datasets/{dataset_id}/executeQueries"
    else:
        url = f"{API_BASE}/datasets/{dataset_id}/executeQueries"
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}},
        timeout=120,
    )
    if resp.status_code != 200:
        raise PowerBIError(f"executeQueries 실패 ({resp.status_code}): {resp.text[:500]}")
    body = resp.json()
    try:
        return body["results"][0]["tables"][0]["rows"]
    except (KeyError, IndexError) as e:
        raise PowerBIError(f"응답 형식이 예상과 다릅니다: {e}") from e


def _mock_rows(query_name: str) -> list[dict]:
    """데모용 샘플 데이터 (리파코 브랜드 구조)."""
    rng = random.Random(query_name)
    if "브랜드" in query_name:
        rows = []
        for brand, base, margin in (
            ("아가드", 4200, 0.34), ("베이비스탠다드", 2600, 0.29),
            ("모모빈", 1500, 0.31), ("다이소 PB", 3100, 0.18),
        ):
            sales = (base + rng.randint(-400, 400)) * 10000
            rate = round(margin + rng.uniform(-0.04, 0.03), 3)
            rows.append({
                "브랜드": brand, "매출": sales,
                "공헌이익": int(sales * rate), "공헌이익률": rate,
            })
        return rows
    if "채널" in query_name:
        return [
            {"채널": ch, "매출": (base + rng.randint(-300, 300)) * 10000}
            for ch, base in (
                ("자사몰", 1800), ("쿠팡", 3200), ("네이버", 1400),
                ("오픈마켓 기타", 700), ("다이소", 3100), ("해외 B2C", 1100),
            )
        ]
    if "SKU" in query_name or "품절" in query_name:
        skus = [
            ("AG-CARSEAT-01", "아가드", 210, 3), ("BS-BOTTLE-330", "베이비스탠다드", 480, 12),
            ("MM-BIN-L", "모모빈", 350, 25), ("DS-WIPES-80", "다이소 PB", 1900, 6),
            ("AG-STROLLER-X", "아가드", 90, 18), ("BS-PACIFIER-2P", "베이비스탠다드", 620, 4),
        ]
        return [
            {"SKU": s, "브랜드": b, "판매량": q + rng.randint(-30, 30), "재고잔여일": d}
            for s, b, q, d in skus
        ]
    today = date.today()
    rows = []
    base = rng.randint(9000, 11000)
    for i in range(30, 0, -1):
        d = today - timedelta(days=i)
        drift = rng.randint(-1500, 1600) + (700 if d.weekday() >= 5 else 0)
        rows.append({"날짜": d.isoformat(), "매출": max(1000, base + drift) * 10000})
    return rows


def load_queries() -> list[dict]:
    with open(config.QUERIES_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("queries", [])


def fetch_all() -> tuple[list[dict], list[str]]:
    """queries.json의 모든 쿼리를 실행한다.

    Returns:
        (결과 목록 [{name, description, rows}], 오류 메모 목록)
    """
    results: list[dict] = []
    errors: list[str] = []
    for q in load_queries():
        name = q.get("name", "이름 없음")
        try:
            if config.POWERBI_MOCK:
                rows = _mock_rows(name)
            else:
                rows = execute_dax(q["dataset_id"], q["dax"], q.get("workspace_id", ""))
            results.append(
                {
                    "name": name,
                    "description": q.get("description", ""),
                    "rows": rows[: config.MAX_ROWS_PER_QUERY],
                }
            )
        except Exception as e:
            logger.exception("쿼리 '%s' 실행 실패", name)
            errors.append(f"'{name}' 데이터 수집 실패: {e}")
    return results, errors
