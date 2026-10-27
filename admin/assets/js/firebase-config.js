// إعدادات Firebase — مشروع qatar-oman (موحّد للموقع ولوحة التحكم)
const firebaseConfig = {
  apiKey: "AIzaSyDNQotHYC8WcMi1AAHrHJe_rQR7OZ0V5FI",
  authDomain: "qatar-oman.firebaseapp.com",
  databaseURL: "https://qatar-oman-default-rtdb.firebaseio.com",
  projectId: "qatar-oman",
  storageBucket: "qatar-oman.firebasestorage.app",
  messagingSenderId: "488967046879",
  appId: "1:488967046879:web:77d744e1b45d0f59da8b6a",
  measurementId: "G-FP0FKD9W4T"
};

if (!firebase.apps.length) {
  firebase.initializeApp(firebaseConfig);
}

// بيانات دخول لوحة التحكم (نفس حساب موقع العملاء — له صلاحية قراءة/كتابة على customers)
const PANEL_EMAIL = 'panel-dashboard@qatar-oman.local';
const PANEL_PASSWORD = 'ZainDashboard2026!';

const db = firebase.firestore();
const rtd = firebase.database();
window.db = db;
