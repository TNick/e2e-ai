import React from "react";
import { createRoot } from "react-dom/client";
import CssBaseline from "@mui/material/CssBaseline";
import { ThemeProvider, createTheme } from "@mui/material/styles";
import App from "./App.jsx";

const theme = createTheme({
  palette: { mode: "dark", primary: { main: "#4c8bf5" } },
  components: {
    // Dense, table-first operations tool.
    MuiTableCell: { styleOverrides: { root: { paddingTop: 4, paddingBottom: 4 } } },
  },
});

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <App />
    </ThemeProvider>
  </React.StrictMode>,
);
