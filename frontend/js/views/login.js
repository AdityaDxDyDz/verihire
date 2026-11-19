import { h, mount } from "../dom.js";
import { logoMark } from "../icons.js";
import { button, banner, field } from "../components/ui.js";
import { renderText } from "../components/evidence.js";
import { AuthError } from "../auth.js";

const SAMPLE = "Built and maintained a billing module using FastAPI, serving 42k users.";

export function loginView(ctx) {
  const mode = ctx.auth.mode;
  let tab = "signin";
  let busy = false;
  let message = null; // {tone, text}
  const formHost = h("div", { class: "login-form" });
  const start = SAMPLE.indexOf("FastAPI");

  async function run(task) {
    if (busy) return;
    busy = true;
    message = null;
    draw();
    try {
      const out = await task();
      if (out?.needsConfirmation) message = { tone: "info", text: "Account created. Check your inbox for a confirmation link, then sign in." };
      else if (ctx.auth.isAuthed()) {
        ctx.afterSignIn();
        return;
      }
    } catch (err) {
      message = { tone: "bad", text: err instanceof AuthError ? err.message : "Something went wrong. Try again." };
    }
    busy = false;
    draw();
  }

  function draw() {
    const email = h("input", { class: "input", id: "email", type: "email", autocomplete: "email", required: true, placeholder: "you@example.com", "aria-describedby": "login-msg" });
    const password = h("input", { class: "input", id: "password", type: "password", autocomplete: tab === "signin" ? "current-password" : "new-password", placeholder: tab === "signin" ? "Your password" : "At least 6 characters" });
    const msg = h("div", { id: "login-msg", "aria-live": "polite" }, message ? banner(message.tone, message.text) : null);
    let controls;
    if (mode === "mock") {
      controls = [
        h("h2", null, "Try the demo workspace"),
        h("p", { class: "muted" }, "You are looking at sample data. Nothing you do here is uploaded or saved."),
        button({ label: "Open demo workspace", variant: "primary", onClick: () => run(async () => ctx.auth.signInMock()) }),
      ];
    } else if (mode === "dev") {
      controls = [
        h("h2", null, "Sign in"),
        field({ label: "Email", id: "email", control: email, hint: "Development sign-in: no password needed." }),
        msg,
        button({ label: busy ? "Signing in" : "Continue", type: "submit", variant: "primary", disabled: busy }),
      ];
    } else {
      controls = [
        h("div", { class: "tabs", role: "tablist", "aria-label": "Account" },
          h("button", { role: "tab", type: "button", "aria-selected": String(tab === "signin"), onClick: () => { tab = "signin"; message = null; draw(); } }, "Sign in"),
          h("button", { role: "tab", type: "button", "aria-selected": String(tab === "signup"), onClick: () => { tab = "signup"; message = null; draw(); } }, "Create account")),
        field({ label: "Email", id: "email", control: email }),
        field({ label: "Password", id: "password", control: password }),
        msg,
        button({ label: busy ? "Please wait" : tab === "signin" ? "Sign in" : "Create account", type: "submit", variant: "primary", disabled: busy }),
      ];
    }
    const form = h(
      "form",
      {
        class: "stack", style: { gap: "16px" }, novalidate: true,
        onSubmit: (e) => {
          e.preventDefault();
          if (mode === "dev") run(async () => ctx.auth.signInDev(email.value));
          else if (mode === "supabase") run(() => (tab === "signin" ? ctx.auth.signInPassword(email.value, password.value) : ctx.auth.signUp(email.value, password.value)));
        },
      },
      controls,
    );
    mount(formHost, form);
    if (!busy) (mode === "mock" ? form.querySelector("button") : email).focus?.();
  }

  const el = h(
    "div",
    { class: "login-wrap" },
    h(
      "div",
      { class: "login" },
      h(
        "div",
        { class: "login-pitch" },
        h("div", { class: "brand", style: { pointerEvents: "none" } }, logoMark(30), h("span", { class: "brand-word" }, "VeriHire")),
        h("div", { class: "stack", style: { gap: "16px" } },
          h("h1", null, "Shortlist with evidence you can check."),
          h("p", { class: "muted" }, "Rank resumes against a job description, then see the exact lines of each resume behind every skill."),
          h("div", { class: "sample", "aria-label": "Example of highlighted evidence" }, renderText(SAMPLE, [{ start: start, end: start + 7 }], { sweep: true }))),
        h("p", { class: "small muted" }, "Names and contact details stay hidden until you choose to reveal them."),
      ),
      formHost,
    ),
  );
  draw();
  return { el, title: "Sign in" };
}
