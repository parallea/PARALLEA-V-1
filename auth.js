/* Parallea auth UI controller (vanilla, no build step). */
(function () {
  const ParalleaAuth = {};

  async function fetchJson(url, opts) {
    const res = await fetch(url, Object.assign({ credentials: "same-origin", headers: { "Content-Type": "application/json" } }, opts));
    let body = null;
    try { body = await res.json(); } catch (_) { /* ignore */ }
    return { ok: res.ok, status: res.status, body: body || {} };
  }

  function setError(msg) {
    const el = document.getElementById("errorMsg");
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  function setLoading(form, on) {
    const btn = form.querySelector("button[type=submit]");
    if (!btn) return;
    btn.classList.toggle("loading", !!on);
    btn.disabled = !!on;
  }

  function bindPasswordToggles(root) {
    root.querySelectorAll(".pw-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const name = btn.getAttribute("data-toggle");
        const input = root.querySelector(`input[name="${name}"]`);
        if (!input) return;
        const isPw = input.type === "password";
        input.type = isPw ? "text" : "password";
        btn.textContent = isPw ? "hide" : "show";
        btn.setAttribute("aria-label", isPw ? "Hide password" : "Show password");
      });
    });
  }

  async function setupGoogleButton() {
    const btn = document.getElementById("googleBtn");
    const divider = document.getElementById("divider");
    if (!btn) return;
    try {
      const r = await fetchJson("/api/auth/providers");
      if (r.body && r.body.google) {
        btn.hidden = false;
        if (divider) divider.hidden = false;
        btn.addEventListener("click", () => {
          window.location.href = "/auth/google";
        });
      }
    } catch (_) { /* leave hidden if probe fails */ }
  }

  function readForm(form) {
    const data = {};
    new FormData(form).forEach((v, k) => { data[k] = typeof v === "string" ? v.trim() : v; });
    return data;
  }

  async function handleLogin(form) {
    const data = readForm(form);
    if (!data.email || !data.password) {
      setError("Email and password are required.");
      return;
    }
    setError(null); setLoading(form, true);
    const r = await fetchJson("/api/auth/login", { method: "POST", body: JSON.stringify(data) });
    setLoading(form, false);
    if (!r.ok) {
      setError(r.body.detail || "Couldn't sign you in.");
      return;
    }
    window.location.href = r.body.redirect || "/";
  }

  async function handleSignup(form) {
    const data = readForm(form);
    if (!data.role) { setError("Please choose teacher or student."); return; }
    if (!data.name) { setError("Name is required."); return; }
    if (!data.email) { setError("Email is required."); return; }
    if (!data.password || data.password.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (data.password !== data.confirm_password) { setError("Passwords do not match."); return; }
    setError(null); setLoading(form, true);
    const r = await fetchJson("/api/auth/signup", { method: "POST", body: JSON.stringify(data) });
    setLoading(form, false);
    if (!r.ok) {
      setError(r.body.detail || "Couldn't create your account.");
      return;
    }
    window.location.href = r.body.redirect || "/";
  }

  async function handleRoleSelection(form) {
    const data = readForm(form);
    if (!data.role) { setError("Please pick a role."); return; }
    setError(null); setLoading(form, true);
    const r = await fetchJson("/api/auth/role-selection", { method: "POST", body: JSON.stringify(data) });
    setLoading(form, false);
    if (!r.ok) {
      setError(r.body.detail || "Couldn't save your choice.");
      return;
    }
    window.location.href = r.body.redirect || "/";
  }

  ParalleaAuth.init = function (opts) {
    const mode = (opts && opts.mode) || "login";
    bindPasswordToggles(document);
    setupGoogleButton();
    const formEl =
      mode === "login" ? document.getElementById("loginForm") :
      mode === "signup" ? document.getElementById("signupForm") :
      document.getElementById("roleForm");
    if (!formEl) return;
    formEl.addEventListener("submit", (ev) => {
      ev.preventDefault();
      if (mode === "login") return handleLogin(formEl);
      if (mode === "signup") return handleSignup(formEl);
      return handleRoleSelection(formEl);
    });
  };

  window.ParalleaAuth = ParalleaAuth;
})();
