(function () {
  const SESSION_KEY = "ai-data-middleware.session";

  function readSession() {
    try {
      const raw = window.localStorage.getItem(SESSION_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function writeSession(session) {
    window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
    window.dispatchEvent(new CustomEvent("middleware-auth-changed", { detail: session }));
    return session;
  }

  function clearSession() {
    window.localStorage.removeItem(SESSION_KEY);
    window.dispatchEvent(new CustomEvent("middleware-auth-changed", { detail: null }));
  }

  function buildHeaders(headers = {}, session = null) {
    const next = new Headers(headers);
    if (session?.access_token) {
      next.set("Authorization", `Bearer ${session.access_token}`);
    }
    return next;
  }

  async function request(path, options = {}, attemptRefresh = true) {
    const session = readSession();
    const config = { ...options };
    config.headers = buildHeaders(options.headers, session);

    if (options.json !== undefined) {
      config.headers.set("Content-Type", "application/json");
      config.body = JSON.stringify(options.json);
    }

    const response = await fetch(path, config);
    if (response.status !== 401 || !attemptRefresh || !session?.refresh_token || path === "/auth/refresh") {
      return response;
    }

    try {
      await refreshSession(session.refresh_token);
    } catch {
      clearSession();
      return response;
    }

    return request(path, options, false);
  }

  async function readJsonResponse(response) {
    try {
      return await response.json();
    } catch {
      return null;
    }
  }

  async function parseResult(response) {
    return { response, data: await readJsonResponse(response) };
  }

  function errorMessage(data, fallback) {
    if (!data) return fallback;
    return data.error || data.detail || data.message || fallback;
  }

  async function signUp(payload) {
    const { response, data } = await parseResult(
      await request("/auth/signup", { method: "POST", json: payload }, false)
    );
    if (!response.ok || !data?.success || !data.session) {
      throw new Error(errorMessage(data, "Sign up failed."));
    }
    writeSession(data.session);
    return data.session;
  }

  async function signIn(payload) {
    const { response, data } = await parseResult(
      await request("/auth/login", { method: "POST", json: payload }, false)
    );
    if (!response.ok || !data?.success || !data.session) {
      throw new Error(errorMessage(data, "Login failed."));
    }
    writeSession(data.session);
    return data.session;
  }

  async function refreshSession(refreshToken) {
    const { response, data } = await parseResult(
      await request("/auth/refresh", {
        method: "POST",
        json: { refresh_token: refreshToken },
      }, false)
    );
    if (!response.ok || !data?.success || !data.session) {
      throw new Error(errorMessage(data, "Session refresh failed."));
    }
    writeSession(data.session);
    return data.session;
  }

  async function loadCurrentUser() {
    const { response, data } = await parseResult(await request("/auth/me"));
    if (!response.ok || !data?.authenticated || !data.user) {
      clearSession();
      return null;
    }

    const session = readSession() || {};
    const next = { ...session, user: data.user };
    writeSession(next);
    return data.user;
  }

  async function requireAuth(redirectTo = "/") {
    const user = await loadCurrentUser();
    if (user) return user;
    window.location.replace(redirectTo);
    return null;
  }

  async function signOut() {
    try {
      await request("/auth/logout", { method: "POST" }, false);
    } catch {
      // Best-effort logout. Local state is still cleared.
    } finally {
      clearSession();
    }
  }

  window.middlewareAuth = {
    getSession: readSession,
    setSession: writeSession,
    clearSession,
    request,
    parseResult,
    signUp,
    signIn,
    refreshSession,
    loadCurrentUser,
    requireAuth,
    signOut,
  };
})();
