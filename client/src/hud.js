// Leng HUD page: no rendering logic. The Rust side owns capture, the
// pipeline, the payload and the content fragment (leng_core::render, shown
// through the `render_hud` command); this page only delivers the raw
// `HudPayload` JSON string to it and inserts the returned fragment. The page
// starts as a spinner.
const tauri = window.__TAURI__;

const els = {
  loading: document.getElementById("loading"),
  content: document.getElementById("content"),
};

// One render path for every delivery (live `leng:payload` event AND the
// `leng:ready` replay on the Rust side): nothing here must branch on
// payload contents.
function deliver(rawPayload) {
  tauri.core
    .invoke("render_hud", { payloadJson: rawPayload })
    .then((html) => {
      els.loading.hidden = true;
      els.content.innerHTML = html;
    })
    .catch((err) => {
      // Command rejected (invalid payload JSON, parse error): show it as
      // plain text — the page itself must not trust the payload.
      els.loading.hidden = true;
      els.content.textContent = `invalid payload: ${err}`;
    });
}

tauri.event.listen("leng:payload", (event) => {
  try {
    deliver(JSON.stringify(JSON.parse(event.payload)));
  } catch (err) {
    deliver(JSON.stringify({ type: "error", message: `invalid payload: ${err}` }));
  }
});

// The macOS HUD window never takes key focus (focusable=false), so its
// dismissal (Escape / click outside) is handled by the Rust-side global
// monitor, not from this page.

tauri.event.emit("leng:ready", {});
