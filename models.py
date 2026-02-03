from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from extensions import db

class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="user")  # admin/user
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw: str):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

class Product(db.Model):
    __tablename__ = "products"
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(60), unique=True, nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    unit = db.Column(db.String(30), default="pcs")
    stock_qty = db.Column(db.Integer, nullable=False, default=0)

    # Harga berbeda: retail vs reseller (default)
    retail_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    reseller_price = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    min_level = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StockIn(db.Model):
    __tablename__ = "stock_in"
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    qty = db.Column(db.Integer, nullable=False)
    cost_per_unit = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    new_retail_price = db.Column(db.Numeric(12, 2), nullable=True)
    new_reseller_price = db.Column(db.Numeric(12, 2), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship("Product", backref="stockins")

class Reseller(db.Model):
    __tablename__ = "resellers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(140), nullable=False, unique=True)
    phone = db.Column(db.String(40), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ResellerInventory(db.Model):
    __tablename__ = "reseller_inventory"
    id = db.Column(db.Integer, primary_key=True)
    reseller_id = db.Column(db.Integer, db.ForeignKey("resellers.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)

    qty = db.Column(db.Integer, nullable=False, default=0)
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)  # harga reseller khusus (override)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    reseller = db.relationship("Reseller", backref="inventory_rows")
    product = db.relationship("Product")

    __table_args__ = (db.UniqueConstraint("reseller_id", "product_id", name="uq_reseller_product"),)

class Sale(db.Model):
    __tablename__ = "sales"
    id = db.Column(db.Integer, primary_key=True)
    ref = db.Column(db.String(40), nullable=False, index=True)  # kode transaksi sederhana
    channel = db.Column(db.String(20), nullable=False, default="store")  # store/reseller/manual
    total_amount = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SaleItem(db.Model):
    __tablename__ = "sale_items"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sales.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    qty = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    sale = db.relationship("Sale", backref="items")
    product = db.relationship("Product")
