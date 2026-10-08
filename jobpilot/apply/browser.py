"""Stage 6: open the application page, fill it, and (depending on mode) submit.

One generic filler covers Greenhouse, Lever, Ashby and most plain HTML forms:
it reads every visible control and its human label from the DOM, asks the
AnswerResolver for a value, and fills it. Nothing here is ATS-specific except
a few button texts, so new ATSs mostly just work.

Modes (settings.apply.mode):
  assisted  fill in a visible browser, then *you* review and click Submit
  auto      click Submit only if every required field was answered, nothing
            was LLM-drafted, and there is no CAPTCHA; otherwise behave as assisted
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .answers import Answer, AnswerResolver, FormField

EXTRACT_JS = r"""
() => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const text = el => el ? clean(el.innerText || el.textContent) : '';
  const visible = el => el.type === 'file' || !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const byIds = ids => clean(ids.split(/\s+/).map(id => text(document.getElementById(id))).join(' '));
  const ownLabel = el => {
    if (el.getAttribute('aria-labelledby')) { const t = byIds(el.getAttribute('aria-labelledby')); if (t) return t; }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return text(l); }
    const wrap = el.closest('label'); if (wrap) return text(wrap);
    if (el.getAttribute('aria-label')) return clean(el.getAttribute('aria-label'));
    return '';
  };
  // Upload widgets label their input "Attach"/"Upload"; the question is further up.
  const GENERIC = /^(attach|upload|browse|choose( a)? file|select( a)? file|drop files? here|or|enter manually|dropbox|google drive)$/i;
  const nearbyLabel = el => {
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      for (const l of p.querySelectorAll('label, legend, [class*="label"], [class*="question"]')) {
        const t = text(l);
        if (!l.contains(el) && t && !GENERIC.test(t)) return t;
      }
    }
    return '';
  };
  const groupLabel = el => {
    const fs = el.closest('fieldset'); if (fs) { const lg = fs.querySelector('legend'); if (lg) return text(lg); }
    const rg = el.closest('[role="radiogroup"], [role="group"]');
    if (rg) {
      if (rg.getAttribute('aria-labelledby')) return byIds(rg.getAttribute('aria-labelledby'));
      if (rg.getAttribute('aria-label')) return clean(rg.getAttribute('aria-label'));
    }
    let p = el.parentElement;  // climb until the container holds more than this group
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const l = p.querySelector('label:not(:has(input)), legend, [class*="label"], [class*="question"]');
      if (l && !l.contains(el) && text(l)) return text(l);
    }
    return '';
  };
  const isRequired = (el, label) => el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label);

  const fields = [];
  const groups = new Set();
  let n = 0;
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'input' ? (el.type || 'text').toLowerCase() : tag;
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type) || el.disabled) continue;
    // Custom dropdowns keep an invisible twin input for validation; it isn't a question.
    if (el.getAttribute('aria-hidden') === 'true' || !visible(el)) continue;

    if ((type === 'radio' || type === 'checkbox') && el.name) {
      const members = [...document.querySelectorAll(`input[type="${type}"][name="${CSS.escape(el.name)}"]`)];
      if (members.length > 1 || type === 'radio') {
        if (groups.has(type + el.name)) continue;
        groups.add(type + el.name);
        const key = 'jp' + (n++);
        const option_keys = members.map((m, i) => { m.setAttribute('data-jp-key', `${key}-${i}`); return `${key}-${i}`; });
        const label = groupLabel(el) || el.name;
        fields.push({ key, kind: type === 'radio' ? 'radio' : 'checkbox_group', label, hint: el.name,
                      required: members.some(m => m.required) || /\*\s*$/.test(label),
                      options: members.map(m => ownLabel(m) || m.value), option_keys });
        continue;
      }
    }
    const key = 'jp' + (n++);
    el.setAttribute('data-jp-key', key);
    let kind = type;
    if (el.getAttribute('role') === 'combobox' && tag === 'input') kind = 'combobox';
    else if (!['text', 'email', 'tel', 'url', 'number', 'date', 'textarea', 'select', 'checkbox', 'file'].includes(kind)) kind = 'text';
    let label = ownLabel(el);
    if (!label || GENERIC.test(label)) label = nearbyLabel(el) || el.placeholder || el.name || el.id || label;
    const options = tag === 'select' ? [...el.options].filter(o => o.value !== '').map(o => clean(o.text)) : [];
    const help = el.getAttribute('aria-describedby') ? byIds(el.getAttribute('aria-describedby')) : '';
    fields.push({ key, kind, label, hint: clean(`${el.id} ${el.name}`), help, required: isRequired(el, label), options,
                  option_keys: [] });
  }
  return fields;
}
"""

CAPTCHA_SELECTOR = 'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha'
APPLY_BUTTON = re.compile(r"^\s*(apply|apply now|apply for this (job|position|role)|i'?m interested)\s*$", re.I)
SUBMIT_BUTTON = re.compile(r"submit( application)?|send application|apply", re.I)
CONFIRMATION = re.compile(r"thank you|thanks for applying|application (has been |was )?(received|submitted)|"
                          r"we('ve| have) received|successfully submitted", re.I)
TRUTHY = {"yes", "true", "checked", "agree", "i agree", "accept", "i accept", "y"}


@dataclass
class FilledField:
    label: str
    kind: str
    required: bool
    value: Optional[str]
    source: Optional[str]
    needs_review: bool = False
    error: Optional[str] = None


@dataclass
class FillResult:
    url: str
    filled: list[FilledField] = field(default_factory=list)
    missing_required: list[FormField] = field(default_factory=list)
    captcha: bool = False
    submitted: bool = False
    confirmation: Optional[str] = None
    screenshot: Optional[str] = None

    @property
    def needs_review(self) -> bool:
        return any(f.needs_review for f in self.filled)

    @property
    def errors(self) -> list[FilledField]:
        return [f for f in self.filled if f.error]

    def can_auto_submit(self) -> bool:
        return not (self.missing_required or self.captcha or self.needs_review or self.errors)

    def save(self, path: Path) -> None:
        data = asdict(self)
        data["missing_required"] = [asdict(f) for f in self.missing_required]
        path.write_text(json.dumps(data, indent=2))


def extract_fields(page) -> list[FormField]:
    return [FormField(**f) for f in page.evaluate(EXTRACT_JS)]


def _settle(page) -> None:
    """Wait for client-rendered forms; some pages never go fully idle (analytics, chat widgets)."""
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass


def _open_form(page, url: str) -> list[FormField]:
    page.goto(url, wait_until="domcontentloaded")
    _settle(page)
    fields = extract_fields(page)
    if len([f for f in fields if f.kind != "checkbox"]) < 3:  # probably the description page
        button = page.get_by_role("link", name=APPLY_BUTTON).or_(page.get_by_role("button", name=APPLY_BUTTON))
        if button.count():
            button.first.click()
            _settle(page)
            fields = extract_fields(page)
    return fields


def _fill_one(page, f: FormField, answer: Answer) -> None:
    loc = page.locator(f'[data-jp-key="{f.key}"]')
    if f.kind == "file":
        loc.set_input_files(answer.value)
    elif f.kind == "select":
        loc.select_option(label=answer.value)
    elif f.kind == "checkbox":
        if answer.value.strip().lower() in TRUTHY:
            loc.check(force=True)
    elif f.kind in ("radio", "checkbox_group"):
        idx = f.options.index(answer.value)
        page.locator(f'[data-jp-key="{f.option_keys[idx]}"]').check(force=True)
    elif f.kind == "combobox":
        _fill_combobox(page, loc, answer.value)
    else:
        loc.fill(answer.value)


def _visible_options(page, combobox):
    """Options of an open dropdown, scoped to the listbox it controls when it says which."""
    listbox = combobox.get_attribute("aria-controls")
    if listbox:
        return page.locator(f'[id="{listbox}"] [role="option"]:visible')
    return page.locator('[role="option"]:visible')


def _probe_combobox(page, f: FormField) -> None:
    """Custom dropdowns (Greenhouse/Ashby react-select) only render options when
    opened. Read them up front so answers can be matched against real choices."""
    loc = page.locator(f'[data-jp-key="{f.key}"]')
    try:
        loc.click(timeout=3000)
        page.wait_for_timeout(300)
        f.options = [t.strip() for t in _visible_options(page, loc).all_inner_texts() if t.strip()][:200]
        page.keyboard.press("Escape")
    except Exception:
        pass  # leave options empty; filling will type and search instead


def _fill_combobox(page, loc, value: str) -> None:
    from .answers import match_option

    loc.click()
    loc.fill(value)
    page.wait_for_timeout(800)  # async option lists (e.g. location search)
    options = _visible_options(page, loc)
    texts = [t.strip() for t in options.all_inner_texts()]
    choice = match_option(value, texts) if texts else None
    if choice is None and len(texts) == 1:
        choice = texts[0]  # typing narrowed it to one candidate (e.g. a location search)
    if choice is None:
        page.keyboard.press("Escape")
        raise ValueError(f"no dropdown option matches {value!r} (saw {texts[:5]})")
    options.nth(texts.index(choice)).click()


def fill_application(url: str, resolver: AnswerResolver, out_dir: Path, *, mode: str, headless: bool,
                     confirm: Callable[[FillResult], bool], log: Callable[[str], None] = print) -> FillResult:
    """Fill the form at `url`. `confirm` is called in assisted mode (or when auto
    mode can't submit safely) while the browser is still open; it returns True if
    the human submitted the application."""
    from playwright.sync_api import sync_playwright

    from ..chromium import launch

    result = FillResult(url=url)
    with sync_playwright() as p:
        browser = launch(p, headless=headless)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        try:
            fields = _open_form(page, url)
            result.captcha = page.locator(CAPTCHA_SELECTOR).count() > 0
            for f in fields:
                if f.kind == "combobox":
                    _probe_combobox(page, f)
                answer = resolver.resolve(f)
                if answer is None:
                    if f.required:
                        result.missing_required.append(f)
                    result.filled.append(FilledField(f.label, f.kind, f.required, None, None))
                    continue
                entry = FilledField(f.label, f.kind, f.required, answer.value, answer.source, answer.needs_review)
                try:
                    _fill_one(page, f, answer)
                except Exception as e:  # one stubborn widget shouldn't sink the whole form
                    entry.error = f"{type(e).__name__}: {str(e).splitlines()[0]}"
                    if f.required:
                        result.missing_required.append(f)
                result.filled.append(entry)

            shot = out_dir / "filled_form.png"
            page.screenshot(path=str(shot), full_page=True)
            result.screenshot = str(shot)

            if mode == "auto" and result.can_auto_submit():
                result.submitted, result.confirmation = _submit(page)
                page.screenshot(path=str(out_dir / "after_submit.png"), full_page=True)
            elif mode in ("assisted", "auto"):
                if mode == "auto":
                    log("Auto-submit skipped (missing answers, LLM-drafted answers, errors or CAPTCHA); "
                        "falling back to assisted.")
                result.submitted = confirm(result)
        finally:
            result.save(out_dir / "fill_report.json")
            browser.close()
    return result


def _submit(page) -> tuple[bool, Optional[str]]:
    button = page.locator('button[type="submit"], input[type="submit"]')
    if not button.count():
        button = page.get_by_role("button", name=SUBMIT_BUTTON)
    if not button.count():
        return False, None
    button.last.click()
    _settle(page)
    body = page.locator("body").inner_text()
    m = CONFIRMATION.search(body)
    return (bool(m), m.group(0) if m else None)
