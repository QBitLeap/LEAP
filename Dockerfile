FROM python:3.13.5-slim-bookworm@sha256:a7cb177dc60dfa77b223192cd0d9e6ce1aac7cf9a569023d68e7c91b6371a718 AS runtime
WORKDIR /opt/protocol
COPY requirements.lock /opt/requirements.lock
RUN python -m pip install --no-cache-dir --no-deps --only-binary=:all: --require-hashes -r /opt/requirements.lock
COPY protocol/quantums /opt/protocol/quantums
COPY protocol/indexer /opt/protocol/indexer
COPY protocol/LICENSE /usr/share/doc/leap/LICENSE
COPY LICENSE NOTICE /usr/share/doc/leap-app/
COPY source-manifest.json /usr/share/doc/leap/source-manifest.json
COPY --chmod=0555 app /opt/index-app
RUN ln -s /opt/index-app/leap /usr/local/bin/leap
ENV PYTHONPATH=/opt/protocol:/opt/index-app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
RUN mkdir -p /data && chown 1000:1000 /data
USER 1000:1000
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "/opt/index-app/indexapp.py"]
CMD ["--help"]

FROM runtime AS verified
COPY fixtures/ /opt/protocol/
RUN python -m unittest discover -s tests \
    && printf 'Public protocol fixture tests passed\n' > /tmp/leap-tests-passed

FROM runtime
COPY --from=verified /tmp/leap-tests-passed /usr/share/doc/leap/validation.txt
