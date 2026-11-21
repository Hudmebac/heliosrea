(() => {
  document.querySelectorAll("[data-current-year]").forEach((el) => {
    el.textContent = String(new Date().getFullYear());
  });
  const forms = document.querySelectorAll("form.subscribe-form");
  forms.forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const result = form.querySelector(".form-result");
      const button = form.querySelector('button[type="submit"]');
      const email = (form.querySelector('[name="email"]') || {}).value || "";
      const name = (form.querySelector('[name="name"]') || {}).value || "";
      const topics = Array.from(form.querySelectorAll('[name="topics"]:checked')).map((item) => item.value);
      const consent = (form.querySelector('[name="consent"]') || {}).checked === true;
      const donated = (form.querySelector('[name="donated"]') || {}).checked === true;
      const owner = form.dataset.ownerEmail || "";
      const subject = encodeURIComponent("Field notes request");
      const body = encodeURIComponent("Please add me to the monthly field notes.\n\nEmail: " + email + "\nName: " + name + "\n");
      const fallback = "mailto:" + owner + "?subject=" + subject + "&body=" + body;
      const showFallback = (message) => {
        result.className = "form-result err";
        result.textContent = message + " Email " + owner + " directly: ";
        const link = document.createElement("a");
        link.href = fallback;
        link.textContent = owner;
        result.appendChild(link);
        result.appendChild(document.createTextNode("."));
      };
      if (!email.trim()) { result.className = "form-result err"; result.textContent = "Enter an email address."; return; }
      if (!topics.length) { result.className = "form-result err"; result.textContent = "Choose at least one update type."; return; }
      if (!consent) { result.className = "form-result err"; result.textContent = "Tick the consent box before requesting the notes."; return; }
      if (button) { button.disabled = true; button.textContent = "Saving…"; }
      try {
        const response = await fetch("/api/newsletter/subscribe", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: email.trim(), name: name.trim(), topics: topics,
            consent: true, donated: donated, source: form.dataset.source || "field-notes"
          })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || "the request could not be saved");
        form.reset();
        result.className = "form-result ok";
        result.textContent = data.message || "Request saved. The owner can now see it in the Mailroom.";
        if (data.mailto) {
          const link = document.createElement("a");
          link.href = data.mailto;
          link.textContent = " Send a copy by email";
          result.appendChild(link);
        }
      } catch (error) {
        showFallback("The local form could not save this request.");
      } finally {
        if (button) { button.disabled = false; button.textContent = "Request the field notes"; }
      }
    });
  });

  // -- evidence reader ------------------------------------------------------
  document.querySelectorAll("[data-evidence-reader]").forEach((reader) => {
    const tabs = Array.from(reader.querySelectorAll("[data-evidence-tab]"));
    const panels = Array.from(reader.querySelectorAll("[data-evidence-panel]"));
    const select = (name) => {
      tabs.forEach((tab) => {
        const on = tab.dataset.evidenceTab === name;
        tab.setAttribute("aria-selected", on ? "true" : "false");
        tab.tabIndex = on ? 0 : -1;
      });
      panels.forEach((panel) => { panel.hidden = panel.dataset.evidencePanel !== name; });
    };
    tabs.forEach((tab, index) => {
      tab.addEventListener("click", () => select(tab.dataset.evidenceTab));
      tab.addEventListener("keydown", (event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
        tabs[next].focus();
        select(tabs[next].dataset.evidenceTab);
      });
    });
  });

  // -- local search ---------------------------------------------------------
  const searchPanel = document.getElementById("search-panel");
  if (searchPanel) {
    const searchInput = document.getElementById("search-input");
    const searchResults = document.getElementById("search-results");
    const root = document.documentElement.dataset.root || "";
    let searchIndex = null;
    let searchReturnFocus = null;
    const openSearch = () => {
      searchReturnFocus = document.activeElement;
      searchPanel.hidden = false;
      document.body.style.overflow = "hidden";
      searchInput.value = "";
      renderSearch("");
      searchInput.focus();
    };
    const closeSearch = () => {
      searchPanel.hidden = true;
      document.body.style.overflow = "";
      if (searchReturnFocus) searchReturnFocus.focus();
    };
    const loadSearch = async () => {
      if (searchIndex) return searchIndex;
      const response = await fetch(root + "assets/search-index.json");
      if (!response.ok) throw new Error("search index unavailable");
      searchIndex = await response.json();
      return searchIndex;
    };
    const renderSearch = async (query) => {
      searchResults.textContent = "";
      const clean = query.trim().toLowerCase();
      if (!clean) {
        const note = document.createElement("p");
        note.className = "search-empty";
        note.textContent = "Type a term, or press Escape to close.";
        searchResults.appendChild(note);
        return;
      }
      let items = [];
      try { items = await loadSearch(); } catch (error) {
        const note = document.createElement("p");
        note.className = "search-empty";
        note.textContent = "The search index could not load. Open Start here instead.";
        searchResults.appendChild(note);
        return;
      }
      const terms = clean.split(/\s+/).filter(Boolean);
      const hits = items.filter((item) => {
        const haystack = (item.title + " " + item.description + " " + item.type).toLowerCase();
        return terms.every((term) => haystack.includes(term));
      }).slice(0, 14);
      if (!hits.length) {
        const note = document.createElement("p");
        note.className = "search-empty";
        note.textContent = "No match. Try locator, seam, redaction, digest or glossary.";
        searchResults.appendChild(note);
        return;
      }
      hits.forEach((item) => {
        const link = document.createElement("a");
        link.className = "search-result";
        link.href = root + item.url;
        const title = document.createElement("b");
        title.textContent = item.title;
        const type = document.createElement("span");
        type.textContent = item.type;
        const description = document.createElement("p");
        description.textContent = item.description;
        link.append(type, title, description);
        searchResults.appendChild(link);
      });
    };
    document.querySelectorAll("[data-search-open]").forEach((button) => button.addEventListener("click", openSearch));
    document.querySelectorAll("[data-search-close]").forEach((button) => button.addEventListener("click", closeSearch));
    searchPanel.addEventListener("click", (event) => { if (event.target === searchPanel) closeSearch(); });
    searchInput.addEventListener("input", () => renderSearch(searchInput.value));
    document.addEventListener("keydown", (event) => {
      const tag = (document.activeElement && document.activeElement.tagName) || "";
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || document.activeElement.isContentEditable;
      if (event.key === "/" && !typing && searchPanel.hidden) { event.preventDefault(); openSearch(); }
      if (event.key === "Escape" && !searchPanel.hidden) closeSearch();
    });
  }
})();
