"""Opt-in search acceptance accounting; no credential or response logging."""
PAID_CASES = ('search_parallel', 'search_exa')


def validate_paid_case(case, allowed):
    if type(allowed) is not bool or (case in PAID_CASES) != allowed:
        raise ValueError('Real search acceptance requires one search case and --allow-paid-search.')


class SearchTrace:
    """Observe at most one real HTTP search in an isolated diagnostic process."""
    def __init__(self, job_class=None):
        if job_class is None:
            from ai_desktop.services.qt_search import SearchJob
            job_class = SearchJob
        self.job_class = job_class
        self.attempts = 0
        self.results = []

    def __enter__(self):
        from ai_desktop.services.web_search import SearchResult
        self.original_start = self.job_class.start
        def start(job):
            if job.result is not None or job._reply is not None:
                return self.original_start(job)
            if self.attempts >= 1:
                job._finish(SearchResult(error='验收搜索次数已用完。', error_type='acceptance_limit'))
                return
            self.attempts += 1
            job.finished.connect(lambda result: self._observe(job, result))
            return self.original_start(job)
        self.job_class.start = start
        return self

    def _observe(self, job, result):
        self.results.append({'provider': job.request.settings.provider, 'http_status': job.http_status,
                             'error_type': result.error_type, 'source_count': len(result.sources)})

    def __exit__(self, *args):
        self.job_class.start = self.original_start
