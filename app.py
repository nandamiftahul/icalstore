import io
import os
import random
import string
from decimal import Decimal
from datetime import datetime

from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session
from flask_login import login_user, logout_user, login_required, current_user
from barcode import Code128
from barcode.writer import ImageWriter

from config import Config
from extensions import db, login_manager
from models import User, Product, StockIn, Reseller, ResellerInventory, Sale, SaleItem

from dotenv import load_dotenv
load_dotenv()

from datetime import datetime, timedelta
from sqlalchemy import func

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()
        seed_admin_if_empty()

    return app


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def seed_admin_if_empty():
    # Buat admin default kalau belum ada user sama sekali
    if User.query.count() == 0:
        admin = User(username="admin", role="admin", is_active=True)
        admin.set_password(os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123"))
        db.session.add(admin)
        db.session.commit()


def require_admin():
    if not current_user.is_authenticated or current_user.role != "admin":
        flash("Akses ditolak. Admin only.", "danger")
        return redirect(url_for("dashboard"))
    return None


def gen_ref(prefix="TRX"):
    s = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{datetime.utcnow().strftime('%y%m%d')}-{s}"


app = create_app()


# -------------------------
# AUTH
# -------------------------
@app.get("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.post("/login")
def login_post():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    user = User.query.filter_by(username=username).first()
    if not user or not user.is_active or not user.check_password(password):
        flash("Username / password salah, atau user nonaktif.", "danger")
        return redirect(url_for("login"))

    login_user(user)
    flash("Login berhasil.", "success")
    return redirect(url_for("dashboard"))


@app.get("/logout")
@login_required
def logout():
    logout_user()
    flash("Logout berhasil.", "info")
    return redirect(url_for("login"))


# -------------------------
# DASHBOARD
# -------------------------
@app.get("/")
@login_required
def dashboard():
    q = (request.args.get("q") or "").strip()

    products_q = Product.query
    if q:
        products_q = products_q.filter(
            (Product.name.ilike(f"%{q}%")) | (Product.sku.ilike(f"%{q}%"))
        )

    products = products_q.order_by(Product.name.asc()).limit(200).all()

    totals = {
        "product_count": Product.query.count(),
        "total_qty": db.session.query(db.func.coalesce(db.func.sum(Product.stock_qty), 0)).scalar(),
        "total_value": db.session.query(
            db.func.coalesce(db.func.sum(Product.stock_qty * Product.retail_price), 0)
        ).scalar(),
    }

    # folder-like cards (dummy UI like screenshot)
    folders = [
        {"name": "Cleaning Supplies", "count": Product.query.count()},
        {"name": "Main Office", "count": max(1, Product.query.count() // 2)},
    ]

    return render_template(
        "dashboard.html",
        products=products,
        totals=totals,
        q=q,
        folders=folders,
    )


# -------------------------
# PRODUCTS CRUD (simple)
# -------------------------
@app.post("/products/upsert")
@login_required
def product_upsert():
    sku = (request.form.get("sku") or "").strip()
    name = (request.form.get("name") or "").strip()
    unit = (request.form.get("unit") or "pcs").strip()

    cost_price = Decimal(request.form.get("cost_price") or "0")  # ✅ tambah
    retail_price = Decimal(request.form.get("retail_price") or "0")
    reseller_price = Decimal(request.form.get("reseller_price") or "0")

    min_level = int(request.form.get("min_level") or "0")
    notes = (request.form.get("notes") or "").strip()

    if not sku or not name:
        flash("SKU dan Nama wajib diisi.", "danger")
        return redirect(url_for("dashboard"))

    p = Product.query.filter_by(sku=sku).first()
    if not p:
        p = Product(sku=sku, name=name)
        db.session.add(p)

    p.name = name
    p.unit = unit
    p.cost_price = cost_price  # ✅ simpan modal default
    p.retail_price = retail_price
    p.reseller_price = reseller_price
    p.min_level = min_level
    p.notes = notes

    db.session.commit()
    flash("Produk tersimpan.", "success")
    return redirect(url_for("dashboard"))


# -------------------------
# STOCK IN (barang masuk + update harga)
# -------------------------
from decimal import Decimal

@app.post("/stockin/add")
@login_required
def stockin_add():
    sku = (request.form.get("sku") or "").strip()
    qty = int(request.form.get("qty") or "0")

    # ambil string cost (biar bisa cek kosong)
    cost_raw = (request.form.get("cost_per_unit") or "").strip()

    new_retail = (request.form.get("new_retail_price") or "").strip()
    new_reseller = (request.form.get("new_reseller_price") or "").strip()

    if qty <= 0:
        flash("Qty barang masuk harus > 0.", "danger")
        return redirect(url_for("dashboard"))

    p = Product.query.filter_by(sku=sku).first()
    if not p:
        flash("SKU tidak ditemukan. Buat produk dulu.", "danger")
        return redirect(url_for("dashboard"))

    # ✅ kalau cost kosong → pakai modal default product
    if cost_raw == "":
        cost_per_unit = Decimal(p.cost_price or 0)
    else:
        cost_per_unit = Decimal(cost_raw)

    # parse optional harga baru
    new_retail_price = Decimal(new_retail) if new_retail != "" else None
    new_reseller_price = Decimal(new_reseller) if new_reseller != "" else None

    si = StockIn(
        product_id=p.id,
        qty=qty,
        cost_per_unit=cost_per_unit,
        new_retail_price=new_retail_price,
        new_reseller_price=new_reseller_price,
    )
    db.session.add(si)

    # update stok
    p.stock_qty += qty

    # ✅ update modal terakhir (ini yang kamu minta: bisa naik sewaktu-waktu)
    p.cost_price = cost_per_unit

    # update harga jual jika diisi
    if new_retail_price is not None:
        p.retail_price = new_retail_price
    if new_reseller_price is not None:
        p.reseller_price = new_reseller_price

    db.session.commit()
    flash("Barang masuk tercatat, stok & modal terupdate.", "success")
    return redirect(url_for("dashboard"))


# -------------------------
# CASHFLOW (sales list)
# -------------------------
@app.get("/cashflow")
@login_required
def cashflow():
    # filters
    q = (request.args.get("q") or "").strip()
    date_from = (request.args.get("date_from") or "").strip()  # format: YYYY-MM-DD
    date_to = (request.args.get("date_to") or "").strip()      # format: YYYY-MM-DD

    dt_from = None
    dt_to_excl = None  # pakai end-exclusive biar gampang
    try:
        if date_from:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        if date_to:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
            dt_to_excl = dt_to + timedelta(days=1)
    except ValueError:
        flash("Format tanggal harus YYYY-MM-DD", "danger")
        return redirect(url_for("cashflow"))

    # =========================
    # 1) SALDO ALL-TIME (tanpa filter)
    # =========================
    total_sales_all = db.session.query(func.coalesce(func.sum(Sale.total_amount), 0)).scalar()
    total_stockin_all = db.session.query(
        func.coalesce(func.sum(StockIn.qty * StockIn.cost_per_unit), 0)
    ).scalar()

    balance_all_time = Decimal(total_sales_all) - Decimal(total_stockin_all)

    # =========================
    # 2) EVENT LIST (ITEM-LEVEL) + FILTER
    # =========================

    # --- Sale items (uang masuk) ---
    sale_q = (
        db.session.query(
            Sale.created_at.label("time"),
            Sale.ref.label("ref"),
            Sale.channel.label("channel"),
            Product.sku.label("sku"),
            Product.name.label("product_name"),
            SaleItem.qty.label("qty"),
            SaleItem.price.label("unit_price"),
            (SaleItem.qty * SaleItem.price).label("amount"),  # +
            func.cast("SALE", db.String).label("etype"),
        )
        .join(SaleItem, SaleItem.sale_id == Sale.id)
        .join(Product, Product.id == SaleItem.product_id)
    )

    # --- Stock in items (uang keluar, modal) ---
    in_q = (
        db.session.query(
            StockIn.created_at.label("time"),
            func.concat("IN-", StockIn.id).label("ref"),
            func.cast("stock_in", db.String).label("channel"),
            Product.sku.label("sku"),
            Product.name.label("product_name"),
            StockIn.qty.label("qty"),
            StockIn.cost_per_unit.label("unit_price"),
            (-StockIn.qty * StockIn.cost_per_unit).label("amount"),  # -
            func.cast("STOCKIN", db.String).label("etype"),
        )
        .join(Product, Product.id == StockIn.product_id)
    )

    # apply date filter
    if dt_from:
        sale_q = sale_q.filter(Sale.created_at >= dt_from)
        in_q = in_q.filter(StockIn.created_at >= dt_from)
    if dt_to_excl:
        sale_q = sale_q.filter(Sale.created_at < dt_to_excl)
        in_q = in_q.filter(StockIn.created_at < dt_to_excl)

    # apply text filter (sku/name)
    if q:
        like = f"%{q}%"
        sale_q = sale_q.filter((Product.sku.ilike(like)) | (Product.name.ilike(like)))
        in_q = in_q.filter((Product.sku.ilike(like)) | (Product.name.ilike(like)))

    # union all → sort by time desc
    events = sale_q.union_all(in_q).subquery()

    # ambil rows untuk display (misal 500 terakhir agar ringan)
    rows = (
        db.session.query(events)
        .order_by(events.c.time.desc())
        .limit(500)
        .all()
    )

    # =========================
    # 3) SALDO RANGE FILTER (opsional, tapi berguna)
    # =========================
    # saldo periode = sum(amount) dari events yang kena filter (tapi tanpa limit)
    balance_range = db.session.query(func.coalesce(func.sum(events.c.amount), 0)).scalar()
    balance_range = Decimal(balance_range)

    # =========================
    # 4) “TINGKAT LAKUNYA” (qty terjual per produk) dalam range filter
    # =========================
    sold_stats_q = (
        db.session.query(
            Product.sku,
            Product.name,
            func.coalesce(func.sum(SaleItem.qty), 0).label("qty_sold"),
            func.coalesce(func.sum(SaleItem.qty * SaleItem.price), 0).label("revenue"),
        )
        .join(SaleItem, SaleItem.product_id == Product.id)
        .join(Sale, Sale.id == SaleItem.sale_id)
    )
    if dt_from:
        sold_stats_q = sold_stats_q.filter(Sale.created_at >= dt_from)
    if dt_to_excl:
        sold_stats_q = sold_stats_q.filter(Sale.created_at < dt_to_excl)
    if q:
        like = f"%{q}%"
        sold_stats_q = sold_stats_q.filter((Product.sku.ilike(like)) | (Product.name.ilike(like)))

    sold_stats = (
        sold_stats_q
        .group_by(Product.sku, Product.name)
        .order_by(func.sum(SaleItem.qty).desc())
        .limit(20)
        .all()
    )

    return render_template(
        "cashflow.html",
        rows=rows,
        balance_all_time=balance_all_time,
        balance_range=balance_range,
        total_in_all=Decimal(total_sales_all),
        total_out_all=Decimal(total_stockin_all),
        q=q,
        date_from=date_from,
        date_to=date_to,
        sold_stats=sold_stats
    )



# -------------------------
# BARCODE generator (PNG)
# -------------------------
@app.get("/barcode")
@login_required
def barcode_page():
    sku = (request.args.get("sku") or "").strip()
    return render_template("dashboard.html", barcode_sku=sku)  # optional, tetap di dashboard


@app.get("/barcode/png/<sku>")
@login_required
def barcode_png(sku):
    sku = sku.strip()
    if not sku:
        flash("SKU kosong.", "danger")
        return redirect(url_for("dashboard"))

    # Generate Code128 as PNG
    rv = io.BytesIO()
    code = Code128(sku, writer=ImageWriter())
    code.write(rv, options={"module_height": 12.0, "quiet_zone": 3.0, "font_size": 10})
    rv.seek(0)
    return send_file(rv, mimetype="image/png", download_name=f"{sku}.png")


# -------------------------
# RESELLER DATA
# -------------------------
@app.get("/resellers")
@login_required
def resellers():
    resellers = Reseller.query.order_by(Reseller.name.asc()).all()
    products = Product.query.order_by(Product.name.asc()).all()
    selected_id = request.args.get("id")
    selected = None
    rows = []
    if selected_id:
        selected = Reseller.query.filter_by(id=int(selected_id)).first()
        if selected:
            rows = (
                ResellerInventory.query
                .filter_by(reseller_id=selected.id)
                .join(Product, Product.id == ResellerInventory.product_id)
                .order_by(Product.name.asc())
                .all()
            )
    return render_template("reseller.html", resellers=resellers, products=products, selected=selected, rows=rows)


@app.post("/resellers/add")
@login_required
def resellers_add():
    name = (request.form.get("name") or "").strip()
    phone = (request.form.get("phone") or "").strip()
    if not name:
        flash("Nama reseller wajib.", "danger")
        return redirect(url_for("resellers"))
    if Reseller.query.filter_by(name=name).first():
        flash("Reseller sudah ada.", "warning")
        return redirect(url_for("resellers"))
    r = Reseller(name=name, phone=phone)
    db.session.add(r)
    db.session.commit()
    flash("Reseller dibuat.", "success")
    return redirect(url_for("resellers", id=r.id))


@app.post("/resellers/inventory/upsert")
@login_required
def reseller_inventory_upsert():
    reseller_id = int(request.form.get("reseller_id") or "0")
    sku = (request.form.get("sku") or "").strip()
    qty = int(request.form.get("qty") or "0")
    price = Decimal(request.form.get("price") or "0")

    r = Reseller.query.filter_by(id=reseller_id).first()
    p = Product.query.filter_by(sku=sku).first()
    if not r or not p:
        flash("Reseller atau SKU tidak valid.", "danger")
        return redirect(url_for("resellers", id=reseller_id))

    row = ResellerInventory.query.filter_by(reseller_id=r.id, product_id=p.id).first()
    if not row:
        row = ResellerInventory(reseller_id=r.id, product_id=p.id)
        db.session.add(row)

    row.qty = qty
    row.price = price
    row.updated_at = datetime.utcnow()

    db.session.commit()
    flash("Inventory reseller tersimpan.", "success")
    return redirect(url_for("resellers", id=r.id))


# -------------------------
# USER MANAGEMENT (admin only)
# -------------------------
@app.get("/users")
@login_required
def users():
    gate = require_admin()
    if gate:
        return gate
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("users.html", users=users)


@app.post("/users/create")
@login_required
def users_create():
    gate = require_admin()
    if gate:
        return gate

    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    role = (request.form.get("role") or "user").strip()

    if not username or not password:
        flash("Username & password wajib.", "danger")
        return redirect(url_for("users"))

    if User.query.filter_by(username=username).first():
        flash("Username sudah dipakai.", "warning")
        return redirect(url_for("users"))

    u = User(username=username, role=role, is_active=True)
    u.set_password(password)
    db.session.add(u)
    db.session.commit()
    flash("User dibuat.", "success")
    return redirect(url_for("users"))


@app.post("/users/toggle")
@login_required
def users_toggle():
    gate = require_admin()
    if gate:
        return gate
    user_id = int(request.form.get("user_id") or "0")
    u = User.query.filter_by(id=user_id).first()
    if not u:
        flash("User tidak ditemukan.", "danger")
        return redirect(url_for("users"))
    if u.username == "admin":
        flash("Admin default tidak boleh dinonaktifkan.", "warning")
        return redirect(url_for("users"))

    u.is_active = not u.is_active
    db.session.commit()
    flash("Status user diubah.", "info")
    return redirect(url_for("users"))


# -------------------------
# ONLINE STORE (catalog + cart simple)
# -------------------------
@app.get("/store")
@login_required
def store():
    q = (request.args.get("q") or "").strip()
    products_q = Product.query
    if q:
        products_q = products_q.filter(
            (Product.name.ilike(f"%{q}%")) | (Product.sku.ilike(f"%{q}%"))
        )
    products = products_q.order_by(Product.name.asc()).limit(200).all()

    cart = session.get("cart", {})  # {sku: qty}
    cart_count = sum(cart.values()) if cart else 0
    return render_template("store.html", products=products, q=q, cart_count=cart_count)


@app.post("/store/cart/add")
@login_required
def store_cart_add():
    sku = (request.form.get("sku") or "").strip()
    qty = int(request.form.get("qty") or "1")
    if qty <= 0:
        qty = 1

    p = Product.query.filter_by(sku=sku).first()
    if not p:
        flash("SKU tidak ditemukan.", "danger")
        return redirect(url_for("store"))

    cart = session.get("cart", {})
    cart[sku] = int(cart.get(sku, 0)) + qty
    session["cart"] = cart
    flash("Ditambahkan ke cart.", "success")
    return redirect(url_for("store"))


@app.post("/store/cart/clear")
@login_required
def store_cart_clear():
    session["cart"] = {}
    flash("Cart dikosongkan.", "info")
    return redirect(url_for("store"))


@app.get("/store/checkout")
@login_required
def store_checkout():
    cart = session.get("cart", {})
    if not cart:
        flash("Cart kosong.", "warning")
        return redirect(url_for("store"))

    # Build checkout view
    items = []
    total = Decimal("0")
    for sku, qty in cart.items():
        p = Product.query.filter_by(sku=sku).first()
        if not p:
            continue
        price = Decimal(p.retail_price)
        subtotal = price * qty
        total += subtotal
        items.append({"product": p, "qty": qty, "price": price, "subtotal": subtotal})

    return render_template("store.html", checkout_items=items, checkout_total=total, cart_count=sum(cart.values()))


@app.post("/store/checkout")
@login_required
def store_checkout_post():
    cart = session.get("cart", {})
    if not cart:
        flash("Cart kosong.", "warning")
        return redirect(url_for("store"))

    sale = Sale(ref=gen_ref("STORE"), channel="store", total_amount=0)
    db.session.add(sale)
    db.session.flush()

    total = Decimal("0")

    for sku, qty in cart.items():
        p = Product.query.filter_by(sku=sku).first()
        if not p:
            continue

        qty = int(qty)
        if qty <= 0:
            continue

        if p.stock_qty < qty:
            flash(f"Stok kurang untuk {p.name} (tersisa {p.stock_qty}).", "danger")
            db.session.rollback()
            return redirect(url_for("store_checkout"))

        price = Decimal(p.retail_price)
        total += price * qty

        p.stock_qty -= qty

        si = SaleItem(sale_id=sale.id, product_id=p.id, qty=qty, price=price)
        db.session.add(si)

    sale.total_amount = total
    db.session.commit()

    session["cart"] = {}
    flash(f"Checkout berhasil. Ref: {sale.ref}", "success")
    return redirect(url_for("cashflow"))

@app.post("/sales/manual")
@login_required
def manual_sale():
    sku = (request.form.get("sku") or "").strip()
    qty = int(request.form.get("qty") or "0")
    price_raw = (request.form.get("price") or "").strip()
    note = (request.form.get("note") or "").strip()

    if qty <= 0:
        flash("Qty harus > 0.", "danger")
        return redirect(url_for("dashboard"))

    p = Product.query.filter_by(sku=sku).first()
    if not p:
        flash("SKU tidak ditemukan.", "danger")
        return redirect(url_for("dashboard"))

    if p.stock_qty < qty:
        flash(f"Stok tidak cukup. Sisa stok {p.stock_qty} {p.unit}.", "danger")
        return redirect(url_for("dashboard"))

    # harga jual: kalau kosong → pakai retail_price
    if price_raw == "":
        price = Decimal(p.retail_price)
    else:
        price = Decimal(price_raw)

    # buat transaksi
    sale = Sale(ref=gen_ref("OFF"), channel="manual", total_amount=0)
    db.session.add(sale)
    db.session.flush()  # supaya sale.id kebentuk

    # kurangi stok
    p.stock_qty -= qty

    # simpan item
    item = SaleItem(
        sale_id=sale.id,
        product_id=p.id,
        qty=qty,
        price=price
    )
    db.session.add(item)

    total = price * qty
    sale.total_amount = total

    # (opsional) kalau mau catatan, sekarang belum ada kolom note.
    # Kalau kamu mau catatan tersimpan, kita bisa tambah kolom note di tabel sales.

    db.session.commit()

    flash(f"Penjualan offline tersimpan. Ref: {sale.ref} (Rp {total:,.0f})", "success")
    return redirect(url_for("cashflow"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
