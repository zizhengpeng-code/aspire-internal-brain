"""The boundary-weight comparison that decided `--control-edge`.

Kept as a permanent artifact because it is the evidence for a design decision, not a throwaway.
`--hairline` (#E4E7E5) resolves to 1.25:1 against paper — correct for a card edge or a table rule,
which only has to separate, and too faint for the outline that tells you a button is a button. The
question was how much heavier the interactive boundary had to get, and that is a judgement call, so
it was made by looking at the three candidates side by side rather than by arguing about it.

Only #8D8D8D reaches 3:1 against paper; the value that shipped is #868B88, the lightest neutral of
the same hue that clears 3:1 on **both** surfaces controls sit on (3.46:1 paper, 3.15:1 field).

    results/with-app.sh ... APP_FILE=results/boundary-compare.py node results/shot-url.mjs ...
"""
import streamlit as st

st.set_page_config(page_title="boundary weight", layout="centered")

INK, PAPER, FIELD, GREEN = "#131313", "#FFFFFF", "#F4F4F4", "#02D169"

st.markdown(f"""
<style>
  .stApp {{ color: {INK}; }}
  .opt {{ border-radius: 14px; padding: 18px; margin: 0 0 18px; background: {PAPER}; }}
  .opt h4 {{ margin: 0 0 2px; font-size: 15px; font-weight: 700; }}
  .opt .ratio {{ color: #6A706C; font-size: 12.5px; margin: 0 0 14px; }}
  .opt.chosen h4::after {{ content: "  ✓ 采用"; color: #1F7A45; font-size: 12.5px; }}
  .demo {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
  .ctl {{ border-radius: 12px; padding: 9px 14px; font-size: 15px; background: {PAPER};
          min-width: 190px; }}
  .ctl.on-field {{ background: {FIELD}; }}
  .btn {{ border-radius: 12px; padding: 9px 16px; font-size: 15px; font-weight: 600;
          background: {PAPER}; cursor: pointer; min-width: 150px; text-align: center; }}
  .btn.pri {{ background: {GREEN}; color: {INK}; }}
</style>
""", unsafe_allow_html=True)

st.markdown("## Control boundary weight")
st.caption("Same shapes, four boundary values — the three that were compared, and the one that "
           "shipped. Buttons use the hairline too, so the whole control language moves together.")

for name, colour, ratio, note, chosen in [
    ("A — the hairline", "#E4E7E5", "1.25:1", "what shipped before; below the 3:1 non-text floor", False),
    ("B — a mid grey", "#B4BAB6", "1.94:1", "reads as a control, still under the floor", False),
    ("C — lightest grey reaching 3:1 on paper", "#8D8D8D", "3.00:1", "crisp, but only just on paper", False),
    ("D — what shipped", "#868B88", "3.46:1 / 3.15:1",
     "3:1 on paper <b>and</b> on the grey field — the constraint that picked it", True),
]:
    st.markdown(f"""
    <div class="opt{' chosen' if chosen else ''}">
      <h4>{name} &nbsp;<code>{colour}</code></h4>
      <p class="ratio">{ratio} — {note}</p>
      <div class="demo">
        <div class="btn" style="border:1px solid {colour}">Clear this chat</div>
        <div class="btn pri" style="border:1px solid {INK}">Continue</div>
        <div class="ctl" style="border:1px solid {colour}; color:#6A706C">Ask about the knowledge base…</div>
        <div class="ctl on-field" style="border:1px solid {colour}">grace.lim@lumenpay.io</div>
      </div>
    </div>
    """, unsafe_allow_html=True)

st.markdown("---")
st.caption("One property changes: the `border-color` of interactive controls. Card edges, dividers "
           "and the table grid keep the light hairline, so the extra weight lands only on the "
           "things you can press.")
