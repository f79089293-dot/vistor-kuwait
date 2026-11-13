// Netlify Edge Function — تصحيح دومين روابط المشاركة تلقائياً
// ---------------------------------------------------------------------------
// المشكلة: صفحات الموقع الثابتة تحمل روابط مطلقة بالدومين النائب
// (og:image / og:url / canonical / twitter:image / sitemap / feed). على استضافة
// ثابتة لا يوجد خادم يعيد كتابتها، فتظهر صورة المشاركة خربانة إذا تغيّر الدومين.
//
// الحل: هذه الدالة تعمل على حافة شبكة Netlify، تعترض الرد قبل وصوله للزائر أو
// للزاحف (فيسبوك/واتساب/تويتر)، وتقرأ الدومين الحقيقي من الطلب نفسه (request.url)
// ثم تستبدل به الدومين النائب. لا تحتاج أي إعداد ولا متغيّر بيئة: تغيير الدومين
// يعمل تلقائياً.

// الدومين النائب المضمّن في الملفات الثابتة (يُستبدَل دائماً بنطاق الطلب الفعلي).
const PLACEHOLDER_HOST = 'althenayanfarms.app';

// أنواع المحتوى النصية التي قد تحمل روابط مطلقة يجب تصحيحها.
const TEXT_TYPES = [
  'text/html',
  'application/xhtml',
  'application/xml',
  'text/xml',
  'text/plain',
  'application/rss',
  'application/atom',
];

export default async (request, context) => {
  const response = await context.next();

  // أي خطأ غير متوقّع هنا يجب ألا يُسقط الموقع: نُرجع الرد الأصلي كما هو.
  // (تسقط الدالة فقط لو فشل context.next() نفسه.)
  try {
    const contentType = response.headers.get('content-type') || '';
    if (!TEXT_TYPES.some((t) => contentType.includes(t))) {
      return response;
    }

    const { host } = new URL(request.url);
    if (host === PLACEHOLDER_HOST) {
      return response;
    }

    // مهم: نقرأ نسخة (clone) من الرد، لا الرد نفسه. قراءة جسم الرد الأصلي
    // تستهلكه (body stream) فيفشل تمريره للزائر بعدها — وهذا كان سبب انهيار
    // الدالة "uncaught exception" على أي صفحة نصية لا تحمل الدومين النائب
    // (مثل knet.html و verification.html). النسخة تُبقي الرد الأصلي سليماً.
    const body = await response.clone().text();
    if (!body.includes(PLACEHOLDER_HOST)) {
      return response;
    }

    // استبدال اسم المضيف النائب باسم المضيف الفعلي. يغطّي الحالتين معاً:
    //   https://althenayanfarms.app/...  →  https://<host>/...
    //   althenayanfarms.app (نص مجرّد)   →  <host>
    const rewritten = body.split(PLACEHOLDER_HOST).join(host);

    const headers = new Headers(response.headers);
    // الطول/الترميز تغيّرا بعد التعديل — يُعاد حسابهما تلقائياً.
    headers.delete('content-length');
    headers.delete('content-encoding');
    headers.delete('etag');

    return new Response(rewritten, {
      status: response.status,
      statusText: response.statusText,
      headers,
    });
  } catch (err) {
    return response;
  }
};
