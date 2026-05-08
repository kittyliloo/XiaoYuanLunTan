import os
import uuid
import datetime
from functools import wraps
from flask import Flask, request, jsonify, g, send_from_directory
from flask_cors import CORS
import sqlite3

# 尝试导入 PostgreSQL 驱动（如果安装了）
try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'your-secret-key-change-in-production')
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB
CORS(app, supports_credentials=True)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# ---------- 数据库连接（自动适配 Supabase / SQLite） ----------
def get_db():
    db = getattr(g, '_database', None)
    if db is not None:
        return db

    database_url = os.environ.get('DATABASE_URL')
    if database_url and database_url.startswith('postgresql') and PSYCOPG2_AVAILABLE:
        # 生产环境：Supabase PostgreSQL
        from urllib.parse import urlparse
        result = urlparse(database_url)
        conn = psycopg2.connect(
            database=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port,
            sslmode='require'
        )
        conn.row_factory = psycopg2.extras.RealDictCursor
        g._database = conn
        return conn
    else:
        # 本地开发：SQLite
        db_path = os.environ.get('DATABASE_PATH', 'database.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        g._database = conn
        return conn

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def _execute_sql(db, sql, params=None, fetch_one=False, fetch_all=False):
    """
    统一执行 SQL，自动处理 SQLite 和 PostgreSQL 的占位符差异。
    - SQLite 使用 ?
    - PostgreSQL 使用 %s
    """
    if isinstance(db, (sqlite3.Connection,)):
        # SQLite
        cursor = db.cursor()
        if fetch_one:
            cursor.execute(sql, params or ())
            return cursor.fetchone()
        elif fetch_all:
            cursor.execute(sql, params or ())
            return cursor.fetchall()
        else:
            cursor.execute(sql, params or ())
            db.commit()
            return cursor
    elif PSYCOPG2_AVAILABLE and isinstance(db, psycopg2.extensions.connection):
        # PostgreSQL
        # 将 SQL 中的 ? 替换为 %s
        sql_postgres = sql.replace('?', '%s')
        cursor = db.cursor()
        if fetch_one:
            cursor.execute(sql_postgres, params or ())
            return cursor.fetchone()
        elif fetch_all:
            cursor.execute(sql_postgres, params or ())
            return cursor.fetchall()
        else:
            cursor.execute(sql_postgres, params or ())
            db.commit()
            return cursor
    else:
        raise Exception("Unsupported database type")

def init_db():
    with app.app_context():
        db = get_db()
        is_postgres = isinstance(db, psycopg2.extensions.connection) if PSYCOPG2_AVAILABLE else False

        if is_postgres:
            # PostgreSQL 建表语句（使用 SERIAL 自增）
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    avatar TEXT,
                    bio TEXT,
                    role TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS posts (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    image_url TEXT,
                    like_count INTEGER DEFAULT 0,
                    visibility TEXT DEFAULT 'public',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS post_likes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, post_id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS comments (
                    id SERIAL PRIMARY KEY,
                    post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    like_count INTEGER DEFAULT 0,
                    parent_id INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS comment_likes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    comment_id INTEGER NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, comment_id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS friendships (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    friend_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, friend_id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS friend_requests (
                    id SERIAL PRIMARY KEY,
                    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS follows (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    follow_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, follow_id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    content TEXT NOT NULL,
                    is_read INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS reports (
                    id SERIAL PRIMARY KEY,
                    reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    target_type TEXT NOT NULL,
                    target_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else:
            # SQLite 建表语句
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    avatar TEXT,
                    bio TEXT,
                    role TEXT DEFAULT 'user',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    image_url TEXT,
                    like_count INTEGER DEFAULT 0,
                    visibility TEXT DEFAULT 'public',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS post_likes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    post_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, post_id),
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    like_count INTEGER DEFAULT 0,
                    parent_id INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE,
                    FOREIGN KEY (user_id) REFERENCES users (id)
                )
            ''')
            # 确保 parent_id 列存在
            if not is_postgres:
                cursor = db.cursor()
                cursor.execute("PRAGMA table_info(comments)")
                columns = [col[1] for col in cursor.fetchall()]
                if 'parent_id' not in columns:
                    cursor.execute("ALTER TABLE comments ADD COLUMN parent_id INTEGER DEFAULT 0")
                    db.commit()
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS comment_likes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    comment_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, comment_id),
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (comment_id) REFERENCES comments (id) ON DELETE CASCADE
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS friendships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    friend_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, friend_id),
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (friend_id) REFERENCES users (id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS friend_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_user_id) REFERENCES users (id),
                    FOREIGN KEY (to_user_id) REFERENCES users (id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS follows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    follow_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, follow_id),
                    FOREIGN KEY (user_id) REFERENCES users (id),
                    FOREIGN KEY (follow_id) REFERENCES users (id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    is_read INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_user_id) REFERENCES users (id),
                    FOREIGN KEY (to_user_id) REFERENCES users (id)
                )
            ''')
            _execute_sql(db, '''
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    reporter_id INTEGER NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (reporter_id) REFERENCES users (id)
                )
            ''')

        # 创建默认测试用户
        if is_postgres:
            existing = _execute_sql(db, "SELECT * FROM users WHERE username = 'test'", fetch_one=True)
            if not existing:
                _execute_sql(db, "INSERT INTO users (username, password, nickname, bio, avatar) VALUES (%s, %s, %s, %s, %s)",
                             ('test', '123456', '测试用户', '这个人很懒', ''))
            existing = _execute_sql(db, "SELECT * FROM users WHERE username = 'admin'", fetch_one=True)
            if not existing:
                _execute_sql(db, "INSERT INTO users (username, password, nickname, bio, role, avatar) VALUES (%s, %s, %s, %s, %s, %s)",
                             ('admin', 'admin123', '管理员', '系统管理员', 'admin', ''))
        else:
            existing = _execute_sql(db, "SELECT * FROM users WHERE username = 'test'", fetch_one=True)
            if not existing:
                _execute_sql(db, "INSERT INTO users (username, password, nickname, bio, avatar) VALUES (?, ?, ?, ?, ?)",
                             ('test', '123456', '测试用户', '这个人很懒', ''))
            existing = _execute_sql(db, "SELECT * FROM users WHERE username = 'admin'", fetch_one=True)
            if not existing:
                _execute_sql(db, "INSERT INTO users (username, password, nickname, bio, role, avatar) VALUES (?, ?, ?, ?, ?, ?)",
                             ('admin', 'admin123', '管理员', '系统管理员', 'admin', ''))

        db.commit()
    print("数据库初始化完成（支持 SQLite / Supabase PostgreSQL）")

init_db()

def row_to_dict(row):
    return dict(row)

# ---------- 认证装饰器 ----------
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = request.headers.get('X-User-Id')
        if not user_id:
            return jsonify({'error': '未登录'}), 401
        db = get_db()
        user = _execute_sql(db, 'SELECT * FROM users WHERE id = ?', (user_id,), fetch_one=True)
        if not user:
            return jsonify({'error': '用户不存在'}), 401
        g.user = user
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user_id = request.headers.get('X-User-Id')
        if not user_id:
            return jsonify({'error': '未登录'}), 401
        db = get_db()
        user = _execute_sql(db, 'SELECT id, role FROM users WHERE id = ?', (user_id,), fetch_one=True)
        if not user or user['role'] != 'admin':
            return jsonify({'error': '无管理员权限'}), 403
        g.user = user
        return f(*args, **kwargs)
    return decorated

# ---------- 图片上传 ----------
@app.route('/api/upload', methods=['POST'])
@require_auth
def upload_image():
    if 'image' not in request.files:
        return jsonify({'error': '没有文件'}), 400
    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': '空文件名'}), 400
    ext = file.filename.rsplit('.', 1)[-1].lower()
    if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
        return jsonify({'error': '不支持的文件类型'}), 400
    filename = str(uuid.uuid4()) + '.' + ext
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    return jsonify({'url': f'/uploads/{filename}'})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ---------- 用户相关 ----------
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'}), 400
    if len(username) < 3 or len(password) < 3:
        return jsonify({'success': False, 'message': '用户名和密码至少3个字符'}), 400
    db = get_db()
    exist = _execute_sql(db, 'SELECT id FROM users WHERE username = ?', (username,), fetch_one=True)
    if exist:
        return jsonify({'success': False, 'message': '用户名已存在'}), 400
    nickname = data.get('nickname', username)
    bio = data.get('bio', '这个人很懒，什么都没写')
    avatar = data.get('avatar', '')
    _execute_sql(db, 'INSERT INTO users (username, password, nickname, bio, avatar) VALUES (?, ?, ?, ?, ?)',
                 (username, password, nickname, bio, avatar))
    if isinstance(db, sqlite3.Connection):
        user_id = _execute_sql(db, 'SELECT last_insert_rowid() as id', fetch_one=True)['id']
    else:
        cursor = db.cursor()
        cursor.execute('SELECT id FROM users WHERE username = %s', (username,))
        user_id = cursor.fetchone()['id']
    return jsonify({'success': True, 'message': '注册成功', 'user_id': user_id})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    db = get_db()
    user = _execute_sql(db, 'SELECT * FROM users WHERE username = ? AND password = ?', (username, password), fetch_one=True)
    if not user:
        return jsonify({'success': False, 'message': '用户名或密码错误'}), 401
    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'nickname': user['nickname'],
            'avatar': user['avatar'] if user['avatar'] else '',
            'bio': user['bio'],
            'role': user['role']
        }
    })

@app.route('/api/user/<int:user_id>', methods=['GET'])
def get_user(user_id):
    db = get_db()
    user = _execute_sql(db, 'SELECT id, username, nickname, avatar, bio, role FROM users WHERE id = ?', (user_id,), fetch_one=True)
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    return jsonify({
        'id': user['id'],
        'username': user['username'],
        'nickname': user['nickname'],
        'avatar': user['avatar'] if user['avatar'] else '',
        'bio': user['bio'],
        'role': user['role']
    })

@app.route('/api/user/<int:user_id>', methods=['PUT'])
@require_auth
def update_user(user_id):
    if g.user['id'] != user_id:
        return jsonify({'error': '无权限'}), 403
    data = request.json
    db = get_db()
    if 'nickname' in data:
        _execute_sql(db, 'UPDATE users SET nickname = ? WHERE id = ?', (data['nickname'], user_id))
    if 'bio' in data:
        _execute_sql(db, 'UPDATE users SET bio = ? WHERE id = ?', (data['bio'], user_id))
    if 'avatar' in data:
        _execute_sql(db, 'UPDATE users SET avatar = ? WHERE id = ?', (data['avatar'], user_id))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/change-password', methods=['POST'])
@require_auth
def change_password():
    data = request.json
    new_password = data.get('new_password')
    if not new_password or len(new_password) < 3:
        return jsonify({'error': '密码至少3位'}), 400
    db = get_db()
    _execute_sql(db, 'UPDATE users SET password = ? WHERE id = ?', (new_password, g.user['id']))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/delete-account', methods=['DELETE'])
@require_auth
def delete_account():
    db = get_db()
    _execute_sql(db, 'DELETE FROM users WHERE id = ?', (g.user['id'],))
    db.commit()
    return jsonify({'success': True})

# ---------- 帖子相关 ----------
@app.route('/api/posts', methods=['GET'])
def get_posts():
    db = get_db()
    rows = _execute_sql(db, '''
        SELECT p.*, u.nickname as username, u.avatar 
        FROM posts p 
        JOIN users u ON p.user_id = u.id 
        ORDER BY p.created_at DESC
    ''', fetch_all=True)
    result = []
    for p in rows:
        p_dict = dict(p)
        like_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM post_likes WHERE post_id = ?', (p['id'],), fetch_one=True)['cnt']
        comment_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM comments WHERE post_id = ?', (p['id'],), fetch_one=True)['cnt']
        p_dict['likeCount'] = like_count
        p_dict['commentCount'] = comment_count
        result.append(p_dict)
    return jsonify(result)

@app.route('/api/posts', methods=['POST'])
@require_auth
def create_post():
    data = request.json
    category = data.get('category')
    title = data.get('title')
    content = data.get('content')
    image_url = data.get('image_url', '')
    if not title or not content:
        return jsonify({'error': '标题和内容不能为空'}), 400
    db = get_db()
    _execute_sql(db, 'INSERT INTO posts (user_id, category, title, content, image_url) VALUES (?, ?, ?, ?, ?)',
                 (g.user['id'], category, title, content, image_url))
    db.commit()
    return jsonify({'success': True, 'post_id': g.user['id']})  # 不影响前端，只表明成功

@app.route('/api/posts/<int:post_id>', methods=['PUT'])
@require_auth
def update_post(post_id):
    data = request.json
    db = get_db()
    post = _execute_sql(db, 'SELECT user_id FROM posts WHERE id = ?', (post_id,), fetch_one=True)
    if not post or post['user_id'] != g.user['id']:
        return jsonify({'error': '无权限'}), 403
    if 'visibility' in data:
        _execute_sql(db, 'UPDATE posts SET visibility = ? WHERE id = ?', (data['visibility'], post_id))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/posts/<int:post_id>', methods=['DELETE'])
@require_auth
def delete_post(post_id):
    db = get_db()
    post = _execute_sql(db, 'SELECT user_id FROM posts WHERE id = ?', (post_id,), fetch_one=True)
    if not post or post['user_id'] != g.user['id']:
        return jsonify({'error': '无权限'}), 403
    _execute_sql(db, 'DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/posts/<int:post_id>/like', methods=['POST'])
@require_auth
def like_post(post_id):
    db = get_db()
    existing = _execute_sql(db, 'SELECT id FROM post_likes WHERE user_id = ? AND post_id = ?', (g.user['id'], post_id), fetch_one=True)
    if existing:
        _execute_sql(db, 'DELETE FROM post_likes WHERE user_id = ? AND post_id = ?', (g.user['id'], post_id))
        _execute_sql(db, 'UPDATE posts SET like_count = like_count - 1 WHERE id = ?', (post_id,))
        liked = False
    else:
        _execute_sql(db, 'INSERT INTO post_likes (user_id, post_id) VALUES (?, ?)', (g.user['id'], post_id))
        _execute_sql(db, 'UPDATE posts SET like_count = like_count + 1 WHERE id = ?', (post_id,))
        liked = True
    db.commit()
    new_count = _execute_sql(db, 'SELECT like_count FROM posts WHERE id = ?', (post_id,), fetch_one=True)['like_count']
    return jsonify({'liked': liked, 'likeCount': new_count})

# ---------- 评论相关 ----------
@app.route('/api/posts/<int:post_id>/comments', methods=['GET'])
def get_comments(post_id):
    db = get_db()
    rows = _execute_sql(db, '''
        SELECT c.*, u.nickname as author, u.avatar 
        FROM comments c 
        JOIN users u ON c.user_id = u.id 
        WHERE c.post_id = ? 
        ORDER BY c.created_at ASC
    ''', (post_id,), fetch_all=True)
    comment_dict = {}
    for r in rows:
        d = dict(r)
        d['likeCount'] = d['like_count']
        d['replies'] = []
        comment_dict[d['id']] = d
    root_comments = []
    for r in rows:
        if r['parent_id'] == 0:
            root_comments.append(comment_dict[r['id']])
        else:
            parent = comment_dict.get(r['parent_id'])
            if parent:
                parent['replies'].append(comment_dict[r['id']])
            else:
                root_comments.append(comment_dict[r['id']])
    return jsonify(root_comments)

@app.route('/api/posts/<int:post_id>/comments', methods=['POST'])
@require_auth
def add_comment(post_id):
    data = request.json
    content = data.get('content', '').strip()
    parent_id = data.get('parent_id', 0)
    if not content:
        return jsonify({'error': '评论内容不能为空'}), 400
    db = get_db()
    _execute_sql(db, 'INSERT INTO comments (post_id, user_id, content, parent_id) VALUES (?, ?, ?, ?)',
                 (post_id, g.user['id'], content, parent_id))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/comments/<int:comment_id>/like', methods=['POST'])
@require_auth
def like_comment(comment_id):
    db = get_db()
    existing = _execute_sql(db, 'SELECT id FROM comment_likes WHERE user_id = ? AND comment_id = ?', (g.user['id'], comment_id), fetch_one=True)
    if existing:
        _execute_sql(db, 'DELETE FROM comment_likes WHERE user_id = ? AND comment_id = ?', (g.user['id'], comment_id))
        _execute_sql(db, 'UPDATE comments SET like_count = like_count - 1 WHERE id = ?', (comment_id,))
        liked = False
    else:
        _execute_sql(db, 'INSERT INTO comment_likes (user_id, comment_id) VALUES (?, ?)', (g.user['id'], comment_id))
        _execute_sql(db, 'UPDATE comments SET like_count = like_count + 1 WHERE id = ?', (comment_id,))
        liked = True
    db.commit()
    new_count = _execute_sql(db, 'SELECT like_count FROM comments WHERE id = ?', (comment_id,), fetch_one=True)['like_count']
    return jsonify({'liked': liked, 'likeCount': new_count})

@app.route('/api/comments/<int:comment_id>', methods=['DELETE'])
@require_auth
def delete_comment(comment_id):
    db = get_db()
    comment = _execute_sql(db, 'SELECT user_id FROM comments WHERE id = ?', (comment_id,), fetch_one=True)
    if not comment or comment['user_id'] != g.user['id']:
        return jsonify({'error': '无权限'}), 403
    _execute_sql(db, 'DELETE FROM comments WHERE id = ?', (comment_id,))
    db.commit()
    return jsonify({'success': True})

# ---------- 好友相关 ----------
@app.route('/api/friend-requests', methods=['POST'])
@require_auth
def send_friend_request():
    data = request.json
    to_user_id = data.get('to_user_id')
    if to_user_id == g.user['id']:
        return jsonify({'error': '不能添加自己为好友'}), 400
    db = get_db()
    existing = _execute_sql(db, 'SELECT id FROM friendships WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)',
                            (g.user['id'], to_user_id, to_user_id, g.user['id']), fetch_one=True)
    if existing:
        return jsonify({'error': '已经是好友'}), 400
    pending = _execute_sql(db, 'SELECT id FROM friend_requests WHERE from_user_id = ? AND to_user_id = ? AND status = "pending"',
                           (g.user['id'], to_user_id), fetch_one=True)
    if pending:
        return jsonify({'error': '已发送过请求'}), 400
    _execute_sql(db, 'INSERT INTO friend_requests (from_user_id, to_user_id) VALUES (?, ?)', (g.user['id'], to_user_id))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/friend-requests', methods=['GET'])
@require_auth
def get_friend_requests():
    db = get_db()
    requests = _execute_sql(db, '''
        SELECT fr.*, u.nickname as from_username, u.avatar as from_avatar 
        FROM friend_requests fr 
        JOIN users u ON fr.from_user_id = u.id 
        WHERE fr.to_user_id = ? AND fr.status = 'pending'
    ''', (g.user['id'],), fetch_all=True)
    return jsonify([dict(r) for r in requests])

@app.route('/api/friend-requests/<int:request_id>/accept', methods=['POST'])
@require_auth
def accept_friend_request(request_id):
    db = get_db()
    req = _execute_sql(db, 'SELECT * FROM friend_requests WHERE id = ? AND to_user_id = ?', (request_id, g.user['id']), fetch_one=True)
    if not req:
        return jsonify({'error': '请求不存在'}), 404
    _execute_sql(db, 'UPDATE friend_requests SET status = "accepted" WHERE id = ?', (request_id,))
    _execute_sql(db, 'INSERT INTO friendships (user_id, friend_id) VALUES (?, ?), (?, ?)',
                 (req['from_user_id'], req['to_user_id'], req['to_user_id'], req['from_user_id']))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/friends', methods=['GET'])
@require_auth
def get_friends():
    db = get_db()
    friends = _execute_sql(db, '''
        SELECT u.id, u.nickname, u.avatar, u.bio
        FROM friendships f
        JOIN users u ON f.friend_id = u.id
        WHERE f.user_id = ?
    ''', (g.user['id'],), fetch_all=True)
    return jsonify([dict(f) for f in friends])

# ---------- 关注相关 ----------
@app.route('/api/follows', methods=['POST'])
@require_auth
def follow_user():
    data = request.json
    follow_id = data.get('follow_id')
    if follow_id == g.user['id']:
        return jsonify({'error': '不能关注自己'}), 400
    db = get_db()
    existing = _execute_sql(db, 'SELECT id FROM follows WHERE user_id = ? AND follow_id = ?', (g.user['id'], follow_id), fetch_one=True)
    if existing:
        _execute_sql(db, 'DELETE FROM follows WHERE user_id = ? AND follow_id = ?', (g.user['id'], follow_id))
        followed = False
    else:
        _execute_sql(db, 'INSERT INTO follows (user_id, follow_id) VALUES (?, ?)', (g.user['id'], follow_id))
        followed = True
    db.commit()
    return jsonify({'followed': followed})

@app.route('/api/following-posts', methods=['GET'])
@require_auth
def get_following_posts():
    db = get_db()
    posts = _execute_sql(db, '''
        SELECT p.*, u.nickname as username, u.avatar 
        FROM posts p 
        JOIN users u ON p.user_id = u.id 
        WHERE p.user_id IN (SELECT follow_id FROM follows WHERE user_id = ?) 
        ORDER BY p.created_at DESC
    ''', (g.user['id'],), fetch_all=True)
    result = []
    for p in posts:
        p_dict = dict(p)
        like_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM post_likes WHERE post_id = ?', (p['id'],), fetch_one=True)['cnt']
        comment_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM comments WHERE post_id = ?', (p['id'],), fetch_one=True)['cnt']
        p_dict['likeCount'] = like_count
        p_dict['commentCount'] = comment_count
        result.append(p_dict)
    return jsonify(result)

# ---------- 消息相关 ----------
@app.route('/api/messages', methods=['GET'])
@require_auth
def get_messages():
    friend_id = request.args.get('friend_id')
    if not friend_id:
        return jsonify({'error': '缺少好友ID'}), 400
    db = get_db()
    messages = _execute_sql(db, '''
        SELECT * FROM messages 
        WHERE (from_user_id = ? AND to_user_id = ?) OR (from_user_id = ? AND to_user_id = ?) 
        ORDER BY created_at ASC
    ''', (g.user['id'], friend_id, friend_id, g.user['id']), fetch_all=True)
    return jsonify([dict(m) for m in messages])

@app.route('/api/messages', methods=['POST'])
@require_auth
def send_message():
    data = request.json
    to_user_id = data.get('to_user_id')
    content = data.get('content', '').strip()
    if not content or not to_user_id:
        return jsonify({'error': '参数错误'}), 400
    db = get_db()
    _execute_sql(db, 'INSERT INTO messages (from_user_id, to_user_id, content) VALUES (?, ?, ?)',
                 (g.user['id'], to_user_id, content))
    db.commit()
    return jsonify({'success': True})

# ---------- 搜索用户 ----------
@app.route('/api/search-users', methods=['GET'])
@require_auth
def search_users():
    keyword = request.args.get('q', '').strip()
    if not keyword:
        return jsonify([])
    db = get_db()
    users = _execute_sql(db, 'SELECT id, username, nickname, avatar, bio FROM users WHERE username LIKE ? OR nickname LIKE ?',
                         (f'%{keyword}%', f'%{keyword}%'), fetch_all=True)
    return jsonify([dict(u) for u in users])

# ---------- 用户统计 ----------
@app.route('/api/user/<int:user_id>/stats', methods=['GET'])
def get_user_stats(user_id):
    db = get_db()
    post_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM posts WHERE user_id = ?', (user_id,), fetch_one=True)['cnt']
    like_sum = _execute_sql(db, 'SELECT SUM(like_count) as total FROM posts WHERE user_id = ?', (user_id,), fetch_one=True)['total']
    like_count = like_sum if like_sum else 0
    follower_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM follows WHERE follow_id = ?', (user_id,), fetch_one=True)['cnt']
    return jsonify({'postCount': post_count, 'likeCount': like_count, 'followerCount': follower_count})

@app.route('/api/user/<int:user_id>/posts', methods=['GET'])
def get_user_posts(user_id):
    db = get_db()
    posts = _execute_sql(db, 'SELECT * FROM posts WHERE user_id = ? ORDER BY created_at DESC', (user_id,), fetch_all=True)
    result = []
    for p in posts:
        p_dict = dict(p)
        like_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM post_likes WHERE post_id = ?', (p['id'],), fetch_one=True)['cnt']
        comment_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM comments WHERE post_id = ?', (p['id'],), fetch_one=True)['cnt']
        p_dict['likeCount'] = like_count
        p_dict['commentCount'] = comment_count
        result.append(p_dict)
    return jsonify(result)

@app.route('/api/user/<int:user_id>/liked-posts', methods=['GET'])
@require_auth
def get_user_liked_posts(user_id):
    if g.user['id'] != user_id:
        return jsonify({'error': '无权限'}), 403
    db = get_db()
    posts = _execute_sql(db, '''
        SELECT p.*, u.nickname as username, u.avatar 
        FROM posts p 
        JOIN post_likes pl ON p.id = pl.post_id 
        JOIN users u ON p.user_id = u.id 
        WHERE pl.user_id = ?
        ORDER BY pl.created_at DESC
    ''', (user_id,), fetch_all=True)
    return jsonify([dict(p) for p in posts])

@app.route('/api/user/<int:user_id>/commented-posts', methods=['GET'])
@require_auth
def get_user_commented_posts(user_id):
    if g.user['id'] != user_id:
        return jsonify({'error': '无权限'}), 403
    db = get_db()
    posts = _execute_sql(db, '''
        SELECT DISTINCT p.*, u.nickname as username, u.avatar, c.content as user_comment
        FROM posts p 
        JOIN comments c ON p.id = c.post_id 
        JOIN users u ON p.user_id = u.id 
        WHERE c.user_id = ?
        ORDER BY c.created_at DESC
    ''', (user_id,), fetch_all=True)
    return jsonify([dict(p) for p in posts])

# ---------- 举报相关 ----------
@app.route('/api/report', methods=['POST'])
@require_auth
def report_content():
    data = request.json
    target_type = data.get('target_type')
    target_id = data.get('target_id')
    reason = data.get('reason', '').strip()
    if target_type not in ['post', 'comment'] or not target_id or not reason:
        return jsonify({'error': '参数错误'}), 400
    db = get_db()
    _execute_sql(db, 'INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (?, ?, ?, ?)',
                 (g.user['id'], target_type, target_id, reason))
    db.commit()
    return jsonify({'success': True})

# ---------- 管理员接口 ----------
@app.route('/api/admin/stats', methods=['GET'])
@require_admin
def admin_stats():
    db = get_db()
    user_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM users', fetch_one=True)['cnt']
    post_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM posts', fetch_one=True)['cnt']
    comment_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM comments', fetch_one=True)['cnt']
    report_count = _execute_sql(db, 'SELECT COUNT(*) as cnt FROM reports WHERE status = "pending"', fetch_one=True)['cnt']
    return jsonify({'userCount': user_count, 'postCount': post_count, 'commentCount': comment_count, 'reportCount': report_count})

@app.route('/api/admin/users', methods=['GET'])
@require_admin
def admin_users():
    db = get_db()
    users = _execute_sql(db, 'SELECT id, username, nickname, role FROM users', fetch_all=True)
    return jsonify([dict(u) for u in users])

@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@require_admin
def admin_delete_user(user_id):
    db = get_db()
    _execute_sql(db, 'DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/admin/posts', methods=['GET'])
@require_admin
def admin_posts():
    db = get_db()
    posts = _execute_sql(db, '''
        SELECT p.*, u.nickname as username 
        FROM posts p 
        JOIN users u ON p.user_id = u.id 
        ORDER BY p.created_at DESC
    ''', fetch_all=True)
    return jsonify([dict(p) for p in posts])

@app.route('/api/admin/posts/<int:post_id>', methods=['DELETE'])
@require_admin
def admin_delete_post(post_id):
    db = get_db()
    _execute_sql(db, 'DELETE FROM posts WHERE id = ?', (post_id,))
    db.commit()
    return jsonify({'success': True})

@app.route('/api/admin/reports', methods=['GET'])
@require_admin
def admin_reports():
    db = get_db()
    reports = _execute_sql(db, '''
        SELECT r.*, u.nickname as reporter_name 
        FROM reports r 
        JOIN users u ON r.reporter_id = u.id 
        WHERE r.status = 'pending'
        ORDER BY r.created_at DESC
    ''', fetch_all=True)
    return jsonify([dict(r) for r in reports])

@app.route('/api/admin/reports/<int:report_id>', methods=['POST'])
@require_admin
def handle_report(report_id):
    data = request.json
    action = data.get('action')
    if action == 'resolve':
        db = get_db()
        _execute_sql(db, 'UPDATE reports SET status = "resolved" WHERE id = ?', (report_id,))
        db.commit()
        return jsonify({'success': True})
    else:
        return jsonify({'error': '无效操作'}), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)