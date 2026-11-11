"""Human-reviewed master identities; matching proposals never create masters.

Source records and master IDs have separate lifecycles. These comparisons are
identity hints, not engineering assessment rules or model confidence scores.
"""
import json
import math
import re
import unicodedata
from difflib import SequenceMatcher


class IdentityConflict(ValueError):
    """A stale revision, duplicate business key, or unacknowledged conflict."""


FIELDS = ('namespace', 'external_id', 'route_code', 'side', 'start_station_m', 'end_station_m')
LABELS = dict(zip(FIELDS, ('编号所属范围', '外部边坡编号', '路线', '左右侧', '起点桩号（米）', '终点桩号（米）')))
MATCHING_VERSION = 'slope-identity-proposals/1'


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).upper()


def schema(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS slope_identity_profiles(
        slope_id TEXT PRIMARY KEY REFERENCES slopes(id), revision INTEGER NOT NULL DEFAULT 0,
        payload TEXT NOT NULL, reviewed INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS slope_identity_keys(
        namespace TEXT NOT NULL, external_id TEXT NOT NULL, slope_id TEXT NOT NULL REFERENCES slopes(id),
        PRIMARY KEY(namespace,external_id));
      CREATE TABLE IF NOT EXISTS slope_identity_events(
        id INTEGER PRIMARY KEY, slope_id TEXT NOT NULL REFERENCES slopes(id), at TEXT NOT NULL, payload TEXT NOT NULL);
    ''')


def from_label(label):
    text = normalized(label)
    result = {}
    roads = set(re.findall(r'(?<![A-Z0-9])[GSXY]\d{2,4}(?!\d)', text))
    if len(roads) == 1:
        result['route_code'] = roads.pop()
    sides = {side for side in ('左', '右') if side in text}
    if len(sides) == 1:
        result['side'] = sides.pop()+'侧'
    stations = re.findall(r'K(\d+)\+(\d+(?:\.\d+)?)', text)
    if len(stations) == 2:
        values = [int(k)*1000+float(m) for k, m in stations]
        if values[0] <= values[1] and all(float(m) < 1000 for _, m in stations):
            result.update(start_station_m=values[0], end_station_m=values[1])
    return result


def validate_attributes(value):
    if not isinstance(value, dict) or any(k not in FIELDS for k in value):
        raise ValueError('身份字段无效，请仅提交编号范围、外部编号、路线、左右侧和起终点桩号')
    result = {}
    for key in FIELDS:
        val = value.get(key)
        if val is None or val == '':
            continue
        if key.endswith('_m'):
            if isinstance(val, bool):
                raise ValueError('桩号必须是非负米数')
            try:
                val = float(val)
            except (ValueError, TypeError):
                raise ValueError('桩号必须是非负米数') from None
            if not math.isfinite(val) or val < 0:
                raise ValueError('桩号必须是非负有限米数')
        else:
            if not isinstance(val, str) or len(val) > 200:
                raise ValueError('身份文字字段过长或类型无效')
            val = val.strip()
            if key == 'route_code':
                val = normalized(val)
            if key == 'side':
                val = {'左': '左侧', '右': '右侧'}.get(val, val)
                if val not in ('左侧', '右侧'):
                    raise ValueError('左右侧应为左侧、右侧或留空；双侧不能当成单处边坡身份')
        if val != '':
            result[key] = val
    if bool(result.get('namespace')) != bool(result.get('external_id')):
        raise ValueError('外部编号必须同时填写编号所属范围，不能假设全项目编号唯一')
    if ('start_station_m' in result) != ('end_station_m' in result):
        raise ValueError('请同时填写起点和终点桩号，或同时留空')
    if result.get('start_station_m', 0) > result.get('end_station_m', 0):
        raise ValueError('起点桩号不能大于终点桩号')
    return result


def seed(db, slope):
    props = slope.get('props', {})
    attrs = from_label(slope.get('label', ''))
    for key, original in (('route_code', 'route_code_cache'), ('side', 'side'),
                          ('start_station_m', 'start_station_m'), ('end_station_m', 'end_station_m')):
        if props.get(original) is not None:
            attrs[key] = props[original]
    # AUTO codes and survey control points are not confirmed external IDs or slope coordinates.
    try:
        attrs = validate_attributes(attrs)
    except ValueError:
        attrs = from_label(slope.get('label', ''))
    db.execute('INSERT OR IGNORE INTO slope_identity_profiles VALUES(?,0,?,0)',
               (slope['id'], json.dumps(attrs, ensure_ascii=False)))


def profile(db, slope_id):
    slope = db.execute('SELECT * FROM slopes WHERE id=?', (slope_id,)).fetchone()
    if not slope:
        raise KeyError('边坡身份不存在')
    row = db.execute('SELECT * FROM slope_identity_profiles WHERE slope_id=?', (slope_id,)).fetchone()
    keys = [dict(r) for r in db.execute('SELECT namespace,external_id FROM slope_identity_keys WHERE slope_id=?', (slope_id,))]
    events = [json.loads(r[0]) for r in db.execute('SELECT payload FROM slope_identity_events WHERE slope_id=? ORDER BY id DESC', (slope_id,))]
    return {'slope_id': slope_id, 'name': slope['name'], 'revision': row['revision'] if row else 0,
            'attributes': json.loads(row['payload']) if row else from_label(slope['name']),
            'reviewed': bool(row and row['reviewed']), 'keys': keys, 'history': events}


def reviewer(body):
    actor, reason = body.get('actor'), body.get('reason')
    if not isinstance(actor, str) or not isinstance(reason, str) or not actor.strip() or not reason.strip():
        raise ValueError('请填写核对人和依据')
    if len(actor) > 100 or len(reason) > 4000 or body.get('confirmed') is not True:
        raise ValueError('请确认核对结果，并使用有效长度的核对人和依据')
    return actor.strip(), reason.strip()


def save_profile(db, slope_id, body):
    from .slope_library import encoded, now
    actor, reason = reviewer(body)
    old = profile(db, slope_id)
    if type(body.get('revision')) is not int or body['revision'] != old['revision']:
        raise IdentityConflict('边坡身份已更新，请刷新后再核对')
    attrs = validate_attributes(body.get('attributes', {}))
    name = body.get('name', old['name'])
    if not isinstance(name, str) or not name.strip() or len(name) > 250:
        raise ValueError('请填写有效边坡名称')
    if attrs.get('external_id'):
        key = (normalized(attrs['namespace']), normalized(attrs['external_id']))
        owner = db.execute('SELECT slope_id FROM slope_identity_keys WHERE namespace=? AND external_id=?', key).fetchone()
        if owner and owner['slope_id'] != slope_id:
            raise IdentityConflict('该所属范围内的边坡编号已登记给另一处边坡，请核对已有身份')
        # Keep previous keys as historical aliases; IDs are never silently reassigned.
        db.execute('INSERT OR IGNORE INTO slope_identity_keys VALUES(?,?,?)', (*key, slope_id))
    db.execute('INSERT INTO slope_identity_profiles VALUES(?,?,?,1) ON CONFLICT(slope_id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload,reviewed=1',
               (slope_id, old['revision']+1, encoded(attrs)))
    db.execute("UPDATE slopes SET name=?,identity_status='confirmed' WHERE id=?", (name.strip(), slope_id))
    event = {'at': now(), 'action': 'confirm_master_identity', 'actor': actor, 'reason': reason,
             'before': {k: old[k] for k in ('name','attributes','revision')},
             'after': {'name': name.strip(), 'attributes': attrs, 'revision': old['revision']+1}}
    db.execute('INSERT INTO slope_identity_events(slope_id,at,payload) VALUES(?,?,?)', (slope_id, event['at'], encoded(event)))
    return profile(db, slope_id)


def source_identity(db, binding):
    root = db.execute('SELECT * FROM bindings WHERE id=?', (binding['group_id'],)).fetchone() or binding
    attrs = from_label(root['label'])
    row = db.execute('SELECT r.payload FROM records r JOIN snapshots s ON s.id=r.snapshot_id WHERE r.binding_id=? ORDER BY s.created_at DESC,r.rowid DESC LIMIT 1', (root['id'],)).fetchone()
    obj = json.loads(row['payload']) if row else {}
    field_map = {'路线编号':'route_code','道路编号':'route_code','route_code':'route_code',
                 '边坡编号':'external_id','外部边坡编号':'external_id','external_id':'external_id',
                 '编号所属范围':'namespace','namespace':'namespace','左右侧':'side','side':'side',
                 'start_station_m':'start_station_m','end_station_m':'end_station_m'}
    values, evidence = {}, []
    for fact in obj.get('facts', []):
        raw = fact['raw'];field_name = raw.get('field') or raw.get('property_code')
        field = field_map.get(field_name) if isinstance(field_name, str) else None
        if not field or fact.get('issues') or not fact.get('evidence'):
            continue
        val = raw.get('value')
        if isinstance(val, (str, int, float)) and not isinstance(val, bool):
            values.setdefault(field, set()).add(str(val))
            evidence.append({'field': field, 'value': val, 'evidence': fact['evidence'], 'fact_id': raw.get('id')})
    conflicts = []
    for field, choices in values.items():
        if field in attrs:
            choices.add(str(attrs[field]))
        def equivalent(value):
            if field.endswith('_m'):
                try:
                    return str(float(value))
                except (ValueError, TypeError):
                    pass
            if field == 'side':
                value = {'左': '左侧', '右': '右侧', 'left': '左侧', 'right': '右侧'}.get(value, value)
            return normalized(value)
        if len({equivalent(c) for c in choices}) == 1:
            attrs[field] = next(iter(choices))
            if field == 'side':
                attrs[field] = {'左': '左侧', '右': '右侧', 'left': '左侧', 'right': '右侧'}.get(attrs[field], attrs[field])
        else:
            attrs.pop(field, None)
            conflicts.append(LABELS[field]+'在本来源中有不同表述：'+' / '.join(sorted(choices)))
    # Incomplete recognized identifiers remain hints, never registry keys.
    for key in ('start_station_m', 'end_station_m'):
        if key in attrs:
            try:
                attrs[key] = float(attrs[key])
                if not math.isfinite(attrs[key]) or attrs[key] < 0:
                    raise ValueError()
            except (ValueError, TypeError):
                attrs.pop(key, None)
    return {'label': root['label'], 'attributes': attrs, 'conflicts': conflicts, 'evidence': evidence,
            'binding_id': root['id'], 'group_id': root['group_id']}


def compare(attrs, target):
    reasons, conflicts = [], []
    for field in ('namespace','route_code','side'):
        if attrs.get(field) and target.get(field):
            if normalized(attrs[field]) == normalized(target[field]):
                reasons.append(LABELS[field]+'一致')
            else:
                conflicts.append(LABELS[field]+'不同：'+str(attrs[field])+' / '+str(target[field]))
    if all(k in a for a in (attrs, target) for k in ('start_station_m','end_station_m')):
        a, b, c, d = (float(attrs['start_station_m']), float(attrs['end_station_m']),
                       float(target['start_station_m']), float(target['end_station_m']))
        if a == c and b == d:
            reasons.append('桩号范围一致')
        elif max(a, c) <= min(b, d):
            reasons.append('桩号范围部分重合，边界需核对')
        else:
            conflicts.append('桩号范围不重合')
    return reasons, conflicts


def proposals(db, source, *, target_id=''):
    attrs, name = source['attributes'], normalized(source['label'])
    rows = []
    for slope in db.execute('SELECT * FROM slopes ORDER BY id'):
        target = profile(db, slope['id'])
        reasons, conflicts = compare(attrs, target['attributes'])
        # Only current source assignments contribute aliases. Releasing a wrong
        # binding removes its influence without erasing the historical decision.
        aliases = [normalized(slope['name']), *[normalized(r[0]) for r in db.execute('SELECT label FROM bindings WHERE slope_id=?', (slope['id'],))]]
        score = 0
        if name and name in aliases:
            score = 70;reasons.append('名称或已确认来源别名一致')
        elif name and max((SequenceMatcher(None, name, alias).ratio() for alias in aliases), default=0) >= .8:
            score = 25;reasons.append('名称相近，仅供核对')
        if '桩号范围一致' in reasons:
            score = max(score, 90 if '路线一致' in reasons and '左右侧一致' in reasons else 60)
        elif '桩号范围部分重合，边界需核对' in reasons and '路线一致' in reasons:
            score = max(score, 50)
        key = (normalized(attrs.get('namespace')), normalized(attrs.get('external_id')))
        if all(key) and any((k['namespace'], k['external_id']) == key for k in target['keys']):
            score = 100;reasons.append('已登记的外部编号及所属范围一致')
        if slope['id'] == target_id or score:
            rows.append({'slope_id': slope['id'], 'name': slope['name'], 'revision': target['revision'],
                         'attributes': target['attributes'], 'reviewed': target['reviewed'],
                         'rank': score, 'reasons': reasons, 'conflicts': conflicts,
                         'requires_review': True})
    return sorted(rows, key=lambda r: (bool(r['conflicts']), -r['rank'], r['slope_id']))


def preview(paths, *, binding_id='', slope_id='', namespace='', external_id=''):
    from . import slope_library as lib
    with lib.database(paths) as db:
        lib._seed_legacy(db, paths)
        if namespace or external_id:
            if not namespace or not external_id:
                raise ValueError('请同时提供编号所属范围和外部编号')
            row = db.execute('SELECT slope_id FROM slope_identity_keys WHERE namespace=? AND external_id=?',
                             (normalized(namespace), normalized(external_id))).fetchone()
            if not row:
                raise KeyError('该业务编号尚未登记')
            return profile(db, row['slope_id'])
        if not binding_id:
            return profile(db, slope_id)
        binding = db.execute('SELECT * FROM bindings WHERE id=?', (binding_id,)).fetchone()
        if not binding:
            raise KeyError('待核对来源不存在')
        source = source_identity(db, binding)
        candidates = proposals(db, source, target_id=slope_id or binding['slope_id'] or '')
        related = [dict(r) for r in db.execute('SELECT id,revision,slope_id FROM bindings WHERE group_id=? AND slope_id IS ?',
                                               (binding['group_id'], binding['slope_id']))]
        return {'source': source, 'binding': dict(binding), 'candidates': candidates,
                'related': related, 'auto_linked': False,
                'matching_version': MATCHING_VERSION,
                'policy': '新来源先保存；匹配仅建议；核对后关联已有主档或新建主档'}


def update(paths, body):
    from . import slope_library as lib
    with lib.database(paths) as db:
        db.execute('BEGIN IMMEDIATE')
        lib._seed_legacy(db, paths)
        return save_profile(db, body.get('slope_id'), body)
