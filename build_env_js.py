#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_env_js.py — توليد assets/js/firebase-env.js من متغيّرات البيئة
--------------------------------------------------------------------
يُستخدم عند النشر على استضافة ثابتة (Netlify) حيث لا يوجد خادم يحقن
الإعدادات في الصفحة. يقرأ متغيّرات البيئة FIREBASE_* (أو ملف .env محلياً)
ويكتب ملفاً يحتوي الإعدادات العامة فقط:

    window.__FIREBASE_ENV__ = { apiKey: "...", ... };

ملاحظة أمنية: الإعدادات العامة لـ Firebase (apiKey/projectId/...) ليست سرّية —
تصميم Firebase يعتبر apiKey معرّفاً عاماً، والحماية عبر قواعد الأمان. لكن الملف
يتضمّن أيضاً بيانات حساب اللوحة (FIREBASE_PANEL_EMAIL/PASSWORD) لأن موقع العملاء
يحتاجها للمصادقة التلقائية مع Firestore (تسجيل الدخول المجهول معطّل في المشروع).
لهذا الملف **مستثنى من Git** ويُولَّد عند البناء من متغيّرات بيئة المنصة.

الاستخدام:
    python3 build_env_js.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import firebase_config  # noqa: E402

OUT = os.path.join(ROOT, 'assets', 'js', 'firebase-env.js')


def build():
    # client_env يتضمن بيانات حساب اللوحة لأن موقع العملاء يحتاجها للمصادقة
    # التلقائية مع Firestore (تسجيل الدخول المجهول معطّل في هذا المشروع).
    cfg = firebase_config.client_env()
    content = ('// مولّد آلياً من متغيّرات البيئة (build_env_js.py) — لا تُعدّله يدوياً\n'
               'window.__FIREBASE_ENV__ = ' + json.dumps(cfg, ensure_ascii=False) + ';\n')
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write(content)
    if not cfg.get('apiKey'):
        print('تحذير: FIREBASE_API_KEY فارغ — اضبط متغيّرات البيئة قبل النشر.')
    print('كُتب %s' % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(build())
