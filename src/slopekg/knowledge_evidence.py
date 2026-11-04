"""Source-local evidence assembly. No inferred values or domain field rules."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from .visual_evidence import align_quote, literal_supported, source_contains


_UNITS = {'平方厘米':'cm^2','立方厘米':'cm^3','平方毫米':'mm^2','立方毫米':'mm^3',
          '平方千米':'km^2','平方米':'m^2','立方米':'m^3','平方分米':'dm^2',
          '千米':'km','厘米':'cm','毫米':'mm','分米':'dm','微米':'μm','米':'m',
          '千克':'kg','毫克':'mg','克':'g','毫升':'mL','升':'L'}
_UNIT_PATTERN = re.compile('|'.join(sorted(_UNITS, key=len, reverse=True)))


def presentation(value):
    """Normalize width only. Formula powers, subscripts and signs stay distinct."""
    return ''.join(chr(ord(c)-0xFEE0) if '\uff01' <= c <= '\uff5e' else
                   ' ' if c == '\u3000' else c for c in str(value))


def source_quote(quote, source):
    """Return a width-equivalent original quote, retaining source spelling."""
    if not isinstance(quote,str):
        return None
    if not source_contains(presentation(quote),presentation(source),table=True):
        return None
    aligned=align_quote(presentation(quote),presentation(source))
    if aligned:
        return source[aligned['start']:aligned['end']]
    return quote


def canonical_units(value):
    value = unicodedata.normalize('NFC', presentation(value))
    value = _UNIT_PATTERN.sub(lambda m:_UNITS[m[0]], value)
    value=value.replace('²','^2').replace('³','^3')
    return re.sub(r'(?<![A-Za-z])(km|cm|mm|dm|m)([23])(?![A-Za-z0-9])',r'\1^\2',value)


def unit_supported(value, quotes):
    return any(literal_supported(value,q,unit=True) or
               literal_supported(canonical_units(value),canonical_units(q),unit=True) for q in quotes)


def table_headers(evidence, units, allowed):
    """Attach exact structural parents, not guessed neighbouring table cells."""
    rows = list(evidence) if isinstance(evidence,list) else []
    seen = {r.get('span_id') for r in rows if isinstance(r,dict)}
    for row in list(rows):
        source = units.get(row.get('span_id'),{}) if isinstance(row,dict) else {}
        if source.get('source') != 'pdf_table':continue
        for key in source.get('header_ids',[]):
            header=units.get(key,{})
            if (key in allowed and key not in seen and header.get('table_id')==source.get('table_id')
                    and header.get('is_header') and header.get('text')):
                rows.append({'span_id':key,'quote':header['text']});seen.add(key)
    return rows


def table_unit_quotes(value,evidence,units):
    """A table unit must belong to the value column, not another header cell."""
    try:number=Decimal(presentation(value))
    except (InvalidOperation,ValueError):return None
    found=False;quotes=[]
    for row in evidence:
        source=units.get(row['span_id'],{})
        if source.get('source')!='pdf_table' or source.get('is_header'):continue
        positions=[]
        for i,cell in enumerate(source.get('cells',[])):
            try:equal=Decimal(presentation(cell).strip())==number
            except (InvalidOperation,ValueError):equal=False
            if equal:positions.append(i)
        if not positions:continue
        found=True
        for key in source.get('header_ids',[]):
            header=units.get(key,{})
            if header.get('table_id')!=source.get('table_id'):continue
            for i in positions:
                cells=header.get('cells',[])
                cell=str(cells[i]).strip() if i<len(cells) else ''
                canonical=canonical_units(cell)
                if re.fullmatch(r'[A-Za-zμ]+(?:\^[23])?(?:/[A-Za-zμ]+(?:\^[23])?)*',canonical):quotes.append(cell)
    return quotes if found else None


def _adjacent(left,right,units):
    a,b=units.get(left['span_id'],{}),units.get(right['span_id'],{})
    if a.get('page') != b.get('page') or any(u.get('source')=='pdf_table' for u in [a,b]):return False
    x,y=a.get('bbox'),b.get('bbox')
    if not x or not y:return False
    if not all(isinstance(v,(int,float)) for v in [*x,*y]):return False
    overlap=min(x[2],y[2])-max(x[0],y[0])
    height=max(x[3]-x[1],y[3]-y[1]);gap=y[1]-x[3]
    if overlap<=0 or overlap<.55*min(x[2]-x[0],y[2]-y[0]) or not -.15*height<=gap<=1.25*height:return False
    la,rb=left['quote'].rstrip(),right['quote'].lstrip()
    if not la or not rb or la[-1] in '。.!?！？；;':return False
    # Never join scalar fragments, signs, decimals or numeric table cells.
    if re.search(r'[0-9°%.,+−/-]$',la) and re.match(r'[0-9°%.,+−/-]',rb):return False
    ap,bp=align_quote(la,a.get('text','')),align_quote(rb,b.get('text',''))
    if not ap or not bp or ap['end']<len(a['text'].rstrip()) or bp['start']>len(b['text'])-len(b['text'].lstrip()):return False
    # An omitted intervening source line cannot be spliced out of a sentence.
    for key,u in units.items():
        box=u.get('bbox')
        if key in {left['span_id'],right['span_id']} or u.get('page')!=a['page'] or not box:continue
        if x[3]<box[1] and box[3]<y[1] and min(x[2],box[2])>max(x[0],box[0]):return False
    return True


def value_supported(value,evidence,units):
    value=presentation(value)
    if any(literal_supported(value,presentation(e['quote'])) or source_contains(value,presentation(e['quote']),table=True) for e in evidence):return True
    if not re.search(r'[^\W\d_]',value,re.UNICODE):return False
    ordered=sorted(evidence,key=lambda e:(e['page'],(e.get('bbox') or [0,0,0,0])[1],(e.get('bbox') or [0,0,0,0])[0]))
    for start,left in enumerate(ordered):
        joined=left['quote']
        for right in ordered[start+1:]:
            if not _adjacent(left,right,units):break
            joined+=right['quote'];left=right
            if literal_supported(value,joined) or source_contains(value,joined):return True
    return False
