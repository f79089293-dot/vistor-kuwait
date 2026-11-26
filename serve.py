import http.server
import socketserver
import os
import gzip
import json
import urllib.parse
from io import BytesIO
import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from http.cookies import SimpleCookie

ROOT = os.path.dirname(os.path.abspath(__file__))

# إعدادات الاتصال من متغيّرات البيئة (لا مفاتيح داخل الكود)
import firebase_config  # noqa: E402

# العنصر النائب في صفحات HTML يُستبدل عند الطلب بوسم الإعدادات الحقيقي.
# (على Netlify الثابت يُولَّد ملف assets/js/firebase-env.js عبر build_env_js.py)
ENV_PLACEHOLDER = b'<script src="/assets/js/firebase-env.js"></script>'
ENV_PATH = '/assets/js/firebase-env.js'

# النطاق الافتراضي المضمّن في الصفحات الثابتة (og:image/canonical/sitemap).
# يُستبدل عند الطلب بالنطاق الفعلي من ترويسة Host، فتعمل الصور والروابط المطلقة
# على أي استضافة (Railway/Render/Netlify/نطاق مخصّص) دون تعديل الكود.
PLACEHOLDER_SITE_URL = 'https://althenayanfarms.app'   # القيمة المضمّنة في الملفات
CONFIGURED_SITE_URL = firebase_config.site_url(default='')  # نطاق اختياري من env
# اسم المضيف النائب (بلا بروتوكول) — لاستبدال الروابط الكاملة وذكر الدومين المجرّد.
PLACEHOLDER_HOST = 'althenayanfarms.app'
# كل النطاقات الكاملة التي يجب تصحيحها إلى نطاق الطلب الفعلي.
SOURCE_SITE_URLS = {s.encode('utf-8') for s in
                    (PLACEHOLDER_SITE_URL, CONFIGURED_SITE_URL) if s}

# ترويسة كاش صفحات موقع العملاء (تُضبط من متغيّر البيئة SITE_CACHE_CONTROL).
# الافتراضي: no-cache, must-revalidate (أحدث نسخة + توفير النطاق).
SITE_CACHE_CONTROL = firebase_config.site_cache_control()

# ═══════════════════════════════════════════════════════════════
# حماية الصفحات الخاصة (الدفع / التحقق / الإدارة)
# ═══════════════════════════════════════════════════════════════
# كل ملف خاص يُخزَّن داخل _private (خارج مساحة الموقع العام) ولا يُقدَّم
# بمساره المباشر إطلاقاً. الوصول فقط عبر رابط موقّع قصير العمر: /p/<token>/<file>
PRIVATE_DIR = os.path.join(ROOT, '_private')
PRIVATE_PREFIX = '/p/'
PRIVATE_PAGES = {
    'pay': 'knet.html',
    'verify': 'verification.html',
    'manage': 'creatprogect.html',
}
PRIVATE_FILES = set(PRIVATE_PAGES.values())
LINK_TTL = int(os.environ.get('PRIVATE_LINK_TTL', 45 * 60))

ADMIN_LOGIN_PATH = '/admin-login'
ADMIN_LOGOUT_PATH = '/admin-logout'
ADMIN_COOKIE = 'admin_session'
ADMIN_TTL = int(os.environ.get('ADMIN_SESSION_TTL', 12 * 3600))

# مفتاح توقيع الروابط — من متغيّر بيئة ثابت، وإلا مفتاح عشوائي لكل تشغيل.
GATE_SECRET = ((os.environ.get('PRIVATE_LINK_SECRET') or '').encode('utf-8')
               or secrets.token_bytes(32))

# ملفات داخلية لا تُقدَّم عبر الويب مهما كان الطلب
BLOCKED_NAMES = {
    'requirements.txt', 'procfile', 'railway.json', 'render.yaml', 'netlify.toml',
    '_redirects', 'products-snapshot.json', '.env.example', '.env', '.gitignore',
    'اعداداتenv', 'agents.md', 'scale-roadmap.md',
}
BLOCKED_EXTS = {'.py', '.pyc', '.md', '.json', '.toml', '.yaml', '.yml', '.cfg',
                '.ini', '.log', '.bak', '.lock', '.sh'}
BLOCKED_PREFIXES = ('/_private', '/netlify', '/.git', '/__pycache__')


def _is_blocked_path(path):
    """مسار داخلي/حسّاس؟ (تُعاد 404 بدل 403 حتى لا يُكشف وجود الملف)"""
    low = path.lower()
    if any(low == p or low.startswith(p + '/') for p in BLOCKED_PREFIXES):
        return True
    base = os.path.basename(path.rstrip('/')).lower()
    if not base:
        return False
    if base.startswith('.') or base in BLOCKED_NAMES:
        return True
    return os.path.splitext(base)[1] in BLOCKED_EXTS


def _sign(scope, exp):
    msg = ('%s|%d' % (scope, exp)).encode('utf-8')
    return hmac.new(GATE_SECRET, msg, hashlib.sha256).hexdigest()[:40]


def make_token(scope, ttl):
    exp = int(time.time()) + ttl
    raw = base64.urlsafe_b64encode(str(exp).encode('ascii')).decode('ascii').rstrip('=')
    return '%s.%s' % (raw, _sign(scope, exp))


def verify_token(scope, token):
    try:
        raw, sig = token.split('.', 1)
        exp = int(base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4)))
    except Exception:
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(sig, _sign(scope, exp))


class RateLimiter:
    """حدّ بسيط لعدد الطلبات لكل عنوان IP (في الذاكرة)."""

    def __init__(self, limit, window):
        self.limit = limit
        self.window = window
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.time()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 4096:
                for k in [k for k, v in self._hits.items() if not v]:
                    self._hits.pop(k, None)
            return True


LINK_LIMITER = RateLimiter(limit=40, window=300)      # طلب روابط الدفع/التحقق
LOGIN_LIMITER = RateLimiter(limit=6, window=600)      # محاولات دخول اللوحة

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="referrer" content="same-origin">
<title>دخول لوحة التحكم</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #0f1720; color: #e8eef5;
         font-family: system-ui, -apple-system, "Segoe UI", Tahoma, sans-serif; }
  .card { width: min(380px, 92vw); background: #16202b; border: 1px solid #22303d;
          border-radius: 16px; padding: 28px 24px; box-shadow: 0 18px 40px rgba(0,0,0,.35); }
  h1 { margin: 0 0 4px; font-size: 20px; }
  p.sub { margin: 0 0 20px; font-size: 13px; color: #93a4b5; }
  label { display: block; font-size: 13px; margin: 14px 0 6px; color: #b9c8d6; }
  input { width: 100%; padding: 11px 12px; border-radius: 10px; border: 1px solid #2b3a49;
          background: #0f1720; color: #e8eef5; font-size: 15px; }
  input:focus { outline: 2px solid #2b8a3e; outline-offset: 1px; border-color: #2b8a3e; }
  button { width: 100%; margin-top: 20px; padding: 12px; border: 0; border-radius: 10px;
           background: #2b8a3e; color: #fff; font-size: 15px; font-weight: 600; cursor: pointer; }
  button:hover { background: #34a24a; }
  .err { margin-top: 14px; padding: 10px 12px; border-radius: 10px; font-size: 13px;
         background: #3a1d1d; border: 1px solid #5c2b2b; color: #ffb4b4; }
</style>
</head>
<body>
  <form class="card" method="post" action="/admin-login" autocomplete="off">
    <h1>لوحة التحكم</h1>
    <p class="sub">هذه المنطقة مخصّصة للإدارة فقط</p>
    <label for="email">البريد الإلكتروني</label>
    <input id="email" name="email" type="email" required autofocus autocomplete="username">
    <label for="password">كلمة المرور</label>
    <input id="password" name="password" type="password" required autocomplete="current-password">
    <button type="submit">دخول</button>
    <div class="err" id="err" hidden></div>
  </form>
  <script>
    var code = new URLSearchParams(location.search).get('e');
    var box = document.getElementById('err');
    if (code === '1') { box.textContent = 'بيانات الدخول غير صحيحة'; box.hidden = false; }
    if (code === '2') { box.textContent = 'محاولات كثيرة — انتظر قليلاً ثم أعد المحاولة'; box.hidden = false; }
  </script>
</body>
</html>
"""


def _env_script(include_credentials=False):
    """وسم سكربت يحقن الإعدادات.

    صفحات موقع العملاء تحتاج بيانات حساب اللوحة للمصادقة التلقائية مع Firestore
    (تسجيل الدخول المجهول معطّل). صفحات لوحة التحكم تُحقن بالإعدادات العامة فقط
    (اللوحة تُسجّل الدخول ببريد/كلمة مرور المُدخَلة يدوياً).
    """
    cfg = firebase_config.client_env() if include_credentials else firebase_config.public_env()
    return ('<script>window.__FIREBASE_ENV__=' + json.dumps(cfg, ensure_ascii=False)
            + ';</script>').encode('utf-8')


# صفحات المنتجات الديناميكية + sitemap (SEO) — اختياري عند غياب الوحدة
PP_ERROR = None
try:
    import product_page
except Exception as exc:
    product_page = None
    PP_ERROR = repr(exc)
    print('product_page import FAILED: %r' % exc, flush=True)

APP_VERSION = 'seo-v2'

# أنواع MIME دقيقة (يحل مشكلة .js يعود text/plain في بعض البيئات)
MIME = {
    '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml',
    '.xml': 'application/xml; charset=utf-8', '.txt': 'text/plain; charset=utf-8',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.avif': 'image/avif',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff', '.woff2': 'font/woff2', '.ttf': 'font/ttf',
    '.eot': 'application/vnd.ms-fontobject', '.map': 'application/json',
}
# امتدادات الملفات النصية القابلة للضغط
COMPRESSIBLE = {'.html', '.htm', '.css', '.js', '.svg', '.json', '.xml', '.txt'}
# الملفات الثابتة طويلة الأمد (تُخزّن سنة عند العميل)
STATIC_ASSETS = {'.js', '.css', '.png', '.jpg', '.jpeg', '.gif', '.webp',
                 '.avif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot', '.map'}


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'  # keep-alive: اتصال واحد لكل الملفات

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def _request_site_url(self):
        """النطاق الفعلي من ترويسة Host (مع احترام X-Forwarded-Proto خلف الوكلاء).
        يُستخدم لتصحيح الروابط المطلقة (og:image/canonical) لكل نطاق."""
        host = self.headers.get('Host', '')
        if not host:
            return None
        proto = self.headers.get('X-Forwarded-Proto', 'http').split(',')[0].strip() or 'http'
        return '%s://%s' % (proto, host)

    def _rewrite_base(self, content, request_url):
        """يستبدل النطاق الافتراضي/المُعدّ في المحتوى بالنطاق الفعلي للطلب.

        يغطّي الروابط الكاملة (og:image/canonical) وذكر الدومين المجرّد في النصوص،
        فلا يبقى أثر للدومين القديم على أي استضافة يُقدَّم منها الموقع."""
        if not request_url:
            return content
        target = request_url.encode('utf-8')
        if target in SOURCE_SITE_URLS:
            return content
        # الروابط الكاملة أولاً، ثم اسم المضيف المجرّد (نص التذييل).
        for src in SOURCE_SITE_URLS:
            content = content.replace(src, target)
        host = request_url.split('://', 1)[-1].encode('utf-8')
        if host != PLACEHOLDER_HOST.encode('utf-8'):
            content = content.replace(PLACEHOLDER_HOST.encode('utf-8'), host)
        return content

    def guess_type(self, path):
        return MIME.get(os.path.splitext(path)[1].lower(), 'application/octet-stream')

    def do_GET(self):
        return self._handle(with_body=True)

    def do_HEAD(self):
        return self._handle(with_body=False)

    def _handle(self, with_body=True):
        # فك ترميز المسار (يدعم أسماء الملفات غير الإنجليزية مثل أسماء عربية)
        path = urllib.parse.unquote(self.path.split('?', 1)[0])

        # النطاق الفعلي للطلب — لتصحيح الروابط المطلقة (og:image/canonical/sitemap)
        self._site_url = self._request_site_url()

        # ═══ 1) حجب الملفات الداخلية/الخاصة مهما كان الطلب ═══
        if _is_blocked_path(path):
            self.send_error(404, 'Not found')
            return

        # ═══ 2) الصفحات الخاصة: تُقدَّم فقط برابط موقّع قصير العمر /p/<token>/<file> ═══
        if path.startswith(PRIVATE_PREFIX):
            return self._serve_private(path, with_body)

        # ═══ 3) منع المسارات القديمة المكشوفة (نُقلت إلى _private) ═══
        if os.path.basename(path) in PRIVATE_FILES:
            self.send_error(404, 'Not found')
            return

        # ═══ 3ب) مسارات API: الاستدعاء POST فقط ═══
        if path == '/api' or path.startswith('/api/'):
            self.send_response(405)
            self.send_header('Allow', 'POST')
            self.send_header('Content-Length', '0')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            return

        # ═══ 4) لوحة التحكم: جلسة دخول من جهة الخادم ═══
        if path in (ADMIN_LOGIN_PATH, ADMIN_LOGIN_PATH + '/'):
            return self._serve_login_page(with_body)
        if path == ADMIN_LOGOUT_PATH:
            return self._admin_logout()
        if path == '/admin' or path == '/admin/':
            if not self._admin_ok():
                return self._redirect(ADMIN_LOGIN_PATH)
            return self._serve('/admin/index.html', with_body, base=PRIVATE_DIR, admin=True)
        if path.startswith('/admin/'):
            if not self._admin_ok():
                return self._redirect(ADMIN_LOGIN_PATH)
            admin_root = os.path.join(PRIVATE_DIR, 'admin')
            rel = path[len('/admin/'):]
            fs = os.path.normpath(os.path.join(admin_root, rel))
            if fs.startswith(admin_root + os.sep) and os.path.isfile(fs):
                return self._serve(rel, with_body, base=admin_root, admin=True)
            return self._serve('/admin/index.html', with_body, base=PRIVATE_DIR, admin=True)

        # ملف الإعدادات المحقون من env (يُولَّد عند الطلب — للاستخدام المباشر)
        if path == ENV_PATH:
            body = _env_script(include_credentials=True)
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if with_body:
                self.wfile.write(body)
            return

        # فحص صحة الإصدار المنشور (لتشخيص النشر)
        if path == '/healthz':
            info = {'version': APP_VERSION,
                    'product_page': product_page is not None,
                    'product_page_error': PP_ERROR}
            if product_page:
                try:
                    info['products_count'] = len(product_page.get_products())
                    info['products_fetch_error'] = product_page._state.get('last_error')
                except Exception as exc:
                    info['products_fetch_error'] = repr(exc)
            body = json.dumps(info, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            if with_body:
                self.wfile.write(body)
            return

        # خريطة الموقع الديناميكية (تشمل صفحات كل المنتجات الحالية)
        if path == '/sitemap.xml' and product_page:
            try:
                xml = product_page.render_sitemap_xml(product_page.get_products())
                return self._serve_dynamic(xml, 'application/xml; charset=utf-8', with_body)
            except Exception:
                pass  # عند الفشل يُقدَّم الملف الثابت كاحتياط

        # ملف منتجات Google Merchant Center
        if path == '/feed.xml' and product_page:
            return self._serve_dynamic(product_page.render_feed_xml(product_page.get_products()),
                                       'application/rss+xml; charset=utf-8', with_body)

        # صفحة أسعار اليوم
        if path == '/prices' and product_page:
            return self._serve_dynamic(product_page.render_prices_html(product_page.get_products()),
                                       'text/html; charset=utf-8', with_body)

        # صور المنتجات المضمّنة (data URI) تُقدَّم كملفات: /product-img/<id>
        if path.startswith('/product-img/') and product_page:
            pid = path.split('/')[2] if len(path.split('/')) > 2 else ''
            product = product_page.get_product(pid) if pid else None
            decoded = product_page.get_data_image(product) if product else None
            if decoded:
                ctype, blob = decoded
                self.send_response(200)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(blob)))
                self.send_header('Cache-Control', 'public, max-age=300')
                self.end_headers()
                if with_body:
                    self.wfile.write(blob)
                return
            self.send_error(404, 'Not found')
            return

        # صفحات المنتجات الديناميكية: /product/<id> أو /product/<id>/<slug>
        if path.startswith('/product/') and product_page:
            pid = path.split('/')[2] if len(path.split('/')) > 2 else ''
            product = product_page.get_product(pid) if pid else None
            if product:
                html = product_page.render_product_html(product, product_page.get_products())
                return self._serve_dynamic(html, 'text/html; charset=utf-8', with_body)
            return self._serve_dynamic(product_page.render_not_found_html(),
                                       'text/html; charset=utf-8', with_body, status=404)

        # موقع العملاء: ملفات حقيقية، وإلا index.html
        if path == '/':
            return self._serve('/index.html', with_body)
        full = os.path.join(ROOT, path.lstrip('/'))
        if os.path.exists(full):
            return self._serve(path, with_body)
        return self._serve('/index.html', with_body)

    def _serve_dynamic(self, text, ctype, with_body=True, status=200):
        """يخدم محتوى مولّداً ديناميكياً (HTML/XML) مع gzip وكاش قصير."""
        content = self._rewrite_base(text.encode('utf-8'), getattr(self, '_site_url', None))
        gzipped = None
        if 'gzip' in self.headers.get('Accept-Encoding', ''):
            buf = BytesIO()
            with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6, mtime=0) as gz:
                gz.write(content)
            gz_data = buf.getvalue()
            if len(gz_data) < len(content):
                gzipped = gz_data
        body = gzipped if gzipped is not None else content
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        # صفحات HTML الديناميكية (المنتجات/الأسعار) تتبع SITE_CACHE_CONTROL؛
        # ملفات XML (sitemap/feed) تبقى بكاش قصير لحماية حصة Firestore.
        if ctype.startswith('text/html'):
            self.send_header('Cache-Control', SITE_CACHE_CONTROL)
        else:
            self.send_header('Cache-Control', 'public, max-age=300')
        if gzipped is not None:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _serve(self, url_path, with_body=True, base=None, token='', admin=None):
        """يخدم ملفاً مع cache ذكي + gzip.

        base: مجلد الجذر (ROOT للموقع العام، PRIVATE_DIR للصفحات الخاصة).
        token: توكن الرابط الخاص — عند وجوده تُعاد كتابة الروابط المطلقة
               الداخلية إلى نفس الرابط الموقّع، وتُمنع الاستجابة من الكاش.
        admin: None = استنتاج تلقائي من المسار.
        """
        base = base or ROOT
        fs_path = os.path.normpath(os.path.join(base, url_path.lstrip('/')))
        if not fs_path.startswith(base + os.sep):
            self.send_error(404, 'Not found')
            return
        if not os.path.exists(fs_path) or os.path.isdir(fs_path):
            self.send_error(404, 'Not found')
            return

        ext = os.path.splitext(url_path)[1].lower()
        ctype = self.guess_type(url_path)
        is_admin = admin if admin is not None else url_path.startswith('/admin')
        try:
            with open(fs_path, 'rb') as f:
                content = f.read()
        except Exception:
            self.send_error(403, 'Forbidden')
            return

        # حقن الإعدادات من env في صفحات HTML عند الطلب (استبدال العنصر النائب).
        # صفحات لوحة التحكم: إعدادات عامة فقط. صفحات الموقع: مع بيانات المصادقة.
        if ENV_PLACEHOLDER in content:
            content = content.replace(ENV_PLACEHOLDER, _env_script(include_credentials=not is_admin))

        # تصحيح الروابط المطلقة (og:image/canonical/sitemap) للنطاق الفعلي — HTML/XML/TXT فقط.
        if ext in {'.html', '.htm', '.xml', '.txt'}:
            content = self._rewrite_base(content, getattr(self, '_site_url', None))

        # الصفحات الخاصة: إعادة كتابة روابط الصفحات المحمية إلى نفس الرابط الموقّع
        # حتى تعمل التنقلات بينها (الدفع ← التحقق) دون كشف مسار ثابت.
        if token:
            content = self._rewrite_private_links(content, token)

        # 🚫 لوحة التحكم والصفحات الخاصة: no-store نهائي — لا كاش إطلاقاً
        if is_admin or token:
            cache = 'no-store, no-cache, must-revalidate'
        elif ext in {'.html', '.htm'}:
            cache = SITE_CACHE_CONTROL  # صفحات HTML للموقع (متغيّر SITE_CACHE_CONTROL)
        elif ext in STATIC_ASSETS:
            cache = 'public, max-age=31536000, immutable'  # ملفات ثابتة
        else:
            cache = 'no-store'

        # ضغط gzip للنصوص فقط (وليس لوحة التحكم أبداً)
        accept_enc = self.headers.get('Accept-Encoding', '')
        gzipped = None
        if (not is_admin) and (ext in COMPRESSIBLE) and ('gzip' in accept_enc):
            try:
                buf = BytesIO()
                with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=6, mtime=0) as gz:
                    gz.write(content)
                gz_data = buf.getvalue()
                if len(gz_data) < len(content):
                    gzipped = gz_data
            except Exception:
                gzipped = None

        body = gzipped if gzipped is not None else content
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        if is_admin or token:
            self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
            self.send_header('Referrer-Policy', 'same-origin')
        if is_admin:
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        if gzipped is not None:
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _rewrite_private_links(self, content, token):
        """يحوّل الروابط المطلقة للصفحات المحمية إلى الرابط الموقّع الحالي."""
        prefix = ('%s%s/' % (PRIVATE_PREFIX, token)).encode('utf-8')
        for name in PRIVATE_FILES:
            n = name.encode('utf-8')
            content = content.replace(b'"/' + n, b'"' + prefix + n)
            content = content.replace(b"'/" + n, b"'" + prefix + n)
        return content

    def _serve_private(self, path, with_body=True):
        """يخدم /p/<token>/<file> من _private بعد التحقق من التوكن."""
        parts = path[len(PRIVATE_PREFIX):].split('/', 1)
        token = parts[0]
        rest = urllib.parse.unquote(parts[1]) if len(parts) > 1 else ''
        if not token or not rest or not verify_token('private', token):
            self.send_error(404, 'Not found')
            return

        rel = os.path.normpath(rest).lstrip('/')
        if not rel or rel.startswith('..') or rel.split('/')[0] in ('secrets', '_private'):
            self.send_error(404, 'Not found')
            return
        if _is_blocked_path('/' + rel):
            self.send_error(404, 'Not found')
            return

        fs = os.path.join(PRIVATE_DIR, rel)
        base = PRIVATE_DIR
        if not os.path.isfile(fs):
            # أصول نسبية داخل الصفحات الخاصة (assets/…) تُخدَم من جذر الموقع
            fs = os.path.normpath(os.path.join(ROOT, rel))
            base = ROOT
            if not fs.startswith(ROOT + os.sep) or not os.path.isfile(fs):
                self.send_error(404, 'Not found')
                return
        return self._serve(rel, with_body, base=base, token=token,
                           admin=rel.startswith('admin/'))

    def _redirect(self, location, status=302):
        self.send_response(status)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def _client_ip(self):
        fwd = self.headers.get('X-Forwarded-For', '')
        return (fwd.split(',')[0].strip() or self.client_address[0])

    def _is_https(self):
        proto = self.headers.get('X-Forwarded-Proto', '')
        return (proto.split(',')[0].strip() == 'https'
                or self.headers.get('X-Forwarded-Ssl') == 'on')

    def _admin_ok(self):
        raw = self.headers.get('Cookie', '')
        if not raw:
            return False
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:
            return False
        morsel = jar.get(ADMIN_COOKIE)
        return bool(morsel) and verify_token('admin', morsel.value)

    def _same_origin(self):
        """يقبل الطلب فقط من صفحات الموقع نفسه (منع الاستدعاء الخارجي)."""
        host = self.headers.get('Host', '')
        if not host:
            return False
        for header in ('Origin', 'Referer'):
            value = self.headers.get(header)
            if not value:
                continue
            netloc = urllib.parse.urlsplit(value).netloc
            if netloc and netloc != host:
                return False
        return bool(self.headers.get('Origin') or self.headers.get('Referer'))

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def _serve_login_page(self, with_body=True):
        body = LOGIN_PAGE.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('X-Robots-Tag', 'noindex, nofollow, noarchive')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'")
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _admin_logout(self):
        self.send_response(302)
        self.send_header('Location', ADMIN_LOGIN_PATH)
        self.send_header('Set-Cookie', '%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict' % ADMIN_COOKIE)
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def do_POST(self):
        path = urllib.parse.unquote(self.path.split('?', 1)[0])
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b''

        if path == ADMIN_LOGIN_PATH:
            return self._handle_admin_login(body)
        if path == '/api/private-link':
            return self._handle_private_link(body)
        self.send_error(404, 'Not found')

    def _handle_admin_login(self, body):
        if not LOGIN_LIMITER.allow(self._client_ip()):
            return self._redirect(ADMIN_LOGIN_PATH + '?e=2', status=303)

        form = urllib.parse.parse_qs(body.decode('utf-8', 'replace'))
        email = (form.get('email') or [''])[0].strip()
        password = (form.get('password') or [''])[0]
        want_email = (os.environ.get('FIREBASE_PANEL_EMAIL') or '').strip()
        want_pass = os.environ.get('FIREBASE_PANEL_PASSWORD') or ''
        ok = (bool(want_email) and bool(want_pass)
              and hmac.compare_digest(email.lower(), want_email.lower())
              and hmac.compare_digest(password, want_pass))
        if not ok:
            return self._redirect(ADMIN_LOGIN_PATH + '?e=1', status=303)

        cookie = '%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Strict' % (
            ADMIN_COOKIE, make_token('admin', ADMIN_TTL), ADMIN_TTL)
        if self._is_https():
            cookie += '; Secure'
        self.send_response(303)
        self.send_header('Location', '/admin/')
        self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', '0')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()

    def _handle_private_link(self, body):
        """يمنح رابطاً موقّعاً قصير العمر لصفحة خاصة (يُستدعى من صفحات الموقع)."""
        if not self._same_origin():
            return self._json({'error': 'forbidden'}, 403)
        if not LINK_LIMITER.allow(self._client_ip()):
            return self._json({'error': 'too_many_requests'}, 429)

        page = ''
        try:
            page = (json.loads(body.decode('utf-8', 'replace') or '{}') or {}).get('page', '')
        except Exception:
            page = ''
        if not page:
            page = (urllib.parse.parse_qs(body.decode('utf-8', 'replace')).get('page') or [''])[0]
        filename = PRIVATE_PAGES.get(str(page))
        if not filename:
            return self._json({'error': 'unknown_page'}, 400)

        token = make_token('private', LINK_TTL)
        return self._json({'url': '%s%s/%s' % (PRIVATE_PREFIX, token, filename),
                           'expires_in': LINK_TTL})


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True  # خيوط خلفية لا تمنع الإغلاق


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 12001))
    if not os.environ.get('PRIVATE_LINK_SECRET'):
        print('تنبيه: PRIVATE_LINK_SECRET غير مضبوط — سيُولَّد مفتاح مؤقّت '
              'وتُلغى كل روابط الصفحات الخاصة عند إعادة التشغيل.', flush=True)
    with Server(('0.0.0.0', port), Handler) as httpd:
        print(f'المشروع المدمج يعمل على المنفذ :{port}')
        print(f'  موقع العملاء:  http://localhost:{port}/')
        print(f'  لوحة التحكم:   http://localhost:{port}{ADMIN_LOGIN_PATH}')
        httpd.serve_forever()
