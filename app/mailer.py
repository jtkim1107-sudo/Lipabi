"""이메일 발송: 아침 브리핑과 저녁 업무 보고를 메일함으로 배달한다.

SMTP 설정(.env)만 있으면 동작하고, 없으면 조용히 건너뛴다.
Gmail(앱 비밀번호), 네이버웍스, 회사 SMTP 모두 사용 가능.
"""
import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(config.SMTP_HOST and config.SMTP_FROM)


def send(to: str, subject: str, body_html: str) -> bool:
    """메일 한 통을 보낸다. 실패해도 예외를 올리지 않고 False를 반환."""
    if not enabled() or not to:
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config.SMTP_FROM
        msg["To"] = to
        msg.attach(MIMEText(body_html, "html", "utf-8"))
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            if config.SMTP_TLS:
                server.starttls()
            if config.SMTP_USER:
                server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_FROM, [to], msg.as_string())
        return True
    except Exception:
        logger.exception("메일 발송 실패 (%s)", to)
        return False


# ── 템플릿 ──────────────────────────────────────────────

_STYLE_BODY = "font-family:'Apple SD Gothic Neo','Malgun Gothic',sans-serif;color:#1a1a1a;line-height:1.6;max-width:600px;margin:0 auto;padding:20px"
_STYLE_BOX = "border:1px solid #e8e8e8;border-radius:8px;padding:14px 16px;margin:10px 0"
_STYLE_MUTED = "color:#8a8f98;font-size:13px"


def _esc(s: str) -> str:
    return html.escape(s or "")


def _footer() -> str:
    return (
        f'<p style="{_STYLE_MUTED};margin-top:24px">'
        f'<a href="{config.BASE_URL}" style="color:#1a1a1a">칸반보드 열기 →</a><br>'
        f"Lipabi — Power BI AI 분석 칸반보드</p>"
    )


def briefing_html(user_name: str, briefing: dict) -> str:
    items = ""
    for i, f in enumerate(briefing.get("focus", []), 1):
        assignee = (
            f' <span style="color:#30a46c;font-size:12px">추천 담당: {_esc(f.get("suggested_assignee"))}</span>'
            if f.get("suggested_assignee") else ""
        )
        items += (
            f'<div style="{_STYLE_BOX}"><b>{i}. {_esc(f.get("title"))}</b>{assignee}'
            f'<div style="{_STYLE_MUTED}">{_esc(f.get("reason"))}</div></div>'
        )
    tip = (
        f'<p style="background:#fafafa;border-radius:6px;padding:10px 14px;font-size:13px">💡 {_esc(briefing.get("tip"))}</p>'
        if briefing.get("tip") else ""
    )
    return (
        f'<div style="{_STYLE_BODY}">'
        f'<h2 style="font-size:17px">☀️ {_esc(user_name)}님의 오늘 브리핑</h2>'
        f"<p>{_esc(briefing.get('headline'))}</p>"
        f"{items}{tip}{_footer()}</div>"
    )


def work_report_html(report: dict) -> str:
    def ul(items, color="#1a1a1a"):
        lis = "".join(f'<li style="color:{color};margin:4px 0">{_esc(i)}</li>' for i in items)
        return f'<ul style="padding-left:20px">{lis}</ul>'

    sections = f"<p>{_esc(report.get('summary'))}</p>"
    if report.get("highlights"):
        sections += "<h3 style='font-size:14px'>주요 진전</h3>" + ul(report["highlights"])
    if report.get("blockers"):
        sections += "<h3 style='font-size:14px;color:#e5484d'>⛔ 막힘 / 조치 필요</h3>" + ul(
            report["blockers"], color="#e5484d"
        )
    for p in report.get("by_person", []):
        sections += f'<div style="{_STYLE_BOX}"><b>{_esc(p.get("name"))}</b>' + ul(p.get("items", [])) + "</div>"
    return (
        f'<div style="{_STYLE_BODY}">'
        f'<h2 style="font-size:17px">📋 일일 업무 보고 — {_esc(report.get("run_date"))}</h2>'
        f"{sections}{_footer()}</div>"
    )


def send_briefing(user: dict, briefing: dict) -> bool:
    if not user.get("email"):
        return False
    return send(
        user["email"],
        f"[Lipabi] {briefing.get('run_date', '')} 오늘 브리핑 — {user['name']}님",
        briefing_html(user["name"], briefing),
    )


def retro_html(retro: dict) -> str:
    def ul(items, color="#1a1a1a"):
        lis = "".join(f'<li style="color:{color};margin:4px 0">{_esc(i)}</li>' for i in items)
        return f'<ul style="padding-left:20px">{lis}</ul>'

    sections = f"<p>{_esc(retro.get('summary'))}</p>"
    if retro.get("improved"):
        sections += "<h3 style='font-size:14px;color:#30a46c'>나아진 것</h3>" + ul(retro["improved"])
    if retro.get("worsened"):
        sections += "<h3 style='font-size:14px;color:#e5484d'>나빠진 것 / 미해결</h3>" + ul(
            retro["worsened"], color="#e5484d"
        )
    if retro.get("recurring"):
        sections += "<h3 style='font-size:14px'>반복되는 문제</h3>" + ul(retro["recurring"])
    if retro.get("priorities"):
        sections += "<h3 style='font-size:14px'>다음 주 우선순위</h3>" + ul(retro["priorities"])
    return (
        f'<div style="{_STYLE_BODY}">'
        f'<h2 style="font-size:17px">🔁 주간 개선 회고 — {_esc(retro.get("week_of"))}</h2>'
        f"{sections}{_footer()}</div>"
    )


def send_retro(recipients: list[dict], retro: dict) -> int:
    sent = 0
    for u in recipients:
        if u.get("email") and send(
            u["email"],
            f"[Lipabi] 주간 개선 회고 ({retro.get('week_of', '')})",
            retro_html(retro),
        ):
            sent += 1
    return sent


def send_work_report(recipients: list[dict], report: dict) -> int:
    sent = 0
    for u in recipients:
        if u.get("email") and send(
            u["email"],
            f"[Lipabi] {report.get('run_date', '')} 일일 업무 보고",
            work_report_html(report),
        ):
            sent += 1
    return sent
