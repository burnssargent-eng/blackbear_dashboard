/*
 * The dashboard pages' header extras (Sarge, 2026-11-25), loaded by every
 * dashboard page (index.html included; it has no other shared script):
 *
 *   - "Historical ▸" in the nav opens Regions, Years and Customers below it. Under
 *     860px the nav moves into a menu (☰) beside search, as on the Ops pages.
 *   - A search button (top right on a phone, a box beside "Updated" on a
 *     computer) opens the Ops search in a frame (search-frame.html): the same
 *     code, results and "+" as the Ops pages, in the dashboard's dark colours.
 *     The frame drops from the top, as tall as its results; the page stays in
 *     view behind it, dimmed (Sarge, 2026-11-25).
 *     A frame, not the Ops scripts on this page: index.html keeps its own
 *     copies of the shared helpers and projections.html has functions of the
 *     same names, so loading the Ops code here would clash. The frame shares
 *     the sign-in (same site, same browser storage); its links open here.
 */
(function () {
  const FRAME = "search-frame.html";
  const ICON = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
    stroke-linecap="round" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"></circle><path d="M15.5 15.5 21 21"></path></svg>`;
  let frame = null, dim = null;

  function groupShow(btn, open) {
    btn.setAttribute("aria-expanded", String(open));
    const arrow = btn.querySelector(".nav-arrow");
    if (arrow) arrow.textContent = open ? "▾" : "▸";
    const menu = btn.parentElement.querySelector(".nav-group-menu");
    if (menu) menu.hidden = !open;
  }

  function closeGroups(except) {
    document.querySelectorAll("[data-nav-group][aria-expanded='true']").forEach(b => { if (b !== except) groupShow(b, false); });
  }

  function addButton() {
    const header = document.querySelector("header");
    if (!header || header.querySelector(".dash-search-btn")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "dash-search-btn";
    btn.setAttribute("aria-label", "Search");
    btn.innerHTML = `${ICON}<span class="dash-search-text">Search</span>`;
    // Search in its own column at the right, where "Updated" was; "Updated" goes under the
    // title, centred (Sarge, 2026-11-25), as it already sat on a phone.
    const updated = header.querySelector(".last-updated");
    const col = document.createElement("div");
    col.className = "dash-right";
    header.insertBefore(col, updated || null);
    col.appendChild(btn);
    const brand = header.querySelector(".brand-home > div");
    if (updated && brand) brand.appendChild(updated);
    else if (updated) col.appendChild(updated);
    // Under 860px the nav moves into a menu (☰) beside search, as on the Ops pages.
    const menuBtn = document.createElement("button");
    menuBtn.type = "button";
    menuBtn.className = "dash-menu-btn";
    menuBtn.setAttribute("aria-label", "Menu");
    menuBtn.setAttribute("aria-expanded", "false");
    menuBtn.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"
      stroke-linecap="round" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16"></path></svg>`;
    col.insertBefore(menuBtn, btn.nextSibling);
    const menu = document.createElement("nav");
    menu.className = "dash-menu";
    menu.setAttribute("aria-label", "Pages");
    menu.hidden = true;
    header.appendChild(menu);
  }

  /** The menu's rows, from the nav itself (the detail pages fill it after their data loads). */
  function menuHtml() {
    const nav = document.getElementById("main-nav");
    if (!nav) return "";
    const esc = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    const row = (a, sub) => `<a class="dash-menu-item${sub ? " dash-menu-sub" : ""}${a.classList.contains("active") ? " active" : ""}"
      href="${esc(a.getAttribute("href"))}">${esc(a.textContent.trim())}</a>`;
    return [...nav.children].map(el => {
      if (el.tagName === "A") return row(el, false);
      if (!el.classList.contains("nav-group")) return "";
      const label = el.querySelector(".nav-group-btn").firstChild.textContent.trim();
      const subs = [...el.querySelectorAll(".nav-group-menu a")];
      const on = subs.some(a => a.classList.contains("active"));
      return `<button type="button" class="dash-menu-item dash-menu-head${on ? " active" : ""}" data-dash-group aria-expanded="false">${esc(label)}<span
          class="nav-arrow" aria-hidden="true">▸</span></button><div class="dash-menu-group" hidden>${subs.map(a => row(a, true)).join("")}</div>`;
    }).join("");
  }

  function menuShow(open) {
    const menu = document.querySelector(".dash-menu");
    const btn = document.querySelector(".dash-menu-btn");
    if (!menu || !btn) return;
    if (open) menu.innerHTML = menuHtml();
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    btn.classList.toggle("dash-open", open);
  }

  function open() {
    if (!frame) {
      frame = document.createElement("iframe");
      frame.className = "dash-search-frame";
      frame.title = "Search";
      frame.src = FRAME;
      // The page stays in view behind the search, dimmed; a tap on it closes the search.
      dim = document.createElement("div");
      dim.className = "dash-search-dim";
      document.body.appendChild(dim);
      document.body.appendChild(frame);
    } else {
      dim.hidden = false;
      frame.hidden = false;
      try { frame.contentWindow.postMessage({ type: "dash-search-open" }, window.location.origin); } catch (err) { /* */ }
    }
    document.body.classList.add("dash-s-open");
    frame.focus();
  }

  function close() {
    if (!frame) return;
    frame.hidden = true;
    if (dim) dim.hidden = true;
    document.body.classList.remove("dash-s-open");
  }

  document.addEventListener("click", e => {
    if (!e.target.closest) return;
    const group = e.target.closest("[data-nav-group]");
    if (group) {
      const openNow = group.getAttribute("aria-expanded") !== "true";
      closeGroups(group);
      groupShow(group, openNow);
      return;
    }
    if (!e.target.closest(".nav-group-menu")) closeGroups(null);
    if (e.target.closest(".dash-menu-btn")) { menuShow(document.querySelector(".dash-menu").hidden); return; }
    const head = e.target.closest("[data-dash-group]");
    if (head) {
      const openNow = head.getAttribute("aria-expanded") !== "true";
      head.setAttribute("aria-expanded", String(openNow));
      head.querySelector(".nav-arrow").textContent = openNow ? "▾" : "▸";
      head.nextElementSibling.hidden = !openNow;
      return;
    }
    if (!e.target.closest(".dash-menu")) menuShow(false);
    if (e.target.closest(".dash-search-btn")) { open(); return; }
    // From a computer the frame is a panel under the header: a click elsewhere closes it.
    if (frame && !frame.hidden) close();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { closeGroups(null); menuShow(false); close(); }
  });
  window.addEventListener("message", e => {
    if (e.origin !== window.location.origin || !e.data) return;
    if (e.data.type === "dash-search-close") close();
    // The frame is as tall as what it shows (the search box, its results, Assign's bar).
    if (e.data.type === "dash-search-size" && frame) {
      // On a phone at most three quarters of the screen, so some of the page always shows (and takes a tap to close).
      const most = window.innerWidth > 860 ? window.innerHeight - 16 : Math.round(window.innerHeight * 0.75);
      frame.style.height = `${Math.min(Number(e.data.h) || 0, most)}px`;
    }
  });

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", addButton);
  else addButton();
})();
