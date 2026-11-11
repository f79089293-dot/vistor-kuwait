# -*- coding: utf-8 -*-
"""
firebase_config.py — مصدر واحد لمفاتيح الاتصال (لا مفاتيح داخل الكود)
--------------------------------------------------------------------
تقرأ كل مفاتيح الاتصال من متغيّرات البيئة (env) مع دعم ملف `.env` المحلي
(لا يُرفع للمستودع). تُستخدم من:
  - serve.py            → حقن الإعدادات في الصفحة والكود عند الطلب (window.__FIREBASE_ENV__)
  - product_page.py     → جلب المنتجات من Firestore عبر REST
  - build_env_js.py     → توليد assets/js/firebase-env.js للاستضافة الثابتة (Netlify)

لا تضع أي مفتاح هنا. ضع القيم في `.env` محلياً، وفي متغيّرات بيئة المنصة عند النشر.
"""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path=None):
    """تحميل ملف .env إلى os.environ (لا يستبدل متغيّراً موجوداً مسبقاً)."""
    path = path or os.path.join(ROOT, '.env')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def _get(*names, default=''):
    """أول متغيّر بيئة غير فارغ من الأسماء المعطاة (تدعم البادئتين FIREBASE_ و VITE_)."""
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return default


def firebase_env():
    """إعدادات Firebase العامة (يُحقن معظمها في الواجهة) + مفاتيح الخادم الخاصة."""
    return {
        # ── عامة (تُستخدم في المتصفح) ──
        'apiKey': _get('FIREBASE_API_KEY', 'VITE_FIREBASE_API_KEY'),
        'authDomain': _get('FIREBASE_AUTH_DOMAIN', 'VITE_FIREBASE_AUTH_DOMAIN'),
        'databaseURL': _get('FIREBASE_DATABASE_URL', 'VITE_FIREBASE_DATABASE_URL'),
        'projectId': _get('FIREBASE_PROJECT_ID', 'VITE_FIREBASE_PROJECT_ID'),
        'storageBucket': _get('FIREBASE_STORAGE_BUCKET', 'VITE_FIREBASE_STORAGE_BUCKET'),
        'messagingSenderId': _get('FIREBASE_MESSAGING_SENDER_ID', 'VITE_FIREBASE_MESSAGING_SENDER_ID'),
        'appId': _get('FIREBASE_APP_ID', 'VITE_FIREBASE_APP_ID'),
        'measurementId': _get('FIREBASE_MEASUREMENT_ID', 'VITE_FIREBASE_MEASUREMENT_ID'),
        # ── سرّية (تبقى على الخادم فقط، لا تُحقن في المتصفح) ──
        'panelEmail': _get('FIREBASE_PANEL_EMAIL', 'PANEL_EMAIL'),
        'panelPassword': _get('FIREBASE_PANEL_PASSWORD', 'PANEL_PASSWORD'),
    }


PUBLIC_KEYS = ('apiKey', 'authDomain', 'databaseURL', 'projectId',
               'storageBucket', 'messagingSenderId', 'appId', 'measurementId')


def public_env():
    """الإعدادات العامة فقط — تُحقن في صفحات لوحة التحكم (بلا كلمة سر)."""
    env = firebase_env()
    return {k: env.get(k, '') for k in PUBLIC_KEYS}


def client_env():
    """الإعدادات العامة + بيانات حساب اللوحة.

    يحتاجها موقع العملاء للمصادقة التلقائية مع Firestore (تسجيل الدخول المجهول
    anonymous معطّل في هذا المشروع، فالمصادقة بحساب اللوحة). ملاحظة أمنية:
    هذه القيم كانت مضمّنة سابقاً داخل الكود وتُرسَل للمتصفح؛ الآن مصدرها env فقط.
    """
    env = public_env()
    env['panelEmail'] = firebase_env().get('panelEmail', '')
    env['panelPassword'] = firebase_env().get('panelPassword', '')
    return env



# تحميل .env عند الاستيراد (متغيّرات البيئة الحقيقية لها الأولوية)
load_env_file()
