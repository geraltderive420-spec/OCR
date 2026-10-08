import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { ConfirmProvider, ToastProvider } from "./components/common";
import { UploadProvider } from "./uploads/UploadContext";
import "./styles.css";

// Thème choisi appliqué avant le premier rendu (pas de flash clair → sombre).
try {
  const th = localStorage.getItem("ocr-theme");
  if (th === "light" || th === "dark") document.documentElement.setAttribute("data-theme", th);
} catch {
  /* stockage indisponible */
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false, staleTime: 5_000 } },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <ConfirmProvider>
            <AuthProvider>
              <UploadProvider>
                <App />
              </UploadProvider>
            </AuthProvider>
          </ConfirmProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
