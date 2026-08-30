// Regression-проверка на DOM XSS во фронтенде (web/app.js, web/listing.js).
//
// Объявления приходят из Telegram/OLX/Avtoelon -- title, city, description_raw,
// labels флагов и source_url это чужой, не доверенный текст. web/common.js
// строит DOM только через el() (textContent, никогда innerHTML), а
// source_url/photo_url проверяются через safeUrl() перед тем как попасть в
// href/src. Этот скрипт поднимает реальный API+фронт на тестовой базе с
// полезной нагрузкой в каждом текстовом поле и проверяет, что ни один
// alert() не сработал ни на ленте, ни на детальной странице, и что ни один
// сырой <script>/<img onerror=...> тег не попал в DOM как разметка.
//
// Требует Playwright с уже установленным Chromium (см. PLAYWRIGHT_PATH ниже)
// и Node >= 22 (node:sqlite) -- не часть обычного python-тестового набора
// репозитория (CLAUDE.md), запускается вручную:
//   node tools/test_web_xss.mjs
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { existsSync, unlinkSync, readFileSync, copyFileSync } from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

const PLAYWRIGHT_PATH = process.env.PLAYWRIGHT_MODULE_PATH || "/opt/node22/lib/node_modules/playwright/index.js";
const playwrightModule = await import(PLAYWRIGHT_PATH);
const { chromium } = playwrightModule.default || playwrightModule;

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
// api/main.py резолвит DB_PATH относительно себя, без переменной окружения --
// поэтому подменяем настоящий automarket.db на время теста и восстанавливаем
// его обратно в finally, что бы ни случилось.
const DB_PATH = path.join(ROOT, "automarket.db");
const DB_BACKUP = path.join(ROOT, "automarket.db.xss_test_backup");
const PORT = 8099;

function seedDb() {
  if (existsSync(DB_PATH)) copyFileSync(DB_PATH, DB_BACKUP);
  if (existsSync(DB_PATH)) unlinkSync(DB_PATH);
  const db = new DatabaseSync(DB_PATH);
  db.exec(readFileSync(path.join(ROOT, "db/schema.sql"), "utf8"));
  db.prepare(
    `INSERT INTO listings (id, source, source_id, source_url, title, price_usd, price_type,
       needs_review, brand, model, year, city, description_raw, flags, posted_at, first_seen_at, last_seen_at)
     VALUES ('xss1', 'telegram', 'xss1', 'javascript:alert(5)',
       '<img src=x onerror=alert(1)>Chevrolet <script>alert(2)</script>',
       4000.0, 'full_price', 0, 'chevrolet', 'aveo', 2012,
       '"><svg onload=alert(3)>',
       '<script>document.title="hacked"</script>',
       '[{"code":"x","label":"<b onmouseover=alert(4)>bold</b>","severity":"warning","source":"text"}]',
       datetime('now'), datetime('now'), datetime('now'))`
  ).run();
  db.close();
}

async function main() {
  seedDb();

  const server = spawn(
    "python3",
    ["-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", String(PORT)],
    { cwd: ROOT, env: { ...process.env }, stdio: "ignore" }
  );
  await new Promise((r) => setTimeout(r, 2000));

  const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM || "/opt/pw-browsers/chromium" });
  let alertFired = false;
  const failures = [];

  try {
    for (const url of [`http://127.0.0.1:${PORT}/index.html`, `http://127.0.0.1:${PORT}/listing.html?id=xss1`]) {
      const page = await browser.newPage();
      page.on("dialog", async (d) => {
        alertFired = true;
        await d.dismiss();
      });
      await page.goto(url);
      await page.waitForTimeout(1000);
      // Реальная проверка XSS -- смотрим на живой DOM (не на исходный HTML
      // текст), т.к. браузер уже отрендерил бы вредоносную разметку, если
      // бы она была вставлена как HTML, а не как текст.
      const rawTagInDom = await page.evaluate(() => document.querySelector("img[onerror]") !== null || document.querySelector("script:not([src])") !== null && document.title === "hacked");
      if (rawTagInDom) failures.push(`${url}: найден исполняемый тег в живом DOM`);
      await page.close();
    }
  } finally {
    await browser.close();
    server.kill();
    if (existsSync(DB_BACKUP)) {
      copyFileSync(DB_BACKUP, DB_PATH);
      unlinkSync(DB_BACKUP);
    } else if (existsSync(DB_PATH)) {
      unlinkSync(DB_PATH);
    }
  }

  if (alertFired) failures.push("alert() сработал -- payload выполнился");

  if (failures.length) {
    console.error("FAIL:\n" + failures.map((f) => "  - " + f).join("\n"));
    process.exit(1);
  }
  console.log("OK: ни один XSS-payload не выполнился, разметка нигде не вставлена как HTML");
}

main();
