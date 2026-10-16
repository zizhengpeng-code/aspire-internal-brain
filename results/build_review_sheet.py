"""Build the before/after review sheet for the brand pass.

    python results/build_review_sheet.py

Writes `results/ui/review.html` with every screenshot inlined as a data URI, so the sheet is one
portable file — it renders from a file:// path, from the preview panel, or attached to a message,
without needing the PNGs to travel beside it. The figures in the table are the computed styles the
probe actually read out of the DOM (`results/diff_probe.py` prints the same numbers, 37 of them).
"""
import base64
import pathlib

UI = pathlib.Path(__file__).resolve().parent / "ui"
BRAND = pathlib.Path(__file__).resolve().parent.parent / "static" / "brand"
OUT = UI / "review.html"

# The sidebar measures ~300px wide at the 1440px viewport the walk shoots at, so this crop keeps
# the masthead and the notice without dragging in the main column. Cropping rather than shooting
# a second screenshot is what makes the two sides of the dialog pair strictly comparable: same
# page, same viewport, same moment in the flow — only the notice itself differs.
#
# Both sources have to come from the *same* walk as the pairs above. An earlier revision of this
# sheet cropped a `final-0-signin.png` shot taken before the notice became a dialog, so section 6
# showed the button while section 1 still showed the box it replaced.
SIDEBAR_CROP = (0, 0, 304, 424)

INK, PAPER, FIELD, GREEN, HAIRLINE, MUTED = (
    "#131313", "#FFFFFF", "#F4F4F4", "#02D169", "#E4E7E5", "#6A706C")

# (caption, before file, after file, note)
PAIRS = [
    ("登录 · 账号列表",
     "before-0-signin.png", "final-0-signin.png",
     "主区的排布没动：标题、说明、五行账号还是原来的位置。改的是重音色、行首的姓名缩写方块，"
     "以及上方新增的制版头。"),
    ("聊天 · 空态 → 输入态",
     "before-chat.png", "final-0a-typed.png",
     "同一个页面、同一个人。红色单选点变墨色，蓝色提示框变灰，发送箭头在输入后变品牌绿。"),
    ("Admin · 演示状态操作",
     "before-admin.png", "final-admin.png",
     "按钮的 8px 圆角改成 12px，描边从半透明墨色换成 #E4E7E5 细线，数字输入框变灰；"
     "审计表里引用编号的底色回到中性，没有被主题绿染成“成功”。"),
    ("Compliance · 审计检索",
     "before-compliance.png", "final-compliance.png",
     "表头变灰、网格线变淡，选中页签用墨色下划线，筛选框统一成灰底。"),
    ("ROI · 测算与假设",
     "before-roi.png", "final-roi.png",
     "滑杆与数字框都用墨色；指标数值加粗到 700，标签降为次级灰，代码片段用灰底墨字。"),
]

# 从 DOM 里真实读出来的计算样式，不是设计意图。左列是截图里的元素，右两列是前后的值。
TOKENS = [
    ("侧边栏底色", "background", "rgb(240, 242, 246)", "rgb(244, 244, 244)"),
    ("正文字色", "color", "rgb(49, 51, 63)", "rgb(19, 19, 19)"),
    ("st.info 提示框", "background", "rgba(28, 131, 255, 0.1)", "rgb(244, 244, 244)"),
    ("提示框文字", "color", "rgb(0, 84, 163)", "rgb(19, 19, 19)"),
    ("正文链接", "color", "rgb(0, 84, 163)", "rgb(19, 19, 19)"),
    ("助手头像", "background", "rgb(255, 164, 33)", "rgb(19, 19, 19)"),
    ("发送按钮（空输入时）", "background", "rgba(151, 166, 195, 0.15)", "rgb(244, 244, 244)"),
    ("按钮 / 折叠条圆角", "border-radius", "8px", "12px"),
    ("按钮描边", "border-color", "rgba(49, 51, 63, 0.2)", "rgb(228, 231, 229)"),
    ("说明文字（caption）", "color", "rgb(49, 51, 63)", "rgb(106, 112, 108)"),
    ("指标数值", "font-weight", "400", "700"),
    ("表格正文", "color", "rgb(49, 51, 63)", "rgb(19, 19, 19)"),
]

# The dialog's own numbers, read the same way: `results/_dlg_struct.mjs` printed the box tree the
# panel is cut from, `results/_dlg_text.mjs` printed the type inside it.
DLG_TOKENS = [
    ("弹窗遮罩", "background", "rgba(173, 173, 173, 0.25)", "rgba(19, 19, 19, 0.34)"),
    ("弹窗面板圆角", "border-radius", "24px", "18px"),
    ("弹窗标题字体", "font-family", "Source Sans", "Jost"),
    ("弹窗标题字重 / 字号", "font-weight / size", "600 / 24px", "600 / 24px（未改）"),
    ("弹窗正文", "font-family", "Source Sans", "Source Sans（未改）"),
]

# §4.8. Every "after" here was read out of the running app, not off the stylesheet — the numbers,
# the transition and the character counts all come from `results/type-probe.mjs`, and the contrast
# figures from WCAG relative luminance. Sources are named so the claims can be re-run.
DESIGN_TOKENS = [
    ("图标", "用 SVG，不用 emoji", "6 处 emoji（📄 🎫 💬 📁 ⚠️ 👥 ♻️）", "0 处，全部换成 Material 符号"),
    ("悬停过渡", "150–300ms，忌 0ms 突变", "all 0s（瞬变）", "颜色/描边/阴影 0.18s"),
    ("正文行宽", "45–75 字，理想 66", "76 字（42rem）", "64 字（37rem）"),
    ("控件描边", "非文本对比 ≥ 3:1", "#E4E7E5 = 1.25:1", "#868B88 = 3.46:1 / 3.15:1"),
    ("数字对齐", "数据场景用等宽数字", "比例数字", "tabular-nums"),
    ("登录行悬停", "绿只表示动作与选中", "#F2F7F4（淡绿）", "#F4F4F4（中性灰）"),
    ("正文行高", "1.45–1.6，无单位", "1.6", "1.6（未改）"),
    ("焦点环", "不得移除", "3.2px 墨色环", "3.2px 墨色环（未改）"),
]

# §4.8 第二轮。两条都是「同类物件要共线」这条最普通的排版规则，也都被量过之后才动：
# 头像那一行用 DOM 探针（layout-probe.mjs）量，卡片那一行用像素扫读者的截图量。
ALIGN_TOKENS = [
    ("头像", "方块与首行共中线", "低 3.2px（读者截图 3.7px）", "translateY(-3px) → −0.2px"),
    ("卡片等高", "同行卡片共用上下边", "233 / 198 / 233px", "233 / 233 / 233px"),
    ("卡片链接行", "同行动作行共线", "三处相差 39px", "spread 0px"),
    ("卡片时间戳", "不得在词中断行", "2026-10- / 05T09:00…", "2026-10-05（整行）"),
    ("头像内部", "标记要画在方块正中", "挤到右缘、被压成 12px 宽", "19×19，中心偏 ±0.12px"),
]


def data_uri(name):
    return _uri(UI / name)


def crop_sidebar(src, dst):
    """Cut the navigation column out of a full-page shot, so the two sides of the dialog pair are
    the same region of the same screen."""
    from PIL import Image
    Image.open(UI / src).convert("RGB").crop(SIDEBAR_CROP).save(UI / dst)
    return dst


def brand_uri(name):
    return _uri(BRAND / name)


def _uri(path):
    return f"data:image/png;base64,{base64.b64encode(path.read_bytes()).decode()}"


def figure(name, label, cls=""):
    return (f'      <figure class="{cls}">\n'
            f'        <img src="{data_uri(name)}" alt="{label}">\n'
            f'        <figcaption>{label}</figcaption>\n'
            f'      </figure>\n')


def detail(name, caption):
    return (f'  <div class="detail">\n'
            f'    <img src="{data_uri(name)}" alt="{caption}">\n'
            f'    <p class="note" style="margin-top:10px">{caption}</p>\n'
            f'  </div>\n')


def main():
    parts = [f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Aspire · Internal Brain — 品牌与视觉 before / after</title>
<style>
  :root {{ --ink:{INK}; --paper:{PAPER}; --field:{FIELD}; --green:{GREEN};
           --line:{HAIRLINE}; --muted:{MUTED}; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--paper); color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC",
                     "Microsoft YaHei",Helvetica,Arial,sans-serif;
         -webkit-font-smoothing:antialiased; line-height:1.55; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:48px 32px 72px; }}
  h1 {{ font-size:30px; font-weight:700; margin:0 0 10px; letter-spacing:-0.015em; }}
  .lede {{ color:var(--muted); font-size:15px; margin:0 0 6px; max-width:76ch; }}
  h2 {{ font-size:19px; font-weight:700; margin:46px 0 6px; }}
  h2 .n {{ display:inline-block; min-width:26px; color:var(--muted); font-weight:600; }}
  .note {{ color:var(--muted); font-size:13.5px; margin:0 0 18px; max-width:88ch; }}

  .split {{ display:flex; gap:14px; margin:28px 0 8px; flex-wrap:wrap; }}
  .chip {{ flex:1 1 280px; border:1px solid var(--line); border-radius:16px; padding:14px 16px; }}
  .chip h3 {{ font-size:13px; font-weight:700; margin:0 0 6px; }}
  .chip p {{ margin:0; font-size:13.5px; color:var(--muted); }}
  .swatch {{ display:inline-block; width:12px; height:12px; border-radius:4px;
             border:1px solid var(--line); vertical-align:-1px; margin-right:7px; }}

  .brandrow {{ display:flex; gap:18px; align-items:center; flex-wrap:wrap; margin:18px 0 4px;
              border:1px solid var(--line); border-radius:16px; padding:18px 20px; }}
  .brandrow img {{ height:38px; width:auto; display:block; }}
  .brandrow .vr {{ width:1px; align-self:stretch; background:var(--line); }}
  .brandrow .name {{ font-size:22px; font-weight:600; letter-spacing:-0.015em; }}
  .brandrow .small {{ font-size:12.5px; color:var(--muted); }}

  table {{ width:100%; border-collapse:collapse; font-size:13.5px; margin-top:6px; }}
  th, td {{ text-align:left; padding:8px 12px; border-bottom:1px solid var(--line); }}
  th {{ background:var(--field); font-weight:600; font-size:12.5px; }}
  td.prop {{ color:var(--muted); font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
             font-size:12.5px; white-space:nowrap; }}
  td.val {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12.5px; }}
  td.val.old {{ color:var(--muted); }}
  tbody tr:last-child th, tbody tr:last-child td {{ border-bottom:0; }}

  .pair {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; align-items:start; }}
  @media (max-width:820px) {{ .pair {{ grid-template-columns:1fr; }} }}
  /* For crops narrower than the two columns, so they are shown at native size instead of being
     stretched to fill and going soft. */
  .pair.narrow {{ grid-template-columns:repeat(2, minmax(0, {SIDEBAR_CROP[2]}px));
                  justify-content:center; gap:16px; }}
  @media (max-width:820px) {{ .pair.narrow {{ grid-template-columns:minmax(0, {SIDEBAR_CROP[2]}px); }} }}
  figure {{ margin:0; }}
  figure img {{ width:100%; display:block; border:1px solid var(--line); border-radius:14px;
               background:var(--field); }}
  figcaption {{ font-size:12px; color:var(--muted); margin-top:8px; }}
  figure.after figcaption {{ color:var(--ink); font-weight:600; }}

  .detail {{ margin-top:20px; }}
  .detail img {{ width:100%; display:block; border:1px solid var(--line); border-radius:14px; }}
  footer {{ margin-top:52px; padding-top:22px; border-top:1px solid var(--line);
            color:var(--muted); font-size:13px; }}
  footer p {{ max-width:88ch; }}
  code {{ background:var(--field); border-radius:6px; padding:1px 5px; font-size:12.5px; }}
</style>
</head>
<body>
<div class="wrap">

  <h1>Aspire · Internal Brain — 品牌、设计审计与对齐</h1>
  <p class="lede">三轮改动，每一条都用<b>运行中的应用</b>量过再拍下来，不是照着样式表描述。
     <b>①品牌落地</b>：登录页之后的那几页之前还是 Streamlit 默认皮肤（红色单选点、蓝色提示框、
     蓝色链接、橙色机器人）——把真正的 Aspire logo 装进产品、给产品名选了字、
     把两个屏幕收进同一套语言，并把侧边栏常驻的共享提示改成弹窗。
     <b>②设计审计</b>：两套公开设计规则跑了一遍，只采纳与本产品相符的部分
     （Minimalism / Swiss Style、图标用 SVG 不用 emoji、悬停过渡 180ms、正文 64 字、
     控件描边对比 ≥3:1）；面向营销落地页的那套——金色 + 紫色 + 深色底、GSAP 滚动动画——被否掉。
     <b>③对齐</b>：读者两次截图指出的问题，头像与首行文字、同一行的三张引用卡片，
     以及头像方<b>块里面</b>的那个标记：方块位置第一轮的探针量过、是齐的，
     但里面画的标记被图标包裹层挤到右缘并压缩，一个元素可以在正确的位置上却画着错的东西。</p>

  <div class="brandrow">
    <img src="{brand_uri('aspire-lockup.png')}" alt="Aspire">
    <div class="vr"></div>
    <div>
      <div class="name">Internal Brain</div>
      <div class="small">Jost 600 · 与 aspire 字标同一支几何无衬线（单层 a）</div>
    </div>
  </div>
  <p class="note">logo 不是排出来的，是位图母版拆出来的：<code>scripts/make_brand_assets.py</code>
     从白底上把 alpha 反解出来（<code>a=(255-R)/(255-2)</code>），所以缩放后边缘没有白边；
     品牌绿也是从同一张母版里采的 <code>#02D169</code>，不是手填的。</p>

  <div class="split">
    <div class="chip">
      <h3><span class="swatch" style="background:{FIELD}"></span>灰色 {FIELD} —— 只做「表面」</h3>
      <p>侧边栏、输入框、表格表头、提示框、提问气泡、姓名缩写方块。面积大的地方一律是灰的。</p>
    </div>
    <div class="chip">
      <h3><span class="swatch" style="background:{GREEN}"></span>品牌绿 {GREEN} —— 只做「动作、标记、选中」</h3>
      <p>logo、主按钮、按钮悬停、发送箭头、链接下划线、登录屏选中的那一行。刻意没有浅色版本。</p>
    </div>
  </div>

  <h2>真实读出来的样式差异</h2>
  <p class="note">下表不是设计说明，是把浏览器算出来的值原样抓下来对比，共 37 处不同，这里列主要 12 处。</p>
  <table>
    <thead><tr><th>元素</th><th>属性</th><th>之前</th><th>现在</th></tr></thead>
    <tbody>
"""]
    for label, prop, old, new in TOKENS:
        parts.append(f'      <tr><td>{label}</td><td class="prop">{prop}</td>'
                     f'<td class="val old">{old}</td><td class="val">{new}</td></tr>\n')
    parts.append("""    </tbody>
  </table>

  <p class="note" style="margin-top:14px">截图之外还有三件事这张表量不出来：单选点（页面导航前面那个圆点）
     从 Streamlit 的红色 <code>#FF4B4B</code> 变成墨色——它由主题令牌上色，样式表够不着；
     助手头像里那两个字 <code>IB</code> 换成了 Aspire 的图形标记；正文行宽从约 90 字收窄到约 70 字。</p>
""")

    for i, (title, before, after, note) in enumerate(PAIRS, start=1):
        parts.append(f'\n  <h2><span class="n">{i}</span>{title}</h2>\n')
        parts.append(f'  <p class="note">{note}</p>\n')
        parts.append('  <div class="pair">\n')
        parts.append(figure(before, "改动前"))
        parts.append(figure(after, "现在", "after"))
        parts.append('  </div>\n')

    n = len(PAIRS)
    parts.append(f"""
  <h2><span class="n">{n + 1}</span>侧边栏 · 常驻提示框 → 弹窗</h2>
  <p class="note">「这是共享实例」这件事，原来在侧边栏里常驻一整个蓝色提示框，八个文字行压在导航栏上，
     每一屏都在，而访客只会读一次。现在同一个位置是一个按钮，点开是一次性的弹窗：
     读一遍、按 <b>Got it</b> 就收起来。Admin 页仍然保留整段内联说明——那是唯一几个按钮真的会改到
     「别人会看到什么」的页面。</p>
""")
    parts.append('  <div class="pair narrow">\n')
    parts.append(figure(crop_sidebar('before-0-signin.png', 'dialog-before.png'), "改动前"))
    parts.append(figure(crop_sidebar('final-0-signin.png', 'dialog-after.png'), "现在", "after"))
    parts.append('  </div>\n')
    parts.append(detail('final-0-dialog.png',
                        "弹窗本身也按同一套量过再改：遮罩原本是一层很淡的灰，页面留在后面和弹窗一样响，"
                        "改成墨色；面板从 24px 收到和其他卡片一样的 18px；标题是真的 <code>h2</code>，"
                        "在 <code>stHeading</code> 之外，不显式指定就会用主题字体，而它周围的标题都是 Jost。"))
    parts.append('  <table style="margin-top:18px">\n'
                 '    <thead><tr><th>元素</th><th>属性</th><th>Streamlit 默认</th><th>现在</th></tr></thead>\n'
                 '    <tbody>\n')
    for label, prop, old, new in DLG_TOKENS:
        parts.append(f'      <tr><td>{label}</td><td class="prop">{prop}</td>'
                     f'<td class="val old">{old}</td><td class="val">{new}</td></tr>\n')
    parts.append('    </tbody>\n  </table>\n')

    parts.append(f"""
  <h2><span class="n">{n + 2}</span>登录 · 选中态</h2>
  <p class="note">这个状态只在「点中一个账号、还没点 Continue」之间短暂存在，之前的截图流程一直漏掉它。
     行首的姓名缩写方块是 CSS <code>::before</code> 画出来的，不是写在按钮文字里——
     按钮文字只有一种样式，所以原来的 <code>JD · Jamie Doe — Backend Engineer</code> 读起来像三个事实
     被标点串在一起，而不是一个人和他的职位。</p>
""")
    parts.append(detail('final-0-sel.png', "选中的那一行用品牌绿铺满，方块留在原位，勾在右端。"
                                          "Continue 按钮保持黑色——绿色已经用在“选中”上了，"
                                          "最后的动作交给墨色，两种状态才不打架。"))

    parts.append(f"""
  <h2><span class="n">{n + 3}</span>聊天 · 提问后的完整往返</h2>
  <p class="note">因为登录的是合规官 Grace，付款 runbook 不在她此时可读的范围内，所以答案是写在琥珀色里的
     拒绝模板。琥珀保留给这一条：它是一个正确、完整、但被守卫主动留下的答案。</p>
""")
    parts.append(detail('final-0b-chat-answer.png', "会话行被限制在 37rem，实测一行 64 字。"
                                                   "宽屏下不限制时一行约 90 字，眼睛回扫时会丢行。"))

    parts.append(f"""
  <h2><span class="n">{n + 4}</span>对齐 · 头像与同一行的三张卡片</h2>
  <p class="note">三条都不是「风格」问题，是最普通的排版规则：<b>同行之物要共线</b>，
     以及<b>该居中的要在正中</b>。第一对的左边一张是<b>读者自己裁下来发过来的</b>；第三对是读者第二轮
     反馈（放大到只有一颗头像）补出来的 —— 第一轮的探针只量了方块的<b>外沿</b>和旁边的文字，
     在那组数上方块确实齐了，但方块<b>里面</b>的标记被图标的包裹层挤到右缘、又被 flex 压缩
     （19px 要的宽度量出来只剩 12px）。一个元素可以在正确的位置上，里面却画着错的东西。</p>
""")
    parts.append('  <div class="pair">\n')
    parts.append(figure('_align-before-avatar.png', "改动前（读者截图）"))
    parts.append(figure('_align-after-avatar.png', "现在", "after"))
    parts.append('  </div>\n')
    parts.append('  <div class="pair">\n')
    parts.append(figure('_align-before-cards.png', "改动前"))
    parts.append(figure('_align-after-cards.png', "现在", "after"))
    parts.append('  </div>\n')
    parts.append('  <div class="pair">\n')
    parts.append(figure('_tile-mark-before.png', "改动前（读者放大截图）"))
    parts.append(figure('_tile-assistant.png', "现在（4 倍特写）", "after"))
    parts.append('  </div>\n')
    parts.append('  <table style="margin-top:18px">\n'
                 '    <thead><tr><th>元素</th><th>规则</th><th>之前（实测）</th><th>现在（实测）</th></tr></thead>\n'
                 '    <tbody>\n')
    for label, rule, old, new in ALIGN_TOKENS:
        parts.append(f'      <tr><td>{label}</td><td class="prop">{rule}</td>'
                     f'<td class="val old">{old}</td><td class="val">{new}</td></tr>\n')
    parts.append('    </tbody>\n  </table>\n')
    parts.append(detail('_align-after-full.png',
                        "整轮往返。头像那 3px 的一半来自行盒：方块 32px、首行行盒 25.6px，"
                        "顶边对齐时方块中心必然落在文字视觉中心下方 3.2px —— 两个方向都量过，"
                        "DOM 探针给 3.2px，读者截图给 3.7px，取 3px。卡片那三处则是同一个根因："
                        "卡片高度跟着自己的文字走，标题多折一行就比旁边高一行，三个 <b>Open in …</b> "
                        "于是差 39px。列本来已经等高（<code>st.columns</code> 的职责），"
                        "所以只需要让卡片填满所在列、把脚注压到底部——标题少折一行的那张，"
                        "多出来的空档落在脚注<b>上方</b>，而不是把脚注顶上去了。"))

    parts.append("""
  <h2><span class="n">10</span>设计审计 · 两套公开规则的取舍</h2>
  <p class="note">把 <code>ui-ux-pro-max</code>（设计智能技能：按产品描述返回风格、配色、字体与交付前清单）
     和 <code>typography-audit</code>（78 条排版规则）跑了一遍。生成器给的<b>风格</b>结论
     （Minimalism / Swiss Style，适用「企业应用、仪表盘、专业工具」）和已有方向一致，所以这一轮是<b>审查</b>
     而不是重做；它的<b>版式</b>（hero + logo 墙 + Contact Sales）、<b>配色</b>（金色 #F59E0B + 紫色 #8B5CF6 + 深色底）
     和 <b>GSAP 滚动动画</b>都被否掉了——这是登录后的内部工具，而且紫色渐变正是它自己列的禁项。
     下表是清单里真正落地、并且都用运行中的应用量过的部分。</p>
""")
    parts.append('  <table style="margin-top:6px">\n'
                 '    <thead><tr><th>项目</th><th>规则</th><th>之前（实测）</th><th>现在（实测）</th></tr></thead>\n'
                 '    <tbody>\n')
    for label, rule, old, new in DESIGN_TOKENS:
        parts.append(f'      <tr><td>{label}</td><td class="prop">{rule}</td>'
                     f'<td class="val old">{old}</td><td class="val">{new}</td></tr>\n')
    parts.append('    </tbody>\n  </table>\n')
    parts.append(detail('boundary-choice.png',
                        "控件描边这一步是画出来选的，不是算出来的：#E4E7E5（原用，1.25:1）、#B4BAB6（1.94:1）、"
                        "#8D8D8D（3.00:1）。放大后第一个根本不像按钮，最后一个清晰而不显重。"
                        "最终取 <b>#868B88</b> —— 同色相里<b>同时</b>在纸白与灰底上过 3:1 的最浅值。"
                        "只换可交互的边（输入框、下拉框、次按钮、输入条），分隔线与卡片边仍留细线，"
                        "所以变重的只有你真能按下去的东西。"))
    if (UI / "citations-material-icons.png").exists():
        parts.append(detail('citations-material-icons.png',
                            "引用卡片上的来源图标：原来由操作系统画的 emoji（📄 🎫 💬 📁，三平台三套颜色，"
                            "且永远不是品牌色），现在是 Material 符号——Streamlit 自己的语法，"
                            "画出来是继承文字颜色的内联 SVG。"))

    parts.append("""
  <footer>
    <p><b>怎么验证的。</b>改动落在三处：<code>.streamlit/config.toml</code>（主题令牌）、
       <code>app.py</code> 的 <code>_BRAND_CSS</code>（每屏都注入：字体、令牌、制版头、弹窗）
       与 <code>_APP_CSS</code>（只在登录交接后注入：头像、气泡、行宽、过渡、控件描边），
       以及 <code>scripts/make_brand_assets.py</code> 生成的 <code>static/brand/</code>。
       字体自托管在 <code>static/fonts/</code>，由 Streamlit 的静态挂载以 <code>/app/static/…</code> 提供。</p>
    <p>全量测试 <code>Ran 198 tests … OK (skipped=2)</code>。截图与 DOM 采样：
       <code>results/ui-probe.mjs</code>（<code>SHOT_DIALOG=1</code> 会多截一组弹窗）·
       排版实测：<code>results/type-probe.mjs</code>（输出留在 <code>results/typography-after.txt</code>）·
       对齐实测：<code>results/layout-probe.mjs</code>（输出留在
       <code>results/layout-before.txt</code> / <code>layout-after.txt</code>，<code>SHOT=</code>
       会顺手把量过的那一页拍下来）·
       样式对比：<code>results/diff_probe.py</code> ·
       起服务到收尸的整段生命周期：<code>results/with-app.sh</code>。
       弹窗与控件的盒模型是<b>量</b>出来的（<code>results/_dlg_struct.mjs</code> 打印真实的盒子树，
       <code>results/_cards_dump.mjs</code> 打印卡片那一层），不是照着 Streamlit 的 DOM 猜的。</p>
    <p>第 9 节的头像那一半，左边不是流程截的图，是读者自己裁下来发过来的——量出来的 3.7px
       和 DOM 探针的 3.2px 对得上，所以那 3px 的两个来源都写进了表里。</p>
    <p style="margin-bottom:0">还留着的：登录列表每行仍然是单行文字（按钮文字只能有一种样式，
       两行标题需要另找做法）；Admin / Compliance / ROI 明细里用 <code>·</code> 连接的统计数字没有动，
       那里分隔号是在分隔两个实测值，不是装饰标题。引用卡片上只留日期不留到秒的
       <code>updated_at</code>：25 个字符在三分之一行宽里放不下，它原来正是从日期中间断开；
       到秒的值在审计页读，那里整列都是时间戳。卡片圆角仍是主题给的 12px——原本有一条
       18px 的规则，但它挂在一个这个版本根本不存在的 testid 上，一直没生效，已按实测删掉。</p>
  </footer>

</div>
</body>
</html>
""")

    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
