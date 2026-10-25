// Customer photos (ops-photos.js, db/schema.sql): members only, add/view/caption,
// Remove only hides (a theft photo is evidence), and pages that load the script
// in the right order and keep working without it.

process.chdir(require("path").join(__dirname, ".."));

const fs = require("fs");

let fails = 0;
const ok = (cond, msg) => {
  if (!cond) { fails++; console.log("  FAIL " + msg); } else console.log("  ok   " + msg);
};

const code = ["dashboard-utils.js", "ops-photos.js"].map(f => fs.readFileSync(f, "utf8")).join("\n");
const make = db => new Function("opsDb", "document", code + `
  return { opsPhotosOn, opsPhotoStripHtml, opsPhotosFor, opsThumbPath, opsPhotoByline, opsPhotos };`)(db, {});

// ── Signed out: nothing shows ──
const out = make(null);
ok(!out.opsPhotosOn(), "signed out, photos are off");
ok(out.opsPhotoStripHtml(5) === "", "signed out, My Route's strip is empty");

// ── Signed in: thumbnails, the Add tile, captions escaped ──
const on = make({ person: { name: "Sarge" }, user: { id: "u1" }, client: { storage: {} } });
ok(on.opsPhotosOn(), "signed in as a member, photos are on");
ok(on.opsThumbPath("12/abc.jpg") === "12/abc_t.jpg", "the thumbnail sits beside the full photo as _t.jpg");
ok(on.opsPhotoStripHtml(12) === "", "no photos: My Route shows no strip");
ok(/data-photo-add/.test(on.opsPhotoStripHtml(12, { add: true })), "no photos: the customer page still offers Add photo");
on.opsPhotos.byCustomer.set(12, [
  { id: 7, customer_id: 12, storage_path: "12/abc.jpg", caption: "Lock <b>on</b> lid", uploaded_by: "u1", uploaded_at: "2026-10-20T15:00:00Z" }]);
on.opsPhotos.urls.set("12/abc_t.jpg", "https://example.test/t?token=1");
on.opsPhotos.people.set("u1", "Jim");
const strip = on.opsPhotoStripHtml(12, { add: true });
ok(/data-photo="7"/.test(strip) && strip.includes("https://example.test/t?token=1"), "a photo shows as its signed thumbnail");
ok(!strip.includes("<b>") && strip.includes("&lt;b&gt;"), "captions are escaped");
ok(!/ops-photo-cap/.test(on.opsPhotoStripHtml(12)), "My Route's strip leaves captions to the full view");
ok(on.opsPhotoByline(on.opsPhotosFor(12)[0]) === "Added by Jim · Oct 20, 2026", "the full view says who added it and when");

// ── The database: Remove hides, nothing deletes, the bucket takes small JPEGs only ──
const schema = fs.readFileSync("db/schema.sql", "utf8");
const revoke = schema.indexOf("revoke update, delete on public.photos from authenticated");
const grant = schema.indexOf("grant update (caption, removed_at, removed_by) on public.photos to authenticated");
ok(revoke > 0 && grant > revoke, "the caption/Remove grant comes after the revoke that would cancel it");
ok(!/grant[^;]*delete[^;]*public\.photos/i.test(schema) && !/on storage\.objects\s+for delete/i.test(schema),
  "no one can delete a photo or its file through the site");
ok(/file_size_limit = 2097152, allowed_mime_types = array\['image\/jpeg'\]/.test(schema), "the bucket takes JPEGs up to 2 MB");
ok(/is\("removed_at", null\)/.test(fs.readFileSync("ops-photos.js", "utf8")), "removed photos are never loaded");

// ── Pages: loaded after ops-db.js, and guarded for a page paired with an older script set ──
["ops-customer.html", "ops-myroute.html"].forEach(f => {
  const html = fs.readFileSync(f, "utf8");
  const db = html.indexOf('<script src="ops-db.js">'), ph = html.indexOf('<script src="ops-photos.js">');
  ok(db > 0 && ph > db, `${f} loads ops-photos.js after ops-db.js`);
  ok(/const photosReady = typeof opsPhoto(StripHtml|Pick) === "function"/.test(html), `${f} keeps working without ops-photos.js`);
});

console.log(fails ? `\n${fails} FAILURE(S)` : "\nALL CHECKS PASSED");
process.exit(fails ? 1 : 0);
