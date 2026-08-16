"""기존 평문 데이터 암호화 마이그레이션 (Phase 0-1)

암호화 도입 이전에 저장된 paycheck_consents 의 계좌번호·생년월일을
암호화 형식('enc1:...')으로 변환한다.

특징:
  - 이미 암호화된 행은 건너뛴다(여러 번 실행해도 안전).
  - --dry-run 으로 무엇이 바뀔지 먼저 확인할 수 있다.
  - 실행 전 대상 DB를 .bak 으로 복사한다.

사용법:
    cd PA
    python migrate_encrypt.py --dry-run     # 확인만
    python migrate_encrypt.py               # 실제 변환
"""

import argparse
import os
import shutil
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import crypto_store  # noqa: E402
from local_config import TENANTS  # noqa: E402
from tenant_db import db_path  # noqa: E402

TARGET_TABLE = 'paycheck_consents'
TARGET_FIELDS = ('account_number', 'birth')


def migrate_tenant(tenant, dry_run):
    path = db_path(tenant)
    if not os.path.exists(path):
        print(f'  [{tenant}] DB 없음 — 건너뜀')
        return 0, 0

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f'SELECT id, {", ".join(TARGET_FIELDS)} FROM {TARGET_TABLE}'
        ).fetchall()
    except sqlite3.OperationalError as exc:
        print(f'  [{tenant}] 테이블 조회 실패 ({exc}) — 건너뜀')
        conn.close()
        return 0, 0

    pending = []
    for r in rows:
        updates = {}
        for f in TARGET_FIELDS:
            val = r[f]
            if val and not crypto_store.is_encrypted(val):
                updates[f] = crypto_store.encrypt(val)
        if updates:
            pending.append((r['id'], updates))

    total, changed = len(rows), len(pending)
    print(f'  [{tenant}] 전체 {total}행 · 변환 대상 {changed}행')

    if dry_run or not pending:
        conn.close()
        return total, changed

    # 되돌릴 수 있도록 원본 백업
    backup = path + '.pre-encrypt.bak'
    shutil.copy2(path, backup)
    print(f'         원본 백업 → {os.path.basename(backup)}')

    for row_id, updates in pending:
        sets = ', '.join(f'{k} = ?' for k in updates)
        conn.execute(f'UPDATE {TARGET_TABLE} SET {sets} WHERE id = ?',
                     list(updates.values()) + [row_id])
    conn.commit()
    conn.close()
    print(f'         {changed}행 암호화 완료')
    return total, changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='변경 없이 대상만 출력')
    args = ap.parse_args()

    if not crypto_store.self_test():
        print('!! 암호화 키 자가검사 실패 — 중단한다.')
        return 1

    mode = '확인 모드(변경 없음)' if args.dry_run else '실제 변환'
    print(f'암호화 마이그레이션 — {mode}')
    print(f'대상: {TARGET_TABLE}.{", ".join(TARGET_FIELDS)}')
    print('-' * 60)

    total_all = changed_all = 0
    for tenant in TENANTS:
        t, c = migrate_tenant(tenant, args.dry_run)
        total_all += t
        changed_all += c

    print('-' * 60)
    print(f'합계: 전체 {total_all}행 · 변환 대상 {changed_all}행')
    if args.dry_run and changed_all:
        print('실제로 변환하려면 --dry-run 없이 다시 실행하라.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
