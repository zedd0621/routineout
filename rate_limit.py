"""로그인·본인조회 시도 횟수 제한 (Phase 0-4)

목적: 이름/생년월일/연락처 뒤 4자리처럼 사내에서 알 수 있는 정보만으로
      타인의 급여 정보를 반복 시도해 열람하는 것을 막는다.

SQLite 기반인 이유:
    PythonAnywhere는 워커 프로세스를 여러 개 띄운다. 파이썬 메모리(dict)에
    카운트를 두면 워커 수만큼 시도 횟수가 곱해져서 제한이 사실상 무력화된다.
    프로세스 간에 공유되는 저장소가 필요하므로 SQLite 파일을 쓴다.

정책:
    WINDOW_SECONDS 안에 MAX_ATTEMPTS 회 실패하면 LOCK_SECONDS 동안 잠근다.
    성공하면 즉시 초기화한다(오타 몇 번 낸 정상 사용자가 오래 묶이지 않도록).
"""

import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(__file__), 'rate_limit.db')

MAX_ATTEMPTS = 10       # 이 횟수만큼 실패하면 잠금
WINDOW_SECONDS = 600    # 실패 누적 집계 구간 (10분)
LOCK_SECONDS = 600      # 잠금 유지 시간 (10분)
PURGE_AFTER_SECONDS = 86400  # 하루 지난 기록은 정리


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS rate_limit (
            scope        TEXT NOT NULL,
            key          TEXT NOT NULL,
            fail_count   INTEGER NOT NULL DEFAULT 0,
            window_start REAL NOT NULL DEFAULT 0,
            locked_until REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (scope, key)
        )
    ''')
    conn.commit()
    conn.close()


def client_key(request, prefix=''):
    """요청자 식별자. PythonAnywhere는 프록시 뒤라 X-Forwarded-For를 먼저 본다."""
    fwd = request.headers.get('X-Forwarded-For', '')
    ip = fwd.split(',')[0].strip() if fwd else (request.remote_addr or 'unknown')
    return f'{prefix}:{ip}' if prefix else ip


def check(scope, key):
    """(허용여부, 남은잠금초) 반환. 허용이면 (True, 0)."""
    now = time.time()
    try:
        conn = _conn()
        row = conn.execute(
            'SELECT locked_until FROM rate_limit WHERE scope = ? AND key = ?',
            (scope, key)
        ).fetchone()
        conn.close()
    except sqlite3.Error:
        # 제한 저장소 장애가 서비스 자체를 막으면 안 되므로 통과시킨다.
        return True, 0

    if row and row['locked_until'] > now:
        return False, int(row['locked_until'] - now) + 1
    return True, 0


def record_failure(scope, key):
    """실패 1회 기록. 임계치에 도달하면 잠근다. 남은 잠금초를 반환(잠기지 않았으면 0)."""
    now = time.time()
    try:
        conn = _conn()
        row = conn.execute(
            'SELECT fail_count, window_start, locked_until FROM rate_limit '
            'WHERE scope = ? AND key = ?', (scope, key)
        ).fetchone()

        if row is None or (now - row['window_start']) > WINDOW_SECONDS:
            count, window_start = 1, now
        else:
            count, window_start = row['fail_count'] + 1, row['window_start']

        locked_until = now + LOCK_SECONDS if count >= MAX_ATTEMPTS else 0.0

        conn.execute(
            'INSERT INTO rate_limit (scope, key, fail_count, window_start, locked_until) '
            'VALUES (?, ?, ?, ?, ?) '
            'ON CONFLICT(scope, key) DO UPDATE SET '
            'fail_count = excluded.fail_count, window_start = excluded.window_start, '
            'locked_until = excluded.locked_until',
            (scope, key, count, window_start, locked_until)
        )
        conn.execute('DELETE FROM rate_limit WHERE window_start < ? AND locked_until < ?',
                     (now - PURGE_AFTER_SECONDS, now))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        return 0

    return int(locked_until - now) + 1 if locked_until else 0


def clear(scope, key):
    """인증 성공 시 누적 실패 초기화."""
    try:
        conn = _conn()
        conn.execute('DELETE FROM rate_limit WHERE scope = ? AND key = ?', (scope, key))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


def lock_message(seconds):
    minutes = max(1, (seconds + 59) // 60)
    return (f'인증 시도 횟수를 초과했습니다. 약 {minutes}분 후 다시 시도해 주세요. '
            f'본인 정보가 맞는데도 계속 실패한다면 담당자에게 문의해 주세요.')
