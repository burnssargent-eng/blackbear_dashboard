/*
 * Customer photos on the shadow pages: the barrel, its spot, the lock.
 * Loaded after ops-db.js. Files live in the private Storage bucket `photos`,
 * one row each in the `photos` table (db/schema.sql); both are members-only,
 * so nothing shows when signed out.
 *
 * The phone shrinks each picture before upload (the free plan has 1 GB and no
 * server-side resizing): a 1600 px JPEG for the full view and a 400 px
 * thumbnail for the strips, `<customer>/<uuid>.jpg` and `<customer>/<uuid>_t.jpg`.
 * Images load through signed links that lapse after an hour, so a copied link
 * stops working. Remove only hides a photo (`removed_at`): a theft photo is
 * evidence, and Undo puts it back.
 *
 * Photos need signal: they are not part of the offline save queue.
 */

const PHOTO_FULL_PX = 1600;
const PHOTO_THUMB_PX = 400;
const PHOTO_LINK_SECONDS = 3600;
const PHOTO_MAX_BYTES = 1900000;          // under the bucket's 2 MB limit (db/schema.sql)
const PHOTO_RESIGN_MS = 45 * 60 * 1000;   // re-sign thumbnails before their links lapse

const opsPhotos = { byCustomer: new Map(), urls: new Map(), people: new Map(), signedAt: 0, ids: [] };

/** Signed in as a team member, with the storage library loaded. */
function opsPhotosOn() {
  return typeof opsDb !== "undefined" && !!opsDb && !!opsDb.person && !!opsDb.client && !!opsDb.client.storage;
}

const opsThumbPath = path => path.replace(/\.jpg$/, "_t.jpg");

/** This customer's photos, newest first (empty until opsPhotosLoad has run). */
function opsPhotosFor(customerId) {
  return opsPhotos.byCustomer.get(customerId) || [];
}

/**
 * Load the photos of these customers and sign their thumbnails. Resolves to
 * false when signed out or offline (the strips then stay empty).
 */
async function opsPhotosLoad(customerIds) {
  if (!opsPhotosOn() || !customerIds.length) return false;
  opsPhotos.ids = customerIds.slice();
  const c = opsDb.client;
  try {
    const [rowsRes, peopleRes] = await Promise.all([
      c.from("photos").select("id,customer_id,storage_path,caption,taken_at,uploaded_by,uploaded_at")
        .in("customer_id", customerIds).is("removed_at", null).order("uploaded_at", { ascending: false }),
      opsPhotos.people.size ? Promise.resolve({ data: null }) : c.from("people").select("user_id,name"),
    ]);
    if (rowsRes.error) throw rowsRes.error;
    (peopleRes.data || []).forEach(p => opsPhotos.people.set(p.user_id, p.name));
    opsPhotos.byCustomer = new Map();
    rowsRes.data.forEach(r => {
      if (!opsPhotos.byCustomer.has(r.customer_id)) opsPhotos.byCustomer.set(r.customer_id, []);
      opsPhotos.byCustomer.get(r.customer_id).push(r);
    });
    const thumbs = rowsRes.data.map(r => opsThumbPath(r.storage_path));
    opsPhotos.urls = new Map();
    if (thumbs.length) {
      const { data, error } = await c.storage.from("photos").createSignedUrls(thumbs, PHOTO_LINK_SECONDS);
      if (error) throw error;
      data.forEach(d => { if (d.signedUrl) opsPhotos.urls.set(d.path, d.signedUrl); });
    }
    opsPhotos.signedAt = Date.now();
    return true;
  } catch (err) {
    console.warn("[ops-photos] could not load", err);
    if (typeof opsLog === "function") opsLog("photos load FAILED", err && err.message);
    return false;
  }
}

/** Reload (and re-sign) when the page comes back after the links may have lapsed. */
function opsPhotosWatch(onReload) {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible" || !opsPhotos.ids.length) return;
    if (Date.now() - opsPhotos.signedAt < PHOTO_RESIGN_MS) return;
    opsPhotosLoad(opsPhotos.ids).then(ok => { if (ok) onReload(); });
  });
}

/**
 * A row of thumbnails. `add` puts the Add photo tile at the end (customer page);
 * My Route shows the strip only when there are photos.
 */
function opsPhotoStripHtml(customerId, { add = false } = {}) {
  const rows = opsPhotosFor(customerId);
  if (!rows.length && !add) return "";
  const thumbs = rows.map(r => {
    const url = opsPhotos.urls.get(opsThumbPath(r.storage_path));
    const label = r.caption ? `Photo: ${r.caption}` : "Photo";
    return `<div class="ops-photo">
      <button type="button" class="ops-photo-thumb" data-photo="${r.id}" aria-label="${escapeHtml(label)}">
        ${url ? `<img src="${escapeHtml(url)}" alt="" loading="lazy">` : ""}</button>
      ${add && r.caption ? `<div class="ops-photo-cap">${escapeHtml(r.caption)}</div>` : ""}
    </div>`;
  }).join("");
  const tile = add ? `<button type="button" class="ops-photo-add" data-photo-add>
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <path d="M3 8h4l2-3h6l2 3h4v11H3z"></path><circle cx="12" cy="13" r="4"></circle></svg>
      Add photo</button>` : "";
  return `<div class="ops-photo-strip${add ? "" : " ops-photo-strip-sm"}">${thumbs}${tile}</div>`;
}

/**
 * The phone's photo picker. One file input for the page, outside everything
 * the page redraws: iPhone Safari hands the picked file to the input that
 * opened the picker, so an input redrawn while the picker was open lost the
 * photo (Sarge's first phone test, 2026-10-24). Call it from the Add photo
 * tap itself, so the browser counts it as the user's own action.
 */
let opsPickerInput = null, opsPickerCallback = null;
function opsPhotoPick(onPick) {
  if (!opsPickerInput) {
    opsPickerInput = document.createElement("input");
    opsPickerInput.type = "file";
    opsPickerInput.accept = "image/*";
    opsPickerInput.setAttribute("aria-hidden", "true");
    opsPickerInput.tabIndex = -1;
    opsPickerInput.style.cssText = "position:fixed;left:0;top:0;width:1px;height:1px;opacity:0;pointer-events:none;";
    document.body.appendChild(opsPickerInput);
    const picked = ev => {
      const file = opsPickerInput.files && opsPickerInput.files[0];
      if (typeof opsLog === "function") opsLog("photo picker", ev.type, file ? `${file.type || "?"} ${file.size} bytes` : "no file");
      if (!file || !opsPickerCallback) return;
      const done = opsPickerCallback;
      opsPickerCallback = null;
      done(file);
    };
    // Some iPhone versions send only one of the two.
    opsPickerInput.addEventListener("change", picked);
    opsPickerInput.addEventListener("input", picked);
  }
  opsPickerInput.value = "";   // the same photo twice in a row still counts as a pick
  opsPickerCallback = onPick;
  if (typeof opsLog === "function") opsLog("photo picker opened");
  opsPickerInput.click();
}

/** Redraw a picture at most `px` on its long side, as a JPEG Blob. */
async function opsPhotoShrink(file, px, quality) {
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise((ok, bad) => {
      const i = new Image();
      i.onload = () => ok(i);
      i.onerror = () => bad(new Error("That file is not a picture this browser can open."));
      i.src = url;
    });
    const scale = Math.min(1, px / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale));
    canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
    return await new Promise((ok, bad) => canvas.toBlob(
      b => (b ? ok(b) : bad(new Error("Could not shrink the photo."))), "image/jpeg", quality));
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Shrink, upload both sizes, and record the photo. Throws with a readable message. */
async function opsPhotoUpload(customerId, file, caption) {
  if (!opsPhotosOn()) throw new Error("Sign in to add photos.");
  const log = (...a) => { if (typeof opsLog === "function") opsLog("photo upload", ...a); };
  log("shrinking", file.type || "?", file.size);
  // One after the other, the thumbnail from the small copy: decoding a 12-48 MP
  // original twice at once can run an older phone out of memory.
  let full = await opsPhotoShrink(file, PHOTO_FULL_PX, 0.8);
  // The bucket refuses files over 2 MB; a very busy picture gets a second, lighter pass.
  if (full.size > PHOTO_MAX_BYTES) full = await opsPhotoShrink(full, PHOTO_FULL_PX, 0.6);
  if (full.size > PHOTO_MAX_BYTES) throw new Error("That photo is too large even after shrinking.");
  const thumb = await opsPhotoShrink(full, PHOTO_THUMB_PX, 0.75);
  log("shrunk to", full.size, "+", thumb.size, "bytes");
  const path = `${customerId}/${opsRandomId()}.jpg`;
  const bucket = opsDb.client.storage.from("photos");
  for (const [p, blob] of [[path, full], [opsThumbPath(path), thumb]]) {
    const { error } = await bucket.upload(p, blob, { contentType: "image/jpeg", upsert: false });
    if (error) throw new Error(`Upload failed (${error.message}).`);
  }
  log("files stored");
  const taken = file.lastModified ? new Date(file.lastModified).toISOString() : null;
  const { error } = await opsDb.client.from("photos")
    .insert({ customer_id: customerId, storage_path: path, caption: caption || null, taken_at: taken });
  if (error) throw new Error(`Not saved (${error.message}).`);
}

async function opsPhotoCaption(photoId, caption) {
  const { error } = await opsDb.client.from("photos").update({ caption: caption || null }).eq("id", photoId);
  if (error) throw new Error(`Caption not saved (${error.message}).`);
}

/** Remove (hide) a photo, or with `removed` false put it back. */
async function opsPhotoRemove(photoId, removed = true) {
  const { error } = await opsDb.client.from("photos")
    .update(removed ? { removed_at: new Date().toISOString(), removed_by: opsDb.user.id }
      : { removed_at: null, removed_by: null }).eq("id", photoId);
  if (error) throw new Error(`Not changed (${error.message}).`);
}

/** "Added by Jim · Oct 20, 2026" */
function opsPhotoByline(r) {
  const who = opsPhotos.people.get(r.uploaded_by);
  const day = new Date(r.uploaded_at).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  return `Added ${who ? `by ${who} ` : ""}· ${day}`;
}

/**
 * The full view over the page: one photo with its caption and who added it,
 * ‹ › (or a swipe) for the others. `editable` adds Edit caption and Remove
 * (customer page); `onChange(kind, row)` runs after a caption is saved or a
 * photo removed, and the page redraws and offers Undo.
 */
function opsPhotoViewer(customerId, photoId, { editable = false, onChange = () => {} } = {}) {
  const rows = opsPhotosFor(customerId).slice();
  if (!rows.length) return;
  let k = Math.max(0, rows.findIndex(r => r.id === photoId));
  let editing = false, message = "";
  const box = document.createElement("div");
  box.className = "ops-viewer";
  box.setAttribute("role", "dialog");
  box.setAttribute("aria-modal", "true");
  document.body.appendChild(box);
  document.body.classList.add("ops-viewer-open");
  const close = () => { box.remove(); document.body.classList.remove("ops-viewer-open"); };

  const fullUrls = new Map();
  const draw = () => {
    const r = rows[k];
    const url = fullUrls.get(r.storage_path) || opsPhotos.urls.get(opsThumbPath(r.storage_path));
    box.innerHTML = `
      <div class="ops-viewer-top">
        <span>${rows.length > 1 ? `${k + 1} of ${rows.length}` : ""}</span>
        <button type="button" class="ops-viewer-close" data-v="close" aria-label="Close">×</button>
      </div>
      <div class="ops-viewer-img">
        ${url ? `<img src="${escapeHtml(url)}" alt="${escapeHtml(r.caption || "Photo")}">` : ""}
        ${rows.length > 1 ? `<button type="button" class="ops-viewer-nav ops-viewer-prev" data-v="prev" aria-label="Previous photo">‹</button>
          <button type="button" class="ops-viewer-nav ops-viewer-next" data-v="next" aria-label="Next photo">›</button>` : ""}
      </div>
      <div class="ops-viewer-foot">
        ${editing ? `<form class="ops-viewer-edit" data-v="caption-form">
            <input type="text" class="form-control" maxlength="200" name="caption" value="${escapeHtml(r.caption || "")}"
              placeholder="Caption" aria-label="Caption">
            <button class="btn btn-primary">Save</button>
            <button type="button" class="btn ops-viewer-btn" data-v="caption-cancel">Cancel</button>
          </form>`
        : `<div class="ops-viewer-cap">${escapeHtml(r.caption || "")}</div>`}
        <div class="ops-viewer-by">${escapeHtml(opsPhotoByline(r))}</div>
        ${message ? `<div class="ops-viewer-msg">${escapeHtml(message)}</div>` : ""}
        ${editable && !editing ? `<div class="ops-viewer-acts">
            <button type="button" class="btn ops-viewer-btn" data-v="caption">Edit caption</button>
            <button type="button" class="btn ops-viewer-btn ms-auto" data-v="remove">Remove</button>
          </div>` : ""}
      </div>`;
    if (editing) box.querySelector("input").focus();
    if (!fullUrls.has(r.storage_path)) {
      opsDb.client.storage.from("photos").createSignedUrl(r.storage_path, PHOTO_LINK_SECONDS).then(({ data }) => {
        if (data && data.signedUrl) { fullUrls.set(r.storage_path, data.signedUrl); if (rows[k] === r) draw(); }
      });
    }
  };
  const step = d => { if (rows.length > 1) { k = (k + d + rows.length) % rows.length; editing = false; message = ""; draw(); } };

  box.addEventListener("click", async e => {
    const b = e.target.closest("[data-v]");
    if (!b) return;
    const r = rows[k];
    switch (b.dataset.v) {
      case "close": close(); break;
      case "prev": step(-1); break;
      case "next": step(1); break;
      case "caption": editing = true; message = ""; draw(); break;
      case "caption-cancel": editing = false; draw(); break;
      case "remove":
        b.disabled = true;
        try { await opsPhotoRemove(r.id); close(); onChange("removed", r); }
        catch (err) { message = err.message; draw(); }
        break;
    }
  });
  box.addEventListener("submit", async e => {
    e.preventDefault();
    const r = rows[k];
    const caption = String(new FormData(e.target).get("caption") || "").trim();
    try { await opsPhotoCaption(r.id, caption); r.caption = caption || null; editing = false; message = ""; onChange("caption", r); }
    catch (err) { message = err.message; }
    draw();
  });
  let x0 = null;
  box.addEventListener("touchstart", e => { x0 = e.touches.length === 1 ? e.touches[0].clientX : null; }, { passive: true });
  box.addEventListener("touchend", e => {
    if (x0 === null || editing) return;
    const dx = e.changedTouches[0].clientX - x0;
    if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1);
    x0 = null;
  });
  draw();
}
