from flask import Flask, render_template, request, redirect, url_for, flash
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from dotenv import load_dotenv
import os

# .env を読み込み（FLASK_SECRET_KEY / DATABASE_URL を使用）
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///instance/app.sqlite")

def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev")
    app.config["DATABASE_URL"] = DATABASE_URL

    # DB 初期化
    os.makedirs(app.instance_path, exist_ok=True)
    engine = create_engine(app.config["DATABASE_URL"], future=True)

    with engine.begin() as conn:
        # 仕入先
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS suppliers(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          name TEXT UNIQUE NOT NULL
        );
        """)
        # 品目（仕入先IDを追加）
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
        # 現在庫
        conn.exec_driver_sql("""
        CREATE TABLE IF NOT EXISTS stock_levels (
            item_id INTEGER PRIMARY KEY,
            current_qty REAL NOT NULL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items(id)
        );
        """)
        # 入出庫トランザクション（任意で使用）
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
        # 仕入れ記録（登録時に在庫へ自動加算）
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

    # 在庫一覧（仕入先名を結合）＋ フォームに使う仕入先一覧
    @app.get("/")
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
            suppliers = s.execute(text("""
              SELECT id, name FROM suppliers ORDER BY name
            """)).all()
        low = [row for row in items if row.qty <= row.min_qty]
        return render_template("index.html", items=items, suppliers=suppliers, low=low)

    # 仕入先の追加
    @app.post("/suppliers")
    def add_supplier():
        name = request.form.get("supplier_name", "").strip()
        if not name:
            flash("仕入先名を入力してください", "error")
            return redirect(url_for("index"))
        with Session(engine) as s:
            try:
                s.execute(text("INSERT INTO suppliers(name) VALUES(:n)"), {"n": name})
                s.commit()
                flash(f"仕入先「{name}」を追加しました", "success")
            except Exception as e:
                s.rollback()
                flash(f"追加失敗: {e}", "error")
        return redirect(url_for("index"))

    # 品目追加（仕入先ひも付け可）
    @app.post("/items")
    def add_item():
        name = request.form.get("name", "").strip()
        unit = request.form.get("unit", "個").strip()
        min_qty = float(request.form.get("min_qty", 0) or 0)
        supplier_id = request.form.get("supplier_id") or None
        if not name:
            flash("品目名を入力してください", "error")
            return redirect(url_for("index"))
        with Session(engine) as s:
            try:
                s.execute(text("""
                  INSERT INTO items(name, unit, min_qty, supplier_id)
                  VALUES(:n,:u,:m,:sid)
                """), {"n": name, "u": unit, "m": min_qty, "sid": supplier_id})
                s.execute(text("""
                  INSERT OR IGNORE INTO stock_levels(item_id, current_qty)
                  SELECT id, 0 FROM items WHERE name = :n
                """), {"n": name})
                s.commit()
                flash(f"品目「{name}」を追加しました", "success")
            except Exception as e:
                s.rollback()
                flash(f"追加に失敗: {e}", "error")
        return redirect(url_for("index"))

    # 入出庫登録（任意で使用・履歴管理用）
    @app.post("/tx")
    def add_tx():
        item_id = int(request.form["item_id"])
        tx_type = request.form["type"]  # IN / OUT / ADJ
        qty = float(request.form["qty"])
        note = request.form.get("note")
        user = request.form.get("user", "staff")

        delta = qty if tx_type == "IN" else (-qty if tx_type == "OUT" else 0)

        with Session(engine) as s:
            try:
                s.execute(text("""
                    INSERT INTO transactions(type, item_id, qty, note, user)
                    VALUES(:t, :i, :q, :n, :u)
                """), {"t": tx_type, "i": item_id, "q": qty, "n": note, "u": user})

                # 残高更新（ADJ のときは絶対値に合わせる）
                s.execute(text("""
                    INSERT INTO stock_levels(item_id, current_qty)
                    VALUES(:i, :q)
                    ON CONFLICT(item_id) DO UPDATE SET
                      current_qty = stock_levels.current_qty + :d,
                      updated_at = CURRENT_TIMESTAMP
                """), {"i": item_id, "q": qty if tx_type == "ADJ" else delta, "d": 0 if tx_type == "ADJ" else delta})

                if tx_type == "ADJ":
                    s.execute(text("""
                        UPDATE stock_levels
                        SET current_qty = :q, updated_at = CURRENT_TIMESTAMP
                        WHERE item_id = :i
                    """), {"i": item_id, "q": qty})

                s.commit()
                op = {"IN": "入庫", "OUT": "出庫", "ADJ": "調整"}[tx_type]
                flash(f"{op}を登録しました", "success")
            except Exception as e:
                s.rollback()
                flash(f"登録に失敗: {e}", "error")
        return redirect(url_for("index"))

    # 入出庫履歴の表示
    @app.get("/transactions")
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

    # ワンクリック増減（＋/−ボタン）
    @app.post("/bump")
    def bump():
        item_id = int(request.form["item_id"])
        delta = float(request.form["delta"])   # 例: +1 / -1 / +0.1 など
        with Session(engine) as s:
            try:
                s.execute(text("""
                  INSERT INTO stock_levels(item_id, current_qty)
                  VALUES(:i, :q)
                  ON CONFLICT(item_id) DO UPDATE SET
                    current_qty = stock_levels.current_qty + :q,
                    updated_at = CURRENT_TIMESTAMP
                """), {"i": item_id, "q": delta})
                s.commit()
            except Exception as e:
                s.rollback()
                flash(f"在庫更新に失敗: {e}", "error")
        return redirect(url_for("index"))

    # 仕入れ登録（在庫へ自動反映）
    @app.post("/purchase")
    def purchase():
        item_id = int(request.form["p_item_id"])
        supplier_id = request.form.get("p_supplier_id") or None
        qty = float(request.form["p_qty"])
        unit_price = float(request.form["p_unit_price"])
        purchased_at = request.form.get("p_date") or None  # datetime-local 文字列OK
        note = request.form.get("p_note")

        with Session(engine) as s:
            try:
                s.execute(text("""
                  INSERT INTO purchases(purchased_at, item_id, supplier_id, qty, unit_price, note)
                  VALUES(COALESCE(:dt, CURRENT_TIMESTAMP), :i, :sid, :q, :up, :note)
                """), {"dt": purchased_at, "i": item_id, "sid": supplier_id,
                       "q": qty, "up": unit_price, "note": note})

                s.execute(text("""
                  INSERT INTO stock_levels(item_id, current_qty)
                  VALUES(:i, :q)
                  ON CONFLICT(item_id) DO UPDATE SET
                    current_qty = stock_levels.current_qty + :q,
                    updated_at = CURRENT_TIMESTAMP
                """), {"i": item_id, "q": qty})

                s.commit()
                flash("仕入れを登録し在庫を更新しました", "success")
            except Exception as e:
                s.rollback()
                flash(f"登録失敗: {e}", "error")
        return redirect(url_for("index"))

    # 仕入れ履歴の表示
    @app.get("/purchases")
    def purchases_list():
        with Session(engine) as s:
            rows = s.execute(text("""
              SELECT p.id, p.purchased_at,
                     i.name AS item,
                     COALESCE(s.name,'') AS supplier,
                     p.qty, p.unit_price,
                     (p.qty * p.unit_price) AS amount,
                     p.note
              FROM purchases p
              JOIN items i ON i.id = p.item_id
              LEFT JOIN suppliers s ON s.id = p.supplier_id
              ORDER BY p.purchased_at DESC
              LIMIT 200
            """)).all()
        return render_template("purchases.html", rows=rows)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
