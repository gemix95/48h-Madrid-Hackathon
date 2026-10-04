"""Build presentation/index.html: every slide of deck.json on one page, scaled to the window, arrow keys to move,
N to show the speaker notes. Open index.html in a browser; no server needed.

    python3 presentation/build.py
"""
import html
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOB = "/_blob/1a635214ac7e8086231bfb20407f7da8"  # the phone screenshot in the published deck -> board-phone.jpg


def main():
    deck = json.loads((HERE / "deck.json").read_text())
    fonts = "".join(f'<link rel="stylesheet" href="{f["href"]}">' for f in deck["faces"].values() if f.get("href"))
    slides, notes = [], []
    for sid in deck["order"]:
        src = (HERE / "slides" / f"{sid}.html").read_text().replace(BLOB, "board-phone.jpg")
        m = re.search(r"<aside>(.*?)</aside>", src, re.S)
        notes.append(m.group(1).strip() if m else "")
        src = re.sub(r"<aside>.*?</aside>", "", src, flags=re.S)
        src = src.replace("<section ", '<section class="slide" ', 1)
        slides.append(src)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(deck['title'])}</title>{fonts}
<style>
body{{margin:0;background:#111;overflow:hidden}}
.slide{{position:absolute;left:0;top:0;width:1920px;height:1080px;box-sizing:border-box;transform-origin:0 0;display:none}}
.slide.on{{display:flex}} .slide *{{margin:0}} .slide ul{{padding-left:40px}}
#notes{{position:fixed;left:0;right:0;bottom:0;background:#000c;color:#eee;font:20px/1.4 sans-serif;padding:16px 24px;display:none}}
</style></head><body>
{''.join(slides)}
<div id="notes"></div>
<script>
const S=[...document.querySelectorAll('.slide')], N={json.dumps(notes)};
let i=0;
function fit(){{const k=Math.min(innerWidth/1920,innerHeight/1080);S.forEach(s=>{{s.style.transform=`translate(${{(innerWidth-1920*k)/2}}px,${{(innerHeight-1080*k)/2}}px) scale(${{k}})`}})}}
function show(){{S.forEach((s,j)=>s.classList.toggle('on',j===i));document.getElementById('notes').textContent=N[i]}}
addEventListener('keydown',e=>{{if(['ArrowRight',' ','PageDown'].includes(e.key))i=Math.min(i+1,S.length-1);
if(['ArrowLeft','PageUp'].includes(e.key))i=Math.max(i-1,0);if(e.key==='n'){{const n=document.getElementById('notes');n.style.display=n.style.display==='block'?'none':'block'}}show()}});
addEventListener('click',()=>{{i=Math.min(i+1,S.length-1);show()}});
addEventListener('resize',fit);fit();show();
</script></body></html>"""
    (HERE / "index.html").write_text(page)
    print(f"index.html: {len(slides)} slides")


if __name__ == "__main__":
    main()
