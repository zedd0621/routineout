"""일일 자동 백업 — GitHub 비공개 저장소 (Phase 0-2)

왜 필요한가:
  고객사 급여 데이터가 PythonAnywhere 파일시스템에만 존재한다. 서버 장애나
  실수로 삭제되면 고객사는 그 달 급여를 지급하지 못한다. 이건 배상 사유다.

왜 '암호화한 뒤' 올리는가:
  백업 대상에는 DB뿐 아니라 paycheck_files 의 엑셀 원본(강사 이름·생년월일·
  연락처가 평문)이 포함된다. 비공개 저장소라도 평문 개인정보를 그대로 올리면
  Phase 0-1(계좌번호 암호화)이 무의미해진다. 그래서 아카이브 전체를 암호화한
  단일 파일로만 올린다.

키 구조 (중요):
  backup_key.txt  : 이 아카이브를 여는 키. 저장소에는 절대 올라가지 않는다.
                    → 반드시 서버 밖(비밀번호 관리자 등)에 따로 보관할 것.
  enc_key.txt     : 계좌번호 복호화 키. 아카이브 '안에' 들어간다.
                    → 백업만 복구하면 같이 딸려오므로 따로 보관할 필요 없다.

  즉 서버가 통째로 사라져도 backup_key.txt 하나만 있으면 전부 복구된다.
  반대로 저장소가 유출돼도 그 키가 없으면 아무것도 읽히지 않는다.

사용법:
    python backup.py              # 백업 1회 수행
    python backup.py --dry-run    # 아카이브만 만들고 push 안 함
"""

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from cryptography.fernet import Fernet  # noqa: E402

# 백업 저장소 클론 위치 (서버 기준)
REPO_DIR = os.path.expanduser('~/routineout-backup')
SNAPSHOT_DIR = 'snapshots'
RETENTION_DAYS = 30

BACKUP_KEY_PATH = os.path.join(BASE_DIR, 'backup_key.txt')

# 백업에 담을 것
INCLUDE = ['tenant_data', 'leads.db', 'audit.db', 'enc_key.txt', 'local_config.py']
# 담지 않을 것 (backup_key.txt 는 절대 포함 금지 — 자물쇠와 열쇠를 같이 두는 셈)
EXCLUDE_NAMES = {'backup_key.txt', 'rate_limit.db', '__pycache__'}
EXCLUDE_SUFFIX = ('.pyc', '.bak', '.pre-encrypt.bak')


def load_backup_key():
    env = os.environ.get('ROUTINEOUT_BACKUP_KEY', '').strip()
    if env:
        return env.encode()
    if os.path.exists(BACKUP_KEY_PATH):
        with open(BACKUP_KEY_PATH, 'rb') as f:
            key = f.read().strip()
        if key:
            return key
    key = Fernet.generate_key()
    with open(BACKUP_KEY_PATH, 'wb') as f:
        f.write(key)
    try:
        os.chmod(BACKUP_KEY_PATH, 0o600)
    except OSError:
        pass
    print('!! 백업 키를 새로 생성했다. 아래 키를 서버 밖에 반드시 보관하라.')
    print('!! 이 키가 없으면 백업본을 영원히 복구할 수 없다.')
    print('   ' + key.decode())
    return key


def _tar_filter(info):
    name = os.path.basename(info.name)
    if name in EXCLUDE_NAMES or name.endswith(EXCLUDE_SUFFIX):
        return None
    return info


def build_archive(dest_path):
    """백업 대상을 tar.gz 로 묶는다. 담긴 파일 수를 반환."""
    count = 0
    with tarfile.open(dest_path, 'w:gz') as tar:
        for item in INCLUDE:
            src = os.path.join(BASE_DIR, item)
            if not os.path.exists(src):
                print(f'   - {item}: 없음, 건너뜀')
                continue
            before = len(tar.getmembers())
            tar.add(src, arcname=item, filter=_tar_filter)
            added = len(tar.getmembers()) - before
            count += added
            print(f'   + {item}: {added}개')
    return count


def run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def prune_old(repo_dir):
    """보관기간이 지난 스냅샷 파일 삭제 (git 이력에는 남는다)."""
    snap_dir = os.path.join(repo_dir, SNAPSHOT_DIR)
    if not os.path.isdir(snap_dir):
        return 0
    cutoff = datetime.now() - timedelta(days=RETENTION_DAYS)
    removed = 0
    for fname in os.listdir(snap_dir):
        if not fname.endswith('.enc'):
            continue
        try:
            d = datetime.strptime(fname[:10], '%Y-%m-%d')
        except ValueError:
            continue
        if d < cutoff:
            os.remove(os.path.join(snap_dir, fname))
            removed += 1
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='아카이브만 만들고 push 하지 않음')
    args = ap.parse_args()

    stamp = datetime.now().strftime('%Y-%m-%d_%H%M')
    print(f'백업 시작 — {stamp}')

    key = load_backup_key()
    fernet = Fernet(key)

    # 1) 아카이브 생성
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = os.path.join(tmp, 'snapshot.tar.gz')
        print('1) 아카이브 생성')
        n_files = build_archive(tar_path)
        raw_size = os.path.getsize(tar_path)
        print(f'   총 {n_files}개 항목 · {raw_size/1024:.0f} KB')

        # 2) 암호화
        print('2) 암호화')
        with open(tar_path, 'rb') as f:
            blob = fernet.encrypt(f.read())
        print(f'   {len(blob)/1024:.0f} KB')

        if args.dry_run:
            out = os.path.join(BASE_DIR, f'backup_dryrun_{stamp}.enc')
            with open(out, 'wb') as f:
                f.write(blob)
            print(f'확인 모드 — {out} 에 저장하고 종료 (push 안 함)')
            return 0

        # 3) 백업 저장소에 배치
        if not os.path.isdir(os.path.join(REPO_DIR, '.git')):
            print(f'!! 백업 저장소가 없다: {REPO_DIR}')
            print('   먼저 비공개 저장소를 클론하라. (SECURITY_2026-08-16.md 참조)')
            return 1

        snap_dir = os.path.join(REPO_DIR, SNAPSHOT_DIR)
        os.makedirs(snap_dir, exist_ok=True)
        target = os.path.join(snap_dir, f'{datetime.now().strftime("%Y-%m-%d")}.enc')
        with open(target, 'wb') as f:
            f.write(blob)
        print(f'3) 저장 → {SNAPSHOT_DIR}/{os.path.basename(target)}')

        removed = prune_old(REPO_DIR)
        if removed:
            print(f'   보관기간({RETENTION_DAYS}일) 경과 스냅샷 {removed}개 삭제')

        # 4) 커밋 & 푸시
        print('4) 저장소에 반영')
        run(['git', 'add', '-A'], REPO_DIR)
        status = run(['git', 'status', '--porcelain'], REPO_DIR)
        if not status.stdout.strip():
            print('   변경 없음 — 커밋 생략')
            return 0
        msg = f'backup {stamp} ({n_files} items, {len(blob)//1024} KB)'
        c = run(['git', 'commit', '-q', '-m', msg], REPO_DIR)
        if c.returncode != 0:
            print('   커밋 실패:', c.stderr.strip()[:300])
            return 1
        p = run(['git', 'push', '-q', 'origin', 'HEAD'], REPO_DIR)
        if p.returncode != 0:
            print('   푸시 실패:', p.stderr.strip()[:300])
            return 1
        print('   완료')

    print('백업 성공')
    return 0


if __name__ == '__main__':
    sys.exit(main())
