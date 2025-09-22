import os
from datetime import timedelta, datetime
from dotenv import load_dotenv

from flask import (
    Flask, render_template, request, redirect, url_for, flash, current_app, abort
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user, login_required, current_user
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, func, select, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session

# ==============================
# 設定
# ==============================
load_dotenv()
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
DATABASE_URL     = os.getenv("DATABASE_URL", "sqlite:///instance/app.sqlite")
# メール確認を将来ONにしたい場合は .env で true に（今は既定 false）
REQUIRE_EMAIL_VERIFY = os.getenv("REQUIRE_EMAIL_VERIFY", "false").lower() == "true"

# ==============================
# アプリ
# ==============================
app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = FLASK_SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)
app.config.update(
    SESSION_COOKIE_SECURE=False if app.debug else True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    REMEMBER_COOKIE_DURATION=timedelta(days=14),
)

# ==============================
# DB
# ==============================
if DATABASE_URL.startswith("sqlite"):
    os.makedirs("instance", exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    future=True,
    echo=False,
)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True))
Base = declarative_base()

class User(UserMixin, Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True)
    name          = Column(String(120), nullable=False)
    email         = Column(String(255), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    role          = Column(String(20),  nullable=False, default="staff")   # admin / staff
    is_active     = Column(Boolean,     nullable=False, default=True)
    email_verified= Column(Boolean,     nullable=False, default=False)
    created_at    = Column(DateTime, server_default=func.now())

    def get_id(self): return str(self.id)
    def set_password(self, raw): self.password_hash = generate_password_hash(raw)
    def check_password(self, raw): return check_password_hash(self.password_hash, raw)

Index("idx_users_email_unique", User.email, unique=True)
Base.metadata.create_all(engine)

# ==============================
# Login 管理
# ==============================
login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id: str):
    with SessionLocal() as s:
        return s.get(User, int(user_id))

app.session_maker = SessionLocal
app.User = User

# ==============================
# ユーティリティ（テンプレ/フォーム差異を吸収）
# ==============================
def choose_template(candidates):
    """候補の中から実在するテンプレートを返す。なければ最初（エラー防止のため存在しなければ abort 404）。"""
    for name in candidates:
        path = os.path.join(app.template_folder or "templates", name)
        if os.path.exists(path):
            return name
    # どれも無ければ 404 にする（テンプレは維持方針なので無理に作らない）
    abort(404, f"テンプレートが見つかりません: {candidates}")

def first_value(form, *keys, default=""):
    """フォームから最初に見つかったキーの値を返す（名前の差異を吸収）。"""
    for k in keys:
        if k in form:
            v = form.get(k, "")
            if isinstance(v, str): v = v.strip()
            return v
    return default

# ==============================
# 役割チェック
# ==============================
from functools import wraps
def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                flash("権限がありません。", "error")
                return redirect(url_for("index"))
            return fn(*args, **kwargs)
        return wrapper
    return deco

# ==============================
# 初期管理者の自動作成（環境変数）
# ==============================
def ensure_admin():
    admin_email = os.getenv("ADMIN_EMAIL")
    admin_pass  = os.getenv("ADMIN_PASSWORD")
    admin_name  = os.getenv("ADMIN_NAME", "Admin")
    if not admin_email or not admin_pass:
        return
    with SessionLocal() as s:
        existing = s.execute(select(User).where(User.email == admin_email.lower())).scalar_one_or_none()
        if existing: return
        u = User(name=admin_name, email=admin_email.lower(), role="admin",
                 is_active=True, email_verified=True)
        u.set_password(admin_pass)
        s.add(u); s.commit()
ensure_admin()

# ==============================
# ページ
# ==============================
@app.get("/")
def index():
    # 既存 index.html をそのまま使う想定
    return render_template(choose_template(["index.html"]))

# --- ログイン（GET）: 既存テンプレを使う ---
@app.get("/login")
@app.get("/signin")
@app.get("/users/login")
def login():
    tpl = choose_template(["auth_login.html","login.html","signin.html"])
    return render_template(tpl)

# --- ログイン（POST）: フォーム名の違いを吸収 ---
@app.post("/login")
@app.post("/signin")
@app.post("/users/login")
def login_post():
    form = request.form
    email = first_value(form, "email","mail","username","user","login","login_id").lower()
    password = first_value(form, "password","pass","pwd")

    remember = bool(first_value(form, "remember","remember_me","keep","keep_me","stay","stay_signed_in","1"))
    with SessionLocal() as s:
        u = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not u or not u.is_active or not u.check_password(password):
            flash("メールまたはパスワードが正しくないか、アカウントが無効です。", "error")
            return redirect(request.path)  # 呼ばれたURLに戻す

        if REQUIRE_EMAIL_VERIFY and not u.email_verified:
            flash("メール確認が未完了のためログインできません。メールのリンクをご確認ください。", "warning")
            return redirect(request.path)

        login_user(u, remember=remember)
        next_url = request.args.get("next") or url_for("index")
        return redirect(next_url)

# --- ログアウト ---
@app.post("/logout")
@app.post("/signout")
def logout():
    if current_user.is_authenticated:
        logout_user()
    flash("ログアウトしました。", "info")
    # ログアウト後にログイン画面へ（どのテンプレでもOK）
    return redirect(url_for("login"))

# --- 新規登録（GET）: 既存テンプレをそのまま ---
@app.get("/register")
@app.get("/signup")
@app.get("/users/new")
def register():
    tpl = choose_template(["auth_register.html","register.html","signup.html","users/new.html"])
    return render_template(tpl)

# --- 新規登録（POST）: 既存フォーム名に合わせて吸収 ---
@app.post("/register")
@app.post("/signup")
@app.post("/users/new")
def register_post():
    form = request.form
    name  = first_value(form, "name","fullname","full_name","user_name","display_name")
    email = first_value(form, "email","mail","username","user","login").lower()
    password = first_value(form, "password","pass","pwd")

    if not name or not email or not password:
        flash("必須項目が未入力です。", "error")
        return redirect(request.path)

    with SessionLocal() as s:
        U = User
        exists = s.execute(select(U).where(U.email == email)).scalar_one_or_none()
        if exists:
            flash("このメールアドレスは既に登録済みです。ログインしてください。", "info")
            return redirect(url_for("login"))

        u = U(name=name, email=email, role="staff", is_active=True,
              email_verified=(not REQUIRE_EMAIL_VERIFY))
        u.set_password(password)
        s.add(u); s.commit()

    if REQUIRE_EMAIL_VERIFY:
        # （将来ONにした時のためのフック：確認メール送信。今はOFF既定）
        try:
            from reset_pass import send_verification_email
            send_verification_email(email, name)
            flash("仮登録しました。確認メールを送信しました。リンクを踏んで登録を完了してください。", "success")
        except Exception:
            # メール未設定でも止まらない
            flash("仮登録しました。メール確認が必要です。", "success")
    else:
        flash("登録が完了しました。ログインしてください。", "success")

    return redirect(url_for("login"))

# --- パスワードリセット系（テンプレ維持、ルートは複数エイリアス） ---
from reset_pass import register_reset_routes
register_reset_routes(app, SessionLocal, User)

# --- 仕入れフォーム（既存 index 側からの action を想定） ---
@app.post("/purchase")
@login_required
def purchase():
    # ここはダミー。既存フォームの name を変えずに受け取れるよう最低限で受理。
    flash("仕入れを受け付けました。（ダミー：後でDB保存を実装）", "info")
    return redirect(url_for("index"))

# 任意：履歴画面（既存テンプレがあれば表示）
@app.get("/purchases")
@login_required
def purchases_page():
    try:
        return render_template(choose_template(["purchases.html","purchases/index.html"]))
    except Exception:
        return render_template(choose_template(["index.html"]))

@app.get("/transactions", endpoint="tx_list")  # ← ここを endpoint="tx_list" に
@login_required
def transactions_page():
    candidates = ["transactions.html", "transactions/index.html"]
    try:
        return render_template(choose_template(candidates))
    except Exception:
        flash("取引一覧テンプレートが見つからなかったため、ダッシュボードに戻りました。", "warning")
        return render_template(choose_template(["index.html"]))

# ==============================
# エントリポイント
# ==============================
if __name__ == "__main__":
    app.run(debug=True)
