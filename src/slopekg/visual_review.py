"""Independent source review and admission. No provider credentials or graph publication."""
from .visual_batching import verification_batches
from .visual_contract import FACT_KEYS, VERIFY, REVIEW_CHECKS, review_result
from .visual_evidence import source_contains, literal_supported, field_is_supported, align_quote
from .visual_sources import fact_quality_issues
from .visual_comparison import compare_independent, unresolved_differences
import re


class VisualReviewer:
    def __init__(self, settings, ask):
        self.settings, self.ask = settings, ask

    async def review(self, doc, number, target, candidates, transcript, images, assignment,
                      support_context=(), table_source=False, checkpoint=None, independent_read=None):
        errors=[]
        originals={f["id"]:f for f in candidates}
        for fact in candidates:
            fact['independent_comparison']=compare_independent(fact,independent_read,candidates)
        wire=[{**{key:f[key] for key in (*FACT_KEYS,"id","quote","bbox")},
               "unit_basis":f.get('unit_basis',''),
               **({'source_bindings':f['source_bindings']} if f.get('source_bindings') else {}),
               **({'independent_comparison':f['independent_comparison']}
                  if f['independent_comparison']['differences'] else {}),
               "support_quotes":f.get("support_quotes",[]),"field_evidence":f.get("field_evidence",{})} for f in candidates]
        crop_slots=max(0,self.settings.max_images-len(images))
        # Every candidate here belongs to the same independently re-rendered
        # high-resolution region. Different fact boxes do not need duplicate
        # images or separate network calls; uncertainty recovery handles tiny text.
        batches=verification_batches(wire,self.settings.max_prompt_chars//2,crop_slots,shared_region=True)
        # One bounded independent quote repair, after initial batches. A repair
        # never changes candidate fields or bypasses the same evidence gates.
        work=[(batch,0,None) for batch in batches]
        for batch,attempt,feedback in work:
            expected = {f["id"] for f in batch}
            def validate(answer):
                return review_result(answer,expected)
            evidence_images=list(images)
            context={"document_id": doc.id, "sha256": doc.sha256,
                "page": number, "target": {k:target[k] for k in ("id","bbox","kind") if k in target},
                "segmentation_hint": assignment, "candidates": batch}
            if independent_read is not None:
                independent_text=independent_read['transcript']
                cap=self.settings.max_prompt_chars//6
                context['independent_source_read']={'transcript':independent_text[:cap],
                    'omitted_chars':max(0,len(independent_text)-cap),'note':'Full blind read retained locally; image remains primary'}
            if feedback:context["repair_feedback"]=feedback
            answer = await self.ask("verify", VERIFY, context, evidence_images, validate)
            by_id = {v["id"]: v for v in answer["verdicts"]}
            for wire_fact in batch:
                fact=originals[wire_fact["id"]]
                review = by_id[fact["id"]]
                fact.setdefault("review_history",[]).append(review)
                fact["review"] = review
                if review.get("validation_errors"): errors.append({"id":fact["id"],"errors":review["validation_errors"]})
                checks = [review[k] for k in REVIEW_CHECKS]
                declared=fact.get("field_evidence",{})
                extra=[k for k in declared if k in {"value","qualifier","condition","time"}] if isinstance(declared,dict) else []
                literal_keys = list(dict.fromkeys(["value","unit","qualifier",*extra]))
                source_fragments=[fact["quote"],*fact.get("support_quotes",[])]
                review_fragments=[review["quote"],*review.get("support_quotes",[])]
                def supports(fragments, independent=False):
                    primary=(independent_read['transcript'] if independent else transcript)
                    primary_table=(bool(independent_read['tables']) if independent else table_source)
                    sources=[(primary,primary_table),*[(r["transcript"],r.get("is_table",False)) for r in support_context]]
                    # The primary assertion must remain inside its own target.
                    # Supplemental headers/units may cite another actually read
                    # region on this page. Never accept an unseen extra quote.
                    return (source_contains(fragments[0],primary,table=primary_table)
                            and all(any(source_contains(q,s,table=is_table) for s,is_table in sources) for q in fragments)
                            and all(field_is_supported(fact,k,fragments,literal_supported) for k in literal_keys))
                source_ok=supports(source_fragments)
                review_ok=supports(review_fragments)
                blind_source_ok=(independent_read is None or supports(source_fragments,True))
                blind_review_ok=(independent_read is None or supports(review_fragments,True))
                blind_ok=blind_source_ok and blind_review_ok
                unresolved=unresolved_differences(fact['independent_comparison'],review)
                issues=fact_quality_issues(fact,review_fragments)
                if any(not any(literal_supported(b['text'],q) for q in source_fragments)
                       for b in fact.get('source_bindings',[]) if b['text'].strip()):
                    issues.append('source_binding_not_grounded')
                fact['quality_issues']=issues
                grounded = source_ok and (review_ok or (independent_read is not None and blind_ok)) and blind_ok and not issues and not unresolved
                fact["support_context"]=[{"quote":q,**{k:r[k] for k in ("region_id","page","bbox")}}
                    for q in dict.fromkeys(source_fragments+review_fragments) if not source_contains(q,transcript,table=table_source)
                    for r in support_context if source_contains(q,r["transcript"],table=r.get("is_table",False))]
                explicit = all(fact[k].strip() and not re.match(r"^(unknown|unresolved)(\b|[（(])",fact[k].strip(),re.I)
                               for k in ("object", "scope", "role"))
                sources=[{"region_id":target["id"],"page":number,"bbox":target["bbox"],"transcript":transcript,
                          "is_table":table_source,"source_type":"primary_read"},
                         *[{**s,"source_type":"linked_read"} for s in support_context]]
                if independent_read is not None:
                    sources.append({"region_id":target["id"],"page":number,"bbox":target["bbox"],
                                    "transcript":independent_read['transcript'],
                                    "is_table":bool(independent_read['tables']),"source_type":"independent_read"})
                fact["source_grounding"]=[{"quote":q,"region_id":s["region_id"],"page":s["page"],
                    "bbox":s["bbox"],"source_type":s["source_type"],"alignment":align_quote(q,s["transcript"])}
                    for q in dict.fromkeys(source_fragments+review_fragments) for s in sources
                    if source_contains(q,s["transcript"],table=s.get("is_table",False))]
                fact["admission_checks"]={"model_supported":review["decision"]=="supported","semantic_checks":dict(zip(REVIEW_CHECKS,checks)),
                    "source_evidence_grounded":supports(source_fragments),"review_evidence_grounded":supports(review_fragments),
                    'independent_source_grounded':blind_source_ok,
                    'independent_review_grounded':blind_review_ok,'field_quality_complete':not issues,
                    'independent_fields_resolved':not unresolved,
                    'unresolved_independent_fields':unresolved,
                    "explicit_object_scope_role":explicit,
                    "read_fields_supported":{k:field_is_supported(fact,k,source_fragments,literal_supported) for k in literal_keys},
                    "review_fields_supported":{k:field_is_supported(fact,k,review_fragments,literal_supported) for k in literal_keys}}
                if review["decision"] == "rejected":
                    fact.update(status="rejected", reason="review_rejected")
                elif review["decision"] == "supported" and all(checks) and grounded and explicit:
                    fact.update(status="verified_model", reason="source_and_semantics_checked_by_model")
                else:
                    fact.update(status="pending", reason="insufficient_source_or_semantic_support")
                    if not blind_source_ok:
                        fact['reason']='independent_source_disagreement'
                    elif unresolved:
                        fact['reason']='independent_field_conflict'
                    elif issues:
                        fact['reason']='incomplete_or_unbound_fields'
            if checkpoint:
                checkpoint()
            if attempt==0:
                retry=[];diagnostics=[]
                for candidate in batch:
                    fact=originals[candidate["id"]];checks=fact["admission_checks"]
                    if (checks["model_supported"] and all(checks["semantic_checks"].values()) and
                            checks["explicit_object_scope_role"] and checks["source_evidence_grounded"] and
                            checks['independent_source_grounded'] and
                            not checks["review_evidence_grounded"] and
                            not (independent_read is not None and checks['independent_review_grounded'])):
                        retry.append(candidate)
                        diagnostics.append({"id":candidate["id"],"review_fields_supported":checks["review_fields_supported"],
                            "failure":"review evidence did not align to actually read source or support complete literal fields"})
                if retry:
                    work.append((retry,1,{"failed_reviews":diagnostics,
                        "instruction":"先前核验引用无法在实际已读源文中对齐或未覆盖字段。独立从原图重读逐字证据，不拼接标题。仅返回确认该断言必需且可定位的原文片段；仍有疑问则uncertain，所有语义项重新检查。候选字段不可改写，不能因先前supported而通过。"}))
        return errors
