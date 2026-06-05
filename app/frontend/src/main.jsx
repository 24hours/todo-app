import React from "react";
import ReactDOM from "react-dom/client";
import * as Sentry from "@sentry/react";
import App from "./App.jsx";
import "./index.css";

// DSN is a public client key (safe to ship in the browser bundle).
// Override per-environment with VITE_SENTRY_DSN if needed.
Sentry.init({
  dsn:
    import.meta.env.VITE_SENTRY_DSN ||
    "https://c8820039f64493ced9f58fca2551ba94@o64703.ingest.us.sentry.io/4511510329688064",
  environment: import.meta.env.MODE,
  integrations: [Sentry.browserTracingIntegration()],
  // Performance tracing. Lower this in production (e.g. 0.1).
  tracesSampleRate: 1.0,
});

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <Sentry.ErrorBoundary fallback={<p className="error">Something went wrong.</p>}>
      <App />
    </Sentry.ErrorBoundary>
  </React.StrictMode>
);
