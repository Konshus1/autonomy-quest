# The verifier base is immutable; dependency installation occurs only while building.
FROM python:3.12-alpine@sha256:6d43704baacd1bfbe7c295d7f13079d5d8104ed33568873133f8fc69980419df

RUN python -m pip install --no-cache-dir --disable-pip-version-check pytest==8.3.5 \
 && addgroup -g 65532 verifier \
 && adduser -D -H -u 65532 -G verifier verifier \
 && mkdir -p /usr/local/lib/python3.12/site-packages/runner/close_loop /opt/aq-trusted/checks /tmp/home \
 && chown 65532:65532 /tmp/home

COPY --chmod=0444 runner/__init__.py /usr/local/lib/python3.12/site-packages/runner/__init__.py
COPY --chmod=0444 runner/close_loop/__init__.py /usr/local/lib/python3.12/site-packages/runner/close_loop/__init__.py
COPY --chmod=0444 runner/close_loop/policy.py /usr/local/lib/python3.12/site-packages/runner/close_loop/policy.py
COPY --chmod=0444 runner/close_loop/verifier.py /usr/local/lib/python3.12/site-packages/runner/close_loop/verifier.py
COPY --chmod=0555 tests/fixtures/close_loop/trusted_checks/pytest_exact.py /opt/aq-trusted/checks/pytest_exact.py

ENV PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    HOME=/tmp/home TMPDIR=/tmp
USER 65532:65532
WORKDIR /candidate
ENTRYPOINT ["python", "-I", "-m", "runner.close_loop.verifier"]
