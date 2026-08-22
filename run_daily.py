"""일일 분석 파이프라인을 수동/cron으로 실행하는 스크립트.

사용법:
    python run_daily.py

crontab 예시 (매일 아침 7시):
    0 7 * * * cd /path/to/Lipabi && /path/to/venv/bin/python run_daily.py
"""
import json
import logging
import sys

from app.pipeline import run_daily_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main() -> int:
    try:
        result = run_daily_pipeline()
    except Exception as e:
        print(f"분석 실패: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
