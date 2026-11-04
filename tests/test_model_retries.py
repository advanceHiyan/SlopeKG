"""Focused offline checks for bounded transient retries and partial-page scope."""
import asyncio
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from slopekg.knowledge_settings import KnowledgeSettings
from slopekg.visual_runtime import VisualRuntime
from slopekg.visual_contract import ContractError
from slopekg.model_client import ModelError
from slopekg.storage import read_json
from slopekg.knowledge_pipeline import KnowledgePipeline
from tests.test_knowledge_pipeline import document
from tests.test_knowledge_v3 import BatchModel
from tests.test_knowledge_ownership import fixture
from slopekg.knowledge_ownership import explicit_section_owners, ambiguous_entity_labels
from slopekg.knowledge_composition import composition_jobs


class Responses:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), 0
    def identity(self): return {'name': 'offline-retry-fixture', 'model': 'fixture'}
    def complete(self, **kwargs):
        self.calls += 1
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return result, {'model': 'fixture', 'usage': {'total_tokens': 0}}


def http_error(status, **kwargs):
    return ModelError('Fixture HTTP failure', receipt={'http_status': status, **kwargs})


class BoundedRetryTests(unittest.TestCase):
    def runtime(self, model, root, **settings):
        return VisualRuntime(model, model, Path(root),
            KnowledgeSettings(retry_backoff_seconds=0, **settings))
    def ask(self, runtime, name='one', validate=lambda a: a):
        return runtime.ask('fixture', 'read', {'name': name}, [], validate)

    def test_explicit_transient_responses_are_counted_persisted_and_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Responses([http_error(503), http_error(429), {'ok': True}])
            runtime = self.runtime(model, temp)
            self.assertEqual(self.ask(runtime), {'ok': True})
            self.assertEqual((runtime.calls, runtime.transient_retries), (3, 2))
            record = read_json(next((Path(temp) / 'cache').glob('*.json')))
            self.assertEqual([a['status'] for a in record['attempts']], ['failed', 'failed', 'completed'])
            self.assertEqual(len(runtime.receipts), 3)
            runtime.reset(); self.ask(runtime)
            self.assertEqual(runtime.calls, 0); self.assertEqual(model.calls, 3)

    def test_unknown_transport_auth_and_gateway_timeouts_never_retry(self):
        for error in [ModelError('Unknown billing', receipt={'model': 'fixture'}),
                      http_error(401), http_error(402), http_error(403), http_error(408), http_error(504)]:
            with self.subTest(error=error.receipt), tempfile.TemporaryDirectory() as temp:
                model = Responses([error]); runtime = self.runtime(model, temp)
                with self.assertRaises(ModelError): self.ask(runtime)
                self.assertEqual(model.calls, 1); self.assertEqual(runtime.transient_retries, 0)
                with self.assertRaises(ModelError): self.ask(runtime)
                self.assertEqual(model.calls, 1)

    def test_long_server_cooldown_is_not_shortened_or_automatically_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Responses([http_error(429, retry_after_seconds=120)])
            runtime = self.runtime(model, temp)
            with self.assertRaises(ModelError): self.ask(runtime)
            self.assertEqual(model.calls, 1)

    def test_hard_cap_and_shared_retry_budget_include_all_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Responses([http_error(503), http_error(503)])
            runtime = self.runtime(model, temp, max_calls=2)
            with self.assertRaises(ModelError): self.ask(runtime)
            self.assertEqual((model.calls, runtime.calls), (2, 2))
        with tempfile.TemporaryDirectory() as temp:
            model = Responses([http_error(503), {'ok': True}, http_error(503)])
            runtime = self.runtime(model, temp, transient_retry_budget=1)
            self.ask(runtime)
            with self.assertRaises(ModelError): self.ask(runtime, 'two')
            self.assertEqual((model.calls, runtime.transient_retries), (3, 1))

    def test_contract_errors_and_old_started_markers_do_not_trigger_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            model = Responses([{'invalid': True}]); runtime = self.runtime(model, temp)
            def reject(answer): raise ContractError('Invalid fixture contract')
            with self.assertRaises(ModelError): self.ask(runtime, validate=reject)
            self.assertEqual(model.calls, 1)
            path = next((Path(temp) / 'cache').glob('*.json')); value = read_json(path)
            value['status'] = 'started'; path.write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ModelError): self.ask(runtime)
            self.assertEqual(model.calls, 1)

    def test_async_dispatch_respects_six_inflight_slots(self):
        class Busy:
            def __init__(self): self.lock = threading.Lock(); self.active = self.peak = 0
            def identity(self): return {'name': 'offline-concurrency', 'model': 'fixture'}
            def complete(self, **kwargs):
                with self.lock:
                    self.active += 1; self.peak = max(self.peak, self.active)
                time.sleep(.03)
                with self.lock: self.active -= 1
                return {'ok': True}, {'model': 'fixture'}
        with tempfile.TemporaryDirectory() as temp:
            model = Busy(); runtime = self.runtime(model, temp, model_concurrency=6)
            async def run():
                await asyncio.gather(*(runtime.ask_async('fixture', 'read', {'n': n}, [], lambda a: a) for n in range(10)))
            asyncio.run(run())
            self.assertLessEqual(runtime.peak_concurrency, 6)
            self.assertGreater(model.peak, 1)


class SelectedPageTests(unittest.TestCase):
    def test_shared_class_label_cannot_steal_a_named_object_subsection(self):
        doc = fixture()
        doc['entities'].append({'id':'G','name':'Reactor','kind':'entity','context':'','aliases':[]})
        doc['units'].insert(1,{'id':'generic-heading','page':1,'text':'1.1.1 Reactor inspection','bbox':[0,0,1,1]})
        self.assertEqual(ambiguous_entity_labels(doc), {'G'})
        self.assertEqual(explicit_section_owners(doc)['a']['owner_id'], 'A')
        doc['ambiguous_entity_ids']=['G'];doc['entities'][-1]['unit_ids']=['a','b']
        self.assertFalse(any(j['entity_id']=='G' for j in composition_jobs(doc,KnowledgeSettings())))
        self.assertEqual(doc['entities'][-1]['status'],'identity_requires_review')
        doc['entities'][0]['context']=doc['entities'][1]['context']='Reactor'
        self.assertEqual(ambiguous_entity_labels(doc), set())

    def test_only_selected_pages_enter_model_context_and_whole_source_stays_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'anonymous.pdf'; document(source); model = BatchModel()
            run = KnowledgePipeline(model, root / 'out').run([source], page_selections={str(source): [1, 5, 7]})
            doc = run['documents'][0]
            self.assertEqual({u['page'] for u in doc['units']}, {1, 5, 7})
            self.assertEqual(doc['coverage']['unselected_pages'], [2, 3, 4, 6])
            self.assertEqual(run['processing_status'], 'partial')
            self.assertEqual(run['summary']['pages'], 3)
            for _, context in model.events:
                spans = context.get('source_spans', []) + [s for c in context.get('chunks', []) for s in c['spans']]
                self.assertTrue(all(s['page'] in {1, 5, 7} for s in spans))


if __name__ == '__main__': unittest.main()
