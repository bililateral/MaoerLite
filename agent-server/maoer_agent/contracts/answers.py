"""Explicit quotation selection and binding to original source passages."""
from pydantic import BaseModel, ConfigDict, Field, field_validator
import re

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


class QuotedAnswer(BaseModel):
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
