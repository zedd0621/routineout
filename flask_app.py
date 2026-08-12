import csv
import io
import json
from datetime import datetime
from urllib.parse import quote

from flask import Flask, abort, render_template, request, redirect, session, url_for, Response
from werkzeug.security import check_password_hash

from local_config import SECRET_KEY, TENANTS, OWNER_PASSWORD_HASH
from tenant_db import get_db, init_db, paycheck_folder
from masking import mask_name, mask_birth, mask_phone
import payroll_engine as pay
import recommendation_engine as rec
import leads_db

app = Flask(__name__)
app.secret_key = SECRET_KEY

for _slug in TENANTS:
    init_db(_slug)
leads_db.init_db()


def require_tenant(tenant):
    """미등록 테넌트는 404, 로그인 안 된 세션은 /login으로. 통과 시 None 반환."""
    if tenant not in TENANTS:
        abort(404)
    if session.get('tenant') != tenant:
        return redirect(url_for('login'))
    return None


@app.route('/')
def marketing_home():
    return render_template('marketing_home.html')


@app.route('/philosophy')
def marketing_philosophy():
    return render_template('marketing_philosophy.html')


@app.route('/live-demo')
def marketing_live_demo():
    return render_template('marketing_live_demo.html')


@app.route('/pricing')
def marketing_pricing():
    return render_template('marketing_pricing.html')


@app.route('/apply', methods=['GET', 'POST'])
def marketing_apply():
    if request.method == 'POST':
        conn = leads_db.get_db()
        conn.execute(
            'INSERT INTO leads (submitted_at, name, phone, email, services, org_size, usage_pref, message) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
             request.form.get('name', ''), request.form.get('phone', ''), request.form.get('email', ''),
             ', '.join(request.form.getlist('services')),
             request.form.get('org_size', ''), request.form.get('usage_pref', ''),
             request.form.get('message', ''))
        )
        conn.commit()
        conn.close()
        return render_template('marketing_apply_done.html')
    return render_template('marketing_apply.html')


@app.route('/leads', methods=['GET', 'POST'])
def leads():
    if not session.get('owner_logged_in'):
        if request.method == 'POST':
            if check_password_hash(OWNER_PASSWORD_HASH, request.form.get('password', '')):
                session['owner_logged_in'] = True
                return redirect(url_for('leads'))
            return render_template('leads_login.html', error='비밀번호가 일치하지 않습니다.')
        return render_template('leads_login.html', error=None)

    conn = leads_db.get_db()
    rows = conn.execute('SELECT * FROM leads ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('leads.html', rows=rows)


@app.route('/leads/download')
def leads_download():
    if not session.get('owner_logged_in'):
        return redirect(url_for('leads'))
    conn = leads_db.get_db()
    rows = conn.execute('SELECT * FROM leads ORDER BY id DESC').fetchall()
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', '신청일시', '이름', '연락처', '이메일', '관심서비스', '관리규모', '이용방식', '문의내용'])
    for r in rows:
        writer.writerow([r['id'], r['submitted_at'], r['name'], r['phone'], r['email'],
                          r['services'], r['org_size'], r['usage_pref'], r['message']])
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=routineout_leads.csv'}
    )


@app.route('/leads/logout')
def leads_logout():
    session.pop('owner_logged_in', None)
    return redirect(url_for('leads'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        for slug, config in TENANTS.items():
            if username == config['username'] and check_password_hash(config['password_hash'], password):
                session['tenant'] = slug
                return redirect(url_for('tenant_dashboard', tenant=slug))
        return render_template('login.html', error='아이디 또는 비밀번호가 올바르지 않습니다.')

    return render_template('login.html', error=None)


@app.route('/logout')
def logout():
    session.pop('tenant', None)
    return redirect(url_for('login'))


@app.route('/<tenant>/dashboard')
def tenant_dashboard(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard
    return render_template('dashboard.html', tenant=tenant)


# ── 센터 문의 접수 ────────────────────────────────────────────

@app.route('/<tenant>/center', methods=['GET', 'POST'])
def center_form(tenant):
    if tenant not in TENANTS:
        abort(404)
    if request.method == 'POST':
        f = request.form
        conn = get_db(tenant)
        conn.execute('''
            INSERT INTO responses (
                submitted_at, region, org, addr, manager, tel,
                room_count, room_info, device, target, headcount, course,
                recruit, recruit_channel, period, day, time,
                edubus, edubus_addr, edubus_date, etc, confirmed
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
        ''', (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            f.get('region', ''), f.get('org', ''), f.get('addr', ''),
            f.get('manager', ''), f.get('tel', ''),
            f.get('room_count', ''), f.get('room_info', ''),
            ', '.join(request.form.getlist('device')),
            ', '.join(request.form.getlist('target')),
            f.get('headcount', ''),
            ', '.join(request.form.getlist('course')),
            f.get('recruit', ''), f.get('recruit_channel', ''),
            ', '.join(request.form.getlist('period')),
            ', '.join(request.form.getlist('day')),
            ', '.join(request.form.getlist('time')),
            f.get('edubus', ''), f.get('edubus_addr', ''),
            f.get('edubus_date', ''), f.get('etc', '')
        ))
        conn.commit()
        conn.close()
        return render_template('done.html', tenant=tenant, message='신청이 접수되었습니다.')

    return render_template('center_form.html', tenant=tenant,
                            regions=rec.REGIONS, all_days=rec.ALL_DAYS,
                            all_times=rec.ALL_TIMES, all_months=rec.ALL_MONTHS,
                            courses_basic=rec.COURSES_BASIC,
                            courses_life=rec.COURSES_LIFE,
                            courses_deep=rec.COURSES_DEEP,
                            courses_special_kids=rec.COURSES_SPECIAL_KIDS,
                            courses_special_entrepreneur=rec.COURSES_SPECIAL_ENTREPRENEUR,
                            courses_special_youth=rec.COURSES_SPECIAL_YOUTH,
                            courses_special_disabled=rec.COURSES_SPECIAL_DISABLED,
                            courses_special_job=rec.COURSES_SPECIAL_JOB)


@app.route('/<tenant>/center-admin', methods=['GET'])
def center_admin(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard
    conn = get_db(tenant)
    rows = conn.execute('SELECT * FROM responses ORDER BY id DESC').fetchall()
    conn.close()
    return render_template('center_admin.html', tenant=tenant, rows=rows, regions=rec.REGIONS)


@app.route('/<tenant>/center-admin/add', methods=['POST'])
def center_admin_add(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard
    f = request.form
    conn = get_db(tenant)
    conn.execute('''
        INSERT INTO responses (
            submitted_at, region, org, addr, manager, tel,
            room_count, room_info, device, target, headcount, course,
            recruit, recruit_channel, period, day, time,
            edubus, edubus_addr, edubus_date, etc, confirmed
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
    ''', (
        datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ' (수기)',
        f.get('region', ''), f.get('org', ''), f.get('addr', ''),
        f.get('manager', ''), f.get('tel', ''),
        f.get('room_count', ''), f.get('room_info', ''),
        f.get('device', ''), f.get('target', ''), f.get('headcount', ''),
        f.get('course', ''), f.get('recruit', ''), f.get('recruit_channel', ''),
        f.get('period', ''), f.get('day', ''), f.get('time', ''),
        f.get('edubus', ''), f.get('edubus_addr', ''),
        f.get('edubus_date', ''), f.get('etc', '')
    ))
    conn.commit()
    conn.close()
    return redirect(url_for('center_admin', tenant=tenant))


@app.route('/<tenant>/center-admin/download')
def center_admin_download(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard
    conn = get_db(tenant)
    c = conn.execute('SELECT * FROM responses ORDER BY id DESC')
    rows = c.fetchall()
    col_names = rows[0].keys() if rows else []
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for r in rows:
        writer.writerow([r[k] for k in col_names])
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={tenant}_centers.csv'}
    )


@app.route('/<tenant>/center-admin/confirm/<int:row_id>', methods=['POST'])
def center_admin_confirm(tenant, row_id):
    guard = require_tenant(tenant)
    if guard:
        return guard
    conn = get_db(tenant)
    cur = conn.execute('SELECT confirmed FROM responses WHERE id=?', (row_id,)).fetchone()
    if cur:
        conn.execute('UPDATE responses SET confirmed=? WHERE id=?',
                      (0 if cur['confirmed'] == 1 else 1, row_id))
        conn.commit()
    conn.close()
    return redirect(url_for('center_admin', tenant=tenant))


@app.route('/<tenant>/center-admin/delete/<int:row_id>', methods=['POST'])
def center_admin_delete(tenant, row_id):
    guard = require_tenant(tenant)
    if guard:
        return guard
    conn = get_db(tenant)
    conn.execute('DELETE FROM responses WHERE id=?', (row_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('center_admin', tenant=tenant))


# ── 강사 지원 ─────────────────────────────────────────────────

@app.route('/<tenant>/instructor', methods=['GET', 'POST'])
def instructor_form(tenant):
    if tenant not in TENANTS:
        abort(404)
    if request.method == 'POST':
        f = request.form
        choices = []
        for i in range(1, 6):
            region = f.get(f'region_{i}', '')
            org = f.get(f'org_{i}', '')
            days = request.form.getlist(f'day_{i}')
            times = request.form.getlist(f'time_{i}')
            if region and org:
                choices.append({'rank': i, 'region': region, 'org': org, 'days': days, 'times': times})

        conn = get_db(tenant)
        conn.execute('''
            INSERT INTO instructor_applications (submitted_at, name, tel, role, choices)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            f.get('name', ''), f.get('tel', ''), f.get('role', ''),
            json.dumps(choices, ensure_ascii=False)
        ))
        conn.commit()
        conn.close()
        return render_template('done.html', tenant=tenant, message='지원이 접수되었습니다.')

    centers_data = rec.build_centers_data(tenant)
    today_counts, window_label = rec.get_today_new_counts_by_region(tenant)

    return render_template('instructor_form.html', tenant=tenant,
                            regions=rec.REGIONS,
                            centers_json=json.dumps(centers_data, ensure_ascii=False),
                            today_counts=today_counts,
                            window_label=window_label,
                            all_days=rec.ALL_DAYS, all_times=rec.ALL_TIMES)


@app.route('/<tenant>/instructor-admin', methods=['GET'])
def instructor_admin(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard

    conn = get_db(tenant)
    rows = conn.execute('SELECT * FROM instructor_applications ORDER BY id DESC').fetchall()
    conn.close()

    pf = paycheck_folder(tenant)
    payroll_rows, _, _ = pay.build_payroll_summary(pf, pay.get_available_months(pf))
    payroll_by_name = {r['name']: r for r in payroll_rows}

    parsed_rows = []
    region_counts = {}
    role_counts = {}
    insurance_counts = {'4대보험': 0, '2대보험': 0, '미매칭': 0}

    for row in rows:
        choices = json.loads(row['choices']) if row['choices'] else []
        first_choice = next((c for c in choices if str(c.get('rank', '')) == '1'), None)
        primary_region = first_choice.get('region', '') if first_choice else (
            choices[0].get('region', '') if choices else ''
        )
        p = payroll_by_name.get(row['name'])
        p_masked = None
        if p:
            p_masked = dict(p)
            ins_type = p['insurance_type']
            p_masked['insurance_type'] = '4대보험' if ins_type == '4대보험' else '2대보험'

        parsed_rows.append({
            'id': row['id'],
            'submitted_at': row['submitted_at'],
            'name': mask_name(row['name']),
            'tel': mask_phone(row['tel']),
            'role': row['role'],
            'choices': choices,
            'primary_region': primary_region,
            'pay': p_masked,
        })

        if primary_region:
            region_counts[primary_region] = region_counts.get(primary_region, 0) + 1
        if row['role']:
            role_counts[row['role']] = role_counts.get(row['role'], 0) + 1
        if p_masked:
            insurance_counts[p_masked['insurance_type']] = insurance_counts.get(p_masked['insurance_type'], 0) + 1
        else:
            insurance_counts['미매칭'] += 1

    return render_template('instructor_admin.html', tenant=tenant, rows=parsed_rows,
                            region_counts=sorted(region_counts.items(), key=lambda x: -x[1]),
                            role_counts=sorted(role_counts.items(), key=lambda x: -x[1]),
                            insurance_counts=insurance_counts)


@app.route('/<tenant>/instructor-admin/download')
def instructor_admin_download(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard
    conn = get_db(tenant)
    rows = conn.execute('SELECT * FROM instructor_applications ORDER BY id DESC').fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', '제출일시', '이름', '연락처', '강사구분', '지망순위', '지역', '센터명', '요일', '시간'])
    for row in rows:
        choices = json.loads(row['choices']) if row['choices'] else []
        if not choices:
            writer.writerow([row['id'], row['submitted_at'], row['name'], row['tel'], row['role'], '', '', '', '', ''])
        for ch in choices:
            writer.writerow([
                row['id'], row['submitted_at'], row['name'], row['tel'], row['role'],
                ch.get('rank', ''), ch.get('region', ''), ch.get('org', ''),
                ', '.join(ch.get('days', [])), ', '.join(ch.get('times', []))
            ])
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={tenant}_instructor_applications.csv'}
    )


@app.route('/<tenant>/instructor-admin/delete/<int:row_id>', methods=['POST'])
def instructor_admin_delete(tenant, row_id):
    guard = require_tenant(tenant)
    if guard:
        return guard
    conn = get_db(tenant)
    conn.execute('DELETE FROM instructor_applications WHERE id=?', (row_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('instructor_admin', tenant=tenant))


# ── 배치 매칭 추천 ────────────────────────────────────────────

@app.route('/<tenant>/recommendation', methods=['GET'])
def recommendation(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard

    conn = get_db(tenant)
    confirmed_centers = conn.execute(
        "SELECT region, org FROM responses WHERE confirmed=1 ORDER BY region, org"
    ).fetchall()
    conn.close()

    region_centers = {}
    for row in confirmed_centers:
        region_centers.setdefault(row['region'], []).append(row['org'])

    selected_region = request.args.get('region', '')
    selected_org = request.args.get('org', '')
    center_info = None
    center_req = None
    rec_results = []

    if selected_region and selected_org:
        center_info, center_req, rec_results = rec.build_recommendation(tenant, selected_region, selected_org)
        for r in rec_results:
            r['name'] = mask_name(r['name'])
            r['tel'] = mask_phone(r['tel'])

    return render_template('recommendation.html', tenant=tenant,
                            region_centers=region_centers,
                            region_centers_json=json.dumps(region_centers, ensure_ascii=False),
                            selected_region=selected_region, selected_org=selected_org,
                            center_info=center_info, center_req=center_req, rec_results=rec_results)


# ── 급여내역 확인 (강사 본인 조회) ──────────────────────────────

@app.route('/<tenant>/paycheck', methods=['GET', 'POST'])
def paycheck(tenant):
    if tenant not in TENANTS:
        abort(404)
    pf = paycheck_folder(tenant)
    available_months = pay.get_available_months(pf)

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        birth = request.form.get('birth', '').strip()
        tel_last4 = request.form.get('tel_last4', '').strip()
        target_month = request.form.get('target_month', '').strip()

        instructor_list = pay.load_instructor_list(pf)
        matched = instructor_list.get(name)

        error = None
        if not matched:
            error = '입력하신 정보와 일치하는 강사를 찾을 수 없습니다. 이름을 다시 확인해 주세요.'
        elif pay.normalize_birth(birth) != pay.normalize_birth(matched['birth']):
            error = '생년월일이 일치하지 않습니다.'
        elif not matched['tel'].endswith(tel_last4) or len(tel_last4) != 4:
            error = '연락처 뒤 4자리가 일치하지 않습니다.'

        if error:
            return render_template('paycheck.html', tenant=tenant, available_months=available_months,
                                    error=error, form_data=request.form)

        worklog = pay.load_worklog(pf, target_month)
        entries = worklog.get(name, [])
        if not entries:
            error = f'{target_month} 근무 기록을 찾을 수 없습니다.'
            return render_template('paycheck.html', tenant=tenant, available_months=available_months,
                                    error=error, form_data=request.form)

        daily_total = sum(e['daily_pay'] for e in entries)
        total_hours = sum(e['total_hours'] for e in entries)
        total_lecture = sum(e['lecture_hours'] for e in entries)

        prior_entries = pay.load_worklog(pf, pay.prev_month_str(target_month)).get(name, [])
        weekly_rows, weekly_total = pay.calc_weekly_holiday(entries, target_month, prior_entries=prior_entries)
        total_pay = daily_total + weekly_total

        is_four, ins_reasons, month_sched_hours = pay.judge_insurance(entries, target_month)
        insurance_type = '4대보험' if is_four else '2대보험(고용·산재)'

        snapshot = {
            'entries': entries, 'weekly': weekly_rows, 'weekly_total': weekly_total,
            'daily_total': daily_total, 'insurance_type': insurance_type,
            'insurance_reasons': ins_reasons,
        }

        return render_template('paycheck.html', tenant=tenant, available_months=available_months,
                                result=True, name=name, birth=birth, tel_last4=tel_last4,
                                target_month=target_month, entries=entries, daily_total=daily_total,
                                weekly_rows=weekly_rows, weekly_total=weekly_total,
                                insurance_type=insurance_type, ins_reasons=ins_reasons,
                                month_sched_hours=month_sched_hours,
                                snapshot_json=json.dumps(snapshot, ensure_ascii=False),
                                total_pay=total_pay, total_hours=round(total_hours, 1),
                                total_lecture=round(total_lecture, 1))

    return render_template('paycheck.html', tenant=tenant, available_months=available_months, error=None)


@app.route('/<tenant>/paycheck/consent', methods=['POST'])
def paycheck_consent(tenant):
    if tenant not in TENANTS:
        abort(404)
    f = request.form
    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    conn = get_db(tenant)
    conn.execute('''
        INSERT INTO paycheck_consents
        (consented_at, name, birth, tel_last4, target_month, total_pay,
         ip_address, snapshot_json, bank_name, account_number, insurance_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        f.get('name', ''), f.get('birth', ''), f.get('tel_last4', ''),
        f.get('target_month', ''), int(f.get('total_pay', 0) or 0),
        ip, f.get('snapshot_json', ''),
        f.get('bank_name', ''), f.get('account_number', ''), f.get('insurance_type', '')
    ))
    conn.commit()
    conn.close()
    return render_template('paycheck_done.html', tenant=tenant, name=f.get('name', ''),
                            target_month=f.get('target_month', ''), total_pay=f.get('total_pay', ''))


@app.route('/<tenant>/paycheck-admin', methods=['GET'])
def paycheck_admin(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard

    pf = paycheck_folder(tenant)
    available_months = pay.get_available_months(pf)
    instructor_count = len(pay.load_instructor_list(pf))

    conn = get_db(tenant)
    consents = conn.execute('SELECT * FROM paycheck_consents ORDER BY id DESC').fetchall()
    conn.close()

    masked_consents = []
    for c in consents:
        d = dict(c)
        d['name'] = mask_name(d['name'])
        masked_consents.append(d)

    payroll_target = available_months[:1]
    payroll_rows, payroll_totals, payroll_months = pay.build_payroll_summary(pf, payroll_target)
    for r in payroll_rows:
        r['name'] = mask_name(r['name'])

    return render_template('paycheck_admin.html', tenant=tenant,
                            available_months=available_months, consents=masked_consents,
                            instructor_count=instructor_count,
                            payroll_rows=payroll_rows, payroll_totals=payroll_totals,
                            payroll_months=payroll_months)


@app.route('/<tenant>/paycheck-admin/download')
def paycheck_admin_download(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard
    conn = get_db(tenant)
    c = conn.execute('SELECT * FROM paycheck_consents ORDER BY id DESC')
    rows = c.fetchall()
    col_names = rows[0].keys() if rows else []
    conn.close()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    for r in rows:
        writer.writerow([r[k] for k in col_names])
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={tenant}_paycheck_consents.csv'}
    )


# ── 급여명세서 (강사 본인 발급 + 관리자 열람) ──────────────────────

@app.route('/<tenant>/payslip', methods=['GET', 'POST'])
def payslip(tenant):
    if tenant not in TENANTS:
        abort(404)
    pf = paycheck_folder(tenant)
    available_months = pay.get_available_months(pf)

    if request.method == 'GET':
        return render_template('payslip.html', tenant=tenant, available_months=available_months, error=None)

    name = request.form.get('name', '').strip()
    birth_raw = request.form.get('birth', '').strip()
    tel_last4 = request.form.get('tel_last4', '').strip()
    target_month = request.form.get('target_month', '').strip()

    instructor_list = pay.load_instructor_list(pf)
    matched = instructor_list.get(name)

    error = None
    if not matched:
        error = '입력하신 정보와 일치하는 강사를 찾을 수 없습니다.'
    elif pay.normalize_birth(birth_raw) != pay.normalize_birth(matched['birth']):
        error = '생년월일이 일치하지 않습니다.'
    elif not matched['tel'].endswith(tel_last4) or len(tel_last4) != 4:
        error = '연락처 뒤 4자리가 일치하지 않습니다.'
    elif not target_month:
        error = '조회할 월을 선택해 주세요.'

    if error:
        return render_template('payslip.html', tenant=tenant, available_months=available_months,
                                error=error, form_data=request.form)

    pdf_buf = _build_payslip_pdf(tenant, name, target_month, matched['birth'])
    if pdf_buf is None:
        error = f'{target_month} 근무 기록을 찾을 수 없습니다.'
        return render_template('payslip.html', tenant=tenant, available_months=available_months,
                                error=error, form_data=request.form)

    return _payslip_response(pdf_buf, name, target_month)


def _build_payslip_pdf(tenant, name, target_month, birth):
    pf = paycheck_folder(tenant)
    worklog = pay.load_worklog(pf, target_month)
    entries = worklog.get(name, [])
    if not entries:
        return None

    base_total = sum(int(e.get('base_pay', 0)) + int(e.get('early_bonus', 0))
                      + int(e.get('late_bonus', 0)) for e in entries)
    lecture_total = sum(int(e.get('lecture_pay', 0)) + int(e.get('headcount_bonus', 0))
                         for e in entries)
    prior = pay.load_worklog(pf, pay.prev_month_str(target_month)).get(name, [])
    _, weekly_total = pay.calc_weekly_holiday(entries, target_month, prior_entries=prior)

    total_hours = round(sum(
        e.get('sched_hours', 0) + (0.5 if e.get('early_bonus', 0) else 0)
        + (0.5 if e.get('late_bonus', 0) else 0) for e in entries), 1)
    lecture_hours = round(sum(e.get('lecture_hours', 0) for e in entries), 1)

    total_pay = base_total + lecture_total + weekly_total
    age_exempt = pay.is_employment_insurance_exempt_by_age(birth, target_month)
    insurance_deduction = 0 if age_exempt else int((total_pay * 0.009) // 10 * 10)

    conn = get_db(tenant)
    row = conn.execute(
        'SELECT bank_name, account_number FROM paycheck_consents WHERE name=? AND target_month=? ORDER BY id DESC LIMIT 1',
        (name, target_month)
    ).fetchone()
    conn.close()
    bank_name, account_number = (row['bank_name'], row['account_number']) if row else (None, None)

    return pay.generate_payslip_pdf('routineout (데모)', name, target_month, base_total, lecture_total,
                                     weekly_total, total_hours, lecture_hours,
                                     bank_name, account_number, insurance_deduction)


def _payslip_response(pdf_buf, name, target_month):
    fname = f'{target_month}_급여명세서_{name}.pdf'
    fname_encoded = quote(fname)
    return Response(
        pdf_buf.read(),
        mimetype='application/pdf',
        headers={'Content-Disposition': f"attachment; filename=payslip.pdf; filename*=UTF-8''{fname_encoded}"}
    )


@app.route('/<tenant>/payslip-admin', methods=['GET'])
def payslip_admin(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard

    pf = paycheck_folder(tenant)
    available_months = pay.get_available_months(pf)
    instructor_list = pay.load_instructor_list(pf)

    # 근무기록이 있는 모든 인원 = 급여명세서를 신청했다고 가정
    applicants = set()
    for m in available_months:
        applicants |= set(pay.load_worklog(pf, m).keys())
    applicants = sorted(applicants)

    rows = []
    for nm in applicants:
        info = instructor_list.get(nm, {})
        rows.append({
            'name_masked': mask_name(nm),
            'name_raw': nm,
            'birth_masked': mask_birth(info.get('birth', '')),
            'tel_masked': mask_phone(info.get('tel', '')),
        })

    selected_name = request.args.get('name', '')
    selected_month = request.args.get('month', available_months[0] if available_months else '')
    detail = None
    if selected_name in applicants and selected_month:
        worklog = pay.load_worklog(pf, selected_month)
        entries = worklog.get(selected_name, [])
        if entries:
            base_total = sum(int(e.get('base_pay', 0)) + int(e.get('early_bonus', 0))
                              + int(e.get('late_bonus', 0)) for e in entries)
            lecture_total = sum(int(e.get('lecture_pay', 0)) + int(e.get('headcount_bonus', 0))
                                 for e in entries)
            prior = pay.load_worklog(pf, pay.prev_month_str(selected_month)).get(selected_name, [])
            _, weekly_total = pay.calc_weekly_holiday(entries, selected_month, prior_entries=prior)
            total_pay = base_total + lecture_total + weekly_total
            detail = {
                'name_masked': mask_name(selected_name),
                'month': selected_month,
                'base_total': base_total,
                'lecture_total': lecture_total,
                'weekly_total': weekly_total,
                'total_pay': total_pay,
                'entry_count': len(entries),
            }

    return render_template('payslip_admin.html', tenant=tenant, rows=rows,
                            available_months=available_months,
                            selected_name=selected_name, selected_month=selected_month,
                            detail=detail)


@app.route('/<tenant>/payslip-admin/download', methods=['GET'])
def payslip_admin_download(tenant):
    guard = require_tenant(tenant)
    if guard:
        return guard

    name = request.args.get('name', '')
    target_month = request.args.get('month', '')
    pf = paycheck_folder(tenant)
    instructor_list = pay.load_instructor_list(pf)
    matched = instructor_list.get(name)
    if not matched:
        abort(404)

    pdf_buf = _build_payslip_pdf(tenant, name, target_month, matched['birth'])
    if pdf_buf is None:
        abort(404)
    return _payslip_response(pdf_buf, name, target_month)
