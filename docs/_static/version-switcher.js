(() => {
  "use strict";

  const VERSION_PREFIX_RE = /^v\d+\.\d+/;

  const isVersionSegment = (segment) =>
    segment === "dev" || segment === "main" || VERSION_PREFIX_RE.test(segment);

  const normalizeVersion = (value) => {
    if (!value) {
      return null;
    }
    return value.startsWith("v") ? value : `v${value}`;
  };

  const ensureTrailingSlash = (value) => (value.endsWith("/") ? value : `${value}/`);

  const detectPathInfo = () => {
    const pathname = window.location.pathname || "/";
    const parts = pathname.split("/").filter(Boolean);
    const versionIndex = parts.findIndex(isVersionSegment);
    if (versionIndex === -1) {
      return {
        basePath: ensureTrailingSlash(parts.length ? `/${parts[0]}` : "/"),
        version: null,
        relativePath: "",
      };
    }

    const baseParts = parts.slice(0, versionIndex);
    const basePath = ensureTrailingSlash(baseParts.length ? `/${baseParts.join("/")}` : "/");
    const relativePath = parts.slice(versionIndex + 1).join("/");
    return { basePath, version: parts[versionIndex], relativePath };
  };

  const buildTargetUrl = (versionRoot, relativePath) => {
    const baseUrl = new URL(versionRoot, window.location.origin);
    const targetUrl = new URL(relativePath || "", baseUrl);
    targetUrl.search = window.location.search;
    targetUrl.hash = window.location.hash;
    return { baseUrl, targetUrl };
  };

  const findMountPoint = () =>
    document.querySelector(".navbar-header-items__end") ||
    document.querySelector(".navbar-header-items") ||
    document.querySelector(".bd-header .bd-header__inner") ||
    document.body;

  const buildVersionSwitcher = (versions, latestVersion, currentVersion, basePath, relativePath) => {
    if (!versions.length) {
      return;
    }

    const container = document.createElement("div");
    container.className = "navbar-item warp-version-switcher";

    const label = document.createElement("label");
    label.className = "warp-version-label";
    label.setAttribute("for", "warp-version-select");
    label.textContent = "Version";

    const select = document.createElement("select");
    select.className = "warp-version-select";
    select.id = "warp-version-select";
    select.setAttribute("aria-label", "Documentation version");

    const versionMap = new Map();
    versions.forEach((entry) => {
      if (!entry || !entry.version) {
        return;
      }
      const path = entry.path || entry.url || `${entry.version}/`;
      versionMap.set(entry.version, ensureTrailingSlash(path));

      const option = document.createElement("option");
      option.value = entry.version;
      option.textContent =
        entry.version + (latestVersion && entry.version === latestVersion ? " (latest)" : "");
      select.appendChild(option);
    });

    const normalizedCurrent = normalizeVersion(currentVersion);
    if (normalizedCurrent && versionMap.has(normalizedCurrent)) {
      select.value = normalizedCurrent;
    } else if (latestVersion && versionMap.has(latestVersion)) {
      select.value = latestVersion;
    } else {
      select.selectedIndex = 0;
    }

    select.addEventListener("change", () => {
      const selectedVersion = select.value;
      const versionPath = versionMap.get(selectedVersion);
      if (!versionPath) {
        return;
      }

      const versionRoot = ensureTrailingSlash(`${basePath}${versionPath}`);
      const { baseUrl, targetUrl } = buildTargetUrl(versionRoot, relativePath);

      if (!relativePath) {
        window.location.href = baseUrl.toString();
        return;
      }

      fetch(targetUrl.toString(), { method: "HEAD" })
        .then((response) => {
          window.location.href = response.ok ? targetUrl.toString() : baseUrl.toString();
        })
        .catch(() => {
          window.location.href = baseUrl.toString();
        });
    });

    container.append(label, select);

    const mountPoint = findMountPoint();
    mountPoint.appendChild(container);
  };

  const init = () => {
    if (window.location.protocol === "file:") {
      return;
    }

    const { basePath, version, relativePath } = detectPathInfo();
    const docVersion = normalizeVersion(window.DOCUMENTATION_OPTIONS?.VERSION);
    const currentVersion = normalizeVersion(version) || docVersion;
    if (!currentVersion) {
      return;
    }

    const versionsUrl = `${basePath}versions.json`;
    fetch(versionsUrl)
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (!data || !Array.isArray(data.versions)) {
          return;
        }
        buildVersionSwitcher(
          data.versions,
          data.latest || null,
          currentVersion,
          basePath,
          relativePath
        );
      })
      .catch(() => {});
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
