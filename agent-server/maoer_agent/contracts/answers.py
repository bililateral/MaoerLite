"""Model-independent factual delivery, separate from HTTP and tool protocols."""
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal, get_args
from maoer_agent.contracts.tasks import ControlAction
import re
import json

MAX_BLOCK_SOURCES = 48


def citation_units(text):
    """Paragraphs and list items own citations; fenced literals stay intact."""
    prose = re.sub(r'(?ms)^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[ \t]*(?=\n|$)',
                   '<literal>', text.replace('\r\n', '\n').replace('\r', '\n'))
    return [part for part in re.split(r'\n[ \t]*\n|(?=^ {0,3}(?:\d+[.)]|[-+*])[ \t]+\S)',
                                      prose, flags=re.MULTILINE) if part.strip()]


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
    sources: list[int] = Field(default_factory=list, max_length=MAX_BLOCK_SOURCES)
    episode_id: str = Field(default='', max_length=2000)

    @field_validator('sources',mode='before')
    @classmethod
    def canonical_sources(cls,value):return source_numbers(value)


class QuotedBlock(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    quote_reference: str = Field(min_length=1,max_length=40)
    sources: list[int] = Field(min_length=1,max_length=MAX_BLOCK_SOURCES)
    episode_id: str = Field(default='',max_length=2000)

    @field_validator('sources',mode='before')
    @classmethod
    def canonical_sources(cls,value):return source_numbers(value)

    @property
    def text(self):return ''


class FactualAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    blocks: list[AnswerBlock | QuotedBlock] = Field(min_length=1, max_length=48)
    background: list[AnswerBlock] = Field(default_factory=list, max_length=48)

    def checked_blocks(self, available, *, local_citations=False, episode=None, quote_refs=None):
        if any(number not in available for block in self.blocks for number in block.sources):
            raise ValueError('Unknown answer source')
        if local_citations and any(block.sources and len(citation_units(block.text)) > 1 for block in self.blocks):
            raise ValueError('Each sourced paragraph or list item needs its own block and matching sources')
        if episode and any(block.episode_id != episode['id'] for block in self.blocks):
            raise ValueError('Every block must preserve the resolved episode identity: '+episode['id'])
        if quote_refs is not None and not any(isinstance(block,QuotedBlock) for block in self.blocks):
            raise ValueError('An original-text request needs a quote_reference from the supplied original excerpts')
        blocks=[]
        for block in self.blocks:
            text=block.text
            if isinstance(block,QuotedBlock):
                proof=(quote_refs or {}).get(block.quote_reference)
                if not proof or set(block.sources)!={proof['number']}:
                    raise ValueError('A quoted excerpt must use its bound source and supplied quote_reference')
                # Markdown escaping preserves publisher citation brackets as
                # literal text instead of turning them into this answer's IDs.
                original=re.sub(r'(?<!\\)\[(\d+)\]',r'\\[\1\\]',proof['quote'])
                text='\n'.join('> '+line for line in original.splitlines())
            blocks.append({'text':text,'sources':list(dict.fromkeys(block.sources)),
                **({'episode_id':block.episode_id} if block.episode_id else {})})
        if sum(len(block['text']) for block in blocks)>16000:
            raise ValueError('Answer exceeds message contract')
        return blocks


class EvidenceQuote(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    number: int | str
    quote: str = Field(min_length=1, max_length=600)


class EvidenceReference(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    reference: str = Field(min_length=1, max_length=40)


def quotation_evidence(sources):
    """Each selectable quotation is an unchanged span of an actual source body."""
    bank={}
    for source in sources:
        for line in source.get('evidence','').splitlines():
            if not line.strip():continue
            for offset in range(0,len(line),500):
                if len(bank)>=192:return bank
                bank['source'+str(source['number'])+'_quote'+str(len(bank)+1)]={
                    'number':source['number'],'quote':line[offset:offset+500]}
    return bank


def review_evidence(sources, receipts):
    """Named exact excerpts avoid asking the model to reserialize evidence."""
    bank = {}
    def add(number, text):
        if len(bank)>=192 or not isinstance(text,str) or not text.strip() or len(text)>600:return
        value={'number':number,'quote':text}
        if value not in bank.values():
            prefix='source'+str(number) if type(number) is int else 'receipt'
            bank[prefix+'_'+str(len(bank)+1)]=value
    for source in sources:
        number=source['number']
        for key in ('title','podcast_title','published','published_display'):
            add(number,source.get(key))
        if source.get('duration_seconds') is not None:
            add(number,json.dumps({'duration_seconds':source['duration_seconds']},ensure_ascii=False))
        body=source.get('evidence','')
        # Each window is a contiguous original excerpt. The full source remains available.
        for offset in range(0,min(len(body),2000),500):add(number,body[offset:offset+500])
    def fields(number,value):
        if isinstance(value,dict):
            for key,item in value.items():
                if not isinstance(item,(dict,list)):
                    add(number,json.dumps({key:item},ensure_ascii=False))
                elif key=='items':
                    for row in item[:12]:fields(number,row)
                elif key in ('result','arguments'):fields(number,item)
    for number,text in receipts.items():
        # Recall uses its original conversation proof IDs and exact excerpts.
        if not number.startswith('conversation_'):
            fields(number,json.loads(text))
    return bank


class ClaimCheck(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    claim: str = Field(min_length=1, max_length=600)
    issue: Literal['none', 'unsupported', 'conflict', 'outdated']
    evidence: list[EvidenceQuote] = Field(default_factory=list, max_length=8)


class EvidenceLookup(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    name: str = Field(min_length=1, max_length=64)
    arguments: dict
    goal_id: str = Field(default='', max_length=40, description='本次补查服务的未完成目标id；只有一个匹配目标时可留空')

    @field_validator('name')
    @classmethod
    def readonly(cls, value):
        if value in get_args(ControlAction):raise ValueError('Evidence lookup cannot execute controls')
        return value


class EvidencePlan(BaseModel):
    """Request evidence without asserting that any user goal is satisfied."""
    model_config = ConfigDict(strict=True, extra='forbid')
    lookup: EvidenceLookup | None = None
    reason: str = Field(default='', max_length=500)


class GoalAssessment(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    id: str=Field(min_length=1,max_length=40)
    status: Literal['satisfied','needs_evidence','unavailable','waiting']
    sources: list[int]=Field(default_factory=list,max_length=48,description='资料整数编号；工具回执字符串ID填call_ids，对话证据ID填memory_ids')
    call_ids: list[str]=Field(default_factory=list,max_length=36,description='本轮实际工具回执call_id，不填入sources')
    memory_ids: list[str]=Field(default_factory=list,max_length=36,description='已给定的用户原话或摘要证据ID，必须有对应原文校核')
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
    evidence: list[EvidenceQuote | EvidenceReference] = Field(default_factory=list, max_length=MAX_BLOCK_SOURCES)
    problem: DraftProblem | None = None


class DraftReview(BaseModel):
    """An audit addresses immutable draft blocks; it cannot supply another answer."""
    model_config = ConfigDict(strict=True, extra='forbid')
    decision: Literal['ready', 'needs_evidence', 'revise']
    checks: list[DraftBlockCheck] = Field(max_length=48)
    lookup: EvidenceLookup | None = None
    goals: list[GoalAssessment] = Field(default_factory=list, max_length=36)
    question: str = Field(default='', max_length=500)

    def checked(self, draft, sources, allowed, receipts=None, source_aliases=None, evidence_refs=None, episode=None):
        for check in self.checks:
            resolved=[]
            for proof in check.evidence:
                if isinstance(proof,EvidenceReference):
                    if proof.reference not in (evidence_refs or {}):raise ValueError('Unknown evidence reference: '+proof.reference)
                    proof=EvidenceQuote.model_validate(evidence_refs[proof.reference])
                resolved.append(proof)
            check.evidence=resolved
        available_sources = {source['number'] for source in sources}
        indices = [check.block for check in self.checks]
        if len(set(indices)) != len(indices) or any(i >= len(draft) for i in indices):
            raise ValueError(f'Review checks need one entry per existing block; received {indices}, existing {list(range(len(draft)))}')
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
                missing=set(draft[check.block].get('sources', []))-set(numbers)
                if missing:
                    references={n:[key for key,value in (evidence_refs or {}).items() if value['number']==n]
                                for n in sorted(missing)}
                    raise ValueError('Every draft citation needs checked source evidence: '+json.dumps({
                        'block':check.block,'missing_source_numbers':sorted(missing),
                        'source_reference_choices':references,'received_evidence_numbers':numbers},ensure_ascii=False))
                factual = {number for number in numbers if number in available_sources}
                if not factual.issubset(draft[check.block].get('sources', [])):
                    raise ValueError('Checked factual sources must be cited in their own draft block')
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
        return FactualAnswer(blocks=draft).checked_blocks(available_sources, local_citations=True, episode=episode)

    def revision_feedback(self, draft):
        """Carry the checked defect to composition without inventing replacement prose."""
        return {'blocks': draft,
                'issues': [check.model_dump() for check in self.checks if check.issue != 'none']}
