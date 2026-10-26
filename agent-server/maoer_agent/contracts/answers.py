"""Model-independent factual delivery, separate from HTTP and tool protocols."""
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal, get_args
from maoer_agent.contracts.tasks import ControlAction
import re
import json


def source_view(source):
    """The factual source fields shared by model input and quote validation."""
    fields=('number','kind','title','podcast_title','duration_seconds','source_url','evidence','evidence_field',
        'evidence_level','published','published_display','fetched_at','fetched_display','freshness')
    return {k:source[k] for k in fields if k in source}


def source_numbers(value):
    if not isinstance(value,list):return value
    return [int(v) if isinstance(v,str) and v.isascii() and v.isdecimal() and str(int(v))==v else v for v in value]


def prose_quote_text(value):
    """Match ordinary typography without changing words, case or fact values."""
    return re.sub(r'\s+',' ',str(value).translate(str.maketrans({'‘':"'",'’':"'",'“':'"','”':'"'}))).strip()


def source_quote_matches(quote,source):
    view=source_view(source)
    normalize=prose_quote_text
    if any(normalize(quote) in normalize(value) for value in view.values() if isinstance(value,(str,int,float))):return True
    return bool((quote.lstrip().startswith('{') or re.match(r'^"[^"\n]+"\s*:',quote.lstrip()))
        and receipt_quote_matches(quote,json.dumps(view,ensure_ascii=False)))


def receipt_quote_matches(quote,text):
    """Structured receipt fields compare as data; prose remains an exact excerpt."""
    fragment=quote.strip()
    if not (fragment.startswith('{') or re.match(r'^"[^"\n]+"\s*:',fragment)):
        normalize=prose_quote_text
        return normalize(quote) in normalize(text)
    try:
        expected=json.loads(fragment if fragment.startswith('{') else '{'+fragment+'}')
        actual=json.loads(text)
    except ValueError:
        normalize=lambda value:re.sub(r'\s+',' ',value).strip()
        excerpt,body=normalize(fragment),normalize(text)
        for match in re.finditer(re.escape(excerpt),body):
            # Partial JSON excerpts remain literal evidence, without accepting
            # a numeric prefix such as 1 as proof of the value 1.5.
            if excerpt[-1].isdigit() or excerpt.endswith(('true','false','null')):
                if match.end()<len(body) and body[match.end()] not in ' ,}]':continue
            return True
        return False
    def equal_value(pattern,value):
        if isinstance(pattern,dict):
            return isinstance(value,dict) and pattern.keys()==value.keys() and all(equal_value(v,value[k]) for k,v in pattern.items())
        if isinstance(pattern,list):
            return isinstance(value,list) and len(pattern)==len(value) and all(equal_value(a,b) for a,b in zip(pattern,value))
        if type(pattern) is bool or type(value) is bool:return type(pattern) is type(value) and pattern==value
        return pattern==value
    def find(value):
        if isinstance(value,dict):return all(k in value and equal_value(v,value[k]) for k,v in expected.items()) or any(find(v) for v in value.values())
        if isinstance(value,list):return any(find(v) for v in value)
        return False
    return isinstance(expected,dict) and bool(expected) and find(actual)


class AnswerBlock(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    text: str = Field(min_length=1, max_length=16000)
    sources: list[int] = Field(default_factory=list, max_length=48)

    @field_validator('sources',mode='before')
    @classmethod
    def canonical_sources(cls,value):return source_numbers(value)


class FactualAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    blocks: list[AnswerBlock] = Field(min_length=1, max_length=48)
    background: list[AnswerBlock] = Field(default_factory=list, max_length=48)

    def checked_blocks(self, available):
        if any(number not in available for block in self.blocks for number in block.sources):
            raise ValueError('Unknown answer source')
        if sum(len(block.text) for block in self.blocks) > 16000:
            raise ValueError('Answer exceeds message contract')
        return [{'text': block.text, 'sources': list(dict.fromkeys(block.sources))} for block in self.blocks]


class EvidenceQuote(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    number: int | str
    quote: str = Field(min_length=1, max_length=600)


class ClaimCheck(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    claim: str = Field(min_length=1, max_length=600)
    issue: Literal['none', 'unsupported', 'conflict', 'outdated']
    evidence: list[EvidenceQuote] = Field(default_factory=list, max_length=8)


class EvidenceLookup(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    name: str = Field(min_length=1, max_length=64)
    arguments: dict

    @field_validator('name')
    @classmethod
    def readonly(cls, value):
        if value in get_args(ControlAction):raise ValueError('Evidence lookup cannot execute controls')
        return value


class GoalAssessment(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    id: str=Field(min_length=1,max_length=40)
    status: Literal['satisfied','needs_evidence','unavailable','waiting']
    sources: list[int]=Field(default_factory=list,max_length=48)
    call_ids: list[str]=Field(default_factory=list,max_length=36)
    memory_ids: list[str]=Field(default_factory=list,max_length=36)
    through_date: str=Field(default='',max_length=10)
    note: str=Field(default='',max_length=500)

    @field_validator('sources',mode='before')
    @classmethod
    def canonical_sources(cls,value):return source_numbers(value)


def checked_review_quotes(checks, sources, receipts=None, source_aliases=None):
    """Validate against original source/receipt text for both audit contracts."""
    source_data={s['number']:source_view(s) for s in sources}
    for check in checks:
        for quote in check.evidence:
            number=quote.number
            if isinstance(number,str) and number in (receipts or {}):
                matched=receipt_quote_matches(quote.quote,receipts[number]) or any(
                    item.get('citation') in source_data and source_quote_matches(quote.quote,item)
                    for item in (source_aliases or {}).get(number,[]))
            else:
                if isinstance(number,str) and source_numbers([number])!=[number]:number=source_numbers([number])[0]
                matched=number in source_data and source_quote_matches(quote.quote,source_data[number])
            if not matched:
                raise ValueError(f'Review quote absent from source {quote.number}: {quote.quote[:160]}')


class AnswerReview(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    decision: Literal['ready', 'needs_evidence']
    checks: list[ClaimCheck] = Field(max_length=32)
    blocks: list[AnswerBlock] = Field(default_factory=list, max_length=48)
    background: list[AnswerBlock] = Field(default_factory=list, max_length=48)
    lookup: EvidenceLookup | None = None
    goals: list[GoalAssessment]=Field(default_factory=list,max_length=36)
    question: str = Field(default='', max_length=500)

    def checked(self, sources, allowed, receipts=None, source_aliases=None):
        checked_review_quotes(self.checks, sources, receipts, source_aliases)
        if self.decision == 'needs_evidence':
            if self.blocks or self.background or self.question or self.lookup is None or self.lookup.name not in allowed:
                raise ValueError('Review lookup must be available and separate from delivery')
            return None
        if self.lookup is not None:
            raise ValueError('A ready answer cannot execute a lookup')
        if not self.blocks and self.question:
            return []
        if not self.checks:
            raise ValueError('A ready answer needs its checked claims')
        return FactualAnswer(blocks=self.blocks,background=self.background).checked_blocks({s['number'] for s in sources})


class DraftProblem(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    excerpt: str = Field(min_length=1, max_length=600)
    reason: str = Field(min_length=1, max_length=600)


class DraftBlockCheck(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    block: int = Field(ge=0, lt=48)
    issue: Literal['none', 'unsupported', 'conflict', 'outdated']
    evidence: list[EvidenceQuote] = Field(default_factory=list, max_length=8)
    problem: DraftProblem | None = None


class DraftReview(BaseModel):
    """An audit addresses immutable draft blocks; it cannot supply another answer."""
    model_config = ConfigDict(strict=True, extra='forbid')
    decision: Literal['ready', 'needs_evidence', 'revise']
    checks: list[DraftBlockCheck] = Field(max_length=48)
    lookup: EvidenceLookup | None = None
    goals: list[GoalAssessment] = Field(default_factory=list, max_length=36)
    question: str = Field(default='', max_length=500)

    def checked(self, draft, sources, allowed, receipts=None, source_aliases=None):
        indices = [check.block for check in self.checks]
        if len(set(indices)) != len(indices) or any(i >= len(draft) for i in indices):
            raise ValueError('Review must address distinct existing draft blocks')
        issues = [check for check in self.checks if check.issue != 'none']
        for check in issues:
            problem = check.problem
            if (problem is None or not problem.excerpt.strip() or not problem.reason.strip()
                    or problem.excerpt not in draft[check.block]['text']):
                raise ValueError('A draft defect needs its exact excerpt and specific diagnosis')
            if check.issue in ('conflict', 'outdated') and not check.evidence:
                raise ValueError('A conflict or outdated claim needs actual source evidence')
        if self.decision == 'ready':
            if issues or set(indices) != set(range(len(draft))):
                raise ValueError('Every delivered draft block must pass review')
            for check in self.checks:
                numbers = source_numbers([quote.number for quote in check.evidence])
                if not set(draft[check.block].get('sources', [])).issubset(numbers):
                    raise ValueError('Every draft citation needs checked source evidence')
        if self.decision == 'revise' and (not issues or self.lookup is not None or self.question):
            raise ValueError('Revision needs a draft defect and cannot execute or deliver')
        # Keep the existing exact-quote, source, receipt and lookup validation.
        checked_review_quotes(self.checks, sources, receipts, source_aliases)
        if self.decision == 'needs_evidence':
            if self.question or self.lookup is None or self.lookup.name not in allowed:
                raise ValueError('Review lookup must be available and separate from delivery')
            return None
        if self.decision == 'revise':return None
        if self.lookup is not None:raise ValueError('A ready answer cannot execute a lookup')
        if not draft and self.question:return []
        return FactualAnswer(blocks=draft).checked_blocks({s['number'] for s in sources})

    def revision_feedback(self, draft):
        """Carry the checked defect to composition without inventing replacement prose."""
        return {'blocks': draft,
                'issues': [check.model_dump() for check in self.checks if check.issue != 'none']}
