ARG BLUEDOT_BASE_IMAGE
FROM ${BLUEDOT_BASE_IMAGE}

COPY artifacts/myextension-0.2.1-py3-none-any.whl /tmp/

RUN python -c "from importlib.metadata import version; assert version('jupyterlab').split('.', 1)[0] == '4'; assert version('jupyter-server').split('.', 1)[0] == '2'; import jsonschema" \
    && python -m pip install --no-cache-dir --no-deps --force-reinstall /tmp/myextension-0.2.1-py3-none-any.whl \
    && python -m jupyter server extension enable myextension --sys-prefix \
    && python -c "import myextension; assert myextension.__version__ == '0.2.1'; assert myextension._jupyter_labextension_paths()[0]['dest'] == 'myextension'" \
    && python -m jupyter labextension list \
    && python -m jupyter server extension list \
    && rm -f /tmp/myextension-0.2.1-py3-none-any.whl

ENV JUPYTERLAB_BEHAVIOR_AUDIT_LOG_DIR=/workspace/result/behavior-audit \
    JUPYTERLAB_BEHAVIOR_AUDIT_ANALYSIS_TIMEOUT_SEC=120

# ARK_API_KEY must be injected by the platform secret manager at runtime.
# This image intentionally keeps the base image ENTRYPOINT and CMD unchanged.
