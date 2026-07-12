import React, { useEffect, useState } from "react";
import {
  AppBar, Box, Chip, CssBaseline, Drawer, IconButton, List, ListItemButton,
  ListItemIcon, ListItemText, Toolbar, Typography, Table, TableBody, TableCell,
  TableHead, TableRow, Card, CardContent, Grid, Button, Dialog, DialogTitle,
  DialogContent, DialogActions, TextField, Checkbox, FormControlLabel,
  MenuItem, Link, Tooltip,
} from "@mui/material";
import MenuIcon from "@mui/icons-material/Menu";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import SettingsIcon from "@mui/icons-material/Settings";
import ListAltIcon from "@mui/icons-material/ListAlt";
import BugReportIcon from "@mui/icons-material/BugReport";
import TerminalIcon from "@mui/icons-material/Terminal";
import CheckIcon from "@mui/icons-material/Check";
import SmartToyIcon from "@mui/icons-material/SmartToy";
import { api, formValuesToOptions } from "./api.js";

const NAV = [
  ["active", "Active", <PlayArrowIcon />], ["commands", "Commands", <TerminalIcon />],
  ["runs", "Runs", <ListAltIcon />], ["tests", "Tests", <CheckIcon />],
  ["failures", "Failures", <BugReportIcon />], ["agents", "Agents", <SmartToyIcon />],
  ["settings", "Settings", <SettingsIcon />],
];
const local = (t) => (t ? new Date(t).toLocaleString() : "—");

export default function App() {
  const [view, setView] = useState("active");
  const [open, setOpen] = useState(localStorage.getItem("nav") !== "closed");
  const [health, setHealth] = useState(null);

  useEffect(() => { api.health().then(setHealth).catch(() => setHealth(null)); }, []);
  const toggle = () => { const n = !open; setOpen(n); localStorage.setItem("nav", n ? "open" : "closed"); };

  const width = open ? 200 : 56;
  return (
    <Box sx={{ display: "flex" }}>
      <CssBaseline />
      <AppBar position="fixed" sx={{ zIndex: 1201 }}>
        <Toolbar variant="dense">
          <IconButton color="inherit" edge="start" onClick={toggle}><MenuIcon /></IconButton>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mr: 2 }}>e2e-ai monitor</Typography>
          <Typography variant="body2" sx={{ opacity: 0.7 }}>{health?.monitor?.project_id || "(no project)"}</Typography>
          <Box sx={{ flex: 1 }} />
          <Tooltip title={health?.message || ""}>
            <Chip size="small" label={health?.ok ? "connected" : "check db"} color={health?.ok ? "success" : "warning"} />
          </Tooltip>
        </Toolbar>
      </AppBar>
      <Drawer variant="permanent" sx={{ width, "& .MuiDrawer-paper": { width, overflowX: "hidden" } }}>
        <Toolbar variant="dense" />
        <List dense>
          {NAV.map(([id, label, icon]) => (
            <ListItemButton key={id} selected={view === id} onClick={() => setView(id)}>
              <ListItemIcon sx={{ minWidth: 36 }}>{icon}</ListItemIcon>
              {open && <ListItemText primary={label} />}
            </ListItemButton>
          ))}
        </List>
      </Drawer>
      <Box component="main" sx={{ flexGrow: 1, p: 2 }}>
        <Toolbar variant="dense" />
        <View view={view} info={health?.monitor} />
      </Box>
    </Box>
  );
}

function useAsync(fn, deps = []) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { let live = true; fn().then((d) => live && setData(d)).catch((e) => live && setError(e)); return () => { live = false; }; }, deps);
  return { data, error };
}

function View({ view, info }) {
  if (view === "active") return <Active />;
  if (view === "commands") return <Commands info={info} />;
  if (view === "runs") return <Runs />;
  if (view === "tests") return <Tests />;
  if (view === "failures") return <Failures />;
  if (view === "agents") return <Agents />;
  return <Settings info={info} />;
}

function Active() {
  const summary = useAsync(() => api.summary(), []);
  const shards = useAsync(() => api.shards(), []);
  const c = summary.data?.counts || {};
  return (
    <>
      <Typography variant="h6" gutterBottom>Active</Typography>
      <Grid container spacing={1}>
        {[["Runnable", c.runnable], ["Runs", c.runs], ["Attempts", c.attempts],
          ["Active", summary.data?.active_attempts], ["Failures", c.failures]].map(([k, v]) => (
          <Grid item key={k}><Card sx={{ minWidth: 130 }}><CardContent>
            <Typography variant="caption" color="text.secondary">{k}</Typography>
            <Typography variant="h5">{v ?? 0}</Typography>
          </CardContent></Card></Grid>
        ))}
      </Grid>
      <Typography variant="h6" sx={{ mt: 2 }} gutterBottom>Live shards / runners</Typography>
      {!shards.data?.length && <Typography color="text.secondary">No active attempts.</Typography>}
      <Grid container spacing={1}>
        {(shards.data || []).map((s) => {
          const a = s.attempts[0] || {};
          return (
            <Grid item key={s.label}><Card sx={{ minWidth: 260 }}><CardContent>
              <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
                <Typography variant="subtitle2">{s.label}</Typography>
                <Chip size="small" label={s.database_name || "no-db"} />
              </Box>
              <Typography variant="body2">{a.title || "—"}</Typography>
              <Typography variant="caption" color="text.secondary">
                attempt {a.attempt_index} · {s.attempts.length} attempt(s)
              </Typography>
              <Box>{s.frontend_url && <Link href={s.frontend_url} target="_blank">frontend</Link>}{" "}
                {s.backend_url && <Link href={s.backend_url} target="_blank">backend</Link>}</Box>
            </CardContent></Card></Grid>
          );
        })}
      </Grid>
    </>
  );
}

function Runs() {
  const { data } = useAsync(() => api.runs(), []);
  return (
    <>
      <Typography variant="h6" gutterBottom>Runs</Typography>
      <Table size="small"><TableHead><TableRow>
        <TableCell>Started</TableCell><TableCell>Status</TableCell>
        <TableCell>Attempts</TableCell><TableCell>Reason</TableCell></TableRow></TableHead>
        <TableBody>{(data?.items || []).map((r) => (
          <TableRow key={r.id}><TableCell>{local(r.started_at)}</TableCell>
            <TableCell>{r.status}</TableCell><TableCell>{r.attempt_count}</TableCell>
            <TableCell>{r.reason || ""}</TableCell></TableRow>
        ))}</TableBody></Table>
    </>
  );
}

function Tests() {
  const { data } = useAsync(() => api.tests(), []);
  return (
    <>
      <Typography variant="h6" gutterBottom>Tests</Typography>
      <Table size="small"><TableHead><TableRow>
        <TableCell>Status</TableCell><TableCell>Spec</TableCell>
        <TableCell>Title</TableCell><TableCell>Project</TableCell></TableRow></TableHead>
        <TableBody>{(data || []).map((t) => (
          <TableRow key={t.id}><TableCell>{t.last_status || "unknown"}</TableCell>
            <TableCell>{t.spec_file}</TableCell><TableCell>{t.title}</TableCell>
            <TableCell>{t.project_name || ""}</TableCell></TableRow>
        ))}</TableBody></Table>
    </>
  );
}

function Failures() {
  const { data } = useAsync(async () => {
    const runs = await api.runs(20);
    const out = [];
    for (const r of runs.items.slice(0, 10)) {
      const full = await api.run(r.id);
      full.failures.forEach((f) => out.push(f));
    }
    return out;
  }, []);
  return (
    <>
      <Typography variant="h6" gutterBottom>Recent failures</Typography>
      <Table size="small"><TableHead><TableRow>
        <TableCell>When</TableCell><TableCell>Signature</TableCell>
        <TableCell>Message</TableCell></TableRow></TableHead>
        <TableBody>{(data || []).map((f) => (
          <TableRow key={f.id}><TableCell>{local(f.created_at)}</TableCell>
            <TableCell>{f.signature}</TableCell>
            <TableCell>{(f.error_message || "").split("\n")[0]}</TableCell></TableRow>
        ))}</TableBody></Table>
    </>
  );
}

function Agents() {
  const { data } = useAsync(() => api.agents(), []);
  return (
    <>
      <Typography variant="h6" gutterBottom>Agent invocations</Typography>
      <Table size="small"><TableHead><TableRow>
        <TableCell>Started</TableCell><TableCell>Role</TableCell>
        <TableCell>Agent</TableCell><TableCell>Status</TableCell></TableRow></TableHead>
        <TableBody>{(data || []).map((g) => (
          <TableRow key={g.id}><TableCell>{local(g.started_at)}</TableCell>
            <TableCell>{g.role}</TableCell><TableCell>{g.agent_id}</TableCell>
            <TableCell>{g.status}</TableCell></TableRow>
        ))}</TableBody></Table>
    </>
  );
}

function Commands() {
  const cmds = useAsync(() => api.commands(), []);
  const runs = useAsync(() => api.commandRuns(), []);
  const [selected, setSelected] = useState(null);
  return (
    <>
      <Typography variant="h6" gutterBottom>Commands</Typography>
      <Grid container spacing={1}>
        {(cmds.data || []).map((c) => (
          <Grid item key={c.id}><Card sx={{ minWidth: 240 }}><CardContent>
            <Box sx={{ display: "flex", gap: 1, alignItems: "center" }}>
              <Typography variant="subtitle2">{c.label}</Typography>
              {c.destructive && <Chip size="small" color="warning" label="destructive" />}
            </Box>
            <Typography variant="caption" color="text.secondary">
              e2e-ai {c.argv_prefix.join(" ")}
            </Typography>
            <Box sx={{ mt: 1 }}><Button size="small" variant="outlined" onClick={() => setSelected(c)}>Options…</Button></Box>
          </CardContent></Card></Grid>
        ))}
      </Grid>
      <Typography variant="h6" sx={{ mt: 2 }} gutterBottom>Monitor-launched runs</Typography>
      <Table size="small"><TableHead><TableRow>
        <TableCell>Started</TableCell><TableCell>Command</TableCell>
        <TableCell>Status</TableCell><TableCell>Exit</TableCell></TableRow></TableHead>
        <TableBody>{(runs.data || []).map((r) => (
          <TableRow key={r.command_run_id}><TableCell>{local(r.started_at)}</TableCell>
            <TableCell>{r.command_id}</TableCell><TableCell>{r.status}</TableCell>
            <TableCell>{r.exit_code ?? ""}</TableCell></TableRow>
        ))}</TableBody></Table>
      {selected && <OptionsDialog command={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function OptionsDialog({ command, onClose }) {
  const [values, setValues] = useState(() =>
    Object.fromEntries(command.options.map((o) => [o.name, o.default ?? ""])));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (name, v) => setValues((s) => ({ ...s, [name]: v }));

  const submit = async () => {
    setBusy(true); setError("");
    try {
      await api.startCommand(command.id, formValuesToOptions(command, values));
      onClose();
    } catch (e) { setError(e.message); setBusy(false); }
  };

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Options: {command.label}</DialogTitle>
      <DialogContent>
        {command.options.map((o) => (
          <Box key={o.name} sx={{ my: 1 }}>
            {(o.type === "boolean" || o.type === "toggle") ? (
              <FormControlLabel control={
                <Checkbox checked={!!values[o.name]} onChange={(e) => set(o.name, e.target.checked)} />
              } label={`${o.name} — ${o.help}`} />
            ) : o.type === "choice" ? (
              <TextField select fullWidth size="small" label={o.name} value={values[o.name] ?? ""}
                helperText={o.help} onChange={(e) => set(o.name, e.target.value)}>
                {o.choices.map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}
              </TextField>
            ) : (
              <TextField fullWidth size="small" label={`${o.name} (${o.type})`}
                type={o.type === "integer" ? "number" : "text"} value={values[o.name] ?? ""}
                helperText={o.help} onChange={(e) => set(o.name, e.target.value)} />
            )}
          </Box>
        ))}
        {error && <Typography color="error" variant="body2">{error}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={busy} onClick={submit}>Start</Button>
      </DialogActions>
    </Dialog>
  );
}

function Settings({ info }) {
  return (
    <>
      <Typography variant="h6" gutterBottom>Settings</Typography>
      <Card><CardContent>
        <Typography variant="body2">Project: {info?.project_id || "(none)"}</Typography>
        <Typography variant="body2">Project root: {info?.project_root}</Typography>
        <Typography variant="body2">State DB: {info?.db_path}</Typography>
        <Typography variant="body2">Bind: {info?.host}:{info?.port}</Typography>
        <Typography variant="body2">Refresh: {info?.refresh_ms} ms</Typography>
        <Typography variant="caption" color="text.secondary">
          Read-only monitor. The state database is never written.
        </Typography>
      </CardContent></Card>
    </>
  );
}
