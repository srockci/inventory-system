"""
数据库初始化脚本
"""
from app import app, db, User, Category, SystemSettings, Project, hash_password
import json

def init_db():
    """初始化数据库"""
    with app.app_context():
        db.create_all()
        
        # 创建管理员账号
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', password_hash=hash_password('admin123'), role='admin')
            db.session.add(admin)
        
        # 创建默认分类
        if Category.query.count() == 0:
            default_categories = ['办公用品', '电子设备', '工具', '耗材', '其他']
            for name in default_categories:
                db.session.add(Category(name=name))
        
        # 创建默认系统设置
        if SystemSettings.get('system_name') == '':
            SystemSettings.set('system_name', '物资管理系统')
        
        # 创建默认角色
        if SystemSettings.get('roles') == '':
            SystemSettings.set('roles', json.dumps([
                {'name': 'admin', 'is_admin': True, 'user_count': 0},
                {'name': 'user', 'is_admin': False, 'user_count': 0}
            ]))
            # 创建默认权限
            SystemSettings.set('permissions', json.dumps({
                'admin': {k: True for k in ['stock_in', 'stock_out', 'stock_check', 'products_view', 'products_edit', 'products_delete', 'categories_edit', 'users_view', 'users_edit', 'settings_view', 'settings_edit', 'reports_view']},
                'user': {'stock_in': True, 'stock_out': True, 'stock_check': True, 'products_view': True, 'products_edit': False, 'products_delete': False, 'categories_edit': False, 'users_view': False, 'users_edit': False, 'settings_view': False, 'settings_edit': False, 'reports_view': True}
            }))
        
        # 创建默认项目
        if Project.query.count() == 0:
            default_project = Project(
                name='默认项目',
                code='DEFAULT',
                description='系统默认项目'
            )
            db.session.add(default_project)
        
        # 创建 user_project_roles 表
        from sqlalchemy import text
        try:
            db.session.execute(text("""
                CREATE TABLE IF NOT EXISTS user_project_roles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL,
                    username VARCHAR(50) NOT NULL,
                    role VARCHAR(50) DEFAULT 'viewer',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (project_id) REFERENCES project(id),
                    UNIQUE(project_id, username)
                )
            """))
        except Exception:
            pass
        
        db.session.commit()
        print('数据库初始化完成')

if __name__ == '__main__':
    init_db()
