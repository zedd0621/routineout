"""접근·조회 감사로그 (Phase 0-3)

목적(둘 다 중요):
  1) 개인정보보호법 제39조는 정보주체가 개인정보처리자에게 직접 손해배상을
     청구할 수 있게 하고, 처리자가 '고의·과실 없음'을 입증하지 못하면 면책되지
     않는다(입증책임 전환). 즉 사고가 나면 "우리 시스템 결함이 아니다"를
     routineout이 입증해야 한다. 그 증거가 이 로그다.
  2) 영업 문구 "누가 언제 무엇을 조회했는지 기록되므로 책임소재를 확인해
     드립니다"를 실제로 성립시킨다.

저장 위치:
  테넌트가 특정되는 사건 → tenant_data/<tenant>/data.db 의 audit_log 테이블
      (고객사별 데이터 격리 원칙을 로그에도 동일하게 적용)
  테넌트를 특정할 수 없는 사건(존재하지 않는 아이디로 로그인 시도 등)
      → PA/audit.db (전역)

원칙:
  - 로그 기록 실패가 본 기능을 막지 않는다(모든 예외를 삼킨다).
  - 로그는 추가만 하고 수정·삭제하지 않는다.
"""

import os
import sqlite3
from datetime import datetime

from tenant_db import db_path as tenant_db_path

GLOBAL_DB_PATH = os.path.join(os.path.dirname(__file__), 'audit.db')

SCHEMA = '''
    CREATE TABLE IF NOT EXISTS audit_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        occurred_at TEXT NOT NULL,
        event       TEXT NOT NULL,
        tenant      TEXT,
        actor       TEXT,
        target      TEXT,
        ip          TEXT,
        user_agent  TEXT,
        detail      TEXT
    )
'''

# ── 이벤트 종류 ──
LOGIN_SUCCESS = 'login.success'          # 고객사 계정 로그인 성공
LOGIN_FAIL = 'login.fail'                # 로그인 실패
LOGIN_LOCKED = 'login.locked'            # 시도 초과로 잠김
OWNER_LOGIN_SUCCESS = 'owner.login.success'
OWNER_LOGIN_FAIL = 'owner.login.fail'
PAYCHECK_VIEW = 'paycheck.view'          # 본인확인 통과 후 급여 조회
PAYCHECK_FAIL = 'paycheck.fail'          # 본인확인 실패
PAYCHECK_CONSENT = 'paycheck.consent'    # 급여 동의 제출
PAYSLIP_ISSUE = 'payslip.issue'          # 급여명세서 PDF 발급
PAYSLIP_FAIL = 'payslip.fail'
ADMIN_VIEW = 'admin.view'                # 관리자 화면 조회
ADMIN_EXPORT = 'admin.export'            # 관리자 데이터 내려받기 (대량 반출 경로)
ADMIN_MODIFY = 'admin.modify'            # 관리자 데이터 변경·삭제

EVENT_LABELS = {
    LOGIN_SUCCESS: '로그인 성공',
    LOGIN_FAIL: '로그인 실패',
    LOGIN_LOCKED: '시도초과 잠금',
    OWNER_LOGIN_SUCCESS: '대표 로그인 성공',
    OWNER_LOGIN_FAIL: '대표 로그인 실패',
    PAYCHECK_VIEW: '급여 조회',
    PAYCHECK_FAIL: '급여 조회 본인확인 실패',
    PAYCHECK_CONSENT: '급여 동의 제출',
    PAYSLIP_ISSUE: '명세서 발급',
    PAYSLIP_FAIL: '명세서 본인확인 실패',
    ADMIN_VIEW: '관리자 조회',
    ADMIN_EXPORT: '관리자 내려받기',
    ADMIN_MODIFY: '관리자 변경',
}

# 이 이벤트들은 조회가 아니라 반출/변경이라 별도로 강조해서 본다.
HIGH_RISK_EVENTS = {ADMIN_EXPORT, ADMIN_MODIFY, LOGIN_LOCKED}


def _init(path):
    conn = sqlite3.connect(path, timeout=5)
    conn.execute(SCHEMA)
    conn.execute('CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(occurred_at DESC)')
    conn.commit()
    conn.close()


def init_db(tenant=None):
    """tenant가 None이면 전역 로그 DB를, 아니면 해당 테넌트 DB를 준비한다."""
    _init(GLOBAL_DB_PATH if tenant is None else tenant_db_path(tenant))


def client_ip(request):
    """프록시(PythonAnywhere) 뒤이므로 X-Forwarded-For의 첫 IP가 실제 클라이언트."""
    fwd = request.headers.get('X-Forwarded-For', '')
    return fwd.split(',')[0].strip() if fwd else (request.remote_addr or '')


def log(event, tenant=None, actor=None, target=None, detail=None, request=None):
    """감사 기록 1건 추가. 실패해도 예외를 밖으로 내보내지 않는다."""
    try:
        ip = client_ip(request) if request is not None else None
        ua = (request.headers.get('User-Agent', '')[:300] if request is not None else None)
        path = GLOBAL_DB_PATH if tenant is None else tenant_db_path(tenant)
        conn = sqlite3.connect(path, timeout=5)
        conn.execute(
            'INSERT INTO audit_log (occurred_at, event, tenant, actor, target, ip, user_agent, detail) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), event, tenant,
             actor, target, ip, ua, detail)
        )
        conn.commit()
        conn.close()
    except Exception:
        # 로그 기록 실패가 급여 조회 자체를 막으면 안 된다.
        pass


def recent(tenant, limit=300, event=None):
    """관리자 화면용 최근 기록 조회."""
    try:
        conn = sqlite3.connect(tenant_db_path(tenant), timeout=5)
        conn.row_factory = sqlite3.Row
        if event:
            rows = conn.execute(
                'SELECT * FROM audit_log WHERE event = ? ORDER BY id DESC LIMIT ?',
                (event, limit)).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM audit_log ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        conn.close()
        return rows
    except sqlite3.Error:
        return []


def counts_by_event(tenant):
    """이벤트별 건수 (관리자 화면 요약용)."""
    try:
        conn = sqlite3.connect(tenant_db_path(tenant), timeout=5)
        rows = conn.execute(
            'SELECT event, COUNT(*) FROM audit_log GROUP BY event ORDER BY COUNT(*) DESC'
        ).fetchall()
        conn.close()
        return rows
    except sqlite3.Error:
        return []
