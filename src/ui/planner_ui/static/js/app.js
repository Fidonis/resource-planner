// Behaviour of the pages. No inline script, no eval: templates only carry data-* attributes and
// this file reacts to them.
(function () {
  "use strict";

  var root = document.documentElement;
  var wide = window.matchMedia("(min-width: 1024px)");

  function store(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (e) {
      /* storage can be blocked */
    }
  }

  // ── Sidebar ───────────────────────────────────────────────────────────────────────────────
  // pinned: full width; collapsed: icon rail (hover peeks it open); open: transient drawer below
  // lg. Only the first two are remembered, so a phone session never overwrites the desktop choice.
  function toggleSidebar() {
    var current = root.getAttribute("data-sidebar");
    if (wide.matches) {
      var next = current === "pinned" ? "collapsed" : "pinned";
      root.setAttribute("data-sidebar", next);
      store("rp-sidebar", next);
    } else {
      root.setAttribute("data-sidebar", current === "open" ? "collapsed" : "open");
    }
  }

  function closeDrawer() {
    if (root.getAttribute("data-sidebar") === "open") root.setAttribute("data-sidebar", "collapsed");
  }

  wide.addEventListener("change", function () {
    var saved = null;
    try {
      saved = localStorage.getItem("rp-sidebar");
    } catch (e) {
      /* ignore */
    }
    root.setAttribute("data-sidebar", saved === "collapsed" ? "collapsed" : "pinned");
  });

  // ── Theme ─────────────────────────────────────────────────────────────────────────────────
  function toggleTheme() {
    var next = root.getAttribute("data-theme") === "fidonis-dark" ? "fidonis-light" : "fidonis-dark";
    root.setAttribute("data-theme", next);
    store("rp-theme", next);
    syncThemeButtons();
  }

  function syncThemeButtons() {
    var dark = root.getAttribute("data-theme") === "fidonis-dark";
    document.querySelectorAll("[data-action='toggle-theme']").forEach(function (button) {
      button.setAttribute("aria-pressed", dark ? "true" : "false");
      button.querySelectorAll("[data-theme-icon]").forEach(function (icon) {
        icon.classList.toggle("hidden", icon.getAttribute("data-theme-icon") !== (dark ? "dark" : "light"));
      });
      button.querySelectorAll("[data-theme-label]").forEach(function (label) {
        label.classList.toggle("hidden", label.getAttribute("data-theme-label") !== (dark ? "dark" : "light"));
      });
    });
  }

  // ── Toasts ────────────────────────────────────────────────────────────────────────────────
  function showToast(kind, message) {
    var box = document.getElementById("toasts");
    if (!box || !message) return;
    var alert = document.createElement("div");
    alert.className = "alert alert-" + kind + " shadow-lg text-sm";
    alert.setAttribute("role", kind === "error" || kind === "warning" ? "alert" : "status");
    var text = document.createElement("span");
    text.textContent = message;
    alert.appendChild(text);
    box.appendChild(alert);
    window.setTimeout(function () {
      alert.remove();
    }, kind === "error" ? 10000 : 5000);
  }

  // ── Dialog ────────────────────────────────────────────────────────────────────────────────
  function dialog() {
    return document.getElementById("modal");
  }

  function openDialog() {
    var d = dialog();
    if (d && !d.open) d.showModal();
  }

  function closeDialog() {
    var d = dialog();
    if (d && d.open) d.close();
  }

  // A value that is shown only once (a generated password, a new token): select it and copy it.
  function copySecret(button) {
    var input = document.getElementById(button.getAttribute("data-copy-from"));
    if (!input) return;
    input.focus();
    input.select();
    var done = function () {
      showToast("success", document.body.getAttribute("data-msg-copied") || "Copied.");
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(input.value).then(done, function () {
        /* the value stays selected, so Ctrl+C still works */
      });
    } else if (document.execCommand && document.execCommand("copy")) {
      done();
    }
  }

  // A row of a repeating form (the role mapping) that the user does not want any more.
  function removeRow(button) {
    var row = button.closest("[data-row]");
    if (row) row.remove();
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest && event.target.closest("[data-action]");
    if (!target) {
      if (!event.target.closest || !event.target.closest(".app-sidebar")) closeDrawer();
      return;
    }
    switch (target.getAttribute("data-action")) {
      case "toggle-sidebar":
        toggleSidebar();
        break;
      case "toggle-theme":
        toggleTheme();
        break;
      case "close-modal":
        closeDialog();
        break;
      case "back":
        window.history.back();
        break;
      case "copy-secret":
        copySecret(target);
        break;
      case "remove-row":
        removeRow(target);
        break;
      default:
        break;
    }
  });

  document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail && event.detail.target;
    // An answer that swaps nothing (HX-Reswap: none) leaves the old form in the dialog, which the
    // answer's own modalClose event has just closed: do not open it again.
    var xhr = event.detail && event.detail.xhr;
    if (xhr && xhr.getResponseHeader("HX-Reswap") === "none") return;
    if (target && target.id === "modal-body") {
      openDialog();
      var first = target.querySelector("[autofocus], input:not([type=hidden]), select, textarea");
      if (first) first.focus();
    }
  });

  document.addEventListener("htmx:afterSettle", syncThemeButtons);

  // HX-Trigger events of the server.
  document.addEventListener("toast", function (event) {
    var detail = event.detail || {};
    showToast(detail.kind || "info", detail.message);
  });
  document.addEventListener("modalClose", closeDialog);
  // The list behind a dialog changed but the dialog stays open (it shows a secret): draw it again.
  document.addEventListener("refreshList", function () {
    if (!window.htmx || !document.getElementById("list")) return;
    window.htmx.ajax("GET", window.location.pathname + window.location.search, {
      target: "#list",
      swap: "outerHTML",
    });
  });

  document.addEventListener("close", function (event) {
    if (event.target && event.target.id === "modal") {
      var body = document.getElementById("modal-body");
      if (body) body.textContent = "";
    }
  }, true);

  // A request that never got an answer (network, proxy): say so, the server could not.
  function unreachable() {
    var message = document.body.getAttribute("data-msg-unreachable");
    showToast("error", message || "The server cannot be reached.");
  }
  document.addEventListener("htmx:sendError", unreachable);
  document.addEventListener("htmx:timeout", unreachable);

  document.addEventListener("DOMContentLoaded", syncThemeButtons);

  // ── Planning board: drag and drop ─────────────────────────────────────────────────────────
  // A skill of a task is dragged from the pool onto a member's week, an assignment chip onto
  // another member or week. A cell takes an item only if the member has its skill. The drop is
  // only a request: the server books it and answers with the board, drawn again.
  function skillsOf(cell) {
    return (cell.getAttribute("data-skills") || "").split(",");
  }

  function boardValues(board) {
    return {
      from: board.getAttribute("data-from") || "",
      weeks: board.getAttribute("data-weeks") || "",
      team: board.getAttribute("data-team") || "",
      filter_skill: board.getAttribute("data-skill") || "",
    };
  }

  function markTargets(board, skillId) {
    board.querySelectorAll("[data-drop]").forEach(function (cell) {
      var square = cell.closest("td");
      if (!square) return;
      if (skillId === null) {
        square.removeAttribute("data-target");
      } else {
        square.setAttribute("data-target", skillsOf(cell).indexOf(skillId) >= 0 ? "valid" : "invalid");
      }
    });
    if (skillId === null) board.removeAttribute("data-dragging");
    else board.setAttribute("data-dragging", "");
  }

  function redrawBoard() {
    var board = document.getElementById("board");
    if (!board || !window.htmx) return;
    board.removeAttribute("aria-busy");
    window.htmx.ajax("GET", window.location.pathname + window.location.search, {
      target: "#board",
      swap: "outerHTML",
    });
  }

  function sendDrop(board, verb, path, values) {
    var all = boardValues(board);
    Object.keys(values).forEach(function (key) {
      all[key] = values[key];
    });
    board.setAttribute("aria-busy", "true");
    window.htmx.ajax(verb, path, { source: board, target: board, swap: "outerHTML", values: all });
  }

  function pointOf(original) {
    var source = original && original.changedTouches ? original.changedTouches[0] : original;
    return source && typeof source.clientX === "number" ? { x: source.clientX, y: source.clientY } : null;
  }

  // The week under the pointer when the item was let go. The list the item was last shown in is
  // not used: an item that passed over a week on its way is not dropped there.
  function targetOf(event) {
    var point = pointOf(event.originalEvent);
    var under = point ? document.elementFromPoint(point.x, point.y) : null;
    var square = under && under.closest ? under.closest("td[data-week]") : null;
    return square ? square.querySelector("[data-drop]") : null;
  }

  // The item goes back to where it came from; nothing was requested.
  function putBack(event) {
    var item = event.item;
    if (item.parentNode === event.from) return;
    if (item.getAttribute("data-assignment")) {
      event.from.insertBefore(item, event.from.children[event.oldIndex] || null);
    } else {
      item.remove();
    }
  }

  function finished(event) {
    var board = document.getElementById("board");
    if (!board) return;
    markTargets(board, null);
    var item = event.item;
    var cell = targetOf(event);
    var skill = item.getAttribute("data-skill-id");
    if (!cell || cell === event.from || skillsOf(cell).indexOf(skill) < 0) {
      putBack(event);
      return;
    }
    var place = { member: cell.getAttribute("data-member-id"), week: cell.getAttribute("data-week") };
    var chip = item.getAttribute("data-assignment");
    if (chip) {
      place.version = item.getAttribute("data-version") || "";
      sendDrop(board, "PUT", "/planning/assignments/" + encodeURIComponent(chip) + "/move", place);
    } else {
      // the copy that was dragged out of the pool is not part of the board
      place.task = item.getAttribute("data-task-id");
      place.skill = skill;
      item.remove();
      sendDrop(board, "POST", "/planning/drops", place);
    }
  }

  function setUpBoard() {
    var board = document.getElementById("board");
    if (!board || !window.Sortable || !board.hasAttribute("data-drag-enabled")) return;
    var shared = {
      forceFallback: true,
      fallbackOnBody: true,
      fallbackTolerance: 4,
      animation: 0,
      sort: false,
      ghostClass: "drag-ghost",
      onStart: function (event) {
        markTargets(board, event.item.getAttribute("data-skill-id"));
      },
      onEnd: finished,
    };
    board.querySelectorAll("[data-pool-list]").forEach(function (list) {
      if (list.getAttribute("data-sortable")) return;
      list.setAttribute("data-sortable", "pool");
      window.Sortable.create(
        list,
        Object.assign({}, shared, {
          group: { name: "plan", pull: "clone", put: false },
          draggable: "[data-drag]",
        })
      );
    });
    board.querySelectorAll("[data-drop]").forEach(function (cell) {
      if (cell.getAttribute("data-sortable")) return;
      cell.setAttribute("data-sortable", "cell");
      window.Sortable.create(
        cell,
        Object.assign({}, shared, {
          group: {
            name: "plan",
            pull: true,
            put: function (to, from, item) {
              return skillsOf(to.el).indexOf(item.getAttribute("data-skill-id")) >= 0;
            },
          },
          draggable: "[data-assignment]",
        })
      );
    });
  }

  document.addEventListener("htmx:load", setUpBoard);
  document.addEventListener("DOMContentLoaded", setUpBoard);
  // A board request that failed: the board shows what the drop left behind, so draw it again.
  ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach(function (name) {
    document.addEventListener(name, function (event) {
      var source = event.detail && event.detail.elt;
      if (source && source.id === "board") redrawBoard();
    });
  });

  // ── Alpine components (CSP build: names only, no expressions in the markup) ──────────────
  document.addEventListener("alpine:init", function () {
    // A colour input and a text input that show the same value.
    window.Alpine.data("colorField", function () {
      return {
        init: function () {
          var picker = this.$refs.picker;
          var text = this.$refs.text;
          picker.addEventListener("input", function () {
            text.value = picker.value;
          });
          text.addEventListener("input", function () {
            if (/^#[0-9a-fA-F]{6}$/.test(text.value)) picker.value = text.value;
          });
        },
      };
    });
  });
})();
