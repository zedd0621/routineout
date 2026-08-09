"""aiedu 데모 테넌트용 가짜 데이터 생성 스크립트.
실제 개인정보를 전혀 쓰지 않고, 원본 엑셀/DB와 동일한 구조로 더미 데이터를 새로 만든다.
로컬/서버 양쪽에서 한 번씩 실행해서 tenant_data/aiedu/ 아래에 데이터를 채운다.
"""
import json
import os
import random
from datetime import datetime, timedelta

from openpyxl import Workbook

from tenant_db import init_db, get_db, paycheck_folder, tenant_dir

TENANT = 'aiedu'

FAKE_INSTRUCTORS = [
    {'name': '김민준', 'birth': '1985-03-12', 'tel': '010-2231-5567', 'role': '주강사'},
    {'name': '이서연', 'birth': '1990-07-24', 'tel': '010-3345-8821', 'role': '주강사'},
    {'name': '박도윤', 'birth': '1978-11-02', 'tel': '010-4459-1123', 'role': '보조강사'},
    {'name': '최하은', 'birth': '1995-01-19', 'tel': '010-5567-3345', 'role': '주강사'},
    {'name': '정지호', 'birth': '1988-09-30', 'tel': '010-6678-9012', 'role': '보조강사'},
    {'name': '강수아', 'birth': '1992-05-08', 'tel': '010-7789-2234', 'role': '주강사'},
    {'name': '조은우', 'birth': '1965-02-14', 'tel': '010-8890-4456', 'role': '보조강사'},
    {'name': '윤서준', 'birth': '1983-12-25', 'tel': '010-9901-5567', 'role': '주강사'},
    {'name': '한지민', 'birth': '1998-04-03', 'tel': '010-1012-6678', 'role': '보조강사'},
    {'name': '오세영', 'birth': '1975-08-17', 'tel': '010-1123-7789', 'role': '주강사'},
]

FAKE_CENTERS = [
    {'region': '김포', 'org': '가짜김포행복복지관', 'addr': '경기도 김포시 가짜로 12',
     'manager': '문담당', 'tel': '031-111-2222', 'day': '월,수,금', 'time': '1교시(10:00~12:00),2교시(13:00~15:00)'},
    {'region': '고양', 'org': '가짜고양주민센터', 'addr': '경기도 고양시 가짜로 34',
     'manager': '서담당', 'tel': '031-222-3333', 'day': '화,목', 'time': '2교시(13:00~15:00),3교시(16:00~18:00)'},
    {'region': '파주', 'org': '가짜파주노인복지관', 'addr': '경기도 파주시 가짜로 56',
     'manager': '이담당', 'tel': '031-333-4444', 'day': '월,화,수', 'time': '1교시(10:00~12:00)'},
]

TIME_SLOTS = [
    (10, 0, 12, 0, '1교시(10:00~12:00)'),
    (13, 0, 15, 0, '2교시(13:00~15:00)'),
    (16, 0, 18, 0, '3교시(16:00~18:00)'),
]

WORKLOG_HEADER = [
    '사원번호', '이름', '지점', '직무', '날짜', '요일', '근무유형', '근무일정 템플릿',
    '근무일정\n시작시간', '근무일정\n종료시간', '출근시간', '(시급계산단위 적용)\n출근시간',
    '퇴근시간', '(시급계산단위 적용)\n퇴근시간', '출퇴근기록\n휴게시간', '(계획)\n자동추가된\n휴게시간',
    '(실제)\n(시급계산단위 적용)\n자동추가된\n휴게시간', '(계획)\n총 휴게시간', '(실제)\n총 휴게시간',
    '(실제)\n(시급계산단위 적용)\n총 휴게시간', '(계획)\n총 시간', '(실제)\n총 시간',
    '(실제)\n(시급계산단위 적용)\n총 시간', '(실제)\n강의시간', '주강사\n교육인원', '보조강사\n교육인원',
]


def make_worklog(target_month, out_path):
    year, month = map(int, target_month.split('-'))
    wb = Workbook()
    ws = wb.active
    ws.title = '실급여정산(근무일정 및 출퇴근기록 기반)'

    ws.cell(row=1, column=1, value='routineout 데모(가짜데이터)')
    ws.cell(row=1, column=3, value='야간근로 시작시간')
    ws.cell(row=1, column=4, value='22:00')
    ws.cell(row=2, column=3, value='야간근로 종료시간')
    ws.cell(row=2, column=4, value='06:00')
    for col, label in enumerate(WORKLOG_HEADER, start=1):
        ws.cell(row=3, column=col, value=label)

    weekday_kr = ['월', '화', '수', '목', '금', '토', '일']
    row_idx = 5

    import calendar
    days_in_month = calendar.monthrange(year, month)[1]
    all_dates = [datetime(year, month, d) for d in range(1, days_in_month + 1)]

    # 주(월요일 기준) 단위로 날짜를 묶는다 — 프로필별로 특정 주에 몰아서 배정하기 위함
    weeks = {}
    for dt in all_dates:
        if dt.weekday() == 6:  # 일요일 제외
            continue
        monday = dt - timedelta(days=dt.weekday())
        weeks.setdefault(monday, []).append(dt)
    # 월경계에 걸친 주(월요일이 이번 달이 아닌 주)는 build_payroll_summary의 중복지급
    # 방지 로직(같은 entries를 prior_entries로 재사용)에 걸려 주휴수당이 0으로 지워진다.
    # 데모에서 주휴수당이 확실히 보이도록, 월 안에 완전히 포함된 주만 후보로 쓴다.
    week_list = [weeks[k] for k in sorted(weeks.keys()) if k.month == month]

    # 강사별 근무 프로필: heavy(2명)=4대보험(월 60h+) 시연, medium(4명)=주휴수당(주 15h+) 시연,
    # light(4명)=둘 다 미달 시연 — 세 케이스가 전부 데모에서 보이도록 의도적으로 분포시킴
    profiles = ['heavy', 'heavy', 'medium', 'medium', 'medium', 'medium',
                'light', 'light', 'light', 'light']

    for idx, inst in enumerate(FAKE_INSTRUCTORS):
        profile = profiles[idx % len(profiles)]

        if profile == 'heavy':
            active_weeks = week_list[:min(4, len(week_list))]
            days_per_week = 6
            sessions_per_day = 2
        elif profile == 'medium':
            active_weeks = week_list[:1]
            days_per_week = 6
            sessions_per_day = 2
        else:
            active_weeks = week_list[:min(2, len(week_list))]
            days_per_week = 2
            sessions_per_day = 1

        for wk in active_weeks:
            wk_days = [d for d in wk if d.weekday() < 6]
            chosen_days = wk_days[:days_per_week]
            for date_obj in chosen_days:
                for s in range(sessions_per_day):
                    slot = TIME_SLOTS[s % len(TIME_SLOTS)]
                    sh, sm, eh, em, _ = slot
                    sched_start = date_obj.replace(hour=sh, minute=sm)
                    sched_end = date_obj.replace(hour=eh, minute=em)

                    late_in = random.random() < 0.15
                    early_out = random.random() < 0.15
                    actual_in = sched_start - timedelta(minutes=35) if late_in else sched_start
                    actual_out = sched_end + timedelta(minutes=35) if early_out else sched_end

                    sched_hours = (sched_end - sched_start).total_seconds() / 3600
                    headcount_main = random.randint(12, 28)
                    headcount_sub = random.randint(12, 28)

                    row = [None] * 26
                    row[0] = f'A{idx + 1:03d}'
                    row[1] = inst['name']
                    row[2] = FAKE_CENTERS[idx % len(FAKE_CENTERS)]['org']
                    row[3] = inst['role']
                    row[4] = date_obj
                    row[5] = weekday_kr[date_obj.weekday()]
                    row[6] = '정상근무'
                    row[7] = '기본템플릿'
                    row[8] = sched_start
                    row[9] = sched_end
                    row[10] = actual_in
                    row[11] = actual_in
                    row[12] = actual_out
                    row[13] = actual_out
                    row[14] = timedelta(0)
                    row[15] = timedelta(0)
                    row[16] = timedelta(0)
                    row[17] = timedelta(0)
                    row[18] = timedelta(0)
                    row[19] = timedelta(0)
                    row[20] = timedelta(hours=sched_hours)
                    row[21] = timedelta(hours=sched_hours)
                    row[22] = timedelta(hours=sched_hours)
                    row[23] = timedelta(hours=sched_hours)
                    row[24] = headcount_main
                    row[25] = headcount_sub

                    for col, val in enumerate(row, start=1):
                        ws.cell(row=row_idx, column=col, value=val)
                    row_idx += 1

    wb.save(out_path)


def make_instructor_list(out_path):
    wb = Workbook()
    ws = wb.active
    ws.title = 'Sheet1'
    ws.cell(row=1, column=1, value='성명(생년월일)')
    ws.cell(row=1, column=2, value='이름')
    ws.cell(row=1, column=3, value='연락처')
    for i, inst in enumerate(FAKE_INSTRUCTORS, start=2):
        ws.cell(row=i, column=1, value=f"{inst['name']} ({inst['birth']})")
        ws.cell(row=i, column=2, value=inst['name'])
        ws.cell(row=i, column=3, value=inst['tel'])
    wb.save(out_path)


def make_interview_results(out_path):
    wb = Workbook()
    ws = wb.active
    ws.title = '면접결과 관리표'
    # 실제 파일의 헤더 영역(1~6행)은 병합/장식 위주라 데모에서는 최소 라벨만 채운다.
    ws.cell(row=6, column=15, value='최종합불 여부')
    ws.cell(row=6, column=18, value='기타 우대사항')
    ws.cell(row=6, column=21, value='보정값적용 최종점수')

    bonus_pool = ['청년', '신규강사', '취약계층', '청년/신규강사', '']
    row_idx = 7
    for inst in FAKE_INSTRUCTORS:
        passed = random.random() < 0.8
        ws.cell(row=row_idx, column=15, value=random.choice(bonus_pool))  # O(14, 0-idx)
        ws.cell(row=row_idx, column=16, value='합격' if passed else '불합격')  # P(15)
        ws.cell(row=row_idx, column=18, value=inst['name'])  # R(17)
        ws.cell(row=row_idx, column=21, value=round(random.uniform(70, 98), 1))  # U(20)
        row_idx += 1
    wb.save(out_path)


def seed_db():
    init_db(TENANT)
    conn = get_db(TENANT)

    conn.execute('DELETE FROM responses')
    conn.execute('DELETE FROM instructor_applications')
    for center in FAKE_CENTERS:
        conn.execute('''
            INSERT INTO responses (
                submitted_at, region, org, addr, manager, tel,
                room_count, room_info, device, target, headcount, course,
                recruit, recruit_channel, period, day, time,
                edubus, edubus_addr, edubus_date, etc, confirmed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        ''', (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            center['region'], center['org'], center['addr'], center['manager'], center['tel'],
            '2개', '수용인원: 20명', '노트북, 빔프로젝터', '시니어', '20명',
            '①스마트폰 처음 사용하기, ②스마트폰 첫걸음과 AI 음성검색',
            '자체모집', '현수막', '7월', center['day'], center['time'],
            'N', '', '', '데모용 가짜 데이터'
        ))

    for i, inst in enumerate(FAKE_INSTRUCTORS):
        center = FAKE_CENTERS[i % len(FAKE_CENTERS)]
        days = center['day'].split(',')
        times = center['time'].split(',')
        choices = [{
            'rank': 1,
            'region': center['region'],
            'org': center['org'],
            'days': days,
            'times': times,
        }]
        conn.execute('''
            INSERT INTO instructor_applications (submitted_at, name, tel, role, choices)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            inst['name'], inst['tel'], inst['role'],
            json.dumps(choices, ensure_ascii=False)
        ))

    conn.commit()
    conn.close()


def main():
    random.seed(42)
    tdir = tenant_dir(TENANT)
    pf = paycheck_folder(TENANT)

    make_instructor_list(os.path.join(pf, 'instructor_list.xlsx'))
    make_worklog('2026-06', os.path.join(pf, 'worklog_2026-06.xlsx'))
    make_worklog('2026-07', os.path.join(pf, 'worklog_2026-07.xlsx'))
    make_interview_results(os.path.join(tdir, 'interview_results.xlsx'))
    seed_db()
    print('demo data generated for tenant:', TENANT)


if __name__ == '__main__':
    main()
