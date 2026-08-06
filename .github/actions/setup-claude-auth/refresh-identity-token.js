// Rewrites the GitHub OIDC identity token file every few minutes so Claude
// CLI processes spawned late in the job exchange a still-valid token.
// Spawned detached by action.yml; the runner reaps it when the job ends.

const fs = require("fs");

const AUDIENCE = "https://api.anthropic.com";
const REFRESH_INTERVAL_MS = 4 * 60 * 1000;

const tokenFile = process.argv[2];
const requestUrl = process.env.ACTIONS_ID_TOKEN_REQUEST_URL;
const requestToken = process.env.ACTIONS_ID_TOKEN_REQUEST_TOKEN;

async function refresh() {
  const response = await fetch(
    `${requestUrl}&audience=${encodeURIComponent(AUDIENCE)}`,
    { headers: { Authorization: `Bearer ${requestToken}` } },
  );
  if (!response.ok) {
    throw new Error(`identity token request failed: HTTP ${response.status}`);
  }
  const { value } = await response.json();
  // Truncate in place rather than rename-replace so a bind mount of the file
  // (the Docker e2e job) keeps seeing updates.
  fs.writeFileSync(tokenFile, value, { mode: 0o600 });
}

setInterval(() => {
  // Keep the previous (possibly still valid) token on transient failures.
  refresh().catch(() => {});
}, REFRESH_INTERVAL_MS);
