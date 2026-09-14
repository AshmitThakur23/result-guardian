// Temporary: final pre-submission check of the product documentation.
// Confirms every template section is present, nothing overflows A4, and
// reports how many printed pages the document actually comes to.
const { chromium } = require("@playwright/test");

const WIDTH = 718;   // A4 210mm - 20mm margins, at 96 CSS px/in
const HEIGHT = 1047; // A4 297mm - 23mm margins

const REQUIRED = [
  "PROJECT IDENTITY", "DOCUMENT CONTROL",
  "02 / EXECUTIVE SUMMARY", "PRODUCT BRIEF", "VISION & SUCCESS",
  "03 / PROBLEM, USERS & OPPORTUNITY", "PROBLEM DEFINITION",
  "USERS & STAKEHOLDERS", "EXISTING LANDSCAPE",
  "04 / PRODUCT REQUIREMENTS & USE CASES", "CORE USE CASES",
  "FUNCTIONAL REQUIREMENTS", "NON-FUNCTIONAL REQUIREMENTS",
  "05 / UX, USER JOURNEY & PRODUCT FLOW", "USER JOURNEY", "END-TO-END WORKFLOW",
  "06 / SYSTEM ARCHITECTURE & DATA FLOW", "ARCHITECTURE DIAGRAM", "COMPONENT REGISTER",
  "07 / TECHNOLOGY STACK & ENGINEERING DESIGN", "TECHNOLOGY STACK",
  "IMPLEMENTATION DECISIONS", "CORE LOGIC / ALGORITHMS",
  "08 / DATA, APIS, SECURITY & PRIVACY", "DATA MODEL", "SECURITY & PRIVACY CONTROLS",
  "09 / TESTING, DEPLOYMENT & OPERATIONS", "TEST STRATEGY", "CRITICAL TEST CASES",
  "10 / IMPACT, ROADMAP & FINAL HANDOVER", "RESULTS & IMPACT",
  "RISKS, LIMITATIONS & ROADMAP", "HANDOVER & SUBMISSION CHECKLIST",
];

const BANNED = ["ADD LINK", "NEEDS YOUR INPUT", "TODO", "Lorem", "[Enter", "[Full name]"];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT } });
  await page.goto("file:///" + process.argv[2].replace(/\\/g, "/"), { waitUntil: "networkidle" });
  await page.evaluate(() => document.fonts.ready);
  await page.emulateMedia({ media: "print" });

  const r = await page.evaluate(({ WIDTH, REQUIRED, BANNED }) => {
    const text = document.body.innerText.replace(/\s+/g, " ");
    const over = [...document.querySelectorAll("body *")].filter(e => e.scrollWidth > WIDTH + 1);
    return {
      scrollW: document.body.scrollWidth,
      scrollH: document.body.scrollHeight,
      overflow: over.length,
      missing: REQUIRED.filter(s => !text.includes(s.replace(/\s+/g, " "))),
      banned: BANNED.filter(b => text.includes(b)),
      fontsOK: document.fonts.check("12px Poppins") && document.fonts.check("12px 'Open Sans'"),
      tables: document.querySelectorAll("table").length,
      sections: document.querySelectorAll(".sheet").length,
    };
  }, { WIDTH, REQUIRED, BANNED });

  console.log("sections (sheets) : " + r.sections);
  console.log("tables            : " + r.tables);
  console.log("web fonts loaded  : " + (r.fontsOK ? "YES" : "NO — falling back"));
  console.log("body width        : " + r.scrollW + " / " + WIDTH);
  console.log("overflowing els   : " + r.overflow);
  console.log("approx A4 pages   : " + Math.ceil(r.scrollH / HEIGHT));
  console.log("missing sections  : " + (r.missing.length ? r.missing.join(" | ") : "NONE"));
  console.log("placeholders left : " + (r.banned.length ? r.banned.join(" | ") : "NONE"));
  await browser.close();
})();
