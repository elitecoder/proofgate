import pathlib, sys
from playwright.sync_api import sync_playwright
here = pathlib.Path(__file__).parent
out = pathlib.Path(sys.argv[1])
with sync_playwright() as p:
    b = p.chromium.launch()
    for name in ("catches", "when"):
        for theme in ("light", "dark"):
            pg = b.new_page(viewport={"width": 880, "height": 200}, device_scale_factor=2)
            pg.goto((here / f"{name}.html").as_uri())
            if theme == "dark":
                pg.evaluate("document.documentElement.classList.add('dark')")
            pg.locator("body").screenshot(path=str(out / f"{name}-{theme}.png"))
