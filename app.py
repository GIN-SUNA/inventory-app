from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import os

# .env 読み込み
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///instance/app.sqlite")

# Flask-Login 用の軽量ユーザークラス
class User(UserMixin):
    def __init__(self, id: int, email: str, name: str, role: str):
        self.id = id
        self.email = email
        self.name = name
        self.role = role

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev")
    app.config["DATABASE_URL"] = DATABASE_URL

    # DB 準備
    os.makedirs(app.instance_path, exist_ok=True)
    engine = create_engine(app.config["DATABASE_URL"], future=True)

    with engine.begin() as conn:
        # 認証テーブル
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'staff'
        );
        """)
        # 仕入先
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS suppliers(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT UNIQUE NOT NULL
        );
        """)
        # 品目
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            unit TEXT NOT NULL DEFAULT '個',
            min_qty REAL NOT NULL DEFAULT 0,
            supplier_id INTEGER,
            FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        );
        """)
        # 在庫
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS stock_levels (
            item_id INTEGER PRIMARY KEY,
            current_qty REAL NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(id)
        );
        """)
        # 入出庫（任意）
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT CURRENT_TIMESTAMP,
            type TEXT NOT NULL CHECK (type IN ('IN','OUT','ADJ')),
            item_id INTEGER NOT NULL,
            qty REAL NOT NULL,
            note TEXT,
            user TEXT,
            FOREIGN KEY (item_id) REFERENCES items(id)
        );
        """)
        # 仕入れ
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS purchases(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          purchased_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          item_id INTEGER NOT NULL,
          supplier_id INTEGER,
          qty REAL NOT NULL,
          unit_price REAL NOT NULL,
          note TEXT,
          FOREIGN KEY (item_id) REFERENCES items(id),
          FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
        );
        """)

        # 初回だけ管理者を自動作成（ENV が設定されていて、ユーザーがまだ居ない場合）
        admin_email = os.getenv("ADMIN_EMAIL")
        admin_pass = os.getenv("ADMIN_PASSWORD")
        admin_name = os.getenv("ADMIN_NAME", "Admin")
        if admin_email and admin_pass:
            count = conn.exec_driver_sql("SELECT COUNT(*) FROM users").fetchone()[0]
            if count == 0:
                conn.exec_driver_sql("""
                  INSERT INTO users(email, name, password_hash, role)
                  VALUES(:e, :n, :ph, 'owner')
                """, {"e": admin_email, "n": admin_name,
                      "ph": generate_password_hash(admin_pass)})

    # Flask-Login セットアップ
    login_manager = LoginManager()
    login_manager.login_view = "login"   # 未ログイン時は /login に飛ばす
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        with Session(engine) as s:
            row = s.execute(text("SELECT id, email, name, role FROM users WHERE id = :id"),
                            {"id": int(user_id)}).fetchone()
            if not row:
                return None
            return User(row.id, row.email, row.name, row.role)

    # ===== 認証 ルート =====
    @app.get("/login")
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))
        return render_template("auth_login.html")

    @app.post("/login")
    def login_post():
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with Session(engine) as s:
            row = s.execute(text("SELECT id, email, name, role, password_hash FROM users WHERE lower(email)=:e"),
                            {"e": email}).fetchone()
        if not row or not check_password_hash(row.password_hash, password):
            flash("メールまたはパスワードが違います", "error")
            return redirect(url_for("login"))
        user = User(row.id, row.email, row.name, row.role)
        login_user(user, remember=True)
        flash("ログインしました", "success")
        return redirect(url_for("index"))

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        flash("ログアウトしました", "success")
        return redirect(url_for("login"))

    # （任意）初回だけ有効にしてユーザー作成したい場合に利用
    @app.get("/register")
    def register():
        # すでにユーザーがいれば登録は閉じる（安全のため）
        with Session(engine) as s:
            cnt = s.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
        if cnt > 0:
            flash("登録は無効です。管理者に依頼してください。", "error")
            return redirect(url_for("login"))
        return render_template("auth_register.html")

    @app.post("/register")
    def register_post():
        with Session(engine) as s:
            cnt = s.execute(text("SELECT COUNT(*) FROM users")).scalar_one()
            if cnt > 0:
                flash("登録は無効です。管理者に依頼してください。", "error")
                return redirect(url_for("login"))
        name = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        password = request.form.get("password","")
        if not name or not email or not password:
            flash("すべて入力してください", "error")
            return redirect(url_for("register"))
        with Session(engine) as s:
            try:
                s.execute(text("""
                  INSERT INTO users(email, name, password_hash, role)
                  VALUES(:e,:n,:ph,'owner')
                """), {"e": email, "n": name, "ph": generate_password_hash(password)})
                s.commit()
                flash("ユーザーを作成しました。ログインしてください。", "success")
                return redirect(url_for("login"))
            except Exception as e:
                s.rollback()
                flash(f"登録に失敗: {e}", "error")
                return redirect(url_for("register"))

    # ===== 在庫アプリ ルート =====
    @app.get("/")
    @login_required
    def index():
        with Session(engine) as s:
            items = s.execute(text("""
              SELECT i.id, i.name, i.unit, i.min_qty,
                     COALESCE(sl.current_qty, 0) AS qty,
                     COALESCE(s.name, '') AS supplier
              FROM items i
              LEFT JOIN stock_levels sl ON sl.item_id = i.id
              LEFT JOIN suppliers s ON s.id = i.supplier_id
              ORDER BY i.name
            """)).all()
            suppliers = s.execute(text("SELECT id, name FROM suppliers ORDER BY name")).all()
        low = [row for row in items if row.qty <= row.min_qty]
        return render_template("index.html", items=items, suppliers=suppliers, low=low)

    @app.post("/suppliers")
    @login_required
    def add_supplier():
        name = request.form.get("supplier_name","").strip()
        if not name:
            flash("仕入先名を入力してください","error"); return redirect(url_for("index"))
        with Session(engine) as s:
            try:
                s.execute(text("INSERT INTO suppliers(name) VALUES(:n)"), {"n":name})
                s.commit(); flash(f"仕入先「{name}」を追加しました","success")
            except Exception as e:
                s.rollback(); flash(f"追加失敗: {e}","error")
        return redirect(url_for("index"))

    @app.post("/items")
    @login_required
    def add_item():
        name = request.form.get("name","").strip()
        unit = request.form.get("unit","個").strip()
        min_qty = float(request.form.get("min_qty",0) or 0)
        supplier_id = request.form.get("supplier_id") or None
        if not name:
            flash("品目名を入力してください", "error")
            return redirect(url_for("index"))
        with Session(engine) as s:
            try:
                s.execute(text("""
                  INSERT INTO items(name, unit, min_qty, supplier_id)
                  VALUES(:n,:u,:m,:sid)
                """), {"n":name,"u":unit,"m":min_qty,"sid":supplier_id})
                s.execute(text("""
                  INSERT OR IGNORE INTO stock_levels(item_id, current_qty)
                  SELECT id, 0 FROM items WHERE name=:n
                """), {"n":name})
                s.commit(); flash(f"品目「{name}」を追加しました","success")
            except Exception as e:
                s.rollback(); flash(f"追加に失敗: {e}","error")
        return redirect(url_for("index"))

    @app.post("/bump")
    @login_required
    def bump():
        item_id = int(request.form["item_id"])
        delta = float(request.form["delta"])
        with Session(engine) as s:
            try:
                s.execute(text("""
                  INSERT INTO stock_levels(item_id,current_qty)
                  VALUES(:i,:q)
                  ON CONFLICT(item_id) DO UPDATE SET
                    current_qty = stock_levels.current_qty + :q,
                    updated_at = CURRENT_TIMESTAMP
                """), {"i":item_id,"q":delta})
                s.commit()
            except Exception as e:
                s.rollback(); flash(f"在庫更新に失敗: {e}","error")
        return redirect(url_for("index"))

    @app.post("/purchase")
    @login_required
    def purchase():
        item_id = int(request.form["p_item_id"])
        supplier_id = request.form.get("p_supplier_id") or None
        qty = float(request.form["p_qty"])
        unit_price = float(request.form["p_unit_price"])
        purchased_at = request.form.get("p_date") or None
        note = request.form.get("p_note")
        with Session(engine) as s:
            try:
                s.execute(text("""
                  INSERT INTO purchases(purchased_at,item_id,supplier_id,qty,unit_price,note)
                  VALUES(COALESCE(:dt,CURRENT_TIMESTAMP),:i,:sid,:q,:up,:note)
                """), {"dt":purchased_at,"i":item_id,"sid":supplier_id,"q":qty,"up":unit_price,"note":note})
                s.execute(text("""
                  INSERT INTO stock_levels(item_id,current_qty)
                  VALUES(:i,:q)
                  ON CONFLICT(item_id) DO UPDATE SET
                    current_qty = stock_levels.current_qty + :q,
                    updated_at = CURRENT_TIMESTAMP
                """), {"i":item_id,"q":qty})
                s.commit(); flash("仕入れを登録し在庫を更新しました","success")
            except Exception as e:
                s.rollback(); flash(f"登録失敗: {e}","error")
        return redirect(url_for("index"))

    @app.post("/tx")
    @login_required
    def add_tx():
        item_id = int(request.form["item_id"])
        tx_type = request.form["type"]  # IN/OUT/ADJ
        qty = float(request.form["qty"])
        note = request.form.get("note")
        user = request.form.get("user", current_user.name if current_user.is_authenticated else "staff")
        delta = qty if tx_type == "IN" else (-qty if tx_type == "OUT" else 0)
        with Session(engine) as s:
            try:
                s.execute(text("""
                    INSERT INTO transactions(type, item_id, qty, note, user)
                    VALUES(:t,:i,:q,:n,:u)
                """), {"t":tx_type,"i":item_id,"q":qty,"n":note,"u":user})
                s.execute(text("""
                    INSERT INTO stock_levels(item_id,current_qty)
                    VALUES(:i,:q)
                    ON CONFLICT(item_id) DO UPDATE SET
                      current_qty = stock_levels.current_qty + :d,
                      updated_at = CURRENT_TIMESTAMP
                """), {"i":item_id,"q":qty if tx_type=="ADJ" else delta, "d":0 if tx_type=="ADJ" else delta})
                if tx_type == "ADJ":
                    s.execute(text("""
                        UPDATE stock_levels SET current_qty=:q, updated_at=CURRENT_TIMESTAMP
                        WHERE item_id=:i
                    """), {"i":item_id,"q":qty})
                s.commit()
                op = {"IN":"入庫","OUT":"出庫","ADJ":"調整"}[tx_type]
                flash(f"{op}を登録しました","success")
            except Exception as e:
                s.rollback(); flash(f"登録に失敗: {e}","error")
        return redirect(url_for("index"))

    @app.get("/transactions")
    @login_required
    def tx_list():
        with Session(engine) as s:
            data = s.execute(text("""
                SELECT t.id, t.ts, t.type, i.name, t.qty, t.note, t.user
                FROM transactions t
                JOIN items i ON i.id = t.item_id
                ORDER BY t.ts DESC
                LIMIT 200
            """)).all()
        return render_template("transactions.html", rows=data)

    @app.get("/purchases")
    @login_required
    def purchases_list():
        with Session(engine) as s:
            rows = s.execute(text("""
              SELECT p.id, p.purchased_at, i.name AS item, COALESCE(s.name,'') AS supplier,
                     p.qty, p.unit_price, (p.qty*p.unit_price) AS amount, p.note
              FROM purchases p
              JOIN items i ON i.id=p.item_id
              LEFT JOIN suppliers s ON s.id=p.supplier_id
              ORDER BY p.purchased_at DESC
              LIMIT 200
            """)).all()
        return render_template("purchases.html", rows=rows)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
