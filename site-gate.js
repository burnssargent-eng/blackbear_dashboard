/*
 * The sign-in card in front of the whole site: email and password, checked
 * against the shared database's login and its team list (`people`). One
 * sign-in per device covers every page, until Sign out.
 *
 * Analytics pages load this with `data-protect`: the page is hidden until a
 * team member is signed in. The ops pages load it without, for the card and
 * the project address; ops-db.js does their check, since it keeps the shared
 * store as well.
 *
 * A screen, not a lock: the pages' data files are still public in the repo
 * until it goes private (ROADMAP, shared database, step 6).
 */

const OPS_DB_URL = "https://gaxpuvcfgtebklvwfpik.supabase.co";
// The publishable key: public by design. Every table refuses it without a login.
const OPS_DB_KEY = "sb_publishable_xPXWLsADDA0KdhPUX1l0PQ_LOVccktL";
// The signed-in user last confirmed on the team list, so a page opens at once
// and is re-checked in the background.
const GATE_MEMBER_KEY = "bbb-gate-member";

function gateEsc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function gateRemember(userId) {
  try {
    if (userId) window.localStorage.setItem(GATE_MEMBER_KEY, userId);
    else window.localStorage.removeItem(GATE_MEMBER_KEY);
  } catch (err) { /* private mode: every page just checks */ }
}

function gateRemembered() {
  try { return window.localStorage.getItem(GATE_MEMBER_KEY); } catch (err) { return null; }
}

/** The card: sign-in form, or "not on the team list" for a signed-in outsider. */
function gateCardHtml(session, message = "") {
  if (session && session.user && !session.person) {
    return `<div class="ops-gate"><div class="ops-gate-card">
      <h1>Black Bear ops</h1>
      <p class="ops-gate-msg">${gateEsc(session.user.email || "")} is not on the team list.</p>
      <button type="button" class="ops-gate-btn ops-gate-btn-light" data-gate-signout>Sign out</button>
    </div></div>`;
  }
  return `<div class="ops-gate"><form class="ops-gate-card" data-gate-form>
    <h1>Black Bear ops</h1>
    <input type="email" name="email" placeholder="Email" required autocomplete="username"
      inputmode="email" autocapitalize="off">
    <input type="password" name="password" placeholder="Password" required autocomplete="current-password">
    <button class="ops-gate-btn">Sign in</button>
    <p class="ops-gate-msg">${message ? gateEsc(message) : "Team members only. This device stays signed in until you sign out."}</p>
  </form></div>`;
}

const GATE_CSS = `
html.gate-locked body > :not(.ops-gate) { visibility: hidden; }
.ops-gate { position: fixed; inset: 0; z-index: 10000; overflow: auto; background: #f8f9fa;
  display: flex; align-items: flex-start; justify-content: center; padding: 15vh 16px 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #212529; }
.ops-gate-card { box-sizing: border-box; width: 100%; max-width: 340px; background: #fff;
  border: 1px solid #dee2e6; border-radius: 8px; padding: 20px; }
.ops-gate-card h1 { font-size: 20px; font-weight: 500; margin: 0 0 14px; }
.ops-gate-card input { box-sizing: border-box; display: block; width: 100%; margin: 0 0 8px; padding: 6px 12px;
  font-size: 16px; border: 1px solid #ced4da; border-radius: 6px; }
.ops-gate-btn { box-sizing: border-box; display: block; width: 100%; padding: 7px 12px; font-size: 16px;
  border: 1px solid #212529; border-radius: 6px; background: #212529; color: #fff; cursor: pointer; }
.ops-gate-btn:disabled { opacity: 0.6; }
.ops-gate-btn-light { background: #fff; color: #212529; }
.ops-gate-msg { font-size: 13px; color: #666; margin: 10px 0 0; }
`;

function gateStyles() {
  if (document.getElementById("gate-css")) return;
  const style = document.createElement("style");
  style.id = "gate-css";
  style.textContent = GATE_CSS;
  document.head.appendChild(style);
}

/** Cover the page with the card. Signing in reloads the page. */
function gateShow(client, session, message = "") {
  gateStyles();
  document.documentElement.classList.add("gate-locked");
  document.title = "Sign in — Black Bear ops";
  document.querySelectorAll(".ops-gate").forEach(el => el.remove());
  document.body.insertAdjacentHTML("afterbegin", gateCardHtml(session, message));
  const gate = document.body.querySelector(".ops-gate");
  gate.addEventListener("submit", async e => {
    e.preventDefault();
    const form = e.target;
    const data = new FormData(form);
    form.querySelector("button").disabled = true;
    const { error } = await client.auth.signInWithPassword({
      email: String(data.get("email")).trim(), password: String(data.get("password")) });
    if (!error) { window.location.reload(); return; }
    form.querySelector(".ops-gate-msg").textContent =
      /invalid login/i.test(error.message) ? "Wrong email or password." : `Could not sign in: ${error.message}`;
    form.querySelector("button").disabled = false;
  });
  gate.addEventListener("click", async e => {
    if (!e.target.closest("[data-gate-signout]")) return;
    await client.auth.signOut();
    gateRemember(null);
    window.location.reload();
  });
  const first = gate.querySelector("input");
  if (first) first.focus();
}

/**
 * The signed-in user and their team-list row: { user, person } (person null
 * when not on the list), or null when not signed in. Offline, `error` is set.
 */
async function gateMember(client) {
  const { data } = await client.auth.getSession();
  const user = data && data.session && data.session.user;
  if (!user) return null;
  const { data: people, error } = await client.from("people").select("name,role").eq("user_id", user.id);
  if (error) return { user, person: null, error };
  return { user, person: people.length ? people[0] : null };
}

/* Analytics pages: hidden until a team member is signed in. */
if (typeof document !== "undefined" && document.currentScript && document.currentScript.hasAttribute("data-protect")) {
  document.documentElement.classList.add("gate-locked");
  gateStyles();
  const unlock = () => document.documentElement.classList.remove("gate-locked");
  const client = window.supabase
    ? window.supabase.createClient(OPS_DB_URL, OPS_DB_KEY, { auth: { persistSession: true } })
    : null;
  if (!client) unlock();   // the library did not load: the page stays as it always was
  else {
    (async () => {
      const { data } = await client.auth.getSession();
      const user = data && data.session && data.session.user;
      if (user && gateRemembered() === user.id) unlock();   // confirmed before: open now, re-check below
      const member = await gateMember(client);
      const show = (who, msg = "") => (document.body ? gateShow(client, who, msg)
        : document.addEventListener("DOMContentLoaded", () => gateShow(client, who, msg)));
      if (!member) { gateRemember(null); show(null); return; }
      if (member.error) {   // offline: trust a device that was confirmed before
        if (gateRemembered() === member.user.id) unlock();
        else show(null, "Could not reach the sign-in service. Check the connection and reload.");
        return;
      }
      if (!member.person) { gateRemember(null); show(member); return; }
      gateRemember(member.user.id);
      unlock();
    })();
  }
}
