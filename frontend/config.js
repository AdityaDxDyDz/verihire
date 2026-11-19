// Runtime configuration. In deployment this file is GENERATED from environment variables (see docs/DEPLOYMENT_HANDOFF.md);
// never put a service-role key here. The anon key and URL are public by design.
window.VERIHIRE_CONFIG = {
  mode: "dev",            // "dev" (token dev:<email>) | "supabase"
  apiBaseUrl: "",         // "" = same origin (FastAPI serves this folder)
  supabaseUrl: "",
  supabaseAnonKey: "",
};
