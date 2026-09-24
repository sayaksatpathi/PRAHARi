/* Lightweight i18n for the Prahari dashboard shell — no build step.
 *
 * Adds a Hindi (हिंदी) option for the primary UI so a non-English-first SSB
 * operator can navigate. Covers the static shell (login, navigation, top bar);
 * dynamic view content is a documented follow-up. Language persists per browser.
 */
(function () {
  "use strict";

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
    }
  };

  function getLang() {
    try { return localStorage.getItem("prahari_lang") || "en"; }
    catch (e) { return "en"; }
  }
  function setLang(lang) {
    if (!STRINGS[lang]) lang = "en";
    try { localStorage.setItem("prahari_lang", lang); } catch (e) {}
    apply(lang);
  }
  function t(key) {
    var lang = getLang();
    return (STRINGS[lang] && STRINGS[lang][key]) || STRINGS.en[key] || key;
  }
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
    if (btn) btn.textContent = lang === "hi" ? "हिं" : "EN";
  }

  // public API for other scripts (e.g. re-apply after a dynamic render)
  window.PrahariI18n = { t: t, apply: apply, setLang: setLang, getLang: getLang };

  function wire() {
    apply(getLang());
    var btn = document.getElementById("tb-lang");
    if (btn) {
      btn.addEventListener("click", function () {
        setLang(getLang() === "hi" ? "en" : "hi");
      });
    }
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", wire);
  } else {
    wire();
  }
})();
