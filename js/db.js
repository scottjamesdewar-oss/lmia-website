// Shared SQLite (sql.js / WASM) loader. Fetches data/site.sqlite once and
// hands back a queryable database object. Both pages use this so the file
// is only downloaded and parsed a single time per session (cached on the
// window object - a full page navigation still re-fetches, but the browser
// HTTP cache keeps that cheap after the first load).

async function loadDatabase(onProgress) {
  if (window.__dbPromise) return window.__dbPromise;

  window.__dbPromise = (async () => {
    const SQL = await initSqlJs({
      locateFile: (file) => `https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/${file}`,
    });

    const resp = await fetch("data/site.sqlite");
    const total = Number(resp.headers.get("content-length")) || 0;
    const reader = resp.body.getReader();
    const chunks = [];
    let received = 0;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      received += value.length;
      if (onProgress) onProgress(received, total);
    }

    const buffer = new Uint8Array(received);
    let offset = 0;
    for (const chunk of chunks) {
      buffer.set(chunk, offset);
      offset += chunk.length;
    }

    return new SQL.Database(buffer);
  })();

  return window.__dbPromise;
}

function likePattern(raw) {
  // sql.js's build has no FTS5 module, so search uses plain SQL LIKE.
  // Escape LIKE wildcards in the user's input so they're matched literally.
  const escaped = raw.trim().replace(/[%_\\]/g, (c) => `\\${c}`);
  return `%${escaped}%`;
}
