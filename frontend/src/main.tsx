import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { getInitialTheme } from "./lib/theme";

// Apply persisted theme before first paint to avoid a flash.
document.documentElement.classList.toggle("light", getInitialTheme() === "light");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
