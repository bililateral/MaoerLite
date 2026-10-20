"""Read an answer's registered source, without running a task or fetching a new page."""
class SourceNotFound(Exception):
    pass


class SourceQueries:
    def __init__(self, get_run, read_document):
        self.get_run, self.read_document = get_run, read_document

    def text(self, run_id, number):
        run = self.get_run(run_id)
        source = next((s for s in run.get('sources', []) if s['number'] == number), None)
        if source is None:
            raise SourceNotFound()
        text = self.read_document(source.get('document_id', ''))
        return {'text': text or source.get('evidence', ''),
                'complete': bool(text) and not source.get('description_truncated', False),
                'source_url': source.get('original_url') or source.get('source_url', '')}
