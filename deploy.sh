#!/usr/bin/env bash
# routineout 배포 스크립트
#
# 왜 스크립트로 만들었나:
#   2026-08-16에 같은 사고가 두 번 났다. PythonAnywhere는 `touch wsgi` 직후
#   즉시 재기동되지 않는데, 재기동 확인 없이 완료로 판단했다.
#     · 암호화 배포: 구버전 워커가 암호문(enc1:...)을 관리자 화면에 그대로 노출
#     · 가격표 배포: 구버전 페이지가 계속 서빙됨
#   그래서 "배포 후 실제로 새 버전이 떴는지" 확인을 사람 기억에 맡기지 않고
#   스크립트가 강제한다. 확인이 안 되면 0이 아닌 코드로 종료한다.
#
# 실행 순서:
#   1) 로컬 스모크 테스트          — 실패하면 push조차 하지 않는다
#   2) git push
#   3) 서버 git pull + 스모크 테스트 — 실패하면 재기동하지 않는다
#   4) wsgi touch (재기동 요청)
#   5) /healthz 폴링               — 배포한 커밋 해시가 실제로 응답할 때까지
#
# 사용법:
#   cd PA && ./deploy.sh "커밋 메시지"
#   cd PA && ./deploy.sh --no-commit        # 이미 커밋해둔 경우
#
# 데이터 마이그레이션이 필요한 배포는 이 스크립트를 먼저 끝낸 뒤(=신코드가
# 뜬 것을 확인한 뒤) 마이그레이션을 실행할 것. 순서를 반대로 하면 위 사고가
# 그대로 재현된다.

set -u

SSH_KEY="$HOME/.ssh/routineout"
SSH_HOST="routineout@ssh.pythonanywhere.com"
REMOTE_DIR="~/mysite"
WSGI="/var/www/www_routineout_com_wsgi.py"
HEALTH_URL="https://www.routineout.com/healthz"
POLL_TRIES=20
POLL_INTERVAL=10

cd "$(dirname "$0")" || exit 1

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m!! %s\033[0m\n' "$*"; exit 1; }

# ── 1) 로컬 스모크 테스트 ─────────────────────────────────────
say "1/5  로컬 스모크 테스트"
if [ -z "${SMOKE_TENANT_PASSWORDS:-}" ]; then
  echo "    (SMOKE_TENANT_PASSWORDS 미설정 — 실제 테넌트 어드민 검사는 건너뜀)"
fi
python tests/smoke_test.py | tail -4
[ "${PIPESTATUS[0]}" -eq 0 ] || die "로컬 테스트 실패. 배포 중단."

# ── 2) 커밋 & 푸시 ────────────────────────────────────────────
say "2/5  커밋 & 푸시"
if [ "${1:-}" != "--no-commit" ]; then
  [ $# -ge 1 ] || die "커밋 메시지를 넘기거나 --no-commit 을 쓰라."
  git add -A
  if git diff --cached --quiet; then
    echo "    변경 없음 — 커밋 생략"
  else
    git commit -q -m "$1" || die "커밋 실패"
  fi
fi
git push -q origin main || die "푸시 실패"
LOCAL_REV="$(git rev-parse --short=12 HEAD)"
echo "    배포할 커밋: $LOCAL_REV"

# ── 3) 서버 pull + 테스트 ─────────────────────────────────────
say "3/5  서버 pull + 스모크 테스트"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_HOST" \
  "cd $REMOTE_DIR && git pull -q && python tests/smoke_test.py | tail -4" \
  || die "서버 테스트 실패. 재기동하지 않는다."

# ── 4) 재기동 요청 ────────────────────────────────────────────
say "4/5  재기동 요청"
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_HOST" "touch $WSGI" \
  || die "wsgi touch 실패"
echo "    touch 완료 — 실제 반영까지는 시간이 걸린다"

# ── 5) 반영 확인 (여기가 이 스크립트의 존재 이유) ──────────────
say "5/5  배포 반영 확인"
for i in $(seq 1 $POLL_TRIES); do
  sleep "$POLL_INTERVAL"
  REMOTE_REV="$(curl -s --max-time 30 "$HEALTH_URL" \
    | python -c "import sys,json;print(json.load(sys.stdin).get('rev',''))" 2>/dev/null)"
  if [ "$REMOTE_REV" = "$LOCAL_REV" ]; then
    printf '\n\033[1;32m배포 완료 — 프로덕션 리비전 %s 확인 (%d회차)\033[0m\n' "$REMOTE_REV" "$i"
    exit 0
  fi
  echo "    ${i}/${POLL_TRIES}회차: 프로덕션 ${REMOTE_REV:-(응답없음)} / 기대 $LOCAL_REV"
done

die "제한시간 내 반영 확인 실패. 프로덕션이 아직 구버전일 수 있으니 직접 확인하라: $HEALTH_URL"
