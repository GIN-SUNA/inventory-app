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
    create_engine, Column, Integer, String, Boolean, DateTime, func, select, Index, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, scoped_session, relationship

# ==============================
# 設定
# ==============================
load_dotenv()
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
DATABASE_URL     = os.getenv("DATABASE_URL", "sqlite:///instance/app.sqlite")
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

# --- User モデル ---
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

# --- Supplier モデル ---
class Supplier(Base):
    __tablename__ = "suppliers"
    id      = Column(Integer, primary_key=True)
    name    = Column(String(255), nullable=False)
    contact = Column(String(255))
    email   = Column(String(255))
    created_at = Column(DateTime, server_default=func.now())
    items   = relationship("Item", back_populates="supplier")

# --- Item モデル ---
class Item(Base):
    __tablename__ = "items"
    id      = Column(Integer, primary_key=True)
    name    = Column(String(255), nullable=False)
    sku     = Column(String(100))
    note    = Column(String(255))
    supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    created_at = Column(DateTime, server_default=func.now())
    supplier   = relationship("Supplier", back_populates="items")

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
# ユーティリティ
# ==============================
def choose_template(candidates):
    for name in candidates:
        path = os.path.join(app.template_folder or "templates", name)
        if os.path.exists(path):
            return name
    abort(404, f"テンプレートが見つかりません: {candidates}")

def first_value(form, *keys, default=""):
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
# 初期管理者の自動作成
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
    return render_template(choose_template(["index.html"]))

# --- ログイン（GET） ---
@app.get("/login")
@app.get("/signin")
@app.get("/users/login")
def login():
    tpl = choose_template(["auth_login.html","login.html","signin.html"])
    return render_template(tpl)

# --- ログイン（POST） ---
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
            return redirect(request.path)

        if REQUIRE_EMAIL_VERIFY and not u.email_verified:
            flash("メール確認が未完了のためログインできません。", "warning")
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
    return redirect(url_for("login"))

# --- 新規登録（GET） ---
@app.get("/register")
@app.get("/signup")
@app.get("/users/new")
def register():
    tpl = choose_template(["auth_register.html","register.html","signup.html","users/new.html"])
    return render_template(tpl)

# --- 新規登録（POST） ---
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
        try:
            from reset_pass import send_verification_email
            send_verification_email(email, name)
            flash("仮登録しました。確認メールを送信しました。", "success")
        except Exception:
            flash("仮登録しました。メール確認が必要です。", "success")
    else:
        flash("登録が完了しました。ログインしてください。", "success")

    return redirect(url_for("login"))

# --- パスワードリセット系 ---
from reset_pass import register_reset_routes
register_reset_routes(app, SessionLocal, User)

# --- 仕入れ登録 ---
@app.post("/purchase", endpoint="purchase")
@login_required
def purchase():
    # 将来的に仕入先IDや品目IDなどを保存する拡張も可
    flash("仕入れを登録しました。", "info")
    return redirect(url_for("index"))

@app.post("/add-tx", endpoint="add_tx")
@login_required
def add_tx_alias():
    return purchase()

# --- 仕入先追加 ---
@app.route("/add-supplier", methods=["POST"], endpoint="add_supplier")
@login_required
def add_supplier():
    name    = first_value(request.form, "supplier_name", "name")
    contact = first_value(request.form, "contact")
    email   = first_value(request.form, "email")

    if not name:
        flash("仕入先名は必須です", "error")
        return redirect(url_for("index"))

    with SessionLocal() as s:
        supplier = Supplier(name=name, contact=contact, email=email)
        s.add(supplier)
        s.commit()

    flash(f"仕入先「{name}」を登録しました", "success")
    return redirect(url_for("index"))

# --- 品目追加 ---
@app.post("/add-item", endpoint="add_item")
@login_required
def add_item():
    name  = first_value(request.form, "item_name", "name", "item")
    sku   = first_value(request.form, "sku", "code")
    note  = first_value(request.form, "note", "memo")
    supplier_id = first_value(request.form, "supplier_id")

    if not name:
        flash("品目名は必須です", "error")
        return redirect(url_for("index"))

    with SessionLocal() as s:
        item = Item(name=name, sku=sku, note=note,
                    supplier_id=int(supplier_id) if supplier_id else None)
        s.add(item)
        s.commit()

    flash(f"品目「{name}」を登録しました", "success")
    return redirect(url_for("index"))

# --- 履歴画面 ---
@app.get("/purchases", endpoint="purchases_list")
@login_required
def purchases_page():
    try:
        return render_template(choose_template(["purchases.html","purchases/index.html"]))
    except Exception:
        return render_template(choose_template(["index.html"]))

@app.get("/transactions", endpoint="tx_list")
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
