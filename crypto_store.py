"""민감정보 암호화 저장 (Phase 0-1)

대상: 계좌번호, 생년월일
근거: 개인정보위 해석상 계좌번호는 종류를 불문하고 암호화 저장 대상이다
      (개인정보의 안전성 확보조치 기준). 생년월일은 법정 암호화 대상은
      아니지만 본인확인 인증요소로 쓰이므로 같이 암호화한다.

방식: Fernet (AES-128-CBC + HMAC-SHA256, 인증된 암호화)
      암호문 앞에 'enc1:' 접두어를 붙인다. 이 접두어 덕분에
        - 접두어가 없으면 암호화 이전에 저장된 평문으로 보고 그대로 반환한다
          (마이그레이션 중에도 서비스가 깨지지 않는다)
        - 이미 암호화된 값을 다시 암호화하지 않는다

키 관리 우선순위:
      1) 환경변수 ROUTINEOUT_ENC_KEY  ← 운영 권장
      2) PA/enc_key.txt (없으면 최초 실행 시 자동 생성, gitignore 대상)

  ※ 알려진 한계(반드시 인지할 것):
     키 파일 방식은 키가 DB와 같은 서버에 있다. 서버 파일시스템이 통째로
     털리면 암호화의 방어력은 사라진다. 이 방식이 실제로 막아주는 것은
       - 백업본(GitHub 비공개 저장소 등)만 유출된 경우   ← 키는 백업에서 제외
       - DB 파일만 유출된 경우
     서버 침해까지 막으려면 키를 외부 비밀저장소로 옮겨야 한다(Phase 2).
"""

import os

from cryptography.fernet import Fernet, InvalidToken

PREFIX = 'enc1:'
KEY_PATH = os.path.join(os.path.dirname(__file__), 'enc_key.txt')

_fernet = None


def _load_key():
    env_key = os.environ.get('ROUTINEOUT_ENC_KEY', '').strip()
    if env_key:
        return env_key.encode()

    if os.path.exists(KEY_PATH):
        with open(KEY_PATH, 'rb') as f:
            key = f.read().strip()
        if key:
            return key

    # 최초 실행: 키 생성 후 소유자만 읽을 수 있게 저장
    key = Fernet.generate_key()
    with open(KEY_PATH, 'wb') as f:
        f.write(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass  # Windows 등에서는 무시
    return key


def _cipher():
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_load_key())
    return _fernet


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value):
    """평문 -> 'enc1:<암호문>'. 빈 값과 이미 암호화된 값은 그대로 둔다."""
    if value is None or value == '':
        return value
    value = str(value)
    if is_encrypted(value):
        return value
    return PREFIX + _cipher().encrypt(value.encode('utf-8')).decode('ascii')


def decrypt(value):
    """'enc1:<암호문>' -> 평문. 접두어가 없으면 평문으로 보고 그대로 반환한다
    (암호화 도입 이전 데이터 호환). 복호화 실패 시에도 예외를 던지지 않고
    표시용 문자열을 반환한다 — 급여 조회 자체가 막히면 안 되기 때문."""
    if value is None or value == '':
        return value
    value = str(value)
    if not is_encrypted(value):
        return value
    try:
        return _cipher().decrypt(value[len(PREFIX):].encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError):
        return '(복호화 실패)'


def decrypt_row(row, fields):
    """sqlite3.Row -> dict 로 바꾸면서 지정 필드를 복호화한다."""
    d = dict(row)
    for f in fields:
        if f in d:
            d[f] = decrypt(d[f])
    return d


def self_test():
    """키가 정상 동작하는지 확인 (배포 전 스모크 테스트용)."""
    sample = '110-123-456789'
    token = encrypt(sample)
    return is_encrypted(token) and decrypt(token) == sample
