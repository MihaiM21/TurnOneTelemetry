/* Shared behaviour for the admin console. No framework, no build step —
 * matching the design intent stated in src/api/routers/admin_ui.py.
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------------ toasts */
  function toast(message, kind) {
    var host = document.getElementById("toasts");
    if (!host) {
      host = document.createElement("div");
      host.id = "toasts";
      document.body.appendChild(host);
    }
    var el = document.createElement("div");
    el.className = "toast " + (kind || "info");
    el.textContent = message;
    host.appendChild(el);
    setTimeout(function () { el.remove(); }, kind === "err" ? 7000 : 4000);
  }

  /* --------------------------------------------------- destructive confirmation
   * Revoke / promote / delete / purge were all bare submits with no confirm
   * step. Any element carrying data-confirm now has to be acknowledged.
   */
  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    var msg = form.getAttribute && form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) {
      ev.preventDefault();
    }
  });

  document.addEventListener("click", function (ev) {
    var el = ev.target.closest && ev.target.closest("[data-confirm-click]");
    if (el && !window.confirm(el.getAttribute("data-confirm-click"))) {
      ev.preventDefault();
      ev.stopPropagation();
    }
  });

  /* ------------------------------------------------------------ sortable tables
   * Opt in with <table data-sortable>. Numeric columns sort numerically when
   * every cell parses as a number, otherwise it falls back to text.
   */
  function cellValue(row, index) {
    var cell = row.children[index];
    return cell ? (cell.getAttribute("data-sort") || cell.textContent).trim() : "";
  }

  function makeSortable(table) {
    var head = table.tHead;
    if (!head || !head.rows.length) return;
    Array.prototype.forEach.call(head.rows[0].cells, function (th, index) {
      if (th.hasAttribute("data-nosort")) return;
      th.classList.add("sortable");
      th.addEventListener("click", function () {
        var body = table.tBodies[0];
        if (!body) return;
        var rows = Array.prototype.slice.call(body.rows);
        var asc = !th.classList.contains("asc");

        Array.prototype.forEach.call(head.rows[0].cells, function (other) {
          other.classList.remove("asc", "desc");
        });
        th.classList.add(asc ? "asc" : "desc");

        var numeric = rows.every(function (row) {
          var raw = cellValue(row, index).replace(/[%,\s]/g, "");
          return raw === "" || raw === "—" || !isNaN(parseFloat(raw));
        });

        rows.sort(function (a, b) {
          var x = cellValue(a, index);
          var y = cellValue(b, index);
          if (numeric) {
            var nx = parseFloat(x.replace(/[%,\s]/g, "")) || 0;
            var ny = parseFloat(y.replace(/[%,\s]/g, "")) || 0;
            return asc ? nx - ny : ny - nx;
          }
          return asc ? x.localeCompare(y) : y.localeCompare(x);
        });
        rows.forEach(function (row) { body.appendChild(row); });
      });
    });
  }

  /* ------------------------------------------------------------------ helpers */
  function humanBytes(bytes) {
    if (!bytes) return "0 B";
    var units = ["B", "KB", "MB", "GB", "TB"];
    var i = Math.floor(Math.log(bytes) / Math.log(1024));
    i = Math.min(i, units.length - 1);
    return (bytes / Math.pow(1024, i)).toFixed(i ? 1 : 0) + " " + units[i];
  }

  function humanDuration(seconds) {
    if (seconds == null || !isFinite(seconds) || seconds < 0) return "—";
    seconds = Math.round(seconds);
    if (seconds < 60) return seconds + "s";
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    if (m < 60) return m + "m " + s + "s";
    var h = Math.floor(m / 60);
    return h + "h " + (m % 60) + "m";
  }

  /* POST helper that surfaces failures as toasts rather than silence. */
  async function postJSON(url, body) {
    var response = await fetch(url, {
      method: "POST",
      body: body,
      headers: { "Accept": "application/json" },
    });
    var payload = null;
    try { payload = await response.json(); } catch (e) { /* empty body is fine */ }
    if (!response.ok) {
      var detail = (payload && (payload.detail || payload.message)) || ("HTTP " + response.status);
      if (typeof detail === "object") detail = detail.message || JSON.stringify(detail);
      throw new Error(detail);
    }
    return payload;
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("table[data-sortable]").forEach(makeSortable);
  });

  window.T1 = {
    toast: toast,
    humanBytes: humanBytes,
    humanDuration: humanDuration,
    postJSON: postJSON,
  };
})();
