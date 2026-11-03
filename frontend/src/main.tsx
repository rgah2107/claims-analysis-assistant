/** Mount the workspace. There is one screen and no router. */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";

const root = document.getElementById("root");
if (!root) {
  throw new Error("missing root element");
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
