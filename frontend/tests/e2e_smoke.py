"""End-to-end smoke test in mock mode. Run: python3 tests/e2e_smoke.py  (starts its own static server)."""
import functools, http.server, os, sys, threading
from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screens")
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=ROOT)
H.log_message = lambda *a, **k: None
http.server.SimpleHTTPRequestHandler.log_message = lambda *a, **k: None
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
errors = []

def shot(page, name):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=False)

with sync_playwright() as p:
    b = p.chromium.launch()
    page = b.new_page(viewport={"width": 1360, "height": 860})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "fonts.g" not in m.text and "ERR_" not in m.text else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{BASE}/index.html?mock=1&fast=1#/")
    page.wait_for_selector(".login")
    shot(page, "01-login")
    page.get_by_role("button", name="Continue").first.click() if page.get_by_role("button", name="Continue").count() else page.locator(".login-form button").first.click()
    page.wait_for_selector(".dt")
    page.wait_for_timeout(300)
    shot(page, "02-library")
    page.goto(f"{BASE}/index.html?mock=1&fast=1#/jd")
    page.wait_for_selector(".jd-list")
    shot(page, "03-jd-new")
    page.goto(f"{BASE}/index.html?mock=1&fast=1#/jd/47a5ace6-a614-5f83-a6b7-193395ec3fc5")
    page.wait_for_selector(".search-panel")
    shot(page, "04-jd-edit")
    page.get_by_role("button", name="Search resumes").click()
    page.wait_for_selector("table.dt tbody tr")
    page.wait_for_timeout(300)
    shot(page, "05-results")
    assert page.locator("table.dt tbody tr").count() >= 5, "results rows"
    page.locator(".cand-link").first.click()
    page.wait_for_selector(".drawer")
    shot(page, "06-drawer")
    page.get_by_role("button", name="Analyse fit").click()
    page.wait_for_selector(".gap", timeout=15000)
    page.wait_for_timeout(1200)
    marks = page.locator("mark.ev").count()
    assert marks >= 1, "no highlighted evidence"
    shot(page, "07-analysis")
    page.keyboard.press("Escape")
    page.wait_for_selector(".drawer", state="detached")
    page.locator("tbody input[type=checkbox]").nth(0).check()
    page.locator("tbody input[type=checkbox]").nth(1).check()
    page.get_by_role("link", name="Compare (2)").click()
    page.wait_for_selector(".cmp")
    shot(page, "08-compare")
    m = b.new_page(viewport={"width": 390, "height": 800})
    m.on("pageerror", lambda e: errors.append(str(e)))
    m.goto(f"{BASE}/index.html?mock=1&fast=1#/")
    m.wait_for_selector(".login"); m.locator(".login-form button").first.click(); m.wait_for_selector(".dt")
    m.screenshot(path=f"{OUT}/09-mobile-library.png")
    assert m.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "horizontal overflow on mobile"
    b.close()
srv.shutdown()
print("highlight marks:", marks)
if errors:
    print("ERRORS:", *errors, sep="\n  "); sys.exit(1)
print("E2E OK")
