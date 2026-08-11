# The runtime is intentionally small and contains no AQ application, database client config,
# cloud CLI, SSH material, or actuator. Pin the base by digest so the trusted runner is reviewable.
FROM python:3.12-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pytest==8.3.5

RUN useradd --uid 65532 --gid nogroup --no-create-home --home-dir /nonexistent verifier \
 && mkdir -p /opt/hermetic-verifier \
 && chown root:root /opt/hermetic-verifier \
 && chmod 0555 /opt/hermetic-verifier
COPY --chmod=0555 container_entrypoint.py /opt/hermetic-verifier/container_entrypoint.py
COPY --chmod=0444 verifier_census.py /usr/local/lib/python3.12/site-packages/verifier_census.py

USER 65532:65534
WORKDIR /work/repo
ENTRYPOINT ["python", "/opt/hermetic-verifier/container_entrypoint.py"]
CMD ["python", "-m", "pytest", "-q"]
