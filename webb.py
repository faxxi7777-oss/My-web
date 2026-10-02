# app.py - PyHost Panel (Email login, Owner-only, Aryanispe-style dashboard)
from flask import Flask, request, jsonify, render_template_string, send_file, Response, stream_with_context, after_this_request
from flask_cors import CORS
from functools import wraps
import os, json, subprocess, sys, time, shutil, zipfile, uuid, re, threading
from datetime import datetime

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-secret-key-in-production')
CORS(app)

# ============================================================
# CONFIG
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECTS_DIR = os.path.join(BASE_DIR, 'projects')
PROCESSES_FILE = os.path.join(BASE_DIR, 'processes.json')
USERS_FILE = os.path.join(BASE_DIR, 'users.json')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(PROJECTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

_lock = threading.Lock()
DB_EXTENSIONS = ('.db', '.sqlite', '.sqlite3', '.db3', '.pickle', '.pkl', '.session', '.dat')

# ============================================================
# USERS
# ============================================================
DEFAULT_USERS = {
    "riyaj": {
        "password": "riyaj",
        "email": "riyaj@pyhost.com",
        "role": "owner",
        "full_name": "Riyaj Owner",
        "created": datetime.now().isoformat()
    },
    "user1": {
        "password": "user123",
        "email": "user1@pyhost.com",
        "role": "user",
        "full_name": "User One",
        "created": datetime.now().isoformat()
    },
    "user2": {
        "password": "user123",
        "email": "user2@pyhost.com",
        "role": "user",
        "full_name": "User Two",
        "created": datetime.now().isoformat()
    },
}

def load_users():
    with _lock:
        users = {}
        if os.path.exists(USERS_FILE):
            try:
                with open(USERS_FILE) as f: users = json.load(f)
            except: users = {}
        changed = False
        for u, info in DEFAULT_USERS.items():
            if u not in users:
                users[u] = info; changed = True
            else:
                for k, v in info.items():
                    if k not in users[u]:
                        users[u][k] = v; changed = True
        if 'riyaj' in users:
            users['riyaj']['role'] = 'owner'
        for u, info in users.items():
            if info.get('role') == 'admin':
                info['role'] = 'user'; changed = True
        if changed or not os.path.exists(USERS_FILE):
            with open(USERS_FILE, 'w') as f: json.dump(users, f, indent=2)
        return users

def save_users(users):
    with _lock:
        with open(USERS_FILE, 'w') as f: json.dump(users, f, indent=2)

def find_user_by_email(email):
    if not email: return None, None
    email_l = email.strip().lower()
    users = load_users()
    for un, info in users.items():
        if (info.get('email') or '').strip().lower() == email_l:
            return un, info
    return None, None

# ============================================================
# AUTH
# ============================================================
def get_username():
    return request.headers.get('X-Username') or request.args.get('username')

def require_auth(f):
    @wraps(f)
    def d(*a, **k):
        u = get_username()
        if not u: return jsonify({'error': 'Auth required'}), 401
        users = load_users()
        if u not in users: return jsonify({'error': 'User not found'}), 401
        return f(u, *a, **k)
    return d

def require_owner(f):
    @wraps(f)
    def d(u, *a, **k):
        users = load_users()
        if users.get(u, {}).get('role') != 'owner':
            return jsonify({'error': 'Owner only'}), 403
        return f(u, *a, **k)
    return d

# ============================================================
# PROCESSES
# ============================================================
def load_processes():
    if os.path.exists(PROCESSES_FILE):
        try:
            with open(PROCESSES_FILE) as f: return json.load(f)
        except: return {}
    return {}

def save_processes(p):
    with _lock:
        with open(PROCESSES_FILE, 'w') as f: json.dump(p, f, indent=2)

def is_running(pid):
    try:
        if os.name == 'nt':
            r = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV'],
                               capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                parts = [p.strip('"') for p in line.split(',')]
                if len(parts) > 1 and parts[1] == str(pid):
                    return True
            return False
        os.kill(pid, 0); return True
    except: return False

def kill_pid(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)], capture_output=True, timeout=5)
        else:
            os.killpg(os.getpgid(pid), 9)
        return True
    except:
        try: os.kill(pid, 9); return True
        except: return False

def stop_project_process(project_id):
    procs = load_processes()
    if project_id in procs:
        kill_pid(procs[project_id]['pid'])
        del procs[project_id]; save_processes(procs)
        return True
    return False

def cleanup_dead():
    procs = load_processes(); changed = False
    for pid, info in list(procs.items()):
        if not is_running(info['pid']):
            del procs[pid]; changed = True
    if changed: save_processes(procs)
    return procs

# ============================================================
# AUTO-INSTALL
# ============================================================
def detect_and_install(filepath):
    try:
        with open(filepath, encoding='utf-8', errors='ignore') as f:
            content = f.read()
        imports = list(set(re.findall(r'^(?:from|import)\s+([a-zA-Z0-9_]+)', content, re.MULTILINE)))
        missing = []
        for imp in imports:
            if imp in sys.stdlib_module_names: continue
            if imp in ('flask','telebot','requests','gunicorn','flask_cors'): continue
            try: __import__(imp)
            except ImportError: missing.append(imp)
        installed = []
        for pkg in missing:
            try:
                r = subprocess.run([sys.executable,'-m','pip','install',pkg],
                                   capture_output=True, timeout=180)
                if r.returncode == 0: installed.append(pkg)
            except: pass
        return installed
    except: return []

def install_requirements(project_dir):
    req = os.path.join(project_dir, 'requirements.txt')
    if os.path.exists(req):
        try:
            subprocess.run([sys.executable,'-m','pip','install','-r',req],
                           capture_output=True, timeout=300)
        except: pass

# ============================================================
# PROJECT MANAGEMENT
# ============================================================
def user_projects_dir(username):
    d = os.path.join(PROJECTS_DIR, username)
    os.makedirs(d, exist_ok=True)
    return d

def project_dir(username, project_id):
    return os.path.join(user_projects_dir(username), project_id)

def load_project_meta(username, project_id):
    pdir = project_dir(username, project_id)
    mf = os.path.join(pdir, 'project.json')
    if not os.path.exists(mf): return None
    try:
        with open(mf) as f: return json.load(f)
    except: return None

def save_project_meta(username, project_id, meta):
    pdir = project_dir(username, project_id)
    os.makedirs(pdir, exist_ok=True)
    with open(os.path.join(pdir, 'project.json'), 'w') as f:
        json.dump(meta, f, indent=2)

def find_project_owner(project_id):
    for uname in os.listdir(PROJECTS_DIR):
        pdir = os.path.join(PROJECTS_DIR, uname, project_id)
        if os.path.isdir(pdir) and os.path.exists(os.path.join(pdir, 'project.json')):
            return uname
    return None

def find_main_py(pdir):
    candidates = ['bot.py','main.py','app.py','index.py','run.py']
    for c in candidates:
        for f in os.listdir(pdir):
            if f.lower() == c and os.path.isfile(os.path.join(pdir, f)):
                return f
    for f in sorted(os.listdir(pdir)):
        if f.endswith('.py') and os.path.isfile(os.path.join(pdir, f)):
            return f
    return None

def run_project(username, project_id):
    pdir = project_dir(username, project_id)
    meta = load_project_meta(username, project_id)
    if not meta: return None, 'Project not found'

    main = meta.get('main_file') or find_main_py(pdir)
    if not main:
        return None, 'No .py file found in project'

    main_path = os.path.join(pdir, main)
    if not os.path.exists(main_path):
        return None, f'{main} not found'

    install_requirements(pdir)
    detect_and_install(main_path)
    stop_project_process(project_id)

    log_path = os.path.join(LOGS_DIR, f'{project_id}.log')
    log_f = open(log_path, 'w', buffering=1)
    try:
        kwargs = {}
        if os.name != 'nt':
            kwargs['preexec_fn'] = os.setsid
        proc = subprocess.Popen(
            [sys.executable, '-u', main],
            stdout=log_f, stderr=subprocess.STDOUT,
            cwd=pdir, text=True, **kwargs
        )
    except Exception as e:
        log_f.close()
        return None, str(e)

    procs = load_processes()
    procs[project_id] = {
        'pid': proc.pid,
        'project_id': project_id,
        'project_name': meta.get('name', project_id),
        'main_file': main,
        'username': username,
        'started': datetime.now().isoformat()
    }
    save_processes(procs)
    meta['main_file'] = main
    save_project_meta(username, project_id, meta)
    return proc.pid, None

def clear_project_db(username, project_id):
    pdir = project_dir(username, project_id)
    if not os.path.exists(pdir): return False, 'Project not found'
    removed = []

    data_dir = os.path.join(pdir, 'data')
    if os.path.exists(data_dir):
        for f in os.listdir(data_dir):
            fp = os.path.join(data_dir, f)
            try:
                if os.path.isfile(fp): os.remove(fp)
                elif os.path.isdir(fp): shutil.rmtree(fp)
                removed.append(f)
            except: pass

    for root, dirs, files in os.walk(pdir):
        rel = os.path.relpath(root, pdir)
        if rel == 'data' or rel.startswith('data' + os.sep): continue
        if any(part in ('venv', '.venv', 'env', '__pycache__', '.git') for part in rel.split(os.sep)): continue
        for f in files:
            if f.lower().endswith(DB_EXTENSIONS):
                try:
                    os.remove(os.path.join(root, f))
                    removed.append(f)
                except: pass
    return True, removed

def list_project_files(username, project_id):
    pdir = project_dir(username, project_id)
    if not os.path.exists(pdir): return []
    files = []
    for f in sorted(os.listdir(pdir)):
        if f == 'project.json': continue
        fp = os.path.join(pdir, f)
        if os.path.isfile(fp):
            files.append({
                'name': f,
                'size': os.path.getsize(fp),
                'is_db': f.lower().endswith(DB_EXTENSIONS),
                'is_py': f.endswith('.py')
            })
    return files

# ============================================================
# ROUTES
# ============================================================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/health')
def health():
    return jsonify({'status':'ok','time':datetime.now().isoformat()})

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json or {}
    email = (data.get('email') or '').strip()
    p = data.get('password') or ''
    if not email or not p:
        return jsonify({'error': 'Email & password required'}), 400
    un, info = find_user_by_email(email)
    if un and info.get('password') == p:
        return jsonify({
            'success': True,
            'username': un,
            'email': info.get('email', ''),
            'role': info.get('role', 'user'),
            'full_name': info.get('full_name', '')
        })
    return jsonify({'error': 'Invalid email or password'}), 401

@app.route('/api/register', methods=['POST'])
def register():
    d = request.json or {}
    email     = (d.get('email') or '').strip().lower()
    pw        = d.get('password') or ''
    full_name = (d.get('full_name') or '').strip()

    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'Valid email required'}), 400
    if not pw or len(pw) < 5:
        return jsonify({'error': 'Password must be at least 5 characters'}), 400

    existing_un, _ = find_user_by_email(email)
    if existing_un:
        return jsonify({'error': 'Email already registered'}), 400

    users = load_users()
    base = re.sub(r'[^a-z0-9]', '', email.split('@')[0].lower()) or 'user'
    username = base
    i = 1
    while username in users:
        username = f"{base}{i}"
        i += 1

    users[username] = {
        'password':  pw,
        'email':     email,
        'role':      'user',
        'full_name': full_name,
        'created':   datetime.now().isoformat()
    }
    save_users(users)
    user_projects_dir(username)

    return jsonify({
        'success': True, 'username': username,
        'email': email, 'role': 'user', 'full_name': full_name
    })

# ---------- USERS ----------
@app.route('/api/users')
@require_auth
def api_users(u):
    users = load_users()
    role = users[u]['role']
    out = []
    if role == 'owner':
        for un, info in users.items():
            out.append({
                'username': un, 'role': info['role'],
                'created': info.get('created',''),
                'full_name': info.get('full_name',''),
                'email': info.get('email',''),
                'city': info.get('city',''),
                'state': info.get('state',''),
                'country': info.get('country','')
            })
    else:
        info = users[u]
        out.append({
            'username': u, 'role': info['role'],
            'created': info.get('created',''),
            'full_name': info.get('full_name',''),
            'email': info.get('email',''),
            'city': info.get('city',''),
            'state': info.get('state',''),
            'country': info.get('country','')
        })
    return jsonify({'users': out})

@app.route('/api/users/add', methods=['POST'])
@require_auth
@require_owner
def api_add_user(u):
    d = request.json or {}
    email = (d.get('email') or '').strip().lower()
    pw = d.get('password', '')

    if not email or '@' not in email or '.' not in email.split('@')[-1]:
        return jsonify({'error': 'Valid email required'}), 400
    if not pw or len(pw) < 5:
        return jsonify({'error': 'Password must be at least 5 characters'}), 400

    existing_un, _ = find_user_by_email(email)
    if existing_un:
        return jsonify({'error': 'Email already registered'}), 400

    users = load_users()
    base = re.sub(r'[^a-z0-9]', '', email.split('@')[0].lower()) or 'user'
    username = base
    i = 1
    while username in users:
        username = f"{base}{i}"
        i += 1

    users[username] = {
        'password': pw,
        'email':    email,
        'role':     'user',
        'full_name': d.get('full_name', ''),
        'city':     d.get('city', ''),
        'state':    d.get('state', ''),
        'country':  d.get('country', 'India'),
        'created':  datetime.now().isoformat()
    }
    save_users(users)
    user_projects_dir(username)
    return jsonify({'message': 'User created', 'username': username, 'email': email})

@app.route('/api/users/update', methods=['PUT'])
@require_auth
def api_update_user(u):
    d = request.json or {}
    field = d.get('field'); value = d.get('value'); old = d.get('old_value')
    users = load_users()
    if field == 'password':
        if old and users[u]['password'] != old:
            return jsonify({'error': 'Old password wrong'}), 400
        if len(value or '') < 5:
            return jsonify({'error': 'Password must be at least 5 characters'}), 400
        users[u]['password'] = value; save_users(users)
        return jsonify({'message': 'Password updated'})
    elif field == 'email':
        new_email = (value or '').strip().lower()
        if not new_email or '@' not in new_email:
            return jsonify({'error': 'Valid email required'}), 400
        other_un, _ = find_user_by_email(new_email)
        if other_un and other_un != u:
            return jsonify({'error': 'Email already in use'}), 400
        users[u]['email'] = new_email
        save_users(users)
        return jsonify({'message': 'Email updated'})
    return jsonify({'error': 'Invalid field'}), 400

# ---------- PROJECTS ----------
@app.route('/api/projects')
@require_auth
def api_projects(u):
    users = load_users()
    role = users[u]['role']
    procs = cleanup_dead()
    out = []

    def collect(uname):
        base = os.path.join(PROJECTS_DIR, uname)
        if not os.path.isdir(base): return
        for pid in os.listdir(base):
            meta = load_project_meta(uname, pid)
            if not meta: continue
            running = pid in procs and is_running(procs[pid]['pid'])
            db_count = 0; db_size = 0
            pdir = project_dir(uname, pid)
            for root, dirs, files in os.walk(pdir):
                for f in files:
                    if f.lower().endswith(DB_EXTENSIONS):
                        db_count += 1
                        try: db_size += os.path.getsize(os.path.join(root, f))
                        except: pass
            out.append({
                'id': pid,
                'name': meta.get('name', pid),
                'owner': uname,
                'created': meta.get('created',''),
                'main_file': meta.get('main_file'),
                'running': running,
                'pid': procs.get(pid, {}).get('pid'),
                'db_count': db_count,
                'db_size': db_size,
                'plan_name': meta.get('plan_name'),
                'plan_price': meta.get('plan_price'),
                'domain': meta.get('domain'),
                'status': meta.get('status') or ('Active' if meta.get('plan_name') else None),
                'service_type': meta.get('service_type')
            })

    if role == 'owner':
        for un in os.listdir(PROJECTS_DIR):
            if os.path.isdir(os.path.join(PROJECTS_DIR, un)):
                collect(un)
    else:
        collect(u)
    return jsonify({'projects': out})

@app.route('/api/projects', methods=['POST'])
@require_auth
def api_create_project(u):
    d = request.json or {}
    name = (d.get('name') or '').strip()
    plan_name = (d.get('plan_name') or '').strip()
    plan_price = d.get('plan_price')

    if not name and not plan_name:
        return jsonify({'error': 'Project name or plan required'}), 400
    if len(name) > 60: return jsonify({'error': 'Name too long'}), 400

    pid = str(uuid.uuid4())[:8]
    pdir = project_dir(u, pid)
    os.makedirs(pdir, exist_ok=True)
    os.makedirs(os.path.join(pdir, 'data'), exist_ok=True)

    domain = None
    if plan_name:
        existing = 0
        up_dir = user_projects_dir(u)
        for p_id in os.listdir(up_dir):
            m = load_project_meta(u, p_id)
            if m and m.get('plan_name'):
                existing += 1
        base = re.sub(r'[^a-z0-9]', '', u.lower())[:12] or 'user'
        domain = f"{base}{existing + 1}.myvpsite.fun"

    meta = {
        'id': pid,
        'name': name or plan_name,
        'owner': u,
        'created': datetime.now().isoformat(),
        'main_file': None
    }
    if plan_name:
        meta['plan_name']    = plan_name
        meta['plan_price']   = plan_price
        meta['domain']       = domain
        meta['status']       = 'Active'
        meta['service_type'] = 'Shared Hosting - CPanel'

    save_project_meta(u, pid, meta)
    return jsonify({
        'message': 'Created',
        'id': pid,
        'name': name or plan_name,
        'domain': domain
    })

@app.route('/api/projects/<pid>')
@require_auth
def api_project_detail(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403
    meta = load_project_meta(owner, pid)
    procs = cleanup_dead()
    running = pid in procs and is_running(procs[pid]['pid'])
    files = list_project_files(owner, pid)
    return jsonify({
        'project': {
            'id': pid,
            'name': meta.get('name', pid),
            'owner': owner,
            'created': meta.get('created',''),
            'main_file': meta.get('main_file'),
            'running': running,
            'pid': procs.get(pid, {}).get('pid'),
            'plan_name': meta.get('plan_name'),
            'plan_price': meta.get('plan_price'),
            'domain': meta.get('domain'),
            'status': meta.get('status'),
            'service_type': meta.get('service_type')
        },
        'files': files
    })

@app.route('/api/projects/<pid>', methods=['DELETE'])
@require_auth
def api_delete_project(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403
    stop_project_process(pid)
    pdir = project_dir(owner, pid)
    if os.path.exists(pdir): shutil.rmtree(pdir)
    log = os.path.join(LOGS_DIR, f'{pid}.log')
    if os.path.exists(log): os.remove(log)
    return jsonify({'message': 'Deleted'})

@app.route('/api/projects/<pid>/upload', methods=['POST'])
@require_auth
def api_project_upload(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403

    files = request.files.getlist('files[]')
    if not files: return jsonify({'error': 'No files'}), 400

    pdir = project_dir(owner, pid)
    saved = 0
    for f in files:
        fn = f.filename
        if not fn: continue
        fn = os.path.basename(fn)
        if fn.lower().endswith('.zip'):
            tmp = os.path.join(pdir, f'__{uuid.uuid4().hex}.zip')
            f.save(tmp)
            try:
                with zipfile.ZipFile(tmp) as z:
                    for m in z.namelist():
                        target = os.path.realpath(os.path.join(pdir, m))
                        if not target.startswith(os.path.realpath(pdir) + os.sep): continue
                        z.extract(m, pdir)
                saved += 1
            except Exception as e:
                os.remove(tmp)
                return jsonify({'error': f'ZIP error: {e}'}), 400
            os.remove(tmp)
        else:
            f.save(os.path.join(pdir, fn))
            saved += 1

    main_py = find_main_py(pdir)
    meta = load_project_meta(owner, pid)
    if main_py and not meta.get('main_file'):
        meta['main_file'] = main_py
        save_project_meta(owner, pid, meta)
    return jsonify({'message': 'Uploaded', 'files_uploaded': saved, 'main_file': main_py})

@app.route('/api/projects/<pid>/deploy', methods=['POST'])
@require_auth
def api_project_deploy(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403

    d = request.json or {}
    filename = os.path.basename((d.get('filename') or 'main.py').strip()) or 'main.py'
    if not filename.endswith('.py'):
        return jsonify({'error': 'Only .py files allowed'}), 400
    code = d.get('code', '')
    if not code: return jsonify({'error': 'No code'}), 400

    pdir = project_dir(owner, pid)
    with open(os.path.join(pdir, filename), 'w', encoding='utf-8') as f:
        f.write(code)

    meta = load_project_meta(owner, pid)
    meta['main_file'] = filename
    save_project_meta(owner, pid, meta)

    install_requirements(pdir)
    detect_and_install(os.path.join(pdir, filename))
    return jsonify({'message': 'Deployed', 'filename': filename})

@app.route('/api/projects/<pid>/start', methods=['POST'])
@require_auth
def api_project_start(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403
    proc_pid, err = run_project(owner, pid)
    if err: return jsonify({'error': err}), 400
    return jsonify({'message': 'Started', 'pid': proc_pid})

@app.route('/api/projects/<pid>/stop', methods=['POST'])
@require_auth
def api_project_stop(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403
    if stop_project_process(pid):
        return jsonify({'message': 'Stopped'})
    return jsonify({'error': 'Not running'}), 404

@app.route('/api/projects/<pid>/reset-db', methods=['POST'])
@require_auth
def api_project_reset_db(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403

    was_running = False
    procs = load_processes()
    if pid in procs and is_running(procs[pid]['pid']):
        was_running = True
        stop_project_process(pid)

    ok, removed = clear_project_db(owner, pid)
    if not ok: return jsonify({'error': removed}), 400

    restarted = False
    if was_running:
        _, err = run_project(owner, pid)
        if not err: restarted = True

    return jsonify({
        'message': 'Database cleared.',
        'removed': removed,
        'restarted': restarted
    })

@app.route('/api/projects/<pid>/files/<path:filename>', methods=['DELETE'])
@require_auth
def api_project_delete_file(u, pid, filename):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403
    fn = os.path.basename(filename)
    fp = os.path.join(project_dir(owner, pid), fn)
    if not os.path.isfile(fp): return jsonify({'error': 'File not found'}), 404
    os.remove(fp)
    return jsonify({'message': 'File deleted'})

@app.route('/api/projects/<pid>/download/<path:filename>')
@require_auth
def api_project_download_file(u, pid, filename):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403
    fn = os.path.basename(filename)
    fp = os.path.join(project_dir(owner, pid), fn)
    if not os.path.isfile(fp): return jsonify({'error': 'File not found'}), 404
    return send_file(fp, as_attachment=True, download_name=fn)

@app.route('/api/projects/<pid>/download-all')
@require_auth
def api_project_download_all(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403
    pdir = project_dir(owner, pid)
    if not os.path.exists(pdir): return jsonify({'error': 'Not found'}), 404
    meta = load_project_meta(owner, pid)
    zip_path = os.path.join(BASE_DIR, f'__dl_{pid}_{uuid.uuid4().hex[:6]}.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(pdir):
            for f in files:
                if f == 'project.json': continue
                fp = os.path.join(root, f)
                arc = os.path.relpath(fp, pdir)
                z.write(fp, arc)

    @after_this_request
    def _cleanup(resp):
        try: os.remove(zip_path)
        except: pass
        return resp

    return send_file(zip_path, as_attachment=True,
                     download_name=f"{meta.get('name', pid)}.zip")

@app.route('/api/projects/<pid>/files')
@require_auth
def api_project_files(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403
    return jsonify({'files': list_project_files(owner, pid)})

@app.route('/api/processes')
@require_auth
def api_processes(u):
    procs = cleanup_dead()
    users = load_users()
    role = users[u]['role']
    out = []
    for pid, info in procs.items():
        if role == 'owner' or info.get('username') == u:
            out.append({
                'id': pid, 'pid': info['pid'],
                'project_name': info.get('project_name', ''),
                'main_file': info.get('main_file', ''),
                'username': info.get('username',''),
                'started': info.get('started','')
            })
    return jsonify({'processes': out})

@app.route('/api/logs')
@require_auth
def api_logs(u):
    chunks = []
    for f in os.listdir(LOGS_DIR):
        fp = os.path.join(LOGS_DIR, f)
        if os.path.isfile(fp):
            try:
                with open(fp, errors='ignore') as fh:
                    content = fh.read()[-5000:]
                chunks.append(f'=== {f} ===\n{content}')
            except: pass
    return jsonify({'logs': '\n\n'.join(chunks) if chunks else 'No logs'})

@app.route('/api/terminal/<pid>')
@require_auth
def api_terminal(u, pid):
    users = load_users()
    owner = find_project_owner(pid)
    if not owner: return jsonify({'error': 'Not found'}), 404
    if owner != u and users[u]['role'] != 'owner':
        return jsonify({'error': 'Forbidden'}), 403

    def gen():
        log = os.path.join(LOGS_DIR, f'{pid}.log')
        if not os.path.exists(log):
            yield f"data: {json.dumps({'type':'error','data':'No log file yet'})}\n\n"
        last = 0; last_ping = time.time()
        while True:
            procs = load_processes()
            running = pid in procs and is_running(procs[pid]['pid'])
            if os.path.exists(log):
                size = os.path.getsize(log)
                if size > last:
                    with open(log, errors='ignore') as f:
                        f.seek(last)
                        new = f.read()
                    if new:
                        yield f"data: {json.dumps({'type':'output','data':new})}\n\n"
                    last = size
            if not running:
                yield f"data: {json.dumps({'type':'end','data':'Process ended'})}\n\n"
                break
            if time.time() - last_ping > 15:
                yield ": ping\n\n"
                last_ping = time.time()
            time.sleep(0.5)
    return Response(stream_with_context(gen()), mimetype='text/event-stream',
                    headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})

@app.errorhandler(404)
def nf(e): return jsonify({'error':'Not found'}), 404
@app.errorhandler(500)
def ie(e): return jsonify({'error':'Internal error'}), 500


# ============================================================
# HTML TEMPLATE
# ============================================================
HTML_TEMPLATE = r'''
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PyHost • Client Area</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent;}
:root{
  --bg:#f5f7fa;--card:#fff;--border:#e5e7eb;--border-2:#d1d5db;
  --text:#111827;--dim:#6b7280;--faint:#9ca3af;
  --primary:#2563eb;--primary-h:#1d4ed8;--primary-l:#dbeafe;
  --orange:#f97316;--green:#10b981;--green-l:#d1fae5;
  --red:#ef4444;--red-l:#fee2e2;--yellow:#f59e0b;--yellow-l:#fef3c7;
  --purple:#8b5cf6;--purple-l:#ede9fe;
  --shadow:0 1px 3px rgba(0,0,0,0.06),0 1px 2px rgba(0,0,0,0.04);
  --shadow-md:0 4px 12px rgba(0,0,0,0.08);
  --shadow-lg:0 10px 30px rgba(0,0,0,0.10);
}
html,body{height:100%;}
body{background:var(--bg);color:var(--text);font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;
  min-height:100vh;-webkit-font-smoothing:antialiased;line-height:1.5;}

#loader{position:fixed;inset:0;background:#fff;display:flex;flex-direction:column;
  justify-content:center;align-items:center;z-index:9999;transition:opacity .5s;}
.lring{width:56px;height:56px;border-radius:50%;border:4px solid #e5e7eb;
  border-top-color:var(--primary);animation:spin .8s linear infinite;margin-bottom:20px;}
@keyframes spin{to{transform:rotate(360deg);}}
#loader .lt{font-size:0.72rem;letter-spacing:4px;font-weight:700;color:var(--dim);text-transform:uppercase;}
#loader .lp{margin-top:14px;width:180px;height:3px;background:#e5e7eb;border-radius:3px;overflow:hidden;}
#loader .lp .b{height:100%;width:0%;background:var(--primary);transition:width .2s;}

.toast{position:fixed;top:80px;left:50%;z-index:9998;transform:translateX(-50%) translateY(-160%);
  background:#fff;color:var(--text);padding:14px 22px;border-radius:12px;border:1px solid var(--border);
  box-shadow:var(--shadow-lg);font-size:0.85rem;font-weight:600;
  transition:all .4s cubic-bezier(.22,1,.36,1);opacity:0;pointer-events:none;
  text-align:center;min-width:260px;max-width:92vw;}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0);}
.toast.error{border-color:var(--red);background:#fef2f2;color:var(--red);}
.toast.error #toastMessage{color:var(--red);}
#toastSub{font-size:0.72rem;opacity:0.7;margin-top:3px;font-weight:500;}

/* ============ LOGIN PAGE (Aryanispe style) ============ */
.login-page{min-height:100vh;display:flex;flex-direction:column;background:#f3f4f6;}

/* Top navbar */
.login-topnav{background:#fff;border-bottom:1px solid var(--border);
  padding:12px 16px;display:flex;align-items:center;gap:14px;
  position:sticky;top:0;z-index:50;}
.login-topnav .ham{width:32px;height:32px;border:none;background:transparent;
  cursor:pointer;display:flex;flex-direction:column;justify-content:center;
  align-items:center;gap:5px;}
.login-topnav .ham span{width:22px;height:2.5px;background:#111827;border-radius:2px;}
.login-topnav .brand-wrap{display:flex;align-items:center;gap:8px;flex:1;min-width:0;}
.login-topnav .brand-wrap .logo-circle{width:42px;height:42px;border-radius:50%;
  background:linear-gradient(135deg,#fb923c,#ea580c);color:#fff;
  display:flex;align-items:center;justify-content:center;
  font-size:1.3rem;font-weight:900;flex-shrink:0;
  box-shadow:0 2px 6px rgba(249,115,22,0.35);}
.login-topnav .brand-wrap .brand-name{line-height:1;min-width:0;}
.login-topnav .brand-wrap .b1{font-size:1.05rem;font-weight:900;color:#ea580c;
  letter-spacing:-.5px;white-space:nowrap;}
.login-topnav .brand-wrap .b2{font-size:0.85rem;font-weight:900;color:var(--primary);
  letter-spacing:-.3px;margin-top:1px;}
.login-topnav .nav-right{display:flex;align-items:center;gap:6px;}
.login-topnav .nav-icon{padding:6px 8px;border-radius:8px;cursor:pointer;
  color:#4b5563;font-size:0.85rem;font-weight:600;
  background:transparent;border:none;font-family:inherit;
  display:flex;align-items:center;gap:4px;}
.login-topnav .nav-icon:hover{background:#f3f4f6;}
.login-topnav .nav-icon svg{display:block;}
.login-topnav .nav-icon .caret{font-size:0.6rem;opacity:.6;margin-left:1px;}

/* Main area */
.login-main{flex:1;padding:32px 16px 40px;display:flex;
  justify-content:center;align-items:flex-start;}
.login-card{width:100%;max-width:440px;background:#fff;
  border:1px solid var(--border);border-radius:10px;overflow:hidden;
  box-shadow:0 1px 2px rgba(0,0,0,0.03);}
.login-card .card-body{padding:32px 28px 28px;}
.secure-title{font-size:1.9rem;font-weight:900;color:var(--text);
  letter-spacing:-1px;margin-bottom:30px;line-height:1.15;}

.login-field{margin-bottom:18px;}
.login-field > label{display:block;font-size:0.95rem;font-weight:500;
  color:#374151;margin-bottom:8px;}
.login-field .row-lbl{display:flex;justify-content:space-between;
  align-items:center;margin-bottom:8px;}
.login-field .row-lbl label{margin:0;}
.login-field .row-lbl a{font-size:0.92rem;color:var(--primary);
  font-weight:500;text-decoration:none;}
.login-field .row-lbl a:hover{text-decoration:underline;}
.login-field input{width:100%;padding:14px 16px;
  border:1px solid var(--border-2);border-radius:8px;
  font-size:0.98rem;font-family:inherit;background:#fff;
  color:var(--text);outline:none;transition:.15s;}
.login-field input::placeholder{color:#9ca3af;}
.login-field input:focus{border-color:var(--primary);
  box-shadow:0 0 0 3px rgba(37,99,235,0.12);}
.login-field input.auto-focus{border-color:var(--primary);
  box-shadow:0 0 0 3px rgba(37,99,235,0.12);}
.login-pw-wrap{position:relative;}
.login-pw-wrap input{padding-right:44px;}
.login-pw-eye{position:absolute;right:10px;top:50%;transform:translateY(-50%);
  background:none;border:none;cursor:pointer;padding:6px;border-radius:6px;
  color:#9ca3af;font-size:0.95rem;display:flex;align-items:center;
  justify-content:center;}
.login-pw-eye:hover{background:#f3f4f6;color:#374151;}

.remember{display:flex;align-items:center;gap:10px;
  margin:6px 0 20px;font-size:0.98rem;color:#374151;}
.remember input{width:20px;height:20px;accent-color:var(--primary);
  cursor:pointer;flex-shrink:0;}
.remember label{cursor:pointer;user-select:none;margin:0;}

.btn-login{width:100%;padding:14px;border:none;border-radius:8px;
  background:var(--primary);color:#fff;font-size:1.05rem;font-weight:700;
  font-family:inherit;cursor:pointer;transition:.15s;
  display:flex;align-items:center;justify-content:center;gap:10px;}
.btn-login:hover{background:var(--primary-h);}
.btn-login:active{transform:scale(.995);}
.btn-login:disabled{opacity:.7;cursor:not-allowed;}

.btn-login-spinner{width:16px;height:16px;border-radius:50%;
  border:2px solid rgba(255,255,255,0.35);border-top-color:#fff;
  animation:spin .7s linear infinite;}

.divider-or{display:flex;align-items:center;gap:14px;
  margin:22px 0;font-size:0.88rem;color:#9ca3af;}
.divider-or::before,.divider-or::after{content:'';flex:1;height:1px;
  background:var(--border);}

.btn-google{width:100%;padding:12px;border:1px solid var(--border-2);
  border-radius:8px;background:#fff;color:#374151;
  font-size:0.98rem;font-weight:600;font-family:inherit;cursor:pointer;
  transition:.15s;display:flex;align-items:center;justify-content:center;gap:12px;}
.btn-google:hover{background:#f9fafb;border-color:#9ca3af;}

.login-card-footer{border-top:1px solid var(--border);padding:22px 28px;
  background:#fff;font-size:0.95rem;color:#374151;text-align:left;}
.login-card-footer a{color:var(--primary);font-weight:600;text-decoration:none;}
.login-card-footer a:hover{text-decoration:underline;}

/* Page footer */
.login-page-footer{padding:22px 16px 34px;text-align:center;
  color:#6b7280;font-size:0.88rem;}
.lang-select{display:inline-flex;align-items:center;gap:8px;
  color:#374151;font-weight:500;cursor:pointer;margin-bottom:16px;
  padding:6px 10px;border-radius:6px;}
.lang-select:hover{background:#e5e7eb;}
.lang-select .flag{font-size:1rem;}
.lang-select .caret{font-size:0.6rem;opacity:.6;}
.scroll-top{display:inline-flex;align-items:center;justify-content:center;
  width:34px;height:34px;border-radius:50%;background:#e5e7eb;color:#4b5563;
  font-size:1rem;cursor:pointer;margin-bottom:14px;transition:.15s;
  border:none;font-family:inherit;}
.scroll-top:hover{background:#d1d5db;}
.copy-line{line-height:1.7;color:#4b5563;font-size:0.88rem;}

.err{color:var(--red);background:var(--red-l);border:1px solid #fecaca;
  border-radius:8px;padding:11px 14px;font-size:0.85rem;margin-bottom:16px;
  display:none;}
.err.show{display:block;}
.err.ok{color:#047857;background:var(--green-l);border-color:#a7f3d0;}

@media (max-width:520px){
  .secure-title{font-size:1.7rem;}
  .login-card .card-body{padding:26px 22px 22px;}
  .login-card-footer{padding:20px 22px;font-size:0.9rem;}
  .login-topnav .brand-wrap .b1{font-size:1rem;}
  .login-topnav .brand-wrap .b2{font-size:0.8rem;}
  .login-topnav .brand-wrap .logo-circle{width:38px;height:38px;font-size:1.15rem;}
  .login-topnav .nav-icon{padding:5px 6px;font-size:0.8rem;}
}

/* ============ APP ============ */
#app{min-height:100vh;background:var(--bg);}
.topnav{background:#fff;border-bottom:1px solid var(--border);position:sticky;top:0;z-index:100;
  display:flex;align-items:center;gap:14px;padding:12px 16px;box-shadow:var(--shadow);}
.hamburger{width:38px;height:38px;border:none;background:transparent;cursor:pointer;
  display:flex;flex-direction:column;justify-content:center;align-items:center;gap:5px;border-radius:8px;}
.hamburger:hover{background:#f3f4f6;}
.hamburger span{width:22px;height:2.5px;background:var(--text);border-radius:2px;transition:.2s;}
.brand{display:flex;align-items:center;gap:9px;flex:1;}
.brand .logo{width:38px;height:38px;border-radius:50%;
  background:linear-gradient(135deg,#f97316,#ea580c);color:#fff;
  display:flex;align-items:center;justify-content:center;font-size:1.1rem;font-weight:900;}
.brand .bname{font-size:1.05rem;font-weight:900;color:var(--orange);line-height:1;letter-spacing:-.3px;}
.brand .bname span{display:block;font-size:0.78rem;color:var(--primary);font-weight:900;}
.nav-icons{display:flex;align-items:center;gap:6px;}
.nav-btn{width:38px;height:38px;border-radius:50%;border:none;background:transparent;cursor:pointer;
  display:flex;align-items:center;justify-content:center;font-size:1.15rem;color:var(--dim);transition:.15s;}
.nav-btn:hover{background:#f3f4f6;color:var(--text);}
.avatar{width:38px;height:38px;border-radius:50%;cursor:pointer;
  background:linear-gradient(135deg,#2563eb,#7c3aed);color:#fff;
  display:flex;align-items:center;justify-content:center;
  font-weight:800;font-size:0.9rem;text-transform:uppercase;
  border:2px solid #fff;box-shadow:0 0 0 1px var(--border);}

.sidebar-overlay{position:fixed;inset:0;background:rgba(0,0,0,0.4);z-index:200;
  opacity:0;pointer-events:none;transition:.25s;}
.sidebar-overlay.open{opacity:1;pointer-events:auto;}
.sidebar{position:fixed;top:0;left:0;bottom:0;width:300px;max-width:85vw;background:#fff;
  z-index:201;overflow-y:auto;transform:translateX(-100%);
  transition:.28s cubic-bezier(.22,1,.36,1);box-shadow:4px 0 24px rgba(0,0,0,0.1);}
.sidebar.open{transform:translateX(0);}
.sb-head{display:flex;justify-content:space-between;align-items:center;
  padding:18px 20px;border-bottom:1px solid var(--border);}
.sb-head .close{width:34px;height:34px;border:none;background:transparent;cursor:pointer;
  border-radius:50%;font-size:1.5rem;color:var(--dim);display:flex;align-items:center;justify-content:center;}
.sb-head .close:hover{background:#f3f4f6;color:var(--text);}
.sb-user{padding:16px 20px;border-bottom:1px solid var(--border);background:#f9fafb;}
.sb-user .un{font-size:0.98rem;font-weight:700;color:var(--text);}
.sb-user .em{font-size:0.78rem;color:var(--dim);margin-top:2px;word-break:break-all;}
.sb-user .ur{font-size:0.68rem;color:var(--primary);font-weight:700;
  letter-spacing:1.5px;text-transform:uppercase;margin-top:5px;}
.sb-section{padding:14px 12px 6px;}
.sb-label{font-size:0.65rem;font-weight:800;color:var(--faint);letter-spacing:1.8px;
  text-transform:uppercase;padding:0 12px 8px;}
.sb-item{display:flex;align-items:center;gap:14px;width:100%;padding:13px 16px;
  border:none;background:transparent;cursor:pointer;border-radius:10px;
  font-size:0.94rem;font-weight:500;color:var(--text);font-family:inherit;
  text-align:left;transition:.15s;margin-bottom:2px;}
.sb-item:hover{background:#f3f4f6;}
.sb-item.active{background:var(--primary-l);color:var(--primary);font-weight:700;}
.sb-item .ico{font-size:1.15rem;width:24px;text-align:center;flex-shrink:0;}
.sb-item.red{color:var(--red);}
.sb-item.red:hover{background:var(--red-l);}
.sb-foot{padding:20px;}
.sb-foot .btn{margin-bottom:10px;}

.container{max-width:1100px;margin:0 auto;padding:0 16px 40px;}
.page-head{padding:28px 4px 20px;}
.page-head h1{font-size:2rem;font-weight:900;letter-spacing:-1px;color:var(--text);margin-bottom:6px;line-height:1.15;}
.breadcrumb{font-size:0.9rem;color:var(--faint);font-weight:500;}
.breadcrumb a{color:var(--faint);text-decoration:none;}
.breadcrumb a:hover{color:var(--primary);}
.breadcrumb .sep{margin:0 8px;color:var(--border-2);}

.card{background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:22px;margin-bottom:16px;box-shadow:var(--shadow);}
.card h2{font-size:1.05rem;font-weight:800;color:var(--text);margin-bottom:16px;
  letter-spacing:-.3px;display:flex;align-items:center;gap:8px;}
.card h2 .ico{font-size:1.1rem;}
.card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px;}
.card-head h2{margin:0;}
.icon-btn{width:34px;height:34px;border:none;background:#f3f4f6;border-radius:8px;
  cursor:pointer;color:var(--dim);font-size:1rem;
  display:flex;align-items:center;justify-content:center;}
.icon-btn:hover{background:#e5e7eb;color:var(--text);}

.stat-carousel{margin-bottom:20px;}
.stat-track{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x mandatory;
  scroll-behavior:smooth;scrollbar-width:none;-ms-overflow-style:none;padding:2px 0;}
.stat-track::-webkit-scrollbar{display:none;}
.stat-slide{flex:0 0 100%;scroll-snap-align:start;}
@media(min-width:600px){.stat-slide{flex:0 0 calc(50% - 6px);}}
@media(min-width:900px){.stat-slide{flex:0 0 calc(33.333% - 8px);}}
.stat-big{background:#fff;border:1px solid var(--border);border-radius:14px;
  padding:22px 24px;display:flex;align-items:center;justify-content:space-between;
  box-shadow:var(--shadow);gap:14px;}
.stat-big .left{display:flex;align-items:center;gap:16px;min-width:0;}
.stat-big .ico-wrap{width:54px;height:54px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;font-size:1.5rem;flex-shrink:0;}
.stat-big .ico-wrap.green{background:var(--green-l);color:var(--green);}
.stat-big .ico-wrap.blue{background:var(--primary-l);color:var(--primary);}
.stat-big .ico-wrap.purple{background:var(--purple-l);color:var(--purple);}
.stat-big .lbl{font-size:1rem;font-weight:600;color:var(--text);}
.stat-big .n{font-size:2.2rem;font-weight:900;color:var(--primary);
  line-height:1;letter-spacing:-1.5px;}
.carousel-dots{display:flex;justify-content:center;gap:8px;margin-top:14px;}
.carousel-dots .dot{width:9px;height:9px;border-radius:50%;background:#d1d5db;
  transition:.2s;cursor:pointer;}
.carousel-dots .dot.active{background:var(--primary);transform:scale(1.15);}

.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;
  padding:12px 18px;border:1px solid var(--border-2);border-radius:10px;
  background:#fff;color:var(--text);font-size:0.88rem;font-weight:600;
  font-family:inherit;cursor:pointer;transition:.15s;text-decoration:none;white-space:nowrap;}
.btn:hover{background:#f9fafb;border-color:var(--faint);}
.btn:active{transform:scale(.98);}
.btn.primary{background:var(--primary);border-color:var(--primary);color:#fff;}
.btn.primary:hover{background:var(--primary-h);border-color:var(--primary-h);}
.btn.red{color:var(--red);border-color:#fecaca;}
.btn.red:hover{background:var(--red-l);border-color:var(--red);}
.btn.green{color:var(--green);border-color:#a7f3d0;}
.btn.green:hover{background:var(--green-l);border-color:var(--green);}
.btn.blue{color:var(--primary);border-color:#bfdbfe;}
.btn.blue:hover{background:var(--primary-l);border-color:var(--primary);}
.btn.orange{color:var(--orange);border-color:#fed7aa;}
.btn.orange:hover{background:var(--yellow-l);border-color:var(--orange);}
.btn.full{width:100%;}
.btn-ghost{padding:9px 14px;border:1px solid var(--border-2);border-radius:8px;
  background:#fff;color:var(--text);font-size:0.82rem;font-weight:600;
  cursor:pointer;font-family:inherit;display:inline-flex;align-items:center;
  gap:6px;margin-top:8px;transition:.15s;}
.btn-ghost:hover{background:#f9fafb;border-color:var(--faint);}

.row{display:flex;gap:10px;flex-wrap:wrap;}
.row .btn{flex:1;min-width:0;}

.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;}
.pcard{background:#fff;border:1px solid var(--border);border-radius:14px;padding:18px;
  cursor:pointer;transition:.2s;box-shadow:var(--shadow);position:relative;}
.pcard:hover{box-shadow:var(--shadow-md);transform:translateY(-2px);border-color:#bfdbfe;}
.pcard .pstat{display:inline-flex;align-items:center;gap:6px;
  font-size:0.65rem;font-weight:800;text-transform:uppercase;letter-spacing:1px;
  padding:4px 10px;border-radius:20px;margin-bottom:10px;}
.pcard .pstat.on{background:var(--green-l);color:#047857;}
.pcard .pstat.off{background:var(--red-l);color:#b91c1c;}
.pcard .pstat .dot{width:7px;height:7px;border-radius:50%;background:currentColor;}
.pcard .pstat.on .dot{animation:pulse 2s infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}
.pcard .pname{font-size:1.02rem;font-weight:800;color:var(--text);margin-bottom:6px;
  word-break:break-word;letter-spacing:-.3px;}
.pcard .pmeta{font-size:0.78rem;color:var(--dim);display:flex;flex-wrap:wrap;
  gap:10px;margin-top:8px;font-weight:500;}

.empty{text-align:center;padding:40px 16px;color:var(--dim);}
.empty .cube{width:110px;height:110px;margin:0 auto 20px;position:relative;
  display:flex;align-items:center;justify-content:center;}
.empty .cube::before{content:'';position:absolute;inset:0;
  border:2px dashed var(--border-2);border-radius:14px;
  animation:cubeRot 8s linear infinite;}
@keyframes cubeRot{to{transform:rotate(360deg);}}
.empty .cube .cb{width:52px;height:52px;border-radius:10px;
  background:linear-gradient(135deg,#3b82f6,#1d4ed8);
  box-shadow:0 8px 20px rgba(37,99,235,0.35);position:relative;}
.empty .cube .cb::after{content:'';position:absolute;top:50%;left:50%;
  transform:translate(-50%,-50%);width:22px;height:22px;
  border-radius:5px;background:#fff;opacity:0.9;}
.empty h3{font-size:1.05rem;color:var(--text);font-weight:700;margin-bottom:6px;}
.empty p{font-size:0.88rem;}
.empty a{color:var(--primary);font-weight:600;text-decoration:none;}
.empty a:hover{text-decoration:underline;}

.service-list{display:flex;flex-direction:column;gap:12px;}
.service-item{background:#fff;border:1px solid var(--border);border-radius:14px;
  padding:18px 20px;transition:.2s;}
.service-item:hover{border-color:#bfdbfe;box-shadow:var(--shadow-md);}
.service-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:6px;}
.service-type{font-size:1rem;font-weight:800;color:var(--text);letter-spacing:-.3px;}
.service-badge{background:var(--green-l);color:#047857;font-size:0.7rem;font-weight:800;
  letter-spacing:.5px;padding:5px 12px;border-radius:20px;white-space:nowrap;text-transform:capitalize;}
.service-plan{font-size:0.92rem;font-weight:600;color:var(--text);margin-bottom:4px;}
.service-domain{font-size:0.84rem;color:var(--faint);font-weight:500;margin-bottom:16px;word-break:break-all;}
.service-manage{display:inline-flex;align-items:center;gap:8px;
  padding:11px 22px;border:1px solid var(--border-2);border-radius:10px;
  background:#fff;color:var(--text);font-size:0.88rem;font-weight:700;
  font-family:inherit;cursor:pointer;transition:.15s;}
.service-manage:hover{background:#f9fafb;border-color:var(--faint);}
.service-manage .caret{font-size:0.7rem;opacity:.6;transition:.2s;}
.service-manage:hover .caret{transform:translateY(1px);}

input,textarea,select{width:100%;padding:12px 14px;border:1px solid var(--border-2);
  border-radius:10px;font-size:0.9rem;font-family:inherit;background:#fff;
  color:var(--text);outline:none;transition:.15s;}
input:focus,textarea:focus,select:focus{border-color:var(--primary);
  box-shadow:0 0 0 3px rgba(37,99,235,0.12);}
textarea{resize:vertical;min-height:140px;font-family:'JetBrains Mono',monospace;
  font-size:0.82rem;line-height:1.5;}
label.lbl{display:block;font-size:0.82rem;font-weight:600;color:var(--text);margin:12px 0 6px;}
label.lbl:first-child{margin-top:0;}

.form-section{margin-bottom:24px;padding-bottom:20px;border-bottom:1px solid var(--border);}
.form-section:last-child{border-bottom:none;padding-bottom:0;margin-bottom:0;}
.form-section h4{font-size:1.05rem;font-weight:800;color:var(--text);
  margin-bottom:16px;letter-spacing:-.3px;}
.required{color:var(--red);margin-left:3px;font-weight:700;}
.lbl-row{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:14px 0 6px;}
.lbl-row label{margin:0 !important;}
.hint{font-size:0.78rem;color:var(--faint);font-weight:500;}

.fitem{display:flex;justify-content:space-between;align-items:center;
  padding:14px 16px;margin:8px 0;border:1px solid var(--border);border-radius:12px;
  background:#fafafa;flex-wrap:wrap;gap:10px;transition:.15s;}
.fitem:hover{background:#f3f4f6;border-color:var(--border-2);}
.fitem .fname{font-size:0.88rem;font-weight:600;color:var(--text);
  display:flex;align-items:center;gap:10px;flex-wrap:wrap;min-width:0;}
.fitem .fsize{font-size:0.75rem;color:var(--faint);font-weight:500;}
.fitem .fmeta{font-size:0.78rem;color:var(--dim);margin-top:3px;}
.ftag{font-size:0.62rem;padding:3px 9px;border-radius:20px;font-weight:800;
  text-transform:uppercase;letter-spacing:0.8px;}
.ftag.db{background:var(--yellow-l);color:#92400e;}
.ftag.py{background:var(--green-l);color:#065f46;}
.ftag.owner{background:var(--yellow-l);color:#92400e;}
.ftag.user{background:var(--primary-l);color:#1e40af;}
.fitem .factions{display:flex;gap:6px;}
.fm{padding:6px 12px;font-size:0.75rem;font-weight:600;border:1px solid var(--border);
  border-radius:8px;background:#fff;color:var(--text);cursor:pointer;
  transition:.15s;font-family:inherit;}
.fm:hover{background:#f3f4f6;border-color:var(--faint);}
.fm.red{color:var(--red);border-color:#fecaca;}
.fm.red:hover{background:var(--red-l);}
.fm.blue{color:var(--primary);border-color:#bfdbfe;}
.fm.blue:hover{background:var(--primary-l);}

.dbwarn{background:#fffbeb;border:1px solid #fde68a;border-radius:12px;
  padding:18px;margin-bottom:14px;}
.dbwarn h3{font-size:0.95rem;font-weight:800;color:#92400e;margin-bottom:8px;
  display:flex;align-items:center;gap:6px;}
.dbwarn p{font-size:0.85rem;color:#78350f;line-height:1.6;margin-bottom:12px;}
.dbwarn code{background:#fef3c7;padding:2px 6px;border-radius:4px;
  font-size:0.78rem;font-family:'JetBrains Mono',monospace;}

.fiwrap{position:relative;width:100%;}
.fiwrap input[type=file]{position:absolute;opacity:0;width:100%;height:100%;
  cursor:pointer;top:0;left:0;z-index:2;}
.fiwrap .flabel{background:#fafafa;border:2px dashed var(--border-2);color:var(--dim);
  padding:16px;border-radius:12px;font-size:0.88rem;font-weight:600;
  display:flex;align-items:center;justify-content:center;gap:10px;
  cursor:pointer;min-height:54px;text-align:center;transition:.15s;}
.fiwrap .flabel:hover{border-color:var(--primary);color:var(--primary);background:var(--primary-l);}
.fiwrap .fcnt{background:var(--primary);color:#fff;border-radius:20px;
  padding:2px 10px;font-size:0.72rem;font-weight:700;}

.term{background:#0d1117;color:#7ee787;border-radius:12px;padding:18px;
  max-height:380px;min-height:200px;overflow-y:auto;
  font-family:'JetBrains Mono',monospace;font-size:0.82rem;
  white-space:pre-wrap;word-break:break-all;line-height:1.6;border:1px solid #21262d;}
.term::-webkit-scrollbar{width:8px;}
.term::-webkit-scrollbar-thumb{background:#30363d;border-radius:4px;}

.mov{display:none;position:fixed;inset:0;background:rgba(15,23,42,0.55);
  z-index:999;justify-content:center;align-items:center;padding:20px;
  backdrop-filter:blur(2px);}
.mov.active{display:flex;animation:fadeIn .2s;}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.mbox{background:#fff;border-radius:16px;padding:26px 24px;width:520px;
  max-width:100%;max-height:90vh;overflow-y:auto;
  box-shadow:0 25px 60px rgba(0,0,0,0.25);
  animation:slideUp .3s cubic-bezier(.22,1,.36,1);}
@keyframes slideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
.mbox h3{font-size:1.1rem;font-weight:800;color:var(--text);margin-bottom:18px;letter-spacing:-.3px;}
.mact{display:flex;gap:10px;margin-top:22px;}
.mact .btn{flex:1;padding:13px;}

.p-small{font-size:0.82rem;color:var(--dim);margin:10px 0 0;line-height:1.55;}
.hidden{display:none !important;}

.detail-head{background:#fff;border:1px solid var(--border);border-radius:14px;
  padding:22px;margin-bottom:16px;box-shadow:var(--shadow);}
.detail-head .dh-row{display:flex;justify-content:space-between;align-items:flex-start;
  flex-wrap:wrap;gap:14px;margin-bottom:16px;}
.detail-head .dh-title{font-size:1.5rem;font-weight:900;letter-spacing:-.6px;
  color:var(--text);margin-bottom:6px;}
.detail-head .dh-meta{font-size:0.82rem;color:var(--dim);font-weight:500;}

.plans-grid{display:grid;grid-template-columns:1fr;gap:18px;margin-top:14px;}
@media(min-width:700px){.plans-grid{grid-template-columns:1fr 1fr;}}
@media(min-width:1100px){.plans-grid{grid-template-columns:1fr 1fr 1fr;}}
.plan{background:#fff;border:1px solid var(--border);border-radius:14px;
  padding:32px 22px 24px;position:relative;overflow:hidden;
  box-shadow:var(--shadow);transition:.2s;display:flex;flex-direction:column;text-align:center;}
.plan:hover{box-shadow:var(--shadow-md);transform:translateY(-3px);}
.plan .ribbon{position:absolute;top:20px;left:-42px;width:160px;
  background:var(--primary);color:#fff;font-size:0.62rem;font-weight:800;
  letter-spacing:1.2px;text-transform:uppercase;padding:6px 0;
  transform:rotate(-45deg);text-align:center;
  box-shadow:0 4px 12px rgba(37,99,235,0.4);z-index:2;}
.plan .picon{width:66px;height:66px;margin:8px auto 16px;position:relative;
  display:flex;align-items:center;justify-content:center;}
.plan .picon::before{content:'';position:absolute;inset:0;
  border:2px dashed var(--border-2);border-radius:14px;}
.plan .picon .globe{width:38px;height:38px;border-radius:50%;
  background:linear-gradient(135deg,#3b82f6,#1d4ed8);
  display:flex;align-items:center;justify-content:center;color:#fff;
  font-size:1rem;font-weight:900;box-shadow:0 6px 16px rgba(37,99,235,0.35);}
.plan .pname{font-size:1.15rem;font-weight:800;color:var(--text);
  margin-bottom:12px;letter-spacing:-.3px;}
.plan .price{font-size:2.5rem;font-weight:900;color:var(--text);
  line-height:1;letter-spacing:-2px;}
.plan .period{font-size:0.82rem;color:var(--dim);margin-top:5px;margin-bottom:20px;}
.plan .feats{list-style:none;padding:0;margin:0 0 22px;flex:1;
  display:flex;flex-direction:column;gap:11px;}
.plan .feats li{font-size:0.87rem;color:var(--text);font-weight:600;
  display:flex;align-items:center;justify-content:center;gap:8px;}
.plan .order-btn{width:100%;padding:14px;border:none;border-radius:10px;
  background:var(--primary);color:#fff;font-size:0.92rem;font-weight:800;
  font-family:inherit;cursor:pointer;transition:.15s;letter-spacing:.3px;}
.plan .order-btn:hover{background:var(--primary-h);}
.plan .order-btn:active{transform:scale(.99);}

@media (max-width:600px){
  .page-head h1{font-size:1.6rem;}
  .card{padding:18px;border-radius:12px;}
  .stat-big .n{font-size:1.7rem;}
  .stat-big .ico-wrap{width:44px;height:44px;font-size:1.2rem;}
  .pgrid{grid-template-columns:1fr;}
  .row .btn{font-size:0.82rem;padding:11px 12px;}
  .detail-head .dh-title{font-size:1.25rem;}
  .brand .bname{font-size:0.98rem;}
  .brand .bname span{font-size:0.72rem;}
  .brand .logo{width:34px;height:34px;font-size:1rem;}
}
</style>
</head>
<body>

<div id="loader">
  <div class="lring"></div>
  <div class="lt">Loading</div>
  <div class="lp"><div class="b" id="loaderBar"></div></div>
</div>

<div class="toast" id="toast">
  <div id="toastMessage">SUCCESS</div>
  <div id="toastSub">Operation completed</div>
</div>

<!-- LOGIN PAGE (Aryanispe style) -->
<div class="login-page" id="loginBox">

  <!-- Top navbar -->
  <div class="login-topnav">
    <button class="ham" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
    <div class="brand-wrap">
      <div class="logo-circle">P</div>
      <div class="brand-name">
        <div class="b1">PyHost</div>
        <div class="b2">Projects</div>
      </div>
    </div>
    <div class="nav-right">
      <button class="nav-icon" title="Cart">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <circle cx="9" cy="21" r="1"/>
          <circle cx="20" cy="21" r="1"/>
          <path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/>
        </svg>
      </button>
      <button class="nav-icon">INR <span class="caret">▼</span></button>
      <button class="nav-icon" title="Account">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="12" cy="8" r="4"/>
          <path d="M4 22c0-4.4 3.6-8 8-8s8 3.6 8 8z"/>
        </svg>
        <span class="caret">▼</span>
      </button>
    </div>
  </div>

  <!-- Main -->
  <div class="login-main">

    <!-- LOGIN VIEW -->
    <div class="login-card" id="loginView">
      <div class="card-body">
        <h1 class="secure-title">Secure Client Login</h1>

        <div class="err" id="loginError">Invalid email or password</div>

        <div class="login-field">
          <label for="loginEmail">Email Address</label>
          <input id="loginEmail" type="email" placeholder="Enter email"
                 class="auto-focus" autocomplete="email">
        </div>

        <div class="login-field">
          <div class="row-lbl">
            <label for="pass">Password</label>
            <a href="#" onclick="return false;">Forgot?</a>
          </div>
          <div class="login-pw-wrap">
            <input id="pass" type="password" placeholder="Password"
                   autocomplete="current-password">
            <button class="login-pw-eye" type="button"
                    onclick="toggleLoginPw('pass', this)" aria-label="Show password">👁️</button>
          </div>
        </div>

        <div class="remember">
          <input type="checkbox" id="rememberMe" checked>
          <label for="rememberMe">Remember Me</label>
        </div>

        <button class="btn-login" id="loginBtn">Login</button>

        <div class="divider-or">or</div>

        <button class="btn-google" type="button"
                onclick="showToast('Google login not available', true);">
          <svg width="20" height="20" viewBox="0 0 48 48">
            <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.9 29.3 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.1 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.3-.4-3.5z"/>
            <path fill="#FF3D00" d="M6.3 14.7l6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.9 1.2 8 3.1l5.7-5.7C34.1 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z"/>
            <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.1-11.3-8.1l-6.5 5C9.5 39.6 16.2 44 24 44z"/>
            <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.3-2.3 4.2-4.1 5.6l6.2 5.2C36.9 39.2 44 34 44 24c0-1.3-.1-2.3-.4-3.5z"/>
          </svg>
          Sign in with Google
        </button>
      </div>

      <div class="login-card-footer">
        Not a member yet?
        <a href="#" id="goRegister">Create a New Account</a>
      </div>
    </div>

    <!-- REGISTER VIEW (hidden) -->
    <div class="login-card hidden" id="registerView">
      <div class="card-body">
        <h1 class="secure-title">Create an Account</h1>

        <div class="err" id="regError">Registration failed</div>

        <div class="login-field">
          <label for="regName">Full Name</label>
          <input id="regName" type="text" placeholder="Your full name" autocomplete="name">
        </div>

        <div class="login-field">
          <label for="regEmail">Email Address</label>
          <input id="regEmail" type="email" placeholder="Enter email" autocomplete="email">
        </div>

        <div class="login-field">
          <label for="regPass">Password</label>
          <div class="login-pw-wrap">
            <input id="regPass" type="password" placeholder="At least 5 characters"
                   autocomplete="new-password">
            <button class="login-pw-eye" type="button"
                    onclick="toggleLoginPw('regPass', this)" aria-label="Show password">👁️</button>
          </div>
        </div>

        <div class="login-field">
          <label for="regPass2">Confirm Password</label>
          <div class="login-pw-wrap">
            <input id="regPass2" type="password" placeholder="Repeat password"
                   autocomplete="new-password">
            <button class="login-pw-eye" type="button"
                    onclick="toggleLoginPw('regPass2', this)" aria-label="Show password">👁️</button>
          </div>
        </div>

        <button class="btn-login" id="registerBtn" style="margin-top:6px;">Create Account</button>
      </div>

      <div class="login-card-footer">
        Already have an account?
        <a href="#" id="goLogin">Login here</a>
      </div>
    </div>

  </div>

  <!-- Page footer -->
  <div class="login-page-footer">
    <div class="lang-select">
      <span class="flag">🇬🇧</span> English <span class="caret">▼</span>
    </div>
    <br>
    <button class="scroll-top" onclick="window.scrollTo({top:0,behavior:'smooth'})">↑</button>
    <div class="copy-line">
      Copyright © 2026 PyHost. All Rights Reserved. | Legal Name: PYHOST
    </div>
  </div>

</div>

<!-- APP -->
<div id="app" class="hidden">

  <div class="topnav">
    <button class="hamburger" id="hamburger"><span></span><span></span><span></span></button>
    <div class="brand">
      <div class="logo">P</div>
      <div class="bname">PyHost<span>Projects</span></div>
    </div>
    <div class="nav-icons">
      <button class="nav-btn" title="Notifications">🔔</button>
      <div class="avatar" id="pico">R</div>
    </div>
  </div>

  <div class="sidebar-overlay" id="sidebarOverlay"></div>

  <aside class="sidebar" id="sidebar">
    <div class="sb-head">
      <div class="brand">
        <div class="logo">P</div>
        <div class="bname">PyHost<span>Projects</span></div>
      </div>
      <button class="close" id="sidebarClose">✕</button>
    </div>
    <div class="sb-user">
      <div class="un" id="ddUser">👤 user</div>
      <div class="em" id="ddEmail">user@example.com</div>
      <div class="ur" id="ddRole">Role: User</div>
    </div>

    <div class="sb-section">
      <button class="sb-item" id="mStore"><span class="ico">🏪</span> Store</button>
      <button class="sb-item" id="mMyProjects"><span class="ico">📊</span> Dashboard</button>
      <button class="sb-item" id="mAllProjects"><span class="ico">📁</span> All Projects</button>
    </div>

    <div class="sb-section" id="usersSection">
      <div class="sb-label">Users</div>
      <button class="sb-item" id="mAllUsers"><span class="ico">👥</span> All Users</button>
      <button class="sb-item" id="mAddUser"><span class="ico">➕</span> Create User</button>
    </div>

    <div class="sb-section">
      <div class="sb-label">Account</div>
      <button class="sb-item" id="mChangeUser"><span class="ico">✏️</span> Change Email</button>
      <button class="sb-item" id="mChangePass"><span class="ico">🔑</span> Change Password</button>
    </div>

    <div class="sb-foot">
      <button class="btn full red" id="mLogout"><span>🚪</span> Logout</button>
    </div>
  </aside>

  <div class="container">

    <!-- DASHBOARD -->
    <div id="pageProjects">
      <div class="page-head">
        <h1>My Dashboard</h1>
        <div class="breadcrumb">
          <a href="#" onclick="return false;">Portal Home</a>
          <span class="sep">/</span> Client Area
        </div>
      </div>

      <div class="stat-carousel">
        <div class="stat-track" id="statTrack">
          <div class="stat-slide">
            <div class="stat-big">
              <div class="left">
                <div class="ico-wrap green">📊</div>
                <div class="lbl">Active Services</div>
              </div>
              <div class="n" id="sRunning">0</div>
            </div>
          </div>
          <div class="stat-slide">
            <div class="stat-big">
              <div class="left">
                <div class="ico-wrap blue">📦</div>
                <div class="lbl">Projects</div>
              </div>
              <div class="n" id="sProjects">0</div>
            </div>
          </div>
          <div class="stat-slide">
            <div class="stat-big">
              <div class="left">
                <div class="ico-wrap purple">📄</div>
                <div class="lbl">Files</div>
              </div>
              <div class="n" id="sFiles">0</div>
            </div>
          </div>
        </div>
        <div class="carousel-dots" id="carouselDots">
          <span class="dot active" data-i="0"></span>
          <span class="dot" data-i="1"></span>
          <span class="dot" data-i="2"></span>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h2>Your Active Products / Services</h2>
          <button class="icon-btn" title="Options">⚙</button>
        </div>
        <div id="projectGrid"></div>
      </div>

      <div style="display:none;">
        <button id="newProjectBtn"></button>
        <button id="refreshProjectsBtn"></button>
        <button id="startAllBtn"></button>
        <button id="stopAllBtn"></button>
        <div id="runningNodes"></div>
      </div>
    </div>

    <!-- STORE -->
    <div id="pageStore" class="hidden">
      <div class="page-head">
        <h1>Choose the Perfect<br>Python Hosting Plan</h1>
        <div class="breadcrumb">
          <a href="#" onclick="return false;">Portal Home</a>
          <span class="sep">/</span> Store
        </div>
      </div>
      <p style="font-size:1rem;color:var(--dim);margin:6px 4px 20px;line-height:1.6;font-weight:500;">
        High Performance Python Hosting Powered by SSD Storage, Free SSL &amp; Enterprise Security.
      </p>

      <select id="storeCategory" style="margin-bottom:16px;font-weight:600;font-size:0.95rem;">
        <option>🐍 Python Bot Hosting</option>
        <option>🌐 Web App Hosting</option>
        <option>⚡ VPS Hosting</option>
      </select>

      <div class="plans-grid" id="plansGrid"></div>
    </div>

    <!-- PROJECT DETAIL -->
    <div id="pageProject" class="hidden">
      <div class="page-head">
        <h1 id="pdNameTitle">Project</h1>
        <div class="breadcrumb">
          <a href="#" id="bcHome">Portal Home</a>
          <span class="sep">/</span>
          <a href="#" id="bcProjects">Projects</a>
          <span class="sep">/</span> <span id="pdName">Project</span>
        </div>
      </div>

      <div class="detail-head">
        <div class="dh-row">
          <div style="min-width:0;">
            <div class="dh-title" id="pdName2">Project</div>
            <div class="dh-meta" id="pdMeta">—</div>
          </div>
          <div id="pdStat" class="pstat off">
            <span class="dot"></span>
            <span id="pdStatText">STOPPED</span>
          </div>
        </div>
        <div class="row">
          <button class="btn green" id="pdStart">▶ Start</button>
          <button class="btn red" id="pdStop">■ Stop</button>
          <button class="btn orange" id="pdTerminal">💻 Terminal</button>
          <button class="btn blue" id="pdDownloadAll">📦 Download</button>
          <button class="btn red" id="pdDelete">🗑 Delete</button>
        </div>
      </div>

      <div class="card">
        <h2><span class="ico">🗄</span> Database / Fresh Start</h2>
        <div class="dbwarn">
          <h3>⚠️ Fresh Start</h3>
          <p>Ye button project ke saare <b>database files</b> (<code>.db</code>, <code>.sqlite</code>, <code>.session</code>, <code>.pickle</code>, aur <code>data/</code> folder) delete kar dega. Bot agli baar start hone par bilkul <b>naya/fresh</b> hoga.</p>
          <button class="btn red full" id="pdFresh">🧹 Make Bot Fresh (Clear Database)</button>
        </div>
        <p class="p-small" id="dbInfo">No database files yet.</p>
      </div>

      <div class="card">
        <h2><span class="ico">📤</span> Upload Files</h2>
        <div class="row">
          <div class="fiwrap" style="flex:2;min-width:180px;">
            <input type="file" id="fileInput" multiple>
            <div class="flabel" id="fileLabel">
              📎 Select Files <span class="fcnt">0</span>
            </div>
          </div>
          <button class="btn green" id="uploadBtn" style="flex:1;min-width:110px;">📦 Upload</button>
        </div>
        <p class="p-small" id="uploadStatus">Upload .py, .zip, requirements.txt, ya koi bhi file. Zip auto-extract hogi.</p>
      </div>

      <div class="card">
        <h2><span class="ico">⌨</span> Deploy Code</h2>
        <label class="lbl">Filename</label>
        <input id="pyFilename" value="main.py">
        <label class="lbl">Code</label>
        <textarea id="pyCodeArea" placeholder="# Paste your Python code here..."></textarea>
        <button class="btn primary full" id="deployBtn" style="margin-top:12px;">▶ Save & Deploy</button>
        <p class="p-small" id="deployStatus">Save karta hai aur required modules auto-install karta hai.</p>
      </div>

      <div class="card">
        <h2><span class="ico">📁</span> Project Files</h2>
        <div id="fileList">
          <div style="opacity:0.5;text-align:center;padding:16px;font-size:0.88rem;">No files</div>
        </div>
      </div>

      <button class="btn blue full" id="backBtn">← Back to Dashboard</button>
    </div>

    <!-- USERS -->
    <div id="pageUsers" class="hidden">
      <div class="page-head">
        <h1>Users</h1>
        <div class="breadcrumb">
          <a href="#" onclick="return false;">Portal Home</a>
          <span class="sep">/</span> Users
        </div>
      </div>
      <div class="card">
        <div id="usersList"><div style="opacity:0.5;text-align:center;padding:16px;">Loading...</div></div>
      </div>
      <button class="btn blue full" id="backUsers">← Back to Dashboard</button>
    </div>

    <!-- ALL PROJECTS -->
    <div id="pageAllProjects" class="hidden">
      <div class="page-head">
        <h1>All Projects</h1>
        <div class="breadcrumb">
          <a href="#" onclick="return false;">Portal Home</a>
          <span class="sep">/</span> All Projects
        </div>
      </div>
      <div class="card">
        <div class="pgrid" id="allProjectGrid"></div>
      </div>
      <button class="btn blue full" id="backAllProjects">← Back to Dashboard</button>
    </div>

    <!-- TERMINAL -->
    <div id="pageTerminal" class="hidden">
      <div class="page-head">
        <h1>Live Terminal</h1>
        <div class="breadcrumb">
          <a href="#" onclick="return false;">Portal Home</a>
          <span class="sep">/</span>
          <a href="#" id="bcProjects2">Projects</a>
          <span class="sep">/</span> Terminal
        </div>
      </div>
      <div class="card">
        <div class="row" style="margin-bottom:14px;">
          <button class="btn blue" id="tConnect">▶ Connect</button>
          <button class="btn red" id="tDisconnect">■ Disconnect</button>
          <button class="btn orange" id="tClear">✕ Clear</button>
        </div>
        <div class="term" id="termOut">
          <div style="opacity:0.5;text-align:center;padding:16px;color:#8b949e;">Click Connect to view live output</div>
        </div>
      </div>
      <button class="btn blue full" id="backTerminal">← Back</button>
    </div>

  </div>
</div>

<!-- MODAL -->
<div class="mov" id="modal">
  <div class="mbox">
    <h3 id="mTitle">Modal</h3>
    <div id="mBody"></div>
    <div class="mact">
      <button class="btn" id="mCancel">Cancel</button>
      <button class="btn primary" id="mConfirm">Confirm</button>
    </div>
  </div>
</div>

<script>
(function(){
"use strict";
let currentUser = null, currentEmail = null, currentRole = 'user', isLoggedIn = false;
let projects = [], currentProject = null, projectFiles = [];
let termSource = null, refreshTimer = null;

const $ = id => document.getElementById(id);
const toast = $('toast'), toastMsg = $('toastMessage'), toastSub = $('toastSub');

function showToast(msg, isErr, sub){
  toast.className = 'toast';
  if (isErr) toast.classList.add('error');
  toastMsg.textContent = msg || 'OK';
  toastSub.textContent = sub || '';
  toast.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(()=>toast.classList.remove('show'), 3000);
}
window.showToast = showToast;

function showPage(id){
  ['pageProjects','pageProject','pageUsers','pageAllProjects','pageTerminal','pageStore']
    .forEach(p => $(p).classList.add('hidden'));
  $(id).classList.remove('hidden');
  window.scrollTo({top:0,behavior:'smooth'});
  const map = {
    pageProjects:'mMyProjects', pageStore:'mStore', pageUsers:'mAllUsers',
    pageAllProjects:'mAllProjects'
  };
  document.querySelectorAll('.sb-item').forEach(b => b.classList.remove('active'));
  if (map[id] && $(map[id])) $(map[id]).classList.add('active');
}

async function api(path, method, body){
  const opts = {
    method: method || 'GET',
    headers: {'Content-Type':'application/json','X-Username': currentUser || ''}
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const data = await res.json().catch(()=>({error:'Bad JSON'}));
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}

async function apiUpload(path, formData){
  const res = await fetch(path, {
    method:'POST',
    headers:{'X-Username': currentUser || ''},
    body: formData
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Upload failed');
  return data;
}

// SIDEBAR
function openSidebar(){ $('sidebar').classList.add('open'); $('sidebarOverlay').classList.add('open'); }
function closeSidebar(){ $('sidebar').classList.remove('open'); $('sidebarOverlay').classList.remove('open'); }
$('hamburger').onclick = openSidebar;
$('sidebarClose').onclick = closeSidebar;
$('sidebarOverlay').onclick = closeSidebar;
$('pico').onclick = openSidebar;

// ============ LOGIN PW TOGGLE ============
function toggleLoginPw(id, btn){
  const el = $(id);
  if (!el) return;
  el.type = el.type === 'password' ? 'text' : 'password';
  btn.textContent = el.type === 'password' ? '👁️' : '🙈';
}
window.toggleLoginPw = toggleLoginPw;

// ============ VIEW SWITCH (Login <-> Register) ============
$('goRegister').onclick = e => {
  e.preventDefault();
  $('loginView').classList.add('hidden');
  $('registerView').classList.remove('hidden');
  window.scrollTo({top:0, behavior:'smooth'});
  setTimeout(() => $('regName').focus(), 100);
};

$('goLogin').onclick = e => {
  e.preventDefault();
  $('registerView').classList.add('hidden');
  $('loginView').classList.remove('hidden');
  window.scrollTo({top:0, behavior:'smooth'});
  setTimeout(() => $('loginEmail').focus(), 100);
};

const _le = $('loginEmail');
if (_le) _le.addEventListener('input', function(){ this.classList.remove('auto-focus'); });

// LOGIN
async function doLogin(){
  const email = $('loginEmail').value.trim();
  const p = $('pass').value.trim();
  const err = $('loginError');
  const btn = $('loginBtn');

  const showLerr = (msg) => {
    err.textContent = msg; err.classList.remove('ok');
    err.classList.add('show');
    clearTimeout(err._t);
    err._t = setTimeout(()=>err.classList.remove('show'), 3500);
  };

  if (!email || !p) return showLerr('Enter email & password');

  btn.disabled = true;
  const orig = btn.textContent;
  btn.innerHTML = '<span class="btn-login-spinner"></span> Signing in...';

  try {
    const d = await api('/api/login','POST',{email, password: p});
    currentUser  = d.username;
    currentEmail = d.email;
    currentRole  = d.role;
    isLoggedIn   = true;
    localStorage.setItem('loggedInUser', JSON.stringify({
      username: d.username, email: d.email, role: d.role
    }));
    err.classList.remove('show');
    initApp();
  } catch(e){
    showLerr(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
}
$('loginBtn').onclick = doLogin;

// REGISTER
$('registerBtn').onclick = async () => {
  const name  = $('regName').value.trim();
  const email = $('regEmail').value.trim();
  const p1    = $('regPass').value;
  const p2    = $('regPass2').value;
  const err   = $('regError');
  const btn   = $('registerBtn');

  const showRerr = (msg) => {
    err.textContent = msg; err.classList.remove('ok');
    err.classList.add('show');
    clearTimeout(err._t);
    err._t = setTimeout(() => err.classList.remove('show'), 3500);
  };

  if (!name)                          return showRerr('Full name required');
  if (!email || !email.includes('@')) return showRerr('Valid email required');
  if (!p1 || p1.length < 5)           return showRerr('Password must be at least 5 characters');
  if (p1 !== p2)                      return showRerr('Passwords do not match');

  btn.disabled = true;
  const orig = btn.textContent;
  btn.innerHTML = '<span class="btn-login-spinner"></span> Creating account...';

  try {
    const res = await fetch('/api/register', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ email, password: p1, full_name: name })
    });
    const d = await res.json();
    if (!res.ok) throw new Error(d.error || 'Registration failed');

    err.textContent = '✓ Account created! Please login.';
    err.classList.add('ok', 'show');
    setTimeout(() => {
      $('loginEmail').value = email;
      $('pass').value = '';
      $('goLogin').click();
      err.classList.remove('ok', 'show');
    }, 1200);
  } catch(e) {
    showRerr(e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = orig;
  }
};

document.addEventListener('keydown', e => {
  if (e.key !== 'Enter') return;
  if ($('loginBox').classList.contains('hidden')) return;
  if (!$('loginView').classList.contains('hidden')) {
    doLogin();
  } else if (!$('registerView').classList.contains('hidden')) {
    $('registerBtn').click();
  }
});

function initApp(){
  $('loginBox').classList.add('hidden');
  $('app').classList.remove('hidden');
  $('pico').textContent = currentUser.charAt(0).toUpperCase();
  $('ddUser').textContent = '👤 ' + currentUser;
  $('ddEmail').textContent = currentEmail || '';
  $('ddRole').textContent = 'Role: ' + currentRole.charAt(0).toUpperCase() + currentRole.slice(1);

  const ownerOnly = ['mAllProjects','mAllUsers','mAddUser','usersSection'];
  ownerOnly.forEach(id => { if ($(id)) $(id).style.display = currentRole === 'owner' ? '' : 'none'; });

  showPage('pageProjects');
  loadProjects();
  loadRunning();
  renderPlans();

  if (refreshTimer) clearInterval(refreshTimer);
  refreshTimer = setInterval(() => {
    if (!$('pageProjects').classList.contains('hidden')) {
      loadProjects(); loadRunning();
    }
  }, 12000);
}

function logout(){
  if (termSource){ termSource.close(); termSource = null; }
  if (refreshTimer){ clearInterval(refreshTimer); refreshTimer = null; }
  localStorage.removeItem('loggedInUser');
  currentUser = null; currentEmail = null; isLoggedIn = false; currentProject = null;
  $('app').classList.add('hidden');
  $('loginBox').classList.remove('hidden');
  $('loginEmail').value = ''; $('pass').value = '';
  $('goLogin').click();
  closeSidebar();
  showToast('Signed out');
}
$('mLogout').onclick = logout;

$('mMyProjects').onclick = () => { closeSidebar(); showPage('pageProjects'); loadProjects(); loadRunning(); };
$('mStore').onclick = () => { closeSidebar(); showPage('pageStore'); renderPlans(); };
$('mAllUsers').onclick = () => { closeSidebar(); showPage('pageUsers'); loadUsers(); };
$('mAddUser').onclick = () => { closeSidebar(); promptAddUser(); };
$('mAllProjects').onclick = () => { closeSidebar(); showPage('pageAllProjects'); loadAllProjects(); };
$('mChangeUser').onclick = () => { closeSidebar(); promptChangeEmail(); };
$('mChangePass').onclick = () => { closeSidebar(); promptChangePassword(); };

$('backUsers').onclick = () => showPage('pageProjects');
$('backAllProjects').onclick = () => showPage('pageProjects');
$('backTerminal').onclick = () => { disconnectTerm(); showPage(currentProject ? 'pageProject' : 'pageProjects'); };
$('bcHome').onclick = e => { e.preventDefault(); showPage('pageProjects'); loadProjects(); };
$('bcProjects').onclick = e => { e.preventDefault(); showPage('pageProjects'); loadProjects(); };
$('bcProjects2').onclick = e => { e.preventDefault(); showPage('pageProjects'); loadProjects(); };
$('storeCategory').onchange = e => {
  showToast('Category: ' + e.target.value.replace(/^\S+\s/, ''));
  renderPlans();
};

const PLANS = [
  { name:'PyHost Baby Plan', price:39, ribbon:'Best for Beginner',
    feats:['🐍 1 Project','💾 512 MB RAM','💿 1 GB SSD Storage',
           '🤖 1 Bot Instance','🔄 Auto Restart','💬 Community Support'] },
  { name:'PyHost Starter Plan', price:69, ribbon:'Best in Price',
    feats:['🐍 3 Projects','💾 1 GB RAM','💿 3 GB SSD Storage',
           '🤖 3 Bot Instances','💾 Auto DB Backup','⭐ Priority Support'] },
  { name:'PyHost Younger Plan', price:99, ribbon:'Most Popular',
    feats:['🐍 5 Projects','💾 2 GB RAM','💿 10 GB SSD Storage',
           '🤖 5 Bot Instances','💾 Auto DB Backup','🌐 Custom Domain',
           '⭐ Priority Support'] },
  { name:'PyHost Advanced Plan', price:149, ribbon:'Business Choice',
    feats:['🐍 10 Projects','💾 4 GB RAM','💿 25 GB SSD Storage',
           '🤖 10 Bot Instances','🌐 Custom Domain','🔑 API Access',
           '⭐ Priority Support'] },
  { name:'PyHost Ultimate Plan', price:199, ribbon:'Enterprise Plan',
    feats:['🐍 Unlimited Projects','💾 8 GB RAM','💿 100 GB SSD Storage',
           '🤖 Unlimited Bots','🌐 Custom Domain','🔑 API Access',
           '🛡️ Dedicated Support'] },
];

function renderPlans(){
  const g = $('plansGrid');
  if (!g) return;
  g.innerHTML = PLANS.map(p => `
    <div class="plan">
      <div class="ribbon">${esc(p.ribbon)}</div>
      <div class="picon"><div class="globe">🐍</div></div>
      <div class="pname">${esc(p.name)}</div>
      <div class="price">₹${p.price}</div>
      <div class="period">Monthly</div>
      <ul class="feats">
        ${p.feats.map(f => `<li>${esc(f)}</li>`).join('')}
      </ul>
      <button class="order-btn"
        onclick="window.__orderPlan('${esc(p.name)}', ${p.price})">Order Now</button>
    </div>
  `).join('');
}

window.__orderPlan = (name, price) => {
  showModal('🛒 Order Plan',
    `<div style="text-align:center;font-size:0.92rem;line-height:1.65;">
      <p style="margin-bottom:14px;">Confirm your order:</p>
      <div style="background:#f9fafb;border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:14px;">
        <div style="font-size:1.05rem;font-weight:800;margin-bottom:6px;">${esc(name)}</div>
        <div style="font-size:1.6rem;font-weight:900;color:var(--primary);">₹${price}
          <span style="font-size:0.85rem;color:var(--dim);font-weight:500;">/ month</span></div>
      </div>
      <p style="color:var(--dim);font-size:0.85rem;">Ek hosting service turant activate hogi. Aap dashboard me Manage kar sakte hain.</p>
    </div>`,
    'ORDER NOW', async () => {
      try {
        const r = await api('/api/projects', 'POST', {
          plan_name: name,
          plan_price: price
        });
        closeModal();
        showToast('Order placed ✨', false, `Domain: ${r.domain}`);
        showPage('pageProjects');
        await loadProjects();
        loadRunning();
      } catch(e){ showToast(e.message, true); }
    });
};

async function loadProjects(){
  try {
    const d = await api('/api/projects');
    projects = d.projects || [];
    renderProjects();
    $('sProjects').textContent = projects.length;
    $('sRunning').textContent = projects.filter(p => p.running).length;
    $('sFiles').textContent = projects.length
      ? (projects.reduce((s,p)=>s+(p.db_count||0),0) || '—')
      : 0;
  } catch(e){ console.error(e); }
}

function renderProjects(){
  const g = $('projectGrid');
  if (!projects.length){
    g.className = '';
    g.innerHTML = `
      <div class="empty">
        <div class="cube"><div class="cb"></div></div>
        <h3>No Active Services Found</h3>
        <p><a href="#" id="emptyCreate2">Order New Services</a></p>
      </div>`;
    const e2 = $('emptyCreate2');
    if (e2) e2.onclick = e => { e.preventDefault(); showPage('pageStore'); renderPlans(); };
    return;
  }
  const hasPlans = projects.some(p => p.plan_name);
  if (hasPlans){
    g.className = 'service-list';
    g.innerHTML = projects.map(p => {
      const type = p.service_type || (p.plan_name ? 'Shared Hosting - CPanel' : 'Custom Project');
      const planLine = p.plan_name || p.name;
      const domainLine = p.domain || (p.owner + '-' + p.id + '.myvpsite.fun');
      const status = p.status || 'Active';
      const badgeColor = (status === 'Active') ? '' : 'background:var(--red-l);color:#b91c1c;';
      return `
        <div class="service-item">
          <div class="service-head">
            <div class="service-type">${esc(type)}</div>
            <div class="service-badge" style="${badgeColor}">${esc(status)}</div>
          </div>
          <div class="service-plan">${esc(planLine)}</div>
          <div class="service-domain">${esc(domainLine)}</div>
          <button class="service-manage" onclick="window.__openProject('${p.id}')">
            Manage <span class="caret">▼</span>
          </button>
        </div>`;
    }).join('');
    return;
  }
  g.className = 'pgrid';
  g.innerHTML = projects.map(p => `
    <div class="pcard" onclick="window.__openProject('${p.id}')">
      <div class="pstat ${p.running ? 'on' : 'off'}">
        <span class="dot"></span>${p.running ? 'RUNNING' : 'STOPPED'}
      </div>
      <div class="pname">${esc(p.name)}</div>
      <div class="pmeta">
        <span>👤 ${esc(p.owner)}</span>
        <span>📄 ${p.main_file ? esc(p.main_file) : 'no entry'}</span>
        <span>🗄 ${p.db_count} db</span>
      </div>
    </div>
  `).join('');
}

async function loadAllProjects(){
  try {
    const d = await api('/api/projects');
    const list = d.projects || [];
    const g = $('allProjectGrid');
    if (!list.length){
      g.innerHTML = '<div style="grid-column:1/-1;opacity:0.5;text-align:center;padding:20px;">No projects</div>';
      return;
    }
    g.innerHTML = list.map(p => `
      <div class="pcard" onclick="window.__openProject('${p.id}')">
        <div class="pstat ${p.running ? 'on' : 'off'}">
          <span class="dot"></span>${p.running ? 'RUNNING' : 'STOPPED'}
        </div>
        <div class="pname">${esc(p.name)}</div>
        <div class="pmeta"><span>👤 ${esc(p.owner)}</span><span>🗄 ${p.db_count} db</span></div>
      </div>
    `).join('');
  } catch(e){ showToast(e.message, true); }
}

window.__openProject = async function(pid){
  try {
    const d = await api('/api/projects/' + pid);
    currentProject = d.project;
    projectFiles = d.files || [];
    renderProjectDetail();
    showPage('pageProject');
  } catch(e){ showToast(e.message, true); }
};

function renderProjectDetail(){
  const p = currentProject;
  $('pdNameTitle').textContent = p.name;
  $('pdName').textContent = p.name;
  $('pdName2').textContent = p.name;
  $('pdMeta').textContent = `👤 ${p.owner} · 📄 ${p.main_file || 'no entry'} · Created ${p.created ? new Date(p.created).toLocaleDateString() : '—'}`;
  const st = $('pdStat');
  st.className = 'pstat ' + (p.running ? 'on' : 'off');
  st.style.cssText = `padding:6px 14px;border-radius:20px;font-size:0.72rem;font-weight:800;
    letter-spacing:1px;text-transform:uppercase;display:inline-flex;align-items:center;gap:8px;
    background:${p.running?'var(--green-l)':'var(--red-l)'};
    color:${p.running?'#047857':'#b91c1c'};`;
  $('pdStatText').textContent = p.running ? 'RUNNING · PID ' + p.pid : 'STOPPED';

  const fl = $('fileList');
  if (!projectFiles.length){
    fl.innerHTML = '<div style="opacity:0.5;text-align:center;padding:16px;font-size:0.88rem;">No files yet</div>';
  } else {
    fl.innerHTML = projectFiles.map(f => {
      const tag = f.is_db ? '<span class="ftag db">DB</span>'
                : f.is_py ? '<span class="ftag py">PY</span>' : '';
      return `<div class="fitem">
        <div class="fname">${esc(f.name)} ${tag} <span class="fsize">${(f.size/1024).toFixed(1)} KB</span></div>
        <div class="factions">
          <button class="fm blue" onclick="window.__dlFile('${esc(p.id)}','${esc(f.name)}')">⬇</button>
          <button class="fm red" onclick="window.__delFile('${esc(p.id)}','${esc(f.name)}')">🗑</button>
        </div>
      </div>`;
    }).join('');
  }

  const dbFiles = projectFiles.filter(f => f.is_db);
  if (!dbFiles.length){
    $('dbInfo').textContent = 'No database files detected yet.';
  } else {
    const total = dbFiles.reduce((s, f) => s + f.size, 0);
    $('dbInfo').textContent = `${dbFiles.length} database file(s) · ${(total/1024).toFixed(1)} KB`;
  }
  $('pyFilename').value = p.main_file || 'main.py';
}

window.__dlFile = function(pid, fname){
  const url = `/api/projects/${pid}/download/${encodeURIComponent(fname)}`;
  fetch(url, { headers: { 'X-Username': currentUser } })
    .then(async r => {
      if (!r.ok) throw new Error('Download failed');
      const b = await r.blob();
      const u = URL.createObjectURL(b);
      const a = document.createElement('a');
      a.href = u; a.download = fname;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(u);
      showToast('Download started');
    }).catch(e => showToast(e.message, true));
};

window.__delFile = function(pid, fname){
  showModal('Delete File',
    `<p style="text-align:center;font-size:0.9rem;line-height:1.6;">Delete <b>${esc(fname)}</b>?<br>Ye action undo nahi hoga.</p>`,
    'DELETE', async () => {
      try {
        await api(`/api/projects/${pid}/files/${encodeURIComponent(fname)}`, 'DELETE');
        closeModal();
        showToast('File deleted');
        window.__openProject(pid);
      } catch(e){ showToast(e.message, true); }
    });
};

$('backBtn').onclick = () => { showPage('pageProjects'); loadProjects(); loadRunning(); };
$('pdStart').onclick = async () => {
  if (!currentProject) return;
  try { showToast('Starting...'); await api(`/api/projects/${currentProject.id}/start`, 'POST'); showToast('Started'); window.__openProject(currentProject.id); }
  catch(e){ showToast(e.message, true); }
};
$('pdStop').onclick = async () => {
  if (!currentProject) return;
  try { showToast('Stopping...'); await api(`/api/projects/${currentProject.id}/stop`, 'POST'); showToast('Stopped'); window.__openProject(currentProject.id); }
  catch(e){ showToast(e.message, true); }
};
$('pdTerminal').onclick = () => {
  if (!currentProject) return;
  showPage('pageTerminal');
  $('termOut').innerHTML = '<div style="opacity:0.5;text-align:center;padding:16px;color:#8b949e;">Click Connect to view live output</div>';
};
$('pdDownloadAll').onclick = () => {
  if (!currentProject) return;
  const url = `/api/projects/${currentProject.id}/download-all`;
  fetch(url, { headers: { 'X-Username': currentUser } })
    .then(async r => {
      if (!r.ok) throw new Error('Download failed');
      const b = await r.blob();
      const cd = r.headers.get('Content-Disposition');
      let name = currentProject.name + '.zip';
      const m = cd && cd.match(/filename="?([^"]+)"?/);
      if (m) name = m[1];
      const u = URL.createObjectURL(b);
      const a = document.createElement('a');
      a.href = u; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(u);
      showToast('Download started');
    }).catch(e => showToast(e.message, true));
};
$('pdDelete').onclick = () => {
  if (!currentProject) return;
  showModal('Delete Project',
    `<p style="text-align:center;font-size:0.9rem;line-height:1.6;">Delete <b>${esc(currentProject.name)}</b>?<br>Bot stop hoga aur saari files delete ho jaayengi.</p>`,
    'DELETE', async () => {
      try {
        await api(`/api/projects/${currentProject.id}`, 'DELETE');
        closeModal();
        showToast('Project deleted');
        currentProject = null;
        showPage('pageProjects');
        loadProjects(); loadRunning();
      } catch(e){ showToast(e.message, true); }
    });
};
$('pdFresh').onclick = () => {
  if (!currentProject) return;
  showModal('🧹 Make Bot Fresh',
    `<div style="font-size:0.9rem;line-height:1.65;">
      <p style="margin-bottom:10px;">Ye project ke saare database files delete kar dega:</p>
      <p style="text-align:left;background:#f9fafb;padding:12px;border-radius:10px;font-size:0.82rem;">
        • <code>data/</code> folder ka poora content<br>
        • <code>.db .sqlite .sqlite3 .session .pickle</code> files
      </p>
      <p style="margin-top:12px;">Code files safe rahengi.</p>
      <p style="margin-top:10px;color:var(--red);font-weight:700;">Ye undo nahi hoga.</p>
    </div>`,
    'YES, FRESH START', async () => {
      try {
        showToast('Clearing database...');
        const r = await api(`/api/projects/${currentProject.id}/reset-db`, 'POST');
        closeModal();
        showToast('Bot is fresh now ✨', false, `${r.removed.length} file(s) removed${r.restarted ? ' · restarted' : ''}`);
        window.__openProject(currentProject.id);
      } catch(e){ showToast(e.message, true); }
    });
};

$('fileInput').onchange = function(){
  const c = this.files.length;
  const names = Array.from(this.files).map(f => f.name).join(', ');
  const short = names.length > 30 ? names.slice(0,30) + '…' : names;
  $('fileLabel').innerHTML = c ? `📎 ${esc(short)} <span class="fcnt">${c}</span>` : `📎 Select Files <span class="fcnt">0</span>`;
};

$('uploadBtn').onclick = async () => {
  if (!currentProject) return;
  const input = $('fileInput');
  if (!input.files.length){ showToast('Select files first', true); return; }
  const fd = new FormData();
  for (let i=0; i<input.files.length; i++) fd.append('files[]', input.files[i]);
  try {
    $('uploadStatus').textContent = 'Uploading...';
    const d = await apiUpload(`/api/projects/${currentProject.id}/upload`, fd);
    showToast(`Uploaded ${d.files_uploaded} files`);
    $('uploadStatus').textContent = `✓ ${d.files_uploaded} file(s) uploaded. Main: ${d.main_file || 'none'}`;
    input.value = '';
    $('fileLabel').innerHTML = '📎 Select Files <span class="fcnt">0</span>';
    window.__openProject(currentProject.id);
  } catch(e){
    $('uploadStatus').textContent = '✗ ' + e.message;
    showToast(e.message, true);
  }
};

$('deployBtn').onclick = async () => {
  if (!currentProject) return;
  const code = $('pyCodeArea').value;
  const fn = $('pyFilename').value.trim() || 'main.py';
  if (!code.trim()){ showToast('Code required', true); return; }
  try {
    $('deployStatus').textContent = 'Deploying...';
    await api(`/api/projects/${currentProject.id}/deploy`, 'POST', { filename: fn, code });
    showToast('Code saved');
    $('deployStatus').textContent = '✓ Saved as ' + fn;
    $('pyCodeArea').value = '';
    window.__openProject(currentProject.id);
  } catch(e){
    $('deployStatus').textContent = '✗ ' + e.message;
    showToast(e.message, true);
  }
};

$('newProjectBtn').onclick = () => {
  showModal('➕ New Project',
    `<label class="lbl">Project Name</label>
     <input id="npName" placeholder="e.g. MyBot, TestBot" autofocus>`,
    'CREATE', async () => {
      const name = $('npName').value.trim();
      if (!name){ showToast('Name required', true); return; }
      try {
        const d = await api('/api/projects', 'POST', { name });
        closeModal();
        showToast('Project created ✨', false, name);
        loadProjects();
        window.__openProject(d.id);
      } catch(e){ showToast(e.message, true); }
    });
};
$('refreshProjectsBtn').onclick = () => { loadProjects(); loadRunning(); showToast('Refreshed'); };

async function loadRunning(){
  try {
    const d = await api('/api/processes');
    const procs = d.processes || [];
    const c = $('runningNodes');
    if (!c) return;
    if (!procs.length){
      c.innerHTML = '<div style="opacity:0.5;text-align:center;padding:20px 0;font-size:0.88rem;">Nothing running</div>';
      return;
    }
    c.innerHTML = procs.map(p => `
      <div class="fitem">
        <div class="fname">
          <span style="width:9px;height:9px;background:var(--green);border-radius:50%;
            display:inline-block;box-shadow:0 0 8px var(--green);"></span>
          ${esc(p.project_name)} <span class="fsize">👤 ${esc(p.username)} · PID ${p.pid}</span>
        </div>
        <div class="factions">
          <button class="fm red" onclick="window.__stopProc('${p.id}')">■ Stop</button>
        </div>
      </div>
    `).join('');
  } catch(e){ console.error(e); }
}

window.__stopProc = async (pid) => {
  try { await api(`/api/projects/${pid}/stop`, 'POST'); showToast('Stopped'); loadRunning(); loadProjects(); }
  catch(e){ showToast(e.message, true); }
};

$('startAllBtn').onclick = async () => {
  if (!projects.length){ showToast('No projects', true); return; }
  showToast('Starting all...');
  for (const p of projects){ if (!p.running){ try { await api(`/api/projects/${p.id}/start`, 'POST'); } catch(e){} } }
  showToast('All started'); loadRunning(); loadProjects();
};
$('stopAllBtn').onclick = async () => {
  showToast('Stopping all...');
  for (const p of projects){ if (p.running){ try { await api(`/api/projects/${p.id}/stop`, 'POST'); } catch(e){} } }
  showToast('All stopped'); loadRunning(); loadProjects();
};

async function loadUsers(){
  try {
    const d = await api('/api/users');
    const users = d.users || [];
    if (!users.length){
      $('usersList').innerHTML = '<div style="opacity:0.5;text-align:center;padding:16px;font-size:0.88rem;">No users</div>';
      return;
    }
    $('usersList').innerHTML = users.map(u => `
      <div class="fitem">
        <div style="min-width:0;">
          <div class="fname">${esc(u.username)}
            <span class="ftag ${u.role === 'owner' ? 'owner' : 'user'}">${(u.role||'user').toUpperCase()}</span>
          </div>
          <div class="fmeta">${esc(u.email || '—')}${u.full_name ? ' · ' + esc(u.full_name) : ''}</div>
        </div>
      </div>`).join('');
  } catch(e){ showToast(e.message, true); }
}

function promptAddUser(){
  const body = `
    <div class="form-section">
      <h4>Personal Information</h4>
      <label class="lbl">Full Name <span class="required">*</span></label>
      <input id="auFullName" placeholder="Enter full name">
      <label class="lbl">Email Address <span class="required">*</span></label>
      <input id="auEmail" type="email" placeholder="you@example.com">
    </div>

    <div class="form-section">
      <h4>Billing Address</h4>
      <label class="lbl">City <span class="required">*</span></label>
      <input id="auCity" placeholder="City">
      <label class="lbl">State <span class="required">*</span></label>
      <select id="auState">
        <option value="">— Select State —</option>
        <option>Andhra Pradesh</option>
        <option>Assam</option>
        <option>Bihar</option>
        <option>Delhi</option>
        <option>Goa</option>
        <option>Gujarat</option>
        <option>Haryana</option>
        <option>Karnataka</option>
        <option>Kerala</option>
        <option>Madhya Pradesh</option>
        <option>Maharashtra</option>
        <option>Odisha</option>
        <option>Punjab</option>
        <option>Rajasthan</option>
        <option>Tamil Nadu</option>
        <option>Telangana</option>
        <option>Uttar Pradesh</option>
        <option>West Bengal</option>
        <option>Other</option>
      </select>
      <label class="lbl">Country <span class="required">*</span></label>
      <select id="auCountry">
        <option>India</option>
        <option>United States</option>
        <option>United Kingdom</option>
        <option>Canada</option>
        <option>Australia</option>
        <option>Other</option>
      </select>
    </div>

    <div class="form-section">
      <h4>Account Security</h4>
      <div class="lbl-row">
        <label class="lbl">Password <span class="required">*</span></label>
        <span class="hint">at least 5 characters</span>
      </div>
      <input id="auPass" type="password" placeholder="Password">
      <button type="button" class="btn-ghost" id="auGenPass">🔄 Generate Password</button>
      <label class="lbl">Confirm Password <span class="required">*</span></label>
      <input id="auPass2" type="password" placeholder="Confirm password">
    </div>
  `;

  showModal('➕ Create New User', body, 'REGISTER', async () => {
    const fullName = $('auFullName').value.trim();
    const email    = $('auEmail').value.trim();
    const city     = $('auCity').value.trim();
    const state    = $('auState').value;
    const country  = $('auCountry').value;
    const pass     = $('auPass').value;
    const pass2    = $('auPass2').value;

    if (!fullName) return showToast('Full name required', true);
    if (!email || !email.includes('@')) return showToast('Valid email required', true);
    if (!city)     return showToast('City required', true);
    if (!state)    return showToast('Please select a state', true);
    if (!pass || pass.length < 5)
      return showToast('Password must be at least 5 characters', true);
    if (pass !== pass2)
      return showToast('Passwords do not match', true);

    try {
      const res = await api('/api/users/add', 'POST', {
        email, password: pass,
        full_name: fullName, city, state, country
      });
      closeModal();
      showToast('User created ✨', false, `${email} · username: ${res.username}`);
      loadUsers();
    } catch(e){ showToast(e.message, true); }
  });

  setTimeout(() => {
    const g = $('auGenPass');
    if (!g) return;
    g.onclick = () => {
      const chars = 'ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789@#!';
      let p = '';
      for (let i = 0; i < 12; i++) p += chars[Math.floor(Math.random() * chars.length)];
      $('auPass').value = p;
      $('auPass2').value = p;
      $('auPass').type = 'text';
      $('auPass2').type = 'text';
      showToast('Password generated', false, p);
    };
  }, 120);
}

function promptChangeEmail(){
  showModal('✏️ Change Email',
    `<label class="lbl">New Email Address</label>
     <input id="cuEmail" type="email" placeholder="new@example.com" value="${esc(currentEmail || '')}">`,
    'UPDATE', async () => {
      const v = $('cuEmail').value.trim();
      if (!v || !v.includes('@')){ showToast('Valid email required', true); return; }
      try {
        await api('/api/users/update', 'PUT', { field:'email', value:v });
        currentEmail = v;
        $('ddEmail').textContent = v;
        localStorage.setItem('loggedInUser', JSON.stringify({username:currentUser, email:v, role:currentRole}));
        closeModal();
        showToast('Email updated ✨', false, v);
      } catch(e){ showToast(e.message, true); }
    });
}

function promptChangePassword(){
  showModal('🔑 Change Password',
    `<label class="lbl">Old Password</label><input id="cpOld" type="password">
     <label class="lbl">New Password</label><input id="cpNew" type="password">
     <p class="p-small">At least 5 characters</p>`,
    'UPDATE', async () => {
      const o = $('cpOld').value.trim(), n = $('cpNew').value.trim();
      if (!o || !n){ showToast('Both required', true); return; }
      if (n.length < 5){ showToast('New password must be 5+ chars', true); return; }
      try {
        await api('/api/users/update', 'PUT', { field:'password', old_value:o, value:n });
        closeModal();
        showToast('Password updated');
      } catch(e){ showToast(e.message, true); }
    });
}

function connectTerm(){
  if (!currentProject){ showToast('Open a project first', true); return; }
  if (termSource){ termSource.close(); termSource = null; }
  const out = $('termOut');
  out.innerHTML = '';
  termSource = new EventSource(`/api/terminal/${currentProject.id}?username=${encodeURIComponent(currentUser)}`);
  termSource.onmessage = ev => {
    try {
      const d = JSON.parse(ev.data);
      const div = document.createElement('div');
      if (d.type === 'output'){ div.textContent = d.data; }
      else if (d.type === 'error'){ div.textContent = '✗ ' + d.data; div.style.color = '#f85149'; }
      else { div.textContent = '--- ' + d.data + ' ---'; div.style.opacity = '0.5'; }
      out.appendChild(div);
      out.scrollTop = out.scrollHeight;
    } catch(e){}
  };
  termSource.onerror = () => {
    const div = document.createElement('div');
    div.textContent = '⚠ Connection lost';
    div.style.color = '#f59e0b';
    out.appendChild(div);
  };
  showToast('Connected');
}
function disconnectTerm(){
  if (termSource){ termSource.close(); termSource = null; showToast('Disconnected'); }
}
$('tConnect').onclick = connectTerm;
$('tDisconnect').onclick = disconnectTerm;
$('tClear').onclick = () => { $('termOut').innerHTML = ''; };

function showModal(title, body, confirmText, onConfirm){
  $('mTitle').textContent = title;
  $('mBody').innerHTML = body;
  $('mConfirm').textContent = confirmText || 'Confirm';
  $('modal').classList.add('active');
  $('mConfirm').onclick = () => { if (onConfirm) onConfirm(); else closeModal(); };
  $('mCancel').onclick = closeModal;
  setTimeout(() => { const i = $('mBody').querySelector('input'); if (i) i.focus(); }, 120);
}
function closeModal(){ $('modal').classList.remove('active'); }
$('modal').addEventListener('click', e => { if (e.target === $('modal')) closeModal(); });

function esc(s){
  return String(s == null ? '' : s)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

(function initStatCarousel(){
  const track = $('statTrack');
  const dots  = document.querySelectorAll('#carouselDots .dot');
  if (!track || !dots.length) return;
  track.addEventListener('scroll', () => {
    const w = (track.querySelector('.stat-slide')?.clientWidth || 1) + 12;
    const idx = Math.round(track.scrollLeft / w);
    dots.forEach((d, i) => d.classList.toggle('active', i === Math.min(idx, dots.length-1)));
  }, {passive:true});
  dots.forEach((d, i) => {
    d.onclick = () => {
      const s = track.querySelectorAll('.stat-slide')[i];
      if (s) track.scrollTo({left: s.offsetLeft - track.offsetLeft, behavior:'smooth'});
    };
  });
})();

function boot(){
  const lb = $('loaderBar');
  let p = 0;
  const iv = setInterval(() => { p += 8; lb.style.width = Math.min(p,100) + '%'; if (p >= 100) clearInterval(iv); }, 80);
  setTimeout(() => {
    $('loader').style.opacity = '0';
    setTimeout(() => $('loader').style.display = 'none', 400);
    const saved = localStorage.getItem('loggedInUser');
    if (saved){
      try {
        const d = JSON.parse(saved);
        currentUser = d.username;
        currentEmail = d.email || '';
        currentRole = d.role || 'user';
        isLoggedIn = !!currentUser;
      } catch(e){}
    }
    if (isLoggedIn) initApp();
    else { $('loginBox').classList.remove('hidden'); $('app').classList.add('hidden'); }
  }, 1200);
}

window.addEventListener('load', boot);
})();
</script>
</body>
</html>
'''

# ============================================================
# START
# ============================================================
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    app.run(host='0.0.0.0', port=port, debug=False)