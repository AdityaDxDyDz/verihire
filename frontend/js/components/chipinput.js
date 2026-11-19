// Editable skill list: type, press Enter or comma to add; Backspace on empty input removes the last chip.
import { h, mount, nextId } from "../dom.js";
import { icon } from "../icons.js";
import { displaySkill } from "../format.js";
import { addSkills, removeSkill } from "../validate.js";

export function chipInput({ label, values = [], onChange, placeholder = "Type a skill and press Enter", id = nextId("chips") }) {
  let list = [...values];
  const input = h("input", { id, type: "text", placeholder, "aria-label": label, autocomplete: "off" });
  const box = h("div", { class: "chip-input" });
  const commit = () => {
    if (!input.value.trim()) return;
    list = addSkills(list, input.value);
    input.value = "";
    changed();
  };
  function changed() { draw(); onChange?.([...list]); input.focus(); }
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === ",") { e.preventDefault(); commit(); }
    else if (e.key === "Backspace" && !input.value && list.length) { list = list.slice(0, -1); changed(); }
  });
  input.addEventListener("blur", () => { if (input.value.trim()) { list = addSkills(list, input.value); input.value = ""; draw(); onChange?.([...list]); } });
  input.addEventListener("paste", (e) => {
    const t = e.clipboardData?.getData("text") ?? "";
    if (/[,\n]/.test(t)) { e.preventDefault(); list = addSkills(list, t); changed(); }
  });
  function draw() {
    mount(box, list.map((s) => h("span", { class: "chip outline" }, displaySkill(s),
      h("button", { type: "button", "aria-label": `Remove ${displaySkill(s)}`, style: { all: "unset", cursor: "pointer", display: "inline-flex" }, onClick: () => { list = removeSkill(list, s); changed(); } }, icon("x", { size: 12 })))), input);
  }
  draw();
  box.addEventListener("click", (e) => { if (e.target === box) input.focus(); });
  return { el: box, id, get value() { return [...list]; }, set(v) { list = [...v]; draw(); } };
}
