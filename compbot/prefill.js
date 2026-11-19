// Fills empty, visible form fields with the user's details. Never ticks boxes or submits.
(me) => {
  const filled = [];
  const STATES = {
    NSW: ["nsw", "new south wales"], VIC: ["vic", "victoria"], QLD: ["qld", "queensland"],
    WA: ["wa", "western australia"], SA: ["sa", "south australia"], TAS: ["tas", "tasmania"],
    ACT: ["act", "australian capital territory"], NT: ["nt", "northern territory"],
  };

  // Each field's own window, so this works inside same-origin iframes too.
  const win = (el) => el.ownerDocument.defaultView || window;

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    const s = win(el).getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none";
  };

  const text = (n) => (n && (n.innerText || n.textContent) || "").trim();

  const labelText = (el) => {
    let t = "";
    const root = el.getRootNode();
    if (el.id && root.querySelector) {
      const l = root.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) t += " " + text(l);
    }
    const wrap = el.closest("label");
    if (wrap) t += " " + text(wrap);
    const by = el.getAttribute("aria-labelledby");
    if (by && root.getElementById) by.split(/\s+/).forEach((id) => { const n = root.getElementById(id); if (n) t += " " + text(n); });
    return t;
  };

  // Popup forms often put the label in a plain <div> or <p> next to the box instead of a <label>.
  const nearbyText = (el) => {
    let node = el;
    for (let depth = 0; depth < 3 && node; depth++, node = node.parentElement) {
      const isField = (n) => /^(INPUT|SELECT|TEXTAREA)$/.test(n.tagName) || n.querySelector("input, select, textarea");
      let sib = node.previousElementSibling;
      while (sib && !isField(sib) && !text(sib)) sib = sib.previousElementSibling;
      if (sib && isField(sib)) return "";  // that text belongs to another field
      const t = text(sib);
      if (t && t.length <= 60) return t;
      if (node.parentElement && node.parentElement.querySelectorAll("input, select, textarea").length > 1) break;
    }
    return "";
  };

  // Finds fields in the page, in open shadow roots (web components) and in same-origin iframes.
  const collect = (root, out, seen) => {
    if (!root || seen.has(root)) return out;
    seen.add(root);
    for (const el of root.querySelectorAll("*")) {
      if (/^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) out.push(el);
      if (el.shadowRoot) collect(el.shadowRoot, out, seen);
      if (/^(IFRAME|FRAME)$/.test(el.tagName)) {
        let doc = null;
        try { doc = el.contentDocument; } catch (e) { /* another site's frame: the browser keeps it private */ }
        if (doc) collect(doc, out, seen);
      }
    }
    return out;
  };

  const describe = (el) => [
    el.name, el.id, el.getAttribute("placeholder"), el.getAttribute("aria-label"),
    el.getAttribute("autocomplete"), el.getAttribute("data-name"), labelText(el),
  ].filter(Boolean).join(" | ").toLowerCase();

  const dob = me.date_of_birth || "";
  const [y, m, d] = dob.split("-");
  const fullName = [me.first_name, me.last_name].filter(Boolean).join(" ");

  // Order matters: more specific rules first.
  const rules = [
    { key: "confirm email", re: /(confirm|re-?enter|repeat|verify).{0,20}e-?mail|e-?mail.{0,20}(confirm|again)/, value: me.email },
    { key: "email", re: /e-?mail/, type: "email", value: me.email },
    { key: "first name", re: /first.?name|given.?name|\bfname\b|forename|given-name/, value: me.first_name },
    { key: "last name", re: /last.?name|surname|family.?name|\blname\b|family-name/, value: me.last_name },
    { key: "full name", re: /full.?name|your.?name|^name\b|\bname$|^name \|/, not: /user.?name|company|business|store|pet|child|friend/, value: fullName },
    { key: "phone", re: /phone|mobile|\bmob\b|\btel\b|contact.?number/, type: "tel", value: me.phone },
    { key: "postcode", re: /post.?code|\bzip\b|postal|postal-code/, value: me.postcode },
    { key: "suburb", re: /suburb|\bcity\b|\btown\b|locality|address-level2/, value: me.suburb },
    { key: "state", re: /\bstate\b|province|address-level1/, not: /statement/, value: me.state },
    { key: "address", re: /street|address.?(line)?.?1?|address-line1/, not: /e-?mail|ip.?address|line.?2|address-line2/, value: me.address },
    { key: "country", re: /country/, value: me.country || "Australia" },
    { key: "date of birth", re: /birth|\bdob\b|bday|birthday/, value: dob },
    { key: "gender", re: /gender|\bsex\b/, value: me.gender },
  ];

  const setValue = (el, value) => {
    const w = win(el);
    const proto = el.tagName === "TEXTAREA" ? w.HTMLTextAreaElement.prototype : w.HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    el.blur();
  };

  const pickOption = (sel, rule, value) => {
    const v = String(value).toLowerCase();
    let wanted = [v];
    if (rule.key === "state") {
      const code = Object.keys(STATES).find((k) => STATES[k].includes(v)) || v.toUpperCase();
      wanted = STATES[code] || [v];
    }
    const opt = [...sel.options].find((o) => {
      const t = o.text.trim().toLowerCase(), val = o.value.trim().toLowerCase();
      return wanted.includes(t) || wanted.includes(val) || (rule.key !== "state" && t.startsWith(v));
    });
    if (!opt) return false;
    sel.value = opt.value;
    sel.dispatchEvent(new Event("input", { bubbles: true }));
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  };

  const fields = collect(document, [], new Set());
  for (const el of fields) {
    if (el.disabled || el.readOnly || !visible(el)) continue;
    const type = (el.getAttribute("type") || "text").toLowerCase();
    if (["hidden", "checkbox", "radio", "submit", "button", "password", "file", "image", "reset", "search"].includes(type)) continue;
    if (el.tagName !== "SELECT" && el.value) continue;
    const match = (desc) => rules.find((r) =>
      r.value && ((r.type && type === r.type) || r.re.test(desc)) && !(r.not && r.not.test(desc))
    );
    const rule = match(describe(el)) || match(nearbyText(el).toLowerCase());
    if (!rule) continue;

    if (el.tagName === "SELECT") {
      if (el.selectedIndex > 0) continue;
      if (pickOption(el, rule, rule.value)) filled.push(rule.key);
      continue;
    }
    if (el.tagName === "TEXTAREA") continue; // free-text answers are yours to write
    let value = rule.value;
    if (rule.key === "date of birth" && dob) {
      value = type === "date" ? dob : `${d}/${m}/${y}`;
    }
    setValue(el, value);
    filled.push(rule.key);
  }
  return filled;
}
