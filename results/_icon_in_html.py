"""One question, one answer: does the `:material/name:` shorthand expand inside raw HTML?

`st.markdown` turns that shorthand into an inline SVG that inherits the text colour. Whether it
does so for text that sits *inside* a raw HTML block decides how the citation cards can be built —
if it expands, the cards can be one HTML grid, which is the only way to get them to line up as a
grid; if it does not, the cards have to stay as Streamlit containers.

    APP_FILE=results/_icon_in_html.py PORT=8504 results/with-app.sh \
      node results/shot-url.mjs
"""
import streamlit as st

st.write("### :material/description: markdown shorthand (control)")
st.markdown(":material/description: :material/confirmation_number: :material/chat_bubble: "
            ":material/folder: — shorthand in plain markdown")

st.write("### Inside raw HTML")
st.markdown(
    '<div class="probe">'
    '<p>shorthand in raw html: :material/description: :material/confirmation_number: '
    ':material/chat_bubble: :material/folder:</p>'
    '<p>span: <span>:material/description:</span></p>'
    '<p>strong: <strong>:material/confirmation_number:</strong></p>'
    '<p>caption-ish: <span style="color:#6A706C;font-size:0.82rem">:material/folder: small</span></p>'
    '</div>',
    unsafe_allow_html=True,
)
st.markdown("### Control right after, same page")
st.caption(":material/description: caption shorthand")
