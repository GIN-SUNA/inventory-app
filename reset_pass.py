import os, smtplib
from email.message import EmailMessage
from datetime import datetime
from flask import request, redirect, url_for, flash, render_template, Blueprint, current_app, abort
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy import select
from werkzeug.security import generate_password_hash

def _signer():
    secret = os.environ.get("FLASK_SECRET_KEY", "dev-secret-change-me")
    return URLSafeTimedSerializer(secret, salt="inv-sign")

def make_token(payload: dict) -> str:
    return _signer().dumps(payload)

def load_token(token: str, max_age: int):
    try:
        return _signer().loads(token, max_age=max_age)
    except (SignatureExpired, BadSignature):
        return None

SMTP_HOST = os.getenv("SMTP_HOST"); SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER"); SMTP_PASS = os.getenv("SMTP_PASS")
MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@example.com")

def send_email(to, subject, html):
    if not SMTP_HOST:
        print(f"[DEV MAIL] to={to} subject={subject}\n{html}\n"); return
    msg = EmailMessage()
    msg["From"] = MAIL_FROM; msg["To"] = to; msg["Subject"] = subject
    msg.set_content("HTMLメール対応クライアントでご覧ください。")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        if SMTP_USER: s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def send_verification_email(email, name):
    token = make_token({"email": email})
    link = url_for("pw.verify_email", token=token, _external=True)
    html = f"<p>{name} 様</p><p>以下のリンクでメール確認（24h有効）：<a href='{link}'>{link}</a></p>"
    send_email(email, "メールアドレスの確認", html)

def _choose_template(app, candidates):
    for name in candidates:
        path = os.path.join(app.template_folder or "templates", name)
        if os.path.exists(path):
            return name
    abort(404, f"テンプレートが見つかりません: {candidates}")

def register_reset_routes(app, SessionLocal, User):
    bp = Blueprint("pw", __name__)

    @bp.get("/forgot")
    @bp.get("/password/forgot")
    def forgot():
        tpl = _choose_template(app, ["auth_forgot.html","forgot.html","password/forgot.html"])
        return render_template(tpl)

    @bp.post("/forgot")
    @bp.post("/password/forgot")
    def forgot_post():
        email = (request.form.get("email","") or request.form.get("mail","") or request.form.get("username","")).strip().lower()
        flash("入力されたメール宛に案内を送信しました（該当がある場合）。", "info")
        with SessionLocal() as s:
            u = s.execute(select(User).where(User.email == email, User.is_active == True)).scalar_one_or_none()
            if not u: return redirect(url_for("login"))
            token = make_token({"email": email, "ts": datetime.utcnow().isoformat()})
            link = url_for("pw.reset_form", token=token, _external=True)
            html = f"<p>以下のリンクから新しいパスワードを設定してください（1時間有効）。</p><p><a href='{link}'>{link}</a></p>"
            send_email(email, "パスワード再設定", html)
        return redirect(url_for("login"))

    @bp.get("/reset")
    @bp.get("/password/reset")
    def reset_form():
        token = request.args.get("token","")
        data = load_token(token, max_age=60*60)
        if not data:
            flash("リンクが無効または期限切れです。", "error")
            return redirect(url_for("pw.forgot"))
        tpl = _choose_template(app, ["auth_reset.html","reset.html","password/reset.html"])
        return render_template(tpl, token=token)

    @bp.post("/reset")
    @bp.post("/password/reset")
    def reset_submit():
        token = request.form.get("token","")
        data = load_token(token, max_age=60*60)
        if not data:
            flash("リンクが無効または期限切れです。", "error")
            return redirect(url_for("pw.forgot"))

        password = request.form.get("password","")
        confirm  = request.form.get("confirm","") or request.form.get("password_confirm","")
        if not password or password != confirm:
            flash("パスワードが未入力、または一致しません。", "error")
            return redirect(url_for("pw.reset_form", token=token))

        email = data.get("email")
        with SessionLocal() as s:
            u = s.execute(select(User).where(User.email == email, User.is_active == True)).scalar_one_or_none()
            if not u:
                flash("アカウントが見つかりません。", "error")
                return redirect(url_for("login"))
            u.password_hash = generate_password_hash(password)
            u.email_verified = True
            s.commit()

        flash("パスワードを更新しました。ログインしてください。", "success")
        return redirect(url_for("login"))

    @bp.get("/verify-email")
    def verify_email():
        token = request.args.get("token","")
        data = load_token(token, max_age=60*60*24)
        if not data:
            flash("リンクが無効または期限切れです。", "error")
            return redirect(url_for("login"))
        email = data.get("email")
        with SessionLocal() as s:
            u = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if not u:
                flash("アカウントが見つかりません。", "error")
                return redirect(url_for("login"))
            u.email_verified = True
            s.commit()
        flash("メール確認が完了しました。ログインできます。", "success")
        return redirect(url_for("login"))

    app.register_blueprint(bp)
