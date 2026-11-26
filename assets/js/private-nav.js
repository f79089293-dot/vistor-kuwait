/**
 * private-nav.js — التنقل الآمن إلى الصفحات الخاصة (الدفع/التحقق/الإدارة).
 *
 * الصفحات الخاصة لا تُخدَم بمسار ثابت؛ يطلب العميل رابطاً موقّعاً قصير العمر
 * من الخادم (/api/private-link) ثم ينتقل إليه. على الاستضافة الثابتة (Netlify)
 * يعود تلقائياً إلى المسار الاحتياطي.
 */
(function () {
  'use strict';

  var ENDPOINT = '/api/private-link';
  var TIMEOUT_MS = 5000;

  window.goToPrivate = function (page, fallback) {
    var finished = false;

    function go(url) {
      if (finished) return;
      finished = true;
      window.location.href = url;
    }

    var timer = setTimeout(function () { go(fallback || '/'); }, TIMEOUT_MS);

    fetch(ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'fetch' },
      body: JSON.stringify({ page: page })
    })
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (data) {
        clearTimeout(timer);
        go((data && data.url) || fallback || '/');
      })
      .catch(function () {
        clearTimeout(timer);
        go(fallback || '/');
      });
  };
})();
