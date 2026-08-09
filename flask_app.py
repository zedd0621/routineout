from flask import Flask, abort, render_template, request, redirect, session, url_for
from werkzeug.security import check_password_hash

from local_config import SECRET_KEY, TENANTS

app = Flask(__name__)
app.secret_key = SECRET_KEY


@app.route('/')
def index():
    return render_template('index.html')


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


@app.route('/<tenant>/dashboard')
def tenant_dashboard(tenant):
    if tenant not in TENANTS:
        abort(404)
    if session.get('tenant') != tenant:
        return redirect(url_for('login'))
    return render_template('demo_placeholder.html', tenant=tenant)


@app.route('/logout')
def logout():
    session.pop('tenant', None)
    return redirect(url_for('login'))
