ARG PYTHON_IMAGE=m.daocloud.io/docker.io/library/python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534
FROM ${PYTHON_IMAGE} AS runtime
ARG APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy \
    PATH=/app/.venv/bin:$PATH TIKTOKEN_CACHE_DIR=/opt/tiktoken \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ANONYMIZED_TELEMETRY=False
RUN sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl unzip libaio1t64 libgomp1 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --index-url ${PYPI_INDEX} uv==0.11.7
WORKDIR /app
COPY . /app
RUN uv export --frozen --all-packages --no-dev --extra datasource_oracle \
    --format requirements-txt --output-file /tmp/runtime.txt \
    && uv venv --python /usr/local/bin/python3 /app/.venv \
    && uv pip install --python /app/.venv/bin/python --index-url ${PYPI_INDEX} \
       -r /tmp/runtime.txt \
    && uv pip check --python /app/.venv/bin/python \
    && mkdir -p /opt/tiktoken \
    && /app/.venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); tiktoken.get_encoding('o200k_base')"
COPY docker/offline/vendor/instantclient-basic-linux.x64-19.32.0.0.0dbru.zip /tmp/oracle.zip
RUN unzip -q /tmp/oracle.zip -d /opt/oracle && rm /tmp/oracle.zip \
    && echo /opt/oracle/instantclient_19_32 > /etc/ld.so.conf.d/oracle.conf \
    && ln -s /usr/lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1 \
    && ldconfig \
    && /app/.venv/bin/python -c "import oracledb; oracledb.init_oracle_client(); print(oracledb.clientversion())"
CMD ["dbgpt", "start", "webserver", "--config", "docker/offline/site.toml"]

FROM ${PYTHON_IMAGE} AS sandbox
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
RUN sed -i 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --index-url ${PYPI_INDEX} uv==0.11.7
COPY docker/offline/sandbox-requirements.txt /tmp/requirements.txt
RUN uv venv /opt/venv && uv pip install --python /opt/venv/bin/python \
    --index-url ${PYPI_INDEX} -r /tmp/requirements.txt
ENV PATH=/opt/venv/bin:$PATH MPLBACKEND=Agg PYTHONUNBUFFERED=1
WORKDIR /workspace
CMD ["python"]
