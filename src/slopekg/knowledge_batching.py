"""Capacity planning for the exact Knowledge request, without dropping sources."""
from collections import deque

from . import knowledge_prompts as prompts
from .visual_batching import compact
from .visual_contract import ContractError


def prompt_chars(instruction, context):
    """Same serialization and delimiter as VisualRuntime.ask, including system."""
    return len(prompts.SYSTEM) + len(instruction + '\n输入：' + compact(context))


class RequestCapacityError(ContractError):
    """Even one indivisible source and its context cannot be sent safely."""


def source_closure(ids, units):
    # Include complete header provenance in each split table package.
    result = list(dict.fromkeys(ids))
    for key in result:
        for header in units[key].get('header_ids', []):
            if header not in result:
                result.append(header)
    return result


def split_chunk(chunk, units):
    # Headers are context, not progress; splitting on them can recurse forever.
    core = [key for key in chunk['unit_ids'] if not units[key].get('is_header')]
    if len(core) < 2:
        core = list(chunk['unit_ids'])
    if len(core) < 2:
        return []
    mid = len(core) // 2
    parts = []
    for index, ids in enumerate((core[:mid], core[mid:])):
        keys = source_closure(ids, units)
        parts.append({**chunk, 'id': chunk['id'] + '.' + str(index + 1),
                      'parent_chunk_id': chunk['id'], 'unit_ids': keys,
                      'pages': sorted({units[key]['page'] for key in keys}),
                      'assignments': [], 'attribution_status': 'pending'})
    # Unreferenced header-only spans must also remain represented.
    missing = set(chunk['unit_ids']) - {key for p in parts for key in p['unit_ids']}
    parts[0]['unit_ids'] += [key for key in chunk['unit_ids'] if key in missing]
    if any(set(p['unit_ids']) == set(chunk['unit_ids']) for p in parts):
        return []  # An indivisible header dependency, not permission to truncate.
    return parts


def fit_groups(groups, context, instruction, limit, split):
    """Retain fitting groups; recursively split oversized groups and singletons.

    Context is evaluated at iteration time: sequential discovery may grow its
    registry after every yield. No cached text-length estimate is used.
    """
    queue = deque(list(group) for group in groups)
    while queue:
        group = queue.popleft()
        if not group:
            continue
        rule = instruction(group) if callable(instruction) else instruction
        size = prompt_chars(rule, context(group))
        if size <= limit:
            yield group
            continue
        if len(group) > 1:
            mid = len(group) // 2
            queue.appendleft(group[mid:])
            queue.appendleft(group[:mid])
        else:
            children = split(group[0])
            if len(children) < 2:
                identity = group[0].get('id', group[0].get('entity_id', 'source'))
                raise RequestCapacityError(
                    f'Indivisible source/context exceeds prompt capacity: {identity}; '
                    f'{size}>{limit}; request was not sent; source retained')
            queue.extendleft([child] for child in reversed(children))


def pack_chunks(chunks, units, context, instruction, limit, *, source_budget=None):
    """Fill real request capacity; split at source boundaries, never text slices.

    Unlike halving whole batches, a small overflow moves only the necessary
    sources to the next request. The registry is remeasured after each yield.
    """
    def fits(group):
        if prompt_chars(instruction, context(group)) > limit:
            return False
        # The original text-work allowance remains a separate soft bound.
        # It cannot override the exact wire limit, or slice an atomic source.
        ids = [k for c in group for k in c['unit_ids']]
        return (source_budget is None or sum(len(units[k]['text']) + 70 for k in ids) <= source_budget
                or len({k for k in ids if not units[k].get('is_header')}) <= 1)
    queue, batch = deque(chunks), []
    while queue:
        chunk = queue.popleft()
        if fits([*batch, chunk]):
            batch.append(chunk)
            continue
        core = [k for k in chunk['unit_ids'] if not units[k].get('is_header')]
        if not core:
            core = list(chunk['unit_ids'])
        def children(index):
            parts = []
            for number, ids in enumerate((core[:index], core[index:]), 1):
                keys = source_closure(ids, units)
                parts.append({**chunk, 'id': chunk['id'] + '.' + str(number),
                    'parent_chunk_id': chunk['id'], 'unit_ids': keys,
                    'pages': sorted({units[k]['page'] for k in keys}),
                    'assignments': [], 'attribution_status': 'pending'})
            represented = {k for p in parts for k in p['unit_ids']}
            parts[0]['unit_ids'] += [k for k in chunk['unit_ids'] if k not in represented]
            return parts
        low, high, best = 1, len(core) - 1, None
        while low <= high:
            mid = (low + high) // 2
            left, right = children(mid)
            if fits([*batch, left]):
                best = (left, right); low = mid + 1
            else:
                high = mid - 1
        if best:
            left, right = best
            yield [*batch, left]
            batch = []
            queue.appendleft(right)
        elif batch:
            yield batch
            batch = []
            queue.appendleft(chunk)
        else:
            size = prompt_chars(instruction, context([chunk]))
            raise RequestCapacityError(f'Indivisible source/context exceeds prompt capacity: {chunk["id"]}; '
                                       f'{size}>{limit}; request was not sent; source retained')
    if batch:
        yield batch


def replace_chunks(doc, batch):
    """Replace a split parent before attribution so all child statuses persist."""
    for child in batch:
        if any(c['id'] == child['id'] for c in doc['chunks']):
            continue
        doc['chunks'] = [c for c in doc['chunks'] if not child['id'].startswith(c['id'] + '.')]
        doc['chunks'].append(child)
    doc['chunks'].sort(key=lambda c: tuple(int(p) for p in c['id'].lstrip('c').split('.')))

