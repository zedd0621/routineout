"""routineout 배포 전 스모크 테스트 (Phase 0-5)

목적: A 고객사 요청으로 고친 코드가 B 고객사 화면을 조용히 깨뜨리는 것을 배포 전에 잡는다.
git은 사후 복구 수단이지 감지 수단이 아니므로, 이 파일이 그 감지 역할을 맡는다.

실행:
    cd PA && python tests/smoke_test.py

전체 테넌트의 어드민 페이지까지 검사하려면 비밀번호를 넘긴다(선택):
    SMOKE_TENANT_PASSWORDS="aiedu:비밀번호,abccompany:비밀번호" python tests/smoke_test.py

비밀번호를 안 넘기면 실제 테넌트는 '익명 접근 가능 범위'만 검사하고,
인증이 필요한 화면은 내부 생성 테스트 테넌트(__smoke__)로 검사한다.

종료 코드: 전부 통과 0, 하나라도 실패 1  (배포 스크립트에서 게이트로 사용)
"""

import os
import shutil
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from werkzeug.security import generate_password_hash  # noqa: E402

import local_config  # noqa: E402

# ── 테스트 전용 테넌트 주입 (flask_app import 전에 해야 init_db가 잡는다) ──
SMOKE_TENANT = '__smoke__'
SMOKE_PW = 'smoke-test-pw-1234'
local_config.TENANTS[SMOKE_TENANT] = {
    'username': SMOKE_TENANT,
    'password_hash': generate_password_hash(SMOKE_PW),
}

import flask_app  # noqa: E402
import payroll_engine as pay  # noqa: E402
import rate_limit  # noqa: E402
import audit  # noqa: E402
import crypto_store  # noqa: E402
from masking import mask_account  # noqa: E402
from tenant_db import db_path as tenant_db_path  # noqa: E402

REAL_TENANTS = [t for t in local_config.TENANTS if t != SMOKE_TENANT]

# 어드민 비밀번호를 환경변수로 넘기면 실제 테넌트도 인증 화면까지 검사한다.
_pw_env = os.environ.get('SMOKE_TENANT_PASSWORDS', '').strip()
TENANT_PASSWORDS = {}
if _pw_env:
    for pair in _pw_env.split(','):
        if ':' in pair:
            slug, pw = pair.split(':', 1)
            TENANT_PASSWORDS[slug.strip()] = pw.strip()

# 로그인 없이 열려야 하는 공개 폼 (강사·센터가 직접 제출하는 경로)
PUBLIC_TENANT_PATHS = ['center', 'instructor', 'paycheck', 'payslip']

# 로그인해야만 열려야 하는 관리자 화면
ADMIN_TENANT_PATHS = [
    'dashboard', 'center-admin', 'instructor-admin',
    'recommendation', 'paycheck-admin', 'payslip-admin', 'audit',
]

_results = []


def check(name, condition, detail=''):
    _results.append((name, bool(condition), detail))


def expect_status(client, path, expected, label=None):
    label = label or f'GET {path}'
    try:
        resp = client.get(path)
    except Exception as exc:
        check(label, False, f'예외 발생: {exc!r}')
        return None
    ok = resp.status_code == expected
    check(label, ok, f'기대 {expected}, 실제 {resp.status_code}')
    return resp


# ══════════════════════════════════════════════════════════════
# A. 마케팅 페이지 — 익명 접근 200
# ══════════════════════════════════════════════════════════════
def test_marketing_pages(app):
    with app.test_client() as c:
        for path in ['/', '/philosophy', '/pricing', '/live-demo', '/apply', '/login']:
            expect_status(c, path, 200, f'[마케팅] {path} → 200')


# ══════════════════════════════════════════════════════════════
# B. 접근 제어 — 미인증 차단 / 없는 테넌트 404 / 테넌트 격리
# ══════════════════════════════════════════════════════════════
def test_access_control(app):
    with app.test_client() as c:
        # 미인증 상태에서 관리자 화면 → 로그인으로 리다이렉트
        for path in ADMIN_TENANT_PATHS:
            resp = c.get(f'/{SMOKE_TENANT}/{path}')
            ok = resp.status_code == 302 and '/login' in resp.headers.get('Location', '')
            check(f'[접근제어] 미인증 /{SMOKE_TENANT}/{path} → /login 리다이렉트',
                  ok, f'실제 {resp.status_code} → {resp.headers.get("Location")}')

        # 등록되지 않은 테넌트 → 404
        expect_status(c, '/nosuchtenant/dashboard', 404, '[접근제어] 없는 테넌트 → 404')
        expect_status(c, '/nosuchtenant/paycheck', 404, '[접근제어] 없는 테넌트 공개폼 → 404')

    # 잘못된 비밀번호는 로그인되지 않는다
    with app.test_client() as c:
        resp = c.post('/login', data={'username': SMOKE_TENANT, 'password': 'wrong-password'})
        check('[접근제어] 잘못된 비밀번호 → 로그인 거부',
              resp.status_code == 200, f'실제 {resp.status_code} (리다이렉트면 인증 통과된 것)')
        with c.session_transaction() as sess:
            check('[접근제어] 실패 시 세션에 tenant 미설정', sess.get('tenant') is None)

    # 올바른 비밀번호 → 자기 테넌트 대시보드로
    with app.test_client() as c:
        resp = c.post('/login', data={'username': SMOKE_TENANT, 'password': SMOKE_PW})
        ok = resp.status_code == 302 and SMOKE_TENANT in resp.headers.get('Location', '')
        check('[접근제어] 올바른 비밀번호 → 대시보드 리다이렉트',
              ok, f'실제 {resp.status_code} → {resp.headers.get("Location")}')

        # ★ 테넌트 격리: __smoke__로 로그인한 세션은 다른 테넌트에 접근 못 한다
        for other in REAL_TENANTS:
            resp = c.get(f'/{other}/paycheck-admin')
            ok = resp.status_code == 302 and '/login' in resp.headers.get('Location', '')
            check(f'[격리] {SMOKE_TENANT} 세션 → /{other}/paycheck-admin 차단',
                  ok, f'실제 {resp.status_code} (200이면 고객사 데이터 유출)')


# ══════════════════════════════════════════════════════════════
# C. 공개 폼 — 전 테넌트에서 익명 200
# ══════════════════════════════════════════════════════════════
def test_public_forms(app):
    with app.test_client() as c:
        for tenant in list(REAL_TENANTS) + [SMOKE_TENANT]:
            for path in PUBLIC_TENANT_PATHS:
                expect_status(c, f'/{tenant}/{path}', 200,
                              f'[공개폼] {tenant}/{path} → 200')


# ══════════════════════════════════════════════════════════════
# D. 관리자 화면 — 로그인 후 전 테넌트 200
# ══════════════════════════════════════════════════════════════
def _admin_pages_for(app, tenant, password):
    with app.test_client() as c:
        resp = c.post('/login', data={'username': tenant, 'password': password})
        if resp.status_code != 302:
            check(f'[어드민] {tenant} 로그인', False,
                  f'로그인 실패 (status {resp.status_code}) — 비밀번호 확인 필요')
            return
        check(f'[어드민] {tenant} 로그인', True)
        for path in ADMIN_TENANT_PATHS:
            expect_status(c, f'/{tenant}/{path}', 200, f'[어드민] {tenant}/{path} → 200')


def test_admin_pages(app):
    _admin_pages_for(app, SMOKE_TENANT, SMOKE_PW)
    for tenant in REAL_TENANTS:
        pw = TENANT_PASSWORDS.get(tenant)
        if pw:
            _admin_pages_for(app, tenant, pw)
        else:
            check(f'[어드민] {tenant} — 건너뜀 (비밀번호 미제공)', True,
                  'SMOKE_TENANT_PASSWORDS 환경변수로 넘기면 검사함')


# ══════════════════════════════════════════════════════════════
# E. 급여계산 고정값 검증 — 계산 로직이 바뀌면 즉시 잡힌다
#    (기대값은 2026-08-16 현재 동작을 고정한 것. 의도적으로 규칙을
#     바꿨다면 이 기대값도 같이 고쳐야 한다.)
# ══════════════════════════════════════════════════════════════
def _entry(date, sched_hours):
    return {'date': date, 'sched_hours': sched_hours}


def test_payroll_fixed_values():
    # 15분 단위 올림
    for seconds, expected in [(0, 0), (60, 15), (900, 15), (901, 30), (3600, 60), (-5, 0)]:
        got = pay.ceil_15min(seconds)
        check(f'[급여] ceil_15min({seconds}) == {expected}', got == expected, f'실제 {got}')

    # 주휴수당: 주 15h 미만 미지급, 이상이면 min(주간,40)/40*8*시급
    for hours, expected in [(14, 0), (15, 36300), (20, 48400), (45, 96800)]:
        _, total = pay.calc_weekly_holiday([_entry('2026-08-03', hours)], '2026-08')
        check(f'[급여] 주휴수당 주 {hours}h == {expected}원', total == expected, f'실제 {total}')

    # 주휴수당 상수 자체가 바뀌면 위 기대값이 무의미해지므로 상수도 고정
    check('[급여] WAGE_BASE == 12100', pay.WAGE_BASE == 12100, f'실제 {pay.WAGE_BASE}')
    check('[급여] 주휴 기준시간 15h', pay.WEEKLY_HOLIDAY_MIN_HOURS == 15)
    check('[급여] 주휴 상한 40h', pay.WEEKLY_HOLIDAY_CAP_HOURS == 40)

    # 4대보험 판정: 월 소정근로 60h 경계
    is_four, _, hrs = pay.judge_insurance([_entry('2026-08-03', 59.9)], '2026-08')
    check('[급여] 월 59.9h → 2대보험', is_four is False, f'실제 {is_four}/{hrs}h')
    is_four, _, hrs = pay.judge_insurance([_entry('2026-08-03', 60.0)], '2026-08')
    check('[급여] 월 60.0h → 4대보험', is_four is True, f'실제 {is_four}/{hrs}h')

    # 타 월 근무는 판정에서 제외되어야 한다
    _, _, hrs = pay.judge_insurance(
        [_entry('2026-08-03', 30.0), _entry('2026-07-03', 40.0)], '2026-08')
    check('[급여] 타월 근무 제외 (30h만 집계)', hrs == 30.0, f'실제 {hrs}h')

    # 생년월일 정규화
    for raw, expected in [('1990-01-01', '19900101'), ('1990.01.01', '19900101')]:
        got = pay.normalize_birth(raw)
        check(f'[급여] normalize_birth({raw}) == {expected}', got == expected, f'실제 {got}')

    # 만 65세 이상 고용보험 면제
    for birth, expected in [('1960-01-01', True), ('1990-01-01', False), ('1961-09-01', False)]:
        got = pay.is_employment_insurance_exempt_by_age(birth, '2026-08')
        check(f'[급여] 65세 면제판정 {birth} == {expected}', got == expected, f'실제 {got}')

    # 연말 경계 전월 계산
    check("[급여] prev_month_str('2026-01') == '2025-12'",
          pay.prev_month_str('2026-01') == '2025-12', f"실제 {pay.prev_month_str('2026-01')}")


# ══════════════════════════════════════════════════════════════
# F. 시도 횟수 제한 (Phase 0-4)
#    ※ 실제로 잠기는지까지 확인한다. 검사 후 카운터를 반드시 초기화한다.
# ══════════════════════════════════════════════════════════════
TEST_IP = '127.0.0.1'


def _stored_fail_count(scope, key):
    conn = sqlite3.connect(rate_limit.DB_PATH)
    row = conn.execute('SELECT fail_count FROM rate_limit WHERE scope = ? AND key = ?',
                       (scope, key)).fetchone()
    conn.close()
    return row[0] if row else 0


def _reset_all_limits():
    for scope in ('login', 'owner', 'paycheck', 'payslip'):
        rate_limit.clear(scope, TEST_IP)
        for tenant in list(REAL_TENANTS) + [SMOKE_TENANT]:
            rate_limit.clear(scope, f'{tenant}:{TEST_IP}')


def test_rate_limit(app):
    _reset_all_limits()
    limit = rate_limit.MAX_ATTEMPTS

    # ── 테넌트 로그인 ──
    with app.test_client() as c:
        for _ in range(limit - 1):
            c.post('/login', data={'username': SMOKE_TENANT, 'password': 'wrong'})
        allowed, _ = rate_limit.check('login', TEST_IP)
        check(f'[제한] 로그인 {limit - 1}회 실패까지는 허용', allowed)

        c.post('/login', data={'username': SMOKE_TENANT, 'password': 'wrong'})
        allowed, retry = rate_limit.check('login', TEST_IP)
        check(f'[제한] 로그인 {limit}회 실패 → 잠금', (not allowed) and retry > 0,
              f'allowed={allowed}, retry={retry}s')

        # 잠긴 동안에는 올바른 비밀번호도 통과하면 안 된다
        resp = c.post('/login', data={'username': SMOKE_TENANT, 'password': SMOKE_PW})
        ok = resp.status_code == 200  # 302면 로그인 성공한 것 = 제한 무력
        check('[제한] 잠금 중에는 올바른 비밀번호도 거부', ok,
              f'실제 {resp.status_code} (302면 제한이 뚫린 것)')
        with c.session_transaction() as sess:
            check('[제한] 잠금 중 세션 미설정', sess.get('tenant') is None)
    rate_limit.clear('login', TEST_IP)

    # ── 성공 시 누적 실패 초기화 ──
    with app.test_client() as c:
        c.post('/login', data={'username': SMOKE_TENANT, 'password': 'wrong'})
        check('[제한] 실패 1회가 기록됨', _stored_fail_count('login', TEST_IP) == 1,
              f"실제 {_stored_fail_count('login', TEST_IP)}")
        resp = c.post('/login', data={'username': SMOKE_TENANT, 'password': SMOKE_PW})
        check('[제한] 올바른 비밀번호 → 로그인 성공', resp.status_code == 302,
              f'실제 {resp.status_code}')
        check('[제한] 성공 시 누적 실패 초기화', _stored_fail_count('login', TEST_IP) == 0,
              f"실제 {_stored_fail_count('login', TEST_IP)}")

    # ── 급여조회 본인확인 (핵심: 타인 정보 반복 시도 차단) ──
    pk_key = f'{SMOKE_TENANT}:{TEST_IP}'
    with app.test_client() as c:
        for _ in range(limit):
            c.post(f'/{SMOKE_TENANT}/paycheck',
                   data={'name': '없는사람', 'birth': '1990-01-01',
                         'tel_last4': '0000', 'target_month': '2026-08'})
        allowed, retry = rate_limit.check('paycheck', pk_key)
        check(f'[제한] 급여조회 {limit}회 실패 → 잠금', (not allowed) and retry > 0,
              f'allowed={allowed}, retry={retry}s')
    rate_limit.clear('paycheck', pk_key)

    # ── 급여명세서 조회 ──
    ps_key = f'{SMOKE_TENANT}:{TEST_IP}'
    with app.test_client() as c:
        for _ in range(limit):
            c.post(f'/{SMOKE_TENANT}/payslip',
                   data={'name': '없는사람', 'birth': '1990-01-01',
                         'tel_last4': '0000', 'target_month': '2026-08'})
        allowed, _ = rate_limit.check('payslip', ps_key)
        check(f'[제한] 명세서조회 {limit}회 실패 → 잠금', not allowed)
    rate_limit.clear('payslip', ps_key)

    # ── 대표 리드 페이지 ──
    with app.test_client() as c:
        for _ in range(limit):
            c.post('/leads', data={'password': 'wrong'})
        allowed, _ = rate_limit.check('owner', TEST_IP)
        check(f'[제한] /leads {limit}회 실패 → 잠금', not allowed)
    rate_limit.clear('owner', TEST_IP)

    # ── 프록시 환경(X-Forwarded-For)에서 실제 클라이언트 IP를 식별하는가 ──
    with app.test_client() as c:
        c.post('/login', data={'username': SMOKE_TENANT, 'password': 'wrong'},
               headers={'X-Forwarded-For': '203.0.113.9, 10.0.0.1'})
        check('[제한] X-Forwarded-For의 첫 IP로 카운트',
              _stored_fail_count('login', '203.0.113.9') == 1,
              f"실제 {_stored_fail_count('login', '203.0.113.9')} "
              f"(0이면 프록시 뒤에서 전원이 한 카운터를 공유하게 됨)")
    rate_limit.clear('login', '203.0.113.9')

    _reset_all_limits()


# ══════════════════════════════════════════════════════════════
# G. 감사로그 (Phase 0-3)
#    로그가 없으면 사고 시 "우리 시스템 결함이 아니다"를 입증할 수 없다.
#    따라서 "기록이 실제로 남는가"를 검사한다.
# ══════════════════════════════════════════════════════════════
def _audit_rows(tenant, event=None):
    return audit.recent(tenant, limit=1000, event=event)


def _audit_count(tenant, event):
    return len(_audit_rows(tenant, event))


def test_audit_log(app):
    _reset_all_limits()

    # ── 로그인 성공/실패가 해당 고객사 로그에 남는가 ──
    before_ok = _audit_count(SMOKE_TENANT, audit.LOGIN_SUCCESS)
    before_ng = _audit_count(SMOKE_TENANT, audit.LOGIN_FAIL)
    with app.test_client() as c:
        c.post('/login', data={'username': SMOKE_TENANT, 'password': 'wrong'})
        c.post('/login', data={'username': SMOKE_TENANT, 'password': SMOKE_PW})
    check('[감사] 로그인 실패 기록됨',
          _audit_count(SMOKE_TENANT, audit.LOGIN_FAIL) == before_ng + 1,
          f'{before_ng} → {_audit_count(SMOKE_TENANT, audit.LOGIN_FAIL)}')
    check('[감사] 로그인 성공 기록됨',
          _audit_count(SMOKE_TENANT, audit.LOGIN_SUCCESS) == before_ok + 1,
          f'{before_ok} → {_audit_count(SMOKE_TENANT, audit.LOGIN_SUCCESS)}')

    # ── 기록에 IP가 담기는가 (책임소재 확인의 핵심 필드) ──
    rows = _audit_rows(SMOKE_TENANT, audit.LOGIN_SUCCESS)
    check('[감사] 기록에 IP 포함', bool(rows and rows[0]['ip']),
          f"실제 ip={rows[0]['ip'] if rows else '(행 없음)'}")
    check('[감사] 기록에 발생시각 포함', bool(rows and rows[0]['occurred_at']))

    # ── 급여조회 본인확인 실패가 기록되는가 ──
    before = _audit_count(SMOKE_TENANT, audit.PAYCHECK_FAIL)
    with app.test_client() as c:
        c.post(f'/{SMOKE_TENANT}/paycheck',
               data={'name': '없는사람', 'birth': '1990-01-01',
                     'tel_last4': '0000', 'target_month': '2026-08'})
    check('[감사] 급여조회 본인확인 실패 기록됨',
          _audit_count(SMOKE_TENANT, audit.PAYCHECK_FAIL) == before + 1,
          f'{before} → {_audit_count(SMOKE_TENANT, audit.PAYCHECK_FAIL)}')

    # ── 관리자 조회·반출이 기록되는가 (대량 반출 경로가 핵심) ──
    before_view = _audit_count(SMOKE_TENANT, audit.ADMIN_VIEW)
    before_exp = _audit_count(SMOKE_TENANT, audit.ADMIN_EXPORT)
    with app.test_client() as c:
        c.post('/login', data={'username': SMOKE_TENANT, 'password': SMOKE_PW})
        c.get(f'/{SMOKE_TENANT}/paycheck-admin')
        c.get(f'/{SMOKE_TENANT}/instructor-admin/download')
        expect_status(c, f'/{SMOKE_TENANT}/audit', 200, '[감사] 접근기록 화면 200')
        expect_status(c, f'/{SMOKE_TENANT}/audit/download', 200, '[감사] 접근기록 CSV 200')
    check('[감사] 관리자 조회 기록됨',
          _audit_count(SMOKE_TENANT, audit.ADMIN_VIEW) > before_view,
          f'{before_view} → {_audit_count(SMOKE_TENANT, audit.ADMIN_VIEW)}')
    check('[감사] 관리자 내려받기 기록됨',
          _audit_count(SMOKE_TENANT, audit.ADMIN_EXPORT) == before_exp + 1,
          f'{before_exp} → {_audit_count(SMOKE_TENANT, audit.ADMIN_EXPORT)}')

    # ── 접근기록 화면은 로그인 없이 열리면 안 된다 ──
    with app.test_client() as c:
        resp = c.get(f'/{SMOKE_TENANT}/audit')
        ok = resp.status_code == 302 and '/login' in resp.headers.get('Location', '')
        check('[감사] 미인증 접근기록 열람 차단', ok, f'실제 {resp.status_code}')

    # ── 고객사 격리: A사 로그가 B사 DB에 섞이지 않는가 ──
    for other in REAL_TENANTS:
        rows_other = _audit_rows(other)
        leaked = [r for r in rows_other if r['tenant'] and r['tenant'] != other]
        check(f'[감사] {other} 로그에 타 고객사 기록 없음', not leaked,
              f'{len(leaked)}건 혼입' if leaked else '')

    _reset_all_limits()


# ══════════════════════════════════════════════════════════════
# H. 민감정보 암호화 (Phase 0-1)
#    핵심 검사: 계좌번호가 DB에 평문으로 남지 않는가.
# ══════════════════════════════════════════════════════════════
def test_encryption(app):
    # ── 키·라운드트립 ──
    check('[암호화] 키 자가검사 통과', crypto_store.self_test())

    sample = '110-987-654321'
    token = crypto_store.encrypt(sample)
    check('[암호화] 암호문에 enc1: 접두어', crypto_store.is_encrypted(token), f'{token[:12]}...')
    check('[암호화] 원문이 암호문에 노출되지 않음', sample not in token)
    check('[암호화] 복호화 시 원문 일치', crypto_store.decrypt(token) == sample)

    # ── 하위호환: 접두어 없는 평문은 그대로 통과 (마이그레이션 중 서비스 유지) ──
    check('[암호화] 평문 입력은 그대로 반환', crypto_store.decrypt('110-123-456') == '110-123-456')
    check('[암호화] 이중 암호화 방지', crypto_store.encrypt(token) == token)
    for empty in ('', None):
        check(f'[암호화] 빈 값({empty!r}) 통과', crypto_store.encrypt(empty) == empty)

    # ── 마스킹 ──
    # '110-987-654321' -> 숫자 12자리 -> 앞 8자리 마스킹 + 뒤 4자리
    check('[암호화] 계좌 마스킹 뒤 4자리만 노출',
          mask_account('110-987-654321') == '********4321',
          f"실제 {mask_account('110-987-654321')}")
    check('[암호화] 짧은 계좌는 전부 마스킹', mask_account('1234') == '****')

    # ── 종단 검사: 동의 제출 → DB에 평문 계좌번호가 없어야 한다 ──
    secret_acct = '9999-8888-777666'
    secret_birth = '1977-03-14'
    with app.test_client() as c:
        c.post(f'/{SMOKE_TENANT}/paycheck/consent', data={
            'name': '암호화테스트', 'birth': secret_birth, 'tel_last4': '1234',
            'target_month': '2026-08', 'total_pay': '1234567',
            'snapshot_json': '{}', 'bank_name': '국민은행',
            'account_number': secret_acct, 'insurance_type': '4대보험',
        })

    conn = sqlite3.connect(tenant_db_path(SMOKE_TENANT))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        'SELECT * FROM paycheck_consents WHERE name = ? ORDER BY id DESC LIMIT 1',
        ('암호화테스트',)).fetchone()
    conn.close()

    check('[암호화] 동의 기록이 저장됨', row is not None)
    if row:
        check('[암호화] ★ DB에 평문 계좌번호 없음', row['account_number'] != secret_acct,
              f"실제 저장값 {str(row['account_number'])[:16]}...")
        check('[암호화] 계좌번호가 암호문 형식', crypto_store.is_encrypted(row['account_number']))
        check('[암호화] ★ DB에 평문 생년월일 없음', row['birth'] != secret_birth)
        check('[암호화] 복호화하면 원래 계좌번호',
              crypto_store.decrypt(row['account_number']) == secret_acct)

    # ── 관리자 화면에는 마스킹본만, 내려받기에는 실제 값 ──
    with app.test_client() as c:
        c.post('/login', data={'username': SMOKE_TENANT, 'password': SMOKE_PW})
        html = c.get(f'/{SMOKE_TENANT}/paycheck-admin').get_data(as_text=True)
        check('[암호화] 관리자 화면에 평문 계좌번호 미노출', secret_acct not in html)
        check('[암호화] 관리자 화면에 암호문 그대로 노출 안 됨', 'enc1:' not in html)

        csv_text = c.get(f'/{SMOKE_TENANT}/paycheck-admin/download').get_data(as_text=True)
        check('[암호화] 내려받기에는 복호화된 실제 계좌번호', secret_acct in csv_text)
        check('[암호화] 내려받기에 암호문이 그대로 나가지 않음', 'enc1:' not in csv_text)


# ══════════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════════
def main():
    app = flask_app.app
    app.config['TESTING'] = True

    try:
        test_marketing_pages(app)
        test_access_control(app)
        test_public_forms(app)
        test_admin_pages(app)
        test_payroll_fixed_values()
        test_rate_limit(app)
        test_audit_log(app)
        test_encryption(app)
    finally:
        try:
            _reset_all_limits()
        except Exception:
            pass
        # 테스트 테넌트 흔적 제거
        smoke_dir = os.path.join(BASE_DIR, 'tenant_data', SMOKE_TENANT)
        if os.path.isdir(smoke_dir):
            shutil.rmtree(smoke_dir, ignore_errors=True)

    failed = [r for r in _results if not r[1]]
    print('=' * 72)
    for name, ok, detail in _results:
        if not ok:
            print(f'  FAIL  {name}')
            if detail:
                print(f'        └ {detail}')
    print('=' * 72)
    print(f'검사 대상 테넌트: {", ".join(REAL_TENANTS) or "(없음)"} (+ {SMOKE_TENANT})')
    print(f'총 {len(_results)}건 · 통과 {len(_results) - len(failed)} · 실패 {len(failed)}')
    if failed:
        print('\n>>> 실패 항목이 있다. 배포하지 말 것.')
        return 1
    print('\n>>> 전부 통과. 배포 가능.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
