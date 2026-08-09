from flask import Flask, abort, render_template, request, redirect, session, url_for
from werkzeug.security import check_password_hash

from local_config import SECRET_KEY, TENANTS

app = Flask(__name__)
app.secret_key = SECRET_KEY


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/<tenant>/admin', methods=['GET', 'POST'])
def tenant_admin_login(tenant):
    if tenant not in TENANTS:
        abort(404)

    if request.method == 'POST':
        config = TENANTS[tenant]
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == config['username'] and check_password_hash(config['password_hash'], password):
            session['tenant'] = tenant
            return redirect(url_for('tenant_dashboard', tenant=tenant))
        return render_template('admin_login.html', tenant=tenant, error='아이디 또는 비밀번호가 올바르지 않습니다.')

    return render_template('admin_login.html', tenant=tenant, error=None)


@app.route('/<tenant>/dashboard')
def tenant_dashboard(tenant):
    if tenant not in TENANTS:
        abort(404)
    if session.get('tenant') != tenant:
        return redirect(url_for('tenant_admin_login', tenant=tenant))
    return render_template('demo_placeholder.html', tenant=tenant)


@app.route('/<tenant>/logout')
def tenant_logout(tenant):
    session.pop('tenant', None)
    return redirect(url_for('tenant_admin_login', tenant=tenant))
