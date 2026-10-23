# new-cela — نظام وكلاء ذكي مستقل (Autonomous AI Agent System)

بنية تحتية مجانية 100% لبناء وكيل ذكاء اصطناعي مستقل يجمع بين:

- **أتمتة شاملة وتصفح ويب** (قدرات شبيهة بـ Manus) عبر `browser-use` + Chromium.
- **تفكير عميق وبرمجة ذاتية** (قدرات شبيهة بـ Claude Code) عبر حلقة ReACT ومنفذ أوامر معزول.
- **توجيه نماذج مجاني متعدد المزودات** (Gemini / Groq) بتبديل تلقائي عند تجاوز الحدود.
- **تشغيل آلي بدون سيرفر** عبر GitHub Actions — التكلفة التشغيلية **0$**.

---

## 🏗️ البنية المعمارية

```
new-cela/
├── .github/
│   └── workflows/
│       ├── agent-runner.yml       # مشغّل الوكيل التلقائي (Issues + تشغيل يدوي)
│       └── quality-gate.yml       # فحص آلي: compileall + ruff + pytest
├── src/
│   ├── main.py                    # نقطة الدخول (CLI) — أوضاع: reason / browser / code
│   ├── config.py                  # الإعدادات المركزية (pydantic-settings)
│   ├── core/
│   │   └── planner.py             # حلقة التفكير ReAct (Thought → Action → Observation)
│   ├── execution/
│   │   └── sandbox.py             # منفذ أوامر معزول (قائمة بيضاء + مهلة + بلا shell)
│   ├── browser/
│   │   └── browser_engine.py      # محرك أتمتة المتصفح (Manus-like)
│   └── providers/
│       └── llm_router.py          # موجّه النماذج المجانية + التبديل التلقائي
├── tests/
│   └── test_smoke.py              # اختبارات دخان (بدون مفاتيح)
├── .env.example                   # نموذج متغيرات البيئة
├── .gitignore                     # عزل .env والمخرجات
├── requirements.txt               # المتطلبات المثبَّتة والمتحقق منها
└── README.md
```

---

## 🚀 التشغيل المحلي

```bash
# 1. الاستنساخ والتثبيت
git clone https://github.com/celiaelazamey-arch/new-cela.git
cd new-cela
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium --with-deps

# 2. ضبط المفاتيح (مجانية)
cp .env.example .env
#    • Gemini: https://aistudio.google.com/apikey
#    • Groq:   https://console.groq.com/keys

# 3. التشغيل
python -m src.main --task "حلّل مشروعي واقترح تحسينات" --mode reason
python -m src.main --task "ابحث عن أحدث أخبار الذكاء الاصطناعي" --mode browser
python -m src.main --task "python --version" --mode code
```

---

## 🔐 إدارة الأسرار (مهمة جداً)

**القاعدة: المفاتيح لا تدخل الكود ولا المستودع أبداً.**

| البيئة | الطريقة |
|---|---|
| محلي | ملف `.env` (مستثنى في `.gitignore`) |
| GitHub Actions | Settings ← Secrets and variables ← Actions ← New repository secret |

الأسرار المطلوبة في GitHub:

| الاسم | الوصف |
|---|---|
| `GEMINI_API_KEY` | مفتاح Gemini المجاني (أساسي) |
| `GROQ_API_KEY` | مفتاح Groq المجاني (بديل تلقائي) |
| `GITHUB_TOKEN` | يُولَّد تلقائياً — لا حاجة لإضافته |

> ⚠️ لا تشارك مفاتيحك في أي محادثة أو Issue أو Commit. إذا تسرب مفتاح، أبطِله فوراً من لوحة تحكم المزود.

---

## ⚙️ التشغيل التلقائي عبر GitHub Actions

**طريقة 1 — عبر Issue:**
1. أنشئ Issue جديداً في المستودع.
2. أضف له التصنيف `agent-task`.
3. سيُشغَّل الوكيل تلقائياً ويعلّق بالنتيجة على الـ Issue.

**طريقة 2 — تشغيل يدوي:**
Actions ← Autonomous Agent Pipeline ← Run workflow ← اكتب المهمة واختر النمط.

---

## 🧠 أوضاع الوكيل

| النمط | الوظيفة |
|---|---|
| `reason` | حلقة ReAct للتفكير المنطقي وتحليل المهام النصية |
| `browser` | أتمتة متصفح حقيقية (تصفح، بحث، استخراج بيانات) |
| `code` | تنفيذ أوامر نظام من قائمة بيضاء آمنة (بلا shell) |

---

## 🛡️ ضوابط الأمان المطبقة

1. **عزل المفاتيح** — عبر `.env` محلياً و GitHub Secrets في Actions؛ ولا تُسجَّل في أي output.
2. **منع حقن الأوامر** — التنفيذ عبر `create_subprocess_exec` بدون shell + قائمة بيضاء.
3. **حدود الاستهلاك** — `concurrency` يمنع التشغيل المتوازي، و`max_steps` يحدّ الخطوات، ومهلة زمنية إلزامية.
4. **تبديل المزودات** — تجاوز حدود Gemini المجانية لا يوقف الوكيل (انتقال تلقائي إلى Groq).

---

## 📊 مقارنة القدرات

| القدرة | new-cela | التكلفة |
|---|---|---|
| أتمتة المتصفح (Manus-like) | ✅ browser-use + Chromium | 0$ |
| تفكير عميق (Claude Code-like) | ✅ ReAct Loop + Gemini/Groq | 0$ |
| تشغيل ذاتي بدون سيرفر | ✅ GitHub Actions | 0$ |
| إدارة ملفات وأوامر آمنة | ✅ Sandbox + قائمة بيضاء | 0$ |

---

## 🗺️ خارطة الطريق

- [ ] إضافة OpenRouter Free Tier كمزوّد ثالث في `llm_router.py`
- [ ] ذاكرة طويلة المدى للوكيل (Vector Store مجاني)
- [ ] دعم Playwright MCP كبديل عن browser-use
- [ ] لوحة متابعة المهام عبر GitHub Projects

---

## 📄 الترخيص

انظر ملف [LICENSE](LICENSE).
