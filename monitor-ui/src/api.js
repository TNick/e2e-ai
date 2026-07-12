// Thin fetch adapters for the monitor API. Parsing lives here so components
// stay declarative and the adapters can be unit-tested in isolation.

async function get(path) {
  const res = await fetch("/api" + path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  health: () => get("/health"),
  summary: () => get("/summary"),
  shards: () => get("/shards").then((d) => d.items),
  runs: (limit = 100) => get(`/runs?limit=${limit}`),
  run: (id) => get(`/runs/${encodeURIComponent(id)}`),
  tests: () => get("/tests").then((d) => d.items),
  test: (id) => get(`/tests/${encodeURIComponent(id)}`),
  agents: () => get("/agents").then((d) => d.items),
  commands: () => get("/commands").then((d) => d.items),
  commandRuns: () => get("/command-runs").then((d) => d.items),
  commandRun: (id) => get(`/command-runs/${encodeURIComponent(id)}`),
  commandOutput: (id) => get(`/command-runs/${encodeURIComponent(id)}/output`),
  startCommand: async (id, options) => {
    const res = await fetch(`/api/commands/${encodeURIComponent(id)}/runs`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ options }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },
};

// Convert a command schema + form values into the options payload the API
// expects (mirrors the server-side argv builder's field handling).
export function formValuesToOptions(command, values) {
  const options = {};
  for (const opt of command.options) {
    const v = values[opt.name];
    if (opt.type === "boolean" || opt.type === "toggle") options[opt.name] = !!v;
    else if (opt.type === "repeatable_path") {
      const parts = String(v || "")
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      if (parts.length) options[opt.name] = parts;
    } else if (v !== undefined && v !== "") {
      options[opt.name] = opt.type === "integer" ? Number(v) : v;
    }
  }
  return options;
}
