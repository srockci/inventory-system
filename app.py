"""
物资管理系统 - Flask Web Application
支持扫码入库/出库/盘点，商品二维码生成
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, g
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
import sqlite3
import uuid
import qrcode
import io
import base64
from datetime import datetime, timedelta
import os
from werkzeug.utils import secure_filename

UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

@app.before_request
def inject_settings():
    """全局注入系统设置到所有模板"""
    g.system_name = SystemSettings.get('system_name', '物资管理系统')
    g.system_logo = SystemSettings.get('system_logo', '')
    g.login_background = SystemSettings.get('login_background', '')

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def upload_image(file):
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(UPLOAD_FOLDER, filename))
        return filename
    return None

db = SQLAlchemy(app)

# ============ 数据库模型 ============

class User(db.Model):
    """用户表"""
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Category(db.Model):
    """分类表"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class SystemSettings(db.Model):
    """系统设置表"""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)

    @staticmethod
    def get(key, default=''):
        setting = SystemSettings.query.filter_by(key=key).first()
        return setting.value if setting else default
    
    @staticmethod
    def set(key, value):
        setting = SystemSettings.query.filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = SystemSettings(key=key, value=value)
            db.session.add(setting)
        db.session.commit()

class Product(db.Model):
    """商品表"""
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(50), unique=True, nullable=False)  # 商品编码（扫码用）
    name = db.Column(db.String(200), nullable=False)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    unit = db.Column(db.String(20), default='件')  # 单位
    min_stock = db.Column(db.Integer, default=0)  # 最低库存预警
    current_stock = db.Column(db.Integer, default=0)
    location = db.Column(db.String(100))  # 存放位置
    image_url = db.Column(db.String(500))  # 商品图片路径
    remark = db.Column(db.Text)  # 备注
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = db.relationship('Category', backref='products')

class StockIn(db.Model):
    """入库记录表"""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    operator = db.Column(db.String(50), nullable=False)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='stock_ins')

class StockOut(db.Model):
    """出库记录表"""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    operator = db.Column(db.String(50), nullable=False)
    remark =db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='stock_outs')

class StockCheck(db.Model):
    """盘点记录表"""
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    actual_stock = db.Column(db.Integer, nullable=False)  # 实际盘点数量
    system_stock = db.Column(db.Integer, nullable=False)  # 系统数量
    diff = db.Column(db.Integer, nullable=False)  # 差异
    operator = db.Column(db.String(50), nullable=False)
    remark = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref='stock_checks')

# ============ 辅助函数 ============

def generate_code():
    """生成商品编码"""
    return 'P' + datetime.now().strftime('%Y%m%d%H%M%S') + str(uuid.uuid4().hex[:4]).upper()

def hash_password(pwd):
    """简单密码hash"""
    import hashlib
    return hashlib.sha256(pwd.encode()).hexdigest()

def verify_password(pwd, hashed):
    return hash_password(pwd) == hashed

def generate_qr_code(data):
    """生成二维码图片（返回base64）"""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return base64.b64encode(buffer.getvalue()).decode()

def get_product_by_code(code):
    """通过编码查找商品"""
    return Product.query.filter_by(code=code).first()

# ============ 登录装饰器 ============

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# ============ 路由 ============

@app.route('/')
@login_required
def index():
    """仪表盘"""
    total_products = Product.query.count()
    low_stock_count = Product.query.filter(Product.current_stock <= Product.min_stock, Product.min_stock > 0).count()
    today = datetime.now().date()
    
    stock_in_today = StockIn.query.filter(
        db.func.date(StockIn.created_at) == today
    ).count()
    stock_out_today = StockOut.query.filter(
        db.func.date(StockOut.created_at) == today
    ).count()
    
    return render_template('index.html',
                         total_products=total_products,
                         low_stock_count=low_stock_count,
                         stock_in_today=stock_in_today,
                         stock_out_today=stock_out_today)

# ---- 登录 ----

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        if user and verify_password(password, user.password_hash):
            session['user_id'] = user.id
            session['username'] = user.username
            return redirect(url_for('index'))
        return render_template('login.html', error='用户名或密码错误')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ---- 商品管理 ----

@app.route('/products')
@login_required
def products():
    """商品列表"""
    category_id = request.args.get('category_id', type=int)
    search = request.args.get('search', '')
    
    query = Product.query
    if category_id:
        query = query.filter_by(category_id=category_id)
    if search:
        query = query.filter(Product.name.contains(search) | Product.code.contains(search))
    
    products_list = query.order_by(Product.created_at.desc()).all()
    categories = Category.query.all()
    
    return render_template('products.html', 
                         products=products_list, 
                         categories=categories,
                         current_category=category_id,
                         search=search)

@app.route('/product/add', methods=['GET', 'POST'])
@login_required
def add_product():
    """添加商品"""
    if request.method == 'POST':
        name = request.form.get('name')
        category_id = request.form.get('category_id', type=int)
        unit = request.form.get('unit', '件')
        min_stock = request.form.get('min_stock', type=int, default=0)
        location = request.form.get('location', '')
        remark = request.form.get('remark', '')
        
        # 处理图片上传
        image_url = None
        if 'image' in request.files:
            file = request.files['image']
            image_url = upload_image(file)
        
        product = Product(
            code=generate_code(),
            name=name,
            category_id=category_id,
            unit=unit,
            min_stock=min_stock,
            location=location,
            image_url=image_url,
            remark=remark
        )
        db.session.add(product)
        db.session.commit()
        return redirect(url_for('products'))
    
    categories = Category.query.all()
    return render_template('product_form.html', product=None, categories=categories)

@app.route('/product/<int:product_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_product(product_id):
    """编辑商品"""
    product = Product.query.get_or_404(product_id)
    
    if request.method == 'POST':
        product.name = request.form.get('name')
        product.category_id = request.form.get('category_id', type=int)
        product.unit = request.form.get('unit', '件')
        product.min_stock = request.form.get('min_stock', type=int, default=0)
        product.location = request.form.get('location', '')
        product.remark = request.form.get('remark', '')
        
        # 处理图片上传（可选）
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename:
                new_image = upload_image(file)
                if new_image:
                    product.image_url = new_image
        
        db.session.commit()
        return redirect(url_for('products'))
    
    categories = Category.query.all()
    return render_template('product_form.html', product=product, categories=categories)

@app.route('/product/<int:product_id>/delete', methods=['POST'])
@login_required
def delete_product(product_id):
    """删除商品"""
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    return redirect(url_for('products'))

@app.route('/product/<int:product_id>/qrcode')
@login_required
def product_qrcode(product_id):
    """生成商品二维码"""
    product = Product.query.get_or_404(product_id)
    qr_data = f"INV:{product.code}"
    qr_base64 = generate_qr_code(qr_data)
    return render_template('qrcode.html', product=product, qr_base64=qr_base64)

# ---- 入库 ----

@app.route('/stock-in')
@login_required
def stock_in_page():
    """入库页面"""
    products = Product.query.order_by(Product.name).all()
    return render_template('stock_in.html', products=products)

@app.route('/stock-in/do', methods=['POST'])
@login_required
def do_stock_in():
    """执行入库"""
    code = request.form.get('code', '').strip()
    quantity = request.form.get('quantity', type=int)
    remark = request.form.get('remark', '')
    
    # 支持扫码（code格式: INV:xxxxx）
    if code.startswith('INV:'):
        code = code[4:]
    
    product = get_product_by_code(code)
    if not product:
        return jsonify({'success': False, 'message': f'商品编码不存在: {code}'})
    
    if quantity <= 0:
        return jsonify({'success': False, 'message': '数量必须大于0'})
    
    # 入库记录
    stock_in = StockIn(
        product_id=product.id,
        quantity=quantity,
        operator=session['username'],
        remark=remark
    )
    product.current_stock += quantity
    
    db.session.add(stock_in)
    db.session.commit()
    
    return jsonify({
        'success': True, 
        'message': f'入库成功: {product.name} x {quantity}',
        'product_name': product.name,
        'new_stock': product.current_stock
    })

@app.route('/stock-in/records')
@login_required
def stock_in_records():
    """入库记录"""
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    query = StockIn.query.order_by(StockIn.created_at.desc())
    
    if date_from:
        query = query.filter(db.func.date(StockIn.created_at) >= date_from)
    if date_to:
        query = query.filter(db.func.date(StockIn.created_at) <= date_to)
    
    records = query.limit(100).all()
    return render_template('stock_records.html', records=records, type='in')

# ---- 出库 ----

@app.route('/stock-out')
@login_required
def stock_out_page():
    """出库页面"""
    products = Product.query.order_by(Product.name).all()
    return render_template('stock_out.html', products=products)

@app.route('/stock-out/do', methods=['POST'])
@login_required
def do_stock_out():
    """执行出库"""
    code = request.form.get('code', '').strip()
    quantity = request.form.get('quantity', type=int)
    remark = request.form.get('remark', '')
    
    if code.startswith('INV:'):
        code = code[4:]
    
    product = get_product_by_code(code)
    if not product:
        return jsonify({'success': False, 'message': f'商品编码不存在: {code}'})
    
    if quantity <= 0:
        return jsonify({'success': False, 'message': '数量必须大于0'})
    
    if product.current_stock < quantity:
        return jsonify({'success': False, 'message': f'库存不足，当前库存: {product.current_stock}'})
    
    stock_out = StockOut(
        product_id=product.id,
        quantity=quantity,
        operator=session['username'],
        remark=remark
    )
    product.current_stock -= quantity
    
    db.session.add(stock_out)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': f'出库成功: {product.name} x {quantity}',
        'product_name': product.name,
        'new_stock': product.current_stock
    })

@app.route('/stock-out/records')
@login_required
def stock_out_records():
    """出库记录"""
    date_from = request.args.get('date_from')
    date_to = request.args.get('date_to')
    
    query = StockOut.query.order_by(StockOut.created_at.desc())
    
    if date_from:
        query = query.filter(db.func.date(StockOut.created_at) >= date_from)
    if date_to:
        query = query.filter(db.func.date(StockOut.created_at) <= date_to)
    
    records = query.limit(100).all()
    return render_template('stock_records.html', records=records, type='out')

# ---- 盘点 ----

@app.route('/stock-check')
@login_required
def stock_check_page():
    """盘点页面"""
    products = Product.query.order_by(Product.name).all()
    return render_template('stock_check.html', products=products)

@app.route('/stock-check/do', methods=['POST'])
@login_required
def do_stock_check():
    """执行盘点"""
    code = request.form.get('code', '').strip()
    actual_stock = request.form.get('actual_stock', type=int)
    remark = request.form.get('remark', '')
    
    if code.startswith('INV:'):
        code = code[4:]
    
    product = get_product_by_code(code)
    if not product:
        return jsonify({'success': False, 'message': f'商品编码不存在: {code}'})
    
    if actual_stock < 0:
        return jsonify({'success': False, 'message': '数量不能为负'})
    
    diff = actual_stock - product.current_stock
    
    stock_check = StockCheck(
        product_id=product.id,
        actual_stock=actual_stock,
        system_stock=product.current_stock,
        diff=diff,
        operator=session['username'],
        remark=remark
    )
    
    product.current_stock = actual_stock
    
    db.session.add(stock_check)
    db.session.commit()
    
    diff_text = '正常' if diff == 0 else (f'盘盈 +{diff}' if diff > 0 else f'盘亏 {diff}')
    
    return jsonify({
        'success': True,
        'message': f'盘点完成: {product.name}',
        'diff_text': diff_text,
        'new_stock': product.current_stock
    })

@app.route('/stock-check/records')
@login_required
def stock_check_records():
    """盘点记录"""
    records = StockCheck.query.order_by(StockCheck.created_at.desc()).limit(100).all()
    return render_template('stock_check_records.html', records=records)

# ---- 分类管理 ----

@app.route('/categories')
@login_required
def categories():
    """分类列表"""
    categories_list = Category.query.order_by(Category.name).all()
    return render_template('categories.html', categories=categories_list)

@app.route('/category/add', methods=['POST'])
@login_required
def add_category():
    """添加分类"""
    name = request.form.get('name')
    category = Category(name=name)
    db.session.add(category)
    db.session.commit()
    return redirect(url_for('categories'))

@app.route('/category/<int:category_id>/delete', methods=['POST'])
@login_required
def delete_category(category_id):
    """删除分类"""
    category = Category.query.get_or_404(category_id)
    # 检查是否有商品使用此分类
    if Product.query.filter_by(category_id=category_id).first():
        return jsonify({'success': False, 'message': '该分类下有商品，无法删除'})
    db.session.delete(category)
    db.session.commit()
    return redirect(url_for('categories'))

# ---- 预警 ----

@app.route('/alerts')
@login_required
def alerts():
    """库存预警（低库存）"""
    low_stock = Product.query.filter(
        Product.current_stock <= Product.min_stock,
        Product.min_stock > 0
    ).order_by(Product.current_stock).all()
    return render_template('alerts.html', products=low_stock, alert_type='low')

@app.route('/high-stock-alerts')
@login_required
def high_stock_alerts():
    """高库存预警"""
    # 高库存 = 库存超过最高库存（暂时用 min_stock * 3 作为默认值，或者需要加 max_stock 字段）
    # 这里先简单实现：如果设置了预警值且库存超过预警值的 3 倍
    high_stock = Product.query.filter(
        Product.min_stock > 0,
        Product.current_stock >= Product.min_stock * 3
    ).order_by(Product.current_stock.desc()).all()
    return render_template('alerts.html', products=high_stock, alert_type='high')

# ---- 系统设置 ----

@app.route('/settings')
@login_required
def settings():
    """系统设置"""
    if session.get('username') != 'admin':
        return '无权限', 403
    
    # 获取当前设置
    system_name = SystemSettings.get('system_name', '物资管理系统')
    system_logo = SystemSettings.get('system_logo', '')
    login_background = SystemSettings.get('login_background', '')
    
    return render_template('settings.html', 
                         system_name=system_name,
                         system_logo=system_logo,
                         login_background=login_background)

@app.route('/settings/upload-logo', methods=['POST'])
@login_required
def upload_logo():
    """上传系统Logo"""
    if session.get('username') != 'admin':
        return jsonify({'success': False, 'message': '无权限'})
    
    if 'logo' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'})
    
    file = request.files['logo']
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'})
    
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"logo.{ext}"
        file.save(os.path.join(UPLOAD_FOLDER, filename))
        SystemSettings.set('system_logo', filename)
        return jsonify({'success': True, 'message': 'Logo上传成功'})
    
    return jsonify({'success': False, 'message': '不支持的文件格式'})

@app.route('/settings/upload-background', methods=['POST'])
@login_required
def upload_background():
    """上传登录页背景"""
    if session.get('username') != 'admin':
        return jsonify({'success': False, 'message': '无权限'})
    
    if 'background' not in request.files:
        return jsonify({'success': False, 'message': '没有文件'})
    
    file = request.files['background']
    if file.filename == '':
        return jsonify({'success': False, 'message': '没有选择文件'})
    
    if file and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f"background.{ext}"
        file.save(os.path.join(UPLOAD_FOLDER, filename))
        SystemSettings.set('login_background', filename)
        return jsonify({'success': True, 'message': '背景上传成功'})
    
    return jsonify({'success': False, 'message': '不支持的文件格式'})

@app.route('/settings/update', methods=['POST'])
@login_required
def update_settings():
    """更新系统设置"""
    if session.get('username') != 'admin':
        return jsonify({'success': False, 'message': '无权限'})
    
    system_name = request.form.get('system_name', '')
    login_background_url = request.form.get('login_background_url', '')
    
    SystemSettings.set('system_name', system_name)
    SystemSettings.set('login_background', login_background_url)
    
    return jsonify({'success': True, 'message': '设置已保存'})

@app.route('/settings/reset-logo', methods=['POST'])
@login_required
def reset_logo():
    """删除Logo"""
    if session.get('username') != 'admin':
        return jsonify({'success': False, 'message': '无权限'})
    SystemSettings.set('system_logo', '')
    return jsonify({'success': True, 'message': 'Logo已删除'})

@app.route('/settings/reset-background', methods=['POST'])
@login_required
def reset_background():
    """删除登录背景"""
    if session.get('username') != 'admin':
        return jsonify({'success': False, 'message': '无权限'})
    SystemSettings.set('login_background', '')
    return jsonify({'success': True, 'message': '背景已删除'})

# ---- 用户管理 ----

@app.route('/users')
@login_required
def users():
    """用户列表（仅管理员）"""
    if session.get('username') != 'admin':
        return '无权限', 403
    users_list = User.query.all()
    return render_template('users.html', users=users_list)

@app.route('/user/add', methods=['POST'])
@login_required
def add_user():
    """添加用户"""
    if session.get('username') != 'admin':
        return '无权限', 403
    username = request.form.get('username')
    password = request.form.get('password')
    
    if User.query.filter_by(username=username).first():
        return jsonify({'success': False, 'message': '用户名已存在'})
    
    user = User(username=username, password_hash=hash_password(password))
    db.session.add(user)
    db.session.commit()
    return redirect(url_for('users'))

@app.route('/user/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_user(user_id):
    """编辑用户"""
    if session.get('username') != 'admin':
        return '无权限', 403
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        new_username = request.form.get('username')
        
        # 检查新用户名是否被占用（排除自己）
        if new_username != user.username and User.query.filter_by(username=new_username).first():
            return jsonify({'success': False, 'message': '用户名已存在'})
        
        user.username = new_username
        db.session.commit()
        return redirect(url_for('users'))
    
    return render_template('user_edit.html', user=user)

@app.route('/user/<int:user_id>/reset-password', methods=['GET', 'POST'])
@login_required
def reset_user_password(user_id):
    """重置用户密码（仅管理员）"""
    if session.get('username') != 'admin':
        return '无权限', 403
    
    user = User.query.get_or_404(user_id)
    
    if request.method == 'POST':
        new_password = request.form.get('password')
        if len(new_password) < 6:
            return jsonify({'success': False, 'message': '密码至少6位'})
        
        user.password_hash = hash_password(new_password)
        db.session.commit()
        return jsonify({'success': True, 'message': f'密码已重置为: {new_password}'})
    
    return render_template('user_reset_password.html', user=user)

@app.route('/user/<int:user_id>/delete', methods=['POST'])
@login_required
def delete_user(user_id):
    """删除用户"""
    if session.get('username') != 'admin':
        return '无权限', 403
    
    user = User.query.get_or_404(user_id)
    
    # 不允许删除自己
    if user.username == session.get('username'):
        return jsonify({'success': False, 'message': '不能删除自己'})
    
    # 不允许删除最后一个管理员
    if user.username == 'admin' and User.query.filter_by(username='admin').count() <= 1:
        return jsonify({'success': False, 'message': '不能删除最后一个管理员'})
    
    db.session.delete(user)
    db.session.commit()
    return redirect(url_for('users'))

# ============ 初始化 ============

def init_db():
    """初始化数据库"""
    with app.app_context():
        db.create_all()
        
        # 创建管理员账号
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', password_hash=hash_password('admin123'))
            db.session.add(admin)
        
        # 创建默认分类
        if Category.query.count() == 0:
            default_categories = ['办公用品', '电子设备', '工具', '耗材', '其他']
            for name in default_categories:
                db.session.add(Category(name=name))
        
        # 创建默认系统设置
        if SystemSettings.get('system_name') == '':
            SystemSettings.set('system_name', '物资管理系统')
        
        db.session.commit()
        print('数据库初始化完成')

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
