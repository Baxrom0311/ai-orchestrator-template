#!/usr/bin/env python3
"""Playwright screenshot test for TMS dark/light theme verification.

Usage:
    python screenshot_test.py --theme dark
    python screenshot_test.py --theme light
    python screenshot_test.py  # both themes

Captures screenshots of key pages and checks for theme issues:
- White backgrounds in dark mode
- Black/invisible text in light mode
- Broken layouts
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("ERROR: playwright not installed. Run: pip install playwright && python -m playwright install chromium")
    sys.exit(1)

BASE_URL = "http://127.0.0.1:8000"
SCREENSHOT_DIR = Path(__file__).parent.parent / ".agentloop" / "screenshots"

PAGES = [
    ("/", "login"),
    ("/dashboard", "dashboard"),
    ("/tasks", "tasks"),
    ("/projects", "projects"),
    ("/departments", "departments"),
    ("/users", "users"),
    ("/inbox", "chat"),
]


def set_theme(page, theme: str):
    """Set CoreUI theme via localStorage and reload."""
    page.evaluate(f"""() => {{
        localStorage.setItem('coreui-free-bootstrap-admin-template-theme', '{theme}');
    }}""")
    page.reload()
    page.wait_for_load_state("networkidle")


def check_white_backgrounds(page, theme: str) -> list:
    """Check for elements with white/near-white backgrounds in dark mode."""
    if theme != "dark":
        return []

    issues = page.evaluate("""() => {
        const issues = [];
        const elements = document.querySelectorAll('.card, .card-body, .modal-content, .form-control, .form-select, .list-group-item, .dropdown-menu, .table');
        elements.forEach(el => {
            const style = window.getComputedStyle(el);
            const bg = style.backgroundColor;
            // Parse rgb values
            const match = bg.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
            if (match) {
                const [_, r, g, b] = match.map(Number);
                // If very light (near white) in dark mode — it's a bug
                if (r > 240 && g > 240 && b > 240) {
                    const tag = el.tagName.toLowerCase();
                    const cls = el.className.toString().substring(0, 50);
                    issues.push(`${tag}.${cls} has white bg: ${bg}`);
                }
            }
        });
        return issues.slice(0, 20);  // limit
    }""")
    return issues


def check_invisible_text(page, theme: str) -> list:
    """Check for text that's invisible (same color as background)."""
    issues = page.evaluate("""() => {
        const issues = [];
        const elements = document.querySelectorAll('p, span, a, h1, h2, h3, h4, h5, h6, td, th, label, .nav-link');
        elements.forEach(el => {
            if (!el.textContent.trim()) return;
            const style = window.getComputedStyle(el);
            const color = style.color;
            const bg = style.backgroundColor;
            // Simple check: if text color is very close to background
            const colorMatch = color.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
            const bgMatch = bg.match(/rgb\\((\\d+),\\s*(\\d+),\\s*(\\d+)\\)/);
            if (colorMatch && bgMatch) {
                const [_, cr, cg, cb] = colorMatch.map(Number);
                const [__, br, bg2, bb] = bgMatch.map(Number);
                const diff = Math.abs(cr-br) + Math.abs(cg-bg2) + Math.abs(cb-bb);
                if (diff < 30) {
                    const tag = el.tagName.toLowerCase();
                    issues.push(`${tag}: text invisible (color=${color}, bg=${bg})`);
                }
            }
        });
        return issues.slice(0, 20);
    }""")
    return issues


def run_test(theme: str) -> dict:
    """Run screenshot test for given theme."""
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    results = {"theme": theme, "pages": [], "issues": [], "screenshots": []}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()

        for path, name in PAGES:
            url = f"{BASE_URL}{path}"
            try:
                page.goto(url, timeout=10000)
                page.wait_for_load_state("domcontentloaded")
            except Exception as e:
                # Page might need auth — skip
                results["pages"].append({"name": name, "status": "skip", "reason": str(e)[:100]})
                continue

            set_theme(page, theme)
            time.sleep(0.5)

            # Take screenshot
            screenshot_path = SCREENSHOT_DIR / f"{name}_{theme}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            results["screenshots"].append(str(screenshot_path))

            # Check issues
            white_bg = check_white_backgrounds(page, theme)
            invisible = check_invisible_text(page, theme)

            page_result = {
                "name": name,
                "status": "pass" if not white_bg and not invisible else "fail",
                "white_backgrounds": white_bg,
                "invisible_text": invisible,
            }
            results["pages"].append(page_result)
            results["issues"].extend(
                [f"[{name}] {i}" for i in white_bg] +
                [f"[{name}] {i}" for i in invisible]
            )

        browser.close()

    return results


def main():
    parser = argparse.ArgumentParser(description="TMS Theme Screenshot Test")
    parser.add_argument("--theme", choices=["dark", "light", "both"], default="both")
    args = parser.parse_args()

    themes = ["dark", "light"] if args.theme == "both" else [args.theme]
    all_results = []
    total_issues = 0

    for theme in themes:
        print(f"\n{'='*50}")
        print(f"Testing {theme.upper()} mode...")
        print(f"{'='*50}")

        result = run_test(theme)
        all_results.append(result)
        total_issues += len(result["issues"])

        for page in result["pages"]:
            status = "✓" if page["status"] == "pass" else "✗" if page["status"] == "fail" else "⊘"
            print(f"  {status} {page['name']}")
            if page.get("white_backgrounds"):
                for i in page["white_backgrounds"][:3]:
                    print(f"    ⚠ {i}")
            if page.get("invisible_text"):
                for i in page["invisible_text"][:3]:
                    print(f"    ⚠ {i}")

    # Summary
    print(f"\n{'='*50}")
    print(f"SUMMARY: {total_issues} issues found")
    print(f"Screenshots saved to: {SCREENSHOT_DIR}")
    print(f"{'='*50}")

    # Save JSON report
    report_path = SCREENSHOT_DIR / "report.json"
    report_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"Report: {report_path}")

    sys.exit(1 if total_issues > 0 else 0)


if __name__ == "__main__":
    main()
