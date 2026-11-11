// إعدادات Firebase — تُقرأ من متغيّرات البيئة (لا مفاتيح في الكود)
//   • serve.py: window.__FIREBASE_ENV__ محقون في الصفحة عند الطلب
//   • Netlify الثابت: assets/js/firebase-env.js مولّد عند البناء
const ENV = window.__FIREBASE_ENV__ || {};
const firebaseConfig = {
  apiKey: ENV.apiKey,
  authDomain: ENV.authDomain,
  databaseURL: ENV.databaseURL,
  projectId: ENV.projectId,
  storageBucket: ENV.storageBucket,
  messagingSenderId: ENV.messagingSenderId,
  appId: ENV.appId,
  measurementId: ENV.measurementId
};

if (!firebaseConfig.apiKey) {
  console.error('Firebase config مفقود: اضبط متغيّرات البيئة FIREBASE_*.');
}

if (!firebase.apps.length) {
  firebase.initializeApp(firebaseConfig);
}

// لا توجد بيانات دخول مضمّنة في الواجهة. تسجيل دخول اللوحة يستخدم
// Firebase Auth (بريد/كلمة مرور المُدخَلة في شاشة الدخول) — انظر admin/assets/js/app.js.

const db = firebase.firestore();
const rtd = firebase.database();
window.db = db;
