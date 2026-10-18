"""Source geometry guards. These locate ink, never identify business objects.

Inspired by Docling's source-cell/bbox reconciliation. No external OCR weights
are downloaded; optional image geometry is only a fallback to native text cells.
"""
from __future__ import annotations

from statistics import median


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def intersects(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def contain_source_cells(region, cells):
    """Never cut through a source cell; do not flood-fill adjacent columns."""
    touched = [c["bbox"] for c in cells if intersects(region, c["bbox"])
               and region[0] <= (c["bbox"][0]+c["bbox"][2])/2 <= region[2]]
    return union([region, *touched]) if touched else list(region)


def recovery_bounds(parent, issue, cells, edges=()):
    """Recover context outside the *parent* crop, not just a bigger fragment.

    A raster fallback without cells grows relative to the current crop. With
    source cells, add the nearest line in the requested direction. No page-size
    coordinates or document-specific station/section rules are used.
    """
    base = union([parent, issue])
    seed=list(base)
    wanted = set(edges) or {"top", "bottom", "left", "right"}
    selected = []
    for edge in (e for e in ("top","bottom","left","right") if e in wanted):
        axis = 1 if edge in {"top", "bottom"} else 0
        other = 1-axis
        forward = edge in {"bottom", "right"}
        candidates = []
        for cell in cells:
            b = cell["bbox"]
            # A horizontal neighbour in another column is not a continuation
            # just because the two boxes share the same page.
            overlap = min(seed[other+2],b[other+2])-max(seed[other],b[other])
            if overlap <= 0 or overlap < min(seed[other+2]-seed[other],b[other+2]-b[other])/2:
                continue
            distance = b[axis]-seed[axis+2] if forward else seed[axis]-b[axis+2]
            if distance >= 0:
                candidates.append((distance,b))
        if candidates:
            selected.append(min(candidates,key=lambda c:c[0])[1])
        else:
            index = axis+2 if forward else axis
            delta = (seed[axis+2]-seed[axis])/2
            base[index] = min(1,seed[index]+delta) if forward else max(0,seed[index]-delta)
    return contain_source_cells(union([base,*selected]),cells)


def image_line_cells(path):
    """Return geometry-only ink runs, in normalized display-page coordinates.

    Contrast and spacing are measured from this image; constants below are
    dimensionless noise/spacing ratios, not PDF coordinates or page templates.
    Do not use these boxes to assert text, reading order, or OCR confidence.
    """
    try:
        import cv2
        import numpy as np
        from PIL import Image
    except ImportError:
        return []
    with Image.open(path) as source:
        gray = np.asarray(source.convert("L"))
    height,width = gray.shape
    _, mask = cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask,8)
    parts=[tuple(map(int,s)) for s in stats[1:] if s[2]>1 and s[3]>1
           and s[2]<width/2 and s[3]<height/10 and max(s[2]/s[3],s[3]/s[2])<40]
    if not parts:return []
    # Area weighting prevents a page's faint speckle population from deciding
    # that a two-pixel noise component is the normal character height.
    ordered=sorted(parts,key=lambda s:s[3]); total=sum(s[4] for s in ordered)
    cumulative=0
    for s in ordered:
        cumulative+=s[4]
        if cumulative>=total/2:
            scale=s[3];break
    parts=[s for s in parts if scale/4<=s[3]<=3*scale and s[4]>=scale*scale/25]
    rows=[]
    for x,y,w,h,area in sorted(parts,key=lambda s:s[1]+s[3]/2):
        center=y+h/2
        if rows and abs(center-median(p[1]+p[3]/2 for p in rows[-1]))<scale*.6:
            rows[-1].append((x,y,w,h,area))
        else:rows.append([(x,y,w,h,area)])
    result=[]
    for row in rows:
        chunks=[]
        for part in sorted(row):
            if chunks and part[0]-max(p[0]+p[2] for p in chunks[-1])<=scale*3:
                chunks[-1].append(part)
            else:chunks.append([part])
        for chunk in chunks:
            # Include punctuation/antialiased edges. Padding derives from this
            # image's glyph scale, independent of source DPI or page dimensions.
            pad=scale/6
            result.append([max(0,(min(p[0] for p in chunk)-pad)/width),
                           max(0,(min(p[1] for p in chunk)-pad)/height),
                           min(1,(max(p[0]+p[2] for p in chunk)+pad)/width),
                           min(1,(max(p[1]+p[3] for p in chunk)+pad)/height)])
    return result
