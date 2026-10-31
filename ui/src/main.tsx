import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "@fontsource-variable/bricolage-grotesque";
import "@fontsource/atkinson-hyperlegible-next/400.css";
import "@fontsource/atkinson-hyperlegible-next/600.css";
import "@fontsource/atkinson-hyperlegible-next/700.css";
import "@fontsource-variable/jetbrains-mono";
import "./styles.css";

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
