"""Viewport evidence and OCR-to-DOM region alignment.

DOM and pixels do not contain identical text: OCR also reads text inside images and misreads
DOM text. align_ocr splits OCR boxes into
  * dom_duplicate  - the same DOM text was rendered there (DOM is the cleaner copy),
  * ocr_variant    - DOM text is there but OCR read it differently (similarity < 0.9, e.g. "Instagum"),
  * image_text     - no DOM text under the box; grouped by the <img> that contains it,
  * unlocated_text - no DOM text and no image (canvas, CSS text, video frames).
Only image_text/unlocated_text are new information, so only they feed the OCR model.
"""
from difflib import SequenceMatcher
import math
import re
import unicodedata

VIEWPORT_SCRIPT = r"""() => {
  const items = []; let visited = 0, truncated = false;
  const LANDMARK = 'nav,header,footer,aside,[role=navigation],[role=banner],[role=contentinfo],[role=complementary],[role=menu],[role=menubar],[role=tablist],[role=dialog]';
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    if (++visited > 20000) { truncated = true; break; }
    const node = walker.currentNode, el = node.parentElement;
    if (!el || el.closest('script,style,noscript,template')) continue;
    const style = getComputedStyle(el);
    if (style.visibility !== 'visible' || style.display === 'none') continue;
    let hidden = false;
    for (let p = el; p; p = p.parentElement) {
      if (Number(getComputedStyle(p).opacity) === 0) { hidden = true; break; }
    }
    if (hidden) continue;
    // Character ranges avoid importing the off-screen part of a long text node.
    const outer = document.createRange(); outer.selectNodeContents(node);
    if (![...outer.getClientRects()].some(r=>r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth)) continue;
    let text = '', boxes = [];
    for (let i = 0; i < node.length; i++) {
      if (i >= 20000) { truncated = true; break; }
      const range = document.createRange();
      range.setStart(node, i); range.setEnd(node, i + 1);
      const r = range.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0 || r.bottom <= 0 || r.top >= innerHeight || r.right <= 0 || r.left >= innerWidth) continue;
      const x = Math.max(0, Math.min(innerWidth - 1, (r.left+r.right)/2));
      const y = Math.max(0, Math.min(innerHeight - 1, (r.top+r.bottom)/2));
      const top = document.elementFromPoint(x,y);
      if (!top || !(el === top || el.contains(top))) continue;
      text += node.data[i];
      boxes.push([r.left,r.top,r.right,r.bottom]);
    }
    if (!text.trim()) continue;
    const landmark = el.closest(LANDMARK);
    const region = landmark ? (landmark.getAttribute('role') || landmark.tagName.toLowerCase())
      : el.closest('button,[role=button]') ? 'button' : el.closest('a') ? 'link' : 'content';
    items.push({text:text.trim(), raw:text, boxes, region, in_main: !!el.closest('main,article,[role=main]')});
  }
  const images = [...document.images].map(img => ({img, r: img.getBoundingClientRect()}))
    .filter(({img, r}) => r.width >= 48 && r.height >= 48 && r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth
      && getComputedStyle(img).visibility === 'visible')
    .slice(0, 200).map(({img, r}) => ({box:[r.left,r.top,r.right,r.bottom], alt:(img.alt || '').slice(0, 500)}));
  return {text:items.map(x=>x.text).join('\n'), items, images, truncated,
    viewport:{width:innerWidth,height:innerHeight,scroll_x:scrollX,scroll_y:scrollY,dpr:devicePixelRatio}};
}"""


def normalize(value):
    return re.sub(r'\W+', '', unicodedata.normalize('NFKC', value or '').lower())


def compare_texts(dom, ocr, stable=True):
    def chars(value):
        return set(re.findall(r'\w', unicodedata.normalize('NFKC', value).lower()))
    a, b = chars(dom or ''), chars(ocr or '')
    return {'scope': 'same_viewport' if dom is not None else 'image_only',
            'dom_stable_across_screenshot': stable if dom is not None else None,
            'character_overlap': len(a & b) / len(a | b) if a and b else None,
            'dom_characters': len(dom or ''), 'ocr_characters': len(ocr or ''),
            'note': 'Diagnostic only; image text, OCR errors and DOM order can differ. No score adjustment.'}


def _dom_chars(capture):
    """(char, box) pairs; old reports without `raw` keep boxes but lose exact characters."""
    chars = []
    for item in capture.get('items') or []:
        raw, boxes = item.get('raw'), item.get('boxes') or []
        if raw is None and len(item.get('text', '')) == len(boxes):
            raw = item['text']
        visible = [c for c in raw] if raw is not None and len(raw) == len(boxes) else [None] * len(boxes)
        chars.extend(zip(visible, boxes))
    return chars


def _rect(points, dpr):
    xs, ys = [p[0] / dpr for p in points], [p[1] / dpr for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _inside(x, y, box, pad=2):
    return box[0] - pad <= x <= box[2] + pad and box[1] - pad <= y <= box[3] + pad


def align_ocr(capture, ocr_items, similarity_threshold=0.9):
    """Assign each accepted OCR box to a DOM duplicate, OCR misread, image, or unlocated region."""
    viewport = (capture or {}).get('viewport') or {}
    dpr = viewport.get('dpr')
    result = {'method': 'dom_character_boxes_inside_ocr_box', 'available': False, 'similarity_threshold': similarity_threshold,
              'dom_duplicate': [], 'ocr_variant': [], 'image_text': [], 'unlocated_text': [], 'images': []}
    accepted = [(index, item) for index, item in enumerate(ocr_items or []) if item.get('accepted')]
    if not isinstance(dpr, (int, float)) or not math.isfinite(dpr) or dpr <= 0 or not accepted:
        return result
    result['available'] = True
    chars = _dom_chars(capture)
    images = (capture or {}).get('images') or []
    grouped = {}
    for index, item in accepted:
        points = item.get('bbox') or []
        entry = {'ocr_item_index': index, 'text': item.get('text', ''), 'ocr_confidence': item.get('confidence')}
        if len(points) < 3:
            result['unlocated_text'].append(entry)
            continue
        left, top, right, bottom = _rect(points, dpr)
        covered = [c for c, b in chars if _inside((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, (left, top, right, bottom))]
        if covered:
            dom_text = ''.join(c for c in covered if c)
            similarity = (SequenceMatcher(None, normalize(entry['text']), normalize(dom_text)).ratio()
                          if dom_text else None)
            entry.update(dom_text=dom_text, similarity=similarity)
            key = 'dom_duplicate' if similarity is None or similarity >= similarity_threshold else 'ocr_variant'
            result[key].append(entry)
            continue
        cx, cy = (left + right) / 2, (top + bottom) / 2
        owner = next((i for i, image in enumerate(images) if _inside(cx, cy, image['box'], 0)), None)
        if owner is None:
            result['unlocated_text'].append(entry)
        else:
            entry['image_index'] = owner
            result['image_text'].append(entry)
            grouped.setdefault(owner, []).append(entry['text'])
    result['images'] = [{'image_index': i, 'alt': images[i].get('alt', ''), 'box': images[i]['box'], 'texts': texts}
                        for i, texts in sorted(grouped.items())]
    total = len(accepted)
    result['counts'] = {key: len(result[key]) for key in ('dom_duplicate', 'ocr_variant', 'image_text', 'unlocated_text')}
    result['dom_redundancy'] = (result['counts']['dom_duplicate'] + result['counts']['ocr_variant']) / total
    return result


def image_only_segments(alignment):
    """Text that only OCR can see, one segment per image, then text outside images."""
    segments = ['\n'.join(image['texts']) for image in alignment.get('images', [])]
    segments += [item['text'] for item in alignment.get('unlocated_text', [])]
    return [segment for segment in segments if segment.strip()]
