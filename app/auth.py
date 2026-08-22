"""인증 유틸: 비밀번호 해시(PBKDF2), 세션 토큰, 로그인 시도 제한."""
import hashlib
import hmac
import secrets
import time

PBKDF2_ITERATIONS = 600_000  # OWASP 권장 수준 (PBKDF2-HMAC-SHA256)

# 로그인 시도 제한: 15분 안에 5회 실패 시 잠금
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_SEC = 15 * 60
_failed_attempts: dict[str, list[float]] = {}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def is_locked_out(key: str) -> bool:
    now = time.monotonic()
    attempts = [t for t in _failed_attempts.get(key, []) if now - t < ATTEMPT_WINDOW_SEC]
    _failed_attempts[key] = attempts
    return len(attempts) >= MAX_ATTEMPTS


def record_failure(key: str) -> None:
    _failed_attempts.setdefault(key, []).append(time.monotonic())


def clear_failures(key: str) -> None:
    _failed_attempts.pop(key, None)
