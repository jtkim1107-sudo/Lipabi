# Lipabi — Power BI 데이터 AI 아침 분석 & 팀 칸반보드

매일 아침 Power BI 데이터를 자동으로 불러와 AI(Claude)가 분석하고,
그 결과를 **업무 카드**로 만들어 직원들과 함께 칸반보드에서 관리하는 시스템입니다.

```
┌─────────────┐    매일 아침 07:00    ┌──────────────┐         ┌──────────────────────┐
│  Power BI    │ ──── DAX 쿼리 ────▶ │  Claude AI    │ ──────▶ │  칸반보드 (웹)         │
│  데이터셋     │                     │  분석/업무 제안 │         │  AI 제안→할 일→진행→완료 │
└─────────────┘                     └──────────────┘         └──────────────────────┘
```

- **자동 수집**: `config/queries.json`에 정의한 DAX 쿼리를 Power BI REST API로 실행
- **AI 분석**: 요약 + 인사이트 + "오늘 할 업무" 제안을 생성
- **칸반보드**: AI 제안이 카드로 자동 등록 → 직원들이 드래그해서 담당자 지정, 진행 관리
- **스케줄러 내장**: 서버만 켜두면 매일 아침 자동 실행 (기본 07:00 KST)

---

## 1. 빠른 시작 (Power BI 연동 없이 체험)

Power BI 설정 전에도 샘플 데이터로 전체 흐름을 바로 볼 수 있습니다.

```bash
# 1) 의존성 설치
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2) 환경 변수 설정
cp .env.example .env
# .env 파일을 열어 ANTHROPIC_API_KEY만 입력 (POWERBI_MOCK=true 유지)

# 3) 서버 실행
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://localhost:8000` 접속 → **[지금 분석 실행]** 버튼 클릭
→ 샘플 매출 데이터를 AI가 분석해 업무 카드가 생성됩니다.

> Claude API 키는 https://console.anthropic.com 에서 발급합니다.

---

## 2. Power BI 실제 연동

### 2-1. Azure AD 앱 등록 (서비스 주체)

1. [Azure Portal](https://portal.azure.com) → **Microsoft Entra ID** → **앱 등록** → **새 등록**
2. 등록 후 **개요**에서 다음 값을 복사해 `.env`에 입력
   - 애플리케이션(클라이언트) ID → `POWERBI_CLIENT_ID`
   - 디렉터리(테넌트) ID → `POWERBI_TENANT_ID`
3. **인증서 및 비밀** → **새 클라이언트 비밀** 생성 → 값 복사 → `POWERBI_CLIENT_SECRET`
4. Microsoft Entra ID에서 **보안 그룹**을 하나 만들고 이 앱을 멤버로 추가

### 2-2. Power BI 관리 설정

1. [Power BI 관리 포털](https://app.powerbi.com/admin-portal/tenantSettings) → **테넌트 설정**
   → **개발자 설정** → **서비스 주체가 Power BI API를 사용할 수 있음** 활성화
   → 위에서 만든 보안 그룹에 적용
2. 데이터가 있는 **작업 영역** → **액세스 관리** → 보안 그룹(또는 앱)을 **구성원** 이상으로 추가

### 2-3. 쿼리 정의

`config/queries.json`에 매일 불러올 데이터를 DAX로 정의합니다.

- `dataset_id`: Power BI에서 데이터셋을 열었을 때 URL의 `datasets/` 뒤 ID
- `workspace_id`: 작업 영역 URL의 `groups/` 뒤 ID (내 작업 영역이면 빈 문자열)
- `dax`: `EVALUATE`로 시작하는 DAX 쿼리 (Power BI Desktop의 DAX 쿼리 뷰에서 테스트 가능)

설정이 끝나면 `.env`에서 `POWERBI_MOCK=false`로 변경합니다.

---

## 3. 매일 아침 자동 실행

세 가지 방법 중 하나를 선택하세요.

| 방법 | 조건 | 설정 |
|------|------|------|
| **① 내장 스케줄러 (권장)** | 서버가 항상 켜져 있음 | 아무것도 안 해도 됨. `.env`의 `DAILY_RUN_TIME`(기본 07:00)에 자동 실행 |
| **② cron** | 리눅스 서버 | `0 7 * * * cd /path/to/Lipabi && venv/bin/python run_daily.py` |
| **③ GitHub Actions** | 서버가 외부에서 접속 가능 | 저장소 Secrets에 `KANBAN_API_URL`, `RUN_TOKEN` 등록 (`.github/workflows/daily-analysis.yml` 참고) |

---

## 4. 팀에서 함께 쓰기

- 서버를 사내망(또는 클라우드)에 띄우고 직원들에게 주소를 공유하면 됩니다.
- 보드는 15초마다 자동 새로고침되어 서로의 변경 사항이 반영됩니다.
- 카드 사용법:
  - **AI 제안** 열에 매일 아침 분석 결과 업무가 올라옵니다
  - 진행할 업무를 **할 일**로 드래그하고 카드의 담당자 칸에 이름 입력
  - 작업 상황에 따라 **진행 중 → 완료**로 이동
  - 필요 없는 제안은 ✕로 삭제, 수동 업무는 할 일 열의 ＋ 버튼으로 추가

---

## 5. 구조

```
Lipabi/
├── app/
│   ├── main.py        # FastAPI 서버 + 아침 스케줄러
│   ├── pipeline.py    # 수집 → 분석 → 업무 등록 파이프라인
│   ├── powerbi.py     # Power BI REST API (MSAL 인증, DAX 실행)
│   ├── analyzer.py    # Claude AI 분석 (구조화된 JSON 출력)
│   ├── database.py    # SQLite 저장소 (리포트, 업무)
│   ├── config.py      # 환경 변수 설정
│   └── static/        # 칸반보드 웹 UI
├── config/queries.json  # 매일 불러올 DAX 쿼리 정의
├── run_daily.py         # cron/수동 실행용 스크립트
└── .github/workflows/daily-analysis.yml  # (선택) GitHub Actions 스케줄
```

### 주요 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/tasks` | 업무 목록 |
| POST | `/api/tasks` | 업무 생성 |
| PATCH | `/api/tasks/{id}` | 상태/담당자 등 수정 |
| DELETE | `/api/tasks/{id}` | 업무 삭제 |
| GET | `/api/reports/latest` | 최신 AI 분석 리포트 |
| POST | `/api/run` | 분석 즉시 실행 (`X-Run-Token` 헤더) |
