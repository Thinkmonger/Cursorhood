/** Client-side i18n — loads locale JSON and applies copy to the DOM. */

let _messages = {};
let _locale = "en";

function t(key, params = {}) {
  const parts = String(key).split(".");
  let value = _messages;
  for (const part of parts) {
    value = value?.[part];
    if (value === undefined) return key;
  }
  if (typeof value !== "string") return key;
  return value.replace(/\{(\w+)\}/g, (_, name) =>
    params[name] !== undefined && params[name] !== null ? String(params[name]) : `{${name}}`
  );
}

function applyI18n(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.getAttribute("data-i18n"));
  });

  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.getAttribute("data-i18n-placeholder"));
  });

  root.querySelectorAll("[data-i18n-html]").forEach((el) => {
    el.innerHTML = t(el.getAttribute("data-i18n-html"));
  });

  root.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.getAttribute("data-i18n-title"));
  });

  const pageKey = document.documentElement.getAttribute("data-i18n-page");
  if (pageKey) {
    document.title = `${t(`pages.${pageKey}`)} — ${t("app.name")}`;
  }
}

async function initI18n(locale = "en") {
  const res = await fetch(`/locales/${locale}.json`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`Locale ${locale} not found`);
  _messages = await res.json();
  _locale = locale;
  document.documentElement.lang = locale;
  applyI18n();
  window.__i18nReady = true;
  document.dispatchEvent(new CustomEvent("i18n:ready"));
  return _messages;
}

function whenI18nReady(fn) {
  if (window.__i18nReady) {
    fn();
    return;
  }
  document.addEventListener("i18n:ready", fn, { once: true });
}

function setBotPageTitle(name) {
  document.title = t("bot.pageTitle", { name: name || t("bot.titleDefault") });
}
