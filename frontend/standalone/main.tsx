import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import { configureApi } from "../src";
import { App } from "./App";
import { tokenStore } from "./auth";
import "./shell.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

configureApi({
  getToken: tokenStore.get,
  onUnauthorized: () => {
    tokenStore.clear();
    queryClient.clear();
    window.dispatchEvent(new Event("pra:logout"));
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
);
