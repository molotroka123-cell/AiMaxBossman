import io, time, zipfile, statistics, importlib, sys
from pathlib import Path
from ebooklib import epub
OUT = Path('.')
planted = {}
# 1) third-party writer: ebooklib, 3 chapters
for b in range(3):
    book = epub.EpubBook(); book.set_identifier(f'id{b}'); book.set_title(f'Книга {b}'); book.set_language('ru')
    chs = []
    for c in range(4):
        marker = f'MARK-EBL-{b}-{c}'
        ch = epub.EpubHtml(title=f'Гл {c}', file_name=f'text/ch{c}.xhtml', lang='ru')
        ch.content = f'<h1>Глава {c}</h1><p>Текст &amp; данные {marker} конец.</p>'
        book.add_item(ch); chs.append(ch)
        planted.setdefault(f'ebl{b}.epub', []).append(marker)
    book.toc = chs; book.spine = ['nav'] + chs
    book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav())
    epub.write_epub(str(OUT / f'ebl{b}.epub'), book)
# 2) hand-written per spec: OPF in OEBPS/, entities, script decoy, fragment hrefs
def spec(name, n):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        z.writestr('META-INF/container.xml', '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = ''.join(f'<item id="c{i}" href="chap/c{i}.xhtml" media-type="application/xhtml+xml"/>' for i in range(n))
        spine = ''.join(f'<itemref idref="c{i}"/>' for i in range(n))
        z.writestr('OEBPS/content.opf', f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Spec {name}</dc:title></metadata><manifest>{items}</manifest><spine>{spine}</spine></package>')
        for i in range(n):
            m = f'MARK-SPEC-{name}-{i}'
            planted.setdefault(f'spec{name}.epub', []).append(m)
            z.writestr(f'OEBPS/chap/c{i}.xhtml', f'<html xmlns="http://www.w3.org/1999/xhtml"><head><style>p{{}}</style><script>var DECOY="DECOY-{i}";</script></head><body><p>Caf&#233; &lt;b&gt; {m}</p><ul><li>пункт {i}</li></ul></body></html>')
    (OUT / f'spec{name}.epub').write_bytes(buf.getvalue())
for k in range(3): spec(str(k), 5)

def native(path, mod):
    art = mod.parse_file(path)
    return '\n'.join(s.text for s in art.sections), [s.ref for s in art.sections]
def run(label, fn):
    hits = total = decoy = 0; times = []
    for f, marks in sorted(planted.items()):
        t0 = time.perf_counter(); text = fn(OUT / f); times.append((time.perf_counter() - t0) * 1000)
        hits += sum(m in text for m in marks); total += len(marks); decoy += text.count('DECOY-')
    print(f'{label:22} recall {hits}/{total}  decoy_leaks {decoy}  median_ms {statistics.median(times):.2f}')
import bossman.file_intel as fi
from bossman import exec_cache
def nocache(p):
    exec_cache.get_cache().__init__() if hasattr(exec_cache.get_cache(), '__init__') else None
    return native(p, fi)[0]
mode = sys.argv[1]
if mode == 'native': run('native parse_file', lambda p: native(p, fi)[0])
else:
    from markitdown import MarkItDown
    md = MarkItDown(enable_plugins=False)
    md.convert(str(OUT / 'spec0.epub'))  # warm import
    run('markitdown', lambda p: md.convert(str(p)).text_content)
print('refs sample:', native(OUT / 'spec1.epub', fi)[1][:3])
