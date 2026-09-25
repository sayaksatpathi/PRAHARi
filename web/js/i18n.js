/* Lightweight i18n for the Prahari dashboard — no build step.
 *
 * Two mechanisms, one dictionary:
 *
 *  1. Static shell (login, navigation, top bar) uses [data-i18n="key"] and the
 *     keyed STRINGS table, applied on load and language change.
 *
 *  2. Dynamic views (rendered from template literals in views.js) are translated
 *     by a post-render DOM pass, translate(root), that walks leaf-text elements
 *     — stat labels, panel titles, buttons, table headers, nav sections — and
 *     replaces the English text with its translation via T(). Anything not in the
 *     PHRASES table passes through unchanged, so interpolated values ("5/8
 *     cameras", "Why this scored 0.87") are always safe.
 *
 * Languages: English, हिंदी (Hindi), বাংলা (Bengali). Bengali is included as a
 * regional language for the eastern sectors (Indo-Nepal / Indo-Bhutan open
 * borders in North Bengal). Choice persists per browser.
 *
 * NOTE: this is an operator-usability aid for the primary console vocabulary.
 * It is a shell for language support; genuine field-readiness for regional
 * operators still requires operator testing with native speakers — see
 * docs/operator-testing.md.
 */
(function () {
  "use strict";

  var LANGS = ["en", "hi", "bn"];
  var LANG_LABEL = { en: "EN", hi: "हिं", bn: "বাং" };

  // Keyed strings for the static shell (index.html [data-i18n]).
  var STRINGS = {
    en: {
      "login.subtitle": "Border video-intelligence layer. Sign in to continue.",
      "login.username": "Username",
      "login.password": "Password",
      "login.signin": "Sign in",
      "login.hint": "The initial administrator password is printed once to the node console on first start.",
      "nav.operations": "Operations",
      "nav.dashboard": "Dashboard",
      "nav.cameras": "Live Cameras",
      "nav.alerts": "Alerts",
      "nav.events": "Event Log",
      "nav.crosscam": "Cross-Camera",
      "nav.patrol": "Patrol Suppression",
      "nav.configuration": "Configuration",
      "nav.capability": "Camera Capability",
      "nav.zones": "Zones & Rules",
      "nav.node": "Node",
      "nav.health": "System Health",
      "nav.integrity": "Evidence Integrity",
      "nav.demo": "Demonstration",
      "topbar.logout": "Sign out"
    },
    hi: {
      "login.subtitle": "सीमा वीडियो-इंटेलिजेंस परत। जारी रखने के लिए साइन इन करें।",
      "login.username": "उपयोगकर्ता नाम",
      "login.password": "पासवर्ड",
      "login.signin": "साइन इन करें",
      "login.hint": "प्रारंभिक व्यवस्थापक पासवर्ड पहली बार शुरू होने पर नोड कंसोल पर एक बार दिखाया जाता है।",
      "nav.operations": "संचालन",
      "nav.dashboard": "डैशबोर्ड",
      "nav.cameras": "लाइव कैमरे",
      "nav.alerts": "अलर्ट",
      "nav.events": "इवेंट लॉग",
      "nav.crosscam": "क्रॉस-कैमरा",
      "nav.patrol": "गश्त दमन",
      "nav.configuration": "कॉन्फ़िगरेशन",
      "nav.capability": "कैमरा क्षमता",
      "nav.zones": "ज़ोन और नियम",
      "nav.node": "नोड",
      "nav.health": "सिस्टम स्वास्थ्य",
      "nav.integrity": "साक्ष्य अखंडता",
      "nav.demo": "प्रदर्शन",
      "topbar.logout": "साइन आउट"
    },
    bn: {
      "login.subtitle": "সীমান্ত ভিডিও-ইন্টেলিজেন্স স্তর। চালিয়ে যেতে সাইন ইন করুন।",
      "login.username": "ব্যবহারকারীর নাম",
      "login.password": "পাসওয়ার্ড",
      "login.signin": "সাইন ইন করুন",
      "login.hint": "প্রাথমিক প্রশাসক পাসওয়ার্ড প্রথমবার চালু হলে নোড কনসোলে একবার দেখানো হয়।",
      "nav.operations": "পরিচালনা",
      "nav.dashboard": "ড্যাশবোর্ড",
      "nav.cameras": "লাইভ ক্যামেরা",
      "nav.alerts": "সতর্কতা",
      "nav.events": "ইভেন্ট লগ",
      "nav.crosscam": "ক্রস-ক্যামেরা",
      "nav.patrol": "টহল দমন",
      "nav.configuration": "কনফিগারেশন",
      "nav.capability": "ক্যামেরা সক্ষমতা",
      "nav.zones": "জোন ও নিয়ম",
      "nav.node": "নোড",
      "nav.health": "সিস্টেম স্বাস্থ্য",
      "nav.integrity": "প্রমাণ অখণ্ডতা",
      "nav.demo": "প্রদর্শন",
      "topbar.logout": "সাইন আউট"
    }
  };

  // English-as-key phrase table for the dynamic views. Keyed by the exact English
  // text produced in views.js. Missing entries fall back to English.
  var PHRASES = {
    // stat-tile labels (shared statTile helper -> covers every view)
    "Link posture":              { hi: "लिंक स्थिति",             bn: "লিঙ্ক অবস্থা" },
    "Cameras online":            { hi: "ऑनलाइन कैमरे",           bn: "অনলাইন ক্যামেরা" },
    "Alerts (1 h)":              { hi: "अलर्ट (1 घं)",           bn: "সতর্কতা (১ ঘ)" },
    "Queued for sync":           { hi: "सिंक हेतु कतारबद्ध",      bn: "সিঙ্কের জন্য সারিবদ্ধ" },
    "Imagery processed":         { hi: "संसाधित इमेजरी",         bn: "প্রক্রিয়াকৃত চিত্র" },
    "Actually transmitted":      { hi: "वास्तव में प्रेषित",       bn: "প্রকৃতপক্ষে প্রেরিত" },
    "Uptime":                    { hi: "अपटाइम",                 bn: "আপটাইম" },
    "Disk free":                 { hi: "खाली डिस्क",             bn: "ফাঁকা ডিস্ক" },
    "Events recorded":           { hi: "दर्ज घटनाएँ",            bn: "রেকর্ডকৃত ঘটনা" },
    "Inference device":          { hi: "अनुमान डिवाइस",          bn: "ইনফারেন্স ডিভাইস" },
    "Global entities":           { hi: "वैश्विक इकाइयाँ",         bn: "গ্লোবাল সত্তা" },
    "Seen on 2+ cameras":        { hi: "2+ कैमरों पर देखा गया",   bn: "২+ ক্যামেরায় দৃশ্যমান" },
    "Open handoffs":             { hi: "खुले हैंडऑफ़",            bn: "খোলা হ্যান্ডঅফ" },
    "Corridor edges":            { hi: "कॉरिडोर किनारे",         bn: "করিডোর প্রান্ত" },
    "Events assessed":           { hi: "मूल्यांकित घटनाएँ",       bn: "মূল্যায়িত ঘটনা" },
    "Patrol matched":            { hi: "गश्त मिलान",             bn: "টহল মিল" },
    "Patrol deviations":         { hi: "गश्त विचलन",             bn: "টহল বিচ্যুতি" },
    "Removed from the alert path": { hi: "अलर्ट पथ से हटाया गया", bn: "সতর্কতা পথ থেকে সরানো" },

    // panel titles
    "Evidence":                  { hi: "साक्ष्य",                bn: "প্রমাণ" },
    "Integrity":                 { hi: "अखंडता",                bn: "অখণ্ডতা" },
    "Detail":                    { hi: "विवरण",                 bn: "বিবরণ" },
    "Operator action":           { hi: "ऑपरेटर कार्रवाई",        bn: "অপারেটর পদক্ষেপ" },
    "Sector plot":               { hi: "सेक्टर मानचित्र",         bn: "সেক্টর মানচিত্র" },
    "Uplink economics":          { hi: "अपलिंक अर्थशास्त्र",      bn: "আপলিঙ্ক অর্থনীতি" },
    "Alerts, most significant first": { hi: "अलर्ट, सबसे महत्वपूर्ण पहले", bn: "সতর্কতা, সবচেয়ে গুরুত্বপূর্ণ আগে" },
    "Active alerts":              { hi: "सक्रिय अलर्ट",             bn: "সক্রিয় সতর্কতা" },
    "Cameras":                   { hi: "कैमरे",                 bn: "ক্যামেরা" },
    "Uplink and queue":          { hi: "अपलिंक और कतार",         bn: "আপলিঙ্ক ও সারি" },
    "Audit log":                 { hi: "ऑडिट लॉग",              bn: "অডিট লগ" },
    "Scene injection":           { hi: "दृश्य इंजेक्शन",          bn: "দৃশ্য ইনজেকশন" },
    "Connectivity":              { hi: "कनेक्टिविटी",            bn: "সংযোগ" },
    "Recent handoffs":           { hi: "हाल के हैंडऑफ़",          bn: "সাম্প্রতিক হ্যান্ডঅফ" },
    "Corridor topology":         { hi: "कॉरिडोर टोपोलॉजी",        bn: "করিডোর টপোলজি" },
    "Entities seen on more than one camera": { hi: "एक से अधिक कैमरे पर देखी इकाइयाँ", bn: "একাধিক ক্যামেরায় দেখা সত্তা" },
    "Declared patrol roster":    { hi: "घोषित गश्त सूची",         bn: "ঘোষিত টহল তালিকা" },
    "Events a patrol accounted for": { hi: "गश्त द्वारा समझाई घटनाएँ", bn: "টহল দ্বারা ব্যাখ্যাত ঘটনা" },

    // buttons
    "Confirm — genuine":         { hi: "पुष्टि करें — वास्तविक",   bn: "নিশ্চিত করুন — প্রকৃত" },
    "False alarm":               { hi: "झूठा अलार्म",            bn: "ভুয়া সতর্কতা" },
    "Acknowledge only":          { hi: "केवल स्वीकार करें",       bn: "শুধু স্বীকার করুন" },
    "Re-profile":                { hi: "पुनः प्रोफ़ाइल",          bn: "পুনঃপ্রোফাইল" },
    "Verify now":                { hi: "अभी सत्यापित करें",       bn: "এখন যাচাই করুন" },
    "Approach & cross the line": { hi: "पास आकर रेखा पार करें",    bn: "কাছে এসে রেখা অতিক্রম" },
    "Loiterer":                  { hi: "मंडराता व्यक्ति",         bn: "ঘোরাঘুরিকারী" },
    "Group of four":             { hi: "चार का समूह",            bn: "চারজনের দল" },
    "Vehicle with plate":        { hi: "प्लेट सहित वाहन",         bn: "প্লেটসহ যান" },
    "Patrol on its route":       { hi: "मार्ग पर गश्त",           bn: "নিজ পথে টহল" },
    "Patrol walking the wrong way": { hi: "गलत दिशा में गश्त",     bn: "ভুল পথে টহল" },
    "Cover the lens":            { hi: "लेंस ढकें",              bn: "লেন্স ঢাকুন" },
    "Blind with light":          { hi: "रोशनी से अंधा करें",       bn: "আলো দিয়ে অন্ধ করা" },
    "Replay a frozen feed":      { hi: "जमी फ़ीड फिर चलाएँ",       bn: "জমাট ফিড পুনরায় চালান" },
    "Clear":                     { hi: "साफ़ करें",              bn: "পরিষ্কার" },
    "Cut the uplink":            { hi: "अपलिंक काटें",           bn: "আপলিঙ্ক কাটুন" },
    "Restore the uplink":        { hi: "अपलिंक बहाल करें",        bn: "আপলিঙ্ক পুনরুদ্ধার" },

    // common
    "Loading…":                  { hi: "लोड हो रहा है…",          bn: "লোড হচ্ছে…" }
  };

  function getLang() {
    try {
      var l = localStorage.getItem("prahari_lang");
      return LANGS.indexOf(l) >= 0 ? l : "en";
    } catch (e) { return "en"; }
  }
  function nextLang() {
    var i = LANGS.indexOf(getLang());
    return LANGS[(i + 1) % LANGS.length];
  }
  function setLang(lang) {
    if (LANGS.indexOf(lang) < 0) lang = "en";
    try { localStorage.setItem("prahari_lang", lang); } catch (e) {}
    apply(lang);
    // Tell the app to re-render the current dynamic view in the new language.
    try {
      document.dispatchEvent(new CustomEvent("prahari:langchange", { detail: { lang: lang } }));
    } catch (e) {}
  }

  // Keyed lookup for the static shell.
  function t(key) {
    var lang = getLang();
    return (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
  }

  // English-text lookup for dynamic views. Unknown text passes through unchanged.
  function T(text) {
    if (text == null) return text;
    var lang = getLang();
    if (lang === "en") return text;
    var key = String(text).trim();
    var entry = PHRASES[key];
    if (entry && entry[lang]) {
      // Preserve any surrounding whitespace the caller had.
      return String(text).replace(key, entry[lang]);
    }
    return text;
  }

  // Translate the static [data-i18n] shell for the given language.
  function apply(lang) {
    lang = lang || getLang();
    document.documentElement.setAttribute("lang", lang);
    var nodes = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) {
      var key = nodes[i].getAttribute("data-i18n");
      var val = (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key];
      if (val != null) nodes[i].textContent = val;
    }
    var btn = document.getElementById("tb-lang");
    if (btn) btn.textContent = LANG_LABEL[lang] || "EN";
    translate(document.getElementById("view"));
  }

  // Post-render DOM pass: translate leaf-text elements in a dynamic view subtree.
  // Only elements whose entire content is a single text node are touched, so
  // nested markup is never clobbered and interpolated strings pass through.
  var SELECTOR = ".stat-label, .panel-title, .nav-section, .section-title, " +
                 "button, th, .filter-label, label";
  function translate(root) {
    if (!root) return;
    var lang = getLang();
    if (lang === "en") return;   // English is canonical; nothing to do.
    var nodes = root.querySelectorAll(SELECTOR);
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.hasAttribute("data-i18n")) continue;      // handled by keyed apply()
      if (el.children.length > 0) continue;            // not a pure-text leaf
      var raw = el.textContent;
      if (!raw) continue;
      var translated = T(raw);
      if (translated !== raw) el.textContent = translated;
    }
  }

  // Public API.
  window.PrahariI18n = {
    t: t, T: T, apply: apply, translate: translate,
    setLang: setLang, getLang: getLang, nextLang: nextLang
  };

  function wire() {
    apply(getLang());
    var btn = document.getElementById("tb-lang");
    if (btn) {
      btn.addEventListener("click", function () { setLang(nextLang()); });
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
