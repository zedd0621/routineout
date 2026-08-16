"""백업 복구 (Phase 0-2)

backup.py 가 만든 암호화 스냅샷(.enc)을 풀어서 지정한 폴더에 복원한다.
서버가 사라진 상황에서도 backup_key 하나만 있으면 전부 복구된다.

사용법:
    # 서버에서 (키 파일이 있는 경우)
    python restore_backup.py ~/routineout-backup/snapshots/2026-08-16.enc --out ./복구본

    # 다른 PC에서 (키를 직접 입력하는 경우)
    python restore_backup.py 2026-08-16.enc --out ./복구본 --key "gAAAA...=="

복구된 폴더에는 tenant_data/, leads.db, audit.db, enc_key.txt, local_config.py 가
들어 있다. enc_key.txt 가 함께 복구되므로 계좌번호 복호화도 바로 가능하다.

주의: 복구본을 바로 운영에 덮어쓰지 말고, 먼저 별도 폴더에 풀어서 내용을
      확인한 뒤 옮길 것.
"""

import argparse
import os
import sys
import tarfile
import tempfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from cryptography.fernet import Fernet, InvalidToken  # noqa: E402

BACKUP_KEY_PATH = os.path.join(BASE_DIR, 'backup_key.txt')


def resolve_key(cli_key):
    if cli_key:
        return cli_key.strip().encode()
    env = os.environ.get('ROUTINEOUT_BACKUP_KEY', '').strip()
    if env:
        return env.encode()
    if os.path.exists(BACKUP_KEY_PATH):
        with open(BACKUP_KEY_PATH, 'rb') as f:
            return f.read().strip()
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('snapshot', help='복구할 .enc 파일 경로')
    ap.add_argument('--out', required=True, help='복구본을 풀어놓을 폴더')
    ap.add_argument('--key', help='백업 키 (없으면 backup_key.txt 또는 환경변수 사용)')
    ap.add_argument('--list', action='store_true', help='풀지 않고 목록만 출력')
    args = ap.parse_args()

    key = resolve_key(args.key)
    if not key:
        print('!! 백업 키를 찾을 수 없다. --key 로 직접 넘기거나 backup_key.txt 를 두라.')
        return 1

    if not os.path.exists(args.snapshot):
        print(f'!! 스냅샷 파일이 없다: {args.snapshot}')
        return 1

    with open(args.snapshot, 'rb') as f:
        blob = f.read()

    try:
        raw = Fernet(key).decrypt(blob)
    except InvalidToken:
        print('!! 복호화 실패 — 백업 키가 이 스냅샷과 맞지 않는다.')
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = os.path.join(tmp, 'snapshot.tar.gz')
        with open(tar_path, 'wb') as f:
            f.write(raw)

        with tarfile.open(tar_path, 'r:gz') as tar:
            members = tar.getmembers()
            if args.list:
                print(f'스냅샷 내용 ({len(members)}개 항목):')
                for m in members:
                    if m.isfile():
                        print(f'  {m.name}  ({m.size/1024:.1f} KB)')
                return 0
            os.makedirs(args.out, exist_ok=True)
            # 경로 탈출 방지 (신뢰된 자체 백업이지만 방어적으로 확인)
            safe = []
            for m in members:
                if m.name.startswith(('/', '..')) or '..' in m.name.split('/'):
                    print(f'   ! 의심스러운 경로 제외: {m.name}')
                    continue
                safe.append(m)
            tar.extractall(args.out, members=safe)
            print(f'복구 완료 — {len(safe)}개 항목 → {os.path.abspath(args.out)}')

    print('\n복구본에 enc_key.txt 가 포함되어 있으므로 계좌번호 복호화도 바로 가능하다.')
    print('운영에 반영하기 전에 내용을 먼저 확인하라.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
